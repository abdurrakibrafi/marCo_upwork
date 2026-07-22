import time
from datetime import datetime
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.thesportsdb import thesportsdb_service

class Command(BaseCommand):
    help = "Seed cricket player rosters and headshots from TheSportsDB API"

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview players; fetches rosters for verification but skips DB writes')
        parser.add_argument('--limit', type=int, default=None,
                            help='Limit number of cricket teams to process')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        limit = options['limit']

        if dry_run:
            self.stdout.write(self.style.WARNING("=== DRY RUN MODE: DB writes skipped ==="))

        teams = Entity.objects.filter(type='team', sport='cricket').order_by('name')
        self.stdout.write(f"Found {teams.count()} cricket teams in the database.")
        if not teams.exists():
            return

        if limit:
            teams = teams[:limit]

        created_count = 0
        updated_count = 0

        for team in teams:
            self.stdout.write(f"\nFetching cricket roster for: {team.name}")
            try:
                roster = thesportsdb_service.get_team_roster(team_name=team.name)
            except Exception as exc:
                self.stdout.write(self.style.ERROR(f"Error fetching roster for {team.name}: {exc}"))
                continue

            if not roster:
                self.stdout.write(self.style.NOTICE(f"✗ No roster found on TheSportsDB for {team.name}"))
                time.sleep(1.0)
                continue

            self.stdout.write(self.style.SUCCESS(f"✓ Found {len(roster)} players on roster."))

            for pdata in roster:
                pname = pdata.get('name')
                if not pname:
                    continue

                headshot = pdata.get('headshot_url', '')
                ext_id = str(pdata.get('id_player', ''))

                if dry_run:
                    self.stdout.write(f"  [DRY RUN] Player: {pname} | Headshot: {headshot}")
                    continue

                athlete_entity, created = Entity.objects.get_or_create(
                    name=pname,
                    type='athlete',
                    sport='cricket',
                    defaults={
                        'api_source': 'thesportsdb',
                        'external_id': ext_id,
                        'logo_url': headshot,
                        'description': pdata.get('description', '')[:500],
                        'country': pdata.get('nationality', ''),
                        'has_api_data': True,
                    }
                )

                if not created:
                    updated_fields = []
                    if headshot and not athlete_entity.logo_url:
                        athlete_entity.logo_url = headshot
                        updated_fields.append('logo_url')
                    if ext_id and not athlete_entity.external_id:
                        athlete_entity.external_id = ext_id
                        athlete_entity.api_source = 'thesportsdb'
                        updated_fields.extend(['external_id', 'api_source'])
                    if updated_fields:
                        athlete_entity.save(update_fields=updated_fields)

                name_parts = pname.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                dob = None
                dob_str = pdata.get('date_of_birth')
                if dob_str:
                    try:
                        dob = datetime.strptime(dob_str[:10], '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        dob = None

                athlete_detail, _ = Athlete.objects.get_or_create(
                    entity=athlete_entity,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'date_of_birth': dob,
                        'nationality': pdata.get('nationality', ''),
                        'position': pdata.get('position', ''),
                        'current_team': team,
                    }
                )
                if athlete_detail.current_team != team or (pdata.get('position') and not athlete_detail.position):
                    athlete_detail.current_team = team
                    if pdata.get('position'):
                        athlete_detail.position = pdata.get('position')
                    athlete_detail.save()

                if created:
                    created_count += 1
                else:
                    updated_count += 1

            time.sleep(1.5)

        self.stdout.write(self.style.SUCCESS(
            f"\nCricket player backfill completed: {created_count} created, {updated_count} updated."
        ))
