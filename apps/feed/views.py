from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.db.models import Q
from apps.feed.models import FeedItem, Source, UserSource, HiddenSource, Bookmark, Like
from apps.feed.serializers import (
    FeedItemSerializer, FeedItemCompactSerializer,
    SourceSerializer, UserSourceSerializer, AddSourceSerializer, BookmarkSerializer, LikeSerializer
)
from apps.nest.models import UserNest
from apps.entity.models import Entity
from apps.core.utils.mixins import BaseResponseMixin
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle
from django.core.cache import cache
import time


class DetailedFeedItemUserThrottle(UserRateThrottle):
    # rate = '15/minute'
    pass


class DetailedFeedItemAnonThrottle(AnonRateThrottle):
    # rate = '5/minute'
    pass


class FeedPagination(PageNumberPagination):
    page_size = 30
    page_size_query_param = 'limit'
    max_page_size = 50


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_feed(request):
    """
    Get aggregated feed for user's nest
    GET /api/feed/nest?page=1&limit=10&sort=newest&filter=breaking

    filter values: breaking, trending
    sort values: newest, oldest, popular, trending

    Response:
    {
      "count": <total>,
      "next": <url>,
      "previous": <url>,
      "results": [ ... ]
    }
    """
    # Get user's nest entities
    nest_entities = UserNest.objects.filter(
        user=request.user
    ).values_list('entity', flat=True)
    
    if not nest_entities:
        return Response({
            'message': 'No entities in your nest',
            'count': 0,
            'next': None,
            'previous': None,
            'results': []
        })
    
    # Get hidden sources and publishers
    hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
    hidden_source_ids = hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True)
    hidden_publishers = hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True)
 
    # Get user's manually added custom sources
    from apps.source.models import UserCustomSource
    user_custom_source_ids = UserCustomSource.objects.filter(
        user=request.user,
        is_active=True,
    ).values_list('source_id', flat=True)
 
    # Base queryset: entity-based feed OR custom source feed — unified
    feed = FeedItem.objects.filter(
        Q(entities__in=nest_entities) |
        Q(source_id__in=user_custom_source_ids)
    ).exclude(
        source_id__in=hidden_source_ids
    ).exclude(
        publisher_name__in=hidden_publishers
    ).select_related('source').prefetch_related('entities').distinct()

    
    # Apply filters
    raw_filters = request.GET.getlist('filter') or []
    # support comma-separated string too
    if not raw_filters:
        raw_filter_value = request.GET.get('filter')
        if raw_filter_value:
            raw_filters = [v.strip() for v in raw_filter_value.split(',') if v.strip()]

    filters = [v.lower() for v in raw_filters]

    # entity type filters (Teams/Athletes/Leagues)
    entity_type_map = {
        'teams': 'team',
        'athletes': 'athlete',
        'leagues': 'league',
    }
    selected_entity_types = [entity_type_map.get(f) for f in filters if f in entity_type_map]
    if selected_entity_types:
        feed = feed.filter(entities__type__in=selected_entity_types)

    # content type filters (News/Videos/Articles)
    if 'videos' in filters:
        feed = feed.filter(source__domain__icontains='youtube')
    if 'news' in filters or 'articles' in filters:
        # Exclude explicit Video source domain as a proxy
        feed = feed.exclude(source__domain__icontains='youtube')

    # existing feed flags
    if 'breaking' in filters:
        feed = feed.filter(is_breaking=True)
    if 'trending' in filters:
        feed = feed.filter(is_trending=True)

    source_id = request.GET.get('source_id')
    if source_id:
        feed = feed.filter(source_id=source_id)

    q = request.GET.get('q', '').strip()
    if q:
        feed = feed.filter(
            Q(title__icontains=q) | Q(summary__icontains=q)
        )

    # Apply sorting
    sort = request.GET.get('sort', 'newest')
    if sort == 'newest':
        feed = feed.order_by('-published_at')
    elif sort == 'oldest':
        feed = feed.order_by('published_at')
    elif sort == 'popular':
        feed = feed.order_by('-views', '-published_at')
    elif sort == 'trending':
        feed = feed.filter(is_trending=True).order_by('-views', '-published_at')
    else:
        # fallback safe order
        feed = feed.order_by('-published_at')
    
    # Paginate
    paginator = FeedPagination()
    paginated_feed = paginator.paginate_queryset(feed, request)
    
    serializer = FeedItemCompactSerializer(paginated_feed, many=True, context={'request': request})
    
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_feed(request, entity_id):
    """
    Get feed for a specific entity
    GET /api/feed/entity/{entity_id}?page=1
    """
    entity = get_object_or_404(Entity, id=entity_id)
    
    # Get hidden sources and publishers if user is authenticated
    hidden_source_ids = []
    hidden_publishers = []
    if request.user.is_authenticated:
        hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
        hidden_source_ids = hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True)
        hidden_publishers = hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True)
    
    feed = FeedItem.objects.filter(
        entities=entity
    ).exclude(
        source_id__in=hidden_source_ids
    ).exclude(
        publisher_name__in=hidden_publishers
    ).select_related('source').prefetch_related('entities').order_by('-published_at').distinct()

    
    # Paginate
    paginator = FeedPagination()
    paginated_feed = paginator.paginate_queryset(feed, request)
    
    serializer = FeedItemCompactSerializer(paginated_feed, many=True, context={'request': request})
    
    return paginator.get_paginated_response(serializer.data)


