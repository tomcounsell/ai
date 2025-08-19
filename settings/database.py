"""
Database and caching settings for the project.
"""

import logging
import os

import dj_database_url

from settings.env import LOCAL

# Database configuration - prefer DATABASE_URL if available
# For local development, override in .env.local
database_url = os.environ.get("DATABASE_URL")
if database_url:
    DATABASES = {
        "default": dj_database_url.config(),
    }
else:
    # Fallback to individual database settings
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("DB_NAME", "local_postgres_db_name"),
            "USER": os.environ.get("DB_USER", "local_postgres_username"),
            "PASSWORD": os.environ.get("DB_PASSWORD", "local_postgres_password"),
            "HOST": os.environ.get("DB_HOST", "localhost"),
            "PORT": os.environ.get("DB_PORT", "5432"),
        }
    }

# Supabase configuration
SUPABASE_PROJECT_URL = os.environ.get("SUPABASE_PROJECT_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_BUCKET_NAME = os.environ.get("SUPABASE_BUCKET_NAME")

# Cache configuration
REDIS_URL = os.environ.get("REDIS_URL")
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "unique-snowflake",
        }
    }

# For development environment tracking
SIMULATED_ENV = LOCAL is True

logger = logging.getLogger("database")

# Uncomment to set up Redis connection more explicitly
# import redis
# if LOCAL:
#     if REDIS_URL:
#         redis_db = redis.from_url(REDIS_URL)
#     else:
#         REDIS_HOST, REDIS_PORT = "127.0.0.1:6379".split(":")
#         pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=0)
#         redis_db = redis.Redis(connection_pool=pool)
# else:
#     redis_db = redis.from_url(os.environ.get("REDIS_URL"))
#
# if DEBUG:
#     logger.info("Redis connection established for app database.")
#     used_memory, maxmemory = int(redis_db.info()["used_memory"]), int(
#         redis_db.info()["maxmemory"]
#     )
#     maxmemory_human = redis_db.info()["maxmemory_human"]
#     if maxmemory > 0:
#         logger.info(
#             f"Redis currently consumes {round(100 * used_memory / maxmemory, 2)}% out of {maxmemory_human}"
#         )
