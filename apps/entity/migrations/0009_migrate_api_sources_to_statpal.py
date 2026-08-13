from django.db import migrations

def update_api_sources(apps, schema_editor):
    Entity = apps.get_model('entity', 'Entity')
    legacy_sources = ['api_sports', 'balldontlie', 'api_cricket']
    updated_count = Entity.objects.filter(api_source__in=legacy_sources).update(api_source='statpal')
    print(f"Updated {updated_count} Entity records to api_source='statpal'")

    Event = apps.get_model('event', 'Event')
    updated_events = Event.objects.filter(api_source__in=legacy_sources).update(api_source='statpal')
    print(f"Updated {updated_events} Event records to api_source='statpal'")

def reverse_api_sources(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('entity', '0008_seed_bangladesh_hockey'),
        ('event', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(update_api_sources, reverse_api_sources),
    ]
