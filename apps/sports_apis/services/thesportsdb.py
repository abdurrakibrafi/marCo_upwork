"""
apps/sports_apis/services/thesportsdb.py

TheSportsDB API service — used for:
1. Logo/badge enrichment for teams and leagues missing images
2. YouTube highlights per event
3. Venue photos

Free key: 123
Premium key: set THESPORTSDB_KEY in .env

Docs: https://www.thesportsdb.com/api.php

FIXES vs original:
- get_team_badge: was using 'strTeamBadge' — correct key is 'strBadge'
- search_league: response key is 'countries' not 'countrys', and free API
  rejects league name searches — now fetches all leagues by sport and
  filters locally by name similarity
- get_event_highlights: response key is 'tvhighlights' not 'event',
  and field is 'strVideo' (already correct) but structure is flat not nested
"""

import logging
import time
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Module-level cache for the all-leagues-by-sport response.
# TheSportsDB returns ALL leagues for a sport (~500+ rows) — we cache it
# in memory so the enrich task only fetches it once per process lifetime.
_leagues_cache: dict = {}  # sport_str -> list[dict]


def _name_similarity(a: str, b: str) -> float:
    """Simple word overlap similarity 0.0–1.0."""
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return 0.0
    return len(a_words & b_words) / max(len(a_words), len(b_words))


