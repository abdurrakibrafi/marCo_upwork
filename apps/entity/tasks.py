from celery import shared_task
from celery.utils.log import get_task_logger
from django.core.cache import cache
from datetime import datetime
import requests as req
from django.conf import settings
from apps.entity.models import Entity, Team, EntityStats
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.balldontlie import balldontlie_service

logger = get_task_logger(__name__)


def _current_season(sport: str) -> int:
    now = datetime.now()
    year, month = now.year, now.month
    if sport == 'soccer':
        return year if month >= 8 else year - 1
    elif sport == 'basketball':
        return year if month >= 10 else year - 1
    return year


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR — fires once per hour, dispatches one task per LEAGUE not per team
# ─────────────────────────────────────────────────────────────────────────────

@shared_task
def update_all_team_stats():
    """
    Dynamically discovers every league that has teams in the DB,
    then fires exactly ONE task per league.

    That one task fetches standings once and updates ALL teams in
    that league from the single response — no matter how many leagues
    or teams exist, it never makes more than 1 API call per league.

    Adding a new league never requires touching this file.
    """

    # ── Soccer: find every unique api_sports league in the DB ──────────────
    # Only teams that: came from api_sports, have a league linked, are active
    soccer_league_ids = (
        Entity.objects
        .filter(
            type='team',
            sport='soccer',
            is_active=True,
            api_source='api_sports',
            team_details__league__isnull=False,
            team_details__league__api_source='api_sports',
        )
        .values_list('team_details__league__external_id', flat=True)
        .distinct()
    )

    soccer_count = 0
    for league_external_id in soccer_league_ids:
        if not league_external_id:
            continue
        try:
            league_id = int(league_external_id)
        except (ValueError, TypeError):
            continue
        update_soccer_league_stats.delay(league_id)
        soccer_count += 1

    # ── Basketball: one task for all NBA teams ─────────────────────────────
    has_nba = Entity.objects.filter(
        type='team', sport='basketball',
        is_active=True, api_source='balldontlie'
    ).exists()

    if has_nba:
        update_nba_standings.delay()

    logger.info(
        f"Dispatched stats update: {soccer_count} soccer leagues, "
        f"{'1 NBA task' if has_nba else 'no NBA teams'}"
    )
    return f"Dispatched {soccer_count} soccer league tasks + {'NBA' if has_nba else 'no NBA'}"


@shared_task
def seed_players_for_team(team_external_id: str, season: int = None):
    import requests
    from django.conf import settings
    from apps.entity.models import Entity, Athlete
    from datetime import datetime

    # Auto-detect season if not passed
    if season is None:
        now = datetime.now()
        season = now.year if now.month >= 8 else now.year - 1

    headers = {'x-apisports-key': settings.API_SPORTS_KEY}

    page = 1
    created_total = 0

    team_entity = Entity.objects.filter(
        api_source='api_sports',
        external_id=str(team_external_id)
    ).first()

    if not team_entity:
        return f"Team {team_external_id} not found in DB"

    while True:
        resp = requests.get(
            'https://v3.football.api-sports.io/players',
            headers=headers,
            params={'team': team_external_id, 'season': season, 'page': page},
            timeout=10,
        )

        if resp.status_code != 200:
            break

        data = resp.json()
        players = data.get('response', [])

        if not players:
            break

        for item in players:
            p = item.get('player', {})
            player_id = str(p.get('id', ''))
            if not player_id:
                continue

            player_entity, _ = Entity.objects.get_or_create(
                api_source='api_sports',
                external_id=player_id,
                defaults={
                    'type': 'athlete',
                    'name': p.get('name', ''),
                    'sport': team_entity.sport,
                    'logo_url': p.get('photo', ''),
                    'country': p.get('nationality', ''),
                    'has_api_data': True,
                }
            )

            # Parse height/weight safely
            def parse_int(val, unit):
                try:
                    return int(val.replace(unit, '').strip())
                except Exception:
                    return None

            _, was_created = Athlete.objects.get_or_create(
                entity=player_entity,
                defaults={
                    'first_name':  p.get('firstname', ''),
                    'last_name':   p.get('lastname', ''),
                    'date_of_birth': p.get('birth', {}).get('date') or None,
                    'nationality': p.get('nationality', ''),
                    'height_cm':   parse_int(p.get('height', ''), 'cm'),
                    'weight_kg':   parse_int(p.get('weight', ''), 'kg'),
                    'current_team': team_entity,
                }
            )
            if was_created:
                created_total += 1

        # Check if more pages
        total_pages = data.get('paging', {}).get('total', 1)
        if page >= total_pages:
            break
        page += 1

    return f"Seeded {created_total} players for team {team_external_id} season {season}"

