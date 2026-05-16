"""
Entity name normalization and deduplication utilities.
"""
import unicodedata
import logging
from difflib import SequenceMatcher

logger = logging.getLogger(__name__)


def normalize_entity_name(name: str) -> str:
    """
    Normalize entity names: lowercase, remove accents, strip whitespace.
    
    Examples:
        "Real Madrid" → "real madrid"
        "Atlético Madrid" → "atletico madrid"
        "  Manchester   City  " → "manchester city"
    """
    if not name:
        return ""
    
    # Remove accents
    nfd = unicodedata.normalize('NFD', name)
    normalized = ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')
    
    # Lowercase, strip, remove extra spaces
    normalized = normalized.lower().strip()
    normalized = ' '.join(normalized.split())
    
    return normalized


def similarity_ratio(s1: str, s2: str) -> float:
    """
    Calculate similarity between two strings (0-1 scale).
    Uses normalized names.
    """
    n1 = normalize_entity_name(s1)
    n2 = normalize_entity_name(s2)
    
    if not n1 or not n2:
        return 1.0 if n1 == n2 else 0.0
    
    return SequenceMatcher(None, n1, n2).ratio()


def find_similar_entity(name: str, entities_list, threshold: float = 0.85):
    """
    Find similar entity from list using normalized names.
    
    Args:
        name: Entity name to search for
        entities_list: List of Entity objects
        threshold: Similarity threshold (0-1)
    
    Returns:
        (Entity, score) if found above threshold, else (None, 0.0)
    """
    best_match = None
    best_score = 0.0
    
    norm_name = normalize_entity_name(name)
    if not norm_name:
        return None, 0.0
    
    for entity in entities_list:
        entity_norm = normalize_entity_name(entity.name)
        score = SequenceMatcher(None, norm_name, entity_norm).ratio()
        
        if score > best_score:
            best_score = score
            best_match = entity
    
    if best_score >= threshold:
        return best_match, best_score
    
    return None, best_score


def is_duplicate(name: str, existing_name: str, threshold: float = 0.90) -> bool:
    """
    Check if name is likely a duplicate of existing_name.
    
    Examples:
        ("Real Madrid", "real madrid") → True
        ("Real Madrid CF", "Real Madrid") → True (>90% match)
        ("Barcelona", "Real Madrid") → False
    """
    return similarity_ratio(name, existing_name) >= threshold
