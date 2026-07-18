from django.core.management.base import BaseCommand
from apps.entity.models import Entity
from apps.entity.utils.matcher import find_team_logo_by_name

class Command(BaseCommand):
    help = "Backfill empty logo URLs for entities by searching the database for matching names with logos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would change without saving to the database",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # Get only team entities with empty or invalid StatPal logo_url
        from django.db.models import Q
        entities_to_fix = Entity.objects.filter(
            Q(logo_url="") | (Q(logo_url__contains="statpal.io") & ~Q(logo_url__contains="/soccer/")),
            type="team"
        ).order_by("name")
        total_found = entities_to_fix.count()
        self.stdout.write(f"Found {total_found} team entities with empty/invalid logo_url.")

        backfilled_count = 0
        for entity in entities_to_fix:
            # Bypass cache to perform a direct query, or use cache. Clear cache to be safe.
            from django.core.cache import cache
            cache_key = f"logo_by_name_{entity.name.strip().lower().replace(' ', '_')}"
            cache.delete(cache_key)
            
            fallback_logo = find_team_logo_by_name(entity.name)
            if fallback_logo:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Found logo for '{entity.name}' ({entity.sport}, {entity.type}): {fallback_logo}"
                    )
                )
                if not dry_run:
                    entity.logo_url = fallback_logo
                    entity.save(update_fields=["logo_url"])
                backfilled_count += 1

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry-run completed. {backfilled_count} logos would be backfilled."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Successfully backfilled {backfilled_count} logos!"))
