"""Request rate limiting."""

import logging

from django.db import DatabaseError
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class TripRateThrottle(ScopedRateThrottle):
    """Limit trip planning per client IP (rate from the `trip` scope).

    Counters live in the database cache. If the cache fails, requests are let through rather
    than failing: rate limiting protects the routing quota but isn't worth an outage.
    """

    def allow_request(self, request: Request, view: APIView) -> bool:
        """Apply the rate limit, allowing the request if the cache can't be used."""
        try:
            return super().allow_request(request, view)
        except DatabaseError:
            logger.warning('Rate limiting skipped: cache unavailable', exc_info=True)
            return True
