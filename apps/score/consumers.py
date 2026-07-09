import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import LiveScore
from .serializers import LiveScoreSerializer

class LiveScoreConsumer(AsyncWebsocketConsumer):
    GROUP_ALL = 'live_scores'

    async def connect(self):
        # optional ?sport=soccer filter via query param
        query_string = self.scope.get('query_string', b'').decode()
        from urllib.parse import parse_qs
        params = parse_qs(query_string)
        raw_sport = params.get('sport', [None])[0]
        if raw_sport:
            raw_sport = raw_sport.lower().strip()
            if raw_sport in ('null', 'undefined', '', 'none'):
                raw_sport = None
        self.sport_filter = raw_sport

        # join the global group always
        await self.channel_layer.group_add(self.GROUP_ALL, self.channel_name)

        # also join sport-specific group if filter provided
        if self.sport_filter:
            await self.channel_layer.group_add(
                f'live_scores_{self.sport_filter}',
                self.channel_name
            )

        await self.accept()
        await self.send_snapshot()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP_ALL, self.channel_name)
        if self.sport_filter:
            await self.channel_layer.group_discard(
                f'live_scores_{self.sport_filter}',
                self.channel_name
            )

    async def send_snapshot(self):
        games = await self.get_live_games()
        await self.send(text_data=json.dumps({
            'type': 'snapshot',
            'count': len(games),
            'games': games
        }))

    async def score_update(self, event):
        await self.send(text_data=json.dumps(event))

    @database_sync_to_async
    def get_live_games(self):
        from django.db import close_old_connections
        close_old_connections()
        qs = LiveScore.objects.filter(status='live').order_by('-updated_at')
        if self.sport_filter:
            qs = qs.filter(sport=self.sport_filter)
        serializer = LiveScoreSerializer(qs, many=True, context={'request': None})
        return serializer.data
    
    