import logging

from django.shortcuts import get_object_or_404
from django.core.cache import cache
from django.db.models import Prefetch
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from apps.feed.models import FeedItem
from apps.feed.serializers import FeedItemCompactSerializer
from apps.nest.models import UserNest
from apps.entity.models import Entity
from apps.entity.serializers import EntityCompactSerializer
from apps.sports_apis.services.ai_service import source_ai_service

from .models import UserCustomSource
from .serializers import SourceSuggestionSerializer, UserCustomSourceSerializer
from .services import (
    enrich_source_suggestions,
    add_user_custom_source,
    get_source_preview_data,
)

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

    # Cache miss — call AI synchronously
    if suggestions is None:
        suggestions = source_ai_service.suggest_sources(query)
        timeout = 6 * 3600 if suggestions else 1800
        cache.set(cache_key, suggestions or [], timeout=timeout)

    # Delegate database enrichment and subscription status to service layer
    enriched = enrich_source_suggestions(request.user, suggestions or [])
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

    user_source, was_created, message, resp_status = add_user_custom_source(
        user=request.user,
        source_id=source_id,
        domain=domain,
        name=name,
        rss_url=rss_url,
        favicon_url=favicon_url,
        search_query=search_query,
    )

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
        return Response({
            'count': 0,
            'source': UserCustomSourceSerializer(user_source).data,
            'results': [],
            'message': 'Add entities to your nest to see relevant articles from this source.',
        })

    # Get all feed items from this source that have entities matching user's nest
    # Optimized: Prefetch matching entities into to_attr to eliminate N+1 SQL queries per item
    matching_entities_prefetch = Prefetch(
        'entities',
        queryset=Entity.objects.filter(id__in=user_entities),
        to_attr='matching_user_entities',
    )
    feed = FeedItem.objects.filter(
        source_id=source_id,
        entities__id__in=user_entities,
    ).prefetch_related(matching_entities_prefetch).distinct().order_by('-published_at')

    paginator = SourceFeedPagination()
    paginated = paginator.paginate_queryset(feed, request)

    enriched_items = []
    for item in paginated:
        item_data = FeedItemCompactSerializer(item, context={'request': request}).data
        matching_entities = getattr(item, 'matching_user_entities', [])
        item_data['matching_entities'] = EntityCompactSerializer(
            matching_entities, many=True
        ).data
        enriched_items.append(item_data)

    total_count = (
        paginator.page.paginator.count
        if (paginator.page and paginator.page.paginator)
        else feed.count()
    )

    return Response({
        'count': total_count,
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

    preview, is_cached = get_source_preview_data(request.user, query)
    if not preview:
        return Response(
            {'error': 'Could not validate this source. Please check the URL or name and try again.'},
            status=status.HTTP_404_NOT_FOUND
        )

    response_data = {'success': True, 'preview': preview}
    if is_cached:
        response_data['status'] = 'cached'

    return Response(response_data)