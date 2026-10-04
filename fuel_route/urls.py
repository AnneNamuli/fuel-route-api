"""URL routes for the trip API, saved trips, the map page and the health check."""

from django.urls import path

from . import views

urlpatterns = [
    path('api/trips/', views.TripView.as_view(), name='trip'),
    path('api/trips/saved/', views.SavedTripListView.as_view(), name='saved-trips'),
    path('map/', views.TripMapView.as_view(), name='trip-map'),
    path('health/', views.HealthView.as_view(), name='health'),
]
