"""Minimal OpenRouteService client: geocoding and driving directions."""

import contextlib
import hashlib
import logging
import re
from collections.abc import Callable
from typing import Any, TypedDict, cast

import requests
from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError

logger = logging.getLogger(__name__)


class GeocodeResult(TypedDict):
    """The best geocoding match for a query."""

    label: str
    state: str | None  # two-letter code
    latitude: float
    longitude: float


class Route(TypedDict):
    """A driving route; coordinates are [longitude, latitude] pairs."""

    distance_miles: float
    duration_seconds: float
    coordinates: list[list[float]]


BASE_URL = 'https://api.openrouteservice.org'
# Assumption: the vehicle is routed as a car. The brief gives no vehicle type; 'driving-hgv' would
# route a heavy truck (avoiding low bridges and weight limits) if that's what's needed.
ROUTING_PROFILE = 'driving-car'
# ORS's value for "snap to the nearest road within the largest radius allowed" (350 m).
MAX_SNAP_RADIUS = -1
METERS_PER_MILE = 1609.344
RESPONSE_EXCERPT_CHARS = 300  # how much of an unexpected response to log or show
TIMEOUT_SECONDS = 10
CACHE_SECONDS = 60 * 60 * 24


# ORS error code for "no road within the snapping radius of coordinate N".
UNROUTABLE_POINT = 2010


class RoutingError(Exception):
    """A routing/geocoding failure; `status` is the HTTP status the API should answer with.

    `code` is the OpenRouteService error code, if any; `point` is the index of the coordinate
    that couldn't be snapped to a road (for UNROUTABLE_POINT errors).
    """

    def __init__(self, message: str, status: int = 502, code: int | None = None, point: int | None = None) -> None:
        """Store the message, HTTP status and ORS error details."""
        super().__init__(message)
        self.status = status
        self.code = code
        self.point = point


def _request(method: str, path: str, **kwargs: Any) -> Any:
    """Call the API and return its JSON, turning failures into RoutingError."""
    if not settings.ORS_API_KEY:
        raise RoutingError('ORS_API_KEY is not configured.', status=503)
    try:
        response = requests.request(
            method,
            BASE_URL + path,
            headers={'Authorization': settings.ORS_API_KEY},
            timeout=TIMEOUT_SECONDS,
            **kwargs,
        )
    except requests.RequestException as exc:
        logger.warning('OpenRouteService request to %s failed: %r', path, exc)
        raise RoutingError(f'OpenRouteService is unreachable ({exc.__class__.__name__}).') from exc

    if not response.ok:
        logger.warning(
            'OpenRouteService %s returned HTTP %s: %s',
            path,
            response.status_code,
            response.text[:RESPONSE_EXCERPT_CHARS],
        )

    if response.status_code == 429:
        raise RoutingError('OpenRouteService rate limit reached; try again shortly.', status=503)
    if response.status_code in (401, 403):
        if 'quota' in response.text.lower():
            raise RoutingError('OpenRouteService quota exceeded for this API key; try again later.', status=503)
        raise RoutingError('OpenRouteService rejected the API key.', status=503)
    if not response.ok:
        try:
            error = response.json()['error']
            message, code = error['message'], error.get('code')
        except (ValueError, KeyError, TypeError):
            message, code = response.text[:RESPONSE_EXCERPT_CHARS], None
        point = re.search(r'coordinate (\d+)', message) if code == UNROUTABLE_POINT else None
        # 404 from directions means no drivable route between the points.
        status = 400 if response.status_code in (400, 404) else 502
        raise RoutingError(
            f'OpenRouteService error: {message}',
            status=status,
            code=code,
            point=int(point[1]) if point else None,
        )
    try:
        return response.json()
    except ValueError as exc:
        logger.warning('OpenRouteService %s returned non-JSON: %s', path, response.text[:RESPONSE_EXCERPT_CHARS])
        raise RoutingError('OpenRouteService returned an unreadable response.') from exc


def _parse[T](path: str, parse: Callable[[Any], T], data: Any) -> T:
    """Run `parse(data)`, turning an unexpected response shape into RoutingError."""
    try:
        return parse(data)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning('Unexpected OpenRouteService %s response: %r', path, data)
        raise RoutingError('OpenRouteService returned an unexpected response.') from exc


def _cached[T](key_parts: tuple, fetch: Callable[[], T]) -> T:
    """Return the cached result for `key_parts`, calling `fetch()` and caching it on a miss.

    A broken cache (e.g. the cache table not created yet) only costs speed, never the request.
    """
    key = 'ors:' + hashlib.sha256(repr(key_parts).encode()).hexdigest()
    try:
        result = cache.get(key)
    except DatabaseError:
        return fetch()
    if result is None:
        result = fetch()
        with contextlib.suppress(DatabaseError):
            cache.set(key, result, CACHE_SECONDS)
    return result


def geocode(text: str) -> GeocodeResult | None:
    """Return {'label', 'state', 'latitude', 'longitude'} for the best US match of `text`, or None."""

    def parse(data: Any) -> GeocodeResult | dict:
        """Extract the best match; {} when nothing matches."""
        if not data.get('features'):
            return {}
        feature = data['features'][0]
        longitude, latitude = feature['geometry']['coordinates']
        properties = feature['properties']
        return {
            'label': properties.get('label', text),
            'state': properties.get('region_a'),
            'latitude': float(latitude),
            'longitude': float(longitude),
        }

    def fetch() -> GeocodeResult | dict:
        """Query the geocoding API."""
        path = '/geocode/search'
        data = _request('GET', path, params={'text': text, 'boundary.country': 'US', 'size': 1})
        return _parse(path, parse, data)

    result = _cached(('geocode', text.strip().lower()), fetch)
    # {} (cached as 'no match') means nothing was found.
    return cast(GeocodeResult, result) if result else None


def directions(start: tuple[float, float], finish: tuple[float, float]) -> Route:
    """Return the driving route between two (latitude, longitude) points.

    The result is {'distance_miles', 'duration_seconds', 'coordinates'}, with coordinates as
    [longitude, latitude] pairs (at least two).
    """

    def parse(data: Any) -> Route:
        """Extract distance, duration and geometry from the first route."""
        feature = data['features'][0]
        summary = feature['properties']['summary']
        coordinates = feature['geometry']['coordinates']
        if len(coordinates) < 2:
            raise ValueError('route geometry has fewer than two points')
        return {
            'distance_miles': float(summary.get('distance', 0)) / METERS_PER_MILE,
            'duration_seconds': float(summary.get('duration', 0)),
            'coordinates': coordinates,
        }

    def fetch() -> Route:
        """Query the directions API."""
        path = f'/v2/directions/{ROUTING_PROFILE}/geojson'
        body = {
            'coordinates': [[start[1], start[0]], [finish[1], finish[0]]],
            'radiuses': [MAX_SNAP_RADIUS, MAX_SNAP_RADIUS],
        }
        return _parse(path, parse, _request('POST', path, json=body))

    return _cached(('directions', start, finish), fetch)
