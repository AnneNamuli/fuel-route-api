"""Plan a trip end to end: resolve the locations, get the route, pick fuel stops, build the response."""

import contextlib
import hashlib
import logging
import re
from typing import Any, TypedDict

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError, IntegrityError
from django.db.models import F
from django.utils import timezone

from fuel_route.models import SavedTrip

from . import openrouteservice, places
from .fuel_optimizer import START_SEARCH_MILES, NoFuelPlanError, Purchase, plan_fuel_stops
from .geo import in_usa, simplify_line
from .route_stations import stations_along_route
from .text import collapse_whitespace

logger = logging.getLogger(__name__)

SECONDS_PER_HOUR = 3600
# How far (in degrees, about 50 m) the drawn route may stray from the real one; keeps responses small.
MAP_TOLERANCE_DEGREES = 0.0005
ROAD_POINT_CACHE_SECONDS = 60 * 60 * 24 * 30
COORDINATES = re.compile(r'^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$')


class Location(TypedDict):
    """A resolved start or finish: the user's query and where it is."""

    query: str
    label: str
    latitude: float
    longitude: float


class FuelStop(TypedDict):
    """One fuel stop in the API response."""

    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    miles_from_start: float
    detour_miles: float
    price_per_gallon: float
    gallons: float
    cost: float


class Trip(TypedDict):
    """The planned trip returned by the API (map_url is added by the view)."""

    start: Location
    finish: Location
    distance_miles: float
    duration_hours: float
    vehicle: dict[str, float]
    fuel_stops: list[FuelStop]
    notes: list[str]
    total_gallons: float
    total_fuel_cost: float
    map: dict[str, Any]


class TripError(Exception):
    """A trip that can't be planned; `status` is the HTTP status the API should answer with."""

    def __init__(self, message: str, status: int = 400) -> None:
        """Store the message and HTTP status."""
        super().__init__(message)
        self.status = status


def plan_trip(start_query: str, finish_query: str) -> Trip:
    """Plan a trip between two US locations and return the API response data.

    Makes one routing API call; "City, ST" and "latitude,longitude" inputs are resolved offline,
    anything else costs one geocoding call each.
    """
    try:
        start = resolve_location(start_query)
        finish = resolve_location(finish_query)
        route = route_between(start, finish)
    except openrouteservice.RoutingError as exc:
        raise TripError(str(exc), status=exc.status) from exc

    total_miles = route['distance_miles']
    candidates = stations_along_route(route['coordinates'], total_miles)
    range_miles, mpg = settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG
    try:
        purchases = plan_fuel_stops(candidates, total_miles, range_miles, mpg, settings.FUEL_STOP_COST)
    except NoFuelPlanError as exc:
        raise TripError(str(exc), status=422) from exc

    stops = [stop_data(purchase) for purchase in purchases]
    return Trip(
        start=start,
        finish=finish,
        distance_miles=round(total_miles, 1),
        duration_hours=round(route['duration_seconds'] / SECONDS_PER_HOUR, 2),
        vehicle={'range_miles': range_miles, 'mpg': mpg},
        fuel_stops=stops,
        notes=trip_notes(purchases),
        total_gallons=round(sum(p.gallons for p in purchases), 2),
        total_fuel_cost=round(sum(p.cost for p in purchases), 2),
        map=build_map(route['coordinates'], stops),
    )


def trip_notes(purchases: list[Purchase]) -> list[str]:
    """Return plain-language notes on assumptions that affect the cost of this particular trip."""
    if not purchases:
        return [
            'No fuel station on this route, so the trip is assumed to start with a full tank and total_fuel_cost is 0.'
        ]
    first = purchases[0].candidate
    if first.mile > START_SEARCH_MILES:
        return [
            f'No fuel station within {START_SEARCH_MILES:g} miles of the start; fuel up to the first '
            f'station (mile {first.mile:.0f}) is priced at that station.'
        ]
    return []


def route_between(start: Location, finish: Location) -> openrouteservice.Route:
    """Return the driving route between two resolved locations.

    If a city's Census reference point isn't near a road, re-locate that city with the geocoding
    API and retry (each endpoint at most once, so at most 3 calls); the corrected point is cached
    so later trips need one call.
    """
    endpoints = [start, finish]
    relocated: set[int] = set()
    while True:
        try:
            return openrouteservice.directions(
                (start['latitude'], start['longitude']),
                (finish['latitude'], finish['longitude']),
            )
        except openrouteservice.RoutingError as exc:
            point = exc.point if exc.code == openrouteservice.UNROUTABLE_POINT else None
            if point is None or point in relocated:
                raise
            relocated.add(point)
            if not relocate_by_geocoding(endpoints[point]):
                raise TripError(
                    f'"{endpoints[point]["query"]}" is not near a drivable road; '
                    'try a nearby town or a street address.',
                ) from exc


