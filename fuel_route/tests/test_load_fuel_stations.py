"""Tests for the load_fuel_stations command."""

import io
import tempfile
from decimal import Decimal
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from fuel_route.models import FuelPrice, FuelStation

HEADER = 'OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n'


class LoadFuelStationsTests(TestCase):
    """The load_fuel_stations management command."""

    def setUp(self):
        """Create a temporary directory for CSV files."""
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.temp_dir = Path(temp_dir.name)

    def write_csv(self, body, header=HEADER):
        """Write a CSV file and return its path."""
        path = self.temp_dir / 'prices.csv'
        path.write_text(header + body, encoding='utf-8')
        return str(path)

    def load(self, body, header=HEADER):
        """Run the command on a CSV with the given body."""
        call_command('load_fuel_stations', self.write_csv(body, header), stdout=io.StringIO())

    def test_cleans_whitespace_and_state_case(self):
        """Text values are trimmed, inner whitespace collapsed and states uppercased."""
        self.load('7,JIMS  BEST STOP ,"I-44,  EXIT 283",Big Cabin      ,ok,307,3.00733333\n')

        station = FuelStation.objects.get()
        self.assertEqual(station.name, 'JIMS BEST STOP')
        self.assertEqual(station.address, 'I-44, EXIT 283')
        self.assertEqual(station.city, 'Big Cabin')
        self.assertEqual(station.state, 'OK')
        self.assertEqual(station.prices.get().retail_price, Decimal('3.00733333'))

    def test_keeps_every_row_including_duplicates(self):
        """Repeated OPIS IDs become one station with one price row per CSV line."""
        self.load(
            '20,PILOT TRAVEL CENTER #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.899\n'
            '20,PILOT #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.899\n'
            '20,PILOT #1243,"I-8, EXIT 119",Gila Bend,AZ,930,3.899\n'
        )

        station = FuelStation.objects.get()
        self.assertEqual(station.name, 'PILOT TRAVEL CENTER #1243')
        self.assertEqual(
            sorted(station.prices.values_list('name', flat=True)),
            ['PILOT #1243', 'PILOT #1243', 'PILOT TRAVEL CENTER #1243'],
        )

    def test_reimport_replaces_prices_and_keeps_station_coordinates(self):
        """Re-importing replaces prices, updates stations in place and removes stations not in the file."""
        self.load('7,OLD NAME,Addr,City,OK,1,3.00\n9,GONE,Addr,City,WI,2,3.10\n')
        station = FuelStation.objects.get(opis_id=7)
        station.latitude, station.longitude = 36.45, -95.22
        station.save()

        self.load('7,NEW NAME,Addr,City,OK,1,3.50\n')

        station = FuelStation.objects.get()
        self.assertEqual((station.opis_id, station.name), (7, 'NEW NAME'))
        self.assertEqual((station.latitude, station.longitude), (36.45, -95.22))
        self.assertEqual(list(FuelPrice.objects.values_list('retail_price', flat=True)), [Decimal('3.50')])

    def assert_load_fails(self, message, body='', header=HEADER, path=None):
        """Assert a load fails with `message` and leaves previously loaded data untouched."""
        self.load('7,KEEP,Addr,City,OK,1,3.00\n')
        with self.assertRaisesMessage(CommandError, message):
            call_command('load_fuel_stations', path or self.write_csv(body, header))
        self.assertEqual(FuelPrice.objects.get().name, 'KEEP')

    def test_missing_file(self):
        """A missing file is reported."""
        self.assert_load_fails('Cannot read', path=str(self.temp_dir / 'nope.csv'))

    def test_missing_columns(self):
        """Missing columns are listed."""
        self.assert_load_fails('Missing columns: Address', header='OPIS Truckstop ID\n')

    def test_empty_file_leaves_data_unchanged(self):
        """A header-only file doesn't wipe existing data."""
        self.assert_load_fails('has no rows')

    def test_reads_file_with_byte_order_mark(self):
        """A CSV saved with a UTF-8 byte-order mark (e.g. from Excel) loads normally."""
        path = self.temp_dir / 'bom.csv'
        path.write_text(HEADER + '7,STOP,Addr,City,OK,1,3.00\n', encoding='utf-8-sig')
        call_command('load_fuel_stations', str(path), stdout=io.StringIO())
        self.assertEqual(FuelStation.objects.get().opis_id, 7)

    def test_out_of_range_values_report_line(self):
        """Values the database can't hold are reported with their line, not as database errors."""
        for body, message in [
            ('-7,STOP,Addr,City,OK,1,3.00\n', 'must not be negative'),
            ('7,STOP,Addr,City,OK,1,0\n', 'must be between 0 and 100'),
            ('7,STOP,Addr,City,OK,1,123.4\n', 'must be between 0 and 100'),
            ('7,STOP,Addr,City,Oklahoma,1,3.00\n', 'not a two-letter code'),
            (f'7,{"X" * 101},Addr,City,OK,1,3.00\n', 'longer than 100 characters'),
            ('7,STOP,Addr,City,OK,one,3.00\n', 'Rack ID'),
        ]:
            with self.subTest(message):
                self.assert_load_fails(message, body=body)

    def test_invalid_price_reports_line(self):
        """A bad price is reported with its line number."""
        self.assert_load_fails(
            "Line 3: Retail Price 'abc' is not a number",
            body=('7,OK ROW,Addr,City,OK,1,3.00\n8,BAD ROW,Addr,City,OK,1,abc\n'),
        )

    def test_malformed_files_are_reported_clearly(self):
        """Non-UTF-8 text, an unclosed quote and NUL characters fail with a message, not a crash."""
        cases = [
            (HEADER.encode() + b'7,CAF\xe9 STOP,Addr,City,OK,1,3.00\n', 'is not UTF-8 text'),
            ((HEADER + '7,"UNCLOSED,Addr,City,OK,1,3.00\n8,B,Addr,City,OK,1,3.00\n').encode(), 'quote left open'),
            ((HEADER + '7,A\x00B,Addr,City,OK,1,3.00\n').encode(), 'NUL character'),
        ]
        self.load('7,KEEP,Addr,City,OK,1,3.00\n')
        for content, message in cases:
            with self.subTest(message):
                path = self.temp_dir / 'bad.csv'
                path.write_bytes(content)
                with self.assertRaisesMessage(CommandError, message):
                    call_command('load_fuel_stations', str(path))
                self.assertEqual(FuelPrice.objects.get().name, 'KEEP')

    def test_database_failure_is_reported_and_changes_nothing(self):
        """If the database fails mid-import, the command says so and the old data stays."""
        from unittest import mock

        from django.db import DatabaseError

        self.load('7,KEEP,Addr,City,OK,1,3.00\n')
        with (
            mock.patch(
                'fuel_route.services.price_import.FuelPrice.objects.bulk_create', side_effect=DatabaseError('lost')
            ),
            self.assertRaisesMessage(CommandError, 'Database error; nothing was changed'),
        ):
            call_command('load_fuel_stations', self.write_csv('8,NEW,Addr,City,OK,1,3.10\n'))
        self.assertEqual(FuelPrice.objects.get().name, 'KEEP')
