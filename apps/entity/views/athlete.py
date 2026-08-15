import logging
import requests
from django.shortcuts import get_object_or_404
from django.core.cache import cache
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.entity.models import Entity, Athlete, EntityStats
from apps.entity.serializers import EntitySerializer
from .common import _current_season, HEADERS_SPORTS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# ATHLETE STATS
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_stats(request, athlete_id):
    """
    GET /api/entities/athlete/{athlete_id}/stats/?season=2024
    """
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    athlete_entity = athlete_entity.canonical_entity or athlete_entity
    season = request.GET.get('season') or str(_current_season(athlete_entity.sport))
    force_refresh = request.GET.get('force_refresh', '').lower() in ('true', '1')

    # 1 — try DB first (unless force_refresh is requested)
    if not force_refresh:
        stats = EntityStats.objects.filter(entity=athlete_entity, season=season).first()
        if stats and stats.stats_data:
            non_empty_count = sum(1 for v in stats.stats_data.values() if bool(v))
            if non_empty_count >= 3:
                return Response({
                    'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
                    'season':  season,
                    'stats':   stats.stats_data,
                    'source':  'db',
                })

    # 2 — live API fallback & multi-source data merging
    stats_data = {}

    # A) Try TheSportsDB first for profile bio, stats, images, and attributes
    if athlete_entity.name:
        tsdb_stats = _fetch_thesportsdb_player_stats(athlete_entity.name, athlete_entity=athlete_entity, force_refresh=force_refresh)
        if tsdb_stats:
            stats_data.update(tsdb_stats)

    # B) Try API-Football performance stats if external_id is available and needed
    if not stats_data and athlete_entity.external_id and athlete_entity.sport == 'soccer':
        soccer_stats = _fetch_soccer_player_stats(athlete_entity.external_id, season)
        if soccer_stats:
            stats_data.update(soccer_stats)

    # C) Enrich remaining empty fields with local Athlete DB details if available
    ad = getattr(athlete_entity, 'athlete_details', None)
    if ad:
        if not stats_data.get('position') and ad.position:
            stats_data['position'] = ad.position
        if not stats_data.get('nationality') and ad.nationality:
            stats_data['nationality'] = ad.nationality
        if not stats_data.get('height') and ad.height_cm:
            stats_data['height'] = f"{ad.height_cm} cm"
        if not stats_data.get('weight') and ad.weight_kg:
            stats_data['weight'] = f"{ad.weight_kg} kg"
        if not stats_data.get('date_of_birth') and ad.date_of_birth:
            stats_data['date_of_birth'] = str(ad.date_of_birth)
        if not stats_data.get('team') and ad.current_team:
            stats_data['team'] = ad.current_team.name

    # 3 — save combined stats to DB
    if stats_data:
        EntityStats.objects.update_or_create(
            entity=athlete_entity,
            season=season,
            stat_type='season',
            defaults={'stats_data': stats_data},
        )

    return Response({
        'athlete': EntitySerializer(athlete_entity, context={'request': request}).data,
        'season':  season,
        'stats':   stats_data,
        'source':  'live_api' if stats_data else 'empty',
    })


def _fetch_soccer_player_stats(external_id, season):
    cache_key = f'player_stats:soccer:{external_id}:{season}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    try:
        resp = requests.get(
            'https://v3.football.api-sports.io/players',
            headers=HEADERS_SPORTS,
            params={'id': external_id, 'season': season},
            timeout=10,
        )
        if resp.status_code != 200:
            return {}

        response = resp.json().get('response', [])
        if not response:
            return {}

        player   = response[0]
        p_info   = player.get('player', {})
        s        = player.get('statistics', [{}])[0]
        games    = s.get('games', {})
        goals    = s.get('goals', {})
        passes   = s.get('passes', {})
        cards    = s.get('cards', {})
        shots    = s.get('shots', {})
        dribbles = s.get('dribbles', {})

        stats_data = {
            'appearances':  games.get('appearences', 0),
            'minutes':      games.get('minutes', 0),
            'rating':       games.get('rating'),
            'goals':        goals.get('total', 0),
            'assists':      goals.get('assists', 0),
            'shots_total':  shots.get('total', 0),
            'shots_on':     shots.get('on', 0),
            'passes_total': passes.get('total', 0),
            'passes_key':   passes.get('key', 0),
            'pass_accuracy':passes.get('accuracy', 0),
            'dribbles_success': dribbles.get('success', 0),
            'yellow_cards': cards.get('yellow', 0),
            'red_cards':    cards.get('red', 0),
            'nationality':  p_info.get('nationality', ''),
            'height':       p_info.get('height', ''),
            'weight':       p_info.get('weight', ''),
            'age':          p_info.get('age', 0),
        }
        cache.set(cache_key, stats_data, timeout=3600)
        return stats_data

    except Exception:
        return {}