def relocate_by_geocoding(location: Location) -> bool:
    """Move a Census-resolved city to the geocoder's point for it, in place. Returns whether it moved.

    Only "City, ST" inputs qualify: coordinates are the user's own, and anything else was already
    located by the geocoder, so asking again wouldn't give a different point.
    """
    if not is_city_query(location['query']):
        return False
    found = openrouteservice.geocode(location['query'])
    if found is None or (found['latitude'], found['longitude']) == (location['latitude'], location['longitude']):
        return False
    location['latitude'], location['longitude'] = found['latitude'], found['longitude']
    logger.info('Relocated %r to %s, %s to reach a road', location['query'], found['latitude'], found['longitude'])
    with contextlib.suppress(DatabaseError):  # caching is an optimisation only
        cache.set(road_point_key(location['query']), found, ROAD_POINT_CACHE_SECONDS)
    return True


def is_city_query(query: str) -> bool:
    """Whether a query is a "City, ST" name resolved offline from the Census files."""
    return not COORDINATES.match(query) and places.lookup_query(query) is not None


def road_point_key(query: str) -> str:
    """Return the cache key for a city's corrected (road-reachable) location."""
    return 'road-point:' + hashlib.sha256(collapse_whitespace(query).lower().encode()).hexdigest()


def save_trip(trip: Trip) -> None:
    """Remember a planned trip for the saved-trips dropdowns, counting how often it's planned."""
    key = {'start': collapse_whitespace(trip['start']['query']), 'finish': collapse_whitespace(trip['finish']['query'])}
    details = {
        'start_label': trip['start']['label'],
        'finish_label': trip['finish']['label'],
        'distance_miles': trip['distance_miles'],
        'total_fuel_cost': trip['total_fuel_cost'],
    }
    bump = {'times_planned': F('times_planned') + 1, 'last_planned_at': timezone.now()}
    if SavedTrip.objects.filter(**key).update(**details, **bump):
        return
    try:
        SavedTrip.objects.create(**key, **details)
    except IntegrityError:  # another request saved it first
        SavedTrip.objects.filter(**key).update(**details, **bump)


def resolve_location(query: str) -> Location:
    """Resolve user input ("latitude,longitude", "City, ST" or any address) to a location in the USA."""
    match = COORDINATES.match(query)
    if match:
        latitude, longitude = float(match[1]), float(match[2])
        # Names are only ever resolved within the US; raw coordinates need checking.
        if not in_usa(latitude, longitude):
            raise TripError(f'"{query}" is not within the USA.')
        return Location(query=query, label=f'{latitude}, {longitude}', latitude=latitude, longitude=longitude)

    found: places.PlaceMatch | openrouteservice.GeocodeResult | None = (
        cached_road_point(query) or places.lookup_query(query) or openrouteservice.geocode(query)
    )
    if found is None:
        raise TripError(f'Could not find "{query}" in the USA.')
    return Location(query=query, label=found['label'], latitude=found['latitude'], longitude=found['longitude'])


def cached_road_point(query: str) -> openrouteservice.GeocodeResult | None:
    """Return a previously corrected location for this city (see `relocate_by_geocoding`), or None."""
    try:
        return cache.get(road_point_key(query))
    except DatabaseError:
        return None


def stop_data(purchase: Purchase) -> FuelStop:
    """Return the API representation of one fuel stop."""
    station = purchase.candidate.station
    return FuelStop(
        opis_id=station.opis_id,
        name=station.name,
        address=station.address,
        city=station.city,
        state=station.state,
        latitude=purchase.candidate.latitude,
        longitude=purchase.candidate.longitude,
        miles_from_start=round(purchase.candidate.mile, 1),
        detour_miles=round(purchase.candidate.detour_miles, 1),
        price_per_gallon=round(purchase.candidate.price, 3),
        gallons=round(purchase.gallons, 2),
        cost=round(purchase.cost, 2),
    )


def build_map(coordinates: list[list[float]], stops: list[FuelStop]) -> dict[str, Any]:
    """Return a GeoJSON FeatureCollection with the route line (simplified for drawing) and a point per fuel stop."""
    features = [
        {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': simplify_line(coordinates, MAP_TOLERANCE_DEGREES)},
            'properties': {'kind': 'route'},
        }
    ]
    for number, stop in enumerate(stops, start=1):
        features.append(
            {
                'type': 'Feature',
                'geometry': {'type': 'Point', 'coordinates': [stop['longitude'], stop['latitude']]},
                'properties': {'kind': 'fuel_stop', 'stop': number, **stop},
            }
        )
    return {'type': 'FeatureCollection', 'features': features}
