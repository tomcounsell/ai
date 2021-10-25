import logging
import numpy as np
from popoto.pubsub import Publisher

logger = logging.getLogger(__name__)


class DataSource(Publisher):
    compression_algorithm = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class Muscle(DataSource):
    pass


class AgentPrediction(DataSource):
    pass
