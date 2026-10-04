"""Management command: load the OPIS fuel price CSV (see services/price_import.py)."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from fuel_route.services.price_import import PriceImportError, import_prices


class Command(BaseCommand):
    """Load the OPIS fuel price CSV."""

    help = (
        'Load the OPIS fuel price CSV. Prices are replaced with the file contents (every row, '
        'duplicates included); stations are updated in place so their coordinates are kept.'
    )

    def add_arguments(self, parser: CommandParser) -> None:
        """Register the CSV path argument."""
        parser.add_argument('csv_path')

    def handle(self, csv_path: str, **options: Any) -> None:
        """Import the CSV and report what changed."""
        try:
            result = import_prices(csv_path)
        except PriceImportError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                f'Loaded {result.prices} prices for {result.stations} stations from {csv_path} '
                f'({result.removed_stations} stations no longer in the file were removed).'
            )
        )
