import time
import re
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.statpal import statpal_service

class Command(BaseCommand):
    help = "Backfill roster/athlete entities for soccer teams using StatPal API"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run the command without saving anything to the database'
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # Get all soccer team entities in the DB that have external IDs
        teams = Entity.objects.filter(type='team', sport='soccer').exclude(external_id="")
        self.stdout.write(f"Found {teams.count()} soccer teams with external IDs in the database.")

        created_count = 0
        updated_count = 0

        # Position mapping from StatPal letters to human-readable names
        position_map = {
            'G': 'Goalkeeper',
            'D': 'Defender',
            'M': 'Midfielder',
            'A': 'Attacker'
        }

        for team in teams:
            self.stdout.write(f"\nProcessing team: {team.name} (External ID: {team.external_id})")
            time.sleep(0.5)  # Rate limiting safety delay

            response = statpal_service.get_soccer_team(team.external_id)
            if not response.get('success'):
                self.stdout.write(self.style.ERROR(f"  Failed to fetch squad for {team.name} (ID: {team.external_id}): {response.get('error')}"))
                continue

            data = response.get('data', {})
            team_info = data.get('team', {})
            squad_info = team_info.get('squad', {})
            players = squad_info.get('player', [])

            if not players:
                self.stdout.write(self.style.WARNING(f"  No players found in the squad for {team.name}."))
                continue

            self.stdout.write(f"  Found {len(players)} players in squad.")

            for player in players:
                player_id = player.get('id')
                fullname = player.get('name', '').strip()
                if not player_id or not fullname:
                    continue

                raw_pos = player.get('position', '')
                pos = position_map.get(raw_pos, raw_pos)

                jersey = player.get('number')
                jersey_number = int(jersey) if jersey and str(jersey).isdigit() else None

                # Parse first and last names
                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                # Construct image URL using access key from settings
                access_key = getattr(settings, 'STATPAL_ACCESS_KEY', '')
                logo_url = f"https://statpal.io/api/v2/soccer/images?type=player&id={player_id}&access_key={access_key}"

                nationality = team.country or ""

                self.stdout.write(f"    Player: {fullname} (Position: {pos}, Jersey: {jersey_number})")

                if not dry_run:
                    try:
                        ae, created = Entity.objects.update_or_create(
                            api_source='statpal',
                            external_id=str(player_id),
                            defaults={
                                'type': 'athlete',
                                'name': fullname,
                                'sport': 'soccer',
                                'logo_url': logo_url,
                                'has_api_data': True,
                                'is_active': True,
                            }
                        )
                        Athlete.objects.update_or_create(
                            entity=ae,
                            defaults={
                                'first_name': first_name,
                                'last_name': last_name,
                                'nationality': nationality,
                                'current_team': team,
                                'position': pos,
                                'jersey_number': jersey_number,
                            }
                        )
                        if created:
                            created_count += 1
                        else:
                            updated_count += 1
                    except Exception as p_err:
                        self.stdout.write(self.style.ERROR(f"      Failed to save player {fullname}: {p_err}"))

        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f"\nSoccer backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nSoccer backfill dry-run completed successfully!"))
