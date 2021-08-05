import logging
from abc import ABC

from systems.data.data_source import DataSource, Muscle, AgentPrediction

logger = logging.getLogger(__name__)


class Stimulus(ABC):
    data = bytes()
    static_params: dict = {}
    motor_params: dict = {}
    default_params: dict = {}
    param_generators: dict = {}
    source: DataSource = None

    def __init__(self, source: DataSource, raw_input: bytes, *args, **kwargs):
        self.source = source

    def publish(self, *args, **kwargs):
        # open kafka channel, push self.data
        pass

    def prepare(self, *args, **kwargs):
        # overwrite me
        logger.warning("data preparation undefined")
        return self.data


class Motor(Stimulus):
    source: Muscle = None


class Prediction(Stimulus):
    source: AgentPrediction = None
