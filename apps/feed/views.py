from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework import status
from rest_framework.pagination import PageNumberPagination
from django.shortcuts import get_object_or_404
from django.db.models import Q, Count
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


def build_feed_serializer_context(request, paginated_feed, selected_entity_types=None):
    context = {'request': request}
    if selected_entity_types:
        context['selected_entity_types'] = selected_entity_types
    if not paginated_feed:
        return context

    items_list = list(paginated_feed)
    if not items_list:
        return context

    page_item_ids = [item.id for item in items_list]

    if request and request.user and request.user.is_authenticated:
        context['user_nest_entity_ids'] = set(
            UserNest.objects.filter(user=request.user).values_list('entity_id', flat=True)
        )
        context['user_bookmarked_ids'] = set(
            Bookmark.objects.filter(user=request.user, feed_item_id__in=page_item_ids)
            .values_list('feed_item_id', flat=True)
        )
        context['user_liked_ids'] = set(
            Like.objects.filter(user=request.user, feed_item_id__in=page_item_ids)
            .values_list('feed_item_id', flat=True)
        )

    first_item = items_list[0]
    if not hasattr(first_item, 'like_count'):
        likes_qs = Like.objects.filter(feed_item_id__in=page_item_ids).values('feed_item_id').annotate(c=Count('id'))
        context['like_counts_map'] = {row['feed_item_id']: row['c'] for row in likes_qs}

    return context


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_feed(request):
    """
    Get aggregated feed for user's nest
    GET /api/feed/nest?page=1&limit=10&sort=newest&filter=breaking

    filter values: breaking, trending
    sort values: newest, oldest, popular, trending, least, likes, most_liked, least_liked

    Response:
    {
      "count": <total>,
      "next": <url>,
      "previous": <url>,
      "results": [ ... ]
    }
    """
    page = request.GET.get('page', '1')
    limit = request.GET.get('limit', '10')
    sort = request.GET.get('sort', 'newest').strip().lower()
    raw_filter_str = request.GET.get('filter', '')
    raw_type_str = request.GET.get('type', '')
    source_id_str = request.GET.get('source_id', '')
    q_str = request.GET.get('q', '').strip()

    # 1. Fast Cache Layer (Sub-10ms response for repeated requests)
    cache_key = f"nest_feed:{request.user.id}:p{page}:l{limit}:s{sort}:f{raw_filter_str}:t{raw_type_str}:src{source_id_str}:q{q_str}"
    cached_data = cache.get(cache_key)
    if cached_data:
        return Response(cached_data)

    # Extract filters (supports both 'type' and 'filter' query parameters)
    raw_filters = request.GET.getlist('type') + request.GET.getlist('filter')
    filters = []
    for rf in raw_filters:
        if rf:
            filters.extend([v.strip().lower() for v in rf.split(',') if v.strip()])

    # entity type filters (Teams/Athletes/Leagues - singular & plural)
    entity_type_map = {
        'team': 'team',
        'teams': 'team',
        'athlete': 'athlete',
        'athletes': 'athlete',
        'league': 'league',
        'leagues': 'league',
    }
    selected_entity_types = [entity_type_map.get(f) for f in filters if f in entity_type_map]

    # Get user's nest entities (filtered by requested entity type if specified)
    nest_qs = UserNest.objects.filter(user=request.user)
    if selected_entity_types:
        nest_qs = nest_qs.filter(entity__type__in=selected_entity_types)

    nest_entity_ids = list(nest_qs.values_list('entity_id', flat=True))
    
    # Get user's manually added custom sources
    from apps.source.models import UserCustomSource
    user_custom_source_ids = list(UserCustomSource.objects.filter(
        user=request.user,
        is_active=True,
    ).values_list('source_id', flat=True))

    if not nest_entity_ids and not user_custom_source_ids:
        return Response({
            'message': 'No matching entities in your nest',
            'count': 0,
            'next': None,
            'previous': None,
            'results': []
        })
    
    # Get hidden sources and publishers
    hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
    hidden_source_ids = list(hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True))
    hidden_publishers = list(hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True))
 
    # Subquery lookup for max performance
    if nest_entity_ids:
        nest_item_ids_qs = list(FeedItem.entities.through.objects.filter(
            entity_id__in=nest_entity_ids
        ).values_list('feeditem_id', flat=True).distinct())
    else:
        nest_item_ids_qs = []

    if user_custom_source_ids and nest_item_ids_qs:
        feed = FeedItem.objects.filter(
            Q(id__in=nest_item_ids_qs) | Q(source_id__in=user_custom_source_ids)
        ).distinct()
    elif user_custom_source_ids:
        feed = FeedItem.objects.filter(source_id__in=user_custom_source_ids).distinct()
    else:
        feed = FeedItem.objects.filter(id__in=nest_item_ids_qs).distinct()

    feed = feed.exclude(
        source_id__in=hidden_source_ids
    ).exclude(
        publisher_name__in=hidden_publishers
    ).select_related('source').prefetch_related('entities')

    if selected_entity_types and nest_entity_ids:
        matching_item_ids = list(FeedItem.objects.filter(
            entities__id__in=nest_entity_ids,
            entities__type__in=selected_entity_types
        ).values_list('id', flat=True).distinct())
        feed = feed.filter(id__in=matching_item_ids)

    # content type filters (News/Videos/Articles)
    if 'video' in filters or 'videos' in filters:
        feed = feed.filter(
            Q(source__domain__icontains='youtube') | 
            Q(url__icontains='youtube.com/watch') | 
            Q(url__icontains='youtube.com/shorts') | 
            Q(url__icontains='youtu.be/')
        )
    if 'news' in filters or 'article' in filters or 'articles' in filters:
        feed = feed.exclude(
            Q(source__domain__icontains='youtube') | 
            Q(url__icontains='youtube.com/watch') | 
            Q(url__icontains='youtube.com/shorts') | 
            Q(url__icontains='youtu.be/')
        )

    # existing feed flags
    if 'breaking' in filters:
        feed = feed.filter(is_breaking=True)
    if 'trending' in filters:
        feed = feed.filter(is_trending=True)

    if source_id_str:
        feed = feed.filter(source_id=source_id_str)

    if q_str:
        feed = feed.filter(
            Q(title__icontains=q_str) | Q(summary__icontains=q_str)
        )

    # Apply sorting
    if sort == 'newest':
        feed = feed.order_by('-published_at')
    elif sort == 'oldest':
        feed = feed.order_by('published_at')
    elif sort == 'popular':
        feed = feed.order_by('-views', '-published_at')
    elif sort == 'trending':
        feed = feed.order_by('-is_trending', '-views', '-published_at')
    elif sort in ['least', 'likes', 'most_liked', 'most_likes', 'liked']:
        feed = feed.annotate(like_count=Count('liked_by')).order_by('-like_count', '-published_at')
    elif sort in ['least_liked', 'least_likes']:
        feed = feed.annotate(like_count=Count('liked_by')).order_by('like_count', '-published_at')
    else:
        feed = feed.order_by('-published_at')

    # Paginate
    paginator = FeedPagination()
    paginated_feed = paginator.paginate_queryset(feed, request)
    
    context = build_feed_serializer_context(request, paginated_feed, selected_entity_types=selected_entity_types)
    serializer = FeedItemCompactSerializer(paginated_feed, many=True, context=context)
    
    res_data = paginator.get_paginated_response(serializer.data).data
    cache.set(cache_key, res_data, timeout=300)
    return Response(res_data)


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
    
    context = build_feed_serializer_context(request, paginated_feed)
    serializer = FeedItemCompactSerializer(paginated_feed, many=True, context=context)
    
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
    
    context = build_feed_serializer_context(request, feed)
    serializer = FeedItemCompactSerializer(feed, many=True, context=context)
    
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
    
    context = build_feed_serializer_context(request, feed)
    serializer = FeedItemCompactSerializer(feed, many=True, context=context)
    
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
 
 