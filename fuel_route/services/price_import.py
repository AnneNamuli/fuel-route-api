"""Import the OPIS fuel price CSV: validate every row, then replace prices and update stations.

Assumptions about the file:
- It has the columns in REQUIRED_COLUMNS (other columns are ignored), UTF-8, optionally with a BOM.
- Prices are US dollars per gallon. Rows with the same OPIS ID are the same station: their
  address, city, state and rack ID match, and the first row's details are kept.
- The file is the complete, current list: stations not in it are removed on import.
"""

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from django.db import DatabaseError, models, transaction

from fuel_route.models import FuelPrice, FuelStation

from .text import collapse_whitespace

REQUIRED_COLUMNS = {
    'OPIS Truckstop ID',
    'Truckstop Name',
    'Address',
    'City',
    'State',
    'Rack ID',
    'Retail Price',
}
STATION_UPDATE_FIELDS = ['name', 'address', 'city', 'state', 'rack_id']
BULK_BATCH_SIZE = 1000  # rows per INSERT, to keep statements a reasonable size

# The largest price the retail_price column can store (100 for max_digits=10, decimal_places=8),
# read from the model so the check can't drift from the schema.
_PRICE_FIELD = FuelPrice._meta.get_field('retail_price')
MAX_PRICE = Decimal(10) ** (_PRICE_FIELD.max_digits - _PRICE_FIELD.decimal_places)

Row = tuple[dict[str, Any], dict[str, Any]]  # (station fields, price fields)


class PriceImportError(Exception):
    """The CSV can't be imported; the message says why (and on which line, for bad values)."""


@dataclass(frozen=True)
class ImportResult:
    """What an import changed."""

    prices: int
    stations: int
    removed_stations: int


def import_prices(csv_path: str) -> ImportResult:
    """Validate the whole CSV, then replace all prices and update stations in one transaction."""
    rows = read_rows(csv_path)
    try:
        return save_rows(rows)
    except DatabaseError as exc:
        raise PriceImportError(f'Database error; nothing was changed: {exc}') from exc


def read_rows(csv_path: str) -> list[Row]:
    """Parse and validate every row, failing on the first bad one without touching the database."""
    try:
        # utf-8-sig also reads files saved with a byte-order mark (e.g. from Excel).
        with open(csv_path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise PriceImportError(f'Missing columns: {", ".join(sorted(missing))}.')
            rows = []
            for row in reader:
                try:
                    rows.append(parse_row(row))
                except ValueError as exc:
                    raise PriceImportError(f'Line {reader.line_num}: {exc}.') from exc
    except OSError as exc:
        raise PriceImportError(f'Cannot read {csv_path}: {exc.strerror}.') from exc
    except UnicodeDecodeError as exc:
        raise PriceImportError(f'{csv_path} is not UTF-8 text; save it as UTF-8 (CSV UTF-8 in Excel).') from exc
    except csv.Error as exc:
        raise PriceImportError(f'{csv_path} is not a valid CSV file: {exc}.') from exc

    if not rows:
        raise PriceImportError(f'{csv_path} has no rows; existing data was left unchanged.')
    return rows


def save_rows(rows: list[Row]) -> ImportResult:
    """Replace all prices with `rows` and update stations in place (keeping their coordinates)."""
    stations: dict[int, dict[str, Any]] = {}
    for station, _ in rows:
        stations.setdefault(station['opis_id'], station)  # a repeated OPIS ID keeps its first row's details

    # One transaction, so a failed import leaves the existing data untouched.
    with transaction.atomic():
        FuelPrice.objects.all().delete()
        removed, _ = FuelStation.objects.exclude(opis_id__in=stations).delete()
        FuelStation.objects.bulk_create(
            [FuelStation(**fields) for fields in stations.values()],
            batch_size=BULK_BATCH_SIZE,
            update_conflicts=True,
            unique_fields=['opis_id'],
            update_fields=STATION_UPDATE_FIELDS,
        )
        station_ids = dict(FuelStation.objects.values_list('opis_id', 'id'))
        FuelPrice.objects.bulk_create(
            [FuelPrice(station_id=station_ids[s['opis_id']], **price) for s, price in rows],
            batch_size=BULK_BATCH_SIZE,
        )
    return ImportResult(prices=len(rows), stations=len(stations), removed_stations=removed)


def parse_row(row: dict[str, str]) -> Row:
    """Return (station fields, price fields) for one CSV row, raising ValueError for bad values."""
    for column in REQUIRED_COLUMNS:
        value = row.get(column)
        if value is None:  # a short row, or an unclosed quote that swallowed the rest of the file
            raise ValueError(f'no value for {column} (is a row short, or a quote left open?)')
        if '\x00' in value:  # Postgres can't store NUL characters
            raise ValueError(f'{column} contains a NUL character')
    station: dict[str, Any] = {
        'opis_id': parse_id(row, 'OPIS Truckstop ID'),
        'name': collapse_whitespace(row['Truckstop Name']),
        'address': collapse_whitespace(row['Address']),
        'city': collapse_whitespace(row['City']),
        'state': collapse_whitespace(row['State']).upper(),
        'rack_id': parse_id(row, 'Rack ID'),
    }
    if len(station['state']) != 2:
        raise ValueError(f'State {row["State"]!r} is not a two-letter code')
    price = {'name': station['name'], 'retail_price': parse_price(row['Retail Price'])}
    check_lengths(station, FuelStation)
    check_lengths(price, FuelPrice)
    return station, price


def parse_price(value: str) -> Decimal:
    """Parse a retail price as Decimal, raising ValueError with a readable message."""
    try:
        price = Decimal(value.strip())
    except InvalidOperation:
        raise ValueError(f'Retail Price {value!r} is not a number') from None
    if not 0 < price < MAX_PRICE:
        raise ValueError(f'Retail Price {value!r} must be between 0 and {MAX_PRICE}')
    return price


def parse_id(row: dict[str, str], column: str) -> int:
    """Parse a non-negative integer ID column, raising ValueError with a readable message."""
    try:
        number = int(row[column])
    except ValueError:
        raise ValueError(f'{column} {row[column]!r} is not a whole number') from None
    if number < 0:
        raise ValueError(f'{column} {row[column]!r} must not be negative')
    return number


def check_lengths(fields: dict[str, Any], model: type[models.Model]) -> None:
    """Raise ValueError if any text field is longer than the model allows."""
    for name, value in fields.items():
        max_length = model._meta.get_field(name).max_length
        if isinstance(value, str) and max_length and len(value) > max_length:
            raise ValueError(f'{name} is longer than {max_length} characters: {value[:40]!r}...')
