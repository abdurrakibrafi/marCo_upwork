from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from datetime import datetime
import time
import logging
import requests as req

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django.conf import settings

from apps.entity.models import Entity, Team, Athlete, League
from apps.core.utils.mixins import BaseResponseMixin



logger = logging.getLogger(__name__)

HEADERS_SPORTS = {'x-apisports-key': settings.API_SPORTS_KEY}
HEADERS_BDL = {'Authorization': settings.BALLDONTLIE_KEY}


def _get_current_season(sport='soccer'):
    now = datetime.now()
    year, month = now.year, now.month
    if sport == 'soccer':
        return year if month >= 8 else year - 1
    elif sport == 'basketball':
        return year if month >= 10 else year - 1
    return year


def _get_or_create_entity(name, entity_type, sport, external_id, api_source,
                          logo_url='', country=''):
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        defaults={
            'name': name,
            'type': entity_type,
            'sport': sport,
            'logo_url': logo_url,
            'country': country,
            'has_api_data': True,
        }
    )
    if not created and logo_url and not entity.logo_url:
        entity.logo_url = logo_url
        entity.save(update_fields=['logo_url'])

    if entity_type == 'team':
        Team.objects.get_or_create(entity=entity)
    elif entity_type == 'league':
        League.objects.get_or_create(entity=entity)
    elif entity_type == 'athlete':
        parts = name.split(' ', 1)
        Athlete.objects.get_or_create(
            entity=entity,
            defaults={
                'first_name': parts[0],
                'last_name': parts[1] if len(parts) > 1 else '',
            }
        )

    entity.refresh_from_db()
    return entity, created


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Fetch ALL leagues from API-Sports, save to DB
# Run once. Re-running is safe — skips already saved ones.
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_all_leagues(request):
    """
    POST /api/entities/seed/all-leagues/

    Fetches every league API-Sports has and saves them all to DB.
    Run this ONCE on first setup.
    Re-running is safe — already existing leagues are skipped.

    Optional params:
      season = 2025 (default: current season)
      current = true (only leagues active this season — recommended)
    """
    mixin = BaseResponseMixin()
    try:
        season = int(request.GET.get('season', _get_current_season('soccer')))
        current = request.GET.get('current', 'true')  # default: only active leagues

        params = {'season': season}
        if current == 'true':
            params['current'] = 'true'

        resp = req.get(
            'https://v3.football.api-sports.io/leagues',
            headers=HEADERS_SPORTS,
            params=params,
            timeout=30,
        )
        if resp.status_code != 200:
            return mixin.error_response(
                message=f'API-Sports returned {resp.status_code}',
                status_code=status.HTTP_502_BAD_GATEWAY
            )

        all_leagues = resp.json().get('response', [])
        created_count = 0
        skipped_count = 0

        for item in all_leagues:
            lg = item.get('league', {})
            country_data = item.get('country', {})

            league_id = lg.get('id')
            name = lg.get('name', '').strip()
            if not league_id or not name:
                continue

            _, created = _get_or_create_entity(
                name=name,
                entity_type='league',
                sport='soccer',
                external_id=league_id,
                api_source='api_sports',
                logo_url=lg.get('logo', ''),
                country=country_data.get('name', ''),
            )
            if created:
                created_count += 1
            else:
                skipped_count += 1

        data = {
            'total_from_api': len(all_leagues),
            'newly_created': created_count,
            'already_existed': skipped_count,
            'total_leagues_in_db': Entity.objects.filter(
                type='league', sport='soccer', api_source='api_sports'
            ).count(),
        }
        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Fetch teams for all leagues in DB
