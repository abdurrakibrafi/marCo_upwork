from django.db.models import Q
from apps.nest.models import Entity

class EntitySearchService:
    """Service for searching entities"""
    
    @staticmethod
    def search(query: str, entity_type: str = None, sport: str = None, limit: int = 20):
        """
        Search entities by name
        
        Args:
            query: Search term
            entity_type: Filter by type (team, athlete, league)
            sport: Filter by sport
            limit: Max results
        """
        queryset = Entity.objects.filter(is_active=True)
        
        # Text search
        if query:
            queryset = queryset.filter(
                Q(name__icontains=query) |
                Q(description__icontains=query) |
                Q(metadata__aliases__icontains=query)
            )
        
        # Type filter
        if entity_type:
            queryset = queryset.filter(type=entity_type)
        
        # Sport filter
        if sport:
            queryset = queryset.filter(sport=sport)
        
        return queryset.distinct()[:limit]
    
    @staticmethod
    def get_trending(limit: int = 10):
        """Get trending entities (by follower count)"""
        return Entity.objects.filter(is_active=True).order_by('-follower_count')[:limit]
    
    @staticmethod
    def get_by_type_and_sport(entity_type: str, sport: str, limit: int = 20):
        """Get entities by type and sport"""
        return Entity.objects.filter(
            type=entity_type,
            sport=sport,
            is_active=True
        ).order_by('-follower_count')[:limit]