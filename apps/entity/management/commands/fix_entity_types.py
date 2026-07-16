from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity, Team, Athlete

class Command(BaseCommand):
    help = "Fix entity type collisions (e.g., entities set as team but linked to an Athlete record)"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes and count affected records without saving'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # Find all Entity records that are type='team' but have an Athlete details entry
        mismatched_entities = Entity.objects.filter(
            type='team',
            athlete_details__isnull=False
        ).select_related('athlete_details')

        affected_count = mismatched_entities.count()
        self.stdout.write(f"Found {affected_count} affected entity records with type mismatch (set as 'team' but has Athlete details).")

        for entity in mismatched_entities:
            self.stdout.write(
                f"  - Entity ID {entity.id}: Name='{entity.name}', Sport='{entity.sport}' "
                f"(Linked Athlete: {entity.athlete_details.first_name} {entity.athlete_details.last_name})"
            )

            if not dry_run:
                try:
                    with transaction.atomic():
                        # Update type to 'athlete'
                        entity.type = 'athlete'
                        entity.save(update_fields=['type'])

                        # Delete any associated Team record (players shouldn't have team details)
                        Team.objects.filter(entity=entity).delete()
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"    Failed to fix entity ID {entity.id}: {e}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"Successfully corrected {affected_count} entity types to 'athlete' and cleaned up associated team details."))
        else:
            self.stdout.write(self.style.SUCCESS(f"Dry-run preview completed. {affected_count} records would be corrected."))
