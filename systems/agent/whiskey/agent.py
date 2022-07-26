import numpy
import numpy as np
from popoto import (
    Model,
    Field,
    DataFrameField,
    ListField,
    SortedField,
    AutoKeyField,
    KeyField,
)

from systems.agent.whiskey.flection import Flection
from collections import namedtuple

TopographySeed = namedtuple(
    "TopographySeed", "sparsity, fractality, uniformity, random_seed"
)
"""
The TopographySeed creates a deterministic initial network for an Agent Blueprint
(just like the seed for a map generator places rivers, mountains, islands, etc)
Agents may converge on efficient topographies but require new random seeds to generate constant diversity within. 
"""


class AgentBlueprint:
    """
    The blueprint for an Agent's collection of flections
    provides function generators for updating flections of the same blueprint
    """

    input_size: tuple = (0, 0)  # size of the input SDR
    output_size: tuple = (0, 0)  # size of the output SDR
    grammar: list = [
        Flection,
    ]
    topography_seed: TopographySeed = TopographySeed()

    @property
    def input_width(self):
        return self.input_size[0]

    @property
    def input_height(self):
        return self.input_size[1]

    @property
    def output_width(self):
        return self.output_size[0]

    @property
    def output_height(self):
        return self.output_size[1]


class Agent(Model):
    """
    An Agent is a large collection of flections
    number of potential flections  = 32 * excitron_count
    because excitrons can have up to 32 potential flections going in or out
    """

    id = AutoKeyField()
    excitrons_count = KeyField()
    perception_space = KeyField()
    # Choice from ['vision', 'metronome', 'translators', ...]

    flections_from_e = DataFrameField()
    flections_to_e = DataFrameField()
    flections_stregth = DataFrameField()

    utility_score = SortedField(type=int)
    value_score = SortedField(type=int)
    # grammar - capable complexity of communication
    # vocabulary - communication limits nouns, verbs, adjectives

    @property
    def cost(self) -> int:
        """
        input size: self.input_SDR.size
        output size: self.output_SDR.size
        internal flections:
        """
        # the number of flections is an easy 99% of cost. just add 100 overhead cost
        return 100 + len(self.flections)

    @property
    def calc_value_score(self) -> int:
        utility = self.utility_score  # the value as voted by other agents
        cost = self.cost  # the cost of operation = the count of flections
        # maybe need to normalize first
        self.value_score = utility - cost
        return self.value_score

    input_SDR = DataFrameField()
    output_SDR = DataFrameField()
