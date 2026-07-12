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

def _normalize_list(val):
    if not val:
        return []
    if isinstance(val, dict):
        return [val]
    if isinstance(val, list):
        return val
    return []


def _convert_statpal_stats_to_api_sports(team_stats, home_team_name, home_team_id, away_team_name, away_team_id):
    if not team_stats or not isinstance(team_stats, dict):
        return []
    
    metrics = [
        ("Shots on Goal", lambda s: s.get("shots", {}).get("ongoal")),
        ("Shots off Goal", lambda s: s.get("shots", {}).get("offgoal")),
        ("Total Shots", lambda s: s.get("shots", {}).get("total")),
        ("Blocked Shots", lambda s: s.get("shots", {}).get("blocked")),
        ("Shots insidebox", lambda s: s.get("shots", {}).get("insidebox")),
        ("Shots outsidebox", lambda s: s.get("shots", {}).get("outsidebox")),
        ("Fouls", lambda s: s.get("fouls", {}).get("total")),
        ("Corner Kicks", lambda s: s.get("corners", {}).get("total")),
        ("Offsides", lambda s: s.get("offsides", {}).get("total")),
        ("Ball Possession", lambda s: s.get("possession_percent", {}).get("total")),
        ("Yellow Cards", lambda s: s.get("yellowcards", {}).get("total")),
        ("Red Cards", lambda s: s.get("redcards", {}).get("total")),
        ("Goalkeeper Saves", lambda s: s.get("saves", {}).get("total")),
        ("Total passes", lambda s: s.get("passes", {}).get("total")),
        ("Passes accurate", lambda s: s.get("passes", {}).get("accurate")),
    ]
    
    home_stats = []
    away_stats = []
    
    home_data = team_stats.get("home", {})
    away_data = team_stats.get("away", {})
    
    for metric_name, extractor in metrics:
        try:
            home_val = extractor(home_data)
            if home_val is not None:
                home_stats.append({"type": metric_name, "value": home_val})
        except Exception:
            pass
            
        try:
            away_val = extractor(away_data)
            if away_val is not None:
                away_stats.append({"type": metric_name, "value": away_val})
        except Exception:
            pass
            
    return [
        {
            "team": {"id": home_team_id, "name": home_team_name},
            "statistics": home_stats
        },
        {
            "team": {"id": away_team_id, "name": away_team_name},
            "statistics": away_stats
        }
    ]


