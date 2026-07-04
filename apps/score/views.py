from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.core.cache import cache
from .models import LiveScore
from .serializers import LiveScoreSerializer
from apps.core.utils.mixins import BaseResponseMixin

# @api_view(['GET'])
# @permission_classes([IsAuthenticated])
# def live_scores(request):
#     """
#     Get all live scores across all sports
#     Frontend polls this every 15 seconds
#     """
#     mixin = BaseResponseMixin()
#     try:
#         # Get from cache first
#         cached_nba = cache.get('live_scores_nba')
#         cached_nfl = cache.get('live_scores_nfl')
#         cached_soccer = cache.get('live_scores_soccer')
#         cached_cricket = cache.get('live_scores_cricket')
        
#         # If cache exists, use it (faster)
#         if any([cached_nba, cached_nfl, cached_soccer, cached_cricket]):
#             # Get live games from database (last 30 seconds)
#             live_games = LiveScore.objects.filter(status='live').order_by('-updated_at')[:20]
#             serializer = LiveScoreSerializer(live_games, many=True)
            
#             data = {
#                 'count': live_games.count(),
#                 'games': serializer.data
#             }
#             return mixin.success_response(data=data, message='Live scores retrieved successfully')
#         else:
#             # Cache miss - return empty and celery will update soon
#             return mixin.success_response(
#                 data={'games': []},
#                 message='Live scores are being updated...'
#             )
#     except Exception as exc:
#         return mixin.handle_exception(exc)

# apps/score/views.py

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores(request):
    """
    Get all live scores across all sports
    Frontend polls this every 15 seconds
    """
    mixin = BaseResponseMixin()
    try:
        live_games = LiveScore.objects.filter(status='live').order_by('-updated_at')[:20]
        serializer = LiveScoreSerializer(live_games, many=True)

        data = {
            'count': live_games.count(),
            'games': serializer.data
        }
        return mixin.success_response(data=data, message='Live scores retrieved successfully')
    except Exception as exc:
        return mixin.handle_exception(exc)
# তোমার existing imports-এর নিচে এই function টা add করো:

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def nest_live_scores(request):

    mixin = BaseResponseMixin()
    try:
        from apps.nest.models import UserNest
        from django.db.models import Q

        nest_entity_ids = list(
            UserNest.objects.filter(user=request.user)
            .values_list("entity_id", flat=True)
        )
        if not nest_entity_ids:
            return mixin.success_response(
                data={"count": 0, "games": []},
                message="No entities in your nest.",
            )

        # LiveScore-এ সরাসরি team entity FK নেই,
        # তাই Event → LiveScore join করতে হবে
        from apps.event.models import Event
        live_event_ids = (
            Event.objects.filter(
                api_source="statpal",
                status="live",
            )
            .filter(
                Q(home_entity_id__in=nest_entity_ids)
                | Q(away_entity_id__in=nest_entity_ids)
            )
            .values_list("external_id", flat=True)
        )

        qs = LiveScore.objects.filter(
            status="live",
            external_id__in=list(live_event_ids),
        ).order_by("-updated_at")

        sport = request.query_params.get("sport")
        if sport:
            qs = qs.filter(sport=sport.lower())

        serializer = LiveScoreSerializer(list(qs), many=True)
        return mixin.success_response(
            data={"count": qs.count(), "games": serializer.data}
        )

    except Exception as exc:
        return mixin.handle_exception(exc)
    
