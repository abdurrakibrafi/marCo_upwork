from django.core.management.base import BaseCommand
from django.db import transaction
from apps.entity.models import Entity, Team, Athlete
from apps.entity.utils.normalizers import similarity_ratio

class Command(BaseCommand):
    help = "Fix entity type collisions (resolving player vs team ID collisions safely)"

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

        total_scanned = mismatched_entities.count()
        self.stdout.write(f"Scanned {total_scanned} entities with type='team' that have linked Athlete records.")

        players_to_fix = []
        teams_to_clean = []

        for entity in mismatched_entities:
            athlete = entity.athlete_details
            athlete_fullname = f"{athlete.first_name} {athlete.last_name}".strip()
            
            # Compare Entity name with Athlete name
            similarity = similarity_ratio(entity.name, athlete_fullname)

            if similarity >= 0.70:
                # Case A: Real Player wrongly marked as Team (names match, e.g. Nordin Amrabat)
                players_to_fix.append((entity, athlete_fullname))
            else:
                # Case B: Real Team with collided Athlete record (names differ, e.g. Boston Celtics vs player name)
                teams_to_clean.append((entity, athlete_fullname, athlete))

        # --- Report Preview ---
        self.stdout.write(self.style.WARNING(f"\n[Preview] Players wrongly marked as Teams: {len(players_to_fix)} record(s)"))
        for entity, name in players_to_fix:
            self.stdout.write(f"  - Entity ID {entity.id}: '{entity.name}' (Sport: {entity.sport}) -> Convert type to 'athlete'")

        self.stdout.write(self.style.WARNING(f"\n[Preview] Real Teams with collided Player records: {len(teams_to_clean)} record(s)"))
        for entity, player_name, _ in teams_to_clean:
            self.stdout.write(f"  - Entity ID {entity.id}: '{entity.name}' (Sport: {entity.sport}) -> Keep as 'team', DELETE incorrect Player record '{player_name}'")

        # --- Execute Changes ---
        if not dry_run:
            fixed_players = 0
            cleaned_teams = 0
            try:
                with transaction.atomic():
                    # 1. Fix wrong player entities
                    for entity, _ in players_to_fix:
                        entity.type = 'athlete'
                        entity.save(update_fields=['type'])
                        Team.objects.filter(entity=entity).delete()
                        fixed_players += 1

                    # 2. Clean up collided athlete records from real teams
                    for entity, _, athlete in teams_to_clean:
                        athlete.delete()
                        cleaned_teams += 1

                self.stdout.write(self.style.SUCCESS(f"\nSuccessfully corrected {fixed_players} player entities and cleaned up {cleaned_teams} collided team athlete records."))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"\nTransaction failed: {e}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nDry-run completed. {len(players_to_fix)} player entities would be corrected, {len(teams_to_clean)} collided athlete records would be deleted."))
