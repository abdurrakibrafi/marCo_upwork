"""
apps/source/tasks.py

Celery tasks triggered when a user adds a custom source.
We reuse the existing RSS discovery + polling infrastructure from apps/feed/tasks.py.
"""

import logging
from celery import shared_task
from urllib.parse import urlparse

from apps.sports_apis.services.rss import rss_discovery_service, rss_polling_service

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=1)
def generate_source_suggestions(self, query: str, cache_key: str):
    """
    Generate AI-powered source suggestions asynchronously.
    Stores results in cache with the given key.
    """
    try:
        from apps.sports_apis.services.ai_service import source_ai_service
        from django.core.cache import cache
        
        logger.info(f"Generating source suggestions for query: {query}")
        
        # Generate suggestions (this calls APIs)
        suggestions = source_ai_service.suggest_sources(query)
        
        if suggestions:
            # Cache for 6 hours
            cache.set(cache_key, suggestions, timeout=6 * 3600)
            logger.info(f"Cached {len(suggestions)} suggestions for query: {query}")
            return f"Generated {len(suggestions)} suggestions for '{query}'"
        else:
            # Cache empty result for 1 hour to avoid repeated failed attempts
            cache.set(cache_key, [], timeout=1800)
            logger.info(f"No suggestions found for query: {query}")
            return f"No suggestions found for '{query}'"
            
    except Exception as e:
        logger.error(f"Failed to generate suggestions for '{query}': {e}")
        return f"Error generating suggestions for '{query}': {e}"


@shared_task(bind=True, max_retries=2)
def discover_and_poll_user_source(self, source_id: int):
    """
    Called right after a user adds a custom source.
    1. If source has no rss_url, run discovery on its domain
    2. Poll the feed to populate initial articles
    3. Update the Source record with the found rss_url
    """
    from apps.feed.models import Source

    try:
        source = Source.objects.get(id=source_id)
    except Source.DoesNotExist:
        return f"Source {source_id} not found"

    # Step 1: Discover RSS if not already set
    if not source.rss_url and source.domain:
        logger.info(f"Discovering RSS feeds for domain: {source.domain}")
        feeds = rss_discovery_service.discover_feeds_for_domain(source.domain)
        if feeds:
            source.rss_url = feeds[0]
            source.save(update_fields=['rss_url'])
            logger.info(f"Found RSS feed for {source.domain}: {feeds[0]}")
        else:
            logger.warning(f"No RSS feed found for {source.domain}")
            return f"No RSS feed found for {source.domain}"

    # Step 2: Poll the feed immediately to get initial articles
    if source.rss_url:
        from apps.feed.tasks import poll_single_source
        poll_single_source.delay(source.id)
        return f"Discovery + poll triggered for source {source_id} ({source.domain})"

    return f"Source {source_id} has no RSS url after discovery"


@shared_task
def poll_user_custom_sources(user_id: int):
    """
    Force-poll all sources a specific user has added.
    Can be called when user opens the Source screen to get fresh data.
    """
    from .models import UserCustomSource
    from apps.feed.tasks import poll_single_source

    custom_sources = UserCustomSource.objects.filter(
        user_id=user_id,
        is_active=True,
        source__rss_url__isnull=False,
    ).exclude(source__rss_url='').select_related('source')

    count = 0
    for cs in custom_sources:
        poll_single_source.delay(cs.source_id)
        count += 1

    return f"Triggered polling for {count} custom sources for user {user_id}"