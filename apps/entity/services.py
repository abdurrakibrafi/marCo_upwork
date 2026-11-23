from django.db.models import Q
from apps.nest.models import Entity

class EntitySearchService:
    """Service providing search, trending, and filtered queries for sports entities."""
    
    @staticmethod
    def search(query: str, entity_type: str = None, sport: str = None, limit: int = 20):
        """Search active entities by keyword matching across name, description, and aliases.
        
        Args:
            query (str): Keyword query string.
            entity_type (str, optional): Filter by entity type ('team', 'athlete', 'league').
            sport (str, optional): Filter by sport slug.
            limit (int, optional): Maximum result limit. Defaults to 20.

        Returns:
            QuerySet: Filtered Entity queryset.
        """
        queryset = Entity.objects.filter(is_active=True, canonical_entity__isnull=True)
        
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
            if sport.lower().strip() in ('hockey', 'ice_hockey', 'ice hockey', 'nhl'):
                queryset = queryset.filter(sport__in=['hockey', 'ice_hockey'])
            else:
                queryset = queryset.filter(sport=sport)
        
        from apps.entity.views.base import _dedupe_entities
        return _dedupe_entities(list(queryset.distinct()[:limit * 2]), limit=limit)
    
    @staticmethod
    def get_trending(limit: int = 10):
        """Retrieve trending entities ordered by highest follower count.

        Args:
            limit (int, optional): Maximum number of results to return. Defaults to 10.

        Returns:
            QuerySet: Top trending Entity instances.
        """
        return Entity.objects.filter(is_active=True).order_by('-follower_count')[:limit]
    
    @staticmethod
    def get_by_type_and_sport(entity_type: str, sport: str, limit: int = 20):
        """Retrieve entities filtered by a specific type and sport.

        Args:
            entity_type (str): Type of entity ('team', 'athlete', 'league').
            sport (str): Sport slug ('soccer', 'basketball', etc.).
            limit (int, optional): Maximum result limit. Defaults to 20.

        Returns:
            QuerySet: Filtered Entity instances ordered by follower count.
        """
        return Entity.objects.filter(
            type=entity_type,
            sport=sport,
            is_active=True
        ).order_by('-follower_count')[:limit]