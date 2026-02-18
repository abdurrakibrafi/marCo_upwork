from django.conf import settings
from .base import BaseAPIService

class BallDontLieService(BaseAPIService):
    """Service for BallDontLie API (NBA, NFL, MLB, NHL)"""
    
    BASE_URLS = {
        'nba': 'https://api.balldontlie.io/v1/nba',
        'nfl': 'https://api.balldontlie.io/v1/nfl',
        'mlb': 'https://api.balldontlie.io/v1/mlb',
        'nhl': 'https://api.balldontlie.io/v1/nhl',
    }
    
    def __init__(self):
        super().__init__(settings.BALLDONTLIE_API_KEY)
    
    def get_live_games(self, sport: str):
        """Get live games for a sport (nba, nfl, mlb, nhl)"""
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}
        
        url = f"{self.BASE_URLS[sport]}/games/live"
        headers = {'Authorization': self.api_key}
        
        return self.fetch(url, headers=headers)
    
    def get_games_by_date(self, sport: str, date: str):
        """Get games for a specific date (YYYY-MM-DD)"""
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}
        
        url = f"{self.BASE_URLS[sport]}/games"
        headers = {'Authorization': self.api_key}
        params = {'dates[]': date}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_teams(self, sport: str):
        """Get all teams for a sport"""
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}
        
        url = f"{self.BASE_URLS[sport]}/teams"
        headers = {'Authorization': self.api_key}
        
        return self.fetch(url, headers=headers)
    
    def get_standings(self, sport: str):
        """Get standings for a sport"""
        if sport not in self.BASE_URLS:
            return {'success': False, 'error': f'Sport {sport} not supported'}
        
        url = f"{self.BASE_URLS[sport]}/standings"
        headers = {'Authorization': self.api_key}
        
        return self.fetch(url, headers=headers)

# Global instance
balldontlie_service = BallDontLieService()