from celery import shared_task
from celery.utils.log import get_task_logger
from collections import defaultdict
from django.core.cache import cache
from datetime import datetime
import requests as req
from django.conf import settings
from apps.entity.models import Entity, Team, EntityStats
from apps.sports_apis.services.api_sports import api_sports_service
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.statpal import statpal_service

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

    # ── Soccer: find every unique soccer league in the DB ──────────────
    # Only teams that: have a league linked, are active
    soccer_league_ids = (
        Entity.objects
        .filter(
            type='team',
            sport='soccer',
            is_active=True,
            api_source__in=['api_sports', 'statpal'],
            team_details__league__isnull=False,
            team_details__league__api_source__in=['api_sports', 'statpal'],
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
        is_active=True, api_source__in=['balldontlie', 'statpal']
    ).exists()

    if has_nba:
        update_nba_standings.delay()

    # Cricket does not have a league table.  Aggregate completed matches from
    # every current tour so all cricket teams receive a current-year record.
    has_cricket = Entity.objects.filter(
        type='team', sport='cricket', is_active=True, api_source='statpal'
    ).exists()
    if has_cricket:
        update_cricket_team_stats.delay()

    # ── Football: one task for NFL standings ─────────────────────────────
    has_football = Entity.objects.filter(
        type='team', sport='football', is_active=True
    ).exists()
    if has_football:
        update_football_league_stats.delay()

    # ── Baseball: one task for MLB standings ─────────────────────────────
    has_baseball = Entity.objects.filter(
        type='team', sport='baseball', is_active=True
    ).exists()
    if has_baseball:
        update_baseball_team_stats.delay()

    # ── Hockey: one task for NHL standings ───────────────────────────────
    has_hockey = Entity.objects.filter(
        type='team', sport='hockey', is_active=True
    ).exists()
    if has_hockey:
        update_hockey_team_stats.delay()

    logger.info(
        f"Dispatched stats update: {soccer_count} soccer leagues, "
        f"{'1 NBA task' if has_nba else 'no NBA teams'}, "
        f"{'1 cricket task' if has_cricket else 'no cricket teams'}, "
        f"{'1 NFL task' if has_football else 'no NFL teams'}, "
        f"{'1 MLB task' if has_baseball else 'no MLB teams'}, "
        f"{'1 NHL task' if has_hockey else 'no NHL teams'}"
    )
    return (
        f"Dispatched {soccer_count} soccer league tasks + "
        f"{'NBA' if has_nba else 'no NBA'} + "
        f"{'cricket' if has_cricket else 'no cricket'} + "
        f"{'NFL' if has_football else 'no NFL'} + "
        f"{'MLB' if has_baseball else 'no MLB'} + "
        f"{'NHL' if has_hockey else 'no NHL'}"
    )



@shared_task
def seed_players_for_team(team_external_id: str, season: int = None):
    from apps.entity.models import Entity, Athlete

    team_entity = Entity.objects.filter(
        api_source__in=['api_sports', 'statpal'],
        external_id=str(team_external_id)
    ).first()

    if not team_entity:
        return f"Team {team_external_id} not found in DB"

    if team_entity.api_source == 'api_sports':
        headers = {'x-apisports-key': settings.API_SPORTS_KEY}
        try:
            resp = req.get(
                'https://v3.football.api-sports.io/players/squads',
                headers=headers,
                params={'team': team_external_id},
                timeout=15,
            )
            if resp.status_code != 200:
                return f"Failed to fetch team details from API-Sports: HTTP {resp.status_code}"

            squads = resp.json().get('response', [])
            if not squads:
                return f"No squads returned from API-Sports for team {team_external_id}"

            players = squads[0].get('players', [])
            created_total = 0
            for p in players:
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
                        'has_api_data': True,
                        'logo_url': p.get('photo', '') or '',
                    }
                )

                name = p.get('name', '').strip()
                name_parts = name.split()
                first_name = name_parts[0] if name_parts else ''
                last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                athlete, was_created = Athlete.objects.get_or_create(
                    entity=player_entity,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'current_team': team_entity,
                    }
                )
                athlete.position = p.get('position', '')
                athlete.jersey_number = p.get('number') or None
                athlete.save()
                if was_created:
                    created_total += 1

            return f"Seeded {created_total} players for team {team_external_id} from API-Sports"
        except Exception as e:
            return f"Failed to fetch squad for {team_entity.name} from API-Sports: {e}"

    result = statpal_service.get_soccer_team(team_external_id)
    if not result['success']:
        return f"Failed to fetch team details from StatPal: {result.get('error')}"

    squad = result['data'].get('team', {}).get('squad', {}).get('player', [])
    if isinstance(squad, dict):
        squad = [squad]

    created_total = 0
    for p in squad:
        player_id = str(p.get('id', ''))
        if not player_id:
            continue

        player_entity, _ = Entity.objects.get_or_create(
            api_source='statpal',
            external_id=player_id,
            defaults={
                'type': 'athlete',
                'name': p.get('name', ''),
                'sport': team_entity.sport,
                'has_api_data': True,
            }
        )

        name_parts = p.get('name', '').split()
        first_name = name_parts[0] if name_parts else ''
        last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

        _, was_created = Athlete.objects.get_or_create(
            entity=player_entity,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'current_team': team_entity,
            }
        )
        if was_created:
            created_total += 1

    return f"Seeded {created_total} players for team {team_external_id} from StatPal"

