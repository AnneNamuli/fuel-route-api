"""Tests for offline place lookup."""

from django.test import SimpleTestCase

from fuel_route.schema import MAJOR_CITIES
from fuel_route.services import places


class PlacesTests(SimpleTestCase):
    """Offline place lookup from the Census gazetteer."""

    def test_lookup_handles_census_name_variants(self):
        """Consolidated cities, multi-word names and spacing differences all match."""
        self.assertIsNotNone(places.lookup('Indianapolis', 'IN'))  # "Indianapolis city (balance)"
        self.assertIsNotNone(places.lookup('Nashville', 'TN'))  # "Nashville-Davidson metropolitan government"
        self.assertIsNotNone(places.lookup('Carson City', 'NV'))
        self.assertIsNotNone(places.lookup('Mc Calla', 'AL'))  # "McCalla"
        self.assertIsNone(places.lookup('Nowhereville', 'TX'))

    def test_lookup_falls_back_to_unique_county_subdivisions(self):
        """Townships fill gaps in the places file, but not when the name repeats within the state."""
        self.assertNotIn(('NJ', 'mahwah'), places.places())
        self.assertIsNotNone(places.lookup('Mahwah', 'NJ'))  # "Mahwah township"
        self.assertIsNone(places.subdivisions().get(('MO', 'taylor')))  # several Taylor townships in MO

    def test_city_centre_replaces_census_point_away_from_roads(self):
        """San Francisco's Census point is offshore, so its city hall is used instead."""
        self.assertEqual(places.lookup('San Francisco', 'CA'), (37.7793, -122.4193))

    def test_every_major_city_resolves_offline(self):
        """All dropdown cities resolve from the gazetteer, so picking one costs no geocoding call."""
        unresolved = [city for city in MAJOR_CITIES if places.lookup_query(city) is None]
        self.assertEqual(unresolved, [])
        self.assertEqual(len(MAJOR_CITIES), 87)

    def test_lookup_query_accepts_state_codes_and_names(self):
        """'City, ST' and 'City, State, USA' resolve; full street addresses don't."""
        self.assertEqual(places.lookup_query('Denver, CO')['label'], 'Denver, CO')
        self.assertEqual(places.lookup_query('Denver, Colorado, USA')['label'], 'Denver, CO')
        self.assertEqual(places.lookup_query('Washington, D.C.')['label'], 'Washington, DC')
        self.assertEqual(places.lookup_query('  denver ,  co ')['label'], 'Denver, CO')
        self.assertEqual(places.lookup_query('McAllen, TX')['label'], 'McAllen, TX')
        self.assertIsNone(places.lookup_query('1600 Pennsylvania Ave NW, Washington, DC'))
