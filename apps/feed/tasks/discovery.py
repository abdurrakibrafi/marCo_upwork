import logging
import urllib.parse
from celery import shared_task

from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.brave import brave_service
from apps.sports_apis.services.rss import rss_discovery_service
from .helpers import _extract_domain, _entity_matches_text

logger = logging.getLogger(__name__)


@shared_task(name='apps.feed.tasks.discover_rss_feeds_for_entity', bind=True, max_retries=1)
def discover_rss_feeds_for_entity(self, entity_id: int):
    """Discover official and news publisher domains for an entity using Brave Search and extract RSS feeds.

    Args:
        self: Bound Celery task.
        entity_id (int): Primary key of the Entity.

    Returns:
        str: Task execution summary message.
    """
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


@shared_task(name='apps.feed.tasks.extract_rss_from_domain', bind=True, max_retries=1)
def extract_rss_from_domain(self, entity_id: int, domain: str):
    """Scrape and discover RSS/Atom feed URLs associated with a domain.

    Args:
        self: Bound Celery task.
        entity_id (int): Primary key of the Entity.
        domain (str): Web domain to inspect for feeds.

    Returns:
        str: Discovery summary message.
    """
    feeds = rss_discovery_service.discover_feeds_for_domain(domain)
    for feed_url in feeds:
        store_validated_feed.delay(entity_id, feed_url, discovery_source='brave')
    return f"Found {len(feeds)} feeds for {domain}"


@shared_task(name='apps.feed.tasks.store_validated_feed', bind=True, max_retries=1)
def store_validated_feed(self, entity_id: int, feed_url: str, discovery_source: str = 'brave'):
    """Validate discovered RSS feed URL and link it to the target entity in the database.

    Args:
        self: Bound Celery task.
        entity_id (int): Primary key of the Entity.
        feed_url (str): Discovered RSS feed URL.
        discovery_source (str, optional): Discovery origin tag. Defaults to 'brave'.

    Returns:
        str: Persisted source status.
    """
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
    from .polling import poll_single_source
    poll_single_source.delay(source.id)
    return f"Stored source {source.id} for {entity.name} ({'created' if created else 'updated'})"


@shared_task(name='apps.feed.tasks.ensure_entity_has_rss_source')
def ensure_entity_has_rss_source(entity_id: int):
    """Ensure that an entity has guaranteed fallback Google News and YouTube RSS sources created and polled.

    Also backfills any existing unlinked orphan feed items matching the entity.

    Args:
        entity_id (int): Primary key of the Entity.

    Returns:
        str: Status report on source creation and orphan item linking.
    """
    try:
        entity = Entity.objects.get(id=entity_id, is_active=True)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"

    from apps.entity.utils.matcher import is_national_team

    # Build a sport-aware Google News query for ALL entities (not just national teams).
    _SPORT_QUERY_MAP = {
        'soccer':             'soccer OR football OR FIFA OR UEFA OR "Premier League" OR "La Liga" OR "Serie A" OR "Bundesliga" OR "Ligue 1"',
        'football':           'NFL OR "American football" OR touchdown OR quarterback',
        'american_football':  'NFL OR "American football" OR touchdown OR quarterback',
        'basketball':         'NBA OR basketball OR FIBA',
        'baseball':           'MLB OR baseball',
        'ice_hockey':         'NHL OR hockey OR "ice hockey"',
        'hockey':             'NHL OR hockey OR "ice hockey"',
        'cricket':            'cricket OR ICC OR IPL OR "Test match" OR ODI OR T20',
        'tennis':             'tennis OR ATP OR WTA OR "Grand Slam" OR Wimbledon OR "US Open" OR "French Open" OR "Australian Open"',
        'rugby':              'rugby OR "Six Nations" OR "Rugby World Cup" OR RWC',
        'f1':                 'F1 OR "Formula 1" OR "Formula One" OR "Grand Prix" OR FIA',
        'formula1':           'F1 OR "Formula 1" OR "Formula One" OR "Grand Prix" OR FIA',
        'golf':               'golf OR PGA OR "Masters" OR "US Open golf" OR "The Open Championship"',
        'mma':                'MMA OR UFC OR "mixed martial arts"',
        'combat_sports':      'MMA OR UFC OR boxing OR "combat sports"',
        'motorsports':        'motorsport OR racing OR NASCAR OR "Formula E"',
        'volleyball':         'volleyball OR VNL OR FIVB',
        'handball':           'handball OR IHF',
    }

    raw_sport = (entity.sport or '').strip().lower()
    sport_keywords = _SPORT_QUERY_MAP.get(raw_sport, '')

    if sport_keywords:
        query_str = f'"{entity.name}" AND ({sport_keywords})'
    else:
        query_str = f'"{entity.name}"'

    query = urllib.parse.quote(query_str)
    google_news_url = f"https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"

    sport_term = raw_sport

    source, created = Source.objects.get_or_create(
        rss_url=google_news_url,
        defaults={
            'name': f'Google News - {entity.name}',
            'domain': 'news.google.com',
            'is_active': True,
            'discovery_source': 'known',
        }
    )
    if not created:
        source.is_active = True
        source.poll_failures = 0
        source.save(update_fields=['is_active', 'poll_failures'])
    source.entities.add(entity)

    # ── YouTube Video RSS source for video feeds ──
    if is_national_team(entity.name) and sport_term and sport_term != 'none':
        video_query_str = f'"{entity.name}" AND ({sport_term}) site:youtube.com'
    else:
        video_query_str = f'"{entity.name}" site:youtube.com'
    query_video = urllib.parse.quote(video_query_str)
    google_video_url = f"https://news.google.com/rss/search?q={query_video}&hl=en&gl=US&ceid=US:en"

    if is_national_team(entity.name):
        old_sources = Source.objects.filter(
            entities=entity,
            rss_url__icontains="news.google.com",
            is_active=True
        ).exclude(
            rss_url__in=[google_news_url, google_video_url]
        )
        for old_source in old_sources:
            old_source.is_active = False
            old_source.save(update_fields=['is_active'])

    from .polling import poll_single_source
    poll_single_source.delay(source.id)

    video_source = Source.objects.filter(rss_url=google_video_url).first()

    if not video_source:
        video_source = Source.objects.filter(
            entities=entity,
            domain='youtube.com'
        ).first()

    if not video_source:
        video_source = Source.objects.create(
            rss_url=google_video_url,
            name=f'YouTube Video - {entity.name}',
            domain='youtube.com',
            is_active=True,
            discovery_source='known',
        )
    else:
        if video_source.rss_url != google_video_url:
            video_source.rss_url = google_video_url
        video_source.is_active = True
        video_source.poll_failures = 0
        video_source.save(update_fields=['rss_url', 'is_active', 'poll_failures'])

    video_source.entities.add(entity)
    poll_single_source.delay(video_source.id)

    linked = 0
    for item in FeedItem.objects.filter(entities__isnull=True).iterator():
        text = f"{item.title} {item.summary or ''}".lower()
        if _entity_matches_text(entity, text):
            item.entities.add(entity)
            linked += 1

    logger.info(f"ensure_entity_has_rss_source: {entity.name}, source={'created' if created else 'linked'}, backfilled={linked}")
    return f"Google News RSS {'created' if created else 'linked'} for {entity.name}, {linked} orphan items backfilled"
