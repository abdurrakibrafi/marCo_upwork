from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from datetime import datetime, timedelta
from apps.feed.models import FeedItem, Source
from apps.entity.models import Entity
from apps.sports_apis.services.gnews import gnews_service
from apps.sports_apis.services.youtube import youtube_service
import logging

logger = logging.getLogger(__name__)


@shared_task
def update_entity_news(entity_id: int):
    """
    Update news for a specific entity using GNews
    
    Args:
        entity_id: Entity ID
    """
    try:
        entity = Entity.objects.get(id=entity_id)
    except Entity.DoesNotExist:
        logger.error(f"Entity {entity_id} not found")
        return f"Entity {entity_id} not found"
    
    logger.info(f"Updating news for {entity.name}")
    
    # Search GNews
    result = gnews_service.search_entity_news(
        entity_name=entity.name,
        sport=entity.sport,
        max_results=10
    )
    
    if not result.get('success'):
        logger.error(f"GNews failed for {entity.name}: {result.get('error')}")
        return f"GNews failed for {entity.name}"
    
    articles = result['data'].get('articles', [])
    created_count = 0
    
    for article in articles:
        # Get or create GNews source
        source_data = article.get('source', {})
        source, _ = Source.objects.get_or_create(
            type='gnews',
            name=source_data.get('name', 'Unknown Source'),
            defaults={
                'url': source_data.get('url', ''),
                'is_verified': True,
            }
        )
        
        # Check if already exists (by URL)
        if FeedItem.objects.filter(url=article['url']).exists():
            continue
        
        # Create feed item
        try:
            feed_item = FeedItem.objects.create(
                content_type='article',
                entity=entity,
                source=source,
                title=article.get('title', ''),
                description=article.get('description', ''),
                content=article.get('content', ''),
                url=article['url'],
                image_url=article.get('image', ''),
                published_at=datetime.fromisoformat(article['publishedAt'].replace('Z', '+00:00')),
                metadata=article
            )
            created_count += 1
            logger.info(f"Created feed item: {feed_item.title[:50]}")
        
        except Exception as e:
            logger.error(f"Failed to create feed item: {e}")
            continue
    
    # Update source article count
    source.article_count = FeedItem.objects.filter(source=source).count()
    source.save(update_fields=['article_count'])
    
    logger.info(f"Created {created_count} new articles for {entity.name}")
    return f"Created {created_count} articles for {entity.name}"


@shared_task
def update_entity_youtube(entity_id: int):
    """
    Update YouTube content for a specific entity
    
    Args:
        entity_id: Entity ID
    """
    try:
        entity = Entity.objects.get(id=entity_id)
    except Entity.DoesNotExist:
        return f"Entity {entity_id} not found"
    
    logger.info(f"Updating YouTube for {entity.name}")
    
    # Check if we already have YouTube channel ID
    youtube_channel_id = entity.metadata.get('youtube_channel_id')
    
    if not youtube_channel_id:
        # Search for official channel
        channel_info = youtube_service.search_entity_channel(entity.name)
        
        if channel_info:
            youtube_channel_id = channel_info['channel_id']
            
            # Save to entity metadata
            entity.metadata['youtube_channel_id'] = youtube_channel_id
            entity.metadata['youtube_channel_name'] = channel_info['channel_name']
            entity.save(update_fields=['metadata'])
            
            # Create source
            source, _ = Source.objects.get_or_create(
                type='youtube',
                youtube_channel_id=youtube_channel_id,
                defaults={
                    'name': channel_info['channel_name'],
                    'youtube_channel_name': channel_info['channel_name'],
                    'description': channel_info.get('description', ''),
                    'logo_url': channel_info.get('thumbnail', ''),
                    'is_official': True,
                    'is_verified': True,
                }
            )
            logger.info(f"Found YouTube channel for {entity.name}: {channel_info['channel_name']}")
        else:
            logger.warning(f"No YouTube channel found for {entity.name}")
            return f"No YouTube channel found for {entity.name}"
    
    # Get latest videos
    result = youtube_service.get_channel_videos(youtube_channel_id, max_results=10)
    
    if not result.get('success'):
        return f"YouTube API failed for {entity.name}"
    
    videos = result['data'].get('items', [])
    created_count = 0
    
    # Get source
    source = Source.objects.filter(
        type='youtube',
        youtube_channel_id=youtube_channel_id
    ).first()
    
    if not source:
        return f"Source not found for channel {youtube_channel_id}"
    
    for video in videos:
        video_id = video['id']['videoId']
        snippet = video['snippet']
        
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        
        # Check if already exists
        if FeedItem.objects.filter(url=video_url).exists():
            continue
        
        # Create feed item
        try:
            feed_item = FeedItem.objects.create(
                content_type='video',
                entity=entity,
                source=source,
                title=snippet.get('title', ''),
                description=snippet.get('description', ''),
                url=video_url,
                video_url=video_url,
                thumbnail_url=snippet.get('thumbnails', {}).get('high', {}).get('url', ''),
                published_at=datetime.fromisoformat(snippet['publishedAt'].replace('Z', '+00:00')),
                metadata={
                    'video_id': video_id,
                    'channel_title': snippet.get('channelTitle', ''),
                }
            )
            created_count += 1
            logger.info(f"Created video feed item: {feed_item.title[:50]}")
        
        except Exception as e:
            logger.error(f"Failed to create video feed item: {e}")
            continue
    
    logger.info(f"Created {created_count} new videos for {entity.name}")
    return f"Created {created_count} videos for {entity.name}"


@shared_task
def update_all_entity_feeds(entity_id: int):
    """
    Update both news and YouTube for an entity
    
    Args:
        entity_id: Entity ID
    """
    update_entity_news.delay(entity_id)
    update_entity_youtube.delay(entity_id)
    return f"Triggered feed updates for entity {entity_id}"


@shared_task
def update_user_nest_feeds(user_id: int):
    """
    Update feeds for all entities in user's nest
    
    Args:
        user_id: User ID
    """
    from nest.models import UserNest
    
    nest_entities = UserNest.objects.filter(
        user_id=user_id
    ).values_list('entity_id', flat=True)
    
    for entity_id in nest_entities:
        update_all_entity_feeds.delay(entity_id)
    
    return f"Triggered feed updates for {len(nest_entities)} entities in user {user_id}'s nest"


@shared_task
def update_trending_entities_feeds():
    """
    Update feeds for trending entities (top 50 by followers)
    """
    trending = Entity.objects.filter(
        is_active=True
    ).order_by('-follower_count')[:50]
    
    for entity in trending:
        update_all_entity_feeds.delay(entity.id)
    
    return f"Triggered feed updates for {trending.count()} trending entities"


@shared_task
def cleanup_old_feed_items():
    """
    Delete feed items older than 30 days
    """
    cutoff_date = timezone.now() - timedelta(days=30)
    
    deleted_count = FeedItem.objects.filter(
        published_at__lt=cutoff_date
    ).delete()[0]
    
    logger.info(f"Deleted {deleted_count} old feed items")
    return f"Deleted {deleted_count} old feed items"


@shared_task
def mark_trending_items():
    """
    Mark items as trending based on views and recency
    """
    # Reset all trending flags
    FeedItem.objects.update(is_trending=False)
    
    # Mark top items from last 24 hours
    last_24h = timezone.now() - timedelta(hours=24)
    
    trending_items = FeedItem.objects.filter(
        published_at__gte=last_24h
    ).order_by('-views')[:100]
    
    trending_items.update(is_trending=True)
    
    return f"Marked {trending_items.count()} items as trending"