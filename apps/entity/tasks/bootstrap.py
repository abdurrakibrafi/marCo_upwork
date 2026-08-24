import requests as req
from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.cache import cache
from apps.entity.models import Entity, Athlete
from apps.sports_apis.services.statpal import statpal_service

logger = get_task_logger(__name__)


@shared_task
def seed_players_for_team(team_external_id: str, season: int = None):
    """Seed or synchronize athlete rosters for a specific team entity.

    Attempts multi-tier resolution: TheSportsDB -> Wikipedia fallback -> API-Sports -> StatPal.

    Args:
        team_external_id (str): Remote ID of the team entity.
        season (int, optional): Season year filter.

    Returns:
        str: Summary of created/synced players count.
    """
    team_entity = Entity.objects.filter(
        external_id=str(team_external_id)
    ).first()

    if not team_entity:
        return f"Team {team_external_id} not found in DB"

    if team_entity.api_source == 'thesportsdb' or not team_entity.api_source:
        try:
            from apps.sports_apis.services.thesportsdb import TheSportsDBService
            tsdb = TheSportsDBService()
            players = tsdb.get_team_roster(team_id=team_entity.external_id, team_name=team_entity.name)
            created_total = 0
            for p in players:
                p_ext_id = str(p.get('id_player') or f"tsdb_{p['name'].replace(' ', '_').lower()}")
                player_entity, _ = Entity.objects.get_or_create(
                    api_source='thesportsdb',
                    external_id=p_ext_id,
                    defaults={
                        'type': 'athlete',
                        'name': p['name'],
                        'sport': team_entity.sport,
                        'logo_url': p.get('headshot_url', '') or '',
                        'has_api_data': True,
                    }
                )
                name_parts = p['name'].strip().split()
                first_name = name_parts[0] if name_parts else ''
                last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                _, was_created = Athlete.objects.get_or_create(
                    entity=player_entity,
                    defaults={
                        'first_name': first_name,
                        'last_name': last_name,
                        'current_team': team_entity,
                        'position': p.get('position', ''),
                        'nationality': p.get('nationality', ''),
                    }
                )
                if was_created:
                    created_total += 1

            if created_total < 8:
                try:
                    from apps.sports_apis.services.wikipedia import wikipedia_service
                    wiki_players = wikipedia_service.get_team_roster(team_name=team_entity.name, sport=team_entity.sport)
                    for p in wiki_players:
                        p_name = p.get('name', '').strip()
                        if not p_name:
                            continue
                        p_ext_id = f"wiki_{p_name.replace(' ', '_').lower()}_{team_entity.id}"
                        player_entity = Entity.objects.filter(
                            name=p_name,
                            type='athlete',
                            sport=team_entity.sport
                        ).first()
                        if not player_entity:
                            player_entity = Entity.objects.create(
                                name=p_name,
                                type='athlete',
                                sport=team_entity.sport,
                                api_source='wikipedia',
                                external_id=p_ext_id,
                                country=p.get('nationality', '') or team_entity.country or '',
                                has_api_data=True,
                            )
                        name_parts = p_name.split(' ', 1)
                        first_name = name_parts[0] if name_parts else ''
                        last_name = name_parts[1] if len(name_parts) > 1 else ''

                        ath_obj = Athlete.objects.filter(entity=player_entity).first()
                        if not ath_obj:
                            ath_obj = Athlete.objects.create(
                                entity=player_entity,
                                first_name=first_name,
                                last_name=last_name,
                                current_team=team_entity,
                                position=p.get('position', ''),
                                jersey_number=p.get('jersey_number'),
                                nationality=p.get('nationality', '') or team_entity.country or '',
                            )
                            created_total += 1
                        else:
                            if ath_obj.current_team != team_entity:
                                ath_obj.current_team = team_entity
                                ath_obj.save()
                except Exception as wiki_err:
                    logger.warning(f"Wikipedia fallback in Celery task failed for {team_entity.name}: {wiki_err}")

            return f"Seeded {created_total} players for team {team_external_id}"
        except Exception as e:
            logger.warning(f"Player seeding failed for {team_entity.name}: {e}")

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


@shared_task
def bootstrap_all_entities():
    """Comprehensive bootstrap: ensures EVERY active entity in DB has fresh data.
    
    - Fetches recent news from Brave News API for all entities
    - Seeds roster for soccer teams without players
    
    Runs weekly (Sunday 3am) to catch any new entities added via admin.
    Also used for one-time backfill on deployments.
    """
    from apps.feed.tasks import fetch_brave_news_for_entity
    from django.core.management import call_command
    from apps.entity.models import Entity, Athlete
    
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
    
    from apps.feed.tasks import discover_rss_feeds_for_entity
    for i, entity in enumerate(entities):
        if not entity.rss_discovery_done and entity.follower_count > 0:
            discover_rss_feeds_for_entity.apply_async(
                args=[entity.id],
                countdown=i * 3
            )
        
        if entity.type == 'team' and entity.sport == 'soccer' and entity.api_source == 'statpal':
            has_players = Athlete.objects.filter(
                entity__external_id=entity.external_id,
                entity__api_source='statpal'
            ).exists()
            
            if not has_players and entity.external_id:
                seed_players_for_team.apply_async(
                    args=[entity.external_id],
                    countdown=i * 3 + 1
                )
    
    logger.info(f"Bootstrap dispatched {total} entities")
    return f"Bootstrapped {total} entities — news + roster"


@shared_task(bind=True, max_retries=1, default_retry_delay=60, ignore_result=True)
def warm_venue_cache_task(self, team_name: str):
    """Populates the venue cache for a single team name by calling resolve_team_venue.

    Args:
        self: Bound Celery task instance.
        team_name (str): Team name to warm.
    """
    if not team_name:
        return

    lock_key = f"warm_venue_lock_{team_name.lower().replace(' ', '_')}"
    if not cache.add(lock_key, '1', timeout=120):
        return

    try:
        from apps.entity.utils.matcher import resolve_team_venue
        v_name, v_city, v_country = resolve_team_venue(team_name)
        logger.info(
            'warm_venue_cache_task: %s → venue=%s city=%s country=%s',
            team_name, v_name or '(empty)', v_city or '(empty)', v_country or '(empty)'
        )
    except Exception as exc:
        logger.warning('warm_venue_cache_task failed for %s: %s', team_name, exc)
        try:
            raise self.retry(exc=exc)
        except Exception:
            pass
    finally:
        cache.delete(lock_key)


@shared_task(bind=True, max_retries=1, ignore_result=True)
def warm_all_venue_caches(self):
    """Proactively warm venue name/city cache for every team entity in the DB.

    Runs daily at 3am via Celery beat.
    """
    lock_id = 'warm_all_venue_caches_lock'
    if not cache.add(lock_id, '1', timeout=7200):
        logger.info('warm_all_venue_caches already running, skipping')
        return 'skipped — already running'

    try:
        teams = (
            Entity.objects
            .filter(type='team')
            .exclude(name='')
            .values_list('name', flat=True)
            .distinct()
        )

        dispatched = 0
        for i, name in enumerate(teams):
            cache_key = f"venue_by_name_{name.lower().replace(' ', '_')}"
            if cache.get(cache_key) is None:
                warm_venue_cache_task.apply_async(
                    args=[name],
                    countdown=i * 2,
                )
                dispatched += 1

        msg = f'warm_all_venue_caches: dispatched {dispatched} team venue warmers'
        logger.info(msg)
        return msg

    except Exception as exc:
        logger.exception('warm_all_venue_caches failed: %s', exc)
    finally:
        cache.delete(lock_id)
