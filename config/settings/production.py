from .base import *

print("🚀 Running in PRODUCTION mode")

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

# Security settings for production
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True



# CORS settings for production
CORS_ALLOWED_ORIGINS = [
    "https://shophopapp.com",
    "https://www.shophopapp.com",
    "https://api.shophopapp.com",
]

CORS_ALLOW_CREDENTIALS = True

# For Cloudflare
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

# Add this to your production settings

CSRF_TRUSTED_ORIGINS = [
    "https://shophopapp.com",
    "https://www.shophopapp.com",
    "https://api.shophopapp.com",
    "http://shophopapp.com",     
    "http://www.shophopapp.com",
    "http://api.shophopapp.com",
]

CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_ALL_ORIGINS = False
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Database configuration
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
    }
}

# EMAIL_HOST = env.str('EMAIL_HOST')
# EMAIL_PORT = env.int('EMAIL_PORT')
# EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS')
# EMAIL_USE_SSL= env.bool('EMAIL_USE_SSL')
# EMAIL_HOST_USER = env.str('EMAIL_HOST_USER')
# EMAIL_HOST_PASSWORD = env.str('EMAIL_HOST_PASSWORD')
# DEFAULT_FROM_EMAIL = env.str('DEFAULT_FROM_EMAIL')

# BREVO_API_KEY = env.str('BREVO_API_KEY')
# BREVO_FROM_EMAIL = env.str('BREVO_FROM_EMAIL')
# BREVO_FROM_NAME = env.str('BREVO_FROM_NAME')

MAILGUN_API_KEY = env.str("MAILGUN_API_KEY")
MAILGUN_DOMAIN = env.str("MAILGUN_DOMAIN")
MAILGUN_FROM_EMAIL = env.str("MAILGUN_FROM_EMAIL")
MAILGUN_FROM_NAME = env.str("MAILGUN_FROM_NAME")

# Static files with WhiteNoise
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Production logging
LOGGING = { 
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': '/app/logs/django.log',
        },
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file', 'console'],
            'level': 'INFO',
            'propagate': True,
        },
    },
}

import logging
logger = logging.getLogger('django.request')

def log_csrf_failure(request, reason=""):
    logger.warning('CSRF verification failed: %s', reason)

CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'


