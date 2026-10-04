"""Shared test helpers."""

from fuel_route.services.route_stations import Candidate


def candidates(*stops):
    """Build candidates from (name, mile, price) tuples."""
    return [Candidate(station=name, latitude=0, longitude=0, price=price, mile=mile) for name, mile, price in stops]


def summary(purchases):
    """(station, gallons) pairs for easy comparison."""
    return [(p.candidate.station, round(p.gallons, 2)) for p in purchases]
