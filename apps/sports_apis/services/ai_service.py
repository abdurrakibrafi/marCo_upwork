"""
apps/source/ai_service.py

AI-powered source suggestion pipeline:
1. User types a query (e.g. "ESPN football", "cricket news India")
2. OpenAI GPT-4o suggests the best sports news sources for that query
3. We validate each suggestion: check Brave search + RSS discovery
4. Return enriched results with name, domain, description, favicon, rss_url
"""

import json
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

from apps.sports_apis.services.rss import rss_discovery_service

logger = logging.getLogger(__name__)


class SourceAIService:
    """
    Uses OpenAI to suggest sports news sources, then validates
    each one via Brave Search and RSS autodiscovery.
    """

    OPENAI_URL = "https://api.openai.com/v1/chat/completions"
    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self):
        self.openai_key = getattr(settings, "OPENAI_API_KEY", "")
        self.brave_key = getattr(settings, "BRAVESEARCH_KEY", "")

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: main entry point
    # ─────────────────────────────────────────────────────────────────────

    def suggest_sources(self, query: str) -> list[dict]:
        """
        Given a user search query, return a list of suggested sources.
        Each result: {name, domain, description, favicon_url, rss_url, has_rss, tags}
        """
        # Step 1: Ask GPT-4o for source suggestions
        ai_suggestions = self._ask_openai(query)
        if not ai_suggestions:
            # Fallback: use Brave to find sources directly
            ai_suggestions = self._brave_fallback(query)

        if not ai_suggestions:
            return []

        # Step 2: Enrich each suggestion with favicon (skip RSS discovery to reduce API calls)
        enriched = []
        for suggestion in ai_suggestions[:6]:  # reduced to 6 to be more conservative
            enriched_item = self._enrich_lightweight(suggestion)  # lighter version without RSS discovery
            if enriched_item:
                enriched.append(enriched_item)

        return enriched

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1a: OpenAI suggestion
    # ─────────────────────────────────────────────────────────────────────

    def _ask_openai(self, query: str) -> list[dict]:
        if not self.openai_key:
            logger.warning("OpenAI API key not set — skipping AI suggestions")
            return []

        system_prompt = """You are a sports media expert. When given a search query, 
suggest the best sports news sources (websites, newspapers, broadcasters) that match it.

Return ONLY valid JSON — an array of objects with these exact keys:
- name: publisher name (e.g. "ESPN")
- domain: full domain with https (e.g. "https://www.espn.com")
- description: one sentence about what this source covers
- tags: array of relevant tags (e.g. ["football", "NFL", "NBA"])

Rules:
- Only suggest real, existing sports news websites
- Prefer sources with RSS feeds (major sports publishers always have them)
- Match the query language: if query mentions a specific sport or region, bias toward that
- Return 5-8 sources maximum
- Return ONLY the JSON array, no markdown, no explanation"""

        user_prompt = f'Find sports news sources for this query: "{query}"'

        try:
            resp = requests.post(
                self.OPENAI_URL,
                headers={
                    "Authorization": f"Bearer {self.openai_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_completion_tokens": 800,
                },
                timeout=15,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            content = content.strip()

            suggestions = json.loads(content)
            if isinstance(suggestions, list):
                return suggestions
            return []

        except Exception as e:
            logger.warning(f"OpenAI source suggestion failed: {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1b: Brave fallback if OpenAI fails
    # ─────────────────────────────────────────────────────────────────────

    def _brave_fallback(self, query: str) -> list[dict]:
        if not self.brave_key:
            return []

        search_query = f"{query} sports news RSS"
        try:
            resp = requests.get(
                self.BRAVE_URL,
                headers={
                    "X-Subscription-Token": self.brave_key,
                    "Accept": "application/json",
                },
                params={"q": search_query, "count": 10},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("web", {}).get("results", [])

            seen_domains = set()
            suggestions = []
            for item in results:
                url = item.get("url", "")
                domain = self._extract_domain(url)
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                suggestions.append({
                    "name": item.get("meta_url", {}).get("hostname", domain).replace("www.", ""),
                    "domain": domain,
                    "description": item.get("description", ""),
                    "tags": [],
                })

            return suggestions[:8]

        except Exception as e:
            logger.warning(f"Brave fallback failed: {e}")
            return []

    # ─────────────────────────────────────────────────────────────────────
    # STEP 2: Enrich — favicon + RSS discovery
    # ─────────────────────────────────────────────────────────────────────

    def _enrich(self, suggestion: dict) -> dict | None:
        domain = suggestion.get("domain", "").strip()
        if not domain:
            return None

        # Normalize domain
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        parsed = urlparse(domain)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        # Favicon URL (use Google's reliable favicon service)
        favicon_url = f"https://www.google.com/s2/favicons?domain={parsed.netloc}&sz=64"

        # RSS discovery — try to find a valid feed
        rss_url = ""
        has_rss = False
        try:
            feeds = rss_discovery_service.discover_feeds_for_domain(domain)
            if feeds:
                rss_url = feeds[0]
                has_rss = True
        except Exception as e:
            logger.debug(f"RSS discovery failed for {domain}: {e}")

        return {
            "name": suggestion.get("name", parsed.netloc.replace("www.", "")),
            "domain": domain,
            "description": suggestion.get("description", ""),
            "favicon_url": favicon_url,
            "rss_url": rss_url,
            "has_rss": has_rss,
            "tags": suggestion.get("tags", []),
        }

    def _enrich_lightweight(self, suggestion: dict) -> dict | None:
        """Lightweight enrichment without RSS discovery to reduce API calls."""
        domain = suggestion.get("domain", "").strip()
        if not domain:
            return None

        # Normalize domain
        if not domain.startswith("http"):
            domain = f"https://{domain}"
        parsed = urlparse(domain)
        domain = f"{parsed.scheme}://{parsed.netloc}"

        # Favicon URL (use Google's reliable favicon service)
        favicon_url = f"https://www.google.com/s2/favicons?domain={parsed.netloc}&sz=64"

        # Skip RSS discovery to reduce HTTP requests
        return {
            "name": suggestion.get("name", parsed.netloc.replace("www.", "")),
            "domain": domain,
            "description": suggestion.get("description", ""),
            "favicon_url": favicon_url,
            "rss_url": "",  # Will be discovered later if user adds the source
            "has_rss": False,  # Assume false for now
            "tags": suggestion.get("tags", []),
        }
    
    # ADD THIS inside the SourceAIService class
# Place it between _enrich_lightweight and _extract_domain

    def preview_source(self, url_or_name: str) -> dict | None:
        """
        User pastes a URL or name (e.g. "https://www.espn.com" or "ESPN").
        We validate it, discover RSS, and return a preview card.
        No saving happens here — just validation + enrichment.
        """
        raw = url_or_name.strip()
        if not raw:
            return None

        # If it looks like a URL, use it directly
        if raw.startswith('http') or raw.startswith('www.') or '.' in raw:
            if not raw.startswith('http'):
                raw = f'https://{raw}'
            domain = self._extract_domain(raw)
            if not domain:
                return None

            # Scrape homepage for name + description
            name = ''
            description = ''
            try:
                resp = requests.get(
                    domain, timeout=8, headers={'User-Agent': 'Mozilla/5.0'}
                )
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(resp.text, 'html.parser')
                title_tag = soup.find('title')
                name = title_tag.text.strip()[:100] if title_tag else ''
                meta_desc = soup.find('meta', attrs={'name': 'description'})
                if meta_desc:
                    description = meta_desc.get('content', '')[:300]
            except Exception:
                pass

            suggestion = {
                'name': name or urlparse(domain).netloc.replace('www.', ''),
                'domain': domain,
                'description': description,
                'tags': [],
            }
            # Use full _enrich (with RSS discovery) for preview — we want rss_url here
            return self._enrich(suggestion)

        else:
            # It's a name like "ESPN" or "Sky Sports"
            # Ask OpenAI to find the official domain
            suggestions = self._ask_openai(
                f'find official website for sports source: {raw}'
            )
            if suggestions:
                return self._enrich(suggestions[0])
            return None


    def _extract_domain(self, url: str) -> str | None:
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                return None
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return None


# Global instance
source_ai_service = SourceAIService()

