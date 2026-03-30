from django.urls import re_path
from apps.score import consumers

websocket_urlpatterns = [
    re_path(r'^ws/scores/live/$', consumers.LiveScoreConsumer.as_asgi()),
]