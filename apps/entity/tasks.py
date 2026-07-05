from celery import shared_task
from celery.utils.log import get_task_logger
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

    logger.info(
        f"Dispatched stats update: {soccer_count} soccer leagues, "
        f"{'1 NBA task' if has_nba else 'no NBA teams'}"
    )
    return f"Dispatched {soccer_count} soccer league tasks + {'NBA' if has_nba else 'no NBA'}"


@shared_task
def seed_players_for_team(team_external_id: str, season: int = None):
    from apps.entity.models import Entity, Athlete

    team_entity = Entity.objects.filter(
        api_source__in=['api_sports', 'statpal'],
        external_id=str(team_external_id)
    ).first()

    if not team_entity:
        return f"Team {team_external_id} not found in DB"

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