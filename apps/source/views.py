import logging

from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from apps.feed.models import Source, FeedItem
from apps.feed.serializers import FeedItemCompactSerializer

from .models import UserCustomSource
from .serializers import SourceSuggestionSerializer, UserCustomSourceSerializer
from apps.sports_apis.services.ai_service import source_ai_service
from apps.nest.models import UserNest
from apps.entity.serializers import EntityCompactSerializer

logger = logging.getLogger(__name__)


class SourceFeedPagination(PageNumberPagination):
    """Pagination controller for source-specific article listings."""
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 50


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH — AI-powered
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_sources(request):
    """Search for sports news publications and auto-discover RSS feeds using AI suggestions.

    Rate-limited to 5 requests per minute with a 6-hour cache for query outputs.

    Args:
        request: Django HTTP request with query parameter `q`.

    Returns:
        Response: List of suggested publisher sources with existing subscription status.
    """
    query = request.GET.get('q', '').strip()
    if not query:
        return Response(
            {'error': 'Query parameter "q" is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(query) < 2:
        return Response(
            {'error': 'Query must be at least 2 characters'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Rate limit: 5 searches per user per minute
    rate_key = f"source_search_rate:{request.user.id}"
    search_count = cache.get(rate_key, 0)
    if search_count >= 5:
        return Response(
            {'error': 'Too many searches. Please wait a minute before trying again.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )
    cache.set(rate_key, search_count + 1, timeout=60)

    # Cache key per query (lowercase, normalized)
    cache_key = f"source_search:{query.lower().replace(' ', '_')}"
    suggestions = cache.get(cache_key)

    # Cache miss — call AI synchronously right now (wait for result)
    if suggestions is None:
        from apps.sports_apis.services.ai_service import source_ai_service
        suggestions = source_ai_service.suggest_sources(query)
        timeout = 6 * 3600 if suggestions else 1800
        cache.set(cache_key, suggestions or [], timeout=timeout)

    # Enrich with DB info (source_id, is_added) per specific source
    user_custom_sources = list(
        UserCustomSource.objects.filter(
            user=request.user, is_active=True
        ).select_related('source')
    )
    user_custom_source_ids = {ucs.source_id for ucs in user_custom_sources}
    user_custom_rss_urls = {ucs.source.rss_url for ucs in user_custom_sources if ucs.source and ucs.source.rss_url}
    user_custom_domains = {ucs.source.domain for ucs in user_custom_sources if ucs.source and ucs.source.domain}

    GENERIC_DOMAINS = {'youtube.com', 'youtu.be', 'news.google.com', 'google.com', 'twitter.com', 'x.com'}

    enriched = []
    for s in (suggestions or []):
        domain = s.get('domain', '').strip()
        name = s.get('name', '').strip()
        rss_url = s.get('rss_url', '').strip()
        source_id = s.get('source_id')

        clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
        is_generic = any(gd in clean_domain for gd in GENERIC_DOMAINS)

        # 1. Resolve source_id if not already present
        if not source_id:
            existing_source = None
            if rss_url:
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

    serializer = SourceSuggestionSerializer(enriched, many=True)
    return Response({
        'query': query,
        'count': len(enriched),
        'results': serializer.data,
        'status': 'ok',
    })



# ─────────────────────────────────────────────────────────────────────────────
# ADD SOURCE
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_source(request):
    """Add a custom publisher source to the authenticated user's active feed subscriptions.

    Triggers asynchronous RSS endpoint discovery and immediate article fetching.

    Args:
        request: Django HTTP request with payload containing domain/rss_url or source_id.

    Returns:
        Response: Created UserCustomSource instance and confirmation message.
    """
    source_id = request.data.get('source_id')
    domain = request.data.get('domain', '').strip()
    name = request.data.get('name', '').strip()
    rss_url = request.data.get('rss_url', '').strip()
    favicon_url = request.data.get('favicon_url', '').strip()
    search_query = request.data.get('search_query', '').strip()

    if not source_id and not domain:
        return Response(
            {'error': 'Either source_id or domain is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Get or create the Source object
    if source_id:
        source = get_object_or_404(Source, id=source_id)
        created = False
    else:
        # Normalize domain
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
            GENERIC_DOMAINS = {'youtube.com', 'youtu.be', 'news.google.com', 'google.com', 'twitter.com', 'x.com'}
            clean_domain = domain.replace('https://', '').replace('http://', '').split('/')[0].lower()
            if not any(gd in clean_domain for gd in GENERIC_DOMAINS):
                source = Source.objects.filter(domain=domain).first()

        # 4. Fourth attempt: If still not found, create a new one
        if not source:
            from django.db import IntegrityError
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
                # Handle concurrent creation or unexpected integrity clash
                if rss_url:
                    source = Source.objects.filter(rss_url=rss_url).first()
                if not source:
                    source = Source.objects.filter(domain=domain).first()
                if not source:
                    raise

        # If source existed but had no rss_url and we now have one, update it
        if not created and rss_url and not source.rss_url:
            from django.db import IntegrityError
            try:
                source.rss_url = rss_url
                source.save(update_fields=['rss_url'])
            except IntegrityError:
                # If this rss_url already exists under another source, use that source instead
                other_source = Source.objects.filter(rss_url=rss_url).first()
                if other_source:
                    source = other_source

        # If source existed but had no name, update it
        if not created and name and not source.name:
            source.name = name
            source.save(update_fields=['name'])

    # Link to user
    user_source, was_created = UserCustomSource.objects.get_or_create(
        user=request.user,
        source=source,
        defaults={
            'search_query': search_query,
            'is_active': True,
        }
    )

    if not was_created:
        # Re-activate if it was deactivated
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

    # Fire async task: discover RSS feed + poll immediately
    from .tasks import discover_and_poll_user_source
    discover_and_poll_user_source.delay(source.id)

    serializer = UserCustomSourceSerializer(user_source)
    return Response({
        'success': resp_status != status.HTTP_400_BAD_REQUEST,
        'message': message,
        'source': serializer.data,
        'created': was_created,
    }, status=resp_status)


# ─────────────────────────────────────────────────────────────────────────────
# LIST USER'S SOURCES
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_my_sources(request):
    """Retrieve all custom publication feeds manually followed by the authenticated user.

    Args:
        request: Django HTTP request.

    Returns:
        Response: List of user-followed custom sources and health metrics.
    """
    custom_sources = UserCustomSource.objects.filter(
        user=request.user,
        is_active=True,
    ).select_related('source').order_by('-created_at')

    serializer = UserCustomSourceSerializer(custom_sources, many=True)
    return Response({
        'count': custom_sources.count(),
        'sources': serializer.data,
    })


# ─────────────────────────────────────────────────────────────────────────────
# REMOVE SOURCE
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_source(request, source_id: int):
    """Unfollow and remove a custom publication source from the authenticated user's Nest feed.

    Args:
        request: Django HTTP request.
        source_id (int): Primary key of target Source.

    Returns:
        Response: Removal confirmation status.
    """
    deleted, _ = UserCustomSource.objects.filter(
        user=request.user,
        source_id=source_id,
    ).delete()

    if deleted:
        return Response({'success': True, 'message': 'Source removed'})

    return Response(
        {'error': 'Source not found in your sources'},
        status=status.HTTP_404_NOT_FOUND
    )


# ─────────────────────────────────────────────────────────────────────────────
# FORCE REFRESH
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def refresh_source(request, source_id: int):
    """Trigger an immediate background poll of an RSS feed. Rate-limited to once per 5 minutes.

    Args:
        request: Django HTTP request.
        source_id (int): Primary key of target Source.

    Returns:
        Response: Task execution confirmation or rate-limiting error.
    """
    # Check user owns this source
    user_source = get_object_or_404(
        UserCustomSource,
        user=request.user,
        source_id=source_id,
        is_active=True,
    )

    # Rate limit: 1 refresh per source per 5 minutes
    rate_key = f"source_refresh:{request.user.id}:{source_id}"
    if cache.get(rate_key):
        return Response(
            {'error': 'Please wait 5 minutes before refreshing this source again'},
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )

    cache.set(rate_key, True, timeout=300)

    from apps.feed.tasks import poll_single_source
    poll_single_source.delay(source_id)

    return Response({
        'success': True,
        'message': f'Refresh triggered for {user_source.source.name}',
    })


# ─────────────────────────────────────────────────────────────────────────────
# SOURCE FEED — items from one specific source
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_source_feed(request, source_id: int):
    """Retrieve news articles from a specific source, filtered to match entities followed in user's Nest.

    Args:
        request: Django HTTP request with pagination parameters (?page=1&limit=20).
        source_id (int): Primary key of target Source.

    Returns:
        Response: Paginated feed item list with contextual entity tagging.
    """
    # Verify user has this source
    user_source = get_object_or_404(
        UserCustomSource,
        user=request.user,
        source_id=source_id,
        is_active=True,
    )

    # Get user's nest entities
    user_entities = list(
        UserNest.objects.filter(user=request.user).values_list('entity_id', flat=True)
    )

    if not user_entities:
        # User has no entities in nest — return empty feed with helpful message
        return Response({
            'count': 0,
            'source': UserCustomSourceSerializer(user_source).data,
            'results': [],
            'message': 'Add entities to your nest to see relevant articles from this source.',
        })

    # Get all feed items from this source that have entities matching user's nest
    feed = FeedItem.objects.filter(
        source_id=source_id,
        entities__id__in=user_entities,  # Only articles about user's entities
    ).distinct().order_by('-published_at')

    paginator = SourceFeedPagination()
    paginated = paginator.paginate_queryset(feed, request)
    
    # Custom response: include matching entities for each item
    enriched_items = []
    for item in paginated:
        item_data = FeedItemCompactSerializer(item, context={'request': request}).data
        
        # Get entities that match user's nest for this item
        matching_entities = item.entities.filter(id__in=user_entities)
        item_data['matching_entities'] = EntityCompactSerializer(
            matching_entities, many=True
        ).data
        
        enriched_items.append(item_data)

    return Response({
        'count': feed.count(),
        'source': UserCustomSourceSerializer(user_source).data,
        'results': enriched_items,
        'message': f'Showing {len(enriched_items)} article(s) about your entities',
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def preview_source(request):
    """Validate a candidate publisher URL/name via AI discovery and return sample headlines and metadata.

    Args:
        request: Django HTTP request with JSON body `{"query": "espn.com"}`.

    Returns:
        Response: Preview metadata dictionary with recent headlines.
    """
    query = request.data.get('query', '').strip()
    if not query:
        return Response({'error': 'query is required'}, status=status.HTTP_400_BAD_REQUEST)

    # Check cache first
    cache_key = f"source_preview:{query.lower().replace(' ', '_').replace('/', '_')}"
    cached = cache.get(cache_key)
    
    if cached:
        # Still check is_added fresh from DB — that can change
        domain = cached.get('domain', '')
        existing_source = Source.objects.filter(domain=domain).first()
        cached['source_id'] = existing_source.id if existing_source else None
        cached['is_added'] = False
        if existing_source:
            cached['is_added'] = UserCustomSource.objects.filter(
                user=request.user,
                source=existing_source,
                is_active=True,
            ).exists()
        # Fresh headlines too
        cached['recent_headlines'] = []
        if existing_source:
            headlines = FeedItem.objects.filter(
                source=existing_source
            ).order_by('-published_at')[:5].values('title', 'url', 'published_at', 'thumbnail_url')
            cached['recent_headlines'] = list(headlines)
        return Response({'success': True, 'preview': cached, 'status': 'cached'})

    # Cache miss — call AI service
    preview = source_ai_service.preview_source(query)
    if not preview:
        return Response(
            {'error': 'Could not validate this source. Please check the URL or name and try again.'},
            status=status.HTTP_404_NOT_FOUND
        )

    # Cache the preview for 6 hours (domain/name won't change)
    cache.set(cache_key, preview, timeout=6 * 3600)

    # Dedup check
    domain = preview.get('domain', '')
    existing_source = Source.objects.filter(domain=domain).first()
    preview['source_id'] = existing_source.id if existing_source else None
    preview['is_added'] = False
    if existing_source:
        preview['is_added'] = UserCustomSource.objects.filter(
            user=request.user,
            source=existing_source,
            is_active=True,
        ).exists()

    # Recent headlines
    preview['recent_headlines'] = []
    if existing_source:
        headlines = FeedItem.objects.filter(
            source=existing_source
        ).order_by('-published_at')[:5].values('title', 'url', 'published_at', 'thumbnail_url')
        preview['recent_headlines'] = list(headlines)

    return Response({'success': True, 'preview': preview})