# Run after seed_all_leagues. Processes in batches to respect rate limits.
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_all_teams(request):
    """
    POST /api/entities/seed/all-teams/

    For every league in the DB, fetches its teams from API-Sports.
    Processes in batches — pass offset to continue from where you stopped.

    Params:
      offset = 0       (which league to start from, default 0)
      limit  = 10      (how many leagues to process per call, default 10)
      season = 2025

    Response tells you next_offset so you know what to call next.
    Keep calling until has_more = false.
    """
    offset = int(request.GET.get('offset', 0))
    limit = int(request.GET.get('limit', 10))
    season = int(request.GET.get('season', _get_current_season('soccer')))

    # Get leagues from DB — whatever is there, no hardcoding
    leagues = list(
        Entity.objects.filter(
            type='league',
            sport='soccer',
            api_source='api_sports',
            is_active=True,
        ).order_by('id')[offset: offset + limit]
    )

    total_leagues_in_db = Entity.objects.filter(
        type='league', sport='soccer', api_source='api_sports'
    ).count()

    if not leagues:
        return Response({
            'success': True,
            'message': 'No more leagues to process',
            'has_more': False,
        })

    results = {}
    rate_limited = False

    for league_entity in leagues:
        league_id = league_entity.external_id
        try:
            resp = req.get(
                'https://v3.football.api-sports.io/teams',
                headers=HEADERS_SPORTS,
                params={'league': league_id, 'season': season},
                timeout=15,
            )

            if resp.status_code == 429:
                rate_limited = True
                results[league_id] = {'error': 'rate_limited'}
                break

            if resp.status_code != 200:
                results[league_id] = {'error': f'HTTP {resp.status_code}'}
                continue

            created = 0
            for item in resp.json().get('response', []):
                t = item.get('team', {})
                venue = item.get('venue', {})

                entity, c = _get_or_create_entity(
                    name=t['name'],
                    entity_type='team',
                    sport='soccer',
                    external_id=t['id'],
                    api_source='api_sports',
                    logo_url=t.get('logo', ''),
                    country=t.get('country', ''),
                )
                try:
                    team = entity.team_details
                    team.league = league_entity
                    team.venue_name = venue.get('name') or ''
                    team.venue_city = venue.get('city') or ''
                    team.venue_capacity = venue.get('capacity') or None
                    team.save()
                except Team.DoesNotExist:
                    pass
                if c:
                    created += 1

            results[league_id] = {
                'league': league_entity.name,
                'newly_created': created,
            }

        except Exception as e:
            results[league_id] = {'error': str(e)}

    return Response({
        'success': True,
        'offset': offset,
        'limit': limit,
        'leagues_processed': len(results),
        'total_leagues_in_db': total_leagues_in_db,
        'has_more': (offset + limit) < total_leagues_in_db and not rate_limited,
        'next_offset': offset + limit,
        'rate_limited': rate_limited,
        'results': results,
    })


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Fetch player squads for all teams in DB
# Run after seed_all_teams. Processes in batches.
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_all_players(request):
    """
    POST /api/entities/seed/all-players/

    For every soccer team in the DB, fetches its full squad.
    Processes in batches — pass offset to continue.

    Params:
      offset = 0   (which team to start from)
      limit  = 50  (teams per call — keep low to avoid rate limits)

    Keep calling with next_offset until has_more = false.
    """
    offset = int(request.GET.get('offset', 0))
    limit = int(request.GET.get('limit', 50))

    # All soccer teams from api_sports in DB — no hardcoding
    teams = list(
        Entity.objects.filter(
            type='team',
            sport='soccer',
            api_source='api_sports',
            is_active=True,
        ).order_by('id')[offset: offset + limit]
    )

    total_teams = Entity.objects.filter(
        type='team', sport='soccer', api_source='api_sports', is_active=True
    ).count()

    if not teams:
        return Response({
            'success': True,
            'message': 'No more teams to process',
            'has_more': False,
        })

    total_created = 0
    total_found = 0
    rate_limited = False

    for team_entity in teams:
        try:
            resp = req.get(
                'https://v3.football.api-sports.io/players/squads',
                headers=HEADERS_SPORTS,
                params={'team': team_entity.external_id},
                timeout=15,
            )

            if resp.status_code == 429:
                rate_limited = True
                break

            if resp.status_code != 200:
                continue

            squads = resp.json().get('response', [])
            if not squads:
                continue

            players = squads[0].get('players', [])
            total_found += len(players)

            for p in players:
                name = p.get('name', '').strip()
                if not name:
                    continue

                entity, created = _get_or_create_entity(
                    name=name,
                    entity_type='athlete',
                    sport='soccer',
                    external_id=p['id'],
                    api_source='api_sports',
                    logo_url=p.get('photo', ''),
                )

                try:
                    athlete = entity.athlete_details
                    athlete.position = p.get('position', '')
                    athlete.jersey_number = p.get('number') or None
                    athlete.current_team = team_entity
                    if ' ' in name:
                        parts = name.split(' ', 1)
                        athlete.first_name = parts[0]
                        athlete.last_name = parts[1]
                    athlete.save()
                except Athlete.DoesNotExist:
                    pass

                if created:
                    total_created += 1

        except Exception as e:
            logger.error(f"Squad fetch failed for {team_entity.name}: {e}")
            continue

    return Response({
        'success': True,
        'offset': offset,
        'limit': limit,
        'teams_processed': len(teams),
        'total_teams_in_db': total_teams,
        'players_found': total_found,
        'newly_created': total_created,
        'rate_limited': rate_limited,
        'has_more': (offset + limit) < total_teams and not rate_limited,
        'next_offset': offset + limit,
    })


