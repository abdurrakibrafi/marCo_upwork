from django.shortcuts import render
from django.http import JsonResponse
from datetime import datetime
from django.http import HttpResponse
import json

from apps.core.utils.decorators import basic_auth_required
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView

import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from django.conf import settings

from apps.entity.models import Entity, Team, Athlete, League

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPER
# ─────────────────────────────────────────────

def _get_or_create_entity(name, entity_type, sport, external_id, api_source,
                          logo_url='', country='', follower_count=0):
    """Get or create an Entity + its type-specific model."""
    entity, created = Entity.objects.get_or_create(
        api_source=api_source,
        external_id=str(external_id),
        defaults={
            'name': name,
            'type': entity_type,
            'sport': sport,
            'logo_url': logo_url,
            'country': country,
            'follower_count': follower_count,
            'has_api_data': True,
        }
    )
    if not created:
        # Update logo/follower if missing
        updated = False
        if logo_url and not entity.logo_url:
            entity.logo_url = logo_url
            updated = True
        if follower_count and entity.follower_count == 0:
            entity.follower_count = follower_count
            updated = True
        if updated:
            entity.save()

    # Create type-specific sub-model
    if entity_type == 'team' and not hasattr(entity, 'team_details'):
        Team.objects.get_or_create(entity=entity)
    elif entity_type == 'league' and not hasattr(entity, 'league_details'):
        League.objects.get_or_create(entity=entity)
    elif entity_type == 'athlete':
        if not hasattr(entity, 'athlete_details'):
            parts = name.split(' ', 1)
            first = parts[0]
            last = parts[1] if len(parts) > 1 else ''
            Athlete.objects.get_or_create(
                entity=entity,
                defaults={'first_name': first, 'last_name': last}
            )

    return entity, created


