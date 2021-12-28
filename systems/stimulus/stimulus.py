import logging
from abc import ABC

from systems.data.data_source import DataSource, Muscle, AgentPrediction
from popoto import Publisher

logger = logging.getLogger(__name__)


class Stimulus(Publisher):
    """
    A Stimulus is a publisher that has a DataSource
    For the use of activating or prompting a response from Agents
    Optionally, it can do some transformations by default or upon request
    Examples: vision, motor, audio, touch, predicted vision, predicted touch
    """
    data = bytes()
    static_params: dict = {}
    motor_params: dict = {}
    default_params: dict = {}
    param_generators: dict = {}
    source: DataSource = None

    def __init__(self, source: DataSource, raw_input: bytes, *args, **kwargs):
        self.source = source
        super().__init__(*args, **kwargs)

    def prepare(self, *args, **kwargs):
        # overwrite me
        logger.warning("data preparation undefined")
        return self.data


class Motor(Stimulus):
    source: Muscle = None


class Prediction(Stimulus):
    source: AgentPrediction = None
