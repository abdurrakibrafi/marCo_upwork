import logging
import time

import requests as req
from celery import shared_task
from django.conf import settings

from apps.entity.models import Entity, Athlete
from apps.core.views import _get_or_create_entity

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=5, default_retry_delay=60)
def seed_nba_players_task(self, season=2026, per_page=100, cursor=None, page=1):
    """Seed NBA players in background using StatPal roster service."""
    from apps.sports_apis.services.statpal import statpal_service

    nba_teams = Entity.objects.filter(
        sport='basketball',
        type='team',
    )

    created_count = 0
    for team_entity in nba_teams:
        team_abbr = team_entity.name.split()[-1] if team_entity.name else ''
        if not team_abbr:
            continue
        try:
            resp = statpal_service.get_nba_roster(team_abbr)
            if not resp.get('success'):
                continue

            players = resp.get('data', {}).get('team', {}).get('player', [])
            if isinstance(players, dict):
                players = [players]

            for p in players:
                p_name = p.get('name', '').strip()
                p_id = p.get('id', '')
                if not p_name or not p_id:
                    continue

                entity, created = _get_or_create_entity(
                    name=p_name,
                    entity_type='athlete',
                    sport='basketball',
                    external_id=str(p_id),
                    api_source='statpal',
                    country='USA',
                )

                try:
                    athlete = entity.athlete_details
                    athlete.position = p.get('position', '') or ''
                    athlete.current_team = team_entity
                    athlete.save()
                except Athlete.DoesNotExist:
                    pass

                if created:
                    created_count += 1
        except Exception as exc:
            logger.warning(f"Error seeding roster for NBA team {team_entity.name}: {exc}")

    return {
        'status': 'completed',
        'created_total': created_count,
    }

