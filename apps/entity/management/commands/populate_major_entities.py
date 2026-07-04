from django.core.management.base import BaseCommand
from apps.entity.utils.matcher import get_or_create_precise_entity
from apps.entity.models import Entity, Team, League
from apps.sports_apis.services.statpal import statpal_service
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Populate major sports entities (leagues and teams) from StatPal API'

    def handle(self, *args, **options):
        # 1. UEFA Champions League (special cup)
        self.stdout.write("Seeding UEFA Champions League...")
        ucl_league = get_or_create_precise_entity('2838', 'UEFA Champions League', 'soccer', 'league')
        League.objects.get_or_create(entity=ucl_league)

        # 2. Major Soccer Leagues (via Standings)
        soccer_leagues = [
            {'id': '3037', 'name': 'Premier League', 'sport': 'soccer'},
            {'id': '3102', 'name': 'Serie A', 'sport': 'soccer'},
            {'id': '3232', 'name': 'La Liga', 'sport': 'soccer'}, # Spain Primera
            {'id': '3062', 'name': 'Bundesliga', 'sport': 'soccer'},
            {'id': '3054', 'name': 'Ligue 1', 'sport': 'soccer'},
            {'id': '3273', 'name': 'MLS', 'sport': 'soccer'},
            {'id': '3201', 'name': 'Saudi Professional League', 'sport': 'soccer'},
        ]

        for sl in soccer_leagues:
            self.stdout.write(f"Populating soccer league: {sl['name']} (ID: {sl['id']})...")
            # Create/get League Entity
            league_entity = get_or_create_precise_entity(sl['id'], sl['name'], sl['sport'], 'league')
            League.objects.get_or_create(entity=league_entity)

            # Fetch standings to get teams
            r = statpal_service.get_soccer_standings(sl['id'])
            if not r.get('success'):
                self.stdout.write(self.style.WARNING(f"Failed to fetch standings for {sl['name']}: {r.get('error')}"))
                continue

            tournaments = r.get('data', {}).get('standings', {}).get('tournament', [])
            if isinstance(tournaments, dict):
                tournaments = [tournaments]
            elif not isinstance(tournaments, list):
                tournaments = []

            for tour in tournaments:
                teams = tour.get('team', [])
                if isinstance(teams, dict):
                    teams = [teams]
                elif not isinstance(teams, list):
                    teams = []

                self.stdout.write(f"Found {len(teams)} teams in {sl['name']} (tournament: {tour.get('name', 'Main')}). Populating...")

                TEAM_NAME_MAP = {
                    'Manchester Utd': 'Manchester United',
                    'PSG': 'Paris Saint-Germain (PSG)',
                    'Inter': 'Inter Milan',
                }

                for t in teams:
                    tid = t.get('id')
                    tname = t.get('name')
                    if tid and tname:
                        tname = TEAM_NAME_MAP.get(tname, tname)
                        team_entity = get_or_create_precise_entity(tid, tname, sl['sport'], 'team')
                        # Link team to league
                        team_obj, _ = Team.objects.get_or_create(entity=team_entity)
                        team_obj.league = league_entity
                        
                        # Update team standings/stats from the response if available
                        overall = t.get('overall', {})
                        try:
                            team_obj.total_wins = int(overall.get('wins', 0))
                            team_obj.total_losses = int(overall.get('losses', 0))
                            played = int(overall.get('games_played', 0))
                            if played > 0:
                                team_obj.win_percentage = round((team_obj.total_wins / played) * 100, 2)
                        except Exception:
                            pass
                        team_obj.save()

            self.stdout.write(self.style.SUCCESS(f"Successfully populated {sl['name']}"))

        # 3. NBA (Basketball)
        self.stdout.write("Populating basketball league: NBA...")
        nba_league = get_or_create_precise_entity('nba', 'NBA', 'basketball', 'league')
        League.objects.get_or_create(entity=nba_league)

        r = statpal_service.get_nba_standings()
        if not r.get('success'):
            self.stdout.write(self.style.WARNING(f"Failed to fetch NBA standings: {r.get('error')}"))
        else:
            conferences = r.get('data', {}).get('standings', {}).get('tournament', {}).get('league', [])
            total_nba_teams = 0
            for conf in conferences:
                for div in conf.get('division', []):
                    for t in div.get('team', []):
                        tid = t.get('id')
                        tname = t.get('name')
                        if tid and tname:
                            team_entity = get_or_create_precise_entity(tid, tname, 'basketball', 'team')
                            team_obj, _ = Team.objects.get_or_create(entity=team_entity)
                            team_obj.league = nba_league
                            
                            try:
                                team_obj.total_wins = int(t.get('won', 0))
                                team_obj.total_losses = int(t.get('lost', 0))
                                total = team_obj.total_wins + team_obj.total_losses
                                if total > 0:
                                    team_obj.win_percentage = round((team_obj.total_wins / total) * 100, 2)
                            except Exception:
                                pass
                            team_obj.save()
                            total_nba_teams += 1

            self.stdout.write(self.style.SUCCESS(f"Successfully populated NBA with {total_nba_teams} teams"))

        # 4. Cricket Leagues
        cricket_leagues = [
            {'id': '1077', 'name': 'County Championship Division One', 'sport': 'cricket'},
            {'id': '1078', 'name': 'County Championship Division Two', 'sport': 'cricket'},
        ]
        for cl in cricket_leagues:
            self.stdout.write(f"Populating cricket league: {cl['name']} (ID: {cl['id']})...")
            league_entity = get_or_create_precise_entity(cl['id'], cl['name'], cl['sport'], 'league')
            League.objects.get_or_create(entity=league_entity)

        self.stdout.write(self.style.SUCCESS("All major entities populated successfully!"))
