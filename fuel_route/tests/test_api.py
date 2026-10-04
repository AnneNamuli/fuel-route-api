"""Tests for the route API endpoints."""

from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase

from fuel_route.models import FuelPrice, FuelStation, SavedTrip
from fuel_route.schema import MAJOR_CITIES
from fuel_route.services.openrouteservice import UNROUTABLE_POINT, RoutingError
from fuel_route.throttling import TripRateThrottle

DIRECTIONS = 'fuel_route.services.trip_planner.openrouteservice.directions'
GEOCODE = 'fuel_route.services.trip_planner.openrouteservice.geocode'


class RouteApiTests(TestCase):
    """GET /api/trips/ with the routing API mocked."""

    def setUp(self):
        """Mock a ~530 mile straight route and create three stations along it."""
        cache.clear()
        self.route = {
            'distance_miles': 530.0,
            'duration_seconds': 8 * 3600,
            'coordinates': [[-100 + i * 0.1, 40.0] for i in range(101)],  # along latitude 40
        }
        patcher = mock.patch(DIRECTIONS, return_value=self.route)
        self.directions = patcher.start()
        self.addCleanup(patcher.stop)

        for opis_id, longitude, prices in [(1, -97.0, ['3.50']), (2, -95.0, ['3.20', '3.10']), (3, -93.0, ['3.40'])]:
            station = FuelStation.objects.create(
                opis_id=opis_id,
                name=f'STOP {opis_id}',
                address='I-80',
                city='Town',
                state='KS',
                rack_id=1,
                latitude=40.0,
                longitude=longitude,
            )
            for price in prices:
                FuelPrice.objects.create(station=station, name=station.name, retail_price=Decimal(price))

    def plan(self, start='40.0,-100.0', finish='40.0,-90.0'):
        """Call the route API."""
        return self.client.get('/api/trips/', {'start': start, 'finish': finish})

    def test_returns_route_cheapest_stop_and_cost(self):
        """The response has the distance, the cheapest stop, the cost and a GeoJSON map."""
        response = self.plan()

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['distance_miles'], 530.0)
        # No station within 25 miles of the start, so station 1 is the first fill-up; it buys just
        # enough to reach station 2, the cheapest (lowest of its prices: 3.10), which buys the rest.
        self.assertEqual([s['opis_id'] for s in data['fuel_stops']], [1, 2])
        self.assertEqual(data['fuel_stops'][1]['price_per_gallon'], 3.1)
        self.assertAlmostEqual(data['total_gallons'], 53.0, places=1)
        self.assertAlmostEqual(data['total_fuel_cost'], sum(s['cost'] for s in data['fuel_stops']), places=2)
        self.assertIn('No fuel station within 25 miles of the start', data['notes'][0])
        self.assertEqual(data['map']['features'][0]['geometry']['type'], 'LineString')
        self.assertEqual([f['properties'].get('opis_id') for f in data['map']['features']], [None, 1, 2])
        self.directions.assert_called_once()
        self.assertTrue(data['map_url'].startswith('http://testserver/map/?start='))

    def test_fills_up_near_the_start_and_pays_for_every_mile(self):
        """With a station near the start, all 53 gallons (530 mi / 10 mpg) are bought."""
        station = FuelStation.objects.create(
            opis_id=9,
            name='NEAR START',
            address='I-80',
            city='Town',
            state='KS',
            rack_id=1,
            latitude=40.0,
            longitude=-99.9,
        )
        FuelPrice.objects.create(station=station, name=station.name, retail_price=Decimal('3.30'))

        data = self.plan().json()

        self.assertEqual(data['fuel_stops'][0]['opis_id'], 9)
        self.assertAlmostEqual(data['total_gallons'], 53.0, places=1)
        self.assertEqual(data['notes'], [])

    def test_city_state_is_resolved_without_geocoding_api(self):
        """'City, ST' inputs don't call the geocoding API."""
        with mock.patch(GEOCODE) as geocode:
            response = self.plan('Topeka, KS', 'Kansas City, MO')
        self.assertEqual(response.status_code, 200)
        geocode.assert_not_called()

    def test_rejects_location_outside_usa(self):
        """Coordinates outside the USA are rejected."""
        response = self.plan(start='51.5,-0.12')  # London
        self.assertEqual(response.status_code, 400)
        self.assertIn('not within the USA', response.json()['detail'])

    def test_unknown_place(self):
        """A place the geocoder can't find is a 400."""
        with mock.patch(GEOCODE, return_value=None):
            response = self.plan(start='Atlantis')
        self.assertEqual(response.status_code, 400)

    def test_routing_failure_is_reported(self):
        """Routing API errors are passed on with their status."""
        self.directions.side_effect = RoutingError('OpenRouteService quota exceeded', status=503)
        response = self.plan()
        self.assertEqual(response.status_code, 503)
        self.assertIn('quota', response.json()['detail'])

    def test_no_station_in_range(self):
        """A route with no reachable station is a 422."""
        FuelStation.objects.all().delete()
        response = self.plan()
        self.assertEqual(response.status_code, 422)

    def test_missing_input(self):
        """Missing fields are reported per field."""
        response = self.client.get('/api/trips/', {'start': 'Denver, CO'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(), {'detail': 'Invalid input.', 'errors': {'finish': ['This field is required.']}}
        )

    def test_unknown_api_path_is_a_json_404(self):
        """A mistyped API URL gets a JSON 404 like every other API error."""
        response = self.client.get('/api/nope/')
        self.assertEqual((response.status_code, response.json()), (404, {'detail': 'Not found.'}))

    def test_post_is_not_allowed(self):
        """Planning is a GET; POST gets 405."""
        response = self.client.post('/api/trips/', {'start': 'Topeka, KS', 'finish': 'Kansas City, MO'})
        self.assertEqual(response.status_code, 405)

    def test_planned_trips_are_saved_once_and_counted(self):
        """Each successful trip is saved; planning it again bumps its count instead of duplicating it."""
        self.plan('Topeka, KS', 'Kansas City,  MO')
        self.plan('Topeka, KS', 'Kansas City, MO')
        self.plan(start='51.5,-0.12')  # fails, so not saved

        route = SavedTrip.objects.get()
        self.assertEqual((route.start, route.finish, route.times_planned), ('Topeka, KS', 'Kansas City, MO', 2))

    def test_saved_routes_list(self):
        """GET /api/trips/saved/ lists saved trips with a map link."""
        self.plan('Topeka, KS', 'Kansas City, MO')
        routes = self.client.get('/api/trips/saved/').json()
        self.assertEqual([(r['start'], r['finish']) for r in routes], [('Topeka, KS', 'Kansas City, MO')])
        self.assertIn('/map/?start=Topeka', routes[0]['map_url'])

    def test_swagger_suggests_major_cities_and_saved_locations(self):
        """Start and finish get examples (editable suggestions), not an enum that would forbid other places."""
        self.plan('Topeka, KS', 'Kansas City, MO')
        schema = self.client.get('/api/schema/?format=json').json()
        operations = schema['paths']['/api/trips/']
        self.assertEqual(list(operations), ['get'])
        parameters = {p['name']: p for p in operations['get']['parameters']}
        self.assertEqual(sorted(parameters['start']['examples']), sorted({'Topeka, KS', *MAJOR_CITIES}))
        self.assertEqual(sorted(parameters['finish']['examples']), sorted({'Kansas City, MO', *MAJOR_CITIES}))
        self.assertEqual(parameters['start']['examples']['Topeka, KS'], {'value': 'Topeka, KS'})
        self.assertNotIn('enum', parameters['start']['schema'])

    def test_swagger_suggestions_leave_out_saved_coordinates(self):
        """A trip planned from raw coordinates isn't offered as a suggestion."""
        self.plan('40.0,-100.0', 'Kansas City, MO')
        schema = self.client.get('/api/schema/?format=json').json()
        start = next(p for p in schema['paths']['/api/trips/']['get']['parameters'] if p['name'] == 'start')
        self.assertNotIn('40.0,-100.0', start['examples'])

    def test_rejects_coordinates_just_across_the_border(self):
        """Toronto is inside the rough US bounding box but outside the US outline."""
        response = self.plan(start='43.6532,-79.3832')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not within the USA', response.json()['detail'])

    def unroutable(self, point):
        """Build the error ORS returns when coordinate `point` isn't near a road."""
        return RoutingError('no road', status=400, code=UNROUTABLE_POINT, point=point)

    def test_city_away_from_roads_is_relocated_by_geocoding_and_remembered(self):
        """If a city's Census point isn't near a road, geocode it, retry, and reuse that point later."""
        self.directions.side_effect = [self.unroutable(1), self.route, self.route]
        downtown = {'label': 'Kansas City, MO', 'state': 'MO', 'latitude': 39.0997, 'longitude': -94.5786}
        with mock.patch(GEOCODE, return_value=downtown) as geocode:
            first = self.plan('Topeka, KS', 'Kansas City, MO')
            second = self.plan('Topeka, KS', 'Kansas City, MO')

        self.assertEqual((first.status_code, second.status_code), (200, 200))
        geocode.assert_called_once_with('Kansas City, MO')
        self.assertEqual(self.directions.call_args_list[1].args[1], (39.0997, -94.5786))
        self.assertEqual(self.directions.call_args_list[2].args[1], (39.0997, -94.5786))
        self.assertEqual(first.json()['finish']['latitude'], 39.0997)

    def test_coordinates_away_from_roads_are_reported(self):
        """User-given coordinates that aren't near a road get a clear 400, without geocoding."""
        self.directions.side_effect = self.unroutable(0)
        with mock.patch(GEOCODE) as geocode:
            response = self.plan(start='40.5,-100.5')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not near a drivable road', response.json()['detail'])
        geocode.assert_not_called()

    def test_broken_cache_does_not_break_planning(self):
        """If the cache table is missing, trips are still planned (without caching or rate limiting)."""
        with (
            mock.patch('fuel_route.services.trip_planner.cache.get', side_effect=DatabaseError('no table')),
            self.assertLogs('fuel_route.throttling', level='WARNING'),
        ):
            response = self.plan('Topeka, KS', 'Kansas City, MO')
        self.assertEqual(response.status_code, 200)

    def test_unexpected_error_returns_json_500(self):
        """An unexpected exception becomes a JSON 500 with no internal details."""
        self.directions.side_effect = RuntimeError('boom')
        with self.assertLogs('fuel_route.exceptions', level='ERROR'):
            response = self.plan()
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {'detail': 'Internal server error.'})

    def test_failing_to_save_the_route_still_returns_the_trip(self):
        """Saving only feeds the dropdowns, so a database error there doesn't fail the request."""
        with (
            mock.patch('fuel_route.views.save_trip', side_effect=DatabaseError('down')),
            self.assertLogs('fuel_route.views', level='ERROR'),
        ):
            response = self.plan()
        self.assertEqual(response.status_code, 200)

    def test_planning_is_rate_limited_per_client(self):
        """Past the limit, planning returns 429 with a Retry-After header; saved trips are unaffected."""
        with mock.patch.object(TripRateThrottle, 'THROTTLE_RATES', {'trip': '2/minute'}):
            statuses = [self.plan().status_code for _ in range(3)]
            throttled = self.plan()
            routes = self.client.get('/api/trips/saved/')
        self.assertEqual(statuses, [200, 200, 429])
        self.assertIn('Retry-After', throttled.headers)
        self.assertIn('throttled', throttled.json()['detail'])
        self.assertEqual(routes.status_code, 200)


class HealthViewTests(TestCase):
    """GET /health/."""

    def test_ok_when_database_answers(self):
        """With a working database, the health check returns 200."""
        response = self.client.get('/health/')
        self.assertEqual((response.status_code, response.json()), (200, {'status': 'ok', 'database': 'ok'}))

    def test_unavailable_when_database_fails(self):
        """If the database can't be reached, the health check returns 503."""
        with (
            mock.patch('fuel_route.views.connection.cursor', side_effect=DatabaseError('down')),
            self.assertLogs('fuel_route.views', level='ERROR'),
        ):
            response = self.client.get('/health/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['status'], 'unavailable')


class TripMapViewTests(TestCase):
    """The interactive map page."""

    def test_renders_map_page(self):
        """GET /map/ serves the map template."""
        response = self.client.get('/map/', {'start': 'Chicago, IL', 'finish': 'Denver, CO'})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'fuel_route/map.html')
