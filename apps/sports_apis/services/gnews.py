from django.conf import settings
from apps.sports_apis.services.base import BaseAPIService
import logging

logger = logging.getLogger(__name__)


class GNewsService(BaseAPIService):
    """Service for GNews API"""
    
    BASE_URL = 'https://gnews.io/api/v4'
    
    CATEGORIES = [
        'general', 'world', 'nation', 'business',
        'technology', 'entertainment', 'sports', 'science', 'health'
    ]
    
    def __init__(self):
        super().__init__(settings.GNEWS_API_KEY)
    
    def search_entity_news(self, entity_name: str, sport: str, max_results: int = 10):
        """
        Search news for a specific entity
        
        Args:
            entity_name: Name of team/athlete/league
            sport: Sport type (basketball, soccer, etc.)
            max_results: Maximum articles to return (default 10, max 100)
        """
        # Build search query
        query = f'"{entity_name}" {sport}'
        
        url = f"{self.BASE_URL}/search"
        params = {
            'q': query,
            'lang': 'en',
            'max': min(max_results, 100),
            'sortby': 'publishedAt',
            'apikey': self.api_key
        }
        
        result = self.fetch(url, params=params)
        
        if result.get('success'):
            logger.info(f"GNews: Found {len(result['data'].get('articles', []))} articles for {entity_name}")
        else:
            logger.error(f"GNews search failed for {entity_name}: {result.get('error')}")
        
        return result
    
    def search_breaking_news(self, sport: str = 'sports', max_results: int = 20):
        """
        Get breaking sports news
        
        Args:
            sport: Sport keyword or 'sports' for all
            max_results: Maximum articles to return
        """
        url = f"{self.BASE_URL}/top-headlines"
        params = {
            'category': 'sports',
            'lang': 'en',
            'max': min(max_results, 100),
            'apikey': self.api_key
        }
        
        if sport != 'sports':
            params['q'] = sport
        
        return self.fetch(url, params=params)
    
    def get_top_headlines(self, category: str = 'sports', max_results: int = 10):
        """
        Get top headlines by category
        
        Args:
            category: One of CATEGORIES
            max_results: Maximum articles to return
        """
        if category not in self.CATEGORIES:
            return {'success': False, 'error': f'Invalid category: {category}'}
        
        url = f"{self.BASE_URL}/top-headlines"
        params = {
            'category': category,
            'lang': 'en',
            'max': min(max_results, 100),
            'apikey': self.api_key
        }
        
        return self.fetch(url, params=params)


# Global instance
gnews_service = GNewsService()