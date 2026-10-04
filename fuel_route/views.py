"""HTTP layer: the trip API, saved trips list and the map page."""

import logging

from django.db import DatabaseError, connection
from django.views.generic import TemplateView
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SavedTrip
from .schema import health_docs, saved_trips_docs, trip_docs
from .serializers import (
    HealthSerializer,
    SavedTripSerializer,
    TripRequestSerializer,
    TripSerializer,
    map_url,
)
from .services.trip_planner import plan_trip, save_trip
from .throttling import TripRateThrottle

logger = logging.getLogger(__name__)


@trip_docs
class TripView(APIView):
    """Plan a trip between two US locations with the cheapest fuel stops."""

    throttle_classes = [TripRateThrottle]
    throttle_scope = 'trip'

    def get(self, request: Request) -> Response:
        """Plan and save the trip given by the ?start=&finish= query parameters, and return it."""
        params = TripRequestSerializer(data=request.query_params)
        params.is_valid(raise_exception=True)
        start, finish = params.validated_data['start'], params.validated_data['finish']
        trip = plan_trip(start, finish)  # TripError becomes a JSON error in exceptions.py
        try:
            save_trip(trip)
        except DatabaseError:
            # Saving only feeds the dropdowns; the trip itself is still a valid answer.
            logger.exception('Could not save trip %r -> %r', start, finish)
        return Response(TripSerializer({**trip, 'map_url': map_url(request, start, finish)}).data)


@saved_trips_docs
class SavedTripListView(ListAPIView):
    """Trips planned before, for picking one again."""

    serializer_class = SavedTripSerializer
    queryset = SavedTrip.objects.order_by('-last_planned_at')
    pagination_class = None


@health_docs
class HealthView(APIView):
    """Report whether the API can serve requests (used by the Docker healthcheck)."""

    def get(self, request: Request) -> Response:
        """Return 200 if the database answers, 503 otherwise."""
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
        except DatabaseError:
            logger.exception('Health check: database unavailable')
            return Response(HealthSerializer({'status': 'unavailable', 'database': 'unreachable'}).data, status=503)
        return Response(HealthSerializer({'status': 'ok', 'database': 'ok'}).data)


class TripMapView(TemplateView):
    """Interactive map page that calls the trip API and draws the result (prefilled from ?start=&finish=)."""

    template_name = 'fuel_route/map.html'
