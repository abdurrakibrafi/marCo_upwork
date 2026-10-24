import re
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.db.models import Q
from apps.entity.models import Entity
from apps.feed.models import Source, FeedItem
from apps.entity.utils.matcher import _MAJOR_LEAGUE_BADGES


class Command(BaseCommand):
    """Management command to run all database maintenance steps for VPS deployment."""
    help = "Run VPS maintenance: reactivate sources, clean thumbnails, update league logos, and merge duplicates"

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("=== Starting VPS Maintenance ==="))

        # 1. Reactivate news sources
        self.stdout.write("1. Reactivating deactivated news sources...")
        src_cnt = Source.objects.filter(is_active=False).update(
            is_active=True,
            poll_failures=0,
            poll_interval_minutes=30
        )
        self.stdout.write(self.style.SUCCESS(f"   Successfully reactivated {src_cnt} sources."))

        # 2. Clean false team badges from article thumbnails
        self.stdout.write("2. Cleaning false team badges from article thumbnails...")
        bad_cnt = FeedItem.objects.filter(
            Q(thumbnail_url__icontains='/badge/') |
            Q(thumbnail_url__icontains='/cutout/') |
            Q(thumbnail_url__icontains='api-sports.io/football/leagues/')
        ).update(thumbnail_url='')
        self.stdout.write(self.style.SUCCESS(f"   Successfully cleaned {bad_cnt} badge thumbnails."))

        # 3. Backfill YouTube thumbnails
        self.stdout.write("3. Backfilling YouTube video thumbnails...")
        yt_items = FeedItem.objects.filter(thumbnail_url='', url__icontains='youtube.com/watch')
        yt_updated = 0
        for item in yt_items:
            m = re.search(r'v=([a-zA-Z0-9_-]{11})', item.url)
            if m:
                item.thumbnail_url = f'https://img.youtube.com/vi/{m.group(1)}/hqdefault.jpg'
                item.save(update_fields=['thumbnail_url'])
                yt_updated += 1
        self.stdout.write(self.style.SUCCESS(f"   Successfully backfilled {yt_updated} YouTube thumbnails."))

        # 4. Update major league logos in DB
        self.stdout.write("4. Updating major league logos in DB...")
        lg_updated = 0
        for k, badge in _MAJOR_LEAGUE_BADGES.items():
            lg_updated += Entity.objects.filter(type='league', name__iexact=k).update(logo_url=badge)
        self.stdout.write(self.style.SUCCESS(f"   Successfully updated {lg_updated} league logo entries."))

        # 5. Merge duplicate entities and re-link events/follows
        self.stdout.write("5. Merging duplicate entities into canonical records...")
        try:
            call_command('merge_duplicate_entities', commit=True)
            self.stdout.write(self.style.SUCCESS("   Successfully merged duplicate entities."))
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"   merge_duplicate_entities completed with notice: {e}"))

        self.stdout.write(self.style.SUCCESS("=== VPS Maintenance Completed Successfully ==="))
