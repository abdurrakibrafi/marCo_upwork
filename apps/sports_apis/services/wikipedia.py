import re
import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

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


class WikipediaService:
    """Service to search and scrape team rosters / squads from Wikipedia as fallback."""

    def get_team_roster(self, team_name: str, sport: str = 'soccer') -> list[dict]:
        """
        Search Wikipedia for the team and extract squad/roster.
        Returns a list of standardized player dictionaries compatible with TheSportsDBService output.
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
                    pos_candidate = cell_texts[1].upper()
                    # Format: [Jersey No, Pos, Nation, Name]
                    if len(cell_texts) >= 4 and pos_candidate in POS_MAP:
                        jersey = cell_texts[0]
                        nat = cell_texts[2]
                        name = clean_player_name(cell_texts[3])
                        
                        if name and name not in seen_names and len(name) > 2 and not name.lower().startswith('player'):
                            seen_names.add(name)
                            players.append({
                                'id_player': f"wiki_{name.replace(' ', '_').lower()}",
                                'name': name,
                                'jersey_number': int(jersey) if jersey.isdigit() else None,
                                'position': POS_MAP.get(pos_candidate, pos_candidate.capitalize()),
                                'headshot_url': '',
                                'date_of_birth': '',
                                'nationality': nat.replace('\xa0', '').strip(),
                                'sport': sport,
                                'source': 'wikipedia',
                            })
                            
                    # Format: [Pos, Nation, Name]
                    elif pos_candidate in POS_MAP:
                        nat = cell_texts[1] if len(cell_texts) > 2 else ''
                        name = clean_player_name(cell_texts[2] if len(cell_texts) > 2 else cell_texts[1])
                        
                        if name and name not in seen_names and len(name) > 2 and not name.lower().startswith('player'):
                            seen_names.add(name)
                            players.append({
                                'id_player': f"wiki_{name.replace(' ', '_').lower()}",
                                'name': name,
                                'jersey_number': None,
                                'position': POS_MAP.get(pos_candidate, pos_candidate.capitalize()),
                                'headshot_url': '',
                                'date_of_birth': '',
                                'nationality': nat.replace('\xa0', '').strip(),
                                'sport': sport,
                                'source': 'wikipedia',
                            })

            return players
            
        except Exception as e:
            logger.warning(f"Wikipedia roster fetch error for {team_name}: {e}")
            return []


wikipedia_service = WikipediaService()
