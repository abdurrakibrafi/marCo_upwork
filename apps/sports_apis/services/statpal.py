"""
apps/sports_apis/services/statpal.py
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_KEY = getattr(settings, "STATPAL_ACCESS_KEY", "bc343795-df19-407b-8fb2-301dd5cdb844")


class StatPalService:
    def __init__(self):
        self.access_key = _DEFAULT_KEY
        self.base_v1 = "https://statpal.io/api/v1"
        self.base_v2 = "https://statpal.io/api/v2"
        self.timeout = 15

    def _get(self, url: str, extra_params: dict = None) -> dict:
        params = {"access_key": self.access_key}
        if extra_params:
            params.update(extra_params)
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            logger.warning("StatPal %s → HTTP %s", url, resp.status_code)
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        except requests.Timeout:
            return {"success": False, "error": "Request timed out"}
        except Exception as exc:
            logger.exception("StatPal request error: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Soccer (V2)
    # ------------------------------------------------------------------ #

    def get_soccer_live(self) -> dict:
        """Response root: live_matches → league[] → match[]"""
        return self._get(f"{self.base_v2}/soccer/matches/live")

    def get_soccer_fixtures(self, offset: int = 0) -> dict:
        """offset 0=today, -1=yesterday, +1=tomorrow
        Response root: matches_DD_MM_YYYY (dynamic key) → league[] → match[]
        """
        return self._get(f"{self.base_v2}/soccer/matches/daily", {"offset": offset})

    def get_soccer_leagues(self) -> dict:
        """Response root: leagues → league[]"""
        return self._get(f"{self.base_v2}/soccer/leagues")

    def get_soccer_league_matches(self, league_id) -> dict:
        """Response root: matches → tournament → week[] → match[]"""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/matches")

    def get_soccer_standings(self, league_id) -> dict:
        """Response root: standings → tournament → team[]"""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/standings")

    def get_soccer_team(self, team_id) -> dict:
        """Response root: team"""
        return self._get(f"{self.base_v2}/soccer/teams/{team_id}")

    # ------------------------------------------------------------------ #
    # NBA (V1)
    # ------------------------------------------------------------------ #

    def get_nba_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/nba/livescores")

    def get_nba_fixtures(self, day_offset: int = 0) -> dict:
        """Response root: scores → tournament → match[]"""
        token = f"d{day_offset}" if day_offset <= 0 else f"d+{day_offset}"
        return self._get(f"{self.base_v1}/nba/daily/{token}")

    def get_nba_standings(self) -> dict:
        """Response root: standings → tournament → league[] → division[] → team[]"""
        return self._get(f"{self.base_v1}/nba/standings")

    def get_nba_roster(self, team_abbreviation: str) -> dict:
        """Response root: team → player[]"""
        return self._get(f"{self.base_v1}/nba/rosters/{team_abbreviation.lower()}")

    def get_nba_team_stats(self, team_abbreviation: str) -> dict:
        """Response root: statistics → category[] → player[]"""
        return self._get(f"{self.base_v1}/nba/team-stats/{team_abbreviation.lower()}")

    # ------------------------------------------------------------------ #
    # Cricket (V1)
    # ------------------------------------------------------------------ #

    def get_cricket_live(self) -> dict:
        """Response root: scores → category[] → match (single object)"""
        return self._get(f"{self.base_v1}/cricket/livescores")

    def get_cricket_fixtures(self) -> dict:
        """Response root: fixtures → category[] → match (single object)"""
        return self._get(f"{self.base_v1}/cricket/upcoming-schedule")

    def get_cricket_tournaments(self) -> dict:
        """Response root: tours → category[]"""
        return self._get(f"{self.base_v1}/cricket/tour-list")

    def get_cricket_schedule(self, tournament_type: str, tournament_id) -> dict:
        """Response root: scores → category → match[]"""
        return self._get(
            f"{self.base_v1}/cricket/season-schedule/{tournament_type}/{tournament_id}"
        )

    # ------------------------------------------------------------------ #
    # Unified helpers used by Celery tasks
    # ------------------------------------------------------------------ #

    def get_live_scores(self, sport: str) -> dict:
        return {
            "soccer": self.get_soccer_live,
            "nba": self.get_nba_live,
            "cricket": self.get_cricket_live,
        }.get(sport, lambda: {"success": False, "error": f"Unknown sport: {sport}"})()

    def get_fixtures(self, sport: str, offset: int = 0) -> dict:
        if sport == "soccer":
            return self.get_soccer_fixtures(offset=offset)
        if sport == "nba":
            return self.get_nba_fixtures(day_offset=offset)
        if sport == "cricket":
            return self.get_cricket_fixtures()
        return {"success": False, "error": f"Unknown sport: {sport}"}


statpal_service = StatPalService()