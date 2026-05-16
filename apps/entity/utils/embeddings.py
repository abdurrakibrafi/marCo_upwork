"""
AI-based entity matching using OpenAI embeddings.
"""
import json
import logging
from typing import Optional, Tuple
from django.conf import settings

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False


class EmbeddingService:
    """Service for generating and comparing embeddings."""
    
    MODEL = "text-embedding-3-small"  # Cheapest, fast
    
    def __init__(self):
        if not HAS_OPENAI:
            raise ImportError("openai package required. Install: pip install openai")
        
        self.api_key = getattr(settings, 'OPENAI_API_KEY', None)
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set in settings")
        
        self.client = OpenAI(api_key=self.api_key)
    
    def generate_embedding(self, text: str) -> list:
        """Generate embedding for text."""
        try:
            response = self.client.embeddings.create(
                input=text,
                model=self.MODEL,
                encoding_format="float"
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding generation failed for '{text}': {e}")
            return None
    
    def similarity(self, embedding1: list, embedding2: list) -> float:
        """Calculate cosine similarity between two embeddings (0-1)."""
        if not embedding1 or not embedding2:
            return 0.0
        
        import math
        
        # Cosine similarity
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        magnitude1 = math.sqrt(sum(a * a for a in embedding1))
        magnitude2 = math.sqrt(sum(b * b for b in embedding2))
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)
    
    def find_similar(self, text: str, candidates: list, threshold: float = 0.80) -> Tuple[Optional[dict], float]:
        """
        Find most similar entity from candidates using embeddings.
        
        Args:
            text: Search text
            candidates: List of dicts with 'name' and 'embedding' keys
            threshold: Minimum similarity (0-1)
        
        Returns:
            (best_candidate_dict, score) if found above threshold
        """
        text_embedding = self.generate_embedding(text)
        if not text_embedding:
            return None, 0.0
        
        best_match = None
        best_score = 0.0
        
        for candidate in candidates:
            if 'embedding' not in candidate or not candidate['embedding']:
                continue
            
            try:
                candidate_emb = candidate['embedding']
                if isinstance(candidate_emb, str):
                    candidate_emb = json.loads(candidate_emb)
                
                score = self.similarity(text_embedding, candidate_emb)
                
                if score > best_score:
                    best_score = score
                    best_match = candidate
            except Exception as e:
                logger.warning(f"Similarity calc failed for {candidate.get('name')}: {e}")
                continue
        
        if best_score >= threshold:
            return best_match, best_score
        
        return None, best_score


# Singleton instance
_embedding_service = None

def get_embedding_service():
    """Get or create embedding service (lazy init)."""
    global _embedding_service
    if _embedding_service is None:
        try:
            _embedding_service = EmbeddingService()
        except Exception as e:
            logger.warning(f"Embedding service init failed: {e}. AI matching disabled.")
            return None
    return _embedding_service
