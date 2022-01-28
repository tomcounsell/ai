# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = '50 char security key here'

INTERNAL_IPS = [
    "127.0.0.1",
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'AI',
        'USER': 'tomcounsell',
        'PASSWORD': '',
        'HOST': 'localhost',
        'PORT':  '5432',
    }
}

REDIS_URL = ""

# AWS
AWS_ACCESS_KEY_ID = ''
AWS_SECRET_ACCESS_KEY = ''
AWS_STORAGE_BUCKET_NAME = AWS_S3_BUCKET_NAME = 'project-stage'
AWS_OPTIONS = {
    'AWS_ACCESS_KEY_ID': AWS_ACCESS_KEY_ID,
    'AWS_SECRET_ACCESS_KEY': AWS_SECRET_ACCESS_KEY,
    'AWS_STORAGE_BUCKET_NAME': AWS_S3_BUCKET_NAME,
}
AWS_SNS_NAME = ''
AWS_STATIC_URL = 'https://' + AWS_S3_BUCKET_NAME + '.s3.amazonaws.com/'


CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

OPENAI_API_KEY = "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"

# OAUTH AND SOCIAL
SOCIAL_AUTH_GOOGLE_OAUTH2_KEY = ''
SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET = ''

SUPERFACE_SDK_TOKEN = "sfs_a2b4a42772c7366d822ee7c7c53438538669da6536a36146edde360d53b8cc36df4c9fb9d1ddd7ba8310c5cf2a1802db09c52b17703c6400af7f6e7b3b4ac6fc_d69b485b"
