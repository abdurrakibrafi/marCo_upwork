from django.conf import settings
from .base import BaseAPIService

class APICricketService(BaseAPIService):
    """Service for API-Cricket"""
    
    BASE_URL = 'https://api.api-cricket.com/v1'
    
    def __init__(self):
        super().__init__(settings.API_CRICKET_KEY)
    
    def get_live_scores(self):
        """Get live cricket matches"""
        url = f"{self.BASE_URL}/fixtures"
        headers = {'x-rapidapi-key': self.api_key}
        params = {'live': 'all'}
        
        return self.fetch(url, params=params, headers=headers)
    
    def get_fixtures_by_date(self, date: str):
        """Get cricket fixtures for a date"""
        url = f"{self.BASE_URL}/fixtures"
        headers = {'x-rapidapi-key': self.api_key}
        params = {'date': date}
        
        return self.fetch(url, params=params, headers=headers)

# Global instance
api_cricket_service = APICricketService()