"""
apps/feed/utils_url.py

Utility functions for URL normalization and decoding Google News RSS redirect links.
"""

import logging

logger = logging.getLogger(__name__)


def resolve_real_article_url(url: str) -> str:
    """
    Decodes Google News RSS redirect URLs (e.g. news.google.com/rss/articles/...)
    to the actual publisher source URL (e.g. tbsnews.net, espn.com, reuters.com).
    Returns original url if decoding fails or if not a Google News URL.
    """
    if not url or not isinstance(url, str):
        return ""

    url_str = url.strip()
    if "news.google.com" in url_str:
        try:
            from googlenewsdecoder import new_decoderv1
            decoded = new_decoderv1(url_str)
            if isinstance(decoded, dict) and decoded.get("status") and decoded.get("decoded_url"):
                return decoded["decoded_url"]
        except Exception as e:
            logger.debug(f"Google news decoding failed for {url_str}: {e}")

    return url_str
