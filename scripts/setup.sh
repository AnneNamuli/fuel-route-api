#!/bin/sh
# One-off setup run by the `setup` service before the API starts. Safe to re-run:
# migrations are idempotent, the import replaces prices with the CSV's contents (stations and
# their coordinates are kept), and geocoding only fills in missing coordinates, offline.
set -e

CSV="${FUEL_PRICES_CSV:-data/fuel-prices-for-be-assessment.csv}"

python manage.py migrate --noinput
python manage.py createcachetable

if [ -f "$CSV" ]; then
    python manage.py load_fuel_stations "$CSV"
    python manage.py geocode_stations
else
    echo "WARNING: $CSV not found; skipping the fuel price import. Put the CSV in data/ and run" >&2
    echo "         'docker compose run --rm setup' to load it." >&2
fi
