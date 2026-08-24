import base64
from django.http import HttpResponse
from django.conf import settings

def basic_auth_required(view_func):
    """Decorator to enforce HTTP Basic Authentication on API views (e.g., Swagger/Redoc).

    Checks the incoming HTTP Authorization header against configured DOCS_USERNAME
    and DOCS_PASSWORD settings. Prompts browser Basic Auth dialog with a 401 response if missing or invalid.

    Args:
        view_func (callable): The target Django view function to protect.

    Returns:
        callable: Decorated view wrapper handling Basic Auth verification.
    """
    def wrapper(request, *args, **kwargs):
        # Get credentials from settings
        VALID_USERNAME = settings.DOCS_USERNAME
        VALID_PASSWORD = settings.DOCS_PASSWORD

        # Check Authorization header
        if 'HTTP_AUTHORIZATION' in request.META:
            auth = request.META['HTTP_AUTHORIZATION'].split()
            if len(auth) == 2 and auth[0].lower() == 'basic':
                credentials = base64.b64decode(auth[1]).decode('utf-8')
                username, password = credentials.split(':', 1)
                if username == VALID_USERNAME and password == VALID_PASSWORD:
                    return view_func(request, *args, **kwargs)

        # Return 401 - triggers browser password popup
        response = HttpResponse(status=401)
        response['WWW-Authenticate'] = 'Basic realm="MySportsNest API Docs"'
        return response
    return wrapper