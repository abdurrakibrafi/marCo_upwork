"""
apps/sports_apis/services/nhl.py

StatPal NHL service layer.
Mirrors the existing statpal.py structure — every method returns:
    {"success": True,  "data": <dict>}   on success
    {"success": False, "error": <str>}   on failure

StatPal NHL uses V1 base: https://statpal.io/api/v1/nhl/...

Documented endpoints used:
    /nhl/livescores
    /nhl/daily/{token}           token: d-1, d0, d1, d2 …
    /nhl/standings
    /nhl/rosters/{team_abbr}
    /nhl/team-stats/{team_abbr}
    /nhl/injuries/{team_abbr}
    /nhl/odds
"""

import logging
import os
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class NHLService:
    """
    Thin HTTP wrapper around StatPal NHL endpoints.
    All public methods return a dict with 'success' and either 'data' or 'error'.
    """

    def __init__(self):
        self.access_key = settings.STATPAL_ACCESS_KEY
        self.base_v1 = "https://statpal.io/api/v1"
        self.timeout = 15

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _get(self, url: str, extra_params: dict = None) -> dict:
        """Make a GET request and return normalised response dict."""
        params = {"access_key": self.access_key}
        if extra_params:
            params.update(extra_params)
        try:
            resp = requests.get(url, params=params, timeout=self.timeout)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            logger.warning("StatPal NHL %s → HTTP %s", url, resp.status_code)
            return {"success": False, "error": f"HTTP {resp.status_code}"}
        except requests.Timeout:
            return {"success": False, "error": "Request timed out"}
        except Exception as exc:
            logger.exception("StatPal NHL request error: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Live Scores
    # ------------------------------------------------------------------ #

    def get_live_scores(self) -> dict:
        """
        Fetch all currently live NHL games.
        Response root: livescores → tournament → match[]
        """
        return self._get(f"{self.base_v1}/nhl/livescores")

    # ------------------------------------------------------------------ #
    # Daily / Fixtures / Calendar
    # ------------------------------------------------------------------ #

    def get_daily(self, day_offset: int = 0) -> dict:
        """
        Fetch games for a specific day.
        day_offset: 0=today, -1=yesterday, +1=tomorrow, etc.
        Token format: d0, d-1, d1, d2 …
        Response root: scores → tournament → match[]
        """
        if day_offset > 0:
            token = f"d+{day_offset}"
        elif day_offset < 0:
            token = f"d{day_offset}"   # e.g. d-1
        else:
            token = "d0"
        return self._get(f"{self.base_v1}/nhl/daily/{token}")

    # ------------------------------------------------------------------ #
    # Standings
    # ------------------------------------------------------------------ #

    def get_standings(self) -> dict:
        """
        Fetch current NHL standings.
        Response root: standings → tournament → conference[] → division[] → team[]
        """
        return self._get(f"{self.base_v1}/nhl/standings")

    # ------------------------------------------------------------------ #
    # Roster
    # ------------------------------------------------------------------ #

    def get_roster(self, team_abbreviation: str) -> dict:
        """
        Fetch full roster for a team.
        team_abbreviation: e.g. 'TOR', 'BOS', 'NYR' (case-insensitive)
        Response root: team → player[]
        """
        return self._get(
            f"{self.base_v1}/nhl/rosters/{team_abbreviation.upper()}"
        )

    # ------------------------------------------------------------------ #
    # Team Stats
    # ------------------------------------------------------------------ #

    def get_team_stats(self, team_abbreviation: str) -> dict:
        """
        Fetch team statistics.
        Response root: statistics → category[] → player[]  (or team-level stats)
        """
        return self._get(
            f"{self.base_v1}/nhl/team-stats/{team_abbreviation.upper()}"
        )

    # ------------------------------------------------------------------ #
    # Injuries
    # ------------------------------------------------------------------ #

    def get_injuries(self, team_abbreviation: str) -> dict:
        """
        Fetch current injury/suspension report for a team.
        Response root: injuries → player[]
        """
        return self._get(
            f"{self.base_v1}/nhl/injuries/{team_abbreviation.upper()}"
        )

    # ------------------------------------------------------------------ #
    # Odds (pre-game only — isolated, not wired into main feed)
    # ------------------------------------------------------------------ #

    def get_odds(self) -> dict:
        """
        Fetch pre-game odds.
        Isolated — only used when betting module is enabled.
        Response root: odds → game[]
        """
        return self._get(f"{self.base_v1}/nhl/odds")

    # ------------------------------------------------------------------ #
    # Logo helper (reuses the same StatPal image endpoint as soccer)
    # ------------------------------------------------------------------ #

    def download_team_logo(self, team_id: str) -> str:
        """
        Download NHL team logo from StatPal and cache under MEDIA_ROOT/team_logos/.
        Returns the relative media URL, or '' on failure.
        Identical pattern to StatPalService.download_team_logo() — avoids duplication
        by calling the parent service directly.
        """
        from apps.sports_apis.services.statpal import statpal_service
        return statpal_service.download_team_logo(team_id, sport="nhl")


# Module-level singleton — import this everywhere
nhl_service = NHLService()