# ─────────────────────────────────────────────────────────────────────────────
# NBA — unchanged, BallDontLie has no "browse leagues" concept
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_nba_teams(request):
    resp = req.get('https://api.balldontlie.io/v1/teams', headers=HEADERS_BDL, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'BallDontLie returned {resp.status_code}'}, status=502)

    teams = resp.json().get('data', [])
    created_count = 0
    for t in teams:
        _, created = _get_or_create_entity(
            name=t['full_name'], entity_type='team', sport='basketball',
            external_id=t['id'], api_source='balldontlie',
            logo_url=f"https://cdn.nba.com/logos/nba/{t['id']}/global/L/logo.svg",
            country='USA',
        )
        if created:
            created_count += 1

    return Response({'success': True, 'total_from_api': len(teams), 'newly_created': created_count})


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_nba_players(request):
    # Use 2026 season since it works!
    CURRENT_SEASON = 2026
    all_players = []
    cursor = None
    created_count = 0
    page = 1
    
    # Get all teams for lookup
    teams_dict = {
        int(team.external_id): team 
        for team in Entity.objects.filter(
            sport='basketball', 
            type='team', 
            api_source='balldontlie'
        )
    }
    
    while True:
        params = {
            'per_page': 100,
            'season': CURRENT_SEASON  # 2026 works!
        }
        if cursor:
            params['cursor'] = cursor
            
        print(f"Fetching page {page} for season {CURRENT_SEASON}...")
        resp = req.get(
            'https://api.balldontlie.io/v1/players',
            headers=HEADERS_BDL, 
            params=params, 
            timeout=15
        )
        
        if resp.status_code != 200:
            print(f"API error: {resp.status_code}")
            break
            
        data = resp.json()
        players = data.get('data', [])
        
        if not players:
            break
            
        all_players.extend(players)
        
        # Process players
        for p in players:
            name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
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
            
            # Update athlete details
            try:
                athlete = entity.athlete_details
                athlete.position = p.get('position', '')
                jersey = p.get('jersey_number')
                if jersey:
                    # Handle jersey ranges like '9-33', take first number
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
        
        # Pagination
        meta = data.get('meta', {})
        next_cursor = meta.get('next_cursor')
        
        if not next_cursor:
            break
            
        cursor = next_cursor
        page += 1
        
        # Rate limit: 5 req/min = wait 12 seconds
        if page % 5 == 0:  # Every 5 pages, wait a bit longer
            print("Rate limit: waiting 15 seconds...")
            time.sleep(15)
        else:
            time.sleep(12)
    
    return Response({
        'success': True,
        'season_used': CURRENT_SEASON,
        'total_fetched': len(all_players),
        'newly_created': created_count,
        'pages_fetched': page
    })


