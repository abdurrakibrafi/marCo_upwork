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

logger = logging.getLogger(__name__)


class SourceFeedPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 50


# ─────────────────────────────────────────────────────────────────────────────
# SEARCH — AI-powered
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def search_sources(request):
    """
    GET /api/source/search/?q=ESPN football
    
    Uses OpenAI to suggest sources, then validates each with RSS discovery.
    Results are cached for 6 hours per query to save API costs.
    
    Returns list of source suggestions with:
    - name, domain, description, favicon_url
    - rss_url (if found), has_rss
    - source_id (if already in DB), is_added (if user already added it)
    - tags
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
    cached = cache.get(cache_key)

    # If we have cached results, return immediately — NO rate limit check needed
    # Cache hit means no API calls will be made, so no reason to throttle
    if cached is not None:
        suggestions = cached

        user_custom_domains = set(
            UserCustomSource.objects.filter(
                user=request.user, is_active=True
            ).values_list('source__domain', flat=True)
        )

        enriched = []
        for s in suggestions:
            domain = s.get('domain', '')
            existing_source = Source.objects.filter(domain=domain).first()
            s['source_id'] = existing_source.id if existing_source else None
            s['is_added'] = domain in user_custom_domains
            enriched.append(s)

        serializer = SourceSuggestionSerializer(enriched, many=True)
        return Response({
            'query': query,
            'count': len(enriched),
            'results': serializer.data,
            'status': 'cached',
        })

    # Only apply rate limit when cache MISSES — meaning we'll actually hit OpenAI
    rate_key = f"source_search_rate:{request.user.id}"
    search_count = cache.get(rate_key, 0)
    if search_count >= 10:  # raised from 5 to 10 per minute
        return Response(
            {'error': 'Too many searches. Please wait a minute before trying again.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )
    cache.set(rate_key, search_count + 1, timeout=60)

    # Cache miss — trigger async generation
    from .tasks import generate_source_suggestions
    generate_source_suggestions.delay(query, cache_key)

    return Response({
        'query': query,
        'count': 0,
        'results': [],
        'message': 'Generating suggestions... Please try again in a few seconds.',
        'status': 'processing',
    })

# ─────────────────────────────────────────────────────────────────────────────
# ADD SOURCE
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def add_source(request):
    """
    POST /api/source/add/
    Body: {
        "domain": "https://www.espn.com",
        "name": "ESPN",                     (optional)
        "rss_url": "https://...",           (optional, if client already knows it)
        "favicon_url": "https://...",       (optional)
        "search_query": "ESPN football"     (optional, for analytics)
    }
    
    OR if source is already in DB:
    Body: {"source_id": 123, "search_query": "ESPN football"}
    
    Creates UserCustomSource. Triggers async RSS discovery + polling.
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
    else:
        # Normalize domain
        if not domain.startswith('http'):
            domain = f'https://{domain}'

        source, created = Source.objects.get_or_create(
            domain=domain,
            defaults={
                'name': name or domain,
                'rss_url': rss_url or None,
                'favicon_url': favicon_url,
                'is_active': True,
                'discovery_source': 'manual',
            }
        )

        # If source existed but had no rss_url and we now have one, update it
        if not created and rss_url and not source.rss_url:
            source.rss_url = rss_url
            source.save(update_fields=['rss_url'])

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
        else:
            message = f'{source.name} is already in your sources'
    else:
        message = f'{source.name} added to your sources'

    # Fire async task: discover RSS feed + poll immediately
    from .tasks import discover_and_poll_user_source
    discover_and_poll_user_source.delay(source.id)

    serializer = UserCustomSourceSerializer(user_source)
    return Response({
        'success': True,
        'message': message,
        'source': serializer.data,
        'created': was_created,
    }, status=status.HTTP_201_CREATED if was_created else status.HTTP_200_OK)


# ─────────────────────────────────────────────────────────────────────────────
# LIST USER'S SOURCES
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_my_sources(request):
    """
    GET /api/source/my/
    
    Returns all sources the user has manually added.
    Includes health status, last polled time, and failure count.
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
def remove_source(request, source_id):
    """
    DELETE /api/source/<source_id>/remove/
    
    Removes the source from the user's custom sources.
    Does NOT delete the Source object itself (other users may use it).
    The source's items will no longer appear in this user's nest feed.
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
def refresh_source(request, source_id):
    """
    POST /api/source/<source_id>/refresh/
    
    Force re-polls the RSS feed for this source.
    Useful if user wants to manually refresh a source.
    Rate-limited: 1 refresh per source per 5 minutes.
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
def get_source_feed(request, source_id):
    """
    GET /api/source/<source_id>/feed/
    
    Returns feed items from a specific source the user has added.
    Supports pagination: ?page=1&limit=20
    """
    # Verify user has this source
    get_object_or_404(
        UserCustomSource,
        user=request.user,
        source_id=source_id,
        is_active=True,
    )

    feed = FeedItem.objects.filter(
        source_id=source_id,
    ).order_by('-published_at')

    paginator = SourceFeedPagination()
    paginated = paginator.paginate_queryset(feed, request)
    serializer = FeedItemCompactSerializer(paginated, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def preview_source(request):
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