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
    """Per-user request rate throttle for single feed article detail scraping."""
    pass


class DetailedFeedItemAnonThrottle(AnonRateThrottle):
    """Anonymous request rate throttle for single feed article detail scraping."""
    pass


class FeedPagination(PageNumberPagination):
    """Pagination configuration for article feed querysets."""
    page_size = 30
    page_size_query_param = 'limit'
    max_page_size = 50


def build_feed_serializer_context(request, paginated_feed, selected_entity_types=None) -> dict:
    """Pre-fetch user interaction state (likes, bookmarks, nests) into serializer context.

    Prevents N+1 database queries during feed serialization.

    Args:
        request: Active HTTP request instance.
        paginated_feed: List or page of FeedItem model instances.
        selected_entity_types (list, optional): Active entity type filters.

    Returns:
        dict: Serializer context dictionary populated with user state sets.
    """
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

def fast_serialize_feed_items(items, request, selected_entity_types=None) -> list:
    """High-performance batch serializer for large feed item querysets."""
    if not items:
        return []

    items_list = list(items) if not isinstance(items, list) else items
    if not items_list:
        return []

    item_ids = [item.id for item in items_list]

    # Pre-fetch user interaction state in 3 fast indexed queries
    user_bookmarked_ids = set()
    user_liked_ids = set()
    if request and request.user and request.user.is_authenticated:
        user_bookmarked_ids = set(
            Bookmark.objects.filter(user=request.user, feed_item_id__in=item_ids)
            .values_list('feed_item_id', flat=True)
        )
        user_liked_ids = set(
            Like.objects.filter(user=request.user, feed_item_id__in=item_ids)
            .values_list('feed_item_id', flat=True)
        )

    likes_qs = Like.objects.filter(feed_item_id__in=item_ids).values('feed_item_id').annotate(c=Count('id'))
    like_counts_map = {row['feed_item_id']: row['c'] for row in likes_qs}

    from apps.entity.serializers import find_entity_logo, make_logo_url_absolute
    from apps.feed.serializers import _PUBLISHER_DOMAIN

    entity_logo_cache = {}
    entity_dict_cache = {}
    publisher_logo_cache = {}

    def get_pub_logo(publisher, source):
        pub_clean = (publisher or '').strip().lower()
        if pub_clean:
            if pub_clean not in publisher_logo_cache:
                domain = _PUBLISHER_DOMAIN.get(pub_clean)
                if domain:
                    publisher_logo_cache[pub_clean] = f'https://www.google.com/s2/favicons?domain={domain}&sz=64'
                else:
                    guessed = pub_clean.replace(' ', '').replace('.', '') + '.com'
                    publisher_logo_cache[pub_clean] = f'https://www.google.com/s2/favicons?domain={guessed}&sz=64'
            return publisher_logo_cache[pub_clean]
        if source and getattr(source, 'favicon_url', None):
            return source.favicon_url
        if source and getattr(source, 'domain', None) and source.domain != 'news.google.com':
            clean = source.domain.replace('https://', '').replace('http://', '').rstrip('/')
            return f'https://www.google.com/s2/favicons?domain={clean}&sz=64'
        return ''

    results = []
    for item in items_list:
        all_ents = list(item.entities.all())
        if selected_entity_types:
            matching_ents = [e for e in all_ents if e.type in selected_entity_types]
            primary_ent = matching_ents[0] if matching_ents else (all_ents[0] if all_ents else None)
        else:
            matching_ents = all_ents
            primary_ent = all_ents[0] if all_ents else None

        ent_name = primary_ent.name if primary_ent else ''
        ent_logo = ''
        if primary_ent:
            if primary_ent.id not in entity_logo_cache:
                logo = find_entity_logo(primary_ent)
                entity_logo_cache[primary_ent.id] = make_logo_url_absolute(logo, request)
            ent_logo = entity_logo_cache[primary_ent.id]

        src_name = primary_ent.name if primary_ent else (item.source.name if item.source else '')
        pub_name = item.publisher_name or (item.source.name if item.source else '')
        pub_logo = get_pub_logo(item.publisher_name, item.source)
        src_logo = ent_logo if primary_ent else pub_logo

        ents_serialized = []
        for e in matching_ents:
            if e.id not in entity_dict_cache:
                logo = find_entity_logo(e)
                entity_dict_cache[e.id] = {
                    'id': e.id,
                    'name': e.name,
                    'type': e.type,
                    'logo_url': make_logo_url_absolute(logo, request),
                    'sport': e.sport,
                }
            ents_serialized.append(entity_dict_cache[e.id])

        results.append({
            'id': item.id,
            'source_name': src_name,
            'source_logo': src_logo,
            'publisher_name': pub_name,
            'publisher_logo': pub_logo,
            'entity_name': ent_name,
            'entity_logo': ent_logo,
            'entity_names': [e.name for e in matching_ents],
            'entities': ents_serialized,
            'title': item.title,
            'summary': item.summary,
            'thumbnail_url': item.thumbnail_url,
            'url': item.url,
            'published_at': item.published_at.isoformat() if item.published_at else None,
            'is_breaking': item.is_breaking,
            'is_trending': item.is_trending,
            'views': item.views,
            'is_bookmarked': item.id in user_bookmarked_ids,
            'is_liked': item.id in user_liked_ids,
            'like_count': like_counts_map.get(item.id, 0),
        })

    return results


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_nest_feed(request):
    """Retrieve personalized news feed aggregated across the authenticated user's followed Nest entities.

    Supports search queries, entity filtering (team, athlete, league), content type exclusions, and multiple sort modes.

    Args:
        request: HTTP request with query parameters (sort, filter, type, q, source_id).

    Returns:
        Response: JSON response containing all matching serialized feed articles.
    """
    # Parse pagination params
    try:
        page = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        limit = max(1, min(int(request.GET.get('limit', 100)), 200))
    except (ValueError, TypeError):
        limit = 100

    sort = request.GET.get('sort', 'newest').strip().lower()
    raw_filter_str = request.GET.get('filter', '')
    raw_type_str = request.GET.get('type', '')
    source_id_str = request.GET.get('source_id', '')
    q_str = request.GET.get('q', '').strip()

    # 1. Fast Cache Layer (Sub-10ms response for repeated requests)
    cache_key = f"nest_feed:{request.user.id}:p_{page}:lim_{limit}:s_{sort}:f_{raw_filter_str}:t_{raw_type_str}:src_{source_id_str}:q_{q_str}"
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
            'total_count': 0,
            'count': 0,
            'page': page,
            'limit': limit,
            'has_more': False,
            'next_page': None,
            'prev_page': None,
            'results': [],
        })
    
    # Get hidden sources and publishers
    hidden_sources_qs = HiddenSource.objects.filter(user=request.user)
    hidden_source_ids = list(hidden_sources_qs.filter(source__isnull=False).values_list('source_id', flat=True))
    hidden_publishers = list(hidden_sources_qs.exclude(publisher_name='').values_list('publisher_name', flat=True))
 
    # Build a sport-aware entity lookup to prevent cross-sport news contamination.
    # Group the user's followed entities by sport so we can enforce that a FeedItem
    # tagged with e.g. "France (soccer)" is NOT returned when the tag actually belongs
    # to a different sport entity like "France (tennis)".
    if nest_entity_ids:
        from apps.entity.models import Entity as _Entity
        nest_entities_qs = _Entity.objects.filter(
            id__in=nest_entity_ids
        ).values('id', 'sport')

        # Build per-sport entity id sets for the sport-match filter below
        nest_entity_sport_map = {}  # sport -> set of entity_ids
        for row in nest_entities_qs:
            nest_entity_sport_map.setdefault(row['sport'], set()).add(row['id'])

        # Fetch candidate FeedItem IDs via the M2M through table, but only keep those
        # where at least one tagged entity matches both the followed entity ID AND its sport.
        # This is done sport-by-sport and unioned together.
        through_model = FeedItem.entities.through
        valid_item_ids = set()
        for sport, sport_entity_ids in nest_entity_sport_map.items():
            # FeedItems tagged with a nest entity of this sport AND also tagged with
            # any entity of the SAME sport (prevents a wrong-sport entity sneaking in)
            matching_ids = through_model.objects.filter(
                entity_id__in=sport_entity_ids
            ).filter(
                feeditem__entities__sport=sport  # at least one tag must belong to this sport
            ).values_list('feeditem_id', flat=True).distinct()
            valid_item_ids.update(matching_ids)

        nest_item_ids_qs = list(valid_item_ids)
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

    # Total matching count across entire nest
    total_count = feed.count()

    # Apply page slicing
    start = (page - 1) * limit
    end = start + limit
    page_queryset = feed[start:end]

    # Serialize only requested page items with ultra-fast batch serialization
    serialized_results = fast_serialize_feed_items(page_queryset, request, selected_entity_types=selected_entity_types)

    has_more = end < total_count
    next_page = page + 1 if has_more else None
    prev_page = page - 1 if page > 1 else None

    res_data = {
        'total_count': total_count,
        'count': len(serialized_results),
        'page': page,
        'limit': limit,
        'has_more': has_more,
        'next_page': next_page,
        'prev_page': prev_page,
        'results': serialized_results,
    }
    cache.set(cache_key, res_data, timeout=600)
    return Response(res_data)


