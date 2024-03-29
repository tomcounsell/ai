import os

from settings import LOCAL, STAGE, DEMO, PRODUCTION

# STATIC FILES
DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_STORAGE = DEFAULT_FILE_STORAGE


# AWS
AWS_ACCESS_KEY_ID = os.environ.get('AWS_ACCESS_KEY_ID', "")
AWS_SECRET_ACCESS_KEY = os.environ.get('AWS_SECRET_ACCESS_KEY', "")
AWS_STORAGE_BUCKET_NAME = AWS_S3_BUCKET_NAME = os.environ.get('AWS_S3_BUCKET_NAME', "")
AWS_OPTIONS = {
    'AWS_ACCESS_KEY_ID': AWS_ACCESS_KEY_ID,
    'AWS_SECRET_ACCESS_KEY': AWS_SECRET_ACCESS_KEY,
    'AWS_STORAGE_BUCKET_NAME': AWS_S3_BUCKET_NAME,
}
AWS_DEFAULT_ACL = 'public-read'
AWS_SNS_NAME = os.environ.get('AWS_SNS_NAME', "")
AWS_STATIC_URL = 'https://' + AWS_S3_BUCKET_NAME + '.s3.amazonaws.com/'


if not LOCAL:
    STATIC_URL = AWS_STATIC_URL

CACHES = {
    "default": {
         "BACKEND": "redis_cache.RedisCache",
         "LOCATION": os.environ.get('REDIS_URL'),
    }
}


# Database
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql_psycopg2',
        'NAME':     os.environ.get('POSTGRES_DB_NAME'),
        'USER':     os.environ.get('POSTGRES_DB_USERNAME'),
        'PASSWORD': os.environ.get('POSTGRES_DB_PASSWORD'),
        'HOST':     os.environ.get('POSTGRES_DB_HOST'),
        'PORT':     os.environ.get('POSTGRES_DB_PORT'),
    }
}

# SENDINBLUE.COM EMAIL SERVICE
# ANYMAIL = {
#     # (exact settings here depend on your ESP...)
#     "SERVICE_API_KEY": SERVICE_API_KEY,
# }
# EMAIL_BACKEND = "anymail.backends.sendinblue.EmailBackend"
DEFAULT_FROM_EMAIL = "info@example.com"  # if you don't already have this in settings
SERVER_EMAIL = "info@example.com"  # ditto (default from-email for Django errors)

TELEGRAM_BOT_API_TOKEN = os.environ.get('TELEGRAM_BOT_API_TOKEN')

BING_SUBSCRIPTION_KEY = os.environ.get('BING_SUBSCRIPTION_KEY')

# ANALYTICS STAGE
# MIXPANEL_API_TOKEN = os.environ.get('MIXPANEL_API_TOKEN')
# MIXPANEL_API_KEY = os.environ.get('MIXPANEL_API_KEY')
# MIXPANEL_API_SECRET = os.environ.get('MIXPANEL_API_SECRET')

# GOOGLE_ANALYTICS_PROPERTY_ID = 'UA-1234567-8'
# FACEBOOK_PIXEL_ID = '1234567890'
# HUBSPOT_PORTAL_ID = '1234'
# HUBSPOT_DOMAIN = 'somedomain.web101.hubspot.com'
# INTERCOM_APP_ID = '0123456789abcdef0123456789abcdef01234567'
# OPTIMIZELY_ACCOUNT_NUMBER = '1234567'