@api_view(['GET'])
@permission_classes([AllowAny])
@throttle_classes([DetailedFeedItemUserThrottle, DetailedFeedItemAnonThrottle])
def get_feed_item(request, item_id):
    """
    Get detailed feed item
    GET /api/feed/item/{item_id}
    """
    feed_item = get_object_or_404(
        FeedItem.objects.select_related('source').prefetch_related('entities'),
        id=item_id
    )

    # Lazily fetch full content and summary if not already fetched
    if not feed_item.content_fetched:
        lock_key = f"fetching_article:{feed_item.id}"
        # Acquire lock to prevent duplicate concurrent scrapes for this article
        if cache.add(lock_key, True, timeout=20):
            try:
                from .tasks import fetch_article_content
                fetch_article_content(feed_item.id)
                feed_item.refresh_from_db()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(
                    f"Sync article fetch failed for item {feed_item.id}: {exc}"
                )
            finally:
                cache.delete(lock_key)
        else:
            # Another request is fetching. Wait up to 5 seconds.
            for _ in range(10):
                time.sleep(0.5)
                feed_item.refresh_from_db()
                if feed_item.content_fetched:
                    break
    
    # Track view
    if request.user.is_authenticated:
        # View tracking removed - FeedItemView model not implemented
        pass
    
    # Increment view count
    feed_item.views += 1
    feed_item.save(update_fields=['views'])
    
    serializer = FeedItemSerializer(feed_item)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hide_source(request):
    """
    Hide a source or publisher from user's feeds
    POST /api/feed/source/hide
    Body: {"feed_item_id": 123} OR {"publisher_name": "BBC"} OR {"source_id": 123}
    """
    feed_item_id = request.data.get('feed_item_id')
    publisher_name = request.data.get('publisher_name', '').strip()
    source_id = request.data.get('source_id')
    
    if not feed_item_id and not publisher_name and not source_id:
        return Response(
            {'error': 'At least one of feed_item_id, publisher_name, or source_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    # If feed_item_id is provided, resolve to the entire source_id to hide all articles from that source
    if feed_item_id:
        feed_item = get_object_or_404(FeedItem, id=feed_item_id)
        source_id = feed_item.source_id
            
    if publisher_name:
        hidden, created = HiddenSource.objects.get_or_create(
            user=request.user,
            publisher_name=publisher_name
        )
        message = f'{publisher_name} has been hidden from your feeds' if created else f'{publisher_name} was already hidden'
    else:
        source = get_object_or_404(Source, id=source_id)
        hidden, created = HiddenSource.objects.get_or_create(
            user=request.user,
            source=source
        )
        message = f'{source.name} has been hidden from your feeds' if created else f'{source.name} was already hidden'
        
    return Response({
        'success': True,
        'message': message
    }, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def unhide_source(request):
    """
    Unhide a source or publisher
    POST /api/feed/source/unhide
    Body: {"feed_item_id": 123} OR {"publisher_name": "BBC"} OR {"source_id": 123}
    """
    feed_item_id = request.data.get('feed_item_id')
    publisher_name = request.data.get('publisher_name', '').strip()
    source_id = request.data.get('source_id')
    
    if not feed_item_id and not publisher_name and not source_id:
        return Response(
            {'error': 'At least one of feed_item_id, publisher_name, or source_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
        
    if feed_item_id:
        feed_item = get_object_or_404(FeedItem, id=feed_item_id)
        source_id = feed_item.source_id

    if publisher_name:
        deleted_count = HiddenSource.objects.filter(
            user=request.user,
            publisher_name=publisher_name
        ).delete()[0]
        name_str = publisher_name
    else:
        deleted_count = HiddenSource.objects.filter(
            user=request.user,
            source_id=source_id
        ).delete()[0]
        name_str = f"Source {source_id}"
        
    if deleted_count > 0:
        return Response({
            'success': True,
            'message': f'{name_str} has been unhidden'
        })
    else:
        return Response({
            'success': False,
            'message': f'{name_str} was not hidden'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_hidden_sources(request):
    """
    Get list of user's hidden sources and publishers
    GET /api/feed/sources/hidden
    """
    hidden = HiddenSource.objects.filter(
        user=request.user
    ).select_related('source')
    
    sources = [h.source for h in hidden if h.source]
    publishers = [h.publisher_name for h in hidden if h.publisher_name]
    
    serializer = SourceSerializer(sources, many=True)
    
    return Response({
        'count_sources': len(sources),
        'sources': serializer.data,
        'count_publishers': len(publishers),
        'publishers': publishers
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def trigger_feed_update(request, entity_id):
    """
    Manually trigger feed update for an entity
    POST /api/feed/entity/{entity_id}/update
    """
    entity = get_object_or_404(Entity, id=entity_id)
    
    from .tasks import update_all_entity_feeds
    update_all_entity_feeds.delay(entity_id)
    
    return Response({
        'success': True,
        'message': f'Feed update triggered for {entity.name}'
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_breaking_news(request):
    """
    Get breaking news across all sports
    GET /api/feed/breaking
    """
    hidden_source_ids = []
    hidden_publishers = []
    if request.user.is_authenticated:
        hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
        hidden_source_ids = hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True)
        hidden_publishers = hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True)

    feed = FeedItem.objects.filter(
        is_breaking=True
    ).exclude(
        source_id__in=hidden_source_ids
    ).exclude(
        publisher_name__in=hidden_publishers
    ).select_related('source').prefetch_related('entities').order_by('-published_at')[:50]
    
    serializer = FeedItemCompactSerializer(feed, many=True, context={'request': request})
    
    return Response({
        'count': len(feed),
        'items': serializer.data
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_trending_feed(request):
    """
    Get trending content
    GET /api/feed/trending
    """
    hidden_source_ids = []
    hidden_publishers = []
    if request.user.is_authenticated:
        hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
        hidden_source_ids = hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True)
        hidden_publishers = hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True)

    feed = FeedItem.objects.filter(
        is_trending=True
    ).exclude(
        source_id__in=hidden_source_ids
    ).exclude(
        publisher_name__in=hidden_publishers
    ).select_related('source').prefetch_related('entities').order_by('-views', '-published_at')[:50]
    
    serializer = FeedItemCompactSerializer(feed, many=True, context={'request': request})
    
    return Response({
        'count': len(feed),
        'items': serializer.data
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_bookmark(request):
    """
    Bookmark or un-bookmark a feed item (toggle).
    POST /api/feed/bookmark/
    Body: {"feed_item_id": 123}
 
    Returns:
      {"bookmarked": true}  — item was just bookmarked
      {"bookmarked": false} — bookmark was removed
    """
    feed_item_id = request.data.get('feed_item_id')
 
    if not feed_item_id:
        return Response(
            {'error': 'feed_item_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
 
    feed_item = get_object_or_404(FeedItem, id=feed_item_id)
 
    bookmark = Bookmark.objects.filter(user=request.user, feed_item=feed_item).first()
 
    if bookmark:
        bookmark.delete()
        return Response({'bookmarked': False}, status=status.HTTP_200_OK)
    else:
        Bookmark.objects.create(user=request.user, feed_item=feed_item)
        return Response({'bookmarked': True}, status=status.HTTP_201_CREATED)
 
 
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_bookmarks(request):
    """
    Get all bookmarked feed items for the current user.
    GET /api/feed/bookmarks/
 
    Supports pagination: ?page=1&limit=20
    """
    bookmarks = (
        Bookmark.objects
        .filter(user=request.user)
        .select_related('feed_item', 'feed_item__source')
        .prefetch_related('feed_item__entities')
    )
 
    paginator = FeedPagination()
    paginated = paginator.paginate_queryset(bookmarks, request)
    serializer = BookmarkSerializer(paginated, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)
 
 
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_bookmark(request, feed_item_id):
    """
    Remove a specific bookmark.
    DELETE /api/feed/bookmarks/{feed_item_id}/
    """
    deleted, _ = Bookmark.objects.filter(
        user=request.user,
        feed_item_id=feed_item_id,
    ).delete()
 
    if deleted:
        return Response({'success': True}, status=status.HTTP_200_OK)
    return Response(
        {'error': 'Bookmark not found'},
        status=status.HTTP_404_NOT_FOUND
    )
 

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def toggle_like(request):
    """
    Like or unlike a feed item (toggle).
    POST /api/feed/like/
    Body: {"feed_item_id": 123}
 
    Returns:
      {"liked": true,  "like_count": 42}
      {"liked": false, "like_count": 41}
    """
    feed_item_id = request.data.get('feed_item_id')
 
    if not feed_item_id:
        return Response(
            {'error': 'feed_item_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
 
    feed_item = get_object_or_404(FeedItem, id=feed_item_id)
    like = Like.objects.filter(user=request.user, feed_item=feed_item).first()
 
    if like:
        like.delete()
        # Decrement view count used as like proxy, or track separately
        feed_item.views = max(0, feed_item.views - 1)
        feed_item.save(update_fields=['views'])
        liked = False
    else:
        Like.objects.create(user=request.user, feed_item=feed_item)
        feed_item.views += 1
        feed_item.save(update_fields=['views'])
        liked = True
 
    like_count = Like.objects.filter(feed_item=feed_item).count()
 
    return Response(
        {'liked': liked, 'like_count': like_count},
        status=status.HTTP_201_CREATED if liked else status.HTTP_200_OK,
    )


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_like(request, feed_item_id):
    """
    Remove a specific like.
    DELETE /api/feed/likes/{feed_item_id}/
    """
    deleted, _ = Like.objects.filter(
        user=request.user,
        feed_item_id=feed_item_id,
    ).delete()

    if deleted:
        return Response({'success': True}, status=status.HTTP_200_OK)
    return Response(
        {'error': 'Like not found'},
        status=status.HTTP_404_NOT_FOUND
    )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_likes(request):
    """
    Get all liked feed items for the current user.
    GET /api/feed/likes/
    """
    likes = (
        Like.objects
        .filter(user=request.user)
        .select_related('feed_item', 'feed_item__source')
        .prefetch_related('feed_item__entities')
    )
 
    paginator = FeedPagination()
    paginated = paginator.paginate_queryset(likes, request)
    serializer = LikeSerializer(paginated, many=True, context={'request': request})
    return paginator.get_paginated_response(serializer.data)
 
 