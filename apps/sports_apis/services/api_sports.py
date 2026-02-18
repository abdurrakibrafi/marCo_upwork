from django.conf import settings
from .base import BaseAPIService

class APISportsService(BaseAPIService):
    """Service for API-Sports (Soccer, F1, etc.)"""
    
    BASE_URL = 'https://v3.football.api-sports.io'
    
    def __init__(self):
        super().__init__(settings.API_SPORTS_KEY)
    
    def get_live_fixtures(self):
        """Get all live soccer matches"""
        url = f"{self.BASE_URL}/fixtures"
        headers = {'x-apisports-key': self.api_key}
        params = {'live': 'all'}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_fixtures_by_date(self, date: str):
        """Get fixtures for a specific date (YYYY-MM-DD)"""
        url = f"{self.BASE_URL}/fixtures"
        headers = {'x-apisports-key': self.api_key}
        params = {'date': date}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_fixture_details(self, fixture_id: int):
        """Get detailed fixture information"""
        url = f"{self.BASE_URL}/fixtures"
        headers = {'x-apisports-key': self.api_key}
        params = {'id': fixture_id}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_standings(self, league_id: int, season: int):
        """Get league standings"""
        url = f"{self.BASE_URL}/standings"
        headers = {'x-apisports-key': self.api_key}
        params = {'league': league_id, 'season': season}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_teams(self, league_id: int, season: int):
        """Get teams in a league"""
        url = f"{self.BASE_URL}/teams"
        headers = {'x-apisports-key': self.api_key}
        params = {'league': league_id, 'season': season}
        
        return self.fetch(url, params=params, headers=headers)

# Global instance
api_sports_service = APISportsService()