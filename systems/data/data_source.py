import logging
from abc import ABC
import numpy as np
from systems.data.pubsub import Publisher



from settings.redis_db import redis_db

logger = logging.getLogger(__name__)


class DataSource(Publisher):
    compression_algorithm = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class Muscle(DataSource):
    pass


class AgentPrediction(DataSource):
    pass
