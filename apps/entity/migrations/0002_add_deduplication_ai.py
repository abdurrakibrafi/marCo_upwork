"""
Auto-generated migration for new models and fields.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('entity', '0001_initial'),  # Update this based on your actual last migration
        ('feed', '0001_initial'),
        ('nest', '0001_initial'),
    ]

    operations = [
        # Add new fields to Entity model
        migrations.AddField(
            model_name='entity',
            name='embedding',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='entity',
            name='canonical_entity',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='duplicates', to='entity.entity'),
        ),
        migrations.AddField(
            model_name='entity',
            name='normalized_name',
            field=models.CharField(blank=True, db_index=True, max_length=200),
        ),
        
        # Create CanonicalEntity model
        migrations.CreateModel(
            name='CanonicalEntity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sport', models.CharField(choices=[('basketball', 'Basketball'), ('football', 'American Football'), ('soccer', 'Soccer'), ('baseball', 'Baseball'), ('hockey', 'Hockey'), ('cricket', 'Cricket'), ('tennis', 'Tennis'), ('f1', 'Formula 1'), ('mma', 'MMA'), ('golf', 'Golf')], max_length=50)),
                ('entity_type', models.CharField(choices=[('team', 'Team'), ('athlete', 'Athlete'), ('league', 'League')], max_length=20)),
                ('canonical_name', models.CharField(db_index=True, max_length=200)),
                ('name_variations', models.JSONField(default=list)),
                ('external_ids', models.JSONField(default=dict)),
                ('is_curated', models.BooleanField(default=False)),
                ('is_verified', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('entity', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='canonical', to='entity.entity')),
            ],
            options={
                'ordering': ['sport', 'canonical_name'],
            },
        ),
        migrations.AddIndex(
            model_name='canonicalentity',
            index=models.Index(fields=['sport', 'canonical_name'], name='entity_canon_sport_0a1b2c_idx'),
        ),
        migrations.AddIndex(
            model_name='canonicalentity',
            index=models.Index(fields=['is_curated', 'is_verified'], name='entity_canon_is_cur_3d4e5f_idx'),
        ),
        
        # Create RSSSource model
        migrations.CreateModel(
            name='RSSSource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=200)),
                ('url', models.URLField(max_length=2000, unique=True)),
                ('sport', models.CharField(choices=[('basketball', 'Basketball'), ('football', 'American Football'), ('soccer', 'Soccer'), ('baseball', 'Baseball'), ('hockey', 'Hockey'), ('cricket', 'Cricket'), ('tennis', 'Tennis'), ('f1', 'Formula 1'), ('mma', 'MMA'), ('golf', 'Golf')], max_length=50)),
                ('keywords', models.JSONField(default=list)),
                ('is_active', models.BooleanField(default=True)),
                ('fetch_interval_hours', models.PositiveIntegerField(default=6)),
                ('last_fetched_at', models.DateTimeField(blank=True, null=True)),
                ('fetch_failures', models.PositiveIntegerField(default=0)),
                ('is_verified', models.BooleanField(default=False)),
                ('estimated_quality', models.CharField(choices=[('high', 'High'), ('medium', 'Medium'), ('low', 'Low')], default='medium', max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('entities', models.ManyToManyField(blank=True, related_name='rss_sources', to='entity.entity')),
            ],
            options={
                'ordering': ['sport', 'name'],
            },
        ),
        migrations.AddIndex(
            model_name='rsssource',
            index=models.Index(fields=['sport', 'is_active'], name='feed_rss_source_sport_active_idx'),
        ),
        migrations.AddIndex(
            model_name='rsssource',
            index=models.Index(fields=['is_verified'], name='feed_rss_source_verified_idx'),
        ),
        
        # Create EntitySource model
        migrations.CreateModel(
            name='EntitySource',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('priority', models.IntegerField(default=0)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('source', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='entity_selections', to='feed.source')),
                ('user_nest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='selected_sources', to='nest.usernest')),
            ],
            options={
                'ordering': ['-priority', '-added_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='entitysource',
            constraint=models.UniqueConstraint(fields=['user_nest', 'source'], name='unique_user_nest_source'),
        ),
    ]
