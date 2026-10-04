"""Rename SavedRoute to SavedTrip: a trip is the planned journey; a route is only the road path."""

from django.db import migrations, models


class Migration(migrations.Migration):
    """Rename the model and its unique constraint; the data is kept."""

    dependencies = [
        ('fuel_route', '0002_savedroute'),
    ]

    operations = [
        migrations.RenameModel(old_name='SavedRoute', new_name='SavedTrip'),
        migrations.RemoveConstraint(model_name='savedtrip', name='unique_saved_route'),
        migrations.AddConstraint(
            model_name='savedtrip',
            constraint=models.UniqueConstraint(fields=('start', 'finish'), name='unique_saved_trip'),
        ),
    ]
