import pandas as pd

from dataclasses import dataclass, asdict
import random
from abc import ABC

from systems.agent.numenta.brain import Brain


class Simulation(ABC):
    def __init__(self):
        self.brain = Brain()
        self.brain_df = pd.DataFrame()

        self.data_df = pd.DataFrame()

    def load_data(self, data):
        # should be timeseries or array of same-length strings
        self.data_df = pd.DataFrame(data)

    def feedforward(self):

        pass
