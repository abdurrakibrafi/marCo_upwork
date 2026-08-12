import django.db.models.fields
from django.db import migrations

def seed_bangladesh_hockey(apps, schema_editor):
    Entity = apps.get_model('entity', 'Entity')
    Entity.objects.get_or_create(
        name='Bangladesh Hockey',
        type='team',
        sport='hockey',
        defaults={
            'logo_url': 'https://r2.thesportsdb.com/images/media/team/badge/j74o4t1646775146.png',
            'has_api_data': True,
            'follower_count': 1
        }
    )

def reverse_func(apps, schema_editor):
    pass

class Migration(migrations.Migration):

    dependencies = [
        ('entity', '0007_fix_virat_kohli_dob'),
    ]

    operations = [
        migrations.RunPython(seed_bangladesh_hockey, reverse_func),
    ]