# ─────────────────────────────────────────────────────────────────────────────
# CRICKET — fetch all leagues from API, same pattern
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_cricket_leagues(request):
    print("=== STARTING CRICKET SEED ===")
    print(f"API Key exists: {bool(settings.API_CRICKET_KEY)}")
    
    resp = req.get(
        'https://apiv2.api-cricket.com/cricket/',
        params={'method': 'get_leagues', 'APIkey': settings.API_CRICKET_KEY},
        timeout=15,
    )
    print(f"Response status: {resp.status_code}")
    
    if resp.status_code != 200:
        return Response({'error': f'API-Cricket returned {resp.status_code}'}, status=502)

    data = resp.json()
    print(f"API success: {data.get('success')}")
    
    leagues = data.get('result', [])
    print(f"Leagues from API: {len(leagues)}")
    
    created_count = 0
    for lg in leagues:
        name = lg.get('league_name', '').strip()
        league_key = lg.get('league_key', '')
        if not name or not league_key:
            continue
            
        print(f"Processing: {name} (ID: {league_key})")
        
        entity, created = _get_or_create_entity(
            name=name, 
            entity_type='league', 
            sport='cricket',
            external_id=league_key, 
            api_source='api_cricket',
            logo_url=lg.get('league_logo', ''),
            country=lg.get('country_name', ''),
        )
        if created:
            created_count += 1
            print(f"  → CREATED: {name}")
        else:
            print(f"  → Already exists: {name}")
    
    print(f"=== FINISHED: Created {created_count} new leagues ===")
    
    return Response({
        'success': True,
        'total_from_api': len(leagues),
        'newly_created': created_count,
        'total_in_db': Entity.objects.filter(type='league', sport='cricket').count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# DELETE ALL
# ─────────────────────────────────────────────────────────────────────────────

@api_view(['DELETE'])
@permission_classes([IsAdminUser])
def delete_all_entities(request):
    from apps.entity.models import Athlete, League
    counts = {
        'athletes': Athlete.objects.count(),
        'teams': Team.objects.count(),
        'leagues': League.objects.count(),
        'entities': Entity.objects.count(),
    }
    Entity.objects.all().delete()
    return Response({'success': True, 'deleted': counts})


from apps.core.utils.decorators import basic_auth_required

@basic_auth_required
def api_root(request):
    """Enhanced API root endpoint with comprehensive information"""
    # Build absolute URLs
    base_url = request.build_absolute_uri('/')[:-1]
    
    response_data = {
        '🏠 welcome': {
            'message': 'Welcome to MySportsNest API',
            'version': 'v1.0.0',
            'developer': 'Built with 🐍 by Rafi',
            'status': '✅ Healthy & Running',
            'timestamp': datetime.now().isoformat(),
        },
        
        '📚 documentation': {
            'swagger': {
                'url': f"{base_url}/api/docs/swagger/",
                'description': '🎨 Interactive API documentation with Swagger UI',
                'recommended': '⭐ Best for testing endpoints'
            },
            'redoc': {
                'url': f"{base_url}/api/docs/redoc/",
                'description': '📖 Clean, readable API documentation',
                'recommended': '⭐ Best for reading & understanding'
            },
            'schema': {
                'download_from': f"{base_url}/api/docs/swagger/",
                'description': '🔧 Raw OpenAPI 3.0 schema (JSON/YAML)',
                'note': '💡 Tip: Download from Swagger UI (link above) - click /api/schema link at the top'
            },
        },
        
        '📊 api_info': {
            'base_url': base_url,
            'format': 'JSON',
            'authentication': 'Token-based (check docs for details)',
            'rate_limiting': 'Configured (check headers for limits)',
            'cors': 'Enabled for allowed origins'
        },
        
        '💡 getting_started': {
            '1️⃣ explore': 'Visit Swagger UI to see all available endpoints',
            '2️⃣ authenticate': 'Get your API token from /api/auth/login/',
            '3️⃣ test': 'Use the "Try it out" feature in Swagger',
            '4️⃣ integrate': 'Download the schema for your frontend framework'
        },

        '📮 postman_collection': {
            'invitation_link': 'https://app.postman.com/join-team?invite_code=YOUR_INVITE_CODE_HERE',
            'how_to': '👉 Click the link, join the team, start testing. That\'s it!',
        },

        '💝 Love Letter to Frontend Devs': {
            'to': '👋 Hey Syful Bro!',
            'from': 'Your Backend (aka Rafi)',
            'message': 'May your console be error-free and your builds be fast! ⚡',
            'features': [
                '✅ Clear, consistent endpoint naming',
                '✅ Detailed error messages',
                '✅ Request/response examples everywhere',
                '✅ Proper HTTP status codes',
                '✅ Interactive documentation',
                '✅ No surprises, just good APIs'
            ],
            'collab_status': '🤝 Crushing it together!', 
            'ps': '😄 Yes, we know backend is harder than frontend (just kidding... or are we?)',
            'pps': 'Bhai, thanks for making my JSON look good on screen!'
        },
        
        '📞 Need Help?': {
            'documentation': '📚 Check the docs first (seriously, read them!)',
            'backend_team': '💬 Reach out to Rafi',
            'pro_tip': '🎯 90% of questions are answered in Swagger docs',
            'last_resort': '🆘 If nothing works, we\'ll debug together',
            'postman_help': '📮 Stuck with Postman? Just ping Rafi!'
        },
    }
    
    # Convert to JSON string
    json_output = json.dumps(response_data, indent=2, ensure_ascii=False)
    
    # HTML with syntax highlighting CSS
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MySportsNest API</title>
    <style>
        body {{
            margin: 0;
            padding: 20px;
            font-family: 'Courier New', Courier, monospace;
            background: #f5f5f5;
            font-size: 14px;
            line-height: 1.6;
        }}
        pre {{
            margin: 20px;
            padding: 20px;
            background: white;
            border-radius: 5px;
            border: 1px solid #ddd;
            overflow-x: auto;
        }}
        
        /* JSON Syntax Highlighting */
        .json-key {{ color: #0066cc; font-weight: bold; }}
        .json-string {{ color: #669900; }}
        .json-number {{ color: #ff6600; }}
        .json-boolean {{ color: #cc0000; }}
        .json-null {{ color: #cc0000; }}
        
        /* Make URLs clickable */
        .json-url {{
            color: #0066cc;
            text-decoration: underline;
            cursor: pointer;
        }}
        .json-url:hover {{
            color: #0044aa;
        }}
    </style>
</head>
<body>
<pre id="json">{json_output}</pre>

<script>
// Simple JSON syntax highlighting with clickable URLs
function syntaxHighlight(json) {{
    json = json.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return json.replace(/("(\\u[a-zA-Z0-9]{{4}}|\\[^u]|[^\\"])*"(\\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {{
        var cls = 'json-number';
        if (/^"/.test(match)) {{
            if (/:$/.test(match)) {{
                cls = 'json-key';
            }} else {{
                cls = 'json-string';
                // Check if it's a URL
                var urlMatch = match.match(/"(https?:\/\/[^"]+)"/);
                if (urlMatch) {{
                    var url = urlMatch[1];
                    return '"<a href="' + url + '" class="json-url" target="_blank">' + url + '</a>"';
                }}
            }}
        }} else if (/true|false/.test(match)) {{
            cls = 'json-boolean';
        }} else if (/null/.test(match)) {{
            cls = 'json-null';
        }}
        return '<span class="' + cls + '">' + match + '</span>';
    }});
}}

document.getElementById('json').innerHTML = syntaxHighlight(document.getElementById('json').textContent);
</script>
</body>
</html>"""
    
    response = HttpResponse(html_content, content_type='text/html; charset=utf-8')
    
    # Add custom headers
    response['X-API-Developer'] = 'Rafi 👨‍💻'
    response['X-API-Message'] = 'Happy Coding!'
    response['X-Made-With'] = '🐍 and ☕'
    response['X-Frontend-Hero'] = 'Syful 🎨'
    
    return response


