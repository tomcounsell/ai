import logging
import numpy as np

from systems.data.data_source import DataSource
from systems.stimulus.stimulus import Stimulus

logger = logging.getLogger(__name__)


class Metronome(Stimulus):
    """
    A unit of time has passed. This is an internal clock for agents using a time map
    """

    static_params = {
        'ticks_per_second': 1
    }
    motor_params = {}
    param_generators = {
        'ticks_per_second': lambda: 1
    }

    def __init__(self, source: DataSource, raw_input: bytes = b''):
        super().__init__(source, raw_input)

