"""
Core Django settings common to all environments.
"""

import mimetypes
import os

# Detect if we are in a test environment
import sys

from settings.env import BASE_DIR, LOCAL, PRODUCTION, STAGE

TESTING = "test" in sys.argv or "pytest" in sys.modules

# Application definition
DJANGO_APPS = [
    "unfold",  # before django.contrib.admin
    "unfold.contrib.filters",  # optional, if special filters are needed
    "unfold.contrib.forms",  # optional, if special form elements are needed
    "unfold.contrib.inlines",  # optional, if special inlines are needed
    "unfold.contrib.import_export",  # optional, if django-import-export package is used
    "unfold.contrib.guardian",  # optional, if django-guardian package is used
    "unfold.contrib.simple_history",  # optional, if django-simple-history package is used
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "django.contrib.sites",
]

THIRD_PARTY_APPS = [
    "storages",
    # "request", # a statistics module for django. It stores requests in a database for admins to see.
    # "django_user_agents",
    "debug_toolbar",
    "widget_tweaks",
    "rest_framework",
    "rest_framework_api_key",
    "django_filters",
    "django_tailwind_cli",
    "django_htmx",
    "drf_yasg",
    "django_extensions",
    "django_tasks_db",
]

PROJECT_APPS = [
    "apps.common",
    "apps.integration",
    "apps.api",
    "apps.public",  # for web front-end
    "apps.ai",  # AI integrations and agents
    "apps.staff",  # for staff-only admin tools
    "apps.drugs",  # Medication tracker
    "apps.podcast",  # Podcast management and feeds
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PROJECT_APPS
if LOCAL:
    INSTALLED_APPS += [
        "django_browser_reload",
    ]
SITE_ID = 1

# Background task backend (ImmediateBackend for dev/test — runs tasks inline)
TASKS = {
    "default": {
        "BACKEND": "django.tasks.backends.immediate.ImmediateBackend",
    }
}

# Middleware configuration
MIDDLEWARE = [
    "apps.common.utilities.django.middleware.APIHeaderMiddleware",
    # "django_user_agents.middleware.UserAgentMiddleware",
    "django.middleware.gzip.GZipMiddleware",
    "debug_toolbar.middleware.DebugToolbarMiddleware",
    # "request_logging.middleware.LoggingMiddleware",
    # "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # "request.middleware.RequestMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "apps.common.utilities.django.middleware.HtmxLoginRedirectMiddleware",
]

if LOCAL:
    MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")


# URL configuration
ROOT_URLCONF = "settings.urls"
WSGI_APPLICATION = "settings.wsgi.application"

# Template configuration
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            BASE_DIR / "apps" / "public" / "templates",
        ],
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                # 'django.template.context_processors.media',
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.static",
                "django.contrib.messages.context_processors.messages",
                "apps.public.context_processors.active_navigation",
                "apps.public.context_processors.debug_toolbar_toggle",
            ],
            "loaders": [
                (
                    "django.template.loaders.cached.Loader",
                    [
                        # Default Django loader
                        "django.template.loaders.filesystem.Loader",
                        "django.template.loaders.app_directories.Loader",
                    ],
                )
            ],
        },
    },
]

# Static files (CSS, JavaScript, Images)
STATIC_ROOT = BASE_DIR / "staticfiles"
STATIC_URL = "/static/"
# Additional locations of static files
STATICFILES_DIRS = [
    BASE_DIR / "static",
]

STATICFILES_FINDERS = [
    # Default finders
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
]

STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

# Media files (User uploaded files)
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_URL = "/media/"

mimetypes.add_type("text/javascript", ".js", True)
mimetypes.add_type("text/css", ".css", True)

# Authentication settings
AUTH_USER_MODEL = "common.User"
LOGIN_URL = "/account/login"
LOGIN_REDIRECT_URL = "/"

# Password validation
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24 * 7
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_L10N = False
USE_TZ = True

# Default primary key field
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Request settings
REQUEST_IGNORE_PATHS = (r"^admin/",)

# Template Directories
TEMPLATE_DIRS = [
    BASE_DIR / "templates",
]

# Debug toolbar settings
INTERNAL_IPS = [
    "127.0.0.1",
]
DEBUG_TOOLBAR_CONFIG = {
    "SHOW_TOOLBAR_CALLBACK": "apps.common.utilities.django.middleware.show_debug_toolbar",
}

NPM_BIN_PATH = "npm"

# Import Unfold settings

# SSL settings for production
if PRODUCTION or STAGE:
    SECURE_SSL_REDIRECT = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Django REST Framework settings
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
        # 'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAdminUser",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 50,
}

# DRF-YASG (Swagger/OpenAPI) settings
SWAGGER_SETTINGS = {
    "USE_SESSION_AUTH": True,
    "SECURITY_DEFINITIONS": {
        "Basic": {"type": "basic"},
        "Bearer": {"type": "apiKey", "name": "Authorization", "in": "header"},
    },
    "DEFAULT_MODEL_RENDERING": "example",
}

# Silence the warning about compat renderers
SWAGGER_USE_COMPAT_RENDERERS = False

# ============================================================================
# File Storage Service
# ============================================================================
# Options: "local", "supabase", "s3"
STORAGE_BACKEND = "local"

# Supabase Storage (default production backend)
SUPABASE_PROJECT_URL = os.environ.get("SUPABASE_PROJECT_URL", "")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_PUBLIC_BUCKET_NAME = os.environ.get(
    "SUPABASE_PUBLIC_BUCKET_NAME", ""
) or os.environ.get("SUPABASE_BUCKET_NAME", "")
SUPABASE_PRIVATE_BUCKET_NAME = os.environ.get("SUPABASE_PRIVATE_BUCKET_NAME", "")
SUPABASE_USER_ACCESS_TOKEN = os.environ.get("SUPABASE_USER_ACCESS_TOKEN", "")

# S3-compatible storage (alternative production backend)
S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = os.environ.get("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.environ.get("S3_SECRET_KEY", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PUBLIC_URL = os.environ.get("S3_PUBLIC_URL", "")

# ============================================================================
# Podcast Production Settings
# ============================================================================
PODCAST_DEFAULT_MODEL = "claude-sonnet-4-6"
