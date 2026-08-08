import logging
import hashlib
import urllib.parse
from functools import lru_cache
from django.core.cache import cache

logger = logging.getLogger(__name__)

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id', 'utm_name', 'utm_reader',
    'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid', 'twclid', 'mc_cid', 'mc_eid', 'igshid',
    'ref', 'referrer', 'at_medium', 'at_campaign', 'cmpid', 'feature', 'ncid', 'sr_share'
}


def clean_tracking_parameters(url: str) -> str:
    """
    Strips marketing and analytics tracking parameters (e.g. utm_source, fbclid, gclid)
    from a URL to normalize it for canonical article deduplication (agent_task.md Section 9).
    """
    if not url or not isinstance(url, str):
        return ""

    try:
        url_str = url.strip()
        parsed = urllib.parse.urlparse(url_str)
        if not parsed.query:
            return url_str

        params = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        filtered_params = {
            k: v for k, v in params.items()
            if k.lower() not in TRACKING_PARAMS and not k.lower().startswith('utm_')
        }

        new_query = urllib.parse.urlencode(filtered_params, doseq=True)
        cleaned_url = urllib.parse.urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment
        ))
        return cleaned_url
    except Exception as e:
        logger.debug(f"URL tracking clean failed for {url}: {e}")
        return url.strip()


@lru_cache(maxsize=4096)
def resolve_real_article_url(url: str) -> str:
    """
    Decodes Google News RSS redirect URLs (e.g. news.google.com/rss/articles/...)
    to the actual publisher source URL (e.g. tbsnews.net, espn.com, reuters.com)
    and strips tracking parameters for canonical URL deduplication.
    """
    if not url or not isinstance(url, str):
        return ""

    url_str = url.strip()
    if "news.google.com" in url_str:
        cache_key = f"decoded_gnews_url:{hashlib.md5(url_str.encode()).hexdigest()}"
        cached_url = cache.get(cache_key)
        if cached_url:
            return clean_tracking_parameters(cached_url)
        try:
            from googlenewsdecoder import new_decoderv1
            decoded = new_decoderv1(url_str, interval=1)
            if isinstance(decoded, dict) and decoded.get("status") and decoded.get("decoded_url"):
                res = clean_tracking_parameters(decoded["decoded_url"])
                cache.set(cache_key, res, timeout=604800)  # Cache for 7 days
                return res
        except Exception as e:
            logger.debug(f"Google news decoding failed for {url_str}: {e}")

        # Fallback: direct HTTP redirect resolution if decoder fails or rate-limited
        try:
            import requests
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            }
            resp = requests.head(url_str, headers=headers, allow_redirects=True, timeout=5)
            if resp.url and "news.google.com" not in resp.url:
                res = clean_tracking_parameters(resp.url)
                cache.set(cache_key, res, timeout=604800)
                return res
        except Exception as e:
            logger.debug(f"Direct HEAD redirect fallback failed for {url_str}: {e}")

    return clean_tracking_parameters(url_str)
