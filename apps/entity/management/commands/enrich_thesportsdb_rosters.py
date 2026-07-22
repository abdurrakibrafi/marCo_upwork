import time
from datetime import datetime
from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Athlete, Team
from apps.sports_apis.services.thesportsdb import thesportsdb_service

class Command(BaseCommand):
    help = 'Enrich team rosters and seed athlete profiles from TheSportsDB API'

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of teams processed'
        )
        parser.add_argument(
            '--sport',
            type=str,
            default=None,
            help='Filter by specific sport (e.g. soccer, basketball, cricket)'
        )
        parser.add_argument(
            '--team-name',
            type=str,
            default=None,
            help='Process a specific team by name'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        sport = options['sport']
        team_name_filter = options['team_name']

        self.stdout.write("Finding team entities to enrich rosters from TheSportsDB...")
        
        teams = Entity.objects.filter(type='team')
        if sport:
            teams = teams.filter(sport=sport.lower())
        if team_name_filter:
            teams = teams.filter(name__icontains=team_name_filter)

        teams = teams.order_by('name')
        total_count = teams.count()
        self.stdout.write(f"Found {total_count} teams in database.")

        if limit:
            teams = teams[:limit]
            self.stdout.write(f"Limiting to first {limit} teams.")

        total_players_created = 0
        total_players_updated = 0

        for team_entity in teams:
            self.stdout.write(f"\nFetching roster for team [{team_entity.sport.upper()}] {team_entity.name}...")
            
            try:
                roster = thesportsdb_service.get_team_roster(team_name=team_entity.name)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error calling TheSportsDB for {team_entity.name}: {e}"))
                continue

            if not roster:
                self.stdout.write(self.style.NOTICE(f"✗ No roster found on TheSportsDB for {team_entity.name}"))
                time.sleep(1.0)
                continue

            self.stdout.write(self.style.SUCCESS(f"✓ Found {len(roster)} players on roster."))

            for pdata in roster:
                name = pdata.get('name')
                if not name:
                    continue

                player_sport = pdata.get('sport') or team_entity.sport
                headshot_url = pdata.get('headshot_url') or ''
                ext_id = str(pdata.get('id_player') or '')

                # 1. Create or Update Entity
                athlete_entity, created = Entity.objects.get_or_create(
                    name=name,
                    type='athlete',
                    sport=player_sport,
                    defaults={
                        'api_source': 'thesportsdb',
                        'external_id': ext_id,
                        'logo_url': headshot_url,
                        'description': pdata.get('description', '')[:500],
                        'country': pdata.get('nationality', ''),
                        'has_api_data': True,
                    }
                )

                if not created:
                    updated_fields = []
                    if headshot_url and not athlete_entity.logo_url:
                        athlete_entity.logo_url = headshot_url
                        updated_fields.append('logo_url')
                    if ext_id and not athlete_entity.external_id:
                        athlete_entity.external_id = ext_id
                        athlete_entity.api_source = 'thesportsdb'
                        updated_fields.extend(['external_id', 'api_source'])
                    if updated_fields:
                        athlete_entity.save(update_fields=updated_fields)

                # Parse name parts
                name_parts = name.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                # Parse DOB
                dob = None
                dob_str = pdata.get('date_of_birth')
                if dob_str:
                    try:
                        dob = datetime.strptime(dob_str[:10], '%Y-%m-%d').date()
                    except (ValueError, TypeError):
                        dob = None

                # 2. Create or Update Athlete Detail model
                athlete_detail, _ = Athlete.objects.get_or_create(
                    entity=athlete_entity,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'date_of_birth': dob,
                        'nationality': pdata.get('nationality', ''),
                        'position': pdata.get('position', ''),
                        'current_team': team_entity,
                    }
                )
                if athlete_detail.current_team != team_entity or (pdata.get('position') and not athlete_detail.position):
                    athlete_detail.current_team = team_entity
                    if pdata.get('position'):
                        athlete_detail.position = pdata.get('position')
                    athlete_detail.save()

                if created:
                    total_players_created += 1
                else:
                    total_players_updated += 1

            # Respect rate limits — 2.5s delay between teams
            time.sleep(2.5)

        self.stdout.write(self.style.SUCCESS(
            f"\nRoster enrichment completed: {total_players_created} players created, {total_players_updated} updated."
        ))
