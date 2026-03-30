import os
import django
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from channels.routing import ProtocolTypeRouter, URLRouter
from apps.score.middleware import JWTAuthMiddleware
from apps.score import routing as score_routing  

all_websocket_patterns = (
    score_routing.websocket_urlpatterns 
)   

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": JWTAuthMiddleware(URLRouter(all_websocket_patterns)),
})