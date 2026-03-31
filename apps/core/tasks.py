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
    """Seed NBA players in background with pagination and rate-limit handling."""

    teams_dict = {
        int(team.external_id): team
        for team in Entity.objects.filter(
            sport='basketball',
            type='team',
            api_source='balldontlie'
        )
    }

    params = {'per_page': per_page, 'season': season}
    if cursor:
        params['cursor'] = cursor

    url = 'https://api.balldontlie.io/v1/players'

    try:
        logger.info(f"NBA seed task page={page}, cursor={cursor}, params={params}")
        resp = req.get(url, headers={'Authorization': settings.BALLDONTLIE_KEY}, params=params, timeout=15)

        if resp.status_code == 429:
            # API limit hit: wait and retry
            logger.warning('BallDontLie rate limit hit; retrying...')
            raise self.retry(countdown=60, exc=Exception('rate limited'))

        if resp.status_code != 200:
            message = f'BallDontLie returned {resp.status_code}'
            logger.error(message)
            raise Exception(message)

        data = resp.json()
        players = data.get('data', [])

        created_count = 0
        for p in players:
            name = f"{p.get('first_name', '').strip()} {p.get('last_name', '').strip()}".strip()
            if not name:
                continue

            team_data = p.get('team')
            team_entity = None
            if team_data and team_data.get('id') in teams_dict:
                team_entity = teams_dict[team_data['id']]

            entity, created = _get_or_create_entity(
                name=name,
                entity_type='athlete',
                sport='basketball',
                external_id=p['id'],
                api_source='balldontlie',
                logo_url=f"https://cdn.nba.com/headshots/nba/latest/1040x760/{p['id']}.png",
                country=p.get('country', ''),
            )

            try:
                athlete = entity.athlete_details
                athlete.position = p.get('position', '') or ''
                jersey = p.get('jersey_number')
                if jersey:
                    jersey = str(jersey).split('-')[0]
                    try:
                        athlete.jersey_number = int(jersey)
                    except (ValueError, TypeError):
                        athlete.jersey_number = None
                else:
                    athlete.jersey_number = None

                if team_entity:
                    athlete.current_team = team_entity
                athlete.save()
            except Athlete.DoesNotExist:
                pass

            if created:
                created_count += 1

        next_cursor = data.get('meta', {}).get('next_cursor')

        if next_cursor:
            # Enqueue the next page after a throttle-safe delay
            delay = 12
            if page % 5 == 0:
                delay = 15

            self.apply_async(
                kwargs={
                    'season': season,
                    'per_page': per_page,
                    'cursor': next_cursor,
                    'page': page + 1,
                },
                countdown=delay,
            )

            return {
                'status': 'in_progress',
                'page': page,
                'created_this_page': created_count,
                'next_cursor': next_cursor,
            }

        return {
            'status': 'completed',
            'season': season,
            'pages_fetched': page,
            'created_this_page': created_count,
            'total_players_fetched': len(players),
        }

    except Exception as exc:
        logger.error(f"seed_nba_players_task failed: {exc}")
        raise