# ─────────────────────────────────────────────────────────────────────────────
# SOCCER — one task per league
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def update_soccer_league_stats(self, league_id: int):
    """
    Fetch standings for ONE league using StatPal, then update every team in that league
    from the single API response.
    """
    season = _current_season('soccer')
    cache_key = f"standings:soccer:{league_id}:{season}"

    standings_response = cache.get(cache_key)

    if standings_response is None:
        result = statpal_service.get_soccer_standings(league_id)

        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                logger.warning(f"Rate limited on league {league_id}, retrying in 5 min")
                raise self.retry(exc=Exception("Rate limited"), countdown=300)
            logger.error(f"Failed standings for league {league_id}: {result.get('error')}")
            return f"Failed: league {league_id}"

        standings_response = result['data'].get('standings', {})
        cache.set(cache_key, standings_response, timeout=3600)

    # Build lookups: statpal team_id or team_name -> standing data
    team_standings_by_id = {}
    team_standings_by_name = {}
    
    standings_list = standings_response.get('tournament', {}).get('team', [])
    if isinstance(standings_list, dict):
        standings_list = [standings_list]
        
    for standing in standings_list:
        tid = str(standing.get('id', ''))
        tname = standing.get('name', '')
        if tid:
            team_standings_by_id[tid] = standing
        if tname:
            team_standings_by_name[tname.lower()] = standing

    if not team_standings_by_id and not team_standings_by_name:
        return f"No standings data for league {league_id} season {season}"

    # Find all teams in this league in our DB (supporting both api_sports and statpal sources)
    teams = Team.objects.filter(
        entity__sport='soccer',
        entity__is_active=True,
        entity__api_source__in=['api_sports', 'statpal'],
        league__external_id=str(league_id),
        league__api_source__in=['api_sports', 'statpal'],
    ).select_related('entity')

    updated = 0
    for team in teams:
        standing = team_standings_by_id.get(team.entity.external_id)
        if not standing:
            standing = team_standings_by_name.get(team.entity.name.lower())
            
        if not standing:
            continue

        overall = standing.get('overall', {})
        total = standing.get('total', {})
        
        wins = int(overall.get('wins') or 0)
        losses = int(overall.get('losses') or 0)
        draws = int(overall.get('draws') or 0)
        played = int(overall.get('games_played') or 0)

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
                    'rank': int(standing.get('position') or 0),
                    'points': int(total.get('points') or 0),
                    'played': played,
                    'win': wins,
                    'draw': draws,
                    'lose': losses,
                    'goals_for': int(overall.get('goals_scored') or 0),
                    'goals_against': int(overall.get('goals_allowed') or 0),
                    'goal_diff': int(total.get('goal_difference') or 0),
                    'form': standing.get('recent_form', ''),
                }
            }
        )
        updated += 1

    logger.info(f"League {league_id}: updated {updated}/{len(teams)} teams")
    return f"League {league_id}: updated {updated} teams"


