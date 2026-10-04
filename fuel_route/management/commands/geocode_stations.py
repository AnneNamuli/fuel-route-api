"""Management command: set station coordinates from their city and state."""

import time
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from fuel_route.models import FuelStation
from fuel_route.services import openrouteservice, places

ORS_DELAY_SECONDS = 0.7  # stay under OpenRouteService's 100 requests/minute

CityKey = tuple[str, str]  # (state, city)
Cities = dict[CityKey, list[FuelStation]]


class Command(BaseCommand):
    """Set station coordinates from their city and state."""

    help = (
        'Set station coordinates from their city/state using the US Census place gazetteer. '
        'Stations outside the US are skipped. With --ors, cities not in the gazetteer are '
        'looked up with OpenRouteService (progress is saved, so it can be re-run).'
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the --ors and --all options."""
        parser.add_argument('--ors', action='store_true', help='Geocode unmatched cities with OpenRouteService.')
        parser.add_argument(
            '--all', action='store_true', dest='redo_all', help='Redo stations that already have coordinates.'
        )

    def handle(self, ors: bool, redo_all: bool, **options: Any) -> None:
        """Geocode stations by city, optionally falling back to OpenRouteService, and report what's left."""
        stations = FuelStation.objects.filter(state__in=places.us_states())
        if not redo_all:
            stations = stations.filter(latitude__isnull=True)

        cities: Cities = {}
        for station in stations:
            cities.setdefault((station.state, station.city), []).append(station)

        geocoded, unmatched = self.geocode_offline(cities)
        if ors and unmatched:
            found, unmatched = self.geocode_with_ors(cities, unmatched)
            geocoded += found
        self.report(geocoded, unmatched, cities, ors)

    def geocode_offline(self, cities: Cities) -> tuple[int, list[CityKey]]:
        """Place cities found in the Census files. Returns (stations geocoded, cities not found)."""
        geocoded = 0
        unmatched: list[CityKey] = []
        for (state, city), city_stations in cities.items():
            coordinates = places.lookup(city, state)
            if coordinates is None:
                unmatched.append((state, city))
            else:
                geocoded += self.save_coordinates(city_stations, coordinates)
        return geocoded, unmatched

    def geocode_with_ors(self, cities: Cities, unmatched: list[CityKey]) -> tuple[int, list[CityKey]]:
        """Look up the remaining cities with OpenRouteService, saving each as it's found.

        Returns (stations geocoded, cities still not found). Stops at the first API error (e.g. the
        daily quota), leaving the rest for a later run.
        """
        self.stdout.write(f'Looking up {len(unmatched)} cities with OpenRouteService...')
        geocoded = 0
        remaining: list[CityKey] = []
        for index, (state, city) in enumerate(unmatched):
            try:
                location = openrouteservice.geocode(f'{city}, {state}')
            except openrouteservice.RoutingError as exc:
                remaining.extend(unmatched[index:])
                self.stderr.write(self.style.ERROR(f'Stopped early: {exc}'))
                break
            # A miss can fall back to some other place; only trust results in the right state.
            if location is None or location['state'] != state:
                remaining.append((state, city))
            else:
                geocoded += self.save_coordinates(cities[(state, city)], (location['latitude'], location['longitude']))
            time.sleep(ORS_DELAY_SECONDS)
        return geocoded, remaining

    def report(self, geocoded: int, unmatched: list[CityKey], cities: Cities, ors: bool) -> None:
        """Print how many stations were geocoded and how many still lack coordinates."""
        self.stdout.write(self.style.SUCCESS(f'Geocoded {geocoded} stations.'))
        if unmatched:
            missing = sum(len(cities[key]) for key in unmatched)
            hint = '' if ors else ' Re-run with --ors to look them up with OpenRouteService.'
            self.stdout.write(
                self.style.WARNING(f'{missing} stations in {len(unmatched)} cities have no coordinates.{hint}')
            )

    @staticmethod
    def save_coordinates(stations: list[FuelStation], coordinates: tuple[float, float]) -> int:
        """Set and save the same coordinates on all `stations`; return how many were updated."""
        for station in stations:
            station.latitude, station.longitude = coordinates
        FuelStation.objects.bulk_update(stations, ['latitude', 'longitude'])
        return len(stations)
