from django.core.management.base import BaseCommand
from apps.feed.models import FeedItem
from apps.feed.tasks import _is_junk_page

class Command(BaseCommand):
    help = 'Identify and clean up already scraped junk/sportsbook articles, resetting their fetch state.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Dry run - list the articles without actually modifying them in the database',
        )
        parser.add_argument(
            '--limit',
            type=int,
            default=0,
            help='Limit the number of articles processed (0 for no limit)',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        # Get fetched articles
        qs = FeedItem.objects.filter(content_fetched=True)
        
        # We fetch only needed fields for performance
        if limit > 0:
            qs = qs[:limit]

        total = qs.count()
        self.stdout.write(self.style.NOTICE(f"Scanning {total} fetched articles for junk..."))

        reset_count = 0
        for item in qs:
            if _is_junk_page(item.url, item.content):
                self.stdout.write(
                    self.style.WARNING(
                        f"Junk detected: ID={item.id} | Title={item.title[:60]}... | URL={item.url}"
                    )
                )
                if not dry_run:
                    item.content = ""
                    item.ai_summary = ""
                    item.content_fetched = False
                    item.save(update_fields=['content', 'ai_summary', 'content_fetched'])
                reset_count += 1

        if dry_run:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DRY-RUN DONE] Found {reset_count} junk articles out of {total} total scanned."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[DONE] Successfully reset {reset_count} junk articles out of {total} total scanned."
                )
            )
