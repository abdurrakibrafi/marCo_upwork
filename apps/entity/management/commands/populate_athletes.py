from django.core.management.base import BaseCommand
from apps.entity.models import Entity, Team, Athlete
from apps.sports_apis.services.statpal import statpal_service
import logging
import re

logger = logging.getLogger(__name__)

# NBA Team Abbreviations for StatPal
NBA_ABBR_MAP = {
    'Atlanta Hawks': 'atl',
    'Boston Celtics': 'bos',
    'Brooklyn Nets': 'bkn',
    'Charlotte Hornets': 'cha',
    'Chicago Bulls': 'chi',
    'Cleveland Cavaliers': 'cle',
    'Dallas Mavericks': 'dal',
    'Denver Nuggets': 'den',
    'Detroit Pistons': 'det',
    'Golden State Warriors': 'gsw',
    'Houston Rockets': 'hou',
    'Indiana Pacers': 'ind',
    'Los Angeles Clippers': 'lac',
    'Los Angeles Lakers': 'lal',
    'Memphis Grizzlies': 'mem',
    'Miami Heat': 'mia',
    'Milwaukee Bucks': 'mil',
    'Minnesota Timberwolves': 'min',
    'New Orleans Pelicans': 'nop',
    'New York Knicks': 'nyk',
    'Oklahoma City Thunder': 'okc',
    'Orlando Magic': 'orl',
    'Philadelphia 76ers': 'phi',
    'Phoenix Suns': 'phx',
    'Portland Trail Blazers': 'por',
    'Sacramento Kings': 'sac',
    'San Antonio Spurs': 'sas',
    'Toronto Raptors': 'tor',
    'Utah Jazz': 'uta',
    'Washington Wizards': 'was',
}

def parse_nba_height(h_str):
    if not h_str or h_str == '--':
        return None
    try:
        parts = h_str.replace('"', '').split("'")
        feet = int(parts[0].strip())
        inches = int(parts[1].strip()) if len(parts) > 1 else 0
        return int(feet * 30.48 + inches * 2.54)
    except Exception:
        return None

def parse_nba_weight(w_str):
    if not w_str or w_str == '--':
        return None
    try:
        val = int(w_str.replace('lbs', '').strip())
        return int(val * 0.45359237)
    except Exception:
        return None

class Command(BaseCommand):
    help = 'Populate athletes/players for major seeded soccer and NBA teams'

    def handle(self, *args, **options):
        # 1. NBA Teams
        nba_teams = Entity.objects.filter(sport='basketball', type='team', api_source='statpal')
        self.stdout.write(f"Found {nba_teams.count()} NBA teams in database.")
        
        for team in nba_teams:
            abbr = NBA_ABBR_MAP.get(team.name)
            if not abbr:
                self.stdout.write(self.style.WARNING(f"Abbreviation not found for NBA team: {team.name}"))
                continue
                
            # Check if already has roster
            has_players = Athlete.objects.filter(current_team=team).exists()
            if has_players:
                self.stdout.write(f"Roster for {team.name} already seeded. Skipping.")
                continue

            self.stdout.write(f"Fetching roster for NBA team: {team.name} ({abbr})...")
            r = statpal_service.get_nba_roster(abbr)
            if not r.get('success'):
                self.stdout.write(self.style.WARNING(f"Failed to fetch roster for {team.name}: {r.get('error')}"))
                continue

            team_data = r.get('data', {}).get('team') or {}
            players = team_data.get('player', [])
            if not isinstance(players, list):
                players = []
            self.stdout.write(f"Seeding {len(players)} players for {team.name}...")
            
            for p in players:
                pid = p.get('id')
                pname = p.get('name')
                if pid and pname:
                    # Create Player Entity
                    player_entity, _ = Entity.objects.get_or_create(
                        api_source='statpal',
                        external_id=str(pid),
                        defaults={
                            'type': 'athlete',
                            'name': pname,
                            'sport': 'basketball',
                            'logo_url': f"https://statpal.io/api/v2/nba/images?type=player&id={pid}&access_key={statpal_service.access_key}"
                        }
                    )
                    
                    # Split name safely into first/last name
                    name_parts = pname.split(maxsplit=1)
                    first_name = name_parts[0]
                    last_name = name_parts[1] if len(name_parts) > 1 else ''
                    
                    # Create Athlete Details
                    Athlete.objects.get_or_create(
                        entity=player_entity,
                        defaults={
                            'first_name': first_name,
                            'last_name': last_name,
                            'current_team': team,
                            'position': p.get('position', ''),
                            'jersey_number': int(p['number']) if p.get('number') and p['number'].isdigit() else None,
                            'height_cm': parse_nba_height(p.get('heigth')),
                            'weight_kg': parse_nba_weight(p.get('weigth')),
                        }
                    )

        # 2. Soccer Teams
        # For soccer, we have a lot of teams. We will seed rosters for teams belonging to major leagues
        # (Premier League, La Liga, Serie A, Bundesliga, Ligue 1)
        major_soccer_leagues = Entity.objects.filter(
            sport='soccer',
            type='league',
            name__in=['Premier League', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1']
        )
        
        soccer_teams = Entity.objects.filter(
            sport='soccer',
            type='team',
            api_source='statpal',
            team_details__league__in=major_soccer_leagues
        ).distinct()
        
        self.stdout.write(f"Found {soccer_teams.count()} soccer teams in major leagues.")
        
        for team in soccer_teams:
            has_players = Athlete.objects.filter(current_team=team).exists()
            if has_players:
                self.stdout.write(f"Roster for soccer team {team.name} already seeded. Skipping.")
                continue
                
            self.stdout.write(f"Fetching roster for soccer team: {team.name}...")
            r = statpal_service.get_soccer_team(team.external_id)
            if not r.get('success'):
                self.stdout.write(self.style.WARNING(f"Failed to fetch roster for {team.name}: {r.get('error')}"))
                continue
                
            team_data = r.get('data', {}).get('team') or {}
            squad_data = team_data.get('squad') or {}
            players = squad_data.get('player', [])
            if not isinstance(players, list):
                players = []
                
            if not players:
                # Some leagues/teams might have empty squad in StatPal response
                self.stdout.write(f"No squad data found for {team.name}.")
                continue
                
            self.stdout.write(f"Seeding {len(players)} players for {team.name}...")
            
            for p in players:
                pid = p.get('id')
                pname = p.get('name')
                if pid and pname:
                    # Create Player Entity
                    player_entity, _ = Entity.objects.get_or_create(
                        api_source='statpal',
                        external_id=str(pid),
                        defaults={
                            'type': 'athlete',
                            'name': pname,
                            'sport': 'soccer',
                            'logo_url': f"https://statpal.io/api/v2/soccer/images?type=player&id={pid}&access_key={statpal_service.access_key}"
                        }
                    )
                    
                    name_parts = pname.split(maxsplit=1)
                    first_name = name_parts[0]
                    last_name = name_parts[1] if len(name_parts) > 1 else ''
                    
                    # Create Athlete Details
                    Athlete.objects.get_or_create(
                        entity=player_entity,
                        defaults={
                            'first_name': first_name,
                            'last_name': last_name,
                            'current_team': team,
                            'position': p.get('position', ''),
                            'jersey_number': int(p['number']) if p.get('number') and p['number'].isdigit() else None,
                        }
                    )
                    
        self.stdout.write(self.style.SUCCESS("All major athletes populated successfully!"))
