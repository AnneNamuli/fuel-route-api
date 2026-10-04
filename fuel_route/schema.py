"""OpenAPI documentation: what each endpoint looks like in Swagger, and the location suggestions.

Views only apply the decorators defined here, so the API docs live in one place.
"""

from typing import Any

from django.db import DatabaseError
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view

from .models import SavedTrip
from .serializers import LOCATION_HELP, HealthSerializer, TripSerializer
from .services.trip_planner import COORDINATES

MAX_SAVED_TRIPS = 100  # keep the suggestion lists a usable size

# Suggested for start/finish in Swagger alongside saved locations: the 50 largest US cities and
# the state capitals reachable by road from the lower 48 (so not Juneau or Honolulu). All of them
# resolve offline (see services/places.py), so picking one never costs a geocoding call.
MAJOR_CITIES = sorted(
    {
        'New York, NY',
        'Los Angeles, CA',
        'Chicago, IL',
        'Houston, TX',
        'Phoenix, AZ',
        'Philadelphia, PA',
        'San Antonio, TX',
        'San Diego, CA',
        'Dallas, TX',
        'Jacksonville, FL',
        'Austin, TX',
        'Fort Worth, TX',
        'San Jose, CA',
        'Columbus, OH',
        'Charlotte, NC',
        'Indianapolis, IN',
        'San Francisco, CA',
        'Seattle, WA',
        'Denver, CO',
        'Oklahoma City, OK',
        'Nashville, TN',
        'Washington, DC',
        'El Paso, TX',
        'Las Vegas, NV',
        'Boston, MA',
        'Detroit, MI',
        'Portland, OR',
        'Louisville, KY',
        'Memphis, TN',
        'Baltimore, MD',
        'Milwaukee, WI',
        'Albuquerque, NM',
        'Tucson, AZ',
        'Fresno, CA',
        'Sacramento, CA',
        'Mesa, AZ',
        'Atlanta, GA',
        'Kansas City, MO',
        'Colorado Springs, CO',
        'Omaha, NE',
        'Raleigh, NC',
        'Miami, FL',
        'Virginia Beach, VA',
        'Long Beach, CA',
        'Oakland, CA',
        'Minneapolis, MN',
        'Bakersfield, CA',
        'Tulsa, OK',
        'Tampa, FL',
        'Arlington, TX',
        'Montgomery, AL',
        'Little Rock, AR',
        'Hartford, CT',
        'Dover, DE',
        'Tallahassee, FL',
        'Boise, ID',
        'Springfield, IL',
        'Des Moines, IA',
        'Topeka, KS',
        'Frankfort, KY',
        'Baton Rouge, LA',
        'Augusta, ME',
        'Annapolis, MD',
        'Lansing, MI',
        'Saint Paul, MN',
        'Jackson, MS',
        'Jefferson City, MO',
        'Helena, MT',
        'Lincoln, NE',
        'Carson City, NV',
        'Concord, NH',
        'Trenton, NJ',
        'Santa Fe, NM',
        'Albany, NY',
        'Bismarck, ND',
        'Salem, OR',
        'Harrisburg, PA',
        'Providence, RI',
        'Columbia, SC',
        'Pierre, SD',
        'Salt Lake City, UT',
        'Montpelier, VT',
        'Richmond, VA',
        'Olympia, WA',
        'Charleston, WV',
        'Madison, WI',
        'Cheyenne, WY',
    }
)

# Endpoint documentation, applied as class decorators in views.py.

trip_docs = extend_schema_view(
    get=extend_schema(
        summary='Plan a trip with the cheapest fuel stops',
        description=(
            'In these docs, start and finish offer major US cities and saved locations as suggestions; '
            'you can also type any US place, address or "latitude,longitude".'
        ),
        parameters=[
            OpenApiParameter('start', str, required=True, description=LOCATION_HELP),
            OpenApiParameter('finish', str, required=True, description=LOCATION_HELP),
        ],
        responses={200: TripSerializer},
    ),
)

saved_trips_docs = extend_schema_view(
    get=extend_schema(summary='List saved trips (most recently planned first)'),
)

health_docs = extend_schema_view(
    get=extend_schema(summary='Health check', responses={200: HealthSerializer, 503: HealthSerializer}),
)


def trip_location_suggestions(result: dict[str, Any], generator: Any, request: Any, public: bool) -> dict[str, Any]:
    """drf-spectacular postprocessing hook: suggest locations for start and finish in Swagger UI.

    The suggestions are the major US cities plus the starts/finishes of recent saved trips. They're
    OpenAPI `examples`, which Swagger shows as a dropdown that fills in the text box; unlike an enum,
    any other place can still be typed. The schema is generated per request, so newly saved
    locations appear on reload.
    """
    recent = SavedTrip.objects.order_by('-last_planned_at')[:MAX_SAVED_TRIPS]
    try:
        saved = {
            'start': {trip.start for trip in recent},
            'finish': {trip.finish for trip in recent},
        }
    except DatabaseError:  # e.g. generating the schema without a database
        saved = {'start': set(), 'finish': set()}

    operation = result.get('paths', {}).get('/api/trips/', {}).get('get')
    for parameter in (operation or {}).get('parameters', []):
        if parameter['name'] in saved:
            # Saved raw coordinates make poor suggestions (and would sort first), so only names are offered.
            names = {place for place in saved[parameter['name']] if not COORDINATES.match(place)}
            suggestions = sorted(names | set(MAJOR_CITIES))
            parameter['examples'] = {place: {'value': place} for place in suggestions}
    return result
