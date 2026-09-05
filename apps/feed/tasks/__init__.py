from .helpers import (
    _strip_html,
    _extract_publisher,
    _extract_domain,
    _resolve_thumbnail_for_article,
    _entity_matches_text,
)

from .discovery import (
    discover_rss_feeds_for_entity,
    extract_rss_from_domain,
    store_validated_feed,
    ensure_entity_has_rss_source,
)

from .polling import (
    update_all_entity_feeds,
    update_user_nest_feeds,
    update_trending_entities_feeds,
    poll_all_active_sources,
    poll_single_source,
)

from .brave import (
    fetch_brave_news_for_entity,
    fetch_brave_news_for_all_nest_entities,
    fetch_brave_news_for_trending,
    fetch_brave_news_for_all_entities,
)

from .scraping import (
    fetch_article_content,
    _clean_fallback_summary,
    _is_junk_page,
    extract_clean_article,
)

from .cleanup import (
    cleanup_old_feed_items,
    mark_trending_items,
    cleanup_non_sports_national_team_items,
)

__all__ = [
    # Helpers
    "_strip_html",
    "_extract_publisher",
    "_extract_domain",
    "_resolve_thumbnail_for_article",
    "_entity_matches_text",
    # Discovery
    "discover_rss_feeds_for_entity",
    "extract_rss_from_domain",
    "store_validated_feed",
    "ensure_entity_has_rss_source",
    # Polling
    "update_all_entity_feeds",
    "update_user_nest_feeds",
    "update_trending_entities_feeds",
    "poll_all_active_sources",
    "poll_single_source",
    # Brave
    "fetch_brave_news_for_entity",
    "fetch_brave_news_for_all_nest_entities",
    "fetch_brave_news_for_trending",
    "fetch_brave_news_for_all_entities",
    # Scraping
    "fetch_article_content",
    "_clean_fallback_summary",
    "_is_junk_page",
    "extract_clean_article",
    # Cleanup
    "cleanup_old_feed_items",
    "mark_trending_items",
    "cleanup_non_sports_national_team_items",
]
