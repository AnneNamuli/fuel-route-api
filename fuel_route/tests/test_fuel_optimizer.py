"""Tests for choosing fuel stops and how much to buy."""

from django.test import SimpleTestCase

from fuel_route.services.fuel_optimizer import NoFuelPlanError, cheapest_purchases, plan_fuel_stops
from fuel_route.tests.helpers import candidates, summary


class CheapestPurchasesTests(SimpleTestCase):
    """Exact minimum-cost purchases (500 mi range, 10 mpg)."""

    def plan(self, stops, total):
        """Plan purchases for the given stops and trip length."""
        return summary(cheapest_purchases(candidates(*stops), total, range_miles=500, mpg=10))

    def test_no_stop_when_finish_is_within_range(self):
        """A trip shorter than the range needs no fuel."""
        self.assertEqual(self.plan([('A', 100, 3.0)], total=450), [])

    def test_buys_just_enough_to_reach_a_cheaper_station(self):
        """At a pricey station, buy only what's needed to reach a cheaper one."""
        # Start full (500 mi); B is out of reach, so stop at pricey A (100 mi left) and buy only
        # the 100 mi needed to reach cheaper B, then buy the 300 mi to the finish at B.
        stops = [('A', 400, 4.0), ('B', 600, 3.0)]
        self.assertEqual(self.plan(stops, total=900), [('A', 10.0), ('B', 30.0)])

    def test_fills_up_when_nothing_cheaper_is_in_range(self):
        """At the cheapest station in range, fill the tank."""
        # At A: nothing cheaper within 500 mi and the finish (1000) is out of range, so fill up
        # (45 gal), drive to B (350 mi), then buy just enough to finish (200 - 150 mi = 5 gal).
        stops = [('A', 450, 3.0), ('B', 800, 4.0)]
        self.assertEqual(self.plan(stops, total=1000), [('A', 45.0), ('B', 5.0)])

    def test_gap_longer_than_range_is_an_error(self):
        """A stretch longer than the range with no station can't be planned."""
        with self.assertRaises(NoFuelPlanError):
            self.plan([('A', 100, 3.0)], total=700)


class StopCostTests(SimpleTestCase):
    """Trading a little fuel cost for fewer stops."""

    # Buy just enough at S to reach cheap X and fill up there; the cheapest station in range after X
    # is Y, just 20 miles on, so the lowest-cost plan tops up 2 gallons there to save 10 cents/gallon
    # on them at Z.
    STOPS = [('S', 5, 3.50), ('X', 450, 2.80), ('Y', 470, 2.90), ('Z', 900, 3.00)]

    def test_without_stop_cost_chases_every_saving(self):
        """With no stop cost, even a 20-cent saving is worth an extra stop."""
        purchases = plan_fuel_stops(candidates(*self.STOPS), 1300, 500, 10, stop_cost=0)
        self.assertEqual(summary(purchases), [('S', 45.0), ('X', 50.0), ('Y', 2.0), ('Z', 33.0)])

    def test_stop_cost_skips_stops_that_save_little(self):
        """With a $5 stop cost, the top-up at Y is skipped."""
        purchases = plan_fuel_stops(candidates(*self.STOPS), 1300, 500, 10, stop_cost=5)
        self.assertEqual(summary(purchases), [('S', 45.0), ('X', 50.0), ('Z', 35.0)])


