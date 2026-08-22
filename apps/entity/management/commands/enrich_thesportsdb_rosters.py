import time
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db.models import Count
from apps.entity.models import Entity, Athlete, Team
from apps.sports_apis.services.thesportsdb import thesportsdb_service

INDIVIDUAL_SPORTS = ['tennis', 'golf', 'mma', 'boxing', 'combat_sports', 'motorsport', 'formula1', 'f1']

class Command(BaseCommand):
    help = 'Enrich team rosters and seed athlete profiles from TheSportsDB API and Wikipedia fallback'

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
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-checking teams even if already checked or has existing roster'
        )
        parser.add_argument(
            '--include-individual',
            action='store_true',
            help='Include individual sports (tennis, golf, etc.) which usually have no team rosters'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        sport = options['sport']
        team_name_filter = options['team_name']
        force = options['force']
        include_individual = options['include_individual']

        self.stdout.write("Finding team entities to enrich rosters...")
        
        # 1. Base Query
        teams = Entity.objects.filter(type='team')
        
        # Exclude empty or whitespace-only names
        teams = teams.exclude(name__isnull=True).exclude(name__exact='').exclude(name__regex=r'^\s*$')

        # Exclude individual sports by default (e.g. tennis, golf) unless requested
        if not include_individual and not sport:
            teams = teams.exclude(sport__in=INDIVIDUAL_SPORTS)

        if sport:
            teams = teams.filter(sport=sport.lower())
        if team_name_filter:
            teams = teams.filter(name__icontains=team_name_filter)

        # Exclude already checked teams unless --force
        if not force and not team_name_filter:
            # Exclude teams where metadata has roster_checked=True
            teams = teams.exclude(metadata__roster_checked=True)

        teams = teams.order_by('name')
        total_count = teams.count()
        self.stdout.write(f"Found {total_count} teams to process (excluding already checked/invalid).")

        if limit:
            teams = teams[:limit]
            self.stdout.write(f"Limiting to first {limit} teams.")

        total_players_created = 0
        total_players_updated = 0
        teams_enriched_count = 0
        teams_skipped_count = 0

        for idx, team_entity in enumerate(teams, start=1):
            team_clean_name = team_entity.name.strip()
            if not team_clean_name:
                continue

            # Check if team already has sufficient roster in DB (>= 10 athletes)
            existing_count = Athlete.objects.filter(current_team=team_entity).count()
            if not force and existing_count >= 10:
                self.stdout.write(f"[{idx}/{limit or total_count}] {team_entity.name} already has {existing_count} players. Skipping.")
                if not team_entity.metadata.get('roster_checked'):
                    team_entity.metadata['roster_checked'] = True
                    team_entity.metadata['roster_found'] = True
                    team_entity.save(update_fields=['metadata'])
                teams_skipped_count += 1
                continue

            self.stdout.write(f"\n[{idx}/{limit or total_count}] Fetching roster for [{team_entity.sport.upper()}] {team_clean_name}...")
            
            # Step 1: Try TheSportsDB
            roster = []
            try:
                roster = thesportsdb_service.get_team_roster(team_name=team_clean_name)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error calling TheSportsDB for {team_clean_name}: {e}"))

            # Step 2: Wikipedia Fallback if empty or insufficient players (< 8)
            if not roster or len(roster) < 8:
                if not roster:
                    self.stdout.write(self.style.NOTICE(f"✗ No roster found on TheSportsDB for {team_clean_name}. Trying Wikipedia fallback..."))
                else:
                    self.stdout.write(self.style.NOTICE(f"⚠ TheSportsDB only found {len(roster)} players for {team_clean_name}. Trying Wikipedia fallback for full squad..."))
                
                try:
                    from apps.sports_apis.services.wikipedia import wikipedia_service
                    wiki_roster = wikipedia_service.get_team_roster(team_name=team_clean_name, sport=team_entity.sport)
                    if wiki_roster and len(wiki_roster) > len(roster):
                        self.stdout.write(self.style.SUCCESS(f"✓ Wikipedia fallback found {len(wiki_roster)} players!"))
                        roster = wiki_roster
                except Exception as wiki_err:
                    self.stdout.write(self.style.WARNING(f"Wikipedia fallback error: {wiki_err}"))

            # Step 3: Record metadata so we never repeat failed attempts
            if not roster:
                self.stdout.write(self.style.NOTICE(f"✗ No roster found on any source for {team_clean_name} (Marked as checked)"))
                team_entity.metadata['roster_checked'] = True
                team_entity.metadata['roster_found'] = False
                team_entity.save(update_fields=['metadata'])
                time.sleep(0.5)
                continue

            self.stdout.write(self.style.SUCCESS(f"✓ Saving {len(roster)} players on roster for {team_clean_name}..."))

            for pdata in roster:
                name = pdata.get('name')
                if not name:
                    continue

                player_sport = pdata.get('sport') or team_entity.sport
                headshot_url = pdata.get('headshot_url') or ''
                ext_id = str(pdata.get('id_player') or '')

                # 1. Create or Update Entity
                source = pdata.get('source') or 'thesportsdb'
                athlete_entity = Entity.objects.filter(
                    name=name,
                    type='athlete',
                    sport=player_sport
                ).first()

                created = False
                if not athlete_entity:
                    athlete_entity = Entity.objects.create(
                        name=name,
                        type='athlete',
                        sport=player_sport,
                        api_source=source,
                        external_id=ext_id,
                        logo_url=headshot_url,
                        description=pdata.get('description', '')[:500],
                        country=pdata.get('nationality', ''),
                        has_api_data=True,
                    )
                    created = True
                else:
                    updated_fields = []
                    if headshot_url and not athlete_entity.logo_url:
                        athlete_entity.logo_url = headshot_url
                        updated_fields.append('logo_url')
                    if ext_id and not athlete_entity.external_id:
                        athlete_entity.external_id = ext_id
                        athlete_entity.api_source = source
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
                athlete_detail = Athlete.objects.filter(entity=athlete_entity).first()
                if not athlete_detail:
                    athlete_detail = Athlete.objects.create(
                        entity=athlete_entity,
                        first_name=first_name,
                        last_name=last_name,
                        date_of_birth=dob,
                        nationality=pdata.get('nationality', ''),
                        position=pdata.get('position', ''),
                        jersey_number=pdata.get('jersey_number'),
                        current_team=team_entity,
                    )
                if athlete_detail.current_team != team_entity or (pdata.get('position') and not athlete_detail.position):
                    athlete_detail.current_team = team_entity
                    if pdata.get('position'):
                        athlete_detail.position = pdata.get('position')
                    if pdata.get('jersey_number'):
                        athlete_detail.jersey_number = pdata.get('jersey_number')
                    athlete_detail.save()

                if created:
                    total_players_created += 1
                else:
                    total_players_updated += 1

            # Mark team as checked & found
            team_entity.metadata['roster_checked'] = True
            team_entity.metadata['roster_found'] = True
            team_entity.save(update_fields=['metadata'])
            teams_enriched_count += 1

            # Respect rate limits
            time.sleep(1.0)

        self.stdout.write(self.style.SUCCESS(
            f"\n=== Roster enrichment completed ==="
            f"\nTeams Enriched: {teams_enriched_count}"
            f"\nPlayers Created: {total_players_created}"
            f"\nPlayers Updated: {total_players_updated}"
        ))
