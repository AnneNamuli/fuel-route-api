"""API request and response shapes (also used for the OpenAPI docs)."""

from urllib.parse import urlencode

from django.http import HttpRequest
from django.urls import reverse
from rest_framework import serializers

from .models import QUERY_MAX_LENGTH, SavedTrip

LOCATION_HELP = 'US address/place, or "latitude,longitude".'


class TripRequestSerializer(serializers.Serializer):
    """Trip input: start and finish locations in the USA."""

    start = serializers.CharField(max_length=QUERY_MAX_LENGTH, help_text=LOCATION_HELP)
    finish = serializers.CharField(max_length=QUERY_MAX_LENGTH, help_text=LOCATION_HELP)


class LocationSerializer(serializers.Serializer):
    """A resolved location."""

    query = serializers.CharField()
    label = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()


class FuelStopSerializer(serializers.Serializer):
    """A fuel stop and what is bought there."""

    opis_id = serializers.IntegerField()
    name = serializers.CharField()
    address = serializers.CharField()
    city = serializers.CharField()
    state = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    miles_from_start = serializers.FloatField()
    detour_miles = serializers.FloatField(
        help_text='Extra miles to reach the station and return to the route (estimated).'
    )
    price_per_gallon = serializers.FloatField()
    gallons = serializers.FloatField()
    cost = serializers.FloatField()


class VehicleSerializer(serializers.Serializer):
    """Vehicle assumptions used for the plan."""

    range_miles = serializers.FloatField()
    mpg = serializers.FloatField()


class TripSerializer(serializers.Serializer):
    """A planned trip: route, fuel stops, total cost and a GeoJSON map."""

    start = LocationSerializer()
    finish = LocationSerializer()
    distance_miles = serializers.FloatField()
    duration_hours = serializers.FloatField()
    vehicle = VehicleSerializer()
    fuel_stops = FuelStopSerializer(many=True)
    notes = serializers.ListField(
        child=serializers.CharField(),
        help_text="Assumptions that affect this trip's cost, if any.",
    )
    total_gallons = serializers.FloatField(
        help_text='Fuel bought: the tank starts and ends empty, so this is (distance + detours) / mpg.'
    )
    total_fuel_cost = serializers.FloatField(help_text='USD spent on fuel for the whole trip.')
    map = serializers.JSONField(help_text='GeoJSON FeatureCollection: the route line and a point per stop.')
    map_url = serializers.URLField(
        help_text='Interactive map of this trip (served from the cache, no extra routing call).',
    )


class SavedTripSerializer(serializers.ModelSerializer):
    """A previously planned trip."""

    map_url = serializers.SerializerMethodField(help_text='Interactive map of this trip.')

    class Meta:
        model = SavedTrip
        fields = [
            'start',
            'finish',
            'start_label',
            'finish_label',
            'distance_miles',
            'total_fuel_cost',
            'times_planned',
            'last_planned_at',
            'map_url',
        ]

    def get_map_url(self, trip: SavedTrip) -> str:
        """Link to the map page with this trip's start and finish."""
        return map_url(self.context['request'], trip.start, trip.finish)


def map_url(request: HttpRequest, start: str, finish: str) -> str:
    """Absolute URL of the map page for a trip."""
    return request.build_absolute_uri(f'{reverse("trip-map")}?{urlencode({"start": start, "finish": finish})}')


class HealthSerializer(serializers.Serializer):
    """Health check result."""

    status = serializers.ChoiceField(choices=['ok', 'unavailable'])
    database = serializers.ChoiceField(choices=['ok', 'unreachable'])
