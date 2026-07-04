"""
API endpoints for entity search with AI deduplication and suggestions.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.core.cache import cache
from django.db.models import Q
import logging

from apps.entity.models import Entity
from apps.entity.utils.normalizers import normalize_entity_name, find_similar_entity
from apps.entity.utils.embeddings import get_embedding_service
from apps.entity.serializers import EntitySerializer

logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([AllowAny])
def search_entities(request):
    """
    Search entities with smart deduplication.
    
    GET /api/search/entities/?q=barcelona&sport=soccer&type=team&limit=10
    
    - Normalizes query
    - Returns canonical entities
    - Suggests similar matches
    - Uses caching to avoid rate limits
    """
    query = request.GET.get('q', '').strip()
    sport = request.GET.get('sport', '')
    entity_type = request.GET.get('type', '')
    limit = int(request.GET.get('limit', 10))
    
    if not query or len(query) < 2:
        return Response({
            'error': 'Query too short (min 2 chars)',
            'results': []
        })
    
    # Normalize query
    normalized_query = normalize_entity_name(query)
    
    # Check cache first (5 min)
    cache_key = f'entity_search:{normalized_query}:{sport}:{entity_type}'
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"Search cache hit: {query}")
        return Response(cached)
    
    # Build filter
    filters = Q(is_active=True)
    if sport:
        filters &= Q(sport=sport)
    if entity_type:
        filters &= Q(type=entity_type)
    
    # Exact match first (on normalized name)
    exact = Entity.objects.filter(
        filters,
        normalized_name=normalized_query
    )[:1]
    
    if exact.exists():
        result = {
            'query': query,
            'match_type': 'exact',
            'results': EntitySerializer(exact, many=True, context={'request': request}).data,
            'suggestions': []
        }
        cache.set(cache_key, result, 300)
        return Response(result)
    
    # Fuzzy match (similar names)
    # First, find entities whose name or normalized name contains the query words (excluding common noise words)
    words = [w.lower() for w in query.split() if len(w) > 2]
    NOISE = {'fc', 'united', 'city', 'real', 'club', 'town', 'athletic', 'rovers', 'wanderers', 'county', 'saint', 'st', 'de', 'la', 'sports', 'league', 'team'}
    search_words = [w for w in words if w not in NOISE]
    
    # Fallback to all words if query consists only of noise words
    if not search_words:
        search_words = words

    word_filter = Q()
    if search_words:
        for w in search_words:
            word_filter |= Q(name__icontains=w) | Q(normalized_name__icontains=w)
    else:
        word_filter = Q(name__icontains=query) | Q(normalized_name__icontains=normalized_query)

    matched_qs = Entity.objects.filter(
        filters
    ).filter(
        word_filter
    ).order_by('-follower_count')[:50]

    if matched_qs.exists():
        # Mix in some top followed entities to support typo/spelling correction too
        top_entities = Entity.objects.filter(filters).order_by('-follower_count')[:50]
        all_entities = list(matched_qs) + [e for e in top_entities if e not in matched_qs]
    else:
        # No contains match, fallback to top followed entities for spelling correction
        all_entities = Entity.objects.filter(filters).order_by('-follower_count')[:100]
    
    matches = []
    for entity in all_entities:
        similar, score = find_similar_entity(query, [entity], threshold=0.70)
        if similar:
            entity_norm = entity.normalized_name
            # Add bonuses to score to prioritize better matches
            if normalized_query == entity_norm:
                score += 2.0
            elif normalized_query in entity_norm.split():
                score += 1.0
            elif entity_norm.startswith(normalized_query):
                score += 0.5
            matches.append((entity, score))
    
    # Sort by score
    matches.sort(key=lambda x: x[1], reverse=True)
    matched_entities = [m[0] for m in matches[:limit]]
    
    if matched_entities:
        result = {
            'query': query,
            'match_type': 'fuzzy',
            'results': EntitySerializer(matched_entities, many=True, context={'request': request}).data,
            'suggestions': [f"{e.name} ({e.sport})" for e in matched_entities]
        }
    else:
        result = {
            'query': query,
            'match_type': 'none',
            'results': [],
            'suggestions': []
        }
    
    cache.set(cache_key, result, 300)
    return Response(result)


@api_view(['GET'])
@permission_classes([AllowAny])
def search_entities_ai(request):
    """
    AI-powered entity search using embeddings.
    
    GET /api/search/entities-ai/?q=madrid teams&sport=soccer
    
    For complex queries: "teams in madrid", "barcelona players", etc.
    Uses OpenAI embeddings for semantic matching.
    
    Rate limited: Cache results for 10 mins, recommend client-side debounce.
    """
    query = request.GET.get('q', '').strip()
    sport = request.GET.get('sport', '')
    limit = int(request.GET.get('limit', 10))
    
    if not query or len(query) < 3:
        return Response({
            'error': 'Query too short (min 3 chars)',
            'results': []
        })
    
    # Check cache first (10 min for AI calls - more aggressive)
    cache_key = f'entity_ai_search:{query}:{sport}'
    cached = cache.get(cache_key)
    if cached:
        logger.info(f"AI search cache hit: {query}")
        return Response(cached)
    
    embedding_service = get_embedding_service()
    if not embedding_service:
        return Response({
            'error': 'AI search unavailable',
            'fallback': 'Use /api/search/entities/ instead'
        }, status=503)
    
    try:
        # Generate query embedding
        query_embedding = embedding_service.generate_embedding(query)
        if not query_embedding:
            return Response({'error': 'Embedding generation failed'}, status=500)
        
        # Build filter
        filters = Q(is_active=True, embedding__isnull=False)
        if sport:
            filters &= Q(sport=sport)
        
        # Get all candidates
        all_entities = Entity.objects.filter(filters)[:200]
        
        # Score each
        candidates = []
        for entity in all_entities:
            try:
                if not entity.embedding:
                    continue
                
                import json
                embedding = entity.embedding
                if isinstance(embedding, str):
                    embedding = json.loads(embedding)
                
                score = embedding_service.similarity(query_embedding, embedding)
                candidates.append({
                    'entity': entity,
                    'score': score
                })
            except Exception as e:
                logger.warning(f"Score calc failed for {entity.name}: {e}")
                continue
        
        # Sort and limit
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top_matches = [c['entity'] for c in candidates[:limit]]
        
        if top_matches:
            result = {
                'query': query,
                'match_type': 'ai_semantic',
                'results': EntitySerializer(top_matches, many=True, context={'request': request}).data,
                'suggestions': [f"{e.name} ({e.sport})" for e in top_matches],
                'scores': [c['score'] for c in candidates[:limit]]
            }
        else:
            result = {
                'query': query,
                'match_type': 'ai_none',
                'results': [],
                'suggestions': []
            }
        
        cache.set(cache_key, result, 600)  # 10 min cache
        return Response(result)
    
    except Exception as e:
        logger.error(f"AI search failed for '{query}': {e}")
        return Response({
            'error': 'AI search failed',
            'details': str(e)
        }, status=500)


@api_view(['POST'])
@permission_classes([AllowAny])
def suggest_canonical_entity(request):
    """
    Suggest canonical entity for a given name.
    
    POST /api/search/suggest-canonical/
    Body: {"name": "Real Madrid", "sport": "soccer", "type": "team"}
    
    Returns:
    - Canonical entity if found with high confidence
    - List of alternatives if ambiguous
    """
    from apps.entity.models import CanonicalEntity
    
    data = request.data
    name = data.get('name', '').strip()
    sport = data.get('sport', '')
    entity_type = data.get('type', '')
    
    if not name:
        return Response({'error': 'Name required'}, status=400)
    
    # Try to find canonical
    canonicals = CanonicalEntity.objects.filter(
        sport=sport,
        entity_type=entity_type
    )
    
    # Check variations
    for canonical in canonicals:
        if name.lower() in [v.lower() for v in canonical.name_variations]:
            return Response({
                'found': True,
                'canonical': {
                    'id': canonical.entity.id,
                    'name': canonical.canonical_name,
                    'sport': canonical.sport,
                    'type': canonical.entity_type,
                }
            })
    
    # No exact match
    return Response({
        'found': False,
        'message': f"No canonical found for '{name}'",
        'alternatives': EntitySerializer(
            Entity.objects.filter(
                name__icontains=name,
                sport=sport,
                type=entity_type,
                is_active=True
            )[:5],
            many=True,
            context={'request': request}
        ).data
    })
