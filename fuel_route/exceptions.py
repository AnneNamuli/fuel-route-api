"""API-wide error handling."""

import logging
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.defaults import page_not_found
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

from .services.trip_planner import TripError

logger = logging.getLogger(__name__)


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    """Answer every API error with JSON, including unexpected ones (logged, details hidden).

    Every error has a `detail` message; validation errors also list the problems per field under
    `errors`. Trip planning errors keep their own status (400, 422, 502, 503). DRF handles its own
    exceptions (validation, throttling, 405, ...); anything else would otherwise become Django's
    HTML 500 page.
    """
    if isinstance(exc, TripError):  # a trip that can't be planned, with the status that says why
        return Response({'detail': str(exc)}, status=exc.status)
    response = exception_handler(exc, context)
    if response is not None:
        if isinstance(exc, ValidationError):  # keep the per-field messages, under the usual `detail`
            response.data = {'detail': 'Invalid input.', 'errors': response.data}
        return response
    logger.exception('Unhandled error in %s', context['view'].__class__.__name__)
    return Response({'detail': 'Internal server error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def not_found(request: HttpRequest, exception: Exception | None = None) -> HttpResponse:
    """Return 404s for API paths as JSON, like every other API error; other paths keep Django's page.

    Django's 404 handler must be a plain function taking (request, exception), so this isn't a view class.
    """
    if request.path.startswith(('/api/', '/health/')):
        return JsonResponse({'detail': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
    return page_not_found(request, exception)
