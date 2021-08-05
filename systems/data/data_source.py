import logging
from abc import ABC
import numpy as np
import msgpack
import msgpack_numpy as m
m.patch()

from settings.redis_db import redis_db

logger = logging.getLogger(__name__)


class DataSource(ABC):
    compression_algorithm = None

    def __init__(self, *args, **kwargs):
        self.publish_data = None

    def publish(self, channel_name="", publish_data=None, *args, **kwargs):
        channel_name = channel_name or self.__class__.__name__
        publish_data = publish_data or self.publish_data
        if not channel_name or not publish_data:
            return
        # do some transformations here?
        redis_db.publish(channel_name, msgpack.dumps(publish_data))


class Muscle(DataSource):
    pass


class AgentPrediction(DataSource):
    pass
