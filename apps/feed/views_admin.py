"""
Admin API endpoints for managing RSS sources and entities.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django.db import transaction
import logging

from apps.feed.models import RSSSource
from apps.entity.models import Entity

logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def create_rss_source(request):
    """
    Admin: Create a new RSS source.
    
    POST /api/admin/rss-sources/create/
    Body: {
        "name": "La Liga Official",
        "url": "https://www.laliga.com/feed",
        "sport": "soccer",
        "keywords": ["La Liga", "Spanish Football"],
        "estimated_quality": "high",
        "is_verified": true
    }
    """
    data = request.data
    
    try:
        with transaction.atomic():
            rss_source = RSSSource.objects.create(
                name=data.get('name', ''),
                url=data.get('url', ''),
                sport=data.get('sport', 'soccer'),
                keywords=data.get('keywords', []),
                estimated_quality=data.get('estimated_quality', 'medium'),
                is_verified=data.get('is_verified', False),
                is_active=data.get('is_active', True),
                fetch_interval_hours=data.get('fetch_interval_hours', 6),
            )
            
            # Link entities if provided
            entity_ids = data.get('entity_ids', [])
            if entity_ids:
                entities = Entity.objects.filter(id__in=entity_ids)
                rss_source.entities.set(entities)
            
            logger.info(f"Created RSS source: {rss_source.name}")
            
            return Response({
                'success': True,
                'id': rss_source.id,
                'name': rss_source.name,
                'url': rss_source.url,
                'message': f"RSS source '{rss_source.name}' created"
            })
    
    except Exception as e:
        logger.error(f"Failed to create RSS source: {e}")
        return Response({
            'error': str(e)
        }, status=400)


@api_view(['PUT'])
@permission_classes([IsAdminUser])
def update_rss_source(request, rss_source_id):
    """
    Admin: Update RSS source.
    
    PUT /api/admin/rss-sources/{id}/update/
    """
    try:
        rss_source = RSSSource.objects.get(id=rss_source_id)
    except RSSSource.DoesNotExist:
        return Response({'error': 'RSS source not found'}, status=404)
    
    data = request.data
    
    try:
        # Update fields
        for field in ['name', 'url', 'sport', 'keywords', 'estimated_quality', 'is_verified', 'is_active', 'fetch_interval_hours']:
            if field in data:
                setattr(rss_source, field, data[field])
        
        # Update entities if provided
        if 'entity_ids' in data:
            entity_ids = data['entity_ids']
            entities = Entity.objects.filter(id__in=entity_ids)
            rss_source.entities.set(entities)
        
        rss_source.save()
        
        logger.info(f"Updated RSS source: {rss_source.name}")
        
        return Response({
            'success': True,
            'id': rss_source.id,
            'name': rss_source.name,
            'message': f"RSS source '{rss_source.name}' updated"
        })
    
    except Exception as e:
        logger.error(f"Failed to update RSS source: {e}")
        return Response({'error': str(e)}, status=400)


@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def delete_rss_source(request, rss_source_id):
    """
    Admin: Delete RSS source.
    
    DELETE /api/admin/rss-sources/{id}/delete/
    """
    try:
        rss_source = RSSSource.objects.get(id=rss_source_id)
        name = rss_source.name
        rss_source.delete()
        
        logger.info(f"Deleted RSS source: {name}")
        
        return Response({
            'success': True,
            'message': f"RSS source '{name}' deleted"
        })
    
    except RSSSource.DoesNotExist:
        return Response({'error': 'RSS source not found'}, status=404)
    except Exception as e:
        logger.error(f"Failed to delete RSS source: {e}")
        return Response({'error': str(e)}, status=400)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def list_rss_sources(request):
    """
    Admin: List all RSS sources with stats.
    
    GET /api/admin/rss-sources/
    """
    sport = request.GET.get('sport', '')
    is_active = request.GET.get('is_active')
    
    filters = {}
    if sport:
        filters['sport'] = sport
    if is_active is not None:
        filters['is_active'] = is_active.lower() == 'true'
    
    rss_sources = RSSSource.objects.filter(**filters).order_by('-is_verified', 'sport')
    
    data = []
    for rs in rss_sources:
        article_count = rs.rss_sources_articles.count()  # Adjust if reverse relation name is different
        data.append({
            'id': rs.id,
            'name': rs.name,
            'url': rs.url,
            'sport': rs.sport,
            'is_active': rs.is_active,
            'is_verified': rs.is_verified,
            'estimated_quality': rs.estimated_quality,
            'fetch_interval_hours': rs.fetch_interval_hours,
            'last_fetched_at': rs.last_fetched_at,
            'fetch_failures': rs.fetch_failures,
            'article_count': article_count,
            'entities_count': rs.entities.count(),
        })
    
    return Response({
        'total': len(data),
        'sources': data
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def trigger_rss_fetch(request, rss_source_id):
    """
    Admin: Manually trigger RSS fetch for a source.
    
    POST /api/admin/rss-sources/{id}/fetch/
    """
    from apps.entity.tasks_rss import fetch_rss_feed
    
    try:
        rss_source = RSSSource.objects.get(id=rss_source_id)
    except RSSSource.DoesNotExist:
        return Response({'error': 'RSS source not found'}, status=404)
    
    # Queue the task
    task = fetch_rss_feed.apply_async(args=[rss_source_id])
    
    logger.info(f"Manually triggered fetch for RSS source: {rss_source.name} (task: {task.id})")
    
    return Response({
        'success': True,
        'task_id': task.id,
        'message': f"Fetch queued for '{rss_source.name}'"
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def trigger_all_rss_fetch(request):
    """
    Admin: Manually trigger fetch for all RSS sources.
    
    POST /api/admin/rss-sources/fetch-all/
    """
    from apps.entity.tasks_rss import fetch_all_rss_feeds
    
    task = fetch_all_rss_feeds.apply_async()
    
    logger.info(f"Manually triggered fetch-all for RSS sources (task: {task.id})")
    
    return Response({
        'success': True,
        'task_id': task.id,
        'message': 'Fetch queued for all active RSS sources'
    })
