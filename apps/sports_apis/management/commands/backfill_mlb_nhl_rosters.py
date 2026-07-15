import time
import re
import requests
import unicodedata
import datetime
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.mlb_stats import mlb_stats_service
from apps.sports_apis.services.nhl_api import nhl_api_service

class Command(BaseCommand):
    help = "Backfill roster/athlete entities for MLB and NHL teams using free official APIs"

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

    def parse_mlb_height(self, height_str: str) -> int | None:
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

    def parse_mlb_weight(self, weight_lbs) -> int | None:
        if not weight_lbs:
            return None
        try:
            return int(float(weight_lbs) * 0.45359237)
        except Exception:
            return None

    def parse_birth_date(self, date_str: str) -> datetime.date | None:
        if not date_str:
            return None
        try:
            return datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            return None

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: Database changes will not be saved ==="))

        # --- 1. Fetch official MLB and NHL team mappings to match by name ---
        self.stdout.write("Fetching MLB official teams list...")
        mlb_teams_map = {}
        try:
            resp = requests.get("https://statsapi.mlb.com/api/v1/teams?sportId=1", timeout=10)
            if resp.status_code == 200:
                for t in resp.json().get('teams', []):
                    tid = t.get('id')
                    tname = t.get('name')
                    tabbrev = t.get('abbreviation')
                    if tid and tname:
                        mlb_teams_map[self.normalize_name(tname)] = tid
                    if tid and tabbrev:
                        mlb_teams_map[self.normalize_name(tabbrev)] = tid
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to fetch MLB teams list: {e}"))

        self.stdout.write("Fetching NHL official teams list...")
        nhl_teams_map = {}
        try:
            resp = requests.get("https://api-web.nhle.com/v1/standings/now", timeout=10)
            if resp.status_code == 200:
                for t in resp.json().get('standings', []):
                    tabbrev = t.get('teamAbbrev', {}).get('default')
                    tname = t.get('teamName', {}).get('default')
                    tcommon = t.get('teamCommonName', {}).get('default')
                    if tabbrev:
                        if tname:
                            nhl_teams_map[self.normalize_name(tname)] = tabbrev
                        if tcommon:
                            nhl_teams_map[self.normalize_name(tcommon)] = tabbrev
                        nhl_teams_map[self.normalize_name(tabbrev)] = tabbrev
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to fetch NHL teams list: {e}"))

        # --- 2. Query team entities in DB ---
        teams = Entity.objects.filter(type='team', sport__in=['baseball', 'hockey'])
        self.stdout.write(f"Found {teams.count()} baseball/hockey teams in the database.")

        for team in teams:
            normalized_name = self.normalize_name(team.name)
            self.stdout.write(f"\nProcessing team: {team.name} (Sport: {team.sport}, Source: {team.api_source})")

            # --- A. Baseball (MLB) ---
            if team.sport == 'baseball':
                mlb_id = mlb_teams_map.get(normalized_name)
                if not mlb_id:
                    self.stdout.write(self.style.WARNING(f"  Could not find official MLB team mapping for '{team.name}'"))
                    continue

                self.stdout.write(f"  Matched with MLB ID: {mlb_id}. Fetching roster...")
                time.sleep(0.5)  # Rate limit safety delay before API call
                roster = mlb_stats_service.get_team_roster(mlb_id)
                self.stdout.write(f"  Found {len(roster)} players on roster.")

                for entry in roster:
                    person = entry.get('person', {})
                    pid = person.get('id')
                    fullname = person.get('fullName')
                    if not pid or not fullname:
                        continue

                    # Hydrated fields
                    first_name = person.get('firstName', '')
                    last_name = person.get('lastName', '')
                    if not first_name or not last_name:
                        parts = fullname.split(' ')
                        first_name = parts[0]
                        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

                    jersey = entry.get('jerseyNumber')
                    jersey_number = int(jersey) if jersey and str(jersey).isdigit() else None
                    pos = entry.get('position', {}).get('abbreviation') or person.get('primaryPosition', {}).get('abbreviation', '')
                    height = self.parse_mlb_height(person.get('height'))
                    weight = self.parse_mlb_weight(person.get('weight'))
                    dob = self.parse_birth_date(person.get('birthDate'))
                    nationality = person.get('birthCountry', '')

                    # Headshot URL format
                    logo_url = f"https://img.mlbstatic.com/mlb-photos/image/upload/d_people:profile:action:generic.svg/w_213,q_auto:best/v1/people/{pid}/profile/action/generic"

                    self.stdout.write(f"    Player: {fullname} (Position: {pos}, Jersey: {jersey_number})")
                    if not dry_run:
                        try:
                            ae, _ = Entity.objects.update_or_create(
                                api_source='mlb_stats',
                                external_id=str(pid),
                                defaults={
                                    'type': 'athlete',
                                    'name': fullname,
                                    'sport': 'baseball',
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
                                    'height_cm': height,
                                    'weight_kg': weight,
                                    'current_team': team,
                                    'position': pos,
                                    'jersey_number': jersey_number,
                                }
                            )
                        except Exception as p_err:
                            self.stdout.write(self.style.ERROR(f"      Failed to save player {fullname}: {p_err}"))

                time.sleep(0.5)  # Rate limit safety

            # --- B. Hockey (NHL) ---
            elif team.sport == 'hockey':
                nhl_abbrev = nhl_teams_map.get(normalized_name)
                if not nhl_abbrev:
                    self.stdout.write(self.style.WARNING(f"  Could not find official NHL team mapping for '{team.name}'"))
                    continue

                self.stdout.write(f"  Matched with NHL abbreviation: {nhl_abbrev}. Fetching roster...")
                time.sleep(0.5)  # Rate limit safety delay before API call
                roster = nhl_api_service.get_team_roster(nhl_abbrev)
                self.stdout.write(f"  Found {len(roster)} players on roster.")

                for player in roster:
                    pid = player.get('id')
                    fname_dict = player.get('firstName', {})
                    lname_dict = player.get('lastName', {})
                    first_name = fname_dict.get('default', '')
                    last_name = lname_dict.get('default', '')
                    fullname = f"{first_name} {last_name}".strip()
                    if not pid or not fullname:
                        continue

                    pos = player.get('positionCode', '')
                    jersey = player.get('jerseyNumber')
                    jersey_number = int(jersey) if jersey and str(jersey).isdigit() else None
                    height = player.get('heightInCentimeters')
                    weight = player.get('weightInKilograms')
                    dob = self.parse_birth_date(player.get('birthDate'))
                    nationality = player.get('birthCountry', '')
                    logo_url = player.get('headshot', '')

                    self.stdout.write(f"    Player: {fullname} (Position: {pos}, Jersey: {jersey_number})")
                    if not dry_run:
                        try:
                            ae, _ = Entity.objects.update_or_create(
                                api_source='nhl_api',
                                external_id=str(pid),
                                defaults={
                                    'type': 'athlete',
                                    'name': fullname,
                                    'sport': 'hockey',
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
                                    'height_cm': height,
                                    'weight_kg': weight,
                                    'current_team': team,
                                    'position': pos,
                                    'jersey_number': jersey_number,
                                }
                            )
                        except Exception as p_err:
                            self.stdout.write(self.style.ERROR(f"      Failed to save player {fullname}: {p_err}"))

                time.sleep(0.5)  # Rate limit safety

        self.stdout.write(self.style.SUCCESS("\nBackfill command completed successfully!"))
