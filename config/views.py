from django.http import JsonResponse
from django.core.cache import cache
from django.db import connection
from apps.entity.models import Entity
from apps.feed.models import FeedItem
from apps.event.models import Event
from apps.score.models import LiveScore


def health_check(request):
    """
    System health check endpoint
    GET /api/health
    """
    health = {
        'status': 'healthy',
        'checks': {}
    }
    
    # Database check
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        health['checks']['database'] = 'ok'
    except Exception as e:
        health['checks']['database'] = f'error: {str(e)}'
        health['status'] = 'unhealthy'
    
    # Cache check
    try:
        cache.set('health_check', 'ok', 10)
        if cache.get('health_check') == 'ok':
            health['checks']['cache'] = 'ok'
        else:
            health['checks']['cache'] = 'error'
            health['status'] = 'unhealthy'
    except Exception as e:
        health['checks']['cache'] = f'error: {str(e)}'
        health['status'] = 'unhealthy'
    
    # Data counts
    health['counts'] = {
        'entities': Entity.objects.count(),
        'feed_items': FeedItem.objects.count(),
        'events': Event.objects.count(),
        'live_scores': LiveScore.objects.filter(status='live').count(),
    }
    
    return JsonResponse(health)


def api_status(request):
    """
    API status and statistics
    GET /api/status
    """
    from django.utils import timezone
    from datetime import timedelta
    
    now = timezone.now()
    last_24h = now - timedelta(hours=24)
    
    return JsonResponse({
        'version': '1.0.0',
        'timestamp': now.isoformat(),
        'stats': {
            'entities': {
                'total': Entity.objects.count(),
                'teams': Entity.objects.filter(type='team').count(),
                'athletes': Entity.objects.filter(type='athlete').count(),
                'leagues': Entity.objects.filter(type='league').count(),
            },
            'content': {
                'feed_items_total': FeedItem.objects.count(),
                'feed_items_24h': FeedItem.objects.filter(created_at__gte=last_24h).count(),
                'articles': FeedItem.objects.filter(content_type='article').count(),
                'videos': FeedItem.objects.filter(content_type='video').count(),
            },
            'events': {
                'total': Event.objects.count(),
                'upcoming': Event.objects.filter(status='upcoming').count(),
                'live': Event.objects.filter(status='live').count(),
                'completed_24h': Event.objects.filter(status='completed', updated_at__gte=last_24h).count(),
            },
        }
    })