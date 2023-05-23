import numpy
import numpy as np
import pandas as pd
from popoto import (
    Model,
    Field,
    DataFrameField,
    ListField,
    SortedField,
    AutoKeyField,
    KeyField,
)

from apps.agent.whiskey.flection import Flection
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
    number of potential flections = input SDR size + (2^12 * excitron_count)
    because excitrons can have up to 2^12 potential flections going to other excitrons
    Do not allow backward connections within an agent.
    Circular and backward connections can only exist outside the agent.
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

    def cycle(self, input_SDR: pd.DataFrame) -> pd.DataFrame:
        # run 1 agent cycle
        # accept an SDR input and create and SDR output
        A = input_SDR
        for l in range(1, L):
            # get W from flections
            A = np.dot(W, A) + b  # this is traditional NN

            # get W
            W = np.sum(A, W)  # this is excitrons gaining energy
            A = [w % w.inhibition_threshold for w in W]  # get excitrons over threshold
            W = W - A  # fired excitrons lose energy
            self.update_flections(l, W)
        output_SDR = A
        return output_SDR
