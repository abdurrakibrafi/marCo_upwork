import os
import django
import django.db.models as models

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
django.setup()

from apps.event.models import Event

print("Checking ALL events in DB involving Argentina:")
arg_events = Event.objects.filter(
    models.Q(home_entity__name__iexact="Argentina") | models.Q(away_entity__name__iexact="Argentina")
)

print(f"Total events found: {arg_events.count()}")
for e in arg_events.order_by('start_time'):
    print(f"ID: {e.id} | Sport: {e.sport} | Home: {e.home_entity.name} | Away: {e.away_entity.name if e.away_entity else 'None'} | Status: {e.status} (raw: {e.status_detail}) | Time: {e.start_time}")
