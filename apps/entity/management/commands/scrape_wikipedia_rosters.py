import time
import re
import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db.models import Count
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.wikipedia import wikipedia_service

INDIVIDUAL_SPORTS = ['tennis', 'golf', 'mma', 'boxing', 'combat_sports', 'motorsport', 'formula1', 'f1']

class Command(BaseCommand):
    """Management command to scrape squad rosters from Wikipedia pages for teams lacking API data."""
    help = 'Scrape team rosters from Wikipedia for teams with missing roster data'

    def add_arguments(self, parser):
        """Configure command line arguments including sport, team name, and force flags."""
        parser.add_argument(
            '--limit',
            type=int,
            default=None,
            help='Limit the number of teams to process'
        )
        parser.add_argument(
            '--sport',
            type=str,
            default=None,
            help='Filter by sport (e.g. soccer, basketball, cricket)'
        )
        parser.add_argument(
            '--team-name',
            type=str,
            default=None,
            help='Scrape roster for a specific team by name'
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Re-scrape even if team was already checked or has players'
        )

    def handle(self, *args, **options):
        """Execute Wikipedia squad roster scraping and player creation."""
        limit = options['limit']
        sport = options['sport']
        team_name = options['team_name']
        force = options['force']

        # 1. Base Query
        teams = Entity.objects.filter(type='team').exclude(name='').exclude(name__isnull=True)
        
        # Exclude individual sports
        if not sport:
            teams = teams.exclude(sport__in=INDIVIDUAL_SPORTS)
        else:
            teams = teams.filter(sport=sport.lower())

        if team_name:
            teams = teams.filter(name__icontains=team_name)

        teams = teams.order_by('name')
        total_count = teams.count()
        self.stdout.write(f"Found {total_count} matching teams to scrape from Wikipedia.")

        if limit:
            teams = teams[:limit]
            self.stdout.write(f"Limiting to first {limit} teams.")

        total_players_created = 0
        total_teams_enriched = 0

        for idx, team in enumerate(teams, start=1):
            team_clean_name = team.name.strip()
            if not team_clean_name:
                continue

            # Skip if already checked in previous runs
            if not force and not team_name and team.metadata.get('roster_checked') is True:
                continue

            self.stdout.write(f"\n[{idx}/{limit or total_count}] Scraping Wikipedia for [{team.sport.upper()}] {team_clean_name}...")
            
            players = wikipedia_service.get_team_roster(team_clean_name, team.sport)
            
            if not players:
                self.stdout.write(self.style.WARNING(f"✗ No squad table found on Wikipedia for {team_clean_name} (Marked as checked)"))
                team.metadata['roster_checked'] = True
                team.metadata['roster_found'] = False
                team.save(update_fields=['metadata'])
                time.sleep(0.5)
                continue

            self.stdout.write(self.style.SUCCESS(f"✓ Found {len(players)} players for {team_clean_name}! Saving to DB..."))
            
            for pdata in players:
                name = pdata['name']
                player_sport = team.sport
                ext_id = f"wiki_{name.replace(' ', '_').lower()}_{team.id}"
                
                # 1. Create / Update Entity
                athlete_entity = Entity.objects.filter(
                    name=name,
                    type='athlete',
                    sport=player_sport
                ).first()

                if not athlete_entity:
                    athlete_entity = Entity.objects.create(
                        name=name,
                        type='athlete',
                        sport=player_sport,
                        api_source='wikipedia',
                        external_id=ext_id,
                        country=pdata.get('nationality', '') or team.country or '',
                        has_api_data=True,
                    )

                # 2. Split names
                name_parts = name.split(' ', 1)
                first_name = name_parts[0]
                last_name = name_parts[1] if len(name_parts) > 1 else ''

                # 3. Create / Update Athlete Model
                athlete_obj = Athlete.objects.filter(entity=athlete_entity).first()
                if not athlete_obj:
                    athlete_obj = Athlete.objects.create(
                        entity=athlete_entity,
                        first_name=first_name,
                        last_name=last_name,
                        current_team=team,
                        position=pdata.get('position', ''),
                        jersey_number=pdata.get('jersey_number'),
                        nationality=pdata.get('nationality', '') or team.country or '',
                    )
                else:
                    if athlete_obj.current_team != team or not athlete_obj.position:
                        athlete_obj.current_team = team
                        if pdata.get('position'):
                            athlete_obj.position = pdata.get('position')
                        if pdata.get('jersey_number'):
                            athlete_obj.jersey_number = pdata.get('jersey_number')
                        athlete_obj.save()

                total_players_created += 1

            team.metadata['roster_checked'] = True
            team.metadata['roster_found'] = True
            team.save(update_fields=['metadata'])
            total_teams_enriched += 1

            time.sleep(0.8)

        self.stdout.write(self.style.SUCCESS(
            f"\n=== Wikipedia Scraper Complete ==="
            f"\nTotal Teams Enriched: {total_teams_enriched}"
            f"\nTotal Players Saved: {total_players_created}"
        ))
