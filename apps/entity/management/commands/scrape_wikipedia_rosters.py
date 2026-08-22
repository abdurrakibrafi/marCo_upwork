import time
import re
import requests
from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db.models import Count
from apps.entity.models import Entity, Athlete

HEADERS = {
    'User-Agent': 'SportsNestBot/1.0 (https://mysportsnest.com; contact@mysportsnest.com)'
}

POS_MAP = {
    'GK': 'Goalkeeper', 'DF': 'Defender', 'MF': 'Midfielder', 'FW': 'Forward',
    'G': 'Guard', 'F': 'Forward', 'C': 'Center', 'PG': 'Point Guard',
    'SG': 'Shooting Guard', 'SF': 'Small Forward', 'PF': 'Power Forward',
    'GOALKEEPER': 'Goalkeeper', 'DEFENDER': 'Defender', 'MIDFIELDER': 'Midfielder', 'FORWARD': 'Forward',
    'GUARD': 'Guard', 'CENTER': 'Center', 'BATSMAN': 'Batsman', 'BOWLER': 'Bowler', 'ALL-ROUNDER': 'All-Rounder',
    'WICKET-KEEPER': 'Wicket-Keeper'
}

def clean_player_name(raw_name: str) -> str:
    """Removes citations like [1], [208] and parenthetical annotations like (captain), (loan)"""
    cleaned = re.sub(r'\[.*?\]', '', raw_name)
    cleaned = re.sub(r'\(.*?\)', '', cleaned)
    cleaned = cleaned.replace('\xa0', ' ').strip()
    return cleaned

def fetch_wikipedia_squad(team_name: str, sport: str = 'soccer') -> list:
    """
    Searches Wikipedia for the team and extracts the current squad/roster.
    Returns a list of dicts: [{'name': ..., 'jersey_number': ..., 'position': ..., 'nationality': ...}]
    """
    search_url = 'https://en.wikipedia.org/w/api.php'
    query = f"{team_name} football club" if sport == 'soccer' else f"{team_name} {sport}"
    
    try:
        r = requests.get(
            search_url,
            params={'action': 'query', 'list': 'search', 'srsearch': query, 'format': 'json', 'utf8': 1},
            headers=HEADERS,
            timeout=10
        )
        data = r.json()
        results = data.get('query', {}).get('search', [])
        
        if not results:
            r = requests.get(
                search_url,
                params={'action': 'query', 'list': 'search', 'srsearch': team_name, 'format': 'json', 'utf8': 1},
                headers=HEADERS,
                timeout=10
            )
            results = r.json().get('query', {}).get('search', [])
            
        if not results:
            return []
            
        page_title = results[0]['title']
        
        # Fetch Page HTML
        url = f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"
        r_page = requests.get(url, headers=HEADERS, timeout=12)
        if r_page.status_code != 200:
            return []
            
        soup = BeautifulSoup(r_page.text, 'html.parser')
        players = []
        seen_names = set()

        for row in soup.find_all('tr'):
            cells = row.find_all(['td', 'th'])
            cell_texts = [c.get_text().strip() for c in cells]
            
            if len(cell_texts) >= 3:
                # Pattern 1: [Jersey No, Pos, Nation, Name]
                pos_candidate = cell_texts[1].upper()
                if len(cell_texts) >= 4 and pos_candidate in POS_MAP:
                    jersey = cell_texts[0]
                    nat = cell_texts[2]
                    name = clean_player_name(cell_texts[3])
                    
                    if name and name not in seen_names and len(name) > 2 and not name.lower().startswith('player'):
                        seen_names.add(name)
                        players.append({
                            'name': name,
                            'jersey_number': int(jersey) if jersey.isdigit() else None,
                            'position': POS_MAP.get(pos_candidate, pos_candidate.capitalize()),
                            'nationality': nat.replace('\xa0', '').strip(),
                        })
                        
                # Pattern 2: [Pos, Nation, Name]
                elif pos_candidate in POS_MAP:
                    nat = cell_texts[1] if len(cell_texts) > 2 else ''
                    name = clean_player_name(cell_texts[2] if len(cell_texts) > 2 else cell_texts[1])
                    
                    if name and name not in seen_names and len(name) > 2 and not name.lower().startswith('player'):
                        seen_names.add(name)
                        players.append({
                            'name': name,
                            'jersey_number': None,
                            'position': POS_MAP.get(pos_candidate, pos_candidate.capitalize()),
                            'nationality': nat.replace('\xa0', '').strip(),
                        })

        return players
        
    except Exception as e:
        return []


class Command(BaseCommand):
    help = 'Scrape team rosters from Wikipedia for teams with missing roster data'

    def add_arguments(self, parser):
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
            '--all-teams',
            action='store_true',
            help='Process all teams (default is only teams without any roster)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        sport = options['sport']
        team_name = options['team_name']
        all_teams = options['all_teams']

        teams = Entity.objects.filter(type='team')
        
        if not all_teams and not team_name:
            # Only pick teams that currently have 0 athletes
            teams = teams.annotate(num_athletes=Count('current_athletes')).filter(num_athletes=0)
            self.stdout.write(self.style.NOTICE("Targeting teams with 0 existing athletes..."))
            
        if sport:
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
            self.stdout.write(f"\n[{idx}/{limit or total_count}] Scraping Wikipedia for [{team.sport.upper()}] {team.name}...")
            
            players = fetch_wikipedia_squad(team.name, team.sport)
            
            if not players:
                self.stdout.write(self.style.WARNING(f"✗ No squad table found on Wikipedia for {team.name}"))
                time.sleep(0.5)
                continue

            self.stdout.write(self.style.SUCCESS(f"✓ Found {len(players)} players for {team.name}! Saving to DB..."))
            
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
                    was_created_entity = True
                else:
                    was_created_entity = False

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

            total_teams_enriched += 1
            # Polite delay between requests
            time.sleep(0.8)

        self.stdout.write(self.style.SUCCESS(
            f"\n=== Wikipedia Scraper Complete ==="
            f"\nTotal Teams Enriched: {total_teams_enriched}"
            f"\nTotal Players Saved: {total_players_created}"
        ))
