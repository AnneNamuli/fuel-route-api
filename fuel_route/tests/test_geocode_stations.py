"""Tests for the geocode_stations command."""

import io

from django.core.management import call_command
from django.test import TestCase

from fuel_route.models import FuelStation


class GeocodeStationsTests(TestCase):
    """The geocode_stations management command."""

    def test_sets_coordinates_from_gazetteer_and_skips_non_us(self):
        """US stations get city coordinates; Canadian ones are skipped; unknown cities are reported."""
        for opis_id, city, state in [(1, 'Tomah', 'WI'), (2, 'Calgary', 'AB'), (3, 'Nowhereville', 'TX')]:
            FuelStation.objects.create(opis_id=opis_id, name='S', address='A', city=city, state=state, rack_id=1)

        out = io.StringIO()
        call_command('geocode_stations', stdout=out)

        tomah = FuelStation.objects.get(opis_id=1)
        self.assertAlmostEqual(tomah.latitude, 43.98, delta=0.1)
        self.assertIsNone(FuelStation.objects.get(opis_id=2).latitude)
        self.assertIsNone(FuelStation.objects.get(opis_id=3).latitude)
        self.assertIn('1 stations in 1 cities have no coordinates', out.getvalue())