# ─────────────────────────────────────────────
# 1. SEED NBA (BallDontLie)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_nba_teams(request):
    """
    POST /api/entities/seed/nba-teams/
    Pulls all NBA teams from BallDontLie and saves them.
    """
    import requests as req
    headers = {'Authorization': settings.BALLDONTLIE_KEY}
    url = 'https://api.balldontlie.io/v1/teams'

    resp = req.get(url, headers=headers, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'BallDontLie returned {resp.status_code}'}, status=502)

    teams = resp.json().get('data', [])
    created_count = 0

    for t in teams:
        _, created = _get_or_create_entity(
            name=t['full_name'],
            entity_type='team',
            sport='basketball',
            external_id=t['id'],
            api_source='balldontlie',
            logo_url=f"https://cdn.ssref.net/req/202401161/tlogo/bbr/{t.get('abbreviation', '')}.png",
            country='USA',
            follower_count=100000,
        )
        if created:
            created_count += 1

    return Response({
        'success': True,
        'total_from_api': len(teams),
        'newly_created': created_count,
        'message': f'NBA teams seeded. {created_count} new, {len(teams) - created_count} already existed.'
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_nba_players(request):
    """
    POST /api/entities/seed/nba-players/
    Pulls active NBA players from BallDontLie.
    Uses ?per_page=100 + cursor pagination.
    """
    import requests as req
    headers = {'Authorization': settings.BALLDONTLIE_KEY}
    base_url = 'https://api.balldontlie.io/v1/players/active'

    all_players = []
    cursor = None

    # Paginate through all active players
    while True:
        params = {'per_page': 100}
        if cursor:
            params['cursor'] = cursor

        resp = req.get(base_url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            break

        data = resp.json()
        players = data.get('data', [])
        all_players.extend(players)

        cursor = data.get('meta', {}).get('next_cursor')
        if not cursor:
            break

    created_count = 0
    for p in all_players:
        team = p.get('team') or {}
        name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
        if not name:
            continue

        entity, created = _get_or_create_entity(
            name=name,
            entity_type='athlete',
            sport='basketball',
            external_id=p['id'],
            api_source='balldontlie',
            logo_url=f"https://cdn.nba.com/headshots/nba/latest/1040x760/{p['id']}.png",
            country=p.get('country', ''),
            follower_count=50000,
        )

        # Update athlete details
        if hasattr(entity, 'athlete_details'):
            athlete = entity.athlete_details
            athlete.position = p.get('position', '')
            athlete.jersey_number = p.get('jersey_number') or None

            # Link to team entity if exists
            if team.get('id'):
                team_entity = Entity.objects.filter(
                    api_source='balldontlie',
                    external_id=str(team['id']),
                    type='team'
                ).first()
                if team_entity:
                    athlete.current_team = team_entity

            athlete.save()

        if created:
            created_count += 1

    return Response({
        'success': True,
        'total_from_api': len(all_players),
        'newly_created': created_count,
        'message': f'NBA players seeded. {created_count} new.'
    })


# ─────────────────────────────────────────────
# 2. SEED SOCCER (API-Sports)
# ─────────────────────────────────────────────

# Top leagues to seed: EPL(39), La Liga(140), Bundesliga(78), Serie A(135), Ligue 1(61), UCL(2)
SOCCER_LEAGUES = [
    {'id': 39,  'name': 'Premier League',    'country': 'England',  'followers': 5000000},
    {'id': 140, 'name': 'La Liga',           'country': 'Spain',    'followers': 4500000},
    {'id': 78,  'name': 'Bundesliga',        'country': 'Germany',  'followers': 3000000},
    {'id': 135, 'name': 'Serie A',           'country': 'Italy',    'followers': 2500000},
    {'id': 61,  'name': 'Ligue 1',           'country': 'France',   'followers': 2000000},
    {'id': 2,   'name': 'UEFA Champions League', 'country': 'Europe', 'followers': 8000000},
]


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_soccer_leagues(request):
    """
    POST /api/entities/seed/soccer-leagues/
    Seeds the top soccer leagues as Entity objects.
    """
    created_count = 0
    for lg in SOCCER_LEAGUES:
        logo_url = f"https://media.api-sports.io/football/leagues/{lg['id']}.png"
        entity, created = _get_or_create_entity(
            name=lg['name'],
            entity_type='league',
            sport='soccer',
            external_id=lg['id'],
            api_source='api_sports',
            logo_url=logo_url,
            country=lg['country'],
            follower_count=lg['followers'],
        )
        if created:
            created_count += 1

    return Response({
        'success': True,
        'total': len(SOCCER_LEAGUES),
        'newly_created': created_count,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_soccer_teams(request):
    """
    POST /api/entities/seed/soccer-teams/?league_id=39&season=2024
    Seeds teams for a given league + season from API-Sports.
    Default: Premier League 2024.
    """
    import requests as req

    league_id = int(request.GET.get('league_id', 39))
    season = int(request.GET.get('season', 2024))

    headers = {'x-apisports-key': settings.API_SPORTS_KEY}
    url = 'https://v3.football.api-sports.io/teams'
    params = {'league': league_id, 'season': season}

    resp = req.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'API-Sports returned {resp.status_code}'}, status=502)

    response_data = resp.json().get('response', [])
    created_count = 0

    # Find the league entity to link teams
    league_entity = Entity.objects.filter(
        api_source='api_sports',
        external_id=str(league_id),
        type='league'
    ).first()

    for item in response_data:
        t = item.get('team', {})
        venue = item.get('venue', {})

        entity, created = _get_or_create_entity(
            name=t['name'],
            entity_type='team',
            sport='soccer',
            external_id=t['id'],
            api_source='api_sports',
            logo_url=t.get('logo', ''),
            country=t.get('country', ''),
            follower_count=500000,
        )

        # Update team details
        if hasattr(entity, 'team_details'):
            team = entity.team_details
            team.venue_name = venue.get('name', '')
            team.venue_city = venue.get('city', '')
            team.venue_capacity = venue.get('capacity') or None
            if league_entity:
                team.league = league_entity
            team.save()

        if created:
            created_count += 1

    return Response({
        'success': True,
        'league_id': league_id,
        'season': season,
        'total_from_api': len(response_data),
        'newly_created': created_count,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_soccer_players(request):
    """
    POST /api/entities/seed/soccer-players/?league_id=39&season=2024
    Seeds top scorers + top assists for a league as athlete entities.
    This gives us the most popular/well-known players quickly.
    """
    import requests as req

    league_id = int(request.GET.get('league_id', 39))
    season = int(request.GET.get('season', 2024))

    headers = {'x-apisports-key': settings.API_SPORTS_KEY}
    created_count = 0
    all_players = {}

    # Top scorers
    for endpoint in ['topscorers', 'topassists']:
        url = f'https://v3.football.api-sports.io/players/{endpoint}'
        params = {'league': league_id, 'season': season}
        resp = req.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            continue

        for item in resp.json().get('response', []):
            p = item.get('player', {})
            pid = p.get('id')
            if pid and pid not in all_players:
                all_players[pid] = {
                    'player': p,
                    'statistics': item.get('statistics', [{}])[0]
                }

    for pid, data in all_players.items():
        p = data['player']
        stats = data['statistics']
        team_data = stats.get('team', {})

        logo = f"https://media.api-sports.io/football/players/{pid}.png"

        entity, created = _get_or_create_entity(
            name=p.get('name', ''),
            entity_type='athlete',
            sport='soccer',
            external_id=pid,
            api_source='api_sports',
            logo_url=logo,
            country=p.get('nationality', ''),
            follower_count=1000000,
        )

        # Update athlete details
        if hasattr(entity, 'athlete_details'):
            athlete = entity.athlete_details
            athlete.position = stats.get('games', {}).get('position', '')

            # Link to team
            if team_data.get('id'):
                team_entity = Entity.objects.filter(
                    api_source='api_sports',
                    external_id=str(team_data['id']),
                    type='team'
                ).first()
                if team_entity:
                    athlete.current_team = team_entity

            athlete.save()

        if created:
            created_count += 1

    return Response({
        'success': True,
        'league_id': league_id,
        'season': season,
        'total_unique_players': len(all_players),
        'newly_created': created_count,
    })


# ─────────────────────────────────────────────
# 3. SEED CRICKET (API-Cricket)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_cricket_leagues(request):
    """
    POST /api/entities/seed/cricket-leagues/
    Seeds cricket leagues from api-cricket.com
    """
    import requests as req

    url = 'https://apiv2.api-cricket.com/cricket/'
    params = {
        'method': 'get_leagues',
        'APIkey': settings.API_CRICKET_KEY,
    }

    resp = req.get(url, params=params, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'API-Cricket returned {resp.status_code}'}, status=502)

    data = resp.json()
    leagues = data.get('result', [])
    created_count = 0

    for lg in leagues[:50]:  # Top 50 leagues
        entity, created = _get_or_create_entity(
            name=lg.get('league_name', ''),
            entity_type='league',
            sport='cricket',
            external_id=lg.get('league_key', ''),
            api_source='api_cricket',
            logo_url=lg.get('league_logo', ''),
            country=lg.get('country_name', ''),
            follower_count=200000,
        )
        if created:
            created_count += 1

    return Response({
        'success': True,
        'total_from_api': len(leagues),
        'seeded': min(50, len(leagues)),
        'newly_created': created_count,
    })


# ─────────────────────────────────────────────
# 4. SEED EPL VIA BALLDONTLIE (extra teams/players)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_epl_teams(request):
    """
    POST /api/entities/seed/epl-teams/?season=2025
    Seeds EPL teams from BallDontLie EPL v2 API.
    """
    import requests as req

    season = request.GET.get('season', '2025')
    headers = {'Authorization': settings.BALLDONTLIE_KEY}
    url = f'https://api.balldontlie.io/epl/v2/teams'
    params = {'season': season}

    resp = req.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'BallDontLie EPL returned {resp.status_code}: {resp.text}'}, status=502)

    teams = resp.json().get('data', [])
    created_count = 0

    for t in teams:
        entity, created = _get_or_create_entity(
            name=t.get('name', ''),
            entity_type='team',
            sport='soccer',
            external_id=f"epl_{t['id']}",
            api_source='balldontlie_epl',
            logo_url=t.get('logo', ''),
            country='England',
            follower_count=800000,
        )
        if created:
            created_count += 1

    return Response({
        'success': True,
        'season': season,
        'total_from_api': len(teams),
        'newly_created': created_count,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_epl_players(request):
    """
    POST /api/entities/seed/epl-players/?team_id=2&season=2025
    Seeds EPL players/roster for a team from BallDontLie.
    team_id is BallDontLie's internal team ID.
    """
    import requests as req

    team_id = request.GET.get('team_id')
    season = request.GET.get('season', '2025')

    if not team_id:
        return Response({'error': 'team_id is required'}, status=400)

    headers = {'Authorization': settings.BALLDONTLIE_KEY}
    url = 'https://api.balldontlie.io/epl/v2/rosters'
    params = {'team_id': team_id, 'season': season}

    resp = req.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        return Response({'error': f'BallDontLie EPL returned {resp.status_code}: {resp.text}'}, status=502)

    players = resp.json().get('data', [])
    created_count = 0

    for p in players:
        player_data = p.get('player', p)  # roster returns player nested
        name = player_data.get('name', '') or f"{player_data.get('first_name','')} {player_data.get('last_name','')}".strip()
        if not name:
            continue

        entity, created = _get_or_create_entity(
            name=name,
            entity_type='athlete',
            sport='soccer',
            external_id=f"epl_{player_data.get('id', '')}",
            api_source='balldontlie_epl',
            logo_url='',
            country=player_data.get('nationality', ''),
            follower_count=200000,
        )

        if hasattr(entity, 'athlete_details'):
            athlete = entity.athlete_details
            athlete.position = player_data.get('position', '')
            athlete.jersey_number = player_data.get('jersey_number') or None
            athlete.save()

        if created:
            created_count += 1

    return Response({
        'success': True,
        'team_id': team_id,
        'total_from_api': len(players),
        'newly_created': created_count,
    })


# ─────────────────────────────────────────────
# 5. SEED ALL (run everything in sequence)
# ─────────────────────────────────────────────

@api_view(['POST'])
@permission_classes([IsAdminUser])
def seed_all(request):
    """
    POST /api/entities/seed/all/
    Runs all seed operations in sequence.
    Warning: makes many API calls, use once to bootstrap.
    """
    from django.test import RequestFactory
    from django.contrib.auth import get_user_model

    results = {}

    def call(view_fn, **get_params):
        factory = RequestFactory()
        fake_req = factory.post('/', **get_params)
        fake_req.user = request.user
        try:
            resp = view_fn(fake_req)
            return resp.data
        except Exception as e:
            return {'error': str(e)}

    results['soccer_leagues'] = call(seed_soccer_leagues)
    results['soccer_teams_epl'] = call(seed_soccer_teams, QUERY_STRING='league_id=39&season=2024')
    results['soccer_teams_laliga'] = call(seed_soccer_teams, QUERY_STRING='league_id=140&season=2024')
    results['soccer_players_epl'] = call(seed_soccer_players, QUERY_STRING='league_id=39&season=2024')
    results['soccer_players_laliga'] = call(seed_soccer_players, QUERY_STRING='league_id=140&season=2024')
    results['nba_teams'] = call(seed_nba_teams)
    results['nba_players'] = call(seed_nba_players)
    results['cricket_leagues'] = call(seed_cricket_leagues)

    return Response({'success': True, 'results': results})








































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