# apps/score/views.py
# Replace your live_score_detail function with this

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_score_detail(request, score_id):
    mixin = BaseResponseMixin()
    try:
        game = LiveScore.objects.get(id=score_id)
        raw = game.raw_data
        if isinstance(raw, list):
            raw = raw[0] if raw else {}

        sport = game.sport

        if sport == 'cricket':
            # Extract scorecard
            innings_list = raw.get('inning', [])
            if isinstance(innings_list, dict):
                innings_list = [innings_list]
            elif not isinstance(innings_list, list):
                innings_list = []
                
            scorecard = {}
            for inn in innings_list:
                if not isinstance(inn, dict):
                    continue
                inning_name = inn.get('name', f"Innings {inn.get('inningnum', '')}").strip()
                scorecard[inning_name] = []
                
                # Add Batsmen
                batsmen = inn.get('batsmanstats', {}).get('player', [])
                if isinstance(batsmen, dict):
                    batsmen = [batsmen]
                elif not isinstance(batsmen, list):
                    batsmen = []
                for b in batsmen:
                    scorecard[inning_name].append({
                        "type": "Batsman",
                        "player": b.get("batsman", ""),
                        "R": b.get("r", ""),
                        "B": b.get("b", ""),
                        "4s": b.get("s4", ""),
                        "6s": b.get("s6", ""),
                        "SR": b.get("sr", ""),
                        "status": b.get("status", "not out"),
                        "innings": inning_name
                    })
                    
                # Add Bowlers
                bowlers = inn.get('bowlers', {}).get('player', [])
                if isinstance(bowlers, dict):
                    bowlers = [bowlers]
                elif not isinstance(bowlers, list):
                    bowlers = []
                for bowler in bowlers:
                    scorecard[inning_name].append({
                        "type": "Bowler",
                        "player": bowler.get("bowler", ""),
                        "O": bowler.get("o", ""),
                        "R": bowler.get("r", ""),
                        "W": bowler.get("w", ""),
                        "ER": bowler.get("er", ""),
                        "innings": inning_name
                    })

            # Extract ball_by_ball
            commentary_list = raw.get('commentaries', {}).get('commentary', [])
            if isinstance(commentary_list, dict):
                commentary_list = [commentary_list]
            elif not isinstance(commentary_list, list):
                commentary_list = []
                
            ball_by_ball = []
            for item in commentary_list[:10]:
                if not isinstance(item, dict):
                    continue
                ball_by_ball.append({
                    "post": item.get("post", ""),
                    "runs": item.get("runs", ""),
                    "overs": item.get("over", ""),
                    "ended": str(item.get("over_ended", item.get("ended", "false"))).lower()
                })

            # Extract Toss
            toss_str = ""
            info_list = raw.get('matchinfo', {}).get('info', [])
            if isinstance(info_list, dict):
                info_list = [info_list]
            elif not isinstance(info_list, list):
                info_list = []
            for info in info_list:
                if isinstance(info, dict) and info.get('name') == 'Toss':
                    toss_str = info.get('value', '')
                    break

            # Fetch run rates
            home_rr = None
            away_rr = None
            for inn in innings_list:
                if not isinstance(inn, dict):
                    continue
                if inn.get('team') == 'localteam':
                    home_rr = inn.get('total', {}).get('rr')
                elif inn.get('team') == 'visitorteam':
                    away_rr = inn.get('total', {}).get('rr')

            # Fetch League from Event mapping
            from apps.event.models import Event
            event = Event.objects.filter(sport=sport, external_id=game.external_id).first()
            league_name = event.league.name if (event and event.league) else raw.get('league_name', '')

            status_info = raw.get('comment', {}).get('post', '') or raw.get('event_status_info', '')

            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'status_info': status_info,
                'match_type': raw.get('type', ''),
                'toss': toss_str,
                'stadium': raw.get('venue', ''),
                'league': league_name,
                'home_rr': home_rr,
                'away_rr': away_rr,
                'scorecard': scorecard,
                'ball_by_ball': ball_by_ball,
            }

        elif sport == 'soccer':
            from apps.sports_apis.services.api_sports import api_sports_service

            detail_raw = {}
            result = api_sports_service.get_fixture_details(int(game.external_id))
            if result['success']:
                resp = result['data'].get('response', [])
                if resp:
                    detail_raw = resp[0]

            fix_data    = detail_raw.get('fixture', raw.get('fixture', {}))
            league_data = detail_raw.get('league', raw.get('league', {}))

            events_raw = detail_raw.get('events', raw.get('events', []))
            if isinstance(events_raw, dict):
                events = events_raw.get('event', [])
            else:
                events = events_raw
            if isinstance(events, dict):
                events = [events]
            elif not isinstance(events, list):
                events = []
        
            statistics  = detail_raw.get('statistics', [])
            goals       = detail_raw.get('goals', raw.get('goals', {}))
            score       = detail_raw.get('score', raw.get('score', {}))
        
            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'status_info': fix_data.get('status', {}).get('long', ''),
                'stadium': fix_data.get('venue', {}).get('name', ''),
                'league': league_data.get('name', ''),
                'league_logo': league_data.get('logo', ''),
                'home_rr': None,
                'away_rr': None,
                'home_score': goals.get('home'),
                'away_score': goals.get('away'),
                'halftime_score': score.get('halftime', {}),
                'events': events,
                'statistics': statistics,
            }
        else:
            # Generic fallback for NBA, MLB, Tennis, Golf, Horse Racing, Volleyball, Handball, Hockey, etc.
            from apps.event.models import Event
            event = Event.objects.filter(sport=sport, external_id=game.external_id).first()
            league_name = event.league.name if (event and event.league) else raw.get('league_name', '')

            status_info = game.status_detail or game.status

            scorecard = {}
            events = []

            if sport == 'tennis':
                players = raw.get('player', [])
                if isinstance(players, list) and len(players) >= 2:
                    p1, p2 = players[0], players[1]
                    scorecard = {
                        "Set 1": {"home": p1.get("s1"), "away": p2.get("s1")},
                        "Set 2": {"home": p1.get("s2"), "away": p2.get("s2")},
                        "Set 3": {"home": p1.get("s3"), "away": p2.get("s3")},
                        "Set 4": {"home": p1.get("s4"), "away": p2.get("s4")},
                        "Set 5": {"home": p1.get("s5"), "away": p2.get("s5")},
                    }
            elif sport == 'baseball':
                events_raw = raw.get('events', {}).get('event', [])
                if isinstance(events_raw, dict):
                    events_raw = [events_raw]
                elif not isinstance(events_raw, list):
                    events_raw = []
                for ev in events_raw:
                    events.append({
                        "inning": ev.get("inn"),
                        "description": ev.get("desc"),
                        "team": ev.get("team"),
                        "score": f"{ev.get('chw', '0')} - {ev.get('cle', '0')}"
                    })
            elif sport == 'golf':
                players = raw.get('player', [])
                if isinstance(players, dict):
                    players = [players]
                elif not isinstance(players, list):
                    players = []
                leaderboard = []
                for p in sorted(players, key=lambda x: str(x.get('pos', '999'))[:3])[:10]:
                    leaderboard.append({
                        "position": p.get("pos"),
                        "player": p.get("name"),
                        "score": p.get("par"),
                        "today": p.get("today"),
                        "total": p.get("total")
                    })
                scorecard = {"Leaderboard": leaderboard}
            elif sport == 'horse_racing':
                runners = raw.get('runners', {}).get('horse', [])
                if isinstance(runners, dict):
                    runners = [runners]
                elif not isinstance(runners, list):
                    runners = []
                
                runner_list = []
                for h in runners:
                    runner_list.append({
                        "number": h.get("number"),
                        "horse": h.get("name"),
                        "jockey": h.get("jockey"),
                        "trainer": h.get("trainer"),
                        "weight": h.get("wgt"),
                        "odds": h.get("odds", {}).get("bookmaker", {}).get("odd")
                    })
                scorecard = {"Runners": runner_list}
            else:
                # NBA, Volleyball, Handball, Hockey, etc. (periods/sets)
                home_raw = raw.get('home', {})
                away_raw = raw.get('away', {})
                if home_raw and away_raw:
                    for key in ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9']:
                        h_val = home_raw.get(key)
                        a_val = away_raw.get(key)
                        if h_val or a_val:
                            scorecard[f"Period {key[1]}"] = {
                                "home": h_val,
                                "away": a_val
                            }

            data = {
                'id': game.id,
                'sport': sport,
                'home_team': game.home_team,
                'away_team': game.away_team,
                'home_logo': game.home_logo,
                'away_logo': game.away_logo,
                'status': game.status,
                'status_detail': game.status_detail,
                'status_info': status_info,
                'stadium': raw.get('venue', ''),
                'league': league_name,
                'home_rr': None,
                'away_rr': None,
                'home_score': game.home_score,
                'away_score': game.away_score,
                'halftime_score': {},
                'events': events,
                'statistics': [],
                'scorecard': scorecard,
                'ball_by_ball': [],
            }

        return mixin.success_response(data=data)
    except LiveScore.DoesNotExist:
        return mixin.error_response(message='Game not found', status_code=404)
    

    
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live_scores_by_sport(request, sport):
    """
    Get live scores for a specific sport
    Example: /api/scores/live/nba
    """
    mixin = BaseResponseMixin()
    try:
        sport_lower = sport.lower()
        supported_sports = {
            'nba', 'nfl', 'mlb', 'nhl', 'soccer', 'cricket', 'tennis',
            'baseball', 'handball', 'volleyball', 'golf', 'horse_racing',
            'basketball', 'football', 'hockey', 'formula1', 'mma'
        }
        if sport_lower not in supported_sports:
            return mixin.error_response(
                message=f'Sport {sport} not supported',
                status_code=status.HTTP_400_BAD_REQUEST
            )
        
        cache_key = f'live_scores_{sport_lower}'
        cached_data = cache.get(cache_key)
        
        if cached_data:
            # Get from database
            live_games = LiveScore.objects.filter(sport=sport_lower, status='live')
            serializer = LiveScoreSerializer(live_games, many=True)
            
            data = {
                'sport': sport_lower,
                'count': live_games.count(),
                'games': serializer.data
            }
            return mixin.success_response(data=data)
        else:
            return mixin.success_response(
                data={'sport': sport_lower, 'games': []},
                message='Live scores are being updated...'
            )
    except Exception as exc:
        return mixin.handle_exception(exc)