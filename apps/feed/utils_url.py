import logging
import hashlib
from functools import lru_cache
from django.core.cache import cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4096)
def resolve_real_article_url(url: str) -> str:
    """
    Decodes Google News RSS redirect URLs (e.g. news.google.com/rss/articles/...)
    to the actual publisher source URL (e.g. tbsnews.net, espn.com, reuters.com).
    Returns original url if decoding fails or if not a Google News URL.
    Results are cached in memory (lru_cache) and Redis/Django cache to eliminate HTTP latency during serialization.
    """
    if not url or not isinstance(url, str):
        return ""

    url_str = url.strip()
    if "news.google.com" in url_str:
        cache_key = f"decoded_gnews_url:{hashlib.md5(url_str.encode()).hexdigest()}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return cached_url
        try:
            from googlenewsdecoder import new_decoderv1
            decoded = new_decoderv1(url_str)
            if isinstance(decoded, dict) and decoded.get("status") and decoded.get("decoded_url"):
                res = decoded["decoded_url"]
                cache.set(cache_key, res, timeout=604800)  # Cache for 7 days
                return res
        except Exception as e:
            logger.debug(f"Google news decoding failed for {url_str}: {e}")

    return url_str
