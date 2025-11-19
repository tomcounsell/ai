"""
Settings for third-party services and integrations.
"""

import os

from settings.env import LOCAL

# DRF (Django Rest Framework) settings
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.common.utilities.logger.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "100/hour", "user": "1000/hour"},
}

# AWS configuration
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")
AWS_STORAGE_BUCKET_NAME = AWS_S3_BUCKET_NAME = os.environ.get(
    "AWS_STORAGE_BUCKET_NAME", ""
)
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
AWS_OPTIONS = {
    "AWS_ACCESS_KEY_ID": AWS_ACCESS_KEY_ID,
    "AWS_SECRET_ACCESS_KEY": AWS_SECRET_ACCESS_KEY,
    "AWS_STORAGE_BUCKET_NAME": AWS_S3_BUCKET_NAME,
    "AWS_REGION": AWS_REGION,
}
AWS_DEFAULT_ACL = "public-read"
AWS_SNS_NAME = os.environ.get("AWS_SNS_NAME", "")
AWS_STATIC_URL = (
    f"https://{AWS_S3_BUCKET_NAME}.s3.amazonaws.com/" if AWS_S3_BUCKET_NAME else ""
)

# File upload settings
AWS_MAX_UPLOAD_SIZE = int(
    os.environ.get("AWS_MAX_UPLOAD_SIZE", 10485760)
)  # 10MB default
AWS_UPLOAD_BUCKET = os.environ.get("AWS_UPLOAD_BUCKET", AWS_S3_BUCKET_NAME)
AWS_UPLOAD_PREFIX = os.environ.get("AWS_UPLOAD_PREFIX", "uploads")

# S3 for static files in production
if not LOCAL and AWS_S3_BUCKET_NAME:
    STATIC_URL = AWS_STATIC_URL
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    STATICFILES_STORAGE = DEFAULT_FILE_STORAGE

# Email configuration
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@example.com")
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", "server@example.com")

# Loops integration
LOOPS_API_KEY = os.environ.get("LOOPS_API_KEY", "")

# Twilio integration
TWILIO_ENABLED = os.environ.get("TWILIO_ENABLED", "False").lower() == "true"
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER", "")

# Stripe integration
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
STRIPE_ENABLED = os.environ.get("STRIPE_ENABLED", "False").lower() == "true" or (
    STRIPE_API_KEY and STRIPE_WEBHOOK_SECRET
)

# Social Auth
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = os.environ.get("SOCIAL_AUTH_GOOGLE_OAUTH2_KEY", "")
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = os.environ.get(
    "SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET", ""
)

# AI Integration Settings
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_ORG_ID = os.environ.get("OPENAI_ORG_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_VERSION = os.environ.get("ANTHROPIC_VERSION", "2023-06-01")

# QuickBooks Integration Settings
QUICKBOOKS_CLIENT_ID = os.environ.get("QUICKBOOKS_CLIENT_ID", "")
QUICKBOOKS_CLIENT_SECRET = os.environ.get("QUICKBOOKS_CLIENT_SECRET", "")
QUICKBOOKS_WEBHOOK_TOKEN = os.environ.get("QUICKBOOKS_WEBHOOK_TOKEN", "")
QUICKBOOKS_SANDBOX_MODE = (
    os.environ.get("QUICKBOOKS_SANDBOX_MODE", "True").lower() == "true"
)

# MCP Server Settings
MCP_SERVER_HOST = os.environ.get("MCP_SERVER_HOST", "0.0.0.0")
MCP_SERVER_PORT = int(os.environ.get("MCP_SERVER_PORT", "8080"))
MCP_MAX_CONNECTIONS = int(os.environ.get("MCP_MAX_CONNECTIONS", "100"))
MCP_AUTH_REQUIRED = os.environ.get("MCP_AUTH_REQUIRED", "True").lower() == "true"