# ─────────────────────────────────────────────────────────────────────────────
# CRICKET — aggregate every current tour once for all cricket teams
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=300)
def update_cricket_team_stats(self):
    """Store current-year cricket results for every StatPal cricket team.

    Unlike league sports, cricket teams play across bilateral tours.  Fetching
    each tour once is substantially cheaper than scanning every tour separately
    for every team when an individual stats endpoint is requested.
    """
    season = str(_current_season('cricket'))
    teams = list(
        Team.objects.filter(
            entity__type='team',
            entity__sport='cricket',
            entity__is_active=True,
            entity__api_source='statpal',
        ).select_related('entity')
    )
    if not teams:
        return 'No active cricket teams found'

    teams_by_id = {str(team.entity.external_id): team for team in teams}
    teams_by_name = {team.entity.name.strip().lower(): team for team in teams}
    totals = defaultdict(lambda: {
        'wins': 0, 'losses': 0, 'draws': 0, 'no_results': 0,
    })

    result = statpal_service.get_cricket_tournaments()
    if not result.get('success'):
        status_code = result.get('status_code')
        if status_code == 429:
            raise self.retry(exc=Exception('StatPal rate limited'), countdown=300)
        return f"Failed to fetch cricket tours: {result.get('error', 'unknown error')}"

    tours = result.get('data', {}).get('tours', {}).get('category', [])
    if isinstance(tours, dict):
        tours = [tours]

    tours_checked = 0
    for tour in tours:
        if not isinstance(tour, dict):
            continue
        tour_uri = str(tour.get('schedule_uri', '')).strip()
        parts = [part for part in tour_uri.strip('/').split('/') if part]
        if len(parts) < 2:
            continue

        schedule = statpal_service.get_cricket_schedule(parts[0], parts[1])
        if not schedule.get('success'):
            continue
        tours_checked += 1

        categories = schedule.get('data', {}).get('scores', {}).get('category', [])
        if isinstance(categories, dict):
            categories = [categories]

        for category in categories:
            if not isinstance(category, dict):
                continue
            matches = category.get('match', [])
            if isinstance(matches, dict):
                matches = [matches]
            for match in matches:
                if not isinstance(match, dict):
                    continue
                if str(match.get('status', '')).lower() not in ('finished', 'complete', 'completed'):
                    continue

                home = match.get('home', {}) or {}
                away = match.get('away', {}) or {}
                result_text = str(match.get('comment', {}).get('post', '')).lower()
                is_draw = 'drawn' in result_text or 'draw' in result_text
                is_no_result = 'no result' in result_text or 'abandoned' in result_text

                for side in (home, away):
                    team = teams_by_id.get(str(side.get('id', '')))
                    if not team:
                        team = teams_by_name.get(str(side.get('name', '')).strip().lower())
                    if not team:
                        continue

                    record = totals[team.entity_id]
                    if is_draw:
                        record['draws'] += 1
                    elif is_no_result:
                        record['no_results'] += 1
                    elif str(side.get('winner', '')).lower() == 'true':
                        record['wins'] += 1
                    else:
                        record['losses'] += 1

    for team in teams:
        record = totals[team.entity_id]
        played = sum(record.values())
        stats_data = {
            **record,
            'matches_played': played,
            'win_percentage': round(record['wins'] / played * 100, 1) if played else 0,
        }
        EntityStats.objects.update_or_create(
            entity=team.entity,
            season=season,
            stat_type='season',
            defaults={'stats_data': stats_data},
        )
        cache.set(
            f'team_stats:cricket:{team.entity.external_id}:{season}:statpal',
            stats_data,
            timeout=3600,
        )

    logger.info(
        'Cricket stats: updated %s teams from %s tours for season %s',
        len(teams), tours_checked, season,
    )
    return f'Cricket: updated {len(teams)} teams from {tours_checked} tours for season {season}'


