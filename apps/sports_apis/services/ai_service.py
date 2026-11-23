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
    """Service for discovering, previewing, and enriching publication news sources via DB matching, Brave Search, and RSS autodiscovery."""

    BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self):
        """Initialize Source AI discovery service with Brave Search credentials."""
        self.brave_key = getattr(settings, "BRAVESEARCH_KEY", "")

    # ─────────────────────────────────────────────────────────────────────
    # PUBLIC: main entry point
    # ─────────────────────────────────────────────────────────────────────

    POPULAR_SOURCES = [
        {"name": "ESPN", "domain": "https://www.espn.com", "rss_url": "https://www.espn.com/espn/rss/news", "description": "Global sports news coverage, stats, and live scores."},
        {"name": "BBC Sport", "domain": "https://www.bbc.com/sport", "rss_url": "https://push.api.bbci.co.uk/feed/news/friendly/sport", "description": "Sports news, scores, results, and analysis from the BBC."},
        {"name": "Sky Sports", "domain": "https://www.skysports.com", "rss_url": "https://www.skysports.com/rss/12040", "description": "Latest sports news, transfers, live scores, and video highlights."},
        {"name": "Sportsnet", "domain": "https://www.sportsnet.ca", "rss_url": "https://www.sportsnet.ca/feed/", "description": "Leading Canadian sports news network covering NHL, MLB, NBA, and more."},
        {"name": "The Guardian Sport", "domain": "https://www.theguardian.com/sport", "rss_url": "https://www.theguardian.com/sport/rss", "description": "Sports news, match reviews, comments, and podcasts from The Guardian."},
        {"name": "Yahoo Sports", "domain": "https://sports.yahoo.com", "rss_url": "https://sports.yahoo.com/rss/", "description": "Comprehensive sports news, scores, fantasy games, and video."},
        {"name": "Reuters Sports", "domain": "https://www.reuters.com/sports", "rss_url": "https://www.reutersagency.com/feed/", "description": "Global sports reporting, breaking news, and photos."},
        {"name": "The Athletic", "domain": "https://theathletic.com", "rss_url": "", "description": "In-depth sports coverage, analysis, and exclusive reporting."},
    ]

    SPORT_SOURCES = {
        "hockey": [
            {"name": "Sportsnet", "domain": "https://www.sportsnet.ca", "rss_url": "https://www.sportsnet.ca/feed/", "description": "Canadian sports leader covering the NHL, Maple Leafs, and hockey news."},
            {"name": "The Hockey News", "domain": "https://thehockeynews.com", "rss_url": "https://thehockeynews.com/feed", "description": "The definitive voice of hockey news, analysis, and NHL coverage."},
            {"name": "ESPN NHL", "domain": "https://www.espn.com/nhl", "rss_url": "https://www.espn.com/espn/rss/nhl/news", "description": "NHL scores, news, highlights, and rumors from ESPN."},
            {"name": "TSN Hockey", "domain": "https://www.tsn.ca", "rss_url": "", "description": "Canada's sports leader featuring NHL trades, scores, and analysis."},
            {"name": "The Athletic Hockey", "domain": "https://theathletic.com", "rss_url": "", "description": "In-depth hockey coverage, insider stories, and NHL reporting."},
        ],
        "soccer": [
            {"name": "Sky Sports Football", "domain": "https://www.skysports.com/football", "rss_url": "https://www.skysports.com/rss/12040", "description": "Live football news, transfers, Serie A, Champions League, and updates."},
            {"name": "BBC Sport Football", "domain": "https://www.bbc.com/sport/football", "rss_url": "https://push.api.bbci.co.uk/feed/news/friendly/sport", "description": "Football news, fixtures, results, and gossip from the BBC."},
            {"name": "ESPN FC", "domain": "https://www.espn.com/soccer", "rss_url": "https://www.espn.com/espn/rss/soccer/news", "description": "Global soccer news, match reports, transfers, and analysis."},
            {"name": "The Guardian Football", "domain": "https://www.theguardian.com/football", "rss_url": "https://www.theguardian.com/football/rss", "description": "Football analysis, match reviews, and European league coverage."},
            {"name": "Marca", "domain": "https://www.marca.com/en", "rss_url": "https://e00-marca.uecdn.es/rss/en/index.xml", "description": "Leading international and European soccer reporting."},
        ],
        "basketball": [
            {"name": "ESPN NBA", "domain": "https://www.espn.com/nba", "rss_url": "https://www.espn.com/espn/rss/nba/news", "description": "NBA scores, news, standings, and rumors from ESPN."},
            {"name": "Sportsnet Basketball", "domain": "https://www.sportsnet.ca", "rss_url": "https://www.sportsnet.ca/feed/", "description": "Comprehensive Raptors and NBA basketball news coverage."},
            {"name": "Bleacher Report NBA", "domain": "https://bleacherreport.com", "rss_url": "", "description": "Latest NBA news, trade rumors, scores, and highlights."},
            {"name": "The Athletic NBA", "domain": "https://theathletic.com", "rss_url": "", "description": "Deep-dive NBA basketball reporting and expert analysis."},
        ],
        "baseball": [
            {"name": "Sportsnet Baseball", "domain": "https://www.sportsnet.ca", "rss_url": "https://www.sportsnet.ca/feed/", "description": "Toronto Blue Jays and Major League Baseball news coverage."},
            {"name": "ESPN MLB", "domain": "https://www.espn.com/mlb", "rss_url": "https://www.espn.com/espn/rss/mlb/news", "description": "MLB news, scores, stats, and standings from ESPN."},
            {"name": "MLB.com", "domain": "https://www.mlb.com", "rss_url": "", "description": "The official site of Major League Baseball."},
            {"name": "The Athletic MLB", "domain": "https://theathletic.com", "rss_url": "", "description": "In-depth baseball analysis, scoops, and beat reporting."},
        ],
        "american_football": [
            {"name": "ESPN NFL", "domain": "https://www.espn.com/nfl", "rss_url": "https://www.espn.com/espn/rss/nfl/news", "description": "NFL news, scores, fantasy updates, and highlights from ESPN."},
            {"name": "Bleacher Report NFL", "domain": "https://bleacherreport.com", "rss_url": "", "description": "Breaking NFL news, rumors, scores, and game reactions."},
            {"name": "NFL.com", "domain": "https://www.nfl.com", "rss_url": "", "description": "Official news and coverage of the National Football League."},
        ],
    }
    SPORT_SOURCES["ice_hockey"] = SPORT_SOURCES["hockey"]
    SPORT_SOURCES["football"] = SPORT_SOURCES["soccer"]

    def suggest_sources(self, query: str) -> list[dict]:
        """Search and suggest candidate sports news sources given a user query keyword.
        Uses Brave Search API to discover publisher sources, strictly excluding Google News aggregator feeds.

        Args:
            query (str): Keyword or publication name (e.g. 'espn', 'Toronto Maple Leafs', 'cricket news').

        Returns:
            list[dict]: List of enriched source suggestion dictionaries.
        """
        ai_suggestions = []
        clean_query = (query or "").strip()
        if not clean_query:
            return []

        # 1. Primary: Use Brave Search API to find real publisher sources matching the query
        if self.brave_key:
            brave_results = self._brave_search(clean_query)
            if brave_results:
                ai_suggestions.extend(brave_results)

        # 2. Check if query matches an Entity (team/athlete/league) and use relevant sport publishers
        if not ai_suggestions:
            try:
                from apps.entity.models import Entity
                entity = Entity.objects.filter(
                    Q(name__iexact=clean_query) | Q(name__icontains=clean_query)
                ).first()
                if entity and entity.sport:
                    sport_key = entity.sport.lower().strip()
                    matched_sources = self.SPORT_SOURCES.get(sport_key) or []
                    for src in matched_sources:
                        if not any(s["domain"] == src["domain"] for s in ai_suggestions):
                            ai_suggestions.append(dict(src))
            except Exception as e:
                logger.warning(f"Entity sport source lookup failed: {e}")

        # 3. Match keyword against popular sources
        query_lower = clean_query.lower()
        for pop in self.POPULAR_SOURCES:
            if query_lower in pop["name"].lower() or query_lower in pop["domain"].lower():
                if not any(s["domain"] == pop["domain"] for s in ai_suggestions):
                    ai_suggestions.append(dict(pop))

        # 4. Match existing verified DB sources — STRICTLY EXCLUDING Google News aggregator feeds
        try:
            from apps.feed.models import Source
            words = [w.strip() for w in clean_query.split() if len(w.strip()) > 1]
            if words:
                q_words = Q()
                for w in words:
                    q_words |= (Q(name__icontains=w) | Q(domain__icontains=w))
                db_sources = list(
                    Source.objects.filter(q_words)
                    .exclude(domain__icontains='google.com')
                    .exclude(rss_url__icontains='google.com')
                    .exclude(name__icontains='Google News')[:8]
                )
                for src in db_sources:
                    if not any(s.get("domain") == src.domain for s in ai_suggestions):
                        ai_suggestions.append({
                            "source_id": src.id,
                            "name": src.name,
                            "domain": src.domain,
                            "description": f"News articles covering sports from {src.name}.",
                            "tags": [],
                            "rss_url": src.rss_url,
                        })
        except Exception as e:
            logger.warning(f"DB sources search failed: {e}")

        # 5. Fallback if still no suggestions
        if not ai_suggestions:
            for pop in self.POPULAR_SOURCES[:4]:
                ai_suggestions.append(dict(pop))

        # 6. Include 1 clean, deduplicated Google News topic source as an additional aggregator option
        if clean_query and not any('google.com' in s.get('domain', '').lower() for s in ai_suggestions):
            import urllib.parse
            clean_encoded = urllib.parse.quote(f'"{clean_query}"')
            ai_suggestions.append({
                "name": f"Google News - {clean_query}",
                "domain": "https://news.google.com",
                "description": f"Curated news coverage for {clean_query} from Google News.",
                "tags": [],
                "rss_url": f"https://news.google.com/rss/search?q={clean_encoded}&hl=en&gl=US&ceid=US:en",
            })

        # Step 2: Enrich each suggestion with favicon
        enriched = []
        for suggestion in ai_suggestions[:8]:
            enriched_item = self._enrich_lightweight(suggestion)
            if enriched_item:
                if suggestion.get("rss_url"):
                    enriched_item["rss_url"] = suggestion["rss_url"]
                    enriched_item["has_rss"] = True
                enriched.append(enriched_item)

        return enriched

    # ─────────────────────────────────────────────────────────────────────
    # STEP 1: Brave Search suggestions
    # ─────────────────────────────────────────────────────────────────────

    def _brave_search(self, query: str) -> list[dict]:
        """Execute web query via Brave Search to find publisher domains matching sports query.

        Args:
            query (str): Search term.

        Returns:
            list[dict]: Candidate publisher objects.
        """
        if not self.brave_key:
            return []

        search_query = query if ("sports" in query.lower() or "news" in query.lower()) else f"{query} sports news"
        try:
            resp = requests.get(
                self.BRAVE_URL,
                headers={
                    "X-Subscription-Token": self.brave_key,
                    "Accept": "application/json",
                },
                params={"q": search_query, "count": 10},
                timeout=8,
            )
            if resp.status_code != 200:
                logger.warning(f"Brave Search API returned {resp.status_code}: {resp.text[:200]}")
                return []

            data = resp.json()
            results = data.get("web", {}).get("results", [])

            generic_domains = {
                'google.com', 'news.google.com', 'youtube.com', 'youtu.be', 'twitter.com', 'x.com',
                'facebook.com', 'instagram.com', 'reddit.com', 'tiktok.com', 'wikipedia.org',
                'apple.com', 'spotify.com', 'vimeo.com', 'twitch.tv', 'amazon.com'
            }

            seen_domains = set()
            suggestions = []
            for item in results:
                url = item.get("url", "")
                domain = self._extract_domain(url)
                if not domain:
                    continue
                clean_domain = domain.replace("https://", "").replace("http://", "").split("/")[0].lower()
                if any(gd in clean_domain for gd in generic_domains):
                    continue
                if clean_domain in seen_domains:
                    continue
                seen_domains.add(clean_domain)

                # Clean publisher name from title or domain
                raw_title = item.get("title", "")
                name = ""
                if raw_title:
                    import re
                    parts = re.split(r'[:|\-–—]', raw_title)
                    cand = parts[0].strip()
                    if 2 < len(cand) < 60:
                        name = cand
                if not name:
                    name = item.get("meta_url", {}).get("hostname", clean_domain).replace("www.", "")

                # Check if this publisher already exists in DB with an active RSS feed
                rss_url = ""
                source_id = None
                try:
                    from apps.feed.models import Source
                    existing_src = Source.objects.filter(domain__icontains=clean_domain).exclude(rss_url='').first()
                    if existing_src:
                        rss_url = existing_src.rss_url
                        source_id = existing_src.id
                except Exception:
                    pass

                suggestions.append({
                    "source_id": source_id,
                    "name": name,
                    "domain": domain,
                    "description": item.get("description", ""),
                    "tags": [],
                    "rss_url": rss_url,
                })

            return suggestions[:6]

        except Exception as e:
            logger.warning(f"Brave search failed: {e}")
            return []

    def _brave_fallback(self, query: str) -> list[dict]:
        """Backward-compatibility alias for _brave_search."""
        return self._brave_search(query)

    # ─────────────────────────────────────────────────────────────────────
    # STEP 2: Enrich — favicon + RSS discovery
    # ─────────────────────────────────────────────────────────────────────

    def _enrich(self, suggestion: dict) -> dict | None:
        """Enrich a candidate publisher dictionary with high-res favicon and active RSS autodiscovery.

        Args:
            suggestion (dict): Raw candidate source data.

        Returns:
            dict or None: Fully enriched source dictionary.
        """
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
        """Enrich a source candidate with favicon without invoking slow synchronous RSS discovery.

        Args:
            suggestion (dict): Candidate source data.

        Returns:
            dict or None: Lightweight enriched source object.
        """
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
        rss_url = suggestion.get("rss_url", "")
        return {
            "source_id": suggestion.get("source_id"),
            "name": suggestion.get("name", parsed.netloc.replace("www.", "")),
            "domain": domain,
            "description": suggestion.get("description", ""),
            "favicon_url": favicon_url,
            "rss_url": rss_url,
            "has_rss": bool(rss_url),
            "tags": suggestion.get("tags", []),
        }
    
    def preview_source(self, url_or_name: str) -> dict | None:
        """Validate a user-provided URL or publisher name and return preview metadata and active RSS feeds.

        Args:
            url_or_name (str): Website domain or publisher name.

        Returns:
            dict or None: Preview card dictionary or None if invalid.
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
        """Parse base scheme and hostname domain from an arbitrary URL.

        Args:
            url (str): Full web address.

        Returns:
            str or None: Base URL scheme and host.
        """
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                return None
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return None


# Global instance
source_ai_service = SourceAIService()

