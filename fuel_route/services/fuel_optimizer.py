"""Decide where to stop for fuel along a route, and how much to buy at each stop.

Planning happens in two steps:

1. ``choose_stops`` picks *which* stations to stop at. It minimises fuel cost plus a fixed
   cost per stop, so the plan doesn't stop for a fraction of a cent per gallon.
2. ``cheapest_purchases`` decides *how much* to buy at those stations, exactly, with the
   classic greedy rule: if a cheaper station is within range, buy just enough to reach it;
   otherwise fill up and drive to the cheapest station within range.

The vehicle starts empty and fills up at the cheapest station near the start (or, if none is
near, the first station along the route). Its range is measured from that station, and the fuel
for the miles before it is bought there too, so every mile's fuel is paid for; it arrives with
an empty tank. Only if the route has no reachable station does it start with a full tank.
"""

import math
from dataclasses import dataclass

from .route_stations import Candidate

FUEL_STEP_MILES = 5.0  # fuel resolution used when choosing stops

START_SEARCH_MILES = 25.0  # how far along the route to look for the first fill-up
# Floating-point slack: purchases smaller than this many miles of fuel are rounding noise, and
# distances this close to a whole number of fuel steps count as that number.
MIN_PURCHASE_MILES = 1e-6
STEP_TOLERANCE = 1e-9


class NoFuelPlanError(Exception):
    """The route has a stretch longer than the vehicle's range with no station on it."""


@dataclass(slots=True)
class Purchase:
    """Fuel bought at one stop."""

    candidate: Candidate
    gallons: float

    @property
    def cost(self) -> float:
        """Cost of this purchase in dollars."""
        return self.gallons * self.candidate.price


def plan_fuel_stops(
    candidates: list[Candidate], total_miles: float, range_miles: float, mpg: float, stop_cost: float = 0.0
) -> list[Purchase]:
    """Return the purchases for a trip. `candidates` must be sorted by `mile`.

    The first purchase is at `first_fill_up(...)`; stations before it are passed by. Each stop
    has a fixed cost: `stop_cost` dollars plus the fuel for its detour off the route. When any
    stop has one, stops are chosen to minimise fuel plus those costs; the detour fuel is then
    bought at the stop, on top of the fuel for the route itself.
    """
    start = first_fill_up(candidates, range_miles)
    others = [c for c in candidates if start is None or (c is not start and c.mile >= start.mile)]
    if stop_cost > 0 or any(c.detour_miles for c in others):
        others = choose_stops(others, total_miles, range_miles, mpg, stop_cost, start)
    purchases = cheapest_purchases(others, total_miles, range_miles, mpg, start)
    for purchase in purchases:
        purchase.gallons += purchase.candidate.detour_miles / mpg
    return purchases


def stop_fixed_cost(candidate: Candidate, mpg: float, stop_cost: float) -> float:
    """Cost of stopping at a station regardless of how much is bought: the stop itself plus the detour fuel."""
    return stop_cost + candidate.detour_miles / mpg * candidate.price


def first_fill_up(candidates: list[Candidate], range_miles: float) -> Candidate | None:
    """Pick the station for the first fill-up.

    That's the best value within START_SEARCH_MILES of the start, else the first station within
    range, else None (the trip then starts with a full tank). "Best value" counts the detour too,
    spread over the tankful the first stop typically buys: price x (1 + detour / range).
    """
    nearby = [c for c in candidates if c.mile <= START_SEARCH_MILES]
    if nearby:
        return min(nearby, key=lambda c: c.price * (1 + c.detour_miles / range_miles))
    if candidates and candidates[0].mile <= range_miles:
        return candidates[0]
    return None


