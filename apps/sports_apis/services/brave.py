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

    def _build_queries(self, entity_name: str, entity_type: str, sport: str) -> list[str]:
        queries = []

        # Query 1: General news for the entity
        queries.append(f"{entity_name} {sport} news")

        # Query 2: Type-aware search
        if entity_type == "team":
            queries.append(f"{entity_name} {sport} match news")
        elif entity_type == "athlete":
            queries.append(f"{entity_name} {sport} highlights")
        elif entity_type == "league":
            queries.append(f"{entity_name} {sport} standings")

        # Query 3: RSS-heavy sports sites
        queries.append(f"{entity_name} {sport} rss")

        return queries

    def _search(self, query: str) -> list[str]:
        try:
            resp = requests.get(self.BASE_URL, headers=self._headers(), params={"q": query, "size": 10}, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"Brave search request failed ({query}): {e}")
            return []

        urls = []
        # Brave response can include "webPages" -> "value" list
        for key in ("webPages", "results", "items", "data"):
            if key in data and isinstance(data[key], dict):
                items = data[key].get("value") or data[key].get("results") or data[key].get("items")
                if isinstance(items, list):
                    for item in items:
                        url = item.get("url") or item.get("link") or item.get("displayUrl")
                        if url:
                            urls.append(url)
                    return urls
            if key in data and isinstance(data[key], list):
                for item in data[key]:
                    url = item.get("url") or item.get("link")
                    if url:
                        urls.append(url)
                return urls

        # Fallback: try to parse top-level results
        if isinstance(data.get("results"), list):
            for item in data["results"]:
                url = item.get("url") or item.get("link")
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
