from django.conf import settings
from apps.sports_apis.services.base import BaseAPIService
import logging

logger = logging.getLogger(__name__)


class YouTubeService(BaseAPIService):
    """Service for YouTube Data API v3"""
    
    BASE_URL = 'https://www.googleapis.com/youtube/v3'
    
    def __init__(self):
        super().__init__(settings.YOUTUBE_API_KEY)
    
    def search_entity_channel(self, entity_name: str):
        """
        Search for entity's official YouTube channel
        
        Args:
            entity_name: Name of team/athlete/league
        
        Returns:
            Channel info or None
        """
        url = f"{self.BASE_URL}/search"
        params = {
            'part': 'snippet',
            'q': f'{entity_name} official',
            'type': 'channel',
            'maxResults': 1,
            'key': self.api_key
        }
        
        result = self.fetch(url, params=params)
        
        if result.get('success'):
            items = result['data'].get('items', [])
            if items:
                channel = items[0]
                return {
                    'channel_id': channel['snippet']['channelId'],
                    'channel_name': channel['snippet']['channelTitle'],
                    'description': channel['snippet']['description'],
                    'thumbnail': channel['snippet']['thumbnails']['default']['url']
                }
        
        return None
    
    def get_channel_videos(self, channel_id: str, max_results: int = 10):
        """
        Get latest videos from a channel
        
        Args:
            channel_id: YouTube channel ID
            max_results: Maximum videos to return
        """
        url = f"{self.BASE_URL}/search"
        params = {
            'part': 'snippet',
            'channelId': channel_id,
            'order': 'date',
            'type': 'video',
            'maxResults': max_results,
            'key': self.api_key
        }
        
        result = self.fetch(url, params=params)
        
        if result.get('success'):
            logger.info(f"YouTube: Found {len(result['data'].get('items', []))} videos for channel {channel_id}")
        
        return result
    
    def search_highlights(self, team1: str, team2: str = None, max_results: int = 5):
        """
        Search for match highlights
        
        Args:
            team1: First team name
            team2: Second team name (optional)
            max_results: Maximum videos to return
        """
        if team2:
            query = f'{team1} vs {team2} highlights'
        else:
            query = f'{team1} highlights'
        
        url = f"{self.BASE_URL}/search"
        params = {
            'part': 'snippet',
            'q': query,
            'order': 'date',
            'type': 'video',
            'maxResults': max_results,
            'key': self.api_key
        }
        
        return self.fetch(url, params=params)
    
    def get_video_details(self, video_id: str):
        """
        Get detailed video information
        
        Args:
            video_id: YouTube video ID
        """
        url = f"{self.BASE_URL}/videos"
        params = {
            'part': 'snippet,contentDetails,statistics',
            'id': video_id,
            'key': self.api_key
        }
        
        return self.fetch(url, params=params)


# Global instance
youtube_service = YouTubeService()