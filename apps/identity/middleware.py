# apps/users/middleware.py

class StreakMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and hasattr(request.user, 'streak'):
            request.user.streak.update_streak()
        return self.get_response(request)