def cheapest_purchases(
    candidates: list[Candidate], total_miles: float, range_miles: float, mpg: float, start: Candidate | None = None
) -> list[Purchase]:
    """Exact minimum-cost purchases using only `candidates` (sorted by `mile`, none before `start`).

    With a `start` station the tank starts empty and is filled there, including the fuel for the
    miles before it; without one the trip starts at mile 0 with a full tank.
    """
    # Nodes: start, stations, and the finish (cheaper than anything, so we aim for it).
    first = (start.mile, start.price, start) if start else (0.0, math.inf, None)
    nodes = [first] + [(c.mile, c.price, c) for c in candidates] + [(total_miles, -math.inf, None)]
    last = len(nodes) - 1
    fuel = 0.0 if start else range_miles  # in miles of driving
    purchases = []
    i = 0
    while i != last:
        mile, price, candidate = nodes[i]
        reachable = []
        j = i + 1
        while j <= last and nodes[j][0] - mile <= range_miles:
            reachable.append(j)
            j += 1
        if not reachable:
            raise NoFuelPlanError(f'No fuel station within {range_miles:g} miles after mile {mile:.0f} of the route.')

        cheaper = next((k for k in reachable if nodes[k][1] < price), None)
        if cheaper is not None:
            target = cheaper
            buy = max(0.0, (nodes[target][0] - mile) - fuel)
        else:
            target = min(reachable, key=lambda k: nodes[k][1])
            buy = range_miles - fuel

        # Only a station buys: without a start station the trip starts full, so the start never needs to.
        if buy > MIN_PURCHASE_MILES and candidate is not None:
            purchases.append(Purchase(candidate, buy / mpg))
        fuel = max(0.0, fuel + buy - (nodes[target][0] - mile))
        i = target

    if start and start.mile > 0:
        # Fuel for the drive from the trip's start to the first station, priced at that station.
        lead_in = start.mile / mpg
        if purchases and purchases[0].candidate is start:
            purchases[0].gallons += lead_in
        else:
            purchases.insert(0, Purchase(start, lead_in))
    return purchases


def choose_stops(
    candidates: list[Candidate],
    total_miles: float,
    range_miles: float,
    mpg: float,
    stop_cost: float,
    start: Candidate | None = None,
) -> list[Candidate]:
    """Pick the stations to stop at, minimising fuel cost + each stop's fixed cost (`stop_fixed_cost`).

    Dynamic programming over (station, fuel level), with fuel in FUEL_STEP_MILES units and
    distances rounded up, so the chosen stops are always reachable. With a `start` station the
    tank starts empty at that station's position and is filled there (always a stop, so not
    returned); otherwise it starts full at mile 0. Returns the chosen candidates; amounts are
    then computed exactly by `cheapest_purchases`.
    """
    # Nodes: (mile, price, fixed cost of stopping). The start is always a stop, so its fixed cost doesn't matter.
    first = (start.mile, start.price, 0.0) if start else (0.0, 0.0, 0.0)
    nodes = (
        [first]
        + [(c.mile, c.price, stop_fixed_cost(c, mpg, stop_cost)) for c in candidates]
        + [(total_miles, 0.0, 0.0)]
    )
    levels = int(range_miles // FUEL_STEP_MILES)
    n = len(nodes)
    # cost[i][f]: cheapest way to arrive at node i with f units of fuel.
    # arrived_from[i][f]: (previous node, fuel level after buying there).
    cost = [[math.inf] * (levels + 1) for _ in range(n)]
    arrived_from: list[list[tuple[int, int] | None]] = [[None] * (levels + 1) for _ in range(n)]
    # bought_from[i][g]: arrival level before buying up to g at node i (None if nothing bought)
    bought_from: list[list[int | None]] = [[None] * (levels + 1) for _ in range(n)]
    cost[0][0 if start else levels] = 0.0

    for i in range(n - 1):
        mile, price, fixed_cost = nodes[i]
        after = cost[i][:]
        if i > 0 or start:
            unit_price = FUEL_STEP_MILES / mpg * price
            best: float = math.inf
            best_from: int | None = None
            for g in range(levels + 1):
                # Cheapest way to leave with g units after buying something here (arrived with f <= g).
                best += unit_price
                if cost[i][g] + fixed_cost < best:
                    best, best_from = cost[i][g] + fixed_cost, g
                if best < after[g]:
                    after[g] = best
                    bought_from[i][g] = best_from
        for j in range(i + 1, n):
            needed = math.ceil((nodes[j][0] - mile) / FUEL_STEP_MILES - STEP_TOLERANCE)
            if needed > levels:
                break
            for g in range(needed, levels + 1):
                if after[g] < cost[j][g - needed]:
                    cost[j][g - needed] = after[g]
                    arrived_from[j][g - needed] = (i, g)

    level = min(range(levels + 1), key=lambda f: cost[-1][f])
    if math.isinf(cost[-1][level]):
        # Unreachable; let cheapest_purchases report where the gap is.
        return candidates

    chosen: list[Candidate] = []
    node = n - 1
    while node != 0:
        step = arrived_from[node][level]
        if step is None:  # can't happen for a finite cost; guards the backtracking
            return candidates
        prev, leave_level = step
        start_level = bought_from[prev][leave_level]
        if start_level is not None:
            if prev > 0:
                chosen.append(candidates[prev - 1])
            level = start_level
        else:
            level = leave_level
        node = prev
    return chosen[::-1]
