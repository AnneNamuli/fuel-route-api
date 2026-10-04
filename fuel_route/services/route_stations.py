"""Find the fuel stations along a route and where each one is along it."""

import math
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from fuel_route.models import FuelStation

from .geo import MILES_PER_DEGREE, haversine_miles, miles_to_degrees

SAMPLE_SPACING_MILES = 1.0

Sample = tuple[float, float, float]  # (latitude, longitude, miles_from_start)


@dataclass(slots=True)
class Candidate:
    """A fuel station near the route, with its price and position along the route."""

    station: Any  # a FuelStation in the app; anything in tests
    latitude: float
    longitude: float
    price: float
    mile: float = 0.0
    off_route_miles: float = 0.0  # straight-line distance from the route (an estimate: positions are city-level)

    @property
    def detour_miles(self) -> float:
        """Extra driving to reach the station and get back on the route."""
        return 2 * self.off_route_miles


def stations_along_route(coordinates: list[list[float]], total_miles: float) -> list[Candidate]:
    """Return stations near the route with their lowest price and position (in route miles), by position."""
    samples = sample_route(coordinates)
    max_distance = settings.STATION_MAX_DISTANCE_MILES
    candidates = cheapest_per_position(match_to_route(stations_in_area(samples, max_distance), samples, max_distance))
    # Scale positions to the routing service's distance so they agree with total_miles.
    measured = samples[-1][2]
    scale = total_miles / measured if measured else 1.0
    for candidate in candidates:
        candidate.mile *= scale
    return candidates


def cheapest_per_position(candidates: list[Candidate]) -> list[Candidate]:
    """Keep only the cheapest station at each location (input sorted by mile, then price).

    Station coordinates are city-level, so many stations share a location: the same point along
    the route and the same distance from it. Of those, only the cheapest can ever be the best
    place to buy, so dropping the rest can't change the plan; it just gives the fuel optimizer
    less to search.
    """
    kept: list[Candidate] = []
    seen: set[tuple[float, float]] = set()
    for candidate in candidates:
        location = (candidate.mile, candidate.off_route_miles)
        if location not in seen:
            seen.add(location)
            kept.append(candidate)
    return kept


def stations_in_area(samples: list[Sample], margin_miles: float) -> list[Candidate]:
    """Return candidates for stations in the route's bounding box (plus a margin), at their lowest price."""
    latitudes = [s[0] for s in samples]
    longitudes = [s[1] for s in samples]
    highest = max(abs(min(latitudes)), abs(max(latitudes)))
    latitude_margin, longitude_margin = miles_to_degrees(margin_miles, highest + margin_miles / MILES_PER_DEGREE)
    stations = (
        FuelStation.objects.filter(
            latitude__range=(min(latitudes) - latitude_margin, max(latitudes) + latitude_margin),
            longitude__range=(min(longitudes) - longitude_margin, max(longitudes) + longitude_margin),
        )
        .with_lowest_price()
        .filter(min_price__isnull=False)
    )
    return [Candidate(s, s.latitude, s.longitude, float(s.min_price)) for s in stations]


def sample_route(coordinates: list[list[float]]) -> list[Sample]:
    """Turn [longitude, latitude] route coordinates into samples ~1 mile apart.

    Each sample is (latitude, longitude, miles_from_start).
    """
    samples: list[Sample] = []
    travelled = 0.0
    prev = None
    for longitude, latitude in coordinates:
        if prev is not None:
            travelled += haversine_miles(prev[0], prev[1], latitude, longitude)
        if not samples or travelled - samples[-1][2] >= SAMPLE_SPACING_MILES:
            samples.append((latitude, longitude, travelled))
        prev = (latitude, longitude)
    if prev is not None and samples[-1][2] != travelled:
        samples.append((prev[0], prev[1], travelled))
    return samples


def match_to_route(candidates: list[Candidate], samples: list[Sample], max_distance_miles: float) -> list[Candidate]:
    """Keep candidates within `max_distance_miles` of the route, setting `mile` to their position along it.

    Returns the matched candidates sorted by position.
    """
    # Bucket samples in a latitude/longitude grid whose cells are at least max_distance wide at the
    # route's highest latitude, so each candidate only needs to check its own and neighbouring cells.
    highest = max((abs(s[0]) for s in samples), default=0.0)
    cell = max(miles_to_degrees(max_distance_miles, highest))
    grid: dict[tuple[int, int], list[Sample]] = {}
    for latitude, longitude, mile in samples:
        key = (math.floor(latitude / cell), math.floor(longitude / cell))
        grid.setdefault(key, []).append((latitude, longitude, mile))

    matched: list[Candidate] = []
    for candidate in candidates:
        row, col = math.floor(candidate.latitude / cell), math.floor(candidate.longitude / cell)
        best = None
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for latitude, longitude, mile in grid.get((row + dr, col + dc), ()):
                    distance = haversine_miles(candidate.latitude, candidate.longitude, latitude, longitude)
                    if distance <= max_distance_miles and (best is None or distance < best[0]):
                        best = (distance, mile)
        if best is not None:
            candidate.off_route_miles, candidate.mile = best
            matched.append(candidate)
    return sorted(matched, key=lambda c: (c.mile, c.price))
