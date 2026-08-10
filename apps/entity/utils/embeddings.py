"""
Entity matching utilities.
"""
import logging

logger = logging.getLogger(__name__)


def get_embedding_service():
    """Get embedding service. Returns None as OpenAI is disabled."""
    return None

