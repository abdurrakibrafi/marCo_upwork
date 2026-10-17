import requests
from django.core.cache import cache
from ..common import HEADERS_SPORTS


def _fetch_soccer_leaders(external_id, season, stat_type) -> list:
    """Fetch top statistical players (scorers/assists/cards) for a league from API-Football.

    Args:
        external_id (str or int): API-Football league ID.
        season (int or str): Season year.
        stat_type (str): Target metric ('goals', 'assists', 'yellow_cards', 'red_cards').

    Returns:
        list: Leader records formatted with athlete info.
    """
    cache_key = f'leaders:soccer:{external_id}:{season}:{stat_type}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    endpoint_map = {
        'goals':   'topscorers',
        'assists': 'topassists',
        'yellow_cards': 'topyellowcards',
        'red_cards':    'topredcards',
    }
    endpoint = endpoint_map.get(stat_type, 'topscorers')

    try:
        resp = requests.get(
            f'https://v3.football.api-sports.io/players/{endpoint}',
            headers=HEADERS_SPORTS,
            params={'league': external_id, 'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return []

        response = resp.json().get('response', [])
        result = []
        for item in response[:20]:
            p    = item.get('player', {})
            s    = item.get('statistics', [{}])[0]
            goals_data  = s.get('goals', {})
            cards_data  = s.get('cards', {})
            team_data   = s.get('team', {})

            stat_value = {
                'goals':        goals_data.get('total', 0),
                'assists':      goals_data.get('assists', 0),
                'yellow_cards': cards_data.get('yellow', 0),
                'red_cards':    cards_data.get('red', 0),
            }.get(stat_type, 0)

            result.append({
                'athlete_id':  None,
                'name':        p.get('name', ''),
                'photo':       p.get('photo', ''),
                'nationality': p.get('nationality', ''),
                'age':         p.get('age', 0),
                'team':        team_data.get('name', ''),
                'team_logo':   team_data.get('logo', ''),
                stat_type:     stat_value,
            })

        cache.set(cache_key, result, timeout=3600)
        return result

    except Exception:
        return []