def _convert_statpal_events_to_api_sports(match_data, home_team_name, home_team_id, away_team_name, away_team_id):
    events = []
    summary = match_data.get("event_summary", {})
    subs = match_data.get("substitutions", {})
    
    # Check if we have event_summary or substitutions
    has_summary_or_subs = (isinstance(summary, dict) and summary) or (isinstance(subs, dict) and subs)
    
    if has_summary_or_subs:
        if isinstance(summary, dict):
            for side in ["home", "away"]:
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id
                
                side_events = summary.get(side, {})
                if not isinstance(side_events, dict):
                    continue
                    
                # Goals
                goals_list = _normalize_list(side_events.get("goals", {}).get("event", []))
                for g in goals_list:
                    detail = "Normal Goal"
                    if g.get("penalty") == "True":
                        detail = "Penalty"
                    elif g.get("own_goal") == "True":
                        detail = "Own Goal"
                    events.append({
                        "time": {
                            "elapsed": int(g.get("minute") or 0) if g.get("minute") else 0,
                            "extra": int(g.get("extra_min") or 0) if g.get("extra_min") else None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": g.get("player_id"), "name": g.get("player_name")},
                        "assist": {"id": g.get("assist_player_id"), "name": g.get("assist_player_name")} if g.get("assist_player_id") else {"id": None, "name": None},
                        "type": "Goal",
                        "detail": detail,
                        "comments": None
                    })
                    
                # Yellow Cards
                yc_list = _normalize_list(side_events.get("yellowcards", {}).get("event", []))
                for yc in yc_list:
                    events.append({
                        "time": {
                            "elapsed": int(yc.get("minute") or 0) if yc.get("minute") else 0,
                            "extra": int(yc.get("extra_min") or 0) if yc.get("extra_min") else None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": yc.get("player_id"), "name": yc.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Card",
                        "detail": "Yellow Card",
                        "comments": yc.get("comment") or None
                    })

                # Red Cards
                rc_list = _normalize_list(side_events.get("redcards", {}).get("event", []))
                for rc in rc_list:
                    events.append({
                        "time": {
                            "elapsed": int(rc.get("minute") or 0) if rc.get("minute") else 0,
                            "extra": int(rc.get("extra_min") or 0) if rc.get("extra_min") else None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": rc.get("player_id"), "name": rc.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Card",
                        "detail": "Red Card",
                        "comments": rc.get("comment") or None
                    })

                # VAR
                var_list = _normalize_list(side_events.get("var", {}).get("event", []))
                for var in var_list:
                    events.append({
                        "time": {
                            "elapsed": int(var.get("minute") or 0) if var.get("minute") else 0,
                            "extra": int(var.get("extra_min") or 0) if var.get("extra_min") else None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": var.get("player_id"), "name": var.get("player_name")},
                        "assist": {"id": None, "name": None},
                        "type": "Var",
                        "detail": var.get("event_type") or "VAR Decision",
                        "comments": var.get("ref_decision") or None
                    })

        # Substitutions
        if isinstance(subs, dict):
            for side in ["home", "away"]:
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id
                
                sub_list = _normalize_list(subs.get(side, {}).get("substitution", []))
                for s in sub_list:
                    events.append({
                        "time": {
                            "elapsed": int(s.get("minute") or 0) if s.get("minute") else 0,
                            "extra": int(s.get("extra_min") or 0) if s.get("extra_min") else None
                        },
                        "team": {"id": side_team_id, "name": side_team_name},
                        "player": {"id": s.get("player_off_id"), "name": s.get("player_off")},
                        "assist": {"id": s.get("player_on_id"), "name": s.get("player_on")},
                        "type": "subst",
                        "detail": "Substitution",
                        "comments": None
                    })
    else:
        # Fallback: parse generic events list format
        raw_events = match_data.get("events", {})
        if isinstance(raw_events, dict):
            event_list = raw_events.get("event", [])
            if isinstance(event_list, dict):
                event_list = [event_list]
            elif not isinstance(event_list, list):
                event_list = []
                
            for ev in event_list:
                if not isinstance(ev, dict):
                    continue
                
                side = ev.get("team")
                side_team_name = home_team_name if side == "home" else away_team_name
                side_team_id = home_team_id if side == "home" else away_team_id
                
                ev_type = ev.get("type", "").lower()
                api_type = "Goal"
                detail = "Normal Goal"
                if "goal" in ev_type:
                    api_type = "Goal"
                    detail = "Normal Goal"
                elif "card" in ev_type or "yellow" in ev_type or "red" in ev_type:
                    api_type = "Card"
                    detail = "Red Card" if "red" in ev_type else "Yellow Card"
                elif "sub" in ev_type:
                    api_type = "Subst"
                    detail = "Substitution"
                
                events.append({
                    "time": {
                        "elapsed": int(ev.get("minute") or 0) if ev.get("minute") else 0,
                        "extra": int(ev.get("extra_min") or 0) if ev.get("extra_min") else None
                    },
                    "team": {"id": side_team_id, "name": side_team_name},
                    "player": {"id": ev.get("player_id"), "name": ev.get("player")},
                    "assist": {"id": ev.get("assist_id"), "name": ev.get("assist_player")} if ev.get("assist_id") else {"id": None, "name": None},
                    "type": api_type,
                    "detail": detail,
                    "comments": ev.get("result") or None
                })

    events.sort(key=lambda x: x["time"]["elapsed"])
    return events


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
        game = None
        # 1. Try LiveScore ID
        try:
            game = LiveScore.objects.get(id=score_id)
        except LiveScore.DoesNotExist:
            pass

        # 2. Try looking up Event ID -> check if live score exists for its external_id
        from apps.event.models import Event
        event_obj = Event.objects.filter(id=score_id).first()
        if not game and event_obj:
            game = LiveScore.objects.filter(external_id=event_obj.external_id).first()

        # 3. Fallback: If not live, mock the game object from the completed/upcoming Event!
        if not game and event_obj:
            class MockGame:
                id = event_obj.id
                sport = event_obj.sport
                external_id = event_obj.external_id
                home_team = event_obj.home_entity.name if event_obj.home_entity else ""
                away_team = event_obj.away_entity.name if event_obj.away_entity else ""
                home_logo = event_obj.home_entity.logo_url if event_obj.home_entity else ""
                away_logo = event_obj.away_entity.logo_url if event_obj.away_entity else ""
                status = event_obj.status
                status_detail = event_obj.status_detail
                start_time = event_obj.start_time
                home_score = event_obj.home_score
                away_score = event_obj.away_score
                raw_data = event_obj.metadata or {}
            game = MockGame()

        # 4. If still not found anywhere, return 404
        if not game:
            return mixin.error_response(message='Game not found', status_code=404)

        raw = game.raw_data
        if isinstance(raw, list):
            raw = raw[0] if raw else {}
        if not isinstance(raw, dict):
            raw = {}

        sport = game.sport

        # 5. Initialize all output fields with original raw values to match StatPal exact structures
        toss_str = raw.get('event_toss', '')
        status_info = raw.get('event_status_info', game.status_detail or game.status)
        league_name = raw.get('league_name', '')
        home_rr = raw.get('event_home_rr')
        away_rr = raw.get('event_away_rr')
        scorecard = raw.get('scorecard', {})
        ball_by_ball = raw.get('comments', {}).get('Live', [])[-10:]
        events = []
        statistics = []
        halftime_score = {}
        wickets = raw.get('wickets', {})
        lineups = raw.get('lineups', {})
        match_type = raw.get('event_type', raw.get('type', ''))
        stadium = raw.get('event_stadium', raw.get('venue', ''))

        # Fetch League from database Event mapping if available
        from apps.event.models import Event
        event = Event.objects.filter(sport=sport, external_id=game.external_id).first()
        if event and event.league:
            league_name = event.league.name

        # 6. Extract sport-specific details
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
            for inn in innings_list:
                if not isinstance(inn, dict):
                    continue
                if inn.get('team') == 'localteam':
                    home_rr = inn.get('total', {}).get('rr')
                elif inn.get('team') == 'visitorteam':
                    away_rr = inn.get('total', {}).get('rr')

            status_info = raw.get('comment', {}).get('post', '') or raw.get('event_status_info', '')
            wickets = raw.get('wickets', {})
            lineups = raw.get('lineups', {})
            match_type = raw.get('type', '')
            stadium = raw.get('venue', '')

        elif sport == 'soccer':
            from apps.sports_apis.services.statpal import statpal_service

            detail_raw = {}
            if event and event.league:
                try:
                    league_id = int(event.league.external_id)
                    result = statpal_service.get_soccer_match_stats(league_id)
                    if result['success']:
                        matches = result['data'].get('match-stats', {}).get('tournament', {}).get('matches', [])
                        if isinstance(matches, dict):
                            matches = [matches]
                        elif not isinstance(matches, list):
                            matches = []
                        for m in matches:
                            if not isinstance(m, dict):
                                continue
                            if str(m.get('main_id')) == str(game.external_id) or str(m.get('id')) == str(game.external_id):
                                detail_raw = m
                                break
                except Exception:
                    pass
            if not detail_raw and game.raw_data:
                raw_dict = game.raw_data[0] if isinstance(game.raw_data, list) else game.raw_data
                if isinstance(raw_dict, dict) and ("event_summary" in raw_dict or "events" in raw_dict or "team_stats" in raw_dict):
                    detail_raw = raw_dict
            
            if not detail_raw and event and event.metadata:
                if isinstance(event.metadata, dict) and ("event_summary" in event.metadata or "events" in event.metadata or "team_stats" in event.metadata):
                    detail_raw = event.metadata

            if detail_raw:
                statistics = _convert_statpal_stats_to_api_sports(
                    detail_raw.get('team_stats', {}),
                    game.home_team, event.home_entity.id if (event and event.home_entity) else 0,
                    game.away_team, event.away_entity.id if (event and event.away_entity) else 0
                )
                events = _convert_statpal_events_to_api_sports(
                    detail_raw,
                    game.home_team, event.home_entity.id if (event and event.home_entity) else 0,
                    game.away_team, event.away_entity.id if (event and event.away_entity) else 0
                )
                
                ht = detail_raw.get('ht', {}) or {}
                halftime_score = {'home': ht.get('home'), 'away': ht.get('away')}
                status_info = detail_raw.get('status', '')

        else:
            # NBA, MLB, Tennis, Golf, Horse Racing, Volleyball, Handball, Hockey, etc.
            status_info = game.status_detail or game.status

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
                # NBA, Volleyball, Handball, Hockey, etc. (periods/sets/quarters)
                home_raw = raw.get('home', {})
                away_raw = raw.get('away', {})
                if home_raw and away_raw:
                    for key in ['s1', 's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 'q1', 'q2', 'q3', 'q4', 'ot']:
                        h_val = home_raw.get(key)
                        a_val = away_raw.get(key)
                        if h_val or a_val:
                            if key == 'ot':
                                label = "Overtime"
                            elif key.startswith('q'):
                                label = f"Quarter {key[1]}"
                            else:
                                label = f"Period {key[1]}"
                            scorecard[label] = {
                                "home": h_val,
                                "away": a_val
                            }

        # 7. Construct final data dictionary containing ALL keys (original + new fields)
        data = {
            # Original keys:
            'id': game.id,
            'sport': sport,
            'home_team': game.home_team,
            'away_team': game.away_team,
            'home_logo': game.home_logo,
            'away_logo': game.away_logo,
            'status': game.status,
            'status_detail': game.status_detail,
            'start_time': game.start_time,
            'match_type': match_type,
            'toss': toss_str,
            'status_info': status_info,
            'stadium': stadium,
            'league': league_name,
            'home_rr': home_rr,
            'away_rr': away_rr,
            'scorecard': scorecard,
            'ball_by_ball': ball_by_ball,
            'wickets': wickets,
            'lineups': lineups,
            
            # New/Extra fields to ensure compatibility:
            'home_score': getattr(game, 'home_score', None),
            'away_score': getattr(game, 'away_score', None),
            'halftime_score': halftime_score,
            'events': events,
            'statistics': statistics,
        }

        return mixin.success_response(data=data)
    except Exception as exc:
        return mixin.handle_exception(exc)
    

    
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