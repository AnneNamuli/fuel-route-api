"""Gunicorn settings for the `route` container. Worker counts can be tuned with env vars."""

import os

bind = '0.0.0.0:8000'
worker_class = 'gthread'  # threads keep idle keep-alive connections from blocking workers
workers = int(os.environ.get('GUNICORN_WORKERS', 3))
threads = int(os.environ.get('GUNICORN_THREADS', 4))

# Load the app once in the main process, so workers start with it (and its data) already in memory.
preload_app = True


def when_ready(server: object) -> None:
    """Load the bundled Census data before workers are forked, so no request pays for it."""
    from fuel_route.services import geo, places

    places.preload()
    geo.us_polygons()
