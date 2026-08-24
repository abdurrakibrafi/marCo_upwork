import logging
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _safe_parse(url: str, timeout: int = 6) -> feedparser.FeedParserDict:
    """Fetch and parse XML payload from an RSS or Atom feed URL with fallback error handling.

    Args:
        url (str): Remote feed URI.
        timeout (int, optional): Request timeout in seconds. Defaults to 6.

    Returns:
        feedparser.FeedParserDict: Parsed feed structure.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/rss+xml, application/rdf+xml, application/atom+xml, application/xml, text/xml, */*"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return feedparser.parse(resp.content)
        return feedparser.FeedParserDict(bozo=1, entries=[])
    except Exception as e:
        logger.debug(f"RSS fetch failed for {url}: {e}")
        return feedparser.FeedParserDict(bozo=1, entries=[])


class RSSDiscoveryService:
    """Discovers and validates active RSS/Atom news feeds for target publication domains."""

    COMMON_PATHS = [
        '/feed',
        '/feed/',
        '/rss',
        '/rss.xml',
        '/feed.xml',
        '/atom.xml',
        '/feeds/posts/default',
        '/news/rss',
        '/sport/rss',
        '/football/rss',
        '/nba/rss',
        '/nfl/rss',
        '/cricket/rss',
        '/index.xml',
    ]

    def discover_feeds_for_domain(self, domain: str) -> list[str]:
        """Probe website HTML alternate tags and common endpoint paths to discover valid RSS feeds.

        Args:
            domain (str): Target domain URL.

        Returns:
            list[str]: Validated RSS/Atom endpoint URLs.
        """
        feeds = []
        domain = self._normalize_domain(domain)
        if not domain:
            return feeds

        # 1) Homepage discovery via <link rel="alternate">
        feeds.extend(self._discover_from_homepage(domain))

        # 2) Try common feed paths
        for path in self.COMMON_PATHS:
            candidate = urljoin(domain, path)
            if candidate in feeds:
                continue
            if self._validate_feed(candidate):
                feeds.append(candidate)

        return feeds

    def _discover_from_homepage(self, domain: str) -> list[str]:
        """Inspect HTML DOM for `<link rel="alternate" type="application/rss+xml">` declarations.

        Args:
            domain (str): Domain URL.

        Returns:
            list[str]: Discovered feed URLs.
        """
        try:
            resp = requests.get(domain, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        except Exception as e:
            logger.debug(f"Failed to fetch homepage for {domain}: {e}")
            return []

        soup = BeautifulSoup(resp.text, 'html.parser')
        found = []
        for link in soup.find_all('link', rel=lambda x: x and 'alternate' in x.lower()):
            href = link.get('href')
            type_attr = (link.get('type') or '').lower()
            if not href:
                continue
            if 'rss' in type_attr or 'atom' in type_attr or href.lower().endswith(('.xml', '.rss')):
                feed_url = urljoin(domain, href)
                if feed_url not in found and self._validate_feed(feed_url):
                    found.append(feed_url)
        return found

    def _validate_feed(self, feed_url: str) -> bool:
        """Validate if a candidate feed URL serves well-formed XML with one or more entries.

        Args:
            feed_url (str): Candidate feed URL.

        Returns:
            bool: True if feed contains entries, False otherwise.
        """
        try:
            parsed = _safe_parse(feed_url)
            # feedparser sets bozo to 1 if there was a problem
            if parsed.bozo and not parsed.entries:
                return False
            return bool(parsed.entries)
        except Exception as e:
            logger.debug(f"Failed to parse feed {feed_url}: {e}")
            return False

    def _normalize_domain(self, domain: str) -> str | None:
        """Ensure standard HTTPS protocol and extract netloc from domain string.

        Args:
            domain (str): Raw input domain.

        Returns:
            str or None: Normalized base URL string.
        """
        if not domain:
            return None
        parsed = urlparse(domain)
        if not parsed.scheme:
            domain = f"https://{domain}"
            parsed = urlparse(domain)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"


class RSSPollingService:
    """Service for polling RSS/Atom feed sources and parsing article entries."""

    def poll_feed(self, source) -> dict:
        """Fetch, parse, and normalize article entries from a Source model's RSS URL.

        Args:
            source: Source model instance with `rss_url`.

        Returns:
            dict: `{'success': bool, 'entries': list[dict]}` or error status.
        """
        feed_url = source.rss_url
        if not feed_url:
            return {'success': False, 'error': 'no feed url'}

        parsed = _safe_parse(feed_url)
        if parsed.bozo and not parsed.entries:
            return {'success': False, 'error': 'failed to parse feed'}

        entries = []
        for entry in parsed.entries:
            url = entry.get('link') or entry.get('id')
            title = entry.get('title', '')
            summary = entry.get('summary', '')
            published = self._parse_date(entry)
            thumbnail = self._extract_thumbnail(entry)
            entries.append({
                'url': url,
                'title': title,
                'summary': summary,
                'published_at': published,
                'thumbnail_url': thumbnail,
                'raw': entry,
            })

        return {'success': True, 'entries': entries}

    def _extract_thumbnail(self, entry) -> str:
        """Extract article preview image URL from media tags in the RSS feed entry.

        Args:
            entry (dict): Feedparser entry record.

        Returns:
            str: Thumbnail URL.
        """
        # Try common RSS media fields
        if entry.get('media_thumbnail'):
            thumbs = entry.get('media_thumbnail')
            if isinstance(thumbs, list) and thumbs:
                return thumbs[0].get('url', '')
            if isinstance(thumbs, dict):
                return thumbs.get('url', '')

        if entry.get('media_content'):
            media = entry.get('media_content')
            if isinstance(media, list) and media:
                return media[0].get('url', '')
            if isinstance(media, dict):
                return media.get('url', '')

        return ''

    def _parse_date(self, entry):
        """Parse RSS publication timestamp into a timezone-aware UTC datetime.

        Args:
            entry (dict): Feedparser entry object.

        Returns:
            datetime or None: UTC publication timestamp.
        """
        from datetime import datetime, timezone
        
        time_tuple = entry.get('published_parsed') or entry.get('updated_parsed')
        if time_tuple:
            import calendar
            return datetime.fromtimestamp(
                calendar.timegm(time_tuple), tz=timezone.utc
            )
        return None


# Global instances
rss_discovery_service = RSSDiscoveryService()
rss_polling_service = RSSPollingService()
