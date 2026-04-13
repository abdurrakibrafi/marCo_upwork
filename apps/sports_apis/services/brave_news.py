"""
apps/sports_apis/services/brave_news.py

Uses Brave Search NEWS endpoint to fetch real-time sports news for entities.
This is Layer 1 — fast, always fresh, no RSS needed.
"""

import hashlib
import logging
from datetime import datetime, timezone

import requests
from django.conf import settings
from django.utils import timezone as django_timezone

logger = logging.getLogger(__name__)


class BraveNewsService:
    """
    Fetches real-time sports news using Brave Search News API.
    
    Brave news endpoint returns up to 50 fresh articles per query,
    with title, URL, description, thumbnail, and published date.
    """

    NEWS_URL = "https://api.search.brave.com/res/v1/news/search"

    def __init__(self):
        self.api_key = getattr(settings, 'BRAVESEARCH_KEY', '')

    def _headers(self):
        return {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self.api_key,
        }

    def fetch_news_for_entity(self, entity_name: str, entity_type: str, sport: str) -> list[dict]:
        queries = self._build_queries(entity_name, entity_type, sport)
        seen_urls = set()
        articles = []

        for query in queries:
            results = self._search_news(query)
            for item in results:
                url = item.get('url')
                if not url or url in seen_urls:
                    continue
                
                # ── Relevance filter ──────────────────────────────
                title = (item.get('title', '') or '').lower()
                description = (item.get('description', '') or '').lower()
                name_lower = entity_name.lower()
                
                # Skip if entity name not mentioned at all
                if name_lower not in title and name_lower not in description:
                    continue
                # ─────────────────────────────────────────────────
                
                seen_urls.add(url)
                article = self._normalize(item)
                if article:
                    articles.append(article)

        return articles

    def _build_queries(self, name: str, entity_type: str, sport: str) -> list[str]:
        """Build search queries based on entity type."""
        queries = [f"{name} {sport} news"]

        if entity_type == 'team':
            queries.append(f"{name} match results")
        elif entity_type == 'athlete':
            queries.append(f"{name} latest news")
        elif entity_type == 'league':
            queries.append(f"{name} standings results")

        return queries

    def _search_news(self, query: str) -> list[dict]:
        """Call Brave news search endpoint."""
        params = {
            "q": query,
            "count": 20,
            "freshness": "pw",  # last 7 days
            "search_lang": "en",
        }
        try:
            resp = requests.get(
                self.NEWS_URL,
                headers=self._headers(),
                params=params,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get('results', [])
        except Exception as e:
            logger.warning(f"Brave news search failed ({query}): {e}")
            return []

    def _normalize(self, item: dict) -> dict | None:
        """Normalize a Brave news result into our FeedItem format."""
        url = item.get('url', '')
        title = item.get('title', '').strip()

        if not url or not title:
            return None

        # Parse date
        published_at = django_timezone.now()
        age_str = item.get('page_age') or item.get('age', '')
        if age_str:
            try:
                published_at = datetime.fromisoformat(
                    age_str.replace('Z', '+00:00')
                ).replace(tzinfo=timezone.utc)
            except Exception:
                pass

        # Thumbnail
        thumbnail = ''
        thumb = item.get('thumbnail')
        if isinstance(thumb, dict):
            thumbnail = thumb.get('src', '') or thumb.get('original', '')
        elif isinstance(thumb, str):
            thumbnail = thumb

        # Source name
        meta = item.get('meta_url', {})
        source_name = (
            item.get('profile', {}).get('name', '') or
            meta.get('hostname', '') or
            meta.get('netloc', '')
        ).replace('www.', '')

        source_domain = f"{meta.get('scheme', 'https')}://{meta.get('netloc', '')}" if meta.get('netloc') else ''

        return {
            'url': url,
            'url_hash': hashlib.md5(url.encode()).hexdigest(),
            'title': title[:500],
            'summary': (item.get('description', '') or '').strip()[:2000],
            'thumbnail_url': thumbnail,
            'published_at': published_at,
            'source_name': source_name,
            'source_domain': source_domain,
        }


# Global instance
brave_news_service = BraveNewsService()