# ─────────────────────────────────────────────────────────────────────────────
# NBA — one task for all 30 teams
# ─────────────────────────────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_nba_standings(self):
    """
    Fetch NBA standings once using StatPal, update all NBA teams from the single response.
    """
    season = _current_season('basketball')
    cache_key = f"standings:nba:{season}"

    standings = cache.get(cache_key)

    if standings is None:
        result = statpal_service.get_nba_standings()
        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                raise self.retry(exc=Exception("Rate limited"), countdown=120)
            return f"Failed to fetch NBA standings: {result.get('error')}"

        standings = result['data'].get('standings', {})
        cache.set(cache_key, standings, timeout=3600)

    # Build lookups: statpal team_id or team_name -> standing data
    team_standings_by_id = {}
    team_standings_by_name = {}
    
    leagues = standings.get('tournament', {}).get('league', [])
    if isinstance(leagues, dict):
        leagues = [leagues]
        
    for lg in leagues:
        conferences = lg.get('division', [])
        if isinstance(conferences, dict):
            conferences = [conferences]
            
        for conf in conferences:
            teams_list = conf.get('team', [])
            if isinstance(teams_list, dict):
                teams_list = [teams_list]
                
            for standing in teams_list:
                tid = str(standing.get('id', ''))
                tname = standing.get('name', '')
                if tid:
                    team_standings_by_id[tid] = standing
                if tname:
                    team_standings_by_name[tname.lower()] = standing

    if not team_standings_by_id and not team_standings_by_name:
        return "No NBA standings data found"

    teams = Team.objects.filter(
        entity__sport='basketball',
        entity__is_active=True,
        entity__api_source__in=['balldontlie', 'statpal'],
    ).select_related('entity')

    season_label = f"{season}-{str(season + 1)[-2:]}"
    updated = 0

    for team in teams:
        standing = team_standings_by_id.get(team.entity.external_id)
        if not standing:
            standing = team_standings_by_name.get(team.entity.name.lower())
            
        if not standing:
            continue

        wins = int(standing.get('won') or 0)
        losses = int(standing.get('lost') or 0)
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
                    'conference_rank': int(standing.get('position') or 0),
                    'streak': standing.get('streak', ''),
                    'home_record': standing.get('home_record', ''),
                    'road_record': standing.get('road_record', ''),
                }
            }
        )
        updated += 1

    logger.info(f"NBA: updated {updated} teams for season {season_label}")
    return f"NBA: updated {updated} teams"


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_football_league_stats(self):
    """
    Fetch NFL standings once using StatPal, update all NFL teams.
    """
    season = _current_season('football')
    cache_key = f"standings:football:{season}"

    standings = cache.get(cache_key)

    if standings is None:
        result = statpal_service.get_nfl_standings()
        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                raise self.retry(exc=Exception("Rate limited"), countdown=120)
            return f"Failed to fetch NFL standings: {result.get('error')}"

        standings = result['data'].get('standings', {})
        cache.set(cache_key, standings, timeout=3600)

    team_standings_by_id = {}
    team_standings_by_name = {}

    cats = standings.get('category', [])
    if isinstance(cats, dict):
        cats = [cats]

    for cat in cats:
        leagues = cat.get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
        for lg in leagues:
            divs = lg.get('division', [])
            if isinstance(divs, dict):
                divs = [divs]
            for div in divs:
                teams_list = div.get('team', [])
                if isinstance(teams_list, dict):
                    teams_list = [teams_list]
                for standing in teams_list:
                    tid = str(standing.get('id', ''))
                    tname = standing.get('name', '')
                    standing['_conference'] = lg.get('name', '')
                    standing['_division'] = div.get('name', '')
                    if tid:
                        team_standings_by_id[tid] = standing
                    if tname:
                        team_standings_by_name[tname.lower()] = standing

    if not team_standings_by_id and not team_standings_by_name:
        return "No NFL standings data found"

    teams = Team.objects.filter(
        entity__sport='football',
        entity__is_active=True,
    ).select_related('entity')

    updated = 0
    for team in teams:
        standing = team_standings_by_id.get(team.entity.external_id)
        if not standing:
            standing = team_standings_by_name.get(team.entity.name.lower())

        if not standing:
            logger.warning(f"NFL: No standing found for team {team.entity.name}")
            continue

        wins = int(standing.get('won') or 0)
        losses = int(standing.get('lost') or 0)
        ties = int(standing.get('ties') or 0)
        played = wins + losses + ties

        team.total_wins = wins
        team.total_losses = losses
        win_pct_str = standing.get('win_percentage', '0')
        if win_pct_str.startswith('.'):
            win_pct_str = '0' + win_pct_str
        try:
            win_percentage = float(win_pct_str) * 100
        except (ValueError, TypeError):
            win_percentage = 0.0
        team.win_percentage = round(win_percentage, 2)
        team.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])

        EntityStats.objects.update_or_create(
            entity=team.entity,
            season=str(season),
            stat_type='season',
            defaults={
                'stats_data': {
                    'wins': wins,
                    'losses': losses,
                    'ties': ties,
                    'matches_played': played,
                    'win_percentage': float(team.win_percentage),
                    'points_for': int(standing.get('points_for') or 0),
                    'points_against': int(standing.get('points_against') or 0),
                    'conference': standing.get('_conference', ''),
                    'division': standing.get('_division', ''),
                    'rank': int(standing.get('position') or 0),
                    'streak': standing.get('streak', ''),
                    'home_record': standing.get('home_record', ''),
                    'road_record': standing.get('road_record', ''),
                }
            }
        )
        updated += 1

    logger.info(f"NFL: updated {updated} teams for season {season}")
    return f"NFL: updated {updated} teams"


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_baseball_team_stats(self):
    """
    Fetch MLB standings once using StatPal, update all MLB teams.
    """
    season = _current_season('baseball')
    cache_key = f"standings:baseball:{season}"

    standings = cache.get(cache_key)

    if standings is None:
        result = statpal_service.get_mlb_standings()
        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                raise self.retry(exc=Exception("Rate limited"), countdown=120)
            return f"Failed to fetch MLB standings: {result.get('error')}"

        standings = result['data'].get('standings', {})
        cache.set(cache_key, standings, timeout=3600)

    team_standings_by_id = {}
    team_standings_by_name = {}

    cats = standings.get('category', [])
    if isinstance(cats, dict):
        cats = [cats]

    for cat in cats:
        leagues = cat.get('league', [])
        if isinstance(leagues, dict):
            leagues = [leagues]
        for lg in leagues:
            divs = lg.get('division', [])
            if isinstance(divs, dict):
                divs = [divs]
            for div in divs:
                teams_list = div.get('team', [])
                if isinstance(teams_list, dict):
                    teams_list = [teams_list]
                for standing in teams_list:
                    tid = str(standing.get('id', ''))
                    tname = standing.get('name', '')
                    standing['_conference'] = lg.get('name', '')
                    standing['_division'] = div.get('name', '')
                    if tid:
                        team_standings_by_id[tid] = standing
                    if tname:
                        team_standings_by_name[tname.lower()] = standing

    if not team_standings_by_id and not team_standings_by_name:
        return "No MLB standings data found"

    teams = Team.objects.filter(
        entity__sport='baseball',
        entity__is_active=True,
    ).select_related('entity')

    updated = 0
    for team in teams:
        standing = team_standings_by_id.get(team.entity.external_id)
        if not standing:
            standing = team_standings_by_name.get(team.entity.name.lower())

        if not standing:
            logger.warning(f"MLB: No standing found for team {team.entity.name}")
            continue

        wins = int(standing.get('won') or 0)
        losses = int(standing.get('lost') or 0)
        played = wins + losses

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
                    'wins': wins,
                    'losses': losses,
                    'matches_played': played,
                    'win_percentage': float(team.win_percentage),
                    'runs_scored': int(standing.get('runs_scored') or 0),
                    'runs_allowed': int(standing.get('runs_allowed') or 0),
                    'runs_diff': int(standing.get('runs_diff') or 0),
                    'conference': standing.get('_conference', ''),
                    'division': standing.get('_division', ''),
                    'rank': int(standing.get('position') or 0),
                    'streak': standing.get('current_streak', ''),
                    'home_record': standing.get('home_record', ''),
                    'road_record': standing.get('road_record', ''),
                }
            }
        )
        updated += 1

    logger.info(f"MLB: updated {updated} teams for season {season}")
    return f"MLB: updated {updated} teams"


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def update_hockey_team_stats(self):
    """
    Fetch NHL standings once using StatPal, update all NHL teams.
    """
    season = _current_season('hockey')
    cache_key = f"standings:hockey:{season}"

    standings = cache.get(cache_key)

    if standings is None:
        result = statpal_service.get_nhl_standings()
        if not result['success']:
            status_code = result.get('status_code')
            if status_code == 429:
                raise self.retry(exc=Exception("Rate limited"), countdown=120)
            return f"Failed to fetch NHL standings: {result.get('error')}"

        standings = result['data'].get('standings', {})
        cache.set(cache_key, standings, timeout=3600)

    team_standings_by_name = {}

    tourn = standings.get('tournament', {})
    leagues = tourn.get('league', [])
    if isinstance(leagues, dict):
        leagues = [leagues]

    for lg in leagues:
        divs = lg.get('division', [])
        if isinstance(divs, dict):
            divs = [divs]
        for div in divs:
            teams_list = div.get('team', [])
            if isinstance(teams_list, dict):
                teams_list = [teams_list]
            for standing in teams_list:
                tname = standing.get('name', '')
                standing['_conference'] = lg.get('name', '')
                standing['_division'] = div.get('name', '')
                if tname:
                    team_standings_by_name[tname.lower()] = standing

    if not team_standings_by_name:
        return "No NHL standings data found"

    teams = Team.objects.filter(
        entity__sport='hockey',
        entity__is_active=True,
    ).select_related('entity')

    updated = 0
    for team in teams:
        standing = team_standings_by_name.get(team.entity.name.lower())

        if not standing:
            logger.warning(f"Hockey name mismatch: standing not found for team '{team.entity.name}'")
            continue

        wins = int(standing.get('won') or standing.get('regular_ot_wins') or 0)
        losses = int(standing.get('lost') or 0)
        ot_losses = int(standing.get('ot_losses') or 0)
        played = int(standing.get('games_played') or (wins + losses + ot_losses))

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
                    'wins': wins,
                    'losses': losses,
                    'ot_losses': ot_losses,
                    'points': int(standing.get('points') or 0),
                    'matches_played': played,
                    'goals_for': int(standing.get('goals_for') or 0),
                    'goals_against': int(standing.get('goals_against') or 0),
                    'difference': standing.get('difference', ''),
                    'conference': standing.get('_conference', ''),
                    'division': standing.get('_division', ''),
                    'rank': int(standing.get('position') or 0),
                    'streak': standing.get('streak', ''),
                    'home_record': standing.get('home_record', ''),
                    'road_record': standing.get('road_record', ''),
                }
            }
        )
        updated += 1

    logger.info(f"NHL: updated {updated} teams for season {season}")
    return f"NHL: updated {updated} teams"


