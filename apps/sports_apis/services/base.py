import requests
from django.conf import settings
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

class BaseAPIService:
    """Base class for all sports API services"""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
    
    def fetch(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None):
        """Centralized API caller with error handling"""
        try:
            response = self.session.get(
                url,
                params=params,
                headers=headers,
                timeout=15
            )
            response.raise_for_status()
            return {'success': True, 'data': response.json()}
        except requests.exceptions.Timeout:
            logger.error(f"Timeout calling {url}")
            return {'success': False, 'error': 'timeout'}
        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error calling {url}: {e}")
            return {'success': False, 'error': str(e), 'status_code': response.status_code}
        except Exception as e:
            logger.error(f"Error calling {url}: {e}")
            return {'success': False, 'error': str(e)}