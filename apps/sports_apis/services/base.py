import requests
from django.conf import settings
from typing import Optional, Dict
import logging

logger = logging.getLogger(__name__)

class BaseAPIService:
    """Base client class providing session management and standardized HTTP request error handling."""
    
    def __init__(self, api_key: str):
        """Initialize API client with credential token and requests Session.

        Args:
            api_key (str): Provider authentication token.
        """
        self.api_key = api_key
        self.session = requests.Session()
    
    def fetch(self, url: str, params: Optional[Dict] = None, headers: Optional[Dict] = None):
        """Execute GET HTTP request with centralized exception handling and JSON parsing.

        Args:
            url (str): Remote endpoint URI.
            params (dict, optional): URL query parameters.
            headers (dict, optional): Request HTTP headers.

        Returns:
            dict: Standardized result envelope `{'success': bool, 'data': ..., 'error': ...}`.
        """
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