import os
import logging
import redis

from settings import DEBUG, LOCAL

SIMULATED_ENV = (LOCAL is True)
# todo: use this to mark keys in redis db, so they can be separated and deleted

logger = logging.getLogger('redis_db')

if LOCAL:
    from settings.local import REDIS_URL
    if REDIS_URL:
        database = redis.from_url(REDIS_URL)
    else:
        REDIS_HOST, REDIS_PORT = "127.0.0.1:6379".split(":")
        pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT, db=0)
        database = redis.Redis(connection_pool=pool)
else:
    database = redis.from_url(os.environ.get("REDIS_URL"))

if DEBUG:
    logger.info("Redis connection established for app database.")
    used_memory, maxmemory = int(database.info()['used_memory']), int(database.info()['maxmemory'])
    maxmemory_human = database.info()['maxmemory_human']
    logger.info(f"Redis currently consumes {round(100*used_memory/maxmemory, 2)}% out of {maxmemory_human}")
