"""Entity background tasks package.

Re-exports all Celery tasks and helper functions for full backward compatibility.
"""

from .stats import (
    _current_season,
    update_all_team_stats,
    update_soccer_league_stats,
    update_cricket_team_stats,
    update_nba_standings,
    update_football_league_stats,
    update_baseball_team_stats,
    update_hockey_team_stats,
    update_handball_league_stats,
    update_volleyball_league_stats,
    update_tennis_rankings,
    update_golf_leaderboards,
    update_fifa_world_rankings_task,
)

from .bootstrap import (
    seed_players_for_team,
    bootstrap_all_entities,
    warm_venue_cache_task,
    warm_all_venue_caches,
)

__all__ = [
    "_current_season",
    "update_all_team_stats",
    "update_soccer_league_stats",
    "update_cricket_team_stats",
    "update_nba_standings",
    "update_football_league_stats",
    "update_baseball_team_stats",
    "update_hockey_team_stats",
    "update_handball_league_stats",
    "update_volleyball_league_stats",
    "update_tennis_rankings",
    "update_golf_leaderboards",
    "update_fifa_world_rankings_task",
    "seed_players_for_team",
    "bootstrap_all_entities",
    "warm_venue_cache_task",
    "warm_all_venue_caches",
]
