from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.utils.decorators import method_decorator

from apps.core.utils.decorators import basic_auth_required
from apps.core.views import api_root
from config import settings, views
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)


# Protected Spectacular Views
class ProtectedSpectacularSwaggerView(SpectacularSwaggerView):
    @method_decorator(basic_auth_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

class ProtectedSpectacularRedocView(SpectacularRedocView):
    @method_decorator(basic_auth_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

class ProtectedSpectacularAPIView(SpectacularAPIView):
    @method_decorator(basic_auth_required)
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)


urlpatterns = [
    # api_root only once, with protection
    path("", basic_auth_required(api_root), name="api-root"),  
    path("admin/", admin.site.urls),
    path("api/auth/", include("apps.identity.urls")),

    path('api/scores/', include('apps.score.urls')),
    path('api/entities/', include('apps.entity.urls')),
    path('api/nest/', include('apps.nest.urls')),
    path('api/calendar/', include('apps.event.urls')),
    path('api/feed/', include('apps.feed.urls')),
    path('api/core/', include('apps.core.urls')),
    path('api/source/', include('apps.source.urls')),

    # Health & Status
    path('api/health/', views.health_check, name='health_check'),
    path('api/status/', views.api_status, name='api_status'),
]

# API Documentation - protected with basic auth
urlpatterns += [
    path('api/schema/', ProtectedSpectacularAPIView.as_view(), name='schema'),
    path('api/docs/swagger/', ProtectedSpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/docs/redoc/', ProtectedSpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)