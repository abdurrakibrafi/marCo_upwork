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

        from apps.entity.utils.matcher import is_national_team, _logo_url, is_valid_statpal_logo

        # 1. Clear any invalid soccer/non-soccer logos from StatPal (teams, athletes, leagues)
        invalid_logos = Entity.objects.filter(logo_url__contains="statpal.io")
        cleared_count = 0
        for entity in invalid_logos:
            is_soccer_url = "/soccer/" in entity.logo_url
            should_clear = False
            
            if entity.type == "league":
                should_clear = True
            elif entity.type == "athlete":
                if entity.sport != "soccer" or not is_soccer_url or not is_valid_statpal_logo(entity.logo_url):
                    should_clear = True
            elif entity.type == "team":
                if entity.sport == "soccer":
                    if not is_soccer_url or not is_valid_statpal_logo(entity.logo_url):
                        should_clear = True
                else:
                    if is_national_team(entity.name):
                        if not is_soccer_url or not is_valid_statpal_logo(entity.logo_url):
                            should_clear = True
                    else:
                        should_clear = True
                        
            if should_clear:
                self.stdout.write(f"Clearing invalid logo for {entity.type} '{entity.name}': {entity.logo_url}")
                if not dry_run:
                    entity.logo_url = ""
                    entity.save(update_fields=["logo_url"])
                cleared_count += 1
        if cleared_count:
            self.stdout.write(self.style.SUCCESS(f"Cleared {cleared_count} invalid logo URLs."))

        # Get only team entities with empty or invalid StatPal logo_url
        from django.db.models import Q
        entities_to_fix = Entity.objects.filter(
            Q(logo_url="") | Q(logo_url__contains="statpal.io"),
            type="team"
        ).order_by("name")
        total_found = entities_to_fix.count()
        self.stdout.write(f"Found {total_found} team entities with empty/invalid logo_url.")

        backfilled_count = 0
        for entity in entities_to_fix:
            current_logo = entity.logo_url
            is_invalid = current_logo and "statpal.io" in current_logo
            if is_invalid:
                self.stdout.write(f"Clearing invalid logo for '{entity.name}': {current_logo}")
                if not dry_run:
                    entity.logo_url = ""
                    entity.save(update_fields=["logo_url"])

            # Bypass cache to perform a direct query, or use cache. Clear cache to be safe.
            from django.core.cache import cache
            cache_key = f"logo_by_name_{entity.name.strip().lower().replace(' ', '_')}"
            cache.delete(cache_key)
            
            fallback_logo = find_team_logo_by_name(entity.name)
            if not fallback_logo and entity.api_source == "statpal" and entity.external_id:
                if is_national_team(entity.name):
                    fallback_logo = _logo_url(entity.type, entity.external_id, entity.sport)

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
