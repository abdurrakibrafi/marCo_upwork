import logging
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db import IntegrityError
from django.db.models import Q
from rest_framework import status

from apps.feed.models import Source, FeedItem
from apps.sports_apis.services.ai_service import source_ai_service
from .models import UserCustomSource

logger = logging.getLogger(__name__)

GENERIC_DOMAINS = {
    # Video & Streaming
    'youtube.com', 'youtu.be', 'vimeo.com', 'twitch.tv', 'tiktok.com',

    # Social & Community
    'twitter.com', 'x.com', 'reddit.com', 'redd.it', 'facebook.com', 'fb.com', 'instagram.com', 'threads.net',

    # Newsletters, Blogs & Self-Hosting platforms
    'substack.com', 'medium.com', 'beehiiv.com', 'wordpress.com', 'blogspot.com', 'ghost.io', 'patreon.com',

    # Search & Feed Aggregators
    'news.google.com', 'google.com', 'bing.com', 'feedburner.com', 'rss.app',

    # Podcasts & Audio
    'spotify.com', 'apple.com', 'anchor.fm', 'podbean.com', 'soundcloud.com', 'megaphone.fm',
}


def enrich_source_suggestions(user, suggestions: list) -> list:
    """Enrich AI suggested publisher sources with database existence and user subscription status.

    Args:
        user: Django User instance.
        suggestions (list): Raw suggestions from AI service.

    Returns:
        list: Enriched suggestions containing source_id and is_added flags.
    """
    if not suggestions:
        return []

    user_custom_sources = list(
        UserCustomSource.objects.filter(
            user=user, is_active=True
        ).select_related('source')
    )
    user_custom_source_ids = {ucs.source_id for ucs in user_custom_sources}
    user_custom_rss_urls = {ucs.source.rss_url for ucs in user_custom_sources if ucs.source and ucs.source.rss_url}
    user_custom_domains = {ucs.source.domain for ucs in user_custom_sources if ucs.source and ucs.source.domain}

    # Batch pre-fetch existing sources for suggestions that lack a source_id
    unresolved_rss_urls = [
        s['rss_url'].strip() for s in suggestions
        if not s.get('source_id') and s.get('rss_url') and s['rss_url'].strip()
    ]
    unresolved_domains = [
        s['domain'].strip() for s in suggestions
        if not s.get('source_id') and s.get('domain') and s['domain'].strip()
    ]
    unresolved_clean_domains = [
        s['domain'].replace('https://', '').replace('http://', '').split('/')[0].lower()
        for s in suggestions
        if not s.get('source_id') and s.get('domain') and s['domain'].strip()
    ]
    unresolved_names = [
        s['name'].strip() for s in suggestions
        if not s.get('source_id') and s.get('name') and s['name'].strip()
    ]

    candidate_sources = []
    source_batch_q = Q()
    if unresolved_rss_urls:
        source_batch_q |= Q(rss_url__in=unresolved_rss_urls)
    if unresolved_domains:
        source_batch_q |= Q(domain__in=unresolved_domains)
    for cd in set(unresolved_clean_domains):
        if cd:
            source_batch_q |= Q(domain__icontains=cd)
    if unresolved_names:
        for n in set(unresolved_names):
            if n:
                source_batch_q |= Q(name__iexact=n)

    if source_batch_q:
        candidate_sources = list(Source.objects.filter(source_batch_q))

    # Fast in-memory lookup indexes
    sources_by_rss = {src.rss_url: src for src in candidate_sources if src.rss_url}
    sources_by_domain = {src.domain: src for src in candidate_sources if src.domain}

    enriched = []
    for s in suggestions:
        domain = s.get('domain', '').strip()
        name = s.get('name', '').strip()
        rss_url = s.get('rss_url', '').strip()
        source_id = s.get('source_id')

        clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
        is_generic = any(gd in clean_domain for gd in GENERIC_DOMAINS)

        # 1. Resolve source_id if not already present (using pre-fetched batch index with fallback)
        if not source_id:
            existing_source = None
            if rss_url and rss_url in sources_by_rss:
                existing_source = sources_by_rss[rss_url]

            if not existing_source and name and domain:
                for src in candidate_sources:
                    if src.name and src.name.lower() == name.lower() and clean_domain in (src.domain or '').lower():
                        existing_source = src
                        break

            if not existing_source and not is_generic and domain:
                if domain in sources_by_domain:
                    existing_source = sources_by_domain[domain]
                else:
                    for src in candidate_sources:
                        if src.domain and (src.domain == domain or src.domain.lower() == clean_domain):
                            existing_source = src
                            break

            # Fallback direct query only if candidate batch somehow missed it
            if not existing_source and rss_url:
                existing_source = Source.objects.filter(rss_url=rss_url).first()
            if not existing_source and name and domain:
                existing_source = Source.objects.filter(name__iexact=name, domain__icontains=clean_domain).first()
            if not existing_source and not is_generic and domain:
                existing_source = Source.objects.filter(domain=domain).first()

            source_id = existing_source.id if existing_source else None

        s['source_id'] = source_id

        # 2. Check if this specific source is added by the user
        if source_id:
            s['is_added'] = source_id in user_custom_source_ids
        elif rss_url:
            s['is_added'] = rss_url in user_custom_rss_urls
        elif not is_generic:
            s['is_added'] = domain in user_custom_domains or clean_domain in user_custom_domains
        else:
            s['is_added'] = False

        enriched.append(s)

    return enriched


