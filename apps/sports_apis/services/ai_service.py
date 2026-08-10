"""
apps/source/ai_service.py

Source suggestion pipeline:
1. User types a query (e.g. "ESPN football", "cricket news India")
2. We query Brave Search API or search local database/popular sources
3. Return enriched results with name, domain, description, favicon, rss_url
"""

import json
import logging
from urllib.parse import urlparse

import requests
from django.conf import settings

from apps.sports_apis.services.rss import rss_discovery_service

logger = logging.getLogger(__name__)

# pyrefly: ignore [missing-import]
from bs4 import BeautifulSoup
from django.db.models import Q



class SourceAIService:
    """
    Suggests sports news sources using Brave Search, DB matching, and RSS autodiscovery.
    """

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self):
        self.brave_key = getattr(settings, "BRAVESEARCH_KEY", "")

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: main entry point
    # ─────────────────────────────────────────────────────────────────────

    POPULAR_SOURCES = [
        {"name": "ESPN", "domain": "https://www.espn.com", "rss_url": "https://www.espn.com/espn/rss/news", "description": "Global sports news coverage, stats, and live scores."},
        {"name": "BBC Sport", "domain": "https://www.bbc.com/sport", "rss_url": "https://push.api.bbci.co.uk/feed/news/friendly/sport", "description": "Sports news, scores, results, and analysis from the BBC."},
        {"name": "Sky Sports", "domain": "https://www.skysports.com", "rss_url": "https://www.skysports.com/rss/12040", "description": "Latest sports news, transfers, live scores, and video highlights."},
        {"name": "The Guardian Sport", "domain": "https://www.theguardian.com/sport", "rss_url": "https://www.theguardian.com/sport/rss", "description": "Sports news, match reviews, comments, and podcasts from The Guardian."},
        {"name": "Yahoo Sports", "domain": "https://sports.yahoo.com", "rss_url": "https://sports.yahoo.com/rss/", "description": "Comprehensive sports news, scores, fantasy games, and video."},
        {"name": "Reuters Sports", "domain": "https://www.reuters.com/sports", "rss_url": "https://www.reutersagency.com/feed/", "description": "Global sports reporting, breaking news, and photos."},
        {"name": "The Athletic", "domain": "https://theathletic.com", "rss_url": "", "description": "In-depth sports coverage, analysis, and exclusive reporting."},
    ]

    def suggest_sources(self, query: str) -> list[dict]:
        """
        Given a user search query, return a list of suggested sources.
        Checks DB first, then popular sources, and falls back to Brave Search if needed.
        Each result: {name, domain, description, favicon_url, rss_url, has_rss, tags}
        """
        ai_suggestions = []
        
        # 1. Try DB matching FIRST
        try:
            from apps.feed.models import Source
            from apps.entity.models import Entity
            
            words = [w.strip() for w in query.split() if len(w.strip()) > 1]
            db_sources = []
            
            if words:
                # 1a. Try matching ALL words in name/domain (AND search)
                q_and = Q()
                for w in words:
                    q_and &= (Q(name__icontains=w) | Q(domain__icontains=w))
                db_sources = list(Source.objects.filter(q_and)[:6])
                
                # 1b. Try matching ANY words if no results (OR search)
                if not db_sources:
                    q_or = Q()
                    for w in words:
                        q_or |= (Q(name__icontains=w) | Q(domain__icontains=w))
                    db_sources = list(Source.objects.filter(q_or)[:6])
                    
                # 1c. Try matching entities (teams/leagues/athletes) and get their linked sources
                if not db_sources:
                    q_entity = Q()
                    for w in words:
                        q_entity |= Q(name__icontains=w)
                    entities = Entity.objects.filter(q_entity)
                    if entities.exists():
                        db_sources = list(Source.objects.filter(entities__in=entities).distinct()[:6])
            
            # Direct fallback if still no sources
            if not db_sources:
                db_sources = list(Source.objects.filter(
                    Q(name__icontains=query) | Q(domain__icontains=query)
                )[:6])
                
            for src in db_sources:
                ai_suggestions.append({
                    "name": src.name,
                    "domain": src.domain,
                    "description": f"News articles covering sports from {src.name}.",
                    "tags": [],
                    "rss_url": src.rss_url,
                })
        except Exception as e:
            logger.warning(f"DB sources search failed: {e}")

        # 2. Try Popular Sources matching if DB returned no results
        if not ai_suggestions:
            query_lower = query.lower()
            for pop in self.POPULAR_SOURCES:
                if query_lower in pop["name"].lower() or query_lower in pop["domain"].lower():
                    ai_suggestions.append({
                        "name": pop["name"],
                        "domain": pop["domain"],
                        "description": pop["description"],
                        "tags": [],
                        "rss_url": pop["rss_url"],
                    })

        # 3. Try Brave Search fallback if still no suggestions
        if not ai_suggestions and self.brave_key:
            ai_suggestions = self._brave_fallback(query)

        # Step 2: Enrich each suggestion with favicon
        enriched = []
        for suggestion in ai_suggestions[:6]:
            enriched_item = self._enrich_lightweight(suggestion)
            if enriched_item:
                # If suggestion has a custom rss_url, use it
                if suggestion.get("rss_url"):
                    enriched_item["rss_url"] = suggestion["rss_url"]
                    enriched_item["has_rss"] = True
                enriched.append(enriched_item)

        return enriched

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1: Brave Search suggestions
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
                # pyrefly: ignore [missing-import]
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
            # 1. Try DB match fallback
            try:
                from apps.feed.models import Source
                existing = Source.objects.filter(name__icontains=raw).first()
                if existing:
                    return {
                        'name': existing.name,
                        'domain': existing.domain,
                        'description': f"News articles covering sports from {existing.name}.",
                        'tags': [],
                        'rss_url': existing.rss_url,
                        'favicon_url': existing.favicon_url,
                        'has_rss': bool(existing.rss_url),
                    }
            except Exception as e:
                logger.warning(f"DB search in preview_source failed: {e}")

            # 3. Try Popular Sources match fallback
            raw_lower = raw.lower()
            for pop in self.POPULAR_SOURCES:
                if raw_lower in pop['name'].lower():
                    return {
                        'name': pop['name'],
                        'domain': pop['domain'],
                        'description': pop['description'],
                        'tags': [],
                        'rss_url': pop['rss_url'],
                        'favicon_url': f"https://www.google.com/s2/favicons?domain={urlparse(pop['domain']).netloc}&sz=64",
                        'has_rss': bool(pop['rss_url']),
                    }

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

