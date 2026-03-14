from django.conf import settings
from .base import BaseAPIService


class APICricketService(BaseAPIService):
    """Service for API-Cricket (allsportsapi)"""

    # BUG FIX: Missing trailing slash — cricket API is query-param based
    # and the URL must end with '/' for params to attach correctly.
    BASE_URL = 'https://apiv2.api-cricket.com/cricket/'  # was: .../cricket (no slash)

    def __init__(self):
        super().__init__(settings.API_CRICKET_KEY)

    def get_live_scores(self):
        """Get live cricket matches"""
        params = {
            'method': 'get_livescore',
            'APIkey': self.api_key,
        }
        return self.fetch(self.BASE_URL, params=params)

    def get_fixtures_by_date(self, date: str):
        """Get cricket fixtures for a date (YYYY-MM-DD)"""
        params = {
            'method': 'get_events',
            'APIkey': self.api_key,
            'date_start': date,
            'date_stop': date,
        }
        return self.fetch(self.BASE_URL, params=params)

    def get_leagues(self):
        """Get all available cricket leagues"""
        params = {
            'method': 'get_leagues',
            'APIkey': self.api_key,
        }
        return self.fetch(self.BASE_URL, params=params)

    def get_teams(self, league_key: int):
        """Get teams for a league"""
        params = {
            'method': 'get_teams',
            'APIkey': self.api_key,
            'league_key': league_key,
        }
        return self.fetch(self.BASE_URL, params=params)

    def get_standings(self, league_key: int):
        """Get standings for a league"""
        params = {
            'method': 'get_standings',
            'APIkey': self.api_key,
            'league_key': league_key,
        }
        return self.fetch(self.BASE_URL, params=params)


# Global instance
api_cricket_service = APICricketService()