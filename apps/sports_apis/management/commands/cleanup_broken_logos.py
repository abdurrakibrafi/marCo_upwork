from django.core.management.base import BaseCommand
from apps.entity.models import Entity

class Command(BaseCommand):
    help = "Clean up broken StatPal logo URLs for non-soccer teams in the database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without saving to the database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE ==="))

        # Find all entities that have a broken statpal.io logo URL
        broken_entities = Entity.objects.filter(
            logo_url__contains='statpal.io'
        )

        self.stdout.write(f"Found {broken_entities.count()} non-soccer entities with broken StatPal logo URLs.")

        cleaned_count = 0
        for entity in broken_entities:
            self.stdout.write(f"  Clearing logo for {entity.name} (Type: {entity.type}, Sport: {entity.sport}, Logo: {entity.logo_url})")
            if not dry_run:
                entity.logo_url = ""
                entity.save(update_fields=['logo_url'])
                cleaned_count += 1

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Successfully cleared broken logo URLs for {cleaned_count} entities."))
        else:
            self.stdout.write(self.style.SUCCESS("Dry-run preview completed."))
