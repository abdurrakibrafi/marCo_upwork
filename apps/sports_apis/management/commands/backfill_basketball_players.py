import time
import re
import unicodedata
import datetime
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.statpal import statpal_service

class Command(BaseCommand):
    help = "Backfill roster/athlete entities for NBA basketball teams using StatPal API"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Run the command without saving anything to the database'
        )

    def normalize_name(self, name: str) -> str:
        if not name:
            return ""
        name = unicodedata.normalize('NFKD', name).encode('ASCII', 'ignore').decode('utf-8')
        return "".join(c for c in name.lower() if c.isalnum())

    def parse_height_cm(self, height_str: str) -> int | None:
        if not height_str:
            return None
        match = re.match(r"(\d+)'\s*(\d+)", height_str)
        if match:
            try:
                feet = int(match.group(1))
                inches = int(match.group(2))
                return int((feet * 12 + inches) * 2.54)
            except Exception:
                pass
        return None

    def parse_weight_kg(self, weight_str: str) -> int | None:
        if not weight_str:
            return None
        match = re.search(r"(\d+)", str(weight_str))
        if match:
            try:
                lbs = int(match.group(1))
                return int(lbs * 0.45359237)
            except Exception:
                pass
        return None

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # Robust mapping from normalized team names to StatPal V1 NBA roster abbreviations
        nba_team_map = {
            "atlantahawks": "atl",
            "bostonceltics": "bos",
            "brooklynnets": "bkn",
            "charlottehornets": "cha",
            "chicagobulls": "chi",
            "clevelandcavaliers": "cle",
            "dallasmavericks": "dal",
            "denvernuggets": "den",
            "detroitpistons": "det",
            "goldenstatewarriors": "gs",
            "houstonrockets": "hou",
            "indianapacers": "ind",
            "losangelesclippers": "lac",
            "laclippers": "lac",
            "clippers": "lac",
            "losangeleslakers": "lal",
            "lalakers": "lal",
            "lakers": "lal",
            "memphisgrizzlies": "mem",
            "miamiheat": "mia",
            "milwaukeebucks": "mil",
            "minnesotatimberwolves": "min",
            "neworleanspelicans": "no",
            "newyorkknicks": "ny",
            "knicks": "ny",
            "oklahomacitythunder": "okc",
            "orlandomagic": "orl",
            "philadelphia76ers": "phi",
            "phoenixsuns": "phx",
            "portlandtrailblazers": "por",
            "sacramentokings": "sac",
            "sanantoniospurs": "sa",
            "torontoraptors": "tor",
            "utahjazz": "uta",
            "washingtonwizards": "wsh"
        }

        # Query all basketball team entities in the DB
        teams = Entity.objects.filter(type='team', sport='basketball')
        self.stdout.write(f"Found {teams.count()} basketball teams in the database.")

        created_count = 0
        updated_count = 0

        for team in teams:
            normalized_name = self.normalize_name(team.name)
            team_abbrev = nba_team_map.get(normalized_name)

            if not team_abbrev:
                self.stdout.write(self.style.WARNING(f"  Could not find official NBA team abbreviation mapping for '{team.name}'"))
                continue

            self.stdout.write(f"\nProcessing team: {team.name} (Abbreviation: {team_abbrev})")
            time.sleep(0.5)  # Rate limiting safety delay

            response = statpal_service.get_nba_roster(team_abbrev)
            if not response.get('success'):
                self.stdout.write(self.style.ERROR(f"  Failed to fetch roster for {team.name} ({team_abbrev}): {response.get('error')}"))
                continue

            data = response.get('data', {})
            team_info = data.get('team', {})
            team_id = team_info.get('id')
            players = team_info.get('player', [])

            self.stdout.write(f"  Found {len(players)} players in roster.")

            # Fix/remap team external_id to match StatPal's team ID format if it changed
            if team_id:
                if team.external_id != str(team_id) or team.api_source != 'statpal':
                    self.stdout.write(self.style.WARNING(f"  Remapping team external_id from {team.external_id} ({team.api_source}) -> {team_id} (statpal)"))
                    if not dry_run:
                        team.api_source = 'statpal'
                        team.external_id = str(team_id)
                        team.save(update_fields=['api_source', 'external_id'])

            for player in players:
                player_id = player.get('id')
                fullname = player.get('name', '').strip()
                if not player_id or not fullname:
                    continue

                pos = player.get('position', '')
                jersey = player.get('number')
                jersey_number = int(jersey) if jersey and str(jersey).isdigit() else None
                height_cm = self.parse_height_cm(player.get('heigth'))
                weight_kg = self.parse_weight_kg(player.get('weigth'))

                # Parse first and last names
                parts = fullname.split(' ')
                first_name = parts[0]
                last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                # Default empty/null fields since roster endpoint doesn't contain these
                nationality = ""
                dob = None
                logo_url = ""

                self.stdout.write(f"    Player: {fullname} (Position: {pos}, Jersey: {jersey_number}, Height: {height_cm}cm, Weight: {weight_kg}kg)")

                if not dry_run:
                    try:
                        ae, created = Entity.objects.update_or_create(
                            api_source='statpal',
                            external_id=str(player_id),
                            defaults={
                                'type': 'athlete',
                                'name': fullname,
                                'sport': 'basketball',
                                'logo_url': logo_url,
                                'has_api_data': True,
                            }
                        )
                        Athlete.objects.update_or_create(
                            entity=ae,
                            defaults={
                                'first_name': first_name,
                                'last_name': last_name,
                                'date_of_birth': dob,
                                'nationality': nationality,
                                'height_cm': height_cm,
                                'weight_kg': weight_kg,
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
            self.stdout.write(self.style.SUCCESS(f"\nBasketball backfill completed! Created {created_count} athletes, updated {updated_count} athletes."))
        else:
            self.stdout.write(self.style.SUCCESS("\nBasketball backfill dry-run completed successfully!"))
