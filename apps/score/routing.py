from django.urls import re_path
from apps.score import consumers

websocket_urlpatterns = [
    re_path(r'^ws/scores/live/?$', consumers.LiveScoreConsumer.as_asgi()),
    re_path(r'^ws/scores/live/detail/(?P<score_id>\d+)/?$', consumers.LiveScoreDetailConsumer.as_asgi()),
    re_path(r'^ws/scores/live/(?P<score_id>\d+)/detail/?$', consumers.LiveScoreDetailConsumer.as_asgi()),
    re_path(r'^ws/scores/live/(?P<score_id>\d+)/?$', consumers.LiveScoreDetailConsumer.as_asgi()),
    re_path(r'^ws/scores/live/detail/?$', consumers.LiveScoreDetailConsumer.as_asgi()),
]