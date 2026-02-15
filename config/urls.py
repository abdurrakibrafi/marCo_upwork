from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from config import settings


from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from django.http import JsonResponse
from django.urls import reverse
from datetime import datetime

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
                'url': f"{base_url}/api/schema/",
                'description': '🔧 Raw OpenAPI 3.0 schema (JSON/YAML)',
                'note': 'Download this for code generation tools'
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
    
    response = JsonResponse(response_data, json_dumps_params={'indent': 2, 'ensure_ascii': False})
    
    # Add custom headers
    response['X-API-Developer'] = 'Rafi 👨‍💻'
    response['X-API-Message'] = 'Happy Coding!'
    response['X-Made-With'] = '🐍 and ☕'
    response['X-Frontend-Hero'] = 'Syful 🎨'
    
    return response

urlpatterns = [
    path("", api_root, name="api-root"),
    path("admin/", admin.site.urls),
    path("api/auth/", include("apps.identity.urls")),
]


urlpatterns += [
    # OpenAPI schema (raw)
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    # Swagger UI
    path(
        "api/docs/swagger/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    # ReDoc UI
    path(
        "api/docs/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"
    ),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