# ─────────────────────────────────────────────────────────────────────────────
# SOCCER — one task per league
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_soccer_league_stats(self, league_id: int):
    """
    Fetch standings for ONE league, then update every team in that league
    from the single API response.

    1 API call per league regardless of how many teams are in it.
    """
    season = _current_season('soccer')
    cache_key = f"standings:soccer:{league_id}:{season}"

    # Check cache first — avoids re-hitting API if called multiple times
    standings_response = cache.get(cache_key)

    if standings_response is None:
        result = api_sports_service.get_standings(league_id, season)

        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                logger.warning(f"Rate limited on league {league_id}, retrying in 5 min")
                raise self.retry(exc=Exception("Rate limited"), countdown=300)
            logger.error(f"Failed standings for league {league_id}: {result.get('error')}")
            return f"Failed: league {league_id}"

        standings_response = result['data'].get('response', [])
        cache.set(cache_key, standings_response, timeout=3600)

    # Build a flat lookup: api_sports team_id -> standing data
    team_standings = {}
    for group in standings_response:
        for standings_list in group.get('league', {}).get('standings', []):
            for standing in standings_list:
                tid = str(standing.get('team', {}).get('id', ''))
                if tid:
                    team_standings[tid] = standing

    if not team_standings:
        return f"No standings data for league {league_id} season {season}"

    # Find all teams in this league in our DB
    teams = Team.objects.filter(
        entity__sport='soccer',
        entity__is_active=True,
        entity__api_source='api_sports',
        league__external_id=str(league_id),
        league__api_source='api_sports',
    ).select_related('entity')

    updated = 0
    for team in teams:
        standing = team_standings.get(team.entity.external_id)
        if not standing:
            continue

        all_stats = standing.get('all', {})
        wins = all_stats.get('win', 0)
        losses = all_stats.get('lose', 0)
        draws = all_stats.get('draw', 0)
        played = all_stats.get('played', 0)

        team.total_wins = wins
        team.total_losses = losses
        team.win_percentage = round((wins / played) * 100, 2) if played else 0
        team.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])

        EntityStats.objects.update_or_create(
            entity=team.entity,
            season=str(season),
            stat_type='season',
            defaults={
                'stats_data': {
                    'rank': standing.get('rank', 0),
                    'points': standing.get('points', 0),
                    'played': played,
                    'win': wins,
                    'draw': draws,
                    'lose': losses,
                    'goals_for': all_stats.get('goals', {}).get('for', 0),
                    'goals_against': all_stats.get('goals', {}).get('against', 0),
                    'goal_diff': standing.get('goalsDiff', 0),
                    'form': standing.get('form', ''),
                }
            }
        )
        updated += 1

    logger.info(f"League {league_id}: updated {updated}/{len(teams)} teams")
    return f"League {league_id}: updated {updated} teams"


# ─────────────────────────────────────────────────────────────────────────────
# NBA — one task for all 30 teams
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_nba_standings(self):
    """
    Fetch NBA standings once, update all NBA teams from the single response.
    """
    season = _current_season('basketball')
    cache_key = f"standings:nba:{season}"

    standings = cache.get(cache_key)

    if standings is None:
        result = balldontlie_service.get_standings('nba', season=season)
        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                raise self.retry(exc=Exception("Rate limited"), countdown=120)
            return f"Failed to fetch NBA standings: {result.get('error')}"

        standings = result['data'].get('data', [])
        cache.set(cache_key, standings, timeout=3600)

    # Build lookup: balldontlie team_id -> standing
    team_standings = {
        str(s.get('team', {}).get('id', '')): s
        for s in standings
    }

    teams = Team.objects.filter(
        entity__sport='basketball',
        entity__is_active=True,
        entity__api_source='balldontlie',
    ).select_related('entity')

    season_label = f"{season}-{str(season + 1)[-2:]}"
    updated = 0

    for team in teams:
        standing = team_standings.get(team.entity.external_id)
        if not standing:
            continue

        wins = standing.get('wins', 0)
        losses = standing.get('losses', 0)
        total = wins + losses

        team.total_wins = wins
        team.total_losses = losses
        team.win_percentage = round((wins / total) * 100, 2) if total else 0
        team.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])

        EntityStats.objects.update_or_create(
            entity=team.entity,
            season=season_label,
            stat_type='season',
            defaults={
                'stats_data': {
                    'wins': wins,
                    'losses': losses,
                    'win_pct': float(team.win_percentage),
                    'conference': standing.get('conference', ''),
                    'division': standing.get('division', ''),
                    'rank': standing.get('rank', 0),
                }
            }
        )
        updated += 1

    logger.info(f"NBA: updated {updated} teams for season {season_label}")
    return f"NBA: updated {updated} teams"