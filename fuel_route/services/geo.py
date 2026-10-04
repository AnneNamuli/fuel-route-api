"""Distance helpers and the US border check."""

import gzip
import itertools
import math
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

Ring = list[tuple[float, float]]  # (longitude, latitude) points
BoundingBox = tuple[float, float, float, float]  # min_longitude, min_latitude, max_longitude, max_latitude

EARTH_RADIUS_MILES = 3958.8
MILES_PER_DEGREE = 69.17
# US outline (50 states, DC, Puerto Rico) from the Census 1:5,000,000 cartographic boundary file.
US_OUTLINE_FILE = Path(__file__).parent / 'data' / 'cb_2024_us_nation_5m.kml.gz'
# The outline is simplified, so allow points this close outside it (e.g. on the coast).
BORDER_TOLERANCE_MILES = 1.0
# Cap for latitude in degree conversions: longitude degrees shrink to nothing at the poles.
MAX_LATITUDE = 85.0


def haversine_miles(latitude1: float, longitude1: float, latitude2: float, longitude2: float) -> float:
    """Great-circle distance in miles between two latitude/longitude points."""
    latitude1, longitude1, latitude2, longitude2 = map(math.radians, (latitude1, longitude1, latitude2, longitude2))
    a = (
        math.sin((latitude2 - latitude1) / 2) ** 2
        + math.cos(latitude1) * math.cos(latitude2) * math.sin((longitude2 - longitude1) / 2) ** 2
    )
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def miles_to_degrees(miles: float, latitude: float) -> tuple[float, float]:
    """Return how many degrees of (latitude, longitude) cover `miles` at a given latitude."""
    latitude = min(abs(latitude), MAX_LATITUDE)
    return miles / MILES_PER_DEGREE, miles / (MILES_PER_DEGREE * math.cos(math.radians(latitude)))


def simplify_line(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Douglas-Peucker: drop points within `tolerance` (in coordinate units) of the simplified line.

    Keeps the first and last points and every point needed to stay within `tolerance` of the
    original shape, so a straight stretch of road collapses to its two ends.
    """
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    pending = [(0, len(points) - 1)]
    while pending:  # iterative, so long routes can't hit the recursion limit
        first, last = pending.pop()
        (x1, y1), (x2, y2) = points[first][:2], points[last][:2]
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        farthest, farthest_distance = None, tolerance
        for i in range(first + 1, last):
            x, y = points[i][:2]
            # Distance to the line through the segment's ends (or to the point, if they coincide).
            distance = abs(dy * x - dx * y + x2 * y1 - y2 * x1) / length if length else math.hypot(x - x1, y - y1)
            if distance > farthest_distance:
                farthest, farthest_distance = i, distance
        if farthest is not None:
            keep[farthest] = True
            pending.extend([(first, farthest), (farthest, last)])
    return [point for point, kept in zip(points, keep, strict=True) if kept]


@lru_cache(maxsize=1)
def us_polygons() -> list[tuple[BoundingBox, Ring]]:
    """Load the US outline as (bounding box, ring) pairs; rings are lists of (longitude, latitude)."""
    namespace = '{http://www.opengis.net/kml/2.2}'
    with gzip.open(US_OUTLINE_FILE, 'rb') as f:
        tree = ET.parse(f)
    polygons: list[tuple[BoundingBox, Ring]] = []
    for coordinates in tree.iter(f'{namespace}coordinates'):
        ring: Ring = []
        for point in (coordinates.text or '').split():
            longitude, latitude = point.split(',')[:2]
            ring.append((float(longitude), float(latitude)))
        longitudes = [p[0] for p in ring]
        latitudes = [p[1] for p in ring]
        bbox = (min(longitudes), min(latitudes), max(longitudes), max(latitudes))
        polygons.append((bbox, ring))
    return polygons


def in_usa(latitude: float, longitude: float, tolerance_miles: float = BORDER_TOLERANCE_MILES) -> bool:
    """Whether a point is inside the US outline, or within `tolerance_miles` of it."""
    margin = max(miles_to_degrees(tolerance_miles, latitude))
    near = [
        ring
        for (min_longitude, min_latitude, max_longitude, max_latitude), ring in us_polygons()
        if min_longitude - margin <= longitude <= max_longitude + margin
        and min_latitude - margin <= latitude <= max_latitude + margin
    ]
    if any(_inside(longitude, latitude, ring) for ring in near):
        return True
    return any(_distance_to_ring_miles(longitude, latitude, ring) <= tolerance_miles for ring in near)


def _inside(x: float, y: float, ring: Ring) -> bool:
    """Ray-casting point-in-polygon test for one ring of (x, y) points."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _distance_to_ring_miles(longitude: float, latitude: float, ring: Ring) -> float:
    """Approximate distance in miles from a point to the nearest edge of a ring (fine over a few miles)."""
    scale_x = MILES_PER_DEGREE * math.cos(math.radians(latitude))
    points = [
        ((ring_longitude - longitude) * scale_x, (ring_latitude - latitude) * MILES_PER_DEGREE)
        for ring_longitude, ring_latitude in ring
    ]
    best = math.inf
    for (x1, y1), (x2, y2) in itertools.pairwise(points):
        dx, dy = x2 - x1, y2 - y1
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length))
        best = min(best, math.hypot(x1 + t * dx, y1 + t * dy))
    return best
