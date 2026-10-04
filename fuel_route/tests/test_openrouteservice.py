"""Tests for the OpenRouteService client."""

from unittest import mock

import requests
from django.core.cache import cache
from django.db import DatabaseError
from django.test import TestCase, override_settings

from fuel_route.services import openrouteservice

DIRECTIONS_RESPONSE = {
    'features': [
        {
            'geometry': {'coordinates': [[-100.0, 40.0], [-99.0, 40.0]]},
            'properties': {'summary': {'distance': 85_000, 'duration': 3600}},
        }
    ]
}


class DirectionsTests(TestCase):
    """The OpenRouteService directions client."""

    def setUp(self):
        """Start each test with an empty cache."""
        cache.clear()

    def test_converts_distance_to_miles(self):
        """Distance comes back in miles, coordinates unchanged."""
        with mock.patch.object(openrouteservice, '_request', return_value=DIRECTIONS_RESPONSE):
            route = openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
        self.assertAlmostEqual(route['distance_miles'], 52.82, places=2)
        self.assertEqual(route['coordinates'], [[-100.0, 40.0], [-99.0, 40.0]])

    def test_repeat_request_is_served_from_cache(self):
        """The same route twice makes one API call."""
        with mock.patch.object(openrouteservice, '_request', return_value=DIRECTIONS_RESPONSE) as request:
            openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
            openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
        request.assert_called_once()

    def test_broken_cache_falls_back_to_the_api(self):
        """A cache error (e.g. missing cache table) still returns the route."""
        with (
            mock.patch.object(openrouteservice, '_request', return_value=DIRECTIONS_RESPONSE),
            mock.patch.object(openrouteservice.cache, 'get', side_effect=DatabaseError('no table')),
        ):
            route = openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
        self.assertAlmostEqual(route['distance_miles'], 52.82, places=2)


@override_settings(ORS_API_KEY='test-key')
class RequestErrorTests(TestCase):
    """How the client turns API failures into RoutingError."""

    def setUp(self):
        """Start each test with an empty cache."""
        cache.clear()

    def respond(self, status=200, body=None, text=''):
        """Mock `requests.request` to return a response with this status and JSON body (or text)."""
        response = mock.Mock(status_code=status, ok=status < 400, text=text or str(body))
        if body is None:
            response.json.side_effect = ValueError('not JSON')
        else:
            response.json.return_value = body
        return mock.patch.object(openrouteservice.requests, 'request', return_value=response)

    def directions(self, logged=True):
        """Request a route, returning the RoutingError raised (and checking a warning was logged)."""
        if not logged:
            with self.assertRaises(openrouteservice.RoutingError) as raised:
                openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
            return raised.exception
        with (
            self.assertLogs('fuel_route.services.openrouteservice', level='WARNING'),
            self.assertRaises(openrouteservice.RoutingError) as raised,
        ):
            openrouteservice.directions((40.0, -100.0), (40.0, -99.0))
        return raised.exception

    def test_network_failure(self):
        """A connection error becomes a 502 'unreachable' error."""
        with mock.patch.object(openrouteservice.requests, 'request', side_effect=requests.ConnectionError()):
            error = self.directions()
        self.assertEqual((error.status, str(error)), (502, 'OpenRouteService is unreachable (ConnectionError).'))

    def test_quota_exceeded(self):
        """A 403 quota response is reported as quota exceeded (503), not as a bad key."""
        with self.respond(403, {'error': 'Quota exceeded'}, text='{"error": "Quota exceeded"}'):
            error = self.directions()
        self.assertEqual(error.status, 503)
        self.assertIn('quota exceeded', str(error))

    def test_unroutable_point_reports_which_coordinate(self):
        """Error 2010 carries the index of the coordinate that isn't near a road."""
        body = {'error': {'code': 2010, 'message': 'Could not find routable point ... coordinate 1: -99 40.'}}
        with self.respond(404, body):
            error = self.directions()
        self.assertEqual((error.status, error.code, error.point), (400, openrouteservice.UNROUTABLE_POINT, 1))

    def test_non_json_success_response(self):
        """A 200 that isn't JSON (e.g. a proxy page) becomes a RoutingError, not a crash."""
        with self.respond(200, None, text='<html>proxy</html>'):
            error = self.directions()
        self.assertIn('unreadable response', str(error))

    def test_unexpected_response_shape(self):
        """A JSON response without the expected route becomes a RoutingError, not a crash."""
        with self.respond(200, {'features': []}):
            error = self.directions()
        self.assertIn('unexpected response', str(error))

    def test_missing_api_key(self):
        """Without a key, the client fails fast with a 503 and makes no request."""
        with override_settings(ORS_API_KEY=''), mock.patch.object(openrouteservice.requests, 'request') as request:
            error = self.directions(logged=False)
        self.assertEqual(error.status, 503)
        request.assert_not_called()
