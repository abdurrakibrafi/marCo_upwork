import requests
import logging

logger = logging.getLogger(__name__)

class NHLApiService:
    """Client for querying current NHL hockey rosters and active player assets from the NHL API."""
    BASE_URL = "https://api-web.nhle.com/v1"

    def get_team_roster(self, team_abbrev: str) -> list[dict]:
        """Fetch active hockey player roster (forwards, defensemen, goalies) for an NHL team code.

        Args:
            team_abbrev (str): 3-character NHL team ticker (e.g. 'BOS', 'TOR', 'NYR').

        Returns:
            list[dict]: Flattened list of player records.
        """
        url = f"{self.BASE_URL}/roster/{team_abbrev.upper()}/current"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                players = []
                for group in ['forwards', 'defensemen', 'goalies']:
                    players.extend(data.get(group, []))
                return players
            logger.warning(f"NHL API roster fetch failed for team {team_abbrev} (HTTP {resp.status_code})")
        except Exception as e:
            logger.error(f"NHL API request error for team {team_abbrev}: {e}")
        return []

nhl_api_service = NHLApiService()