@shared_task
def bootstrap_all_entities():
    """
    Comprehensive bootstrap: ensures EVERY active entity in DB has fresh data.
    
    - Fetches recent news from Brave News API for all entities
    - Seeds roster for soccer teams without players
    
    Runs weekly (Sunday 3am) to catch any new entities added via admin.
    Also used for one-time backfill on deployments.
    """
    from apps.feed.tasks import fetch_brave_news_for_entity
    from django.core.management import call_command
    from apps.entity.models import Entity, Athlete
    
    # ── Auto-seed if VPS database is empty/unpopulated ──
    team_count = Entity.objects.filter(type='team').count()
    athlete_count = Athlete.objects.count()
    
    if team_count < 50:
        logger.info(f"Database has only {team_count} teams. Running populate_major_entities command...")
        try:
            call_command('populate_major_entities')
        except Exception as e:
            logger.exception(f"Auto-population of major entities failed: {e}")
            
    if athlete_count < 100:
        logger.info(f"Database has only {athlete_count} athletes. Running populate_athletes command...")
        try:
            call_command('populate_athletes')
        except Exception as e:
            logger.exception(f"Auto-population of athletes failed: {e}")

    entities = Entity.objects.filter(is_active=True)
    total = entities.count()
    
    logger.info(f"Starting bootstrap of {total} entities")
    
    for i, entity in enumerate(entities):
        # Fetch news only if the entity is followed, to conserve Brave Search API quota
        if entity.follower_count > 0:
            fetch_brave_news_for_entity.apply_async(
                args=[entity.id],
                countdown=i * 3
            )
        
        # Seed roster for soccer teams with no players yet
        if entity.type == 'team' and entity.sport == 'soccer' and entity.api_source == 'api_sports':
            from apps.entity.models import Athlete
            has_players = Athlete.objects.filter(
                entity__external_id=entity.external_id,
                entity__api_source='api_sports'
            ).exists()
            
            # Check if team has been linked to players (indirect way through current_team)
            # If no direct athletes, try seeding
            if not has_players and entity.external_id:
                seed_players_for_team.apply_async(
                    args=[entity.external_id],
                    countdown=i * 3 + 1
                )
    
    logger.info(f"Bootstrap dispatched {total} entities")
    return f"Bootstrapped {total} entities — news + roster"
