"""
apps/sports_apis/services/statpal.py
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_DEFAULT_KEY = settings.STATPAL_ACCESS_KEY


class StatPalService:
    """Client for querying sports fixtures, standings, team rosters, and real-time live scores from StatPal.io API."""
    def __init__(self):
        """Initialize StatPal service with access key and base URLs."""
        self.access_key = _DEFAULT_KEY
        self.base_v1 = "https://statpal.io/api/v1"
        self.base_v2 = "https://statpal.io/api/v2"
        self.timeout = 15

    def _get(self, url: str, extra_params: dict = None) -> dict:
        """Execute GET request with authentication parameter and parse JSON payload."""
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
        """Retrieve live ongoing soccer match scores."""
        return self._get(f"{self.base_v2}/soccer/matches/live")

    def get_soccer_fixtures(self, offset: int = 0) -> dict:
        """Retrieve soccer fixture schedule by date offset (0=today, -1=yesterday, +1=tomorrow)."""
        return self._get(f"{self.base_v2}/soccer/matches/daily", {"offset": offset})

    def get_soccer_leagues(self) -> dict:
        """Retrieve directory list of soccer leagues and competitions."""
        return self._get(f"{self.base_v2}/soccer/leagues")

    def get_soccer_league_matches(self, league_id) -> dict:
        """Retrieve all scheduled and played fixtures for a soccer league."""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/matches")

    def get_soccer_standings(self, league_id) -> dict:
        """Retrieve table standings for a soccer league."""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/standings")

    def get_soccer_team(self, team_id) -> dict:
        """Retrieve soccer club profile and squad details."""
        return self._get(f"{self.base_v2}/soccer/teams/{team_id}")

    def get_soccer_player(self, player_id) -> dict:
        """Retrieve soccer player profile and bio data."""
        return self._get(f"{self.base_v2}/soccer/players/{player_id}")

    def get_soccer_coach(self, coach_id) -> dict:
        """Retrieve soccer coach/manager profile."""
        return self._get(f"{self.base_v2}/soccer/coaches/{coach_id}")

    def get_soccer_match_stats(self, league_id) -> dict:
        """Retrieve match-level performance statistics across a soccer league."""
        from django.core.cache import cache
        cache_key = f"statpal_404_league_stats:{league_id}"
        if cache.get(cache_key):
            return {"success": False, "error": "HTTP 404 (cached)"}

        res = self._get(f"{self.base_v2}/soccer/leagues/{league_id}/matches/stats")
        if not res.get("success") and res.get("error") == "HTTP 404":
            cache.set(cache_key, True, 86400 * 7)  # Don't poll 404 league again for 7 days
        return res

    def get_soccer_league_stats(self, league_id) -> dict:
        """Retrieve team season aggregated statistics for a soccer league."""
        return self._get(f"{self.base_v2}/soccer/leagues/{league_id}/stats")

    # ------------------------------------------------------------------ #
    # NBA (V1)
    # ------------------------------------------------------------------ #

    def get_nba_live(self) -> dict:
        """Retrieve live NBA basketball scores."""
        return self._get(f"{self.base_v1}/nba/livescores")

    def get_nba_fixtures(self, offset: int = 0) -> dict:
        """Retrieve daily NBA basketball schedule by day offset."""
        if offset == 0:
            token = "d1"  # "today" not supported; need to confirm with StatPal what to use for today
        elif offset > 0:
            token = f"d{offset}"       # d1, d2 ... NOT d+1
        else:
            token = f"d{offset}"       # d-1, d-2 (negative sign auto-included)
        return self._get(f"{self.base_v1}/nba/daily/{token}")

    def get_nba_standings(self) -> dict:
        """Retrieve NBA conference and division table standings."""
        return self._get(f"{self.base_v1}/nba/standings")

    def get_nba_roster(self, team_abbreviation: str) -> dict:
        """Retrieve player roster for an NBA basketball team."""
        return self._get(f"{self.base_v1}/nba/rosters/{team_abbreviation.lower()}")

    def get_nba_team_stats(self, team_abbreviation: str) -> dict:
        """Retrieve team performance metrics for an NBA club."""
        return self._get(f"{self.base_v1}/nba/team-stats/{team_abbreviation.lower()}")

    # ------------------------------------------------------------------ #
    # NFL (V1)
    # ------------------------------------------------------------------ #

    def get_nfl_live(self) -> dict:
        """Retrieve live American football scores."""
        return self._get(f"{self.base_v1}/nfl/livescores")

    def get_nfl_fixtures(self, offset: int = 0) -> dict:
        """Retrieve NFL fixtures by day offset."""
        if offset == 0:
            token = "d1"
        elif offset > 0:
            token = f"d{offset}"
        else:
            token = f"d{offset}"
        return self._get(f"{self.base_v1}/nfl/daily/{token}")

    def get_nfl_schedule(self) -> dict:
        """Retrieve full NFL season schedule."""
        return self._get(f"{self.base_v1}/nfl/season-schedule")

    def get_nfl_standings(self) -> dict:
        """Retrieve NFL division and conference standings."""
        return self._get(f"{self.base_v1}/nfl/standings")

    def get_nfl_rosters(self, team_abbreviation: str) -> dict:
        """Retrieve active player roster for an NFL team."""
        return self._get(f"{self.base_v1}/nfl/rosters/{team_abbreviation.lower()}")

    def get_nfl_injuries(self, team_abbreviation: str) -> dict:
        """Retrieve official player injury report for an NFL team."""
        return self._get(f"{self.base_v1}/nfl/injuries/{team_abbreviation.lower()}")

    def get_nhl_standings(self) -> dict:
        """Retrieve NHL hockey league standings."""
        return self._get(f"{self.base_v1}/nhl/standings")

    def get_nfl_team_stats(self, team_abbreviation: str) -> dict:
        """Retrieve NFL team statistics."""
        return self._get(f"{self.base_v1}/nfl/team-stats/{team_abbreviation.lower()}")

    def get_nfl_player_stats(self, team_abbreviation: str) -> dict:
        """Retrieve player statistics for an NFL team."""
        return self._get(f"{self.base_v1}/nfl/player-stats/{team_abbreviation.lower()}")

    def get_nfl_league_stats(self, stat_type: str) -> dict:
        """Retrieve league-wide NFL leaders and statistics."""
        return self._get(f"{self.base_v1}/nfl/league-stats/{stat_type}")

    # ------------------------------------------------------------------ #
    # NHL / Hockey (V1)
    # ------------------------------------------------------------------ #

    def get_nhl_live(self) -> dict:
        """Retrieve live NHL hockey match scores."""
        return self._get(f"{self.base_v1}/nhl/livescores")

    def get_hockey_live(self) -> dict:
        """Alias for get_nhl_live()."""
        return self.get_nhl_live()

    def get_hockey_fixtures(self, offset: int = 0) -> dict:
        """Retrieve hockey fixtures by day offset."""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"
        return self._get(f"{self.base_v1}/nhl/daily/{token}")

    # ------------------------------------------------------------------ #
    # Tennis (V1)
    # ------------------------------------------------------------------ #

    def get_tennis_live(self) -> dict:
        """Retrieve live tennis match scores."""
        return self._get(f"{self.base_v1}/tennis/livescores")

    def get_tennis_fixtures(self, offset: int = 0) -> dict:
        """Retrieve daily tennis match schedule."""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/tennis/daily/{token}")

    def get_tennis_live_stats(self) -> dict:
        """Retrieve real-time set and point statistics for ongoing tennis matches."""
        return self._get(f"{self.base_v1}/tennis/livestats")

    def get_tennis_standings(self, tour: str = "atp") -> dict:
        """Retrieve world rankings/standings for ATP or WTA tennis tours."""
        tour_slug = str(tour).lower()
        if tour_slug not in ("atp", "wta"):
            tour_slug = "atp"
        return self._get(f"{self.base_v1}/tennis/standings/{tour_slug}")

    def get_tennis_tournament_list(self, tour: str = "atp") -> dict:
        """Retrieve list of tournaments on the ATP/WTA calendar."""
        tour_slug = str(tour).lower()
        if tour_slug not in ("atp", "wta"):
            tour_slug = "atp"
        return self._get(f"{self.base_v1}/tennis/tournament-list/{tour_slug}")

    def get_tennis_tournament_matches(self, tournament_id: int) -> dict:
        """Retrieve draw and match list for a specific tennis tournament."""
        return self._get(f"{self.base_v1}/tennis/tournament/{tournament_id}")


    # ------------------------------------------------------------------ #
    # MLB (V1) - Baseball
    # ------------------------------------------------------------------ #

    def get_mlb_live(self) -> dict:
        """Retrieve live MLB baseball scores."""
        return self._get(f"{self.base_v1}/mlb/livescores")

    def get_mlb_fixtures(self, offset: int = 0) -> dict:
        """Retrieve MLB baseball fixtures by day offset."""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/mlb/daily/{token}")

    def get_mlb_standings(self) -> dict:
        """Retrieve MLB American and National League standings."""
        return self._get(f"{self.base_v1}/mlb/standings")


    # ------------------------------------------------------------------ #
    # Handball (V1)
    # ------------------------------------------------------------------ #

    def get_handball_live(self) -> dict:
        """Retrieve live handball match scores."""
        return self._get(f"{self.base_v1}/handball/livescores")

    def get_handball_fixtures(self, offset: int = 0) -> dict:
        """Retrieve handball fixture schedule by day offset."""
        if offset == 0:
            return {"success": True, "data": {}}  # d0 not supported
        token = f"d{offset}"  # StatPal only accepts d-7..d-1, d1..d7 (no d0, no d+ prefix)
        return self._get(f"{self.base_v1}/handball/daily/{token}")

    def get_handball_standings(self, league_id: int) -> dict:
        """Retrieve standings for a handball league."""
        return self._get(f"{self.base_v1}/handball/standings/{league_id}")

    # ------------------------------------------------------------------ #
    # Volleyball (V1)
    # ------------------------------------------------------------------ #

    def get_volleyball_live(self) -> dict:
        """Retrieve live volleyball match scores."""
        return self._get(f"{self.base_v1}/volleyball/livescores")

    def get_volleyball_standings(self, league_id: int) -> dict:
        """Retrieve league standings for volleyball."""
        return self._get(f"{self.base_v1}/volleyball/standings/{league_id}")

    # ------------------------------------------------------------------ #
    # Golf (V1)
    # ------------------------------------------------------------------ #

    def get_golf_live(self) -> dict:
        """Retrieve live golf leaderboard scores."""
        return self._get(f"{self.base_v1}/golf/livescores")

    def get_golf_schedule(self) -> dict:
        """Retrieve golf tournament season schedule."""
        return self._get(f"{self.base_v1}/golf/schedule")


    # ------------------------------------------------------------------ #
    # Horse Racing (V1)
    # ------------------------------------------------------------------ #

    def get_horse_racing_live(self, country: str) -> dict:
        """Retrieve live horse racing results by country."""
        return self._get(f"{self.base_v1}/horse-racing/live/{country}")

    def get_horse_racing_schedule(self, country: str) -> dict:
        """Retrieve horse racecards and schedule by country."""
        return self._get(f"{self.base_v1}/horse-racing/schedule/{country}")

    # ------------------------------------------------------------------ #
    # Esports (V1)
    # ------------------------------------------------------------------ #

    def get_esports_live(self) -> dict:
        """Retrieve live esports match scores."""
        return self._get(f"{self.base_v1}/esports/livescores")

    # ------------------------------------------------------------------ #
    # Formula 1 (V1)
    # ------------------------------------------------------------------ #

    def get_f1_live(self) -> dict:
        """Retrieve live Formula 1 Grand Prix timing and results."""
        return self._get(f"{self.base_v1}/f1/livescores")

    # ------------------------------------------------------------------ #
    # Cricket (V1)
    # ------------------------------------------------------------------ #

    def get_cricket_live(self) -> dict:
        """Retrieve live cricket match scores and commentary."""
        return self._get(f"{self.base_v1}/cricket/livescores")

    def get_cricket_fixtures(self) -> dict:
        """Retrieve upcoming cricket tour and match fixtures."""
        return self._get(f"{self.base_v1}/cricket/upcoming-schedule")

    def get_cricket_tournaments(self) -> dict:
        """Retrieve list of active cricket tours and series."""
        return self._get(f"{self.base_v1}/cricket/tour-list")

    def get_cricket_schedule(self, tournament_type: str, tournament_id) -> dict:
        """Retrieve match schedule for a specific cricket tour or tournament."""
        return self._get(
            f"{self.base_v1}/cricket/season-schedule/{tournament_type}/{tournament_id}"
        )

    # ------------------------------------------------------------------ #
    # Unified helpers used by Celery tasks
    # ------------------------------------------------------------------ #

    def get_live_scores(self, sport: str) -> dict:
        """Fetch live scores dynamically based on sport slug."""
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
        """Fetch fixtures dynamically by sport slug and day offset."""
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
        """Download team logo from StatPal image endpoint and cache locally under MEDIA_ROOT/team_logos/."""
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