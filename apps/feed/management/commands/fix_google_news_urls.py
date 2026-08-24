from django.core.management.base import BaseCommand
from apps.feed.models import FeedItem
from apps.feed.utils_url import resolve_real_article_url
import hashlib
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Management command to decode Google News redirect URLs into direct publisher links."""
    help = "Decodes and replaces all existing Google News redirect URLs in FeedItem with direct publisher URLs."

    def add_arguments(self, parser):
        """Register CLI arguments for batch processing limits."""
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit the number of items processed (0 = all)',
        )

    def handle(self, *args, **options):
        """Iterate through Google News redirect links and resolve canonical destination URLs."""
        limit = options['limit']
        qs = FeedItem.objects.filter(url__icontains="news.google.com")
        if limit > 0:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(f"Found {total} FeedItems with news.google.com URLs.")

        updated = 0
        failed = 0

        for item in qs.iterator():
            raw_url = item.url
            decoded_url = resolve_real_article_url(raw_url)

            if decoded_url and decoded_url != raw_url and "news.google.com" not in decoded_url:
                new_hash = hashlib.md5(decoded_url.encode()).hexdigest()
                # Check for hash collisions before updating
                existing_item = FeedItem.objects.filter(url_hash=new_hash).exclude(id=item.id).first()
                if existing_item:
                    # Duplicate found: merge entities and remove redundant item
                    existing_item.entities.add(*item.entities.all())
                    item.delete()
                else:
                    item.url = decoded_url
                    item.url_hash = new_hash
                    item.save(update_fields=['url', 'url_hash'])
                updated += 1
            else:
                failed += 1

            if (updated + failed) % 50 == 0:
                self.stdout.write(f"Processed {updated + failed}/{total} items...")

        self.stdout.write(self.style.SUCCESS(f"Successfully fixed {updated} Google News URLs ({failed} unresolvable/skipped)."))