class StartingFuelTests(SimpleTestCase):
    """The tank starts empty and is filled near the start, so all fuel is paid for."""

    def test_fills_up_near_the_start_for_a_short_trip(self):
        """A trip shorter than the range still buys all its fuel, at the cheapest station near the start."""
        stops = [('NEAR', 5, 3.20), ('CHEAPER NEAR', 20, 3.00), ('LATER', 100, 2.50)]
        purchases = plan_fuel_stops(candidates(*stops), 300, 500, 10, stop_cost=5)
        # Buying the last 200 miles (20 gal) at LATER saves $0.50/gal = $10, more than the $5 stop cost.
        self.assertEqual(summary(purchases), [('CHEAPER NEAR', 10.0), ('LATER', 20.0)])
        self.assertAlmostEqual(sum(p.gallons for p in purchases), 30.0)

    def test_total_fuel_covers_the_whole_trip(self):
        """Gallons bought equal distance / mpg on a long trip."""
        stops = [('A', 10, 3.0), ('B', 450, 3.1), ('C', 900, 2.9)]
        purchases = plan_fuel_stops(candidates(*stops), 1200, 500, 10, stop_cost=5)
        self.assertEqual(purchases[0].candidate.station, 'A')
        self.assertAlmostEqual(sum(p.gallons for p in purchases), 120.0)

    def test_range_is_measured_from_the_first_station(self):
        """The first fill-up at mile 200 can reach a station at mile 650 (450 miles on)."""
        purchases = plan_fuel_stops(candidates(('FIRST', 200, 3.0), ('NEXT', 650, 2.5)), 1000, 500, 10)
        # FIRST buys the 200 lead-in miles plus 450 to reach cheaper NEXT; NEXT buys the last 350.
        self.assertEqual(summary(purchases), [('FIRST', 65.0), ('NEXT', 35.0)])

    def test_near_start_station_keeps_its_full_range(self):
        """A station 20 miles in reaches one 495 miles further on, which a fill-up at mile 0 couldn't."""
        purchases = plan_fuel_stops(candidates(('NEAR', 20, 3.0), ('FAR', 515, 2.5)), 900, 500, 10)
        self.assertEqual(summary(purchases), [('NEAR', 51.5), ('FAR', 38.5)])

    def test_starts_full_when_the_route_has_no_station(self):
        """Without any station on a trip shorter than the range, nothing is bought."""
        self.assertEqual(plan_fuel_stops([], 450, 500, 10), [])

    def test_uses_first_station_when_none_is_near_the_start(self):
        """With no station in the first 25 miles, the first station along the route is the first fill-up."""
        purchases = plan_fuel_stops(candidates(('A', 100, 3.0)), 450, 500, 10)
        self.assertEqual(summary(purchases), [('A', 45.0)])


class DetourTests(SimpleTestCase):
    """Stations off the route cost the fuel to get there and back."""

    def stations(self, off_route_miles):
        """START near the start, then ON (on the route) and OFF (a cent cheaper, `off_route_miles` away)."""
        stops = candidates(('START', 5, 3.00), ('ON', 400, 3.00), ('OFF', 400, 2.99))
        stops[2].off_route_miles = off_route_miles
        return stops

    def test_cheaper_station_off_the_route_loses_when_the_detour_costs_more(self):
        """Saving 1c/gal on ~40 gallons ($0.40) isn't worth a 16-mile round trip (1.6 gal, $4.78)."""
        purchases = plan_fuel_stops(self.stations(8), 800, 500, 10)
        self.assertNotIn('OFF', [p.candidate.station for p in purchases])

    def test_detour_fuel_is_bought_at_the_stop(self):
        """Stopping off the route adds the out-and-back fuel to that purchase and to the total."""
        stops = candidates(('START', 5, 3.00), ('OFF', 400, 2.50))
        stops[1].off_route_miles = 3
        purchases = plan_fuel_stops(stops, 800, 500, 10)
        self.assertEqual([p.candidate.station for p in purchases], ['START', 'OFF'])
        self.assertAlmostEqual(purchases[1].gallons, 40.0 + 0.6)  # 400 route miles + 6 detour miles
        self.assertAlmostEqual(sum(p.gallons for p in purchases), 80.6)

    def test_first_fill_up_counts_the_detour(self):
        """Near the start, a station on the route beats one a cent cheaper but 10 miles off it."""
        stops = candidates(('OFF', 5, 2.99), ('ON', 10, 3.00))
        stops[0].off_route_miles = 10
        purchases = plan_fuel_stops(stops, 300, 500, 10)
        self.assertEqual([p.candidate.station for p in purchases], ['ON'])
