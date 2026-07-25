import logging
from urllib.parse import urlparse
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class BraveSearchService:
    """Brave Search API wrapper used for RSS source discovery."""

    BASE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self):
        self.api_key = getattr(settings, "BRAVESEARCH_KEY", "")

    def _headers(self):
        return {
            "X-Subscription-Token": self.api_key,
            "Accept": "application/json",
        }

    def discover_sources_for_entity(self, entity_name: str, entity_type: str, sport: str) -> list[str]:
        """Return a list of publisher domains for an entity.

        This is a lightweight discovery step that only keeps the domain(s),
        not any content snippets (compliant with Brave Search policy).
        """
        if not self.api_key:
            logger.warning("Brave Search API key is not set; skipping discovery")
            return []

        queries = self._build_queries(entity_name, entity_type, sport)
        domains = []
        seen = set()

        for query in queries:
            results = self._search(query)
            for url in results:
                domain = self._extract_domain(url)
                if domain and domain not in seen:
                    seen.add(domain)
                    domains.append(domain)

        logger.info(
            f"Brave discovery for '{entity_name}' ({entity_type}/{sport}): found {len(domains)} domains"
        )
        return domains

    def _build_queries(self, name: str, entity_type: str, sport: str) -> list[str]:
        queries = []
        sport_clean = (sport or '').strip()
        has_sport = bool(sport_clean and sport_clean.lower() != 'none')

        if entity_type == 'team':
            if has_sport:
                queries.append(f'"{name}" {sport_clean}')           # exact match
                queries.append(f'"{name}" match results {sport_clean}')
            else:
                queries.append(f'"{name}" news')
                queries.append(f'"{name}" updates')
        
        elif entity_type == 'athlete':
            if has_sport:
                queries.append(f'"{name}" footballer')         # exact match + context
                queries.append(f'"{name}" {sport_clean} player')
            else:
                queries.append(f'"{name}" news')
        
        elif entity_type == 'league':
            if has_sport:
                queries.append(f'"{name}" {sport_clean} standings')
                queries.append(f'"{name}" table results')
            else:
                queries.append(f'"{name}" standings')
        
        else:
            # Fallback for general or non-sports entity types (person, company, topic, entertainment, etc.)
            queries.append(f'"{name}" news')
            queries.append(f'"{name}" latest updates')

        return queries

    def _search(self, query: str) -> list[str]:
        try:
            resp = requests.get(self.BASE_URL, headers=self._headers(), params={"q": query, "count": 10}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Brave search request failed ({query}): {e}")
            return []

        urls = []
        
        # Brave's actual response key is 'web' -> 'results'
        web_results = data.get('web', {}).get('results', [])
        for item in web_results:
            url = item.get('url')
            if url:
                urls.append(url)

        return urls

    def _extract_domain(self, url: str) -> str | None:
        try:
            parsed = urlparse(url)
            if not parsed.scheme:
                # Assume https if missing
                url = f"https://{url}"
                parsed = urlparse(url)
            if not parsed.netloc:
                return None
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return None


# Global instance
brave_service = BraveSearchService()
