from celery import shared_task
from django.core.cache import cache
from django.conf import settings
from apps.entity.models import Entity, Team, Athlete, League, EntityStats
from apps.sports_apis.services.balldontlie import balldontlie_service
from apps.sports_apis.services.api_sports import api_sports_service
import logging

logger = logging.getLogger(__name__)


@shared_task
def update_nba_team_stats(team_id: int):
    """
    Update NBA team statistics
    
    Args:
        team_id: Entity ID of the team
    """
    try:
        entity = Entity.objects.get(id=team_id, type='team', sport='basketball')
        team = entity.team_details
    except (Entity.DoesNotExist, Team.DoesNotExist):
        return f"Team {team_id} not found"
    
    logger.info(f"Updating stats for {entity.name}")
    
    # Get team stats from BallDontLie
    # Note: BallDontLie doesn't have dedicated team stats endpoint
    # We'll get standings which includes W-L records
    result = balldontlie_service.get_standings('nba')
    
    if result['success']:
        standings = result['data'].get('data', [])
        
        # Find this team in standings
        for standing in standings:
            if str(standing.get('team', {}).get('id')) == entity.external_id:
                # Update team record
                team.total_wins = standing.get('wins', 0)
                team.total_losses = standing.get('losses', 0)
                
                total_games = team.total_wins + team.total_losses
                if total_games > 0:
                    team.win_percentage = (team.total_wins / total_games) * 100
                
                team.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])
                
                # Save detailed stats
                EntityStats.objects.update_or_create(
                    entity=entity,
                    season='2023-24',
                    stat_type='season',
                    defaults={
                        'stats_data': {
                            'wins': team.total_wins,
                            'losses': team.total_losses,
                            'win_pct': float(team.win_percentage),
                            'conference': standing.get('conference', ''),
                            'division': standing.get('division', ''),
                            'rank': standing.get('rank', 0),
                        }
                    }
                )
                
                logger.info(f"Updated stats for {entity.name}: {team.total_wins}-{team.total_losses}")
                return f"Updated {entity.name}: {team.total_wins}-{team.total_losses}"
        
        return f"Team {entity.name} not found in standings"
    
    return f"Failed to fetch standings for {entity.name}"


@shared_task
def update_soccer_team_stats(team_id: int, league_id: int = 39, season: int = 2023):
    """
    Update soccer team statistics
    
    Args:
        team_id: Entity ID of the team
        league_id: API-Sports league ID (default 39 = Premier League)
        season: Season year
    """
    try:
        entity = Entity.objects.get(id=team_id, type='team', sport='soccer')
        team = entity.team_details
    except (Entity.DoesNotExist, Team.DoesNotExist):
        return f"Team {team_id} not found"
    
    logger.info(f"Updating soccer stats for {entity.name}")
    
    # Get standings
    result = api_sports_service.get_standings(league_id, season)
    
    if result['success']:
        standings = result['data'].get('response', [])
        
        for standing_group in standings:
            league_standings = standing_group.get('league', {}).get('standings', [[]])[0]
            
            for standing in league_standings:
                team_data = standing.get('team', {})
                
                if str(team_data.get('id')) == entity.external_id:
                    all_stats = standing.get('all', {})
                    
                    # Update team record
                    team.total_wins = all_stats.get('win', 0)
                    team.total_losses = all_stats.get('lose', 0)
                    draws = all_stats.get('draw', 0)
                    
                    total_games = team.total_wins + team.total_losses + draws
                    if total_games > 0:
                        team.win_percentage = (team.total_wins / total_games) * 100
                    
                    team.save(update_fields=['total_wins', 'total_losses', 'win_percentage'])
                    
                    # Save detailed stats
                    EntityStats.objects.update_or_create(
                        entity=entity,
                        season=str(season),
                        stat_type='season',
                        defaults={
                            'stats_data': {
                                'rank': standing.get('rank', 0),
                                'points': standing.get('points', 0),
                                'played': all_stats.get('played', 0),
                                'win': team.total_wins,
                                'draw': draws,
                                'lose': team.total_losses,
                                'goals_for': all_stats.get('goals', {}).get('for', 0),
                                'goals_against': all_stats.get('goals', {}).get('against', 0),
                                'goal_diff': standing.get('goalsDiff', 0),
                                'form': standing.get('form', ''),
                            }
                        }
                    )
                    
                    logger.info(f"Updated soccer stats for {entity.name}")
                    return f"Updated {entity.name} stats"
        
        return f"Team {entity.name} not found in standings"
    
    return f"Failed to fetch soccer standings"


@shared_task
def update_nba_player_stats(athlete_id: int, season: str = '2023'):
    """
    Update NBA player statistics
    
    Args:
        athlete_id: Entity ID of the athlete
        season: Season year
    """
    try:
        entity = Entity.objects.get(id=athlete_id, type='athlete', sport='basketball')
        athlete = entity.athlete_details
    except (Entity.DoesNotExist, Athlete.DoesNotExist):
        return f"Athlete {athlete_id} not found"
    
    logger.info(f"Updating player stats for {entity.name}")
    
    # Note: BallDontLie player stats require player's API ID
    # For now, we'll cache basic info
    # In production, you'd call player stats endpoint with external_id
    
    EntityStats.objects.update_or_create(
        entity=entity,
        season=season,
        stat_type='season',
        defaults={
            'stats_data': {
                'player_id': entity.external_id,
                'team': athlete.current_team.name if athlete.current_team else '',
                'position': athlete.position,
                # In production, add: points, rebounds, assists, etc.
            }
        }
    )
    
    return f"Updated stats for {entity.name}"


@shared_task
def update_team_roster(team_id: int):
    """
    Update team roster from API
    
    Args:
        team_id: Entity ID of the team
    """
    try:
        entity = Entity.objects.get(id=team_id, type='team')
    except Entity.DoesNotExist:
        return f"Team {team_id} not found"
    
    logger.info(f"Updating roster for {entity.name}")
    
    # Basketball teams (BallDontLie doesn't have roster endpoint in free tier)
    # Soccer teams - use API-Sports
    if entity.sport == 'soccer':
        # Get team details which includes squad
        league_id = 39  # Premier League, adjust based on team's league
        season = 2023
        
        result = api_sports_service.get_teams(league_id, season)
        
        if result['success']:
            teams = result['data'].get('response', [])
            
            for team_data in teams:
                if str(team_data['team']['id']) == entity.external_id:
                    # In production, fetch squad data
                    # For now, just log
                    logger.info(f"Found team data for {entity.name}")
                    return f"Roster update complete for {entity.name}"
    
    return f"Roster update not available for {entity.name}"


@shared_task
def update_all_team_stats():
    """
    Update stats for all active teams
    """
    # NBA teams
    nba_teams = Entity.objects.filter(
        type='team',
        sport='basketball',
        is_active=True,
        has_api_data=True
    )
    
    for team in nba_teams:
        update_nba_team_stats.delay(team.id)
    
    # Soccer teams
    soccer_teams = Entity.objects.filter(
        type='team',
        sport='soccer',
        is_active=True,
        has_api_data=True
    )
    
    for team in soccer_teams:
        update_soccer_team_stats.delay(team.id)
    
    total = nba_teams.count() + soccer_teams.count()
    return f"Triggered stats update for {total} teams"