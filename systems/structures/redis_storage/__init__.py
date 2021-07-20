import os
import logging

# JAN_1_2017_TIMESTAMP = int(1483228800)
# HORIZONS = [PERIODS_1HR, PERIODS_4HR, PERIODS_24HR] = [12, 48, 288]  # num of 5 min samples


deployment_type = os.environ.get('DEPLOYMENT_TYPE', 'LOCAL')
if deployment_type == 'LOCAL':
    logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger('core.apps.TA')


class RedisStorageException(Exception):
    def __init__(self, message):
        self.message = message
        logger.error(message)

class MuchException(Exception):
    def __init__(self, message):
        self.message = message
        much_exception, such_wow =  "=========MUCH===EXCEPTION=======", "=========SUCH=====WOW=========="
        logger.error(f'\n\n{much_exception}\n{message}\n{such_wow}')
