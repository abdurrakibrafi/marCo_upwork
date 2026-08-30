import json
import logging
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.core.serializers.json import DjangoJSONEncoder
from django.utils import timezone
from .models import LiveScore
from .serializers import LiveScoreSerializer
from .services import get_live_score_detail_data

logger = logging.getLogger(__name__)


class LiveScoreConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for streaming live score ticker updates across all sports or per sport channel."""
    GROUP_ALL = 'live_scores'

    async def connect(self):
        """Handle WebSocket connection, register client to sport group, and push initial scoreboard snapshot."""
        # optional ?sport=soccer filter via query param
        query_string = self.scope.get('query_string', b'').decode()
        params = parse_qs(query_string)
        raw_sport = params.get('sport', [None])[0]
        if raw_sport:
            raw_sport = raw_sport.lower().strip()
            if raw_sport in ('null', 'undefined', '', 'none'):
                raw_sport = None
        self.sport_filter = raw_sport

        if self.sport_filter:
            await self.channel_layer.group_add(
                f'live_scores_{self.sport_filter}',
                self.channel_name
            )
        else:
            await self.channel_layer.group_add(self.GROUP_ALL, self.channel_name)

        await self.accept()
        await self.send_snapshot()

    async def disconnect(self, close_code):
        """Handle WebSocket disconnect and unsubscribe from live score channel groups."""
        if self.sport_filter:
            await self.channel_layer.group_discard(
                f'live_scores_{self.sport_filter}',
                self.channel_name
            )
        else:
            await self.channel_layer.group_discard(self.GROUP_ALL, self.channel_name)

    async def send_snapshot(self):
        """Query currently active live games and push immediate scoreboard snapshot to client."""
        games = await self.get_live_games()
        await self.send(text_data=json.dumps({
            'type': 'snapshot',
            'count': len(games),
            'games': games
        }, cls=DjangoJSONEncoder))

    async def score_update(self, event):
        """Relay broadcast score updates only if relevant to the connected user's Nest."""
        user = self.scope.get('user')
        score_data = event.get('data') or {}
        score_id = score_data.get('id') or score_data.get('external_id')
        home_team = score_data.get('home_team', '')
        away_team = score_data.get('away_team', '')

        is_relevant = await self.check_if_relevant_to_user(user, score_id, home_team, away_team)
        if is_relevant:
            await self.send(text_data=json.dumps(event, cls=DjangoJSONEncoder))

    @database_sync_to_async
    def check_if_relevant_to_user(self, user, score_id, home_team, away_team):
        """Check if an incoming live score update matches the user's followed Nest entities."""
        if not user or not user.is_authenticated:
            return False
        from .views import _get_user_live_scores_queryset
        user_live_scores = _get_user_live_scores_queryset(user, sport=self.sport_filter)
        if score_id:
            try:
                if user_live_scores.filter(id=int(score_id)).exists() or user_live_scores.filter(external_id=str(score_id)).exists():
                    return True
            except (ValueError, TypeError):
                if user_live_scores.filter(external_id=str(score_id)).exists():
                    return True
        if home_team or away_team:
            if user_live_scores.filter(home_team__iexact=home_team).exists() or user_live_scores.filter(away_team__iexact=away_team).exists():
                return True
        return False

    @database_sync_to_async
    def get_live_games(self):
        """Synchronously fetch active live scores strictly matching the user's Nest and sport filter."""
        user = self.scope.get('user')
        if user and user.is_authenticated:
            from .views import _get_user_live_scores_queryset
            qs = _get_user_live_scores_queryset(user, sport=self.sport_filter)
        else:
            qs = LiveScore.objects.none()
        serializer = LiveScoreSerializer(qs, many=True, context={'request': None})
        return serializer.data


class LiveScoreDetailConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer for streaming detailed in-game match statistics, timeline, and box scores."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.score_id = None
        self.group_name = None

    async def connect(self):
        """Connect client, parse score_id from path or query parameters, and subscribe to match room."""
        # 1. Extract score_id from URL route kwargs if present
        score_id_param = self.scope.get('url_route', {}).get('kwargs', {}).get('score_id')

        # 2. If not in URL route, check query parameters
        if not score_id_param:
            query_string = self.scope.get('query_string', b'').decode()
            params = parse_qs(query_string)
            score_id_param = params.get('score_id', [None])[0] or params.get('id', [None])[0]

        if score_id_param:
            try:
                self.score_id = int(score_id_param)
            except (ValueError, TypeError):
                self.score_id = score_id_param

        # 3. Add to channel layer group for real-time match detail updates
        if self.score_id:
            self.group_name = f'live_score_detail_{self.score_id}'
            await self.channel_layer.group_add(self.group_name, self.channel_name)

        await self.accept()

        # 4. Immediately send the current live match detail snapshot
        if self.score_id:
            await self.send_detail()

    async def disconnect(self, close_code):
        """Handle client disconnect and remove from match detail group."""
        if self.group_name:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        """Process incoming WebSocket JSON messages (ping/pong, dynamic subscription changes, snapshots)."""
        if not text_data:
            return
        try:
            payload = json.loads(text_data)
        except Exception:
            return

        msg_type = payload.get('type')
        action = payload.get('action')

        if msg_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))
            return

        # Handle subscribing to a specific score_id via WebSocket message
        if action in ('subscribe', 'set_score_id') or ('score_id' in payload and action != 'get_detail'):
            new_score_id = payload.get('score_id') or payload.get('id')
            if new_score_id:
                if self.group_name:
                    await self.channel_layer.group_discard(self.group_name, self.channel_name)

                try:
                    self.score_id = int(new_score_id)
                except (ValueError, TypeError):
                    self.score_id = new_score_id

                self.group_name = f'live_score_detail_{self.score_id}'
                await self.channel_layer.group_add(self.group_name, self.channel_name)
                await self.send_detail()

        elif action in ('get_detail', 'refresh', 'snapshot'):
            await self.send_detail()

    async def send_detail(self):
        """Fetch real-time match details and push JSON response payload to the client."""
        if not self.score_id:
            await self.send(text_data=json.dumps({
                'type': 'live_score_detail',
                'success': False,
                'message': 'No score_id specified',
                'timestamp': timezone.now().isoformat(),
                'status_code': 400,
                'data': None
            }, cls=DjangoJSONEncoder))
            return

        detail_data = await self.fetch_detail(self.score_id)
        if detail_data is not None:
            response = {
                'type': 'live_score_detail',
                'success': True,
                'message': 'Success',
                'timestamp': timezone.now().isoformat(),
                'status_code': 200,
                'data': detail_data
            }
        else:
            response = {
                'type': 'live_score_detail',
                'success': False,
                'message': 'Game not found',
                'timestamp': timezone.now().isoformat(),
                'status_code': 404,
                'data': None
            }

        await self.send(text_data=json.dumps(response, cls=DjangoJSONEncoder))

    async def score_detail_update(self, event):
        """Handle score detail broadcast event from channel layer."""
        # If event already contains formatted response dict
        if 'data' in event and 'success' in event:
            await self.send(text_data=json.dumps(event, cls=DjangoJSONEncoder))
        else:
            # Refresh and send latest detail
            await self.send_detail()

    async def score_update(self, event):
        """Handle general score update event broadcasted to match group."""
        await self.send_detail()

    @database_sync_to_async
    def fetch_detail(self, score_id):
        """Synchronously retrieve full live match and box score data from services."""
        return get_live_score_detail_data(score_id)