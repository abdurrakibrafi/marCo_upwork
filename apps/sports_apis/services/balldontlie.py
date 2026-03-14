from django.conf import settings
from .base import BaseAPIService


class BallDontLieService(BaseAPIService):
    """Service for BallDontLie API (NBA, NFL, MLB, NHL)"""

    # BUG FIX: NBA uses /v1/ not /nba/v1/
    # NFL/MLB/NHL use their sport prefix. Only NBA is at the root /v1/.
    BASE_URLS = {
        'nba': 'https://api.balldontlie.io/v1',       # was wrong: /nba/v1
        'nfl': 'https://api.balldontlie.io/nfl/v1',
        'mlb': 'https://api.balldontlie.io/mlb/v1',
        'nhl': 'https://api.balldontlie.io/nhl/v1',
    }

    def __init__(self):
        super().__init__(settings.BALLDONTLIE_KEY)

    def _headers(self):
        return {'Authorization': self.api_key}

    def get_live_games(self, sport: str):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        if sport == 'nba':
            url = f"{self.BASE_URLS[sport]}/box_scores/live"
        else:
            url = f"{self.BASE_URLS[sport]}/games"

        return self.fetch(url, headers=self._headers())

    def get_games_by_date(self, sport: str, date: str):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        url = f"{self.BASE_URLS[sport]}/games"
        params = {'dates[]': date}

        return self.fetch(url, params=params, headers=self._headers())

    def get_teams(self, sport: str):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        url = f"{self.BASE_URLS[sport]}/teams"

        return self.fetch(url, headers=self._headers())

    def get_standings(self, sport: str, season: int = None):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        url = f"{self.BASE_URLS[sport]}/standings"
        params = {}
        if season:
            params['season'] = season

        return self.fetch(url, params=params, headers=self._headers())

    def get_players(self, sport: str, team_id: int = None):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        url = f"{self.BASE_URLS[sport]}/players"
        params = {}
        if team_id:
            params['team_ids[]'] = team_id

        return self.fetch(url, params=params, headers=self._headers())

    def get_player_season_averages(self, sport: str, season: int, player_id: int):
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}

        url = f"{self.BASE_URLS[sport]}/season_averages"
        params = {'season': season, 'player_ids[]': player_id}

        return self.fetch(url, params=params, headers=self._headers())


# Global instance
balldontlie_service = BallDontLieService()