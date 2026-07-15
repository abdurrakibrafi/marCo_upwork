import requests
import logging

logger = logging.getLogger(__name__)

class MLBStatsService:
    BASE_URL = "https://statsapi.mlb.com/api/v1"

    def get_team_roster(self, team_id: int) -> list[dict]:
        url = f"{self.BASE_URL}/teams/{team_id}/roster"
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            req = requests.Request('GET', url, headers=headers)
            prepared = req.prepare()
            print(f"[MLB DEBUG] Sending GET to: {prepared.url}")
            print(f"[MLB DEBUG] Request Headers: {dict(prepared.headers)}")
            logger.info(f"MLB Stats API debug request: URL={prepared.url} Headers={dict(prepared.headers)}")
            
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                return resp.json().get('roster', [])
            logger.warning(f"MLB Stats API roster fetch failed for team {team_id} (HTTP {resp.status_code})")
        except Exception as e:
            logger.error(f"MLB Stats API request error for team {team_id}: {e}")
        return []

mlb_stats_service = MLBStatsService()
