from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity
from apps.entity.utils.normalizers import similarity_ratio

class Command(BaseCommand):
    help = "Restore team logos from duplicate entities or different api_sources in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview logo restorations without saving to the database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # Find team entities with empty or broken StatPal logos
        target_entities = Entity.objects.filter(type='team').filter(
            logo_url=''
        ) | Entity.objects.filter(type='team').filter(
            logo_url__contains='statpal.io'
        ).exclude(sport='soccer')

        total_targets = target_entities.count()
        self.stdout.write(f"Found {total_targets} team entities with missing or broken logo URLs.")

        restores = []

        # Find all other team entities that have valid logos
        source_candidates = Entity.objects.filter(type='team').exclude(
            logo_url=''
        ).exclude(
            logo_url__contains='statpal.io'
        )

        for entity in target_entities:
            # 1. Try exact name match lookup first (fastest)
            duplicate = source_candidates.filter(
                name__iexact=entity.name,
                sport=entity.sport
            ).exclude(id=entity.id).first()

            # 2. Try substring match or fuzzy match lookup if exact match fails
            if not duplicate:
                sport_candidates = source_candidates.filter(sport=entity.sport).exclude(id=entity.id)
                for candidate in sport_candidates:
                    n1 = entity.name.lower()
                    n2 = candidate.name.lower()
                    if n1 in n2 or n2 in n1 or similarity_ratio(entity.name, candidate.name) >= 0.85:
                        duplicate = candidate
                        break

            if duplicate:
                restores.append((entity, duplicate.logo_url, duplicate.name, duplicate.api_source))

        # --- Report Preview ---
        self.stdout.write(self.style.SUCCESS(f"\nFound {len(restores)} logo(s) available for restore from duplicates:"))
        for entity, logo_url, source_name, source_api in restores:
            self.stdout.write(
                f"  - Entity ID {entity.id} '{entity.name}' ({entity.sport}) -> "
                f"Restore logo from duplicate '{source_name}' ({source_api}): {logo_url}"
            )

        # --- Execute Restore ---
        if not dry_run:
            updated_count = 0
            try:
                with transaction.atomic():
                    for entity, logo_url, _, _ in restores:
                        entity.logo_url = logo_url
                        entity.save(update_fields=['logo_url'])
                        updated_count += 1
                self.stdout.write(self.style.SUCCESS(f"\nSuccessfully restored {updated_count} logos from duplicate database entities!"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\nFailed to restore logos: {e}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nDry-run completed. {len(restores)} logos would be restored locally."))