class TheSportsDBService:

    BASE_URL = "https://www.thesportsdb.com/api/v1/json"

    # Sport name mapping — our internal names → TheSportsDB sport names
    SPORT_MAP = {
        'soccer':     'Soccer',
        'basketball': 'Basketball',
        'cricket':    'Cricket',
        'football':   'American Football',
        'baseball':   'Baseball',
        'hockey':     'Ice Hockey',
    }

    def __init__(self):
        self.api_key = getattr(settings, 'THESPORTSDB_KEY', None) or '3'

    def _get(self, endpoint: str, params: dict = None, timeout: int = 15, max_retries: int = 3) -> dict:
        key = getattr(settings, 'THESPORTSDB_KEY', None) or self.api_key or '3'
        url = f"{self.BASE_URL}/{key}/{endpoint}"

        # Enforce minimum delay between ALL individual HTTP requests to respect rate limit
        time.sleep(1.5)

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, params=params, timeout=timeout, stream=False)
                
                # Detect rate limit via status code or body text
                text = resp.text or ""
                if resp.status_code == 429 or "rate limit" in text.lower() or "too many requests" in text.lower():
                    wait_time = 8 * (attempt + 1)
                    logger.warning(f"TheSportsDB Rate Limit hit ({endpoint}). Cooling down {wait_time}s... (Attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue

                resp.raise_for_status()
                if not text.strip():
                    return {}
                return resp.json()

            except (requests.exceptions.HTTPError, ValueError) as err:
                if attempt < max_retries - 1:
                    wait_time = 8 * (attempt + 1)
                    logger.warning(f"TheSportsDB request issue ({endpoint}): {err}. Cooling down {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                logger.warning(f"TheSportsDB request failed after retries ({endpoint}): {err}")
                return {}
            except Exception as e:
                logger.warning(f"TheSportsDB unexpected error ({endpoint}): {e}")
                return {}
        return {}

    # ── TEAM ────────────────────────────────────────────────────────────

    def search_team(self, team_name: str, sport: str = None) -> dict | None:
        """Search for a team by name and optional sport. Returns best match or None."""
        data = self._get('searchteams.php', {'t': team_name})
        teams = data.get('teams')
        if not teams:
            return None

        if sport:
            target_sport = self.SPORT_MAP.get(sport.lower(), sport).lower()
            for team in teams:
                str_sport = (team.get('strSport') or '').lower()
                if str_sport == target_sport:
                    return team
            # If a specific sport was requested, do not return a team from a different sport
            return None

        return teams[0]

    def get_team_badge(self, team_name: str, sport: str = None) -> str:
        """
        Return badge URL for a team matching sport, or empty string if not found.
        """
        team = self.search_team(team_name, sport=sport)
        if not team:
            return ''
        return team.get('strBadge', '') or team.get('strLogo', '') or ''

    # ── LEAGUE ──────────────────────────────────────────────────────────

    def search_league(self, league_name: str, sport: str = None) -> dict | None:
        """
        Search for a league by name.

        FIX: The free API rejects ?l=<league name> with 'Invalid name passed'
        for most non-exact matches. Instead we fetch all leagues for the sport
        (once, then cache in memory) and filter locally by name similarity.
        """
        sport_str = self.SPORT_MAP.get(sport, '') if sport else ''

        if sport_str:
            # Use module-level cache — the all-leagues list is huge (~500 rows)
            # and slow to fetch. Cache it for the lifetime of the worker process.
            if sport_str not in _leagues_cache:
                logger.info(f"TheSportsDB: fetching all leagues for sport '{sport_str}'")
                data = self._get('search_all_leagues.php', {'s': sport_str}, timeout=30)
                leagues = data.get('countries') or data.get('countrys') or []
                _leagues_cache[sport_str] = leagues if isinstance(leagues, list) else []
                logger.info(f"TheSportsDB: cached {len(_leagues_cache[sport_str])} leagues for {sport_str}")

            leagues = _leagues_cache.get(sport_str, [])
            if not leagues:
                return None

            best_match = None
            best_score = 0.0
            for league in leagues:
                name = league.get('strLeague', '')
                score = _name_similarity(league_name, name)
                if score > best_score:
                    best_score = score
                    best_match = league

            # Require at least 50% word overlap
            if best_match and best_score >= 0.5:
                return best_match
            return None
        else:
            # No sport — try direct name search (may fail for non-exact names)
            data = self._get('search_all_leagues.php', {'l': league_name})
            leagues = data.get('countries') or data.get('countrys') or []
            if not leagues or not isinstance(leagues, list):
                return None
            return leagues[0]

    def get_league_badge(self, league_name: str, sport: str = None) -> str:
        """Return badge/logo URL for a league."""
        league = self.search_league(league_name, sport)
        if not league:
            return ''
        return (
            league.get('strBadge', '')
            or league.get('strLogo', '')
            or league.get('strPoster', '')
            or ''
        )

    # ── EVENT HIGHLIGHTS ────────────────────────────────────────────────

    def get_event_highlights(self, date: str, league_id: str = None) -> list[dict]:
        """
        Get YouTube highlight links for events on a given date.
        date format: YYYY-MM-DD

        FIX: Response key is 'tvhighlights' not 'event'.
        Returns list of {event_name, home_team, away_team, highlight_url, thumbnail}
        """
        params = {'d': date}
        if league_id:
            params['l'] = league_id

        data = self._get('eventshighlights.php', params)
        # FIX: key is 'tvhighlights', not 'event'
        events = data.get('tvhighlights') or []

        results = []
        for ev in events:
            url = ev.get('strVideo') or ev.get('strHighlight') or ''
            if not url:
                continue
            results.append({
                'event_name':    ev.get('strEvent', ''),
                'home_team':     ev.get('strHomeTeam', '') or '',
                'away_team':     ev.get('strAwayTeam', '') or '',
                'highlight_url': url,
                'thumbnail':     ev.get('strThumb', '') or '',
                'sport':         ev.get('strSport', '').lower(),
                'league':        ev.get('strLeague', ''),
                'date':          ev.get('dateEvent', ''),
            })
        return results

    # ── VENUE ────────────────────────────────────────────────────────────

    def search_venue(self, venue_name: str) -> dict | None:
        data = self._get('searchvenues.php', {'v': venue_name})
        venues = data.get('venues')
        if not venues:
            return None
        return venues[0]

    def get_venue_thumb(self, venue_name: str) -> str:
        venue = self.search_venue(venue_name)
        if not venue:
            return ''
        return venue.get('strThumb', '') or venue.get('strFanart1', '') or ''

    # ── SCHEDULE ────────────────────────────────────────────────────────

    def get_events_on_day(self, date: str, sport: str = None, league: str = None) -> list[dict]:
        """
        Get all events on a specific date.
        Useful for enriching our Event model with thumbnails.
        """
        params = {'d': date}
        if sport:
            params['s'] = self.SPORT_MAP.get(sport, sport)
        if league:
            params['l'] = league

        data = self._get('eventsday.php', params)
        return data.get('events') or []

    # ── PLAYER ──────────────────────────────────────────────────────────

    def search_player(self, player_name: str) -> dict | None:
        """Search for a player by name. Returns best match or None."""
        data = self._get('searchplayers.php', {'p': player_name})
        players = data.get('player')
        if not players:
            return None
        return players[0]

    def get_player_headshot(self, player_name: str) -> str:
        """
        Return transparent cutout/headshot URL for a player, or empty string.
        Optimized with query variations and name validation to prevent false matches.
        """
        # Generate variations for initials/spaces
        variations = [
            player_name,
            player_name.replace(". ", "."),
            player_name.replace(".", "").replace(" ", " "),
            player_name.replace(".", "")
        ]
        # De-duplicate variations
        variations = list(dict.fromkeys(variations))

        def is_valid_match(query: str, match: str) -> bool:
            q_norm = query.lower().replace(".", "").strip()
            m_norm = match.lower().replace(".", "").strip()
            q_words = [w for w in q_norm.split() if w]
            m_words = [w for w in m_norm.split() if w]
            for qw in q_words:
                if len(qw) > 2:
                    if qw not in m_words:
                        return False
            return True

        for var in variations:
            player = self.search_player(var)
            if player:
                matched_name = player.get('strPlayer', '')
                if is_valid_match(player_name, matched_name):
                    return player.get('strCutout', '') or player.get('strThumb', '') or ''
        return ''

    def get_team_roster(self, team_name: str = None, team_id: str = None) -> list[dict]:
        """
        Get all players for a team by team name or team ID.
        Returns list of player dictionaries containing name, position, headshot, DOB, nationality, height, weight.
        """
        players = []
        target_id = team_id

        if not target_id and team_name:
            team_info = self.search_team(team_name)
            if team_info:
                target_id = team_info.get('idTeam')

        if target_id:
            data = self._get('lookup_all_players.php', {'id': target_id})
            players = data.get('player') or []

        if not players and team_name:
            data = self._get('searchplayers.php', {'t': team_name})
            players = data.get('player') or []

        results = []
        for p in players:
            name = p.get('strPlayer', '')
            if not name:
                continue
            results.append({
                'id_player': p.get('idPlayer', ''),
                'name': name,
                'position': p.get('strPosition', '') or '',
                'headshot_url': p.get('strCutout', '') or p.get('strThumb', '') or '',
                'date_of_birth': p.get('dateBorn', '') or '',
                'nationality': p.get('strNationality', '') or '',
                'height': p.get('strHeight', '') or '',
                'weight': p.get('strWeight', '') or '',
                'description': p.get('strDescriptionEN', '') or '',
                'team_name': p.get('strTeam', '') or team_name,
                'sport': (p.get('strSport', '') or '').lower(),
                'raw_data': p,
            })
        return results

    def get_player_details(self, player_name: str, player_id: str = None) -> dict | None:
        """
        Get detailed profile for a player.
        """
        player = None
        if player_id:
            data = self._get('lookupplayer.php', {'id': player_id})
            players = data.get('players') or data.get('player') or []
            if players:
                player = players[0]
        if not player and player_name:
            player = self.search_player(player_name)
        if not player:
            return None

        return {
            'id_player': player.get('idPlayer', ''),
            'name': player.get('strPlayer', ''),
            'position': player.get('strPosition', '') or '',
            'headshot_url': player.get('strCutout', '') or player.get('strThumb', '') or '',
            'date_of_birth': player.get('dateBorn', '') or '',
            'nationality': player.get('strNationality', '') or '',
            'height': player.get('strHeight', '') or '',
            'weight': player.get('strWeight', '') or '',
            'description': player.get('strDescriptionEN', '') or '',
            'team_name': player.get('strTeam', '') or '',
            'sport': (player.get('strSport', '') or '').lower(),
            'raw_data': player,
        }


# Global instance
thesportsdb_service = TheSportsDBService()