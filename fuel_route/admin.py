"""Django admin for stations, prices and saved trips."""

from decimal import Decimal

from django.contrib import admin
from django.db.models import Count, QuerySet
from django.http import HttpRequest

from .models import FuelPrice, FuelStation, SavedTrip


class FuelPriceInline(admin.TabularInline):
    """A station's prices, listed on its admin page."""

    model = FuelPrice
    extra = 0
    ordering = ['retail_price']


@admin.register(FuelStation)
class FuelStationAdmin(admin.ModelAdmin):
    """Stations with their lowest price and number of prices."""

    list_display = [
        'opis_id',
        'name',
        'address',
        'city',
        'state',
        'rack_id',
        'min_price',
        'price_count',
        'latitude',
        'longitude',
    ]
    list_filter = ['state']
    search_fields = ['opis_id', 'name', 'city', 'address']
    ordering = ['opis_id']
    inlines = [FuelPriceInline]

    def get_queryset(self, request: HttpRequest) -> QuerySet[FuelStation]:
        """Annotate stations with their lowest price and price count."""
        return super().get_queryset(request).with_lowest_price().annotate(price_count=Count('prices'))

    @admin.display(description='lowest price', ordering='min_price')
    def min_price(self, obj: FuelStation) -> Decimal | None:
        """Return the lowest price listed for the station."""
        return obj.min_price

    @admin.display(description='prices', ordering='price_count')
    def price_count(self, obj: FuelStation) -> int:
        """Return the number of price rows for the station."""
        return obj.price_count


@admin.register(FuelPrice)
class FuelPriceAdmin(admin.ModelAdmin):
    """One row per CSV line, with its station's location."""

    list_display = ['opis_id', 'name', 'city', 'state', 'retail_price', 'station']
    list_filter = ['station__state']
    list_select_related = ['station']
    search_fields = ['station__opis_id', 'name', 'station__city']
    ordering = ['station__opis_id', 'retail_price']
    autocomplete_fields = ['station']

    @admin.display(description='OPIS truckstop ID', ordering='station__opis_id')
    def opis_id(self, obj: FuelPrice) -> int:
        """Return the station's OPIS ID."""
        return obj.station.opis_id

    @admin.display(description='city', ordering='station__city')
    def city(self, obj: FuelPrice) -> str:
        """Return the station's city."""
        return obj.station.city

    @admin.display(description='state', ordering='station__state')
    def state(self, obj: FuelPrice) -> str:
        """Return the station's state."""
        return obj.station.state


@admin.register(SavedTrip)
class SavedTripAdmin(admin.ModelAdmin):
    """Trips planned through the API."""

    list_display = ['start', 'finish', 'distance_miles', 'total_fuel_cost', 'times_planned', 'last_planned_at']
    search_fields = ['start', 'finish']
    ordering = ['-last_planned_at']
