"""
Entity Views Package

Modularized view modules for entity discovery, detail, team statistics/roster/fixtures,
athlete statistics/bio, and league standings/leaders/fixtures.
"""

from .common import (
    _current_season,
    _safe_league_data,
    resolve_team_venue,
    HEADERS_SPORTS,
    HEADERS_BDL,
)

from .base import (
    search_entities,
    get_trending,
    get_entity_detail,
    get_entity_by_slug,
    get_entity_stats,
    get_entity_fixtures,
    get_entity_roster,
    get_entity_standings,
    list_entities,
)

from .team import (
    get_team_stats,
    get_team_roster,
    get_team_standings,
    get_team_fixtures,
    fetch_live_icc_rankings,
    fetch_live_fifa_rankings,
    _fetch_soccer_team_stats_thesportsdb,
    _fetch_stats_from_db_events,
    _normalize_cricket_team_key,
    CRICKET_TEAM_ALIAS_MAP,
    _normalize_team_stats,
    _get_tennis_rankings_helper,
    _get_golf_leaderboard_helper,
    _fetch_cricket_team_stats,
    _fetch_nfl_team_stats,
    _fetch_nhl_team_stats,
    _fetch_mlb_team_stats,
    _fetch_soccer_team_stats,
    _fetch_nba_team_stats,
    _fetch_soccer_team_stats_statpal,
    _fetch_nba_team_stats_statpal,
    _fetch_team_fixtures_live,
)

from .athlete import (
    get_athlete_stats,
    _fetch_soccer_player_stats,
    _fetch_thesportsdb_player_stats,
    get_athlete_bio,
)

from .league import (
    get_league_standings,
    _get_standings_for_league,
    _fetch_league_standings_thesportsdb,
    _fetch_soccer_standings,
    get_league_leaders,
    _fetch_soccer_leaders,
    get_league_fixtures,
)

__all__ = [
    # Common
    '_current_season',
    '_safe_league_data',
    'resolve_team_venue',
    'HEADERS_SPORTS',
    'HEADERS_BDL',
    # Base / Universal
    'search_entities',
    'get_trending',
    'get_entity_detail',
    'get_entity_by_slug',
    'get_entity_stats',
    'get_entity_fixtures',
    'get_entity_roster',
    'get_entity_standings',
    'list_entities',
    # Team
    'get_team_stats',
    'get_team_roster',
    'get_team_standings',
    'get_team_fixtures',
    'fetch_live_icc_rankings',
    'fetch_live_fifa_rankings',
    '_fetch_soccer_team_stats_thesportsdb',
    '_fetch_stats_from_db_events',
    '_normalize_cricket_team_key',
    'CRICKET_TEAM_ALIAS_MAP',
    '_normalize_team_stats',
    '_get_tennis_rankings_helper',
    '_get_golf_leaderboard_helper',
    '_fetch_cricket_team_stats',
    '_fetch_nfl_team_stats',
    '_fetch_nhl_team_stats',
    '_fetch_mlb_team_stats',
    '_fetch_soccer_team_stats',
    '_fetch_nba_team_stats',
    '_fetch_soccer_team_stats_statpal',
    '_fetch_nba_team_stats_statpal',
    '_fetch_team_fixtures_live',
    # Athlete
    'get_athlete_stats',
    '_fetch_soccer_player_stats',
    '_fetch_thesportsdb_player_stats',
    'get_athlete_bio',
    # League
    'get_league_standings',
    '_get_standings_for_league',
    '_fetch_league_standings_thesportsdb',
    '_fetch_soccer_standings',
    'get_league_leaders',
    '_fetch_soccer_leaders',
    'get_league_fixtures',
]
