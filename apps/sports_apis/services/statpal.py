"""
apps/sports_apis/services/statpal.py
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_KEY = settings.STATPAL_ACCESS_KEY


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

    def get_soccer_player(self, player_id) -> dict:
        """Response root: player"""
        return self._get(f"{self.base_v2}/soccer/players/{player_id}")

    def get_soccer_coach(self, coach_id) -> dict:
        """Response root: coach"""
        return self._get(f"{self.base_v2}/soccer/coaches/{coach_id}")

    def get_soccer_match_stats(self, league_id) -> dict:
        """Response root: matches -> tournament -> week[] -> match[]"""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/matches/stats")

    def get_soccer_league_stats(self, league_id) -> dict:
        """Response root: statistics -> team[]"""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/stats")

    # ------------------------------------------------------------------ #
    # NBA (V1)
    # ------------------------------------------------------------------ #

    def get_nba_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/nba/livescores")

    def get_nba_fixtures(self, offset: int = 0) -> dict:
        if offset == 0:
            token = "d1"  # "today" not supported; need to confirm with StatPal what to use for today
        elif offset > 0:
            token = f"d{offset}"       # d1, d2 ... NOT d+1
        else:
            token = f"d{offset}"       # d-1, d-2 (negative sign auto-included)
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
    # NFL (V1)
    # ------------------------------------------------------------------ #

    def get_nfl_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/nfl/livescores")

    def get_nfl_fixtures(self, offset: int = 0) -> dict:
        if offset == 0:
            token = "d1"
        elif offset > 0:
            token = f"d{offset}"
        else:
            token = f"d{offset}"
        return self._get(f"{self.base_v1}/nfl/daily/{token}")

    def get_nfl_schedule(self) -> dict:
        """Response root: stage[] → week[] → matches.match"""
        return self._get(f"{self.base_v1}/nfl/season-schedule")

    def get_nfl_standings(self) -> dict:
        """Response root: standings → tournament → league[] → division[] → team[]"""
        return self._get(f"{self.base_v1}/nfl/standings")

    def get_nfl_rosters(self, team_abbreviation: str) -> dict:
        """Response root: team → player[]"""
        return self._get(f"{self.base_v1}/nfl/rosters/{team_abbreviation.lower()}")

    def get_nfl_injuries(self, team_abbreviation: str) -> dict:
        return self._get(f"{self.base_v1}/nfl/injuries/{team_abbreviation.lower()}")

    def get_nhl_standings(self) -> dict:
        """Response root: standings -> tournament -> league[] -> division[] -> team[]"""
        return self._get(f"{self.base_v1}/nhl/standings")

    def get_nfl_team_stats(self, team_abbreviation: str) -> dict:
        return self._get(f"{self.base_v1}/nfl/team-stats/{team_abbreviation.lower()}")

    def get_nfl_player_stats(self, team_abbreviation: str) -> dict:
        return self._get(f"{self.base_v1}/nfl/player-stats/{team_abbreviation.lower()}")

    def get_nfl_league_stats(self, stat_type: str) -> dict:
        return self._get(f"{self.base_v1}/nfl/league-stats/{stat_type}")

    # ------------------------------------------------------------------ #
    # NHL / Hockey (V1)
    # ------------------------------------------------------------------ #

    def get_nhl_live(self) -> dict:
        """NHL live scores — Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/nhl/livescores")

    def get_hockey_live(self) -> dict:
        """Alias for get_nhl_live() — StatPal endpoint is /nhl/livescores"""
        return self.get_nhl_live()

    def get_hockey_fixtures(self, offset: int = 0) -> dict:
        """Response root: scores → tournament → match[]"""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"
        return self._get(f"{self.base_v1}/nhl/daily/{token}")

    # ------------------------------------------------------------------ #
    # Tennis (V1)
    # ------------------------------------------------------------------ #

    def get_tennis_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/tennis/livescores")

    def get_tennis_fixtures(self, offset: int = 0) -> dict:
        """Response root: scores → tournament → match[]"""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/tennis/daily/{token}")

    def get_tennis_live_stats(self) -> dict:
        """Response root: live_stats → tournament → match[]"""
        return self._get(f"{self.base_v1}/tennis/livestats")

    # ------------------------------------------------------------------ #
    # MLB (V1) - Baseball
    # ------------------------------------------------------------------ #

    def get_mlb_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/mlb/livescores")

    def get_mlb_fixtures(self, offset: int = 0) -> dict:
        """Response root: scores → tournament → match[]"""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/mlb/daily/{token}")

    def get_mlb_standings(self) -> dict:
        """Response root: standings → tournament → league[] → division[] → team[]"""
        return self._get(f"{self.base_v1}/mlb/standings")


    # ------------------------------------------------------------------ #
    # Handball (V1)
    # ------------------------------------------------------------------ #

    def get_handball_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/handball/livescores")

    def get_handball_fixtures(self, offset: int = 0) -> dict:
        """Response root: scores → tournament → match[]"""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/handball/daily/{token}")

    # ------------------------------------------------------------------ #
    # Volleyball (V1)
    # ------------------------------------------------------------------ #

    def get_volleyball_live(self) -> dict:
        """Response root: livescores → tournament → match[]"""
        return self._get(f"{self.base_v1}/volleyball/livescores")

    # ------------------------------------------------------------------ #
    # Golf (V1)
    # ------------------------------------------------------------------ #

    def get_golf_live(self) -> dict:
        """Response root: livescores → tournament[] → player[]"""
        return self._get(f"{self.base_v1}/golf/livescores")

    # ------------------------------------------------------------------ #
    # Horse Racing (V1)
    # ------------------------------------------------------------------ #

    def get_horse_racing_live(self, country: str) -> dict:
        return self._get(f"{self.base_v1}/horse-racing/live/{country}")

    def get_horse_racing_schedule(self, country: str) -> dict:
        return self._get(f"{self.base_v1}/horse-racing/schedule/{country}")

    # ------------------------------------------------------------------ #
    # Esports (V1)
    # ------------------------------------------------------------------ #

    def get_esports_live(self) -> dict:
        return self._get(f"{self.base_v1}/esports/livescores")

    # ------------------------------------------------------------------ #
    # Formula 1 (V1)
    # ------------------------------------------------------------------ #

    def get_f1_live(self) -> dict:
        return self._get(f"{self.base_v1}/f1/livescores")

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
            "nfl": self.get_nfl_live,
            "cricket": self.get_cricket_live,
            "hockey": self.get_hockey_live,
            "tennis": self.get_tennis_live,
            "mlb": self.get_mlb_live,
            "handball": self.get_handball_live,
            "volleyball": self.get_volleyball_live,
            "golf": self.get_golf_live,
            "esports": self.get_esports_live,
            "f1": self.get_f1_live,
            "formula1": self.get_f1_live,
        }.get(sport, lambda: {"success": False, "error": f"Unknown sport: {sport}"})()

    def get_fixtures(self, sport: str, offset: int = 0) -> dict:
        if sport == "soccer":
            return self.get_soccer_fixtures(offset=offset)
        if sport == "nba":
            return self.get_nba_fixtures(offset=offset)
        if sport == "nfl":
            return self.get_nfl_fixtures(offset=offset)
        if sport == "cricket":
            return self.get_cricket_fixtures()
        if sport == "hockey":
            return self.get_hockey_fixtures(offset=offset)
        if sport == "tennis":
            return self.get_tennis_fixtures(offset=offset)
        if sport == "mlb":
            return self.get_mlb_fixtures(offset=offset)
        if sport == "handball":
            return self.get_handball_fixtures(offset=offset)
        return {"success": False, "error": f"Unknown sport: {sport}"}


    def download_team_logo(self, team_id: str, sport: str = "soccer") -> str:
        # Download a team logo from StatPal and cache it under MEDIA_ROOT/team_logos/.
        # Returns the relative media URL, or empty string on failure.
        import os
        from django.conf import settings

        if not team_id:
            return ""

        filename = f"{sport}_{team_id}.png"
        logo_dir = os.path.join(settings.MEDIA_ROOT, "team_logos")
        filepath = os.path.join(logo_dir, filename)
        media_url = f"{settings.MEDIA_URL}team_logos/{filename}"

        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            return media_url

        try:
            os.makedirs(logo_dir, exist_ok=True)
            resp = requests.get(
                f"{self.base_v2}/{sport}/images",
                params={"type": "team", "id": team_id, "access_key": self.access_key},
                headers={"Accept": "image/png, application/json"},
                timeout=self.timeout,
            )
            content_type = resp.headers.get("Content-Type", "")
            if resp.status_code == 200 and "image" in content_type:
                with open(filepath, "wb") as f2:
                    f2.write(resp.content)
                return media_url
            logger.warning(
                "StatPal logo fetch failed for team_id=%s sport=%s -> HTTP %s (%s)",
                team_id, sport, resp.status_code, content_type
            )
            return ""
        except Exception as exc:
            logger.warning("StatPal logo download error for team_id=%s: %s", team_id, exc)
            return ""


statpal_service = StatPalService()