import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import LiveScore
from .serializers import LiveScoreSerializer

class LiveScoreConsumer(AsyncWebsocketConsumer):
    GROUP_NAME = 'live_scores'

    async def connect(self):
        await self.channel_layer.group_add(self.GROUP_NAME, self.channel_name)
        await self.accept()
        await self.send_snapshot()  # send current games immediately on connect

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP_NAME, self.channel_name)

    async def send_snapshot(self):
        games = await self.get_live_games()
        await self.send(text_data=json.dumps({
            'type': 'snapshot',
            'count': len(games),
            'games': games
        }))

    async def score_update(self, event):
        # called by Celery via group_send
        await self.send(text_data=json.dumps({
            'type': 'update',
            'game': event['game']
        }))

    @database_sync_to_async
    def get_live_games(self):
        qs = LiveScore.objects.filter(status='live').order_by('-updated_at')[:20]
        return list(LiveScoreSerializer(qs, many=True).data)