def add_user_custom_source(
    user,
    source_id: int | None = None,
    domain: str = '',
    name: str = '',
    rss_url: str = '',
    favicon_url: str = '',
    search_query: str = ''
) -> tuple[UserCustomSource, bool, str, int]:
    """Add or re-activate a custom publication source for the authenticated user.

    Handles duplicate matching, unique constraint recovery, user association,
    and triggers asynchronous feed discovery and immediate article fetching.

    Args:
        user: Django User instance.
        source_id (int | None): Existing Source ID if adding from known entities.
        domain (str): Source website domain.
        name (str): Source publication title.
        rss_url (str): Verified RSS feed endpoint.
        favicon_url (str): Favicon / publication icon URL.
        search_query (str): Optional search phrase used to discover the source.

    Returns:
        tuple[UserCustomSource, bool, str, int]: (user_source, was_created, message, http_status_code)
    """
    domain = domain.strip()
    name = name.strip()
    rss_url = rss_url.strip()
    favicon_url = favicon_url.strip()
    search_query = search_query.strip()

    if source_id:
        source = get_object_or_404(Source, id=source_id)
        created = False
    else:
        if not domain.startswith('http'):
            domain = f'https://{domain}'

        source = None
        created = False

        # 1. First attempt: Find by unique rss_url if provided
        if rss_url:
            source = Source.objects.filter(rss_url=rss_url).first()

        # 2. Second attempt: Find by name and domain if provided
        if not source and name:
            clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
            source = Source.objects.filter(name__iexact=name, domain__icontains=clean_domain).first()

        # 3. Third attempt: Find by domain if not a generic shared platform
        if not source:
            clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
            if not any(gd in clean_domain for gd in GENERIC_DOMAINS):
                source = Source.objects.filter(domain=domain).first()

        # 4. Fourth attempt: If still not found, create a new one
        if not source:
            try:
                source = Source.objects.create(
                    domain=domain,
                    name=name or domain,
                    rss_url=rss_url or None,
                    favicon_url=favicon_url,
                    is_active=True,
                    discovery_source='manual',
                )
                created = True
            except IntegrityError:
                if rss_url:
                    source = Source.objects.filter(rss_url=rss_url).first()
                if not source:
                    source = Source.objects.filter(domain=domain).first()
                if not source:
                    raise

        # If source existed but had no rss_url and we now have one, update it
        if not created and rss_url and not source.rss_url:
            try:
                source.rss_url = rss_url
                source.save(update_fields=['rss_url'])
            except IntegrityError:
                other_source = Source.objects.filter(rss_url=rss_url).first()
                if other_source:
                    source = other_source

        # If source existed but had no name, update it
        if not created and name and not source.name:
            source.name = name
            source.save(update_fields=['name'])

    user_source, was_created = UserCustomSource.objects.get_or_create(
        user=user,
        source=source,
        defaults={
            'search_query': search_query,
            'is_active': True,
        }
    )

    if not was_created:
        if not user_source.is_active:
            user_source.is_active = True
            user_source.save(update_fields=['is_active'])
            message = f'{source.name} re-added to your sources'
            resp_status = status.HTTP_200_OK
        else:
            message = f'{source.name} is already in your sources'
            resp_status = status.HTTP_400_BAD_REQUEST
    else:
        message = f'{source.name} added to your sources'
        resp_status = status.HTTP_201_CREATED

    from apps.source.tasks import discover_and_poll_user_source
    discover_and_poll_user_source.delay(source.id)

    return user_source, was_created, message, resp_status


def get_source_preview_data(user, query: str) -> tuple[dict | None, bool]:
    """Retrieve preview metadata for a source, checking cache, AI validation, and user subscription.

    Args:
        user: Django User instance.
        query (str): Website URL or publication title to validate.

    Returns:
        tuple[dict | None, bool]: (preview_dict_or_none, is_cached)
    """
    clean_query = query.strip()
    cache_key = f"source_preview:{clean_query.lower().replace(' ', '_').replace('/', '_')}"
    cached = cache.get(cache_key)

    def _resolve_existing_source(data: dict):
        domain = data.get('domain', '')
        rss_url = data.get('rss_url', '')
        clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
        is_generic = any(gd in clean_domain for gd in GENERIC_DOMAINS)

        existing = None
        if rss_url:
            existing = Source.objects.filter(rss_url=rss_url).first()
        if not existing and not is_generic and domain:
            existing = Source.objects.filter(domain=domain).first()
        return existing

    if cached:
        existing_source = _resolve_existing_source(cached)
        cached['source_id'] = existing_source.id if existing_source else None
        cached['is_added'] = False
        if existing_source:
            cached['is_added'] = UserCustomSource.objects.filter(
                user=user,
                source=existing_source,
                is_active=True,
            ).exists()
        cached['recent_headlines'] = []
        if existing_source:
            headlines = FeedItem.objects.filter(
                source=existing_source
            ).order_by('-published_at')[:5].values('title', 'url', 'published_at', 'thumbnail_url')
            cached['recent_headlines'] = list(headlines)
        return cached, True

    preview = source_ai_service.preview_source(clean_query)
    if not preview:
        return None, False

    cache.set(cache_key, preview, timeout=6 * 3600)

    existing_source = _resolve_existing_source(preview)
    preview['source_id'] = existing_source.id if existing_source else None
    preview['is_added'] = False
    if existing_source:
        preview['is_added'] = UserCustomSource.objects.filter(
            user=user,
            source=existing_source,
            is_active=True,
        ).exists()

    preview['recent_headlines'] = []
    if existing_source:
        headlines = FeedItem.objects.filter(
            source=existing_source
        ).order_by('-published_at')[:5].values('title', 'url', 'published_at', 'thumbnail_url')
        preview['recent_headlines'] = list(headlines)

    return preview, False
