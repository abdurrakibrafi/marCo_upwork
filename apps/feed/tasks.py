"""
Key fixes in this file:
- poll_single_source: relaxed entity matching so articles aren't silently dropped
- fetch_brave_news_for_entity: fixed Source unique constraint crash on rss_url=None
"""
from celery import shared_task
from django.utils import timezone
from datetime import timedelta
from django.db.models import Q

from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.brave import brave_service
from apps.sports_apis.services.rss import rss_discovery_service, rss_polling_service

import hashlib
import logging
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


import re

def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common HTML entities from a string."""
    if not text:
        return ''
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>').replace('&nbsp;', ' ').replace('&quot;', '"').replace('&#39;', "'")
    # Collapse multiple whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _extract_publisher(html: str) -> str:
    """Extract the actual publisher name from Google News RSS HTML.

    Google News encodes the publisher like:
        <font color="#6f6f6f">ESPN</font>
    Returns the publisher name, e.g. 'ESPN', or '' if not found.
    """
    if not html:
        return ''
    # Try <font color="#6f6f6f">Publisher</font> pattern (Google News RSS)
    match = re.search(r'<font[^>]*color=["\']#6f6f6f["\'][^>]*>([^<]+)</font>', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    # Fallback: last <li> or trailing text after last </a>
    match = re.search(r'</a>\s*(?:&nbsp;)*\s*([A-Za-z0-9 .\-]+)\s*$', html)
    if match:
        candidate = match.group(1).strip()
        if 2 < len(candidate) < 80:
            return candidate
    return ''


def _extract_domain(url: str) -> str | None:
    try:
        parsed = urlparse(url)
        if not parsed.scheme:
            url = f"https://{url}"
            parsed = urlparse(url)
        if not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"
    except Exception:
        return None


@shared_task(bind=True, max_retries=1)
def discover_rss_feeds_for_entity(self, entity_id: int):
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} does not exist"

    if getattr(entity, 'rss_discovery_done', False):
        return f"Discovery already completed for {entity.name}"

    domains = brave_service.discover_sources_for_entity(entity.name, entity.type, entity.sport)
    for domain in domains:
        extract_rss_from_domain.delay(entity_id, domain)

    entity.rss_discovery_done = True
    entity.save(update_fields=['rss_discovery_done'])
    return f"Discovered {len(domains)} domains for {entity.name}"


@shared_task(bind=True, max_retries=1)
def extract_rss_from_domain(self, entity_id: int, domain: str):
    feeds = rss_discovery_service.discover_feeds_for_domain(domain)
    for feed_url in feeds:
        store_validated_feed.delay(entity_id, feed_url, discovery_source='brave')
    return f"Found {len(feeds)} feeds for {domain}"


@shared_task(bind=True, max_retries=1)
def store_validated_feed(self, entity_id: int, feed_url: str, discovery_source: str = 'brave'):
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} does not exist"

    if not rss_discovery_service._validate_feed(feed_url):
        return f"Feed {feed_url} is not valid"

    domain = _extract_domain(feed_url) or feed_url

    source, created = Source.objects.get_or_create(
        rss_url=feed_url,
        defaults={
            'name': domain,
            'domain': domain,
            'is_active': True,
        }
    )

    source.entities.add(entity)
    poll_single_source.delay(source.id)
    return f"Stored source {source.id} for {entity.name} ({'created' if created else 'updated'})"


@shared_task
def update_all_entity_feeds(entity_id: int):
    return discover_rss_feeds_for_entity.delay(entity_id)


@shared_task
def update_user_nest_feeds(user_id: int):
    from apps.nest.models import UserNest
    nest_entity_ids = list(
        UserNest.objects.filter(user_id=user_id).values_list('entity_id', flat=True)
    )
    for entity_id in nest_entity_ids:
        update_all_entity_feeds.delay(entity_id)
    return f"Triggered feed updates for {len(nest_entity_ids)} entities"


@shared_task
def update_trending_entities_feeds():
    trending = Entity.objects.filter(is_active=True).order_by('-follower_count')[:50]
    for entity in trending:
        update_all_entity_feeds.delay(entity.id)
    return f"Triggered feed updates for {trending.count()} trending entities"


@shared_task
def poll_all_active_sources():
    """Poll all RSS sources that are due, staggered to avoid burst."""
    now = timezone.now()
    due_sources = Source.objects.filter(
        is_active=True,
        rss_url__isnull=False,   # BUG FIX: skip Brave-only sources entirely
    ).exclude(
        rss_url='',
    ).filter(
        Q(last_polled_at__isnull=True) |
        Q(last_polled_at__lte=now - timedelta(minutes=1))
    )

    to_poll = []
    for source in due_sources:
        if not source.last_polled_at:
            to_poll.append(source.id)
            continue
        elapsed = (now - source.last_polled_at).total_seconds()
        if elapsed >= source.poll_interval_minutes * 60:
            to_poll.append(source.id)

    # BUG FIX: stagger tasks 2 seconds apart instead of all at once
    # 100 sources = dispatched over 200 seconds instead of all in 1 second
    for i, source_id in enumerate(to_poll):
        poll_single_source.apply_async(
            args=[source_id],
            countdown=i * 2,  # source 0 fires now, source 1 in 2s, source 2 in 4s...
        )

    return f"Queued {len(to_poll)} sources for polling (staggered over {len(to_poll) * 2}s)"


def _entity_matches_text(entity: Entity, text: str) -> bool:
    """
    Check if a feed article text (title/summary) matches a specific Entity.
    
    Logic:
    1. Exact phrase/name match (with word boundaries).
    2. Common variations/aliases (e.g. "Man Utd" matches "Manchester United", "PSG" matches "Paris Saint-Germain").
    3. Individual word matching for non-generic words (e.g. "Saka" matches "Bukayo Saka", but "Miami" alone does NOT match "Inter Miami" to avoid Dolphins matches).
    """
    from apps.entity.utils.normalizers import normalize_entity_name

    name = normalize_entity_name(entity.name)
    text_norm = normalize_entity_name(text)

    # 1. Exact phrase/name match (with word boundaries)
    escaped_name = re.escape(name)
    if re.search(r'\b' + escaped_name + r'\b', text_norm):
        return True

    # 2. Known Aliases / short forms (with word boundaries)
    aliases_map = {
        'manchester united': {'man united', 'man utd', 'mufc'},
        'manchester city': {'man city', 'mancity', 'mcfc'},
        'real madrid': {'real madrid', 'los blancos'},
        'barcelona': {'barca', 'fc barcelona'},
        'paris saint-germain (psg)': {'psg', 'paris sg', 'paris saint-germain'},
        'paris saint-germain': {'psg', 'paris sg', 'paris saint-germain'},
        'psg': {'psg', 'paris sg', 'paris saint-germain'},
        'inter milan': {'inter', 'internazionale', 'inter milan'},
    }
    
    aliases = aliases_map.get(name, set())
    for alias in aliases:
        if re.search(r'\b' + re.escape(alias) + r'\b', text_norm):
            return True

    # 3. Individual word matching (only for words NOT in generic/ambiguous list)
    GENERIC_WORDS = {
        'fc', 'ac', 'sc', 'cf', 'utd', 'united', 'city', 'town', 'county', 'club', 'sports',
        'miami', 'manchester', 'madrid', 'milan', 'london', 'york', 'los', 'angeles', 'boston',
        'chicago', 'houston', 'dallas', 'san', 'diego', 'francisco', 'jose', 'la', 'de', 'deportivo',
        'real', 'atletico', 'athletic', 'sporting', 'racing', 'union', 'saint', 'st', 'germain',
        'inter', 'sheffield', 'west', 'north', 'south', 'east', 'port', 'rovers', 'wanderers',
        'rangers', 'celtic', 'hearts', 'hibernian', 'albion', 'forest', 'villa', 'palace', 'team',
        'division', 'championship', 'cup', 'state', 'green', 'white', 'red', 'blue', 'black'
    }

    words = [w for w in name.split() if len(w) >= 4]
    for word in words:
        if word not in GENERIC_WORDS:
            if re.search(r'\b' + re.escape(word) + r'\b', text_norm):
                return True

    return False


def _resolve_thumbnail_for_article(title: str, entities: list) -> str:
    """
    Finds a thumbnail for an article using:
    Brave Search API (if BRAVESEARCH_KEY is configured and not rate-limited).
    Returns empty string if not found.
    """
    from django.conf import settings
    import requests

    # ── Brave Search News API Lookup ──
    brave_key = getattr(settings, 'BRAVESEARCH_KEY', '')
    if brave_key:
        try:
            query_clean = re.sub(r'[^\w\s]', ' ', title).strip()
            url = "https://api.search.brave.com/res/v1/news/search"
            headers = {
                "X-Subscription-Token": brave_key,
                "Accept": "application/json"
            }
            resp = requests.get(url, headers=headers, params={"q": query_clean, "count": 1}, timeout=5)
            if resp.status_code == 200:
                results = resp.json().get('results', [])
                if results:
                    thumb = results[0].get('thumbnail')
                    if isinstance(thumb, dict) and (thumb.get('src') or thumb.get('original')):
                        return thumb.get('src') or thumb.get('original')
                    elif isinstance(thumb, str) and thumb:
                        return thumb
        except Exception as e:
            logger.warning(f"Brave Search thumbnail search failed: {e}")

    return ''


@shared_task(bind=True, max_retries=2)
def poll_single_source(self, source_id: int):
    try:
        source = Source.objects.get(id=source_id, is_active=True)
    except Source.DoesNotExist:
        return f"Source {source_id} not found or inactive"
    
    if not source.rss_url:
        return f"Source {source_id} has no RSS url — skipping (Brave-only source)"

    result = rss_polling_service.poll_feed(source)
    if not result.get('success'):
        source.poll_failures += 1
        source.save(update_fields=['poll_failures'])
        logger.warning(f"Polling failed for source {source.id}: {result.get('error')}")
        # Don't retry for permanent failures like bad URLs
        if result.get('error') == 'no feed url':
            source.is_active = False
            source.save(update_fields=['is_active'])
            return f"Deactivated source {source.id} — no feed url"
        raise self.retry(exc=Exception(result.get('error')))

    candidate_entities = list(source.entities.all())
    is_global_source = not candidate_entities
    if is_global_source:
        candidate_entities = list(Entity.objects.filter(is_active=True))

    new_items = 0
    for entry in result.get('entries', []):
        url = entry.get('url')
        if not url:
            continue

        text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()

        # Match entry text against candidate entities
        matched_entities = [e for e in candidate_entities if _entity_matches_text(e, text)]

        # If article doesn't match any entity, skip it
        if not matched_entities:
            continue

        url_hash = hashlib.md5(url.encode()).hexdigest()

        # Resolve thumbnail
        thumbnail_url = entry.get('thumbnail_url', '')
        if not thumbnail_url:
            thumbnail_url = _resolve_thumbnail_for_article(
                title=entry.get('title', ''),
                entities=matched_entities
            )

        obj, created = FeedItem.objects.get_or_create(
            url_hash=url_hash,
            defaults={
                'source': source,
                'title': entry.get('title', '')[:500],
                'url': url,
                'summary': _strip_html(entry.get('summary', '')),
                'publisher_name': _extract_publisher(entry.get('summary', '')),
                'thumbnail_url': thumbnail_url,
                'published_at': entry.get('published_at') or timezone.now(),
            }
        )
        if created:
            if matched_entities:
                obj.entities.set(matched_entities)
            new_items += 1
        else:
            if matched_entities:
                obj.entities.add(*matched_entities)

    source.last_polled_at = timezone.now()
    source.poll_failures = 0
    source.save(update_fields=['last_polled_at', 'poll_failures'])

    logger.info(f"Polled source {source.id}: {new_items} new items")
    return f"Polled source {source.id}: {new_items} new items"


@shared_task
def cleanup_old_feed_items():
    cutoff_date = timezone.now() - timedelta(days=30)
    deleted_count = FeedItem.objects.filter(published_at__lt=cutoff_date).delete()[0]
    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task
def mark_trending_items():
    FeedItem.objects.update(is_trending=False)
    last_24h = timezone.now() - timedelta(hours=24)
    trending_ids = list(
        FeedItem.objects.filter(
            published_at__gte=last_24h
        ).order_by('-views')[:100].values_list('id', flat=True)
    )
    FeedItem.objects.filter(id__in=trending_ids).update(is_trending=True)
    return f"Marked {len(trending_ids)} items as trending"


@shared_task
def fetch_brave_news_for_entity(entity_id: int):
    from apps.sports_apis.services.brave_news import brave_news_service

    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    articles = brave_news_service.fetch_news_for_entity(
        entity.name, entity.type, entity.sport
    )

    if not articles:
        return f"No articles found for {entity.name}"

    new_count = 0
    for article in articles:
        source_domain = article.get('source_domain', '')
        source_name = article.get('source_name', 'Unknown')

        if source_domain:
            # BUG FIX: Brave news sources have no rss_url — using get_or_create
            # on rss_url=None caused unique constraint violations in Postgres
            # (multiple NULL rows). Match on domain instead, and only set
            # rss_url if it doesn't conflict.
            source, _ = Source.objects.get_or_create(
                domain=source_domain,
                rss_url=None,   # explicitly None = Brave-only source
                defaults={
                    'name': source_name,
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )
        else:
            source, _ = Source.objects.get_or_create(
                name='Brave News',
                rss_url=None,
                defaults={
                    'domain': 'brave.com',
                    'is_active': True,
                    'discovery_source': 'brave',
                }
            )

        obj, created = FeedItem.objects.get_or_create(
            url_hash=article['url_hash'],
            defaults={
                'source': source,
                'title': article['title'],
                'url': article['url'],
                'summary': _strip_html(article['summary']),
                'publisher_name': _extract_publisher(article.get('raw_summary', article['summary'])),
                'thumbnail_url': article['thumbnail_url'],
                'published_at': article['published_at'],
            }
        )

        if created:
            obj.entities.add(entity)
            new_count += 1
        else:
            obj.entities.add(entity)

    logger.info(f"Brave news for {entity.name}: {new_count} new articles")
    return f"Fetched {len(articles)} articles for {entity.name}, {new_count} new"


@shared_task
def fetch_brave_news_for_all_nest_entities():
    from apps.nest.models import UserNest
    entity_ids = list(
        UserNest.objects.values_list('entity_id', flat=True).distinct()
    )
    for entity_id in entity_ids:
        fetch_brave_news_for_entity.delay(entity_id)
    return f"Triggered Brave news fetch for {len(entity_ids)} entities"


@shared_task
def fetch_brave_news_for_trending():
    entities = Entity.objects.filter(is_active=True).order_by('-follower_count')[:20]
    for entity in entities:
        fetch_brave_news_for_entity.delay(entity.id)
    return f"Triggered Brave news fetch for {entities.count()} trending entities"


@shared_task
def fetch_brave_news_for_all_entities():
    """
    Fetch fresh news for active entities in the database that are followed by users.
    Filters out unfollowed entities to conserve Brave Search API key quota.
    
    Staggered 2 seconds apart to avoid rate limiting.
    """
    entities = Entity.objects.filter(is_active=True, follower_count__gt=0)
    count = entities.count()
    
    for i, entity in enumerate(entities):
        fetch_brave_news_for_entity.apply_async(
            args=[entity.id],
            countdown=i * 2  # stagger 2s apart
        )
    
    logger.info(f"Triggered news fetch for {count} followed active entities")
    return f"Triggered news fetch for {count} entities"


@shared_task
def ensure_entity_has_rss_source(entity_id: int):
    """
    Guaranteed fallback: targeted Google News RSS per entity.
    No API key needed. Fires every time user adds entity to nest.
    Also backfills orphan FeedItems by title matching.
    """
    import urllib.parse

    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    query = urllib.parse.quote(entity.name)
    google_news_url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"

    source, created = Source.objects.get_or_create(
        rss_url=google_news_url,
        defaults={
            'name': f'Google News - {entity.name}',
            'domain': 'news.google.com',
            'is_active': True,
            'discovery_source': 'known',
        }
    )
    source.entities.add(entity)
    poll_single_source.delay(source.id)

    linked = 0
    for item in FeedItem.objects.filter(entities__isnull=True).iterator():
        text = f"{item.title} {item.summary or ''}".lower()
        if _entity_matches_text(entity, text):
            item.entities.add(entity)
            linked += 1

    logger.info(f"ensure_entity_has_rss_source: {entity.name}, source={'created' if created else 'linked'}, backfilled={linked}")
    return f"Google News RSS {'created' if created else 'linked'} for {entity.name}, {linked} orphan items backfilled"


# ─────────────────────────────────────────────────────────────────────────────
# ARTICLE CONTENT FETCH — Jina AI Reader + OpenAI Summary (Lazy, on-demand)
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(max_retries=2, default_retry_delay=10)
def fetch_article_content(feed_item_id: int):
    """
    Lazily fetches full article content for a FeedItem using Jina AI Reader,
    then generates a 2-3 sentence summary using the project's OpenAI service.

    Called on-demand when a user requests full article details.
    Result is cached in the DB — subsequent calls return instantly.
    """
    try:
        item = FeedItem.objects.get(id=feed_item_id)
    except FeedItem.DoesNotExist:
        return f"FeedItem {feed_item_id} not found"

    if item.content_fetched:
        return f"Already fetched for item {feed_item_id}"

    import requests
    from django.conf import settings

    # ── Step 1: Decode Google redirect using googlenewsdecoder ──────────────
    target_url = item.url
    if "news.google.com" in item.url:
        try:
            # pyrefly: ignore [missing-import]
            from googlenewsdecoder import new_decoderv1
            decoded = new_decoderv1(item.url)
            if decoded.get("status") and decoded.get("decoded_url"):
                target_url = decoded["decoded_url"]
                logger.info(f"[Decoder] Successfully resolved redirect for item {feed_item_id} to: {target_url}")
        except Exception as exc:
            logger.warning(f"[Decoder] Failed decoding redirect for item {feed_item_id}: {exc}")

    # ── Step 2: Fetch and Extract Content ──────────────────────────────────
    content = None
    fetched_html = None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/115.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }

    # Try fetching raw HTML directly first
    try:
        resp = requests.get(target_url, headers=headers, timeout=15)
        if resp.status_code == 200:
            fetched_html = resp.text
            content = extract_clean_article(fetched_html, target_url)
    except Exception as exc:
        logger.info(f"[Scraper] Direct raw fetch failed for {target_url}: {exc}. Falling back to Jina.")

    # Fallback to Jina Reader if direct fetch failed or returned no text
    if not content:
        jina_url = f"https://r.jina.ai/{target_url}"
        try:
            resp = requests.get(
                jina_url,
                headers={"Accept": "text/plain", "X-Timeout": "15"},
                timeout=20,
            )
            resp.raise_for_status()
            jina_content = resp.text.strip()
            content = extract_clean_article(jina_content, target_url)
        except Exception as exc:
            logger.warning(f"[Jina] Failed to fetch content for item {feed_item_id}: {exc}")
            item.content_fetched = False
            item.save(update_fields=["content_fetched"])
            return f"Extraction failed: direct and Jina fallback both failed ({exc})"

    # Check if the extracted content is empty or represents a junk page (like FanDuel sportsbook promos)
    if not content or len(content) < 200 or _is_junk_page(target_url, content):
        item.content = ""
        item.ai_summary = ""
        item.content_fetched = False
        item.save(update_fields=["content", "ai_summary", "content_fetched"])
        return f"Fetch skipped for item {feed_item_id}: junk page or content too short/empty"

    # Trim to first 4000 chars for OpenAI (cost control)
    content_for_ai = content[:4000]

    # ── Step 2: Generate summary via fallback (OpenAI disabled for now) ──────
    ai_summary = _clean_fallback_summary(content, item.title)

    # ── Step 3: Save to DB ───────────────────────────────────────────────────
    item.content = content
    item.ai_summary = ai_summary
    item.content_fetched = True
    item.save(update_fields=["content", "ai_summary", "content_fetched"])

    logger.info(f"[Article] Fetched content + summary for FeedItem {feed_item_id}")
    return f"Done: item {feed_item_id}, content={len(content)} chars"


def _clean_fallback_summary(content: str, title: str) -> str:
    """Helper to generate a clean preview summary from raw/Jina markdown content."""
    if not content:
        return ""
    # Strip Jina headers if present
    if "Markdown Content:" in content:
        content = content.split("Markdown Content:", 1)[1]
    
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
    
    # 1. Generate title keywords
    title_words = {w.lower() for w in re.findall(r'\w+', title) if len(w) > 3}
    
    # 2. Locate header matching title (start of actual body)
    body_start_index = 0
    for idx, p in enumerate(paragraphs):
        if p.startswith('#'):
            p_words = {w.lower() for w in re.findall(r'\w+', p) if len(w) > 3}
            intersection = title_words.intersection(p_words)
            if len(intersection) >= 2:
                body_start_index = idx
                break
                
    # 3. Find 1-2 paragraphs of actual content containing title keywords
    found_paragraphs = []
    for p in paragraphs[body_start_index:]:
        if p.startswith('#') or p.startswith('*') or p.startswith('-') or p.startswith('|') or p.startswith('['):
            continue
            
        # Clean markdown formatting and links
        plain = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', p)
        plain = re.sub(r'\!\[[^\]]*\]\([^\)]+\)', '', plain)
        plain = re.sub(r'\s+', ' ', plain).strip()
        
        lower_plain = plain.lower()
        # Skip sharing, credits, author bios, social media links
        if any(x in lower_plain for x in ('facebook', 'twitter', 'linkedin', 'share on', 'written by', 'editor for', 'read full bio', 'credit:')):
            continue
            
        if len(plain) > 80:
            plain_words = {w.lower() for w in re.findall(r'\w+', plain)}
            intersection = title_words.intersection(plain_words)
            # If paragraph contains title keywords, keep it
            if len(intersection) >= 2:
                found_paragraphs.append(plain)
                if len(found_paragraphs) >= 2:
                    break
                    
    # Combine found paragraphs
    if found_paragraphs:
        return " ".join(found_paragraphs)
        
    # Absolute fallback: simple text cleaning on first 300 chars
    plain = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', content)
    plain = re.sub(r'\s+', ' ', plain).strip()
    if len(plain) > 300:
        short = plain[:300]
        last_dot = short.rfind('.')
        if last_dot > 100:
            return short[:last_dot + 1].strip()
        return short + '...'
    return plain


def _is_junk_page(url: str, content: str) -> bool:
    """
    Identify if the page content is a sportsbook promotion, terms/privacy policy,
    or other junk boilerplate page instead of a real sports news article.
    """
    if not content:
        return True

    content_len = len(content.strip())
    if content_len < 200:
        return True

    content_lower = content.lower()
    url_lower = url.lower()

    # 1. Sportsbook landing/promo domains
    sportsbook_domains = [
        "fanduel.com/sportsbook", "sportsbook.fanduel.com",
        "draftkings.com/sportsbook", "sportsbook.draftkings.com",
        "betmgm.com", "pointsbet.com", "caesars.com/sportsbook",
        "betrivers.com", "bet365.com"
    ]
    if any(domain in url_lower for domain in sportsbook_domains):
        return True

    # 2. Check for sign-up promotion landing pages (not news articles)
    promo_keywords = [
        "promo code", "bonus bet", "bonus bets", "sign-up bonus", "signup bonus",
        "risk-free bet", "deposit match", "sign-up offer", "signup offer",
        "gambling problem? call", "1-800-gambler", "must be 21+", "must be 21 or older",
        "terms and conditions apply", "wagering requirements", "wager $5", "bet $5",
        "new customers only", "bonus code", "free bet", "free bets", "terms apply",
        "gambling problem", "first deposit", "exclusive offer", "play now",
        "t&cs apply", "t&c apply", "wagering", "deposing", "deposit match",
        "odds", "spread", "moneyline", "parlay", "parlays", "fanduel", "draftkings",
        "betmgm", "caesars sportsbook", "bet365", "betrivers"
    ]
    promo_matches = sum(1 for kw in promo_keywords if kw in content_lower)

    if promo_matches >= 6:
        return True
    if promo_matches >= 3 and content_len < 2000:
        return True
    if any(x in url_lower for x in ["promo", "bonus", "betting", "odds", "wagering"]) and promo_matches >= 2:
        return True

    # 3. Standard boilerplate fallback detection (in case readability failed and extracted nav/header garbage)
    boilerplate_indicators = [
        "cookie policy", "privacy policy", "terms of service", "terms of use",
        "all rights reserved", "contact us", "site map", "copyright", "feedback",
        "sign in", "create account", "forgot password", "log in"
    ]
    bp_matches = sum(1 for bp in boilerplate_indicators if bp in content_lower)
    if bp_matches >= 4 and content_len < 1000:
        return True

    return False


def extract_clean_article(html_or_markdown: str, url: str) -> str | None:
    """
    Extract clean article content, stripping boilerplate (nav/footer/headers/ads).
    Supports HTML (uses trafilatura / readability / BeautifulSoup fallback)
    and markdown (cleans up lines/blocks).
    """
    if not html_or_markdown:
        return None

    # Detect if the input is HTML
    is_html = (
        "<html>" in html_or_markdown or 
        "<body" in html_or_markdown or 
        "<div" in html_or_markdown or 
        "<p>" in html_or_markdown
    )

    if is_html:
        # Try 1: trafilatura
        try:
            import trafilatura
            extracted = trafilatura.extract(
                html_or_markdown,
                include_links=False,
                include_images=False,
                include_tables=False,
                no_fallback=False
            )
            if extracted and len(extracted.strip()) > 150:
                return extracted.strip()
        except ImportError:
            logger.info("trafilatura not installed, falling back to readability")
        except Exception as e:
            logger.warning(f"trafilatura extraction failed for {url}: {e}")

        # Try 2: readability-lxml
        try:
            from readability import Document
            from bs4 import BeautifulSoup
            doc = Document(html_or_markdown)
            summary_html = doc.summary()
            soup = BeautifulSoup(summary_html, "html.parser")
            
            # Decompose unwanted elements inside readability summary
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe"]):
                tag.decompose()
                
            extracted = soup.get_text(separator="\n").strip()
            # Collapse multiple newlines and spaces
            lines = [line.strip() for line in extracted.split("\n") if line.strip()]
            extracted = "\n\n".join(lines)
            if extracted and len(extracted) > 150:
                return extracted
        except ImportError:
            logger.info("readability-lxml not installed, falling back to BeautifulSoup")
        except Exception as e:
            logger.warning(f"readability extraction failed for {url}: {e}")

        # Try 3: BeautifulSoup Fallback
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_or_markdown, "html.parser")
            # Strip common boilerplate tags
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "iframe", "noscript"]):
                tag.decompose()
            # Decompose sportsbook and promo classes/ids
            for element in soup.find_all(class_=re.compile(r'sportsbook|betting|promo|footer|nav|share|social', re.I)):
                element.decompose()
            for element in soup.find_all(id=re.compile(r'sportsbook|betting|promo|footer|nav|share|social', re.I)):
                element.decompose()
                
            paragraphs = [p.get_text().strip() for p in soup.find_all("p")]
            extracted = "\n\n".join([p for p in paragraphs if len(p) > 40])
            if extracted and len(extracted) > 150:
                return extracted
        except Exception as e:
            logger.warning(f"BeautifulSoup fallback extraction failed for {url}: {e}")
    else:
        # It is Markdown (e.g. from Jina Reader)
        # Filter lines that look like nav/footer/ad boilerplate
        lines = html_or_markdown.split("\n")
        cleaned_lines = []
        for line in lines:
            line_strip = line.strip()
            if not line_strip:
                continue
            line_lower = line_strip.lower()
            # Skip typical navigation links and footer markers
            if any(term in line_lower for term in [
                "[terms & conditions]", "[privacy policy]", "[cookie policy]", "all rights reserved",
                "join fanduel", "promo code", "wager", "bonus bet", "sign-up bonus", "sportsbook promo",
                "click here to", "share this article", "follow us on", "read next", "related articles",
                "| contact us |", "bet $5 get", "bet $10 get", "fanduel sportsbook", "draftkings sportsbook"
            ]):
                continue
            cleaned_lines.append(line_strip)
        extracted = "\n\n".join(cleaned_lines)
        if len(extracted) > 150:
            return extracted

    return None