@api_view(['GET'])
@permission_classes([AllowAny])
def get_entity_feed(request, entity_id):
    """Retrieve chronologically ordered news articles linked directly to a specific entity.

    Triggers asynchronous RSS source discovery if no articles exist yet for the entity.

    Args:
        request: HTTP request instance.
        entity_id (int): Primary key of the Entity.

    Returns:
        Response: Paginated news articles.
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
    ).select_related('source').prefetch_related('entities').distinct()

    # Enforce strict sport-specific context and exclude cross-sport leaks (e.g. cricket/basketball into soccer)
    sport_clean = (getattr(entity, 'sport', '') or '').strip().lower()
    from apps.entity.utils.matcher import is_national_team
    from django.db import connection

    if connection.vendor == 'postgresql':
        if sport_clean == 'soccer':
            other_sports_regex = (
                r'(cricket|wicket|batsman|batter|bowler|ipl\b|bcci\b|bbl\b|psl\b|cpl\b|\bodi\b|\bt20\b|\bt20i\b|'
                r'\btest match|\bcentury\b|nba\b|wnba\b|basketball|slam dunk|three-pointer|triple-double|'
                r'volleyball|vnl\b|baseball|mlb\b|home run|strikeout|tennis|wimbledon|us open|french open|'
                r'australian open|\batp\b|\bwta\b|formula 1|\bf1\b|grand prix)'
            )
            feed = feed.exclude(Q(title__iregex=other_sports_regex) | Q(summary__iregex=other_sports_regex))

            if is_national_team(entity.name):
                soccer_regex = (
                    r'(soccer|football|fifa|uefa|copa|conmebol|concacaf|champions league|world cup|striker|midfield|'
                    r'defend|goalkeep|goal|penalty|clean sheet|red card|yellow card|hat-trick|neymar|messi|ronaldo|'
                    r'vinicius|mbappe|rodrygo|alisson|ederson|pele|dorival|club|squad|rost|nwsl|premier league|'
                    r'la liga|serie a|bundesliga|ligue 1|mls\b)'
                )
                feed = feed.filter(Q(title__iregex=soccer_regex) | Q(summary__iregex=soccer_regex))

        elif sport_clean == 'cricket':
            other_sports_regex = (
                r'(fifa|uefa|premier league|la liga|serie a|bundesliga|ligue 1|mls\b|el clasico|'
                r'nba\b|wnba\b|basketball|slam dunk|three-pointer|volleyball|vnl\b|baseball|mlb\b|'
                r'home run|tennis|wimbledon|formula 1|\bf1\b)'
            )
            feed = feed.exclude(Q(title__iregex=other_sports_regex) | Q(summary__iregex=other_sports_regex))

            if is_national_team(entity.name):
                cricket_regex = (
                    r'(cricket|icc\b|bcci\b|ipl\b|bpl\b|psl\b|cpl\b|bbl\b|\btest match|\bodi\b|\bt20\b|\bt20i\b|'
                    r'wicket|batsman|batter|bowler|bowling|batting|innings|century|half-century|pitch|ashes|'
                    r'cricinfo|cricbuzz|shakib|kohli|rohit|babar|root)'
                )
                feed = feed.filter(Q(title__iregex=cricket_regex) | Q(summary__iregex=cricket_regex))

        elif sport_clean == 'basketball':
            other_sports_regex = (
                r'(cricket|wicket|batsman|bowler|ipl\b|bcci\b|fifa|uefa|soccer|premier league|'
                r'volleyball|vnl\b|formula 1|\bf1\b)'
            )
            feed = feed.exclude(Q(title__iregex=other_sports_regex) | Q(summary__iregex=other_sports_regex))

            if is_national_team(entity.name):
                basket_regex = (
                    r'(basketball|nba\b|wnba\b|fiba\b|dunk|three-pointer|3-pointer|rebound|assist|'
                    r'triple-double|free throw|playoffs|lebron|curry|giannis)'
                )
                feed = feed.filter(Q(title__iregex=basket_regex) | Q(summary__iregex=basket_regex))

    # Content type filters (News/Articles vs Videos)
    raw_filters = request.GET.getlist('type') + request.GET.getlist('filter')
    filters = []
    for rf in raw_filters:
        if rf:
            filters.extend([v.strip().lower() for v in rf.split(',') if v.strip()])

    if 'video' in filters or 'videos' in filters:
        feed = feed.filter(
            Q(source__domain__icontains='youtube') | 
            Q(url__icontains='youtube.com/watch') | 
            Q(url__icontains='youtube.com/shorts') | 
            Q(url__icontains='youtu.be/')
        )
    elif 'news' in filters or 'article' in filters or 'articles' in filters:
        feed = feed.exclude(
            Q(source__domain__icontains='youtube') | 
            Q(url__icontains='youtube.com/watch') | 
            Q(url__icontains='youtube.com/shorts') | 
            Q(url__icontains='youtu.be/')
        )

    feed = feed.order_by('-published_at')

    # If entity has no feed items yet, auto-trigger targeted RSS source discovery
    if not feed.exists():
        lock_key = f"feed_auto_init:{entity.id}"
        try:
            if cache.add(lock_key, True, timeout=60):
                from .tasks import ensure_entity_has_rss_source
                try:
                    ensure_entity_has_rss_source.delay(entity.id)
                except Exception:
                    pass
        except Exception:
            pass

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
    """Retrieve full article detail, triggering on-demand content fetching and OpenAI summary if unread.

    Args:
        request: HTTP request.
        item_id (int): Primary key of the FeedItem.

    Returns:
        Response: Detailed article JSON object including scraped content and AI summaries.
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
    """Mute a news source or publisher domain for the authenticated user.

    Args:
        request: Request containing feed_item_id, publisher_name, or source_id.

    Returns:
        Response: Success status and feedback message.
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
    """Unmute a previously hidden news source or publisher domain.

    Args:
        request: Request containing feed_item_id, publisher_name, or source_id.

    Returns:
        Response: Success or 404 response.
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
    """List all sources and publisher names hidden by the authenticated user.

    Args:
        request: HTTP request.

    Returns:
        Response: Lists of hidden sources and publishers.
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
    """Trigger background Celery tasks to rediscover and poll news feeds for an entity.

    Args:
        request: HTTP request.
        entity_id (int): Primary key of the Entity.

    Returns:
        Response: Dispatch status confirmation.
    """
    entity = get_object_or_404(Entity, id=entity_id)
    
    from .tasks import update_all_entity_feeds, ensure_entity_has_rss_source
    ensure_entity_has_rss_source.delay(entity_id)
    update_all_entity_feeds.delay(entity_id)
    
    return Response({
        'success': True,
        'message': f'Feed update triggered for {entity.name}'
    })


@api_view(['GET'])
@permission_classes([AllowAny])
def get_breaking_news(request):
    """Retrieve global breaking news articles across all sports entities.

    Args:
        request: HTTP request.

    Returns:
        Response: List of top 50 breaking news articles.
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
    """Retrieve most popular and high-engagement trending articles.

    Args:
        request: HTTP request.

    Returns:
        Response: List of top 50 trending news articles.
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
    """Toggle bookmark status on a feed article for the authenticated user.

    Args:
        request: HTTP request with body {"feed_item_id": 123}.

    Returns:
        Response: JSON object indicating new boolean state {"bookmarked": bool}.
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
    """Retrieve all bookmarked articles saved by the authenticated user.

    Args:
        request: HTTP request with pagination parameters.

    Returns:
        Response: Paginated list of bookmarks.
    """
    bookmarks = (
        Bookmark.objects
        .filter(user=request.user)
        .select_related('feed_item', 'feed_item__source')
        .prefetch_related('feed_item__entities')
    )
 
    paginator = FeedPagination()
    paginated = paginator.paginate_queryset(bookmarks, request)
    feed_items = [b.feed_item for b in paginated if b.feed_item]
    context = build_feed_serializer_context(request, feed_items)
    serializer = BookmarkSerializer(paginated, many=True, context=context)
    return paginator.get_paginated_response(serializer.data)
 
 
