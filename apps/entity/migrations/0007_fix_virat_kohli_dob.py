import datetime
from django.db import migrations

def fix_virat_kohli_dob(apps, schema_editor):
    Athlete = apps.get_model('entity', 'Athlete')
    Entity = apps.get_model('entity', 'Entity')
    
    entities = Entity.objects.filter(name__icontains='Virat Kohli', sport='cricket')
    for ent in entities:
        try:
            ath = getattr(ent, 'athlete_details', None)
            if ath:
                ath.date_of_birth = datetime.date(1988, 11, 5)
                ath.save(update_fields=['date_of_birth'])
        except Exception:
            pass

def reverse_func(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('entity', '0006_alter_canonicalentity_sport_alter_entity_sport'),
    ]

    operations = [
        migrations.RunPython(fix_virat_kohli_dob, reverse_func),
    ]
