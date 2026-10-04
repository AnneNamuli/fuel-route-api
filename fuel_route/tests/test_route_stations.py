"""Tests for finding the stations along a route."""

from django.test import SimpleTestCase

from fuel_route.services.route_stations import Candidate, cheapest_per_position, match_to_route, sample_route
from fuel_route.tests.helpers import candidates


class MatchToRouteTests(SimpleTestCase):
    """Matching stations to the route."""

    def test_matches_stations_at_high_latitude(self):
        """Near the Arctic, a station 9 miles east of the route is still matched (degrees of longitude are short)."""
        samples = sample_route([[-150.0, 64.0], [-150.0, 65.0], [-150.0, 66.0]])
        east = Candidate('east', latitude=65.0, longitude=-150.0 + 9 / (69.0 * 0.4226), price=3.0)
        self.assertEqual([c.station for c in match_to_route([east], samples, max_distance_miles=10)], ['east'])

    def test_keeps_nearby_stations_with_their_position(self):
        """Stations within the distance limit are kept, with their mile along the route."""
        # Route due east along the equator for ~69 miles (1 degree).
        samples = sample_route([[0.0, 0.0], [0.5, 0.0], [1.0, 0.0]])
        near = Candidate('near', latitude=0.05, longitude=0.5, price=3.0)  # ~3.5 mi off route, half way
        far = Candidate('far', latitude=1.0, longitude=0.5, price=3.0)  # ~69 mi off route
        matched = match_to_route([far, near], samples, max_distance_miles=10)
        self.assertEqual([c.station for c in matched], ['near'])
        self.assertAlmostEqual(matched[0].mile, 34.5, delta=1.5)
        self.assertAlmostEqual(matched[0].off_route_miles, 3.45, delta=0.1)
        self.assertAlmostEqual(matched[0].detour_miles, 6.9, delta=0.2)


class CheapestPerPositionTests(SimpleTestCase):
    """Dropping stations that share a position with a cheaper one."""

    def test_keeps_only_the_cheapest_at_each_position(self):
        """Of stations at the same mile, only the cheapest is kept; order by mile is preserved."""
        stops = [('A', 10, 3.0), ('B', 10, 3.2), ('C', 50, 2.9), ('D', 50, 3.1), ('E', 90, 3.5)]
        kept = cheapest_per_position(candidates(*stops))  # already sorted by mile, then price
        self.assertEqual([c.station for c in kept], ['A', 'C', 'E'])

    def test_keeps_stations_at_the_same_mile_but_different_distance_from_the_route(self):
        """Same point along the route but a different detour is a different location, so both stay."""
        stops = candidates(('NEAR', 10, 3.0), ('FAR', 10, 2.9))
        stops[1].off_route_miles = 8
        stops.sort(key=lambda c: (c.mile, c.price))
        self.assertEqual(sorted(c.station for c in cheapest_per_position(stops)), ['FAR', 'NEAR'])