@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_bookmark(request, feed_item_id):
    """Delete a specific article bookmark by feed item ID.

    Args:
        request: HTTP request.
        feed_item_id (int): Primary key of the bookmarked FeedItem.

    Returns:
        Response: HTTP status 200 or 404.
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
    """Toggle like reaction on a feed article and update like counters.

    Args:
        request: HTTP request with body {"feed_item_id": 123}.

    Returns:
        Response: JSON object {"liked": bool, "like_count": int}.
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
    """Remove a like reaction for an article.

    Args:
        request: HTTP request.
        feed_item_id (int): Primary key of the FeedItem.

    Returns:
        Response: HTTP status 200 or 404.
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
    """Retrieve all articles liked by the authenticated user.

    Args:
        request: HTTP request with pagination parameters.

    Returns:
        Response: Paginated list of liked articles.
    """
    likes = (
        Like.objects
        .filter(user=request.user)
        .select_related('feed_item', 'feed_item__source')
        .prefetch_related('feed_item__entities')
    )
 
    paginator = FeedPagination()
    paginated = paginator.paginate_queryset(likes, request)
    feed_items = [l.feed_item for l in paginated if l.feed_item]
    context = build_feed_serializer_context(request, feed_items)
    serializer = LikeSerializer(paginated, many=True, context=context)
    return paginator.get_paginated_response(serializer.data)
 
 