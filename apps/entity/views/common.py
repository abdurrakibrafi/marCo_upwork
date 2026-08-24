import logging
from datetime import datetime
from django.conf import settings
from apps.entity.serializers import EntitySerializer
from apps.entity.utils.matcher import resolve_team_venue_fast as resolve_team_venue

logger = logging.getLogger(__name__)

HEADERS_SPORTS = {
    'x-apisports-key': getattr(settings, 'APISPORTS_KEY', ''),
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

HEADERS_BDL = {
    'Authorization': getattr(settings, 'BALLDONTLIE_API_KEY', ''),
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}


def _current_season(sport='soccer') -> int:
    """Return the current calendar year to be used as default sport season.

    Args:
        sport (str, optional): Sport slug. Defaults to 'soccer'.

    Returns:
        int: Current calendar year (e.g. 2026).
    """
    return datetime.now().year


def _safe_league_data(ent, req) -> dict:
    """Return serialized league dictionary safe for in-memory or unpersisted Entity instances.

    Args:
        ent (Entity): Entity instance (may be unpersisted/in-memory).
        req (Request): HTTP request context.

    Returns:
        dict: Safe league dictionary for response serialization.
    """
    if ent and getattr(ent, 'pk', None):
        return EntitySerializer(ent, context={'request': req}).data
    return {
        'id': None,
        'name': getattr(ent, 'name', ''),
        'external_id': getattr(ent, 'external_id', ''),
        'sport': getattr(ent, 'sport', ''),
        'type': 'league',
        'logo_url': getattr(ent, 'logo_url', '') if hasattr(ent, 'logo_url') else '',
    }
