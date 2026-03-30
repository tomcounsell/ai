"""
Production-specific settings.
"""

import os

from settings import ALLOWED_HOSTS

# Security settings
DEBUG = False
SECRET_KEY = os.environ.get("SECRET_KEY")
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Add any production-specific overrides here
# These will be loaded when DEPLOYMENT_TYPE=PRODUCTION

# Production logging handled in settings/logging.py

# Override hostname if needed
HOSTNAME = os.environ.get("HOSTNAME", "app.bwforce.ai")
ALLOWED_HOSTS = ALLOWED_HOSTS + [HOSTNAME]

# CSRF trusted origins for Django 4.0+ (required for POST requests)
CSRF_TRUSTED_ORIGINS = [
    "https://app.bwforce.ai",
    "https://cuttlefish-ea1h.onrender.com",
    "https://blendedworkforce.ai",
    "https://www.blendedworkforce.ai",
]

# File Storage Service - use Supabase in production
STORAGE_BACKEND = "supabase"

# Background task backend (DatabaseBackend for production — worker via manage.py db_worker)
TASKS = {
    "default": {
        "BACKEND": "django_tasks_db.DatabaseBackend",
        "QUEUES": ["default"],
    }
}
