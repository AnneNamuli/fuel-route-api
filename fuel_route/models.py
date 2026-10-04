"""Database models: fuel stations, their prices and saved trips."""

import uuid

from django.db import models
from django.db.models import Min

QUERY_MAX_LENGTH = 200  # longest start/finish text accepted by the API and saved


class FuelStationQuerySet(models.QuerySet):
    """Queries on stations."""

    def with_lowest_price(self) -> 'FuelStationQuerySet':
        """Annotate each station with `min_price`, its lowest listed price (None if it has no prices)."""
        return self.annotate(min_price=Min('prices__retail_price'))


class FuelStation(models.Model):
    """A truck stop, identified by its OPIS ID. Kept across imports so derived data survives."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    opis_id = models.PositiveIntegerField(unique=True, verbose_name='OPIS truckstop ID')
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(verbose_name='rack ID')
    # Not in the CSV; filled in by geocoding so stations can be matched to a route.
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    objects = FuelStationQuerySet.as_manager()

    def __str__(self) -> str:
        """Return the name, city and state."""
        return f'{self.name} ({self.city}, {self.state})'


class FuelPrice(models.Model):
    """One row of the OPIS CSV. A station can have several, each with its own name alias and price."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    station = models.ForeignKey(FuelStation, on_delete=models.CASCADE, related_name='prices')
    name = models.CharField(max_length=100, verbose_name='listed name')
    retail_price = models.DecimalField(max_digits=10, decimal_places=8, verbose_name='price per gallon')

    class Meta:
        verbose_name = 'price'

    def __str__(self) -> str:
        """Return the listed name and price."""
        return f'{self.name}: {self.retail_price}'


class SavedTrip(models.Model):
    """A start/finish pair that has been planned successfully, offered again in dropdowns."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    start = models.CharField(max_length=QUERY_MAX_LENGTH)
    finish = models.CharField(max_length=QUERY_MAX_LENGTH)
    start_label = models.CharField(max_length=255)
    finish_label = models.CharField(max_length=255)
    distance_miles = models.FloatField()
    total_fuel_cost = models.DecimalField(max_digits=10, decimal_places=2)
    times_planned = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    last_planned_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['start', 'finish'], name='unique_saved_trip')]

    def __str__(self) -> str:
        """Return 'Start → Finish'."""
        return f'{self.start} → {self.finish}'