def _fetch_thesportsdb_player_stats(player_name, athlete_entity=None, force_refresh=False):
    cache_key = f'player_stats:thesportsdb:{player_name.lower().strip()}'
    if force_refresh:
        cache.delete(cache_key)

    cached = cache.get(cache_key)
    if cached and not force_refresh:
        if athlete_entity:
            try:
                ad = getattr(athlete_entity, 'athlete_details', None)
                if ad:
                    if not cached.get('position') and ad.position:
                        cached['position'] = ad.position
                    if not cached.get('nationality') and ad.nationality:
                        cached['nationality'] = ad.nationality
                    if not cached.get('height') and ad.height_cm:
                        cached['height'] = f"{ad.height_cm} cm"
                    if not cached.get('weight') and ad.weight_kg:
                        cached['weight'] = f"{ad.weight_kg} kg"
                    if not cached.get('date_of_birth') and ad.date_of_birth:
                        cached['date_of_birth'] = str(ad.date_of_birth)
                    if not cached.get('team') and ad.current_team:
                        cached['team'] = ad.current_team.name
                if not cached.get('description') and athlete_entity.description:
                    cached['description'] = athlete_entity.description
                if not cached.get('headshot_url') and athlete_entity.logo_url:
                    cached['headshot_url'] = athlete_entity.logo_url
            except Exception:
                pass
        return cached

    try:
        from apps.sports_apis.services.thesportsdb import thesportsdb_service
        player_info = thesportsdb_service.get_player_details(player_name) or {}

        raw = player_info.get('raw_data', {})
        pos = player_info.get('position', '')
        nat = player_info.get('nationality', '')
        h = player_info.get('height', '')
        w = player_info.get('weight', '')
        team = player_info.get('team_name', '')
        dob = player_info.get('date_of_birth', '')
        desc = player_info.get('description', '')
        headshot = player_info.get('headshot_url', '')

        # Enrich empty fields with local Athlete DB details if available
        if athlete_entity:
            try:
                ad = getattr(athlete_entity, 'athlete_details', None)
                if ad:
                    if not pos and ad.position:
                        pos = ad.position
                    if not nat and ad.nationality:
                        nat = ad.nationality
                    if not h and ad.height_cm:
                        h = f"{ad.height_cm} cm"
                    if not w and ad.weight_kg:
                        w = f"{ad.weight_kg} kg"
                    if not dob and ad.date_of_birth:
                        dob = str(ad.date_of_birth)
                    if not team and ad.current_team:
                        team = ad.current_team.name
                if not desc and athlete_entity.description:
                    desc = athlete_entity.description
                if not headshot and athlete_entity.logo_url:
                    headshot = athlete_entity.logo_url

                # Permanently persist fetched static fields into DB models (Entity & Athlete)
                entity_fields_to_save = []
                if desc and not athlete_entity.description:
                    athlete_entity.description = desc
                    entity_fields_to_save.append('description')
                if headshot and not athlete_entity.logo_url:
                    athlete_entity.logo_url = headshot
                    entity_fields_to_save.append('logo_url')
                if entity_fields_to_save:
                    athlete_entity.save(update_fields=entity_fields_to_save)

                from apps.entity.models import Athlete
                athlete_detail_obj, _ = Athlete.objects.get_or_create(
                    entity=athlete_entity,
                    defaults={'first_name': athlete_entity.name.split(' ')[0], 'last_name': ' '.join(athlete_entity.name.split(' ')[1:])}
                )
                ad_updated = False
                if pos and not athlete_detail_obj.position:
                    athlete_detail_obj.position = pos
                    ad_updated = True
                if nat and not athlete_detail_obj.nationality:
                    athlete_detail_obj.nationality = nat
                    ad_updated = True
                if dob and not athlete_detail_obj.date_of_birth:
                    try:
                        from datetime import datetime
                        athlete_detail_obj.date_of_birth = datetime.strptime(dob, '%Y-%m-%d').date()
                        ad_updated = True
                    except Exception:
                        pass
                if h and not athlete_detail_obj.height_cm:
                    try:
                        import re
                        m = re.search(r'\d+', str(h))
                        if m:
                            athlete_detail_obj.height_cm = int(m.group(0))
                            ad_updated = True
                    except Exception:
                        pass
                if w and not athlete_detail_obj.weight_kg:
                    try:
                        import re
                        m = re.search(r'\d+', str(w))
                        if m:
                            athlete_detail_obj.weight_kg = int(m.group(0))
                            ad_updated = True
                    except Exception:
                        pass
                if ad_updated:
                    athlete_detail_obj.save()
            except Exception as err:
                logger.debug(f"Failed to persist athlete_details for {athlete_entity.name}: {err}")

        stats_data = {
            'position': pos,
            'nationality': nat,
            'height': h,
            'weight': w,
            'team': team,
            'date_of_birth': dob,
            'birth_location': raw.get('strBirthLocation', '') or '',
            'number': raw.get('strNumber', '') or '',
            'side': raw.get('strSide', '') or '',
            'status': raw.get('strStatus', '') or '',
            'outfitter': raw.get('strOutfitter', '') or '',
            'agent': raw.get('strAgent', '') or '',
            'date_signed': raw.get('dateSigned', '') or '',
            'description': desc,
            'headshot_url': headshot,
            'signing_fee': raw.get('strSigning', '') or '',
            'wage': raw.get('strWage', '') or '',
            'kit': raw.get('strKit', '') or raw.get('strNumber', '') or '',
        }
        if any(stats_data.values()):
            cache.set(cache_key, stats_data, timeout=86400)
        return stats_data
    except Exception as e:
        logger.warning(f"TheSportsDB player stats lookup failed for '{player_name}': {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# ATHLETE BIO
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['GET'])
@permission_classes([AllowAny])
def get_athlete_bio(request, athlete_id):
    athlete_entity = get_object_or_404(Entity, id=athlete_id, type='athlete')
    athlete_entity = athlete_entity.canonical_entity or athlete_entity
    try:
        athlete = athlete_entity.athlete_details
    except Athlete.DoesNotExist:
        return Response({'error': 'Athlete details not found'}, status=404)

    nationality = athlete.nationality or athlete_entity.country or ''
    bio = athlete_entity.description or ''
    photo = athlete_entity.logo_url or ''

    # Enrich missing fields from TheSportsDB if needed
    if not (athlete.nationality and bio and photo):
        try:
            from apps.sports_apis.services.thesportsdb import thesportsdb_service
            tsdb_info = thesportsdb_service.get_player_details(athlete_entity.name) or {}
            if tsdb_info:
                if tsdb_info.get('nationality'):
                    nationality = tsdb_info.get('nationality')
                    athlete.nationality = nationality
                    athlete.save(update_fields=['nationality'])
                if not bio and tsdb_info.get('description'):
                    bio = tsdb_info.get('description')
                    athlete_entity.description = bio
                    athlete_entity.save(update_fields=['description'])
                if not photo and tsdb_info.get('headshot_url'):
                    photo = tsdb_info.get('headshot_url')
                    athlete_entity.logo_url = photo
                    athlete_entity.save(update_fields=['logo_url'])
        except Exception:
            pass

    return Response({
        'id':                     athlete_entity.id,
        'name':                   f"{athlete.first_name} {athlete.last_name}".strip() or athlete_entity.name,
        'photo':                  athlete_entity.logo_url or '',
        'date_of_birth':          str(athlete.date_of_birth) if athlete.date_of_birth else '',
        'age':                    athlete.age,
        'nationality':            nationality,
        'height_cm':              athlete.height_cm,
        'weight_kg':              athlete.weight_kg,
        'current_team':           EntitySerializer(athlete.current_team, context={'request': request}).data if athlete.current_team else None,
        'position':               athlete.position,
        'jersey_number':          athlete.jersey_number,
        'twitter':                athlete.twitter_handle or '',
        'instagram':              athlete.instagram_handle or '',
        'bio':                    bio,
    })
