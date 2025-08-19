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
HOSTNAME = os.environ.get("HOSTNAME", "app.mycompany.com")
ALLOWED_HOSTS = ALLOWED_HOSTS + [HOSTNAME]
