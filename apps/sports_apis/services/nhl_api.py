import requests
import logging

logger = logging.getLogger(__name__)

class NHLApiService:
    BASE_URL = "https://api-web.nhle.com/v1"

    def get_team_roster(self, team_abbrev: str) -> list[dict]:
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
