from collections import namedtuple
from dataclasses import dataclass, asdict


NeuronState = namedtuple("NeuronState", "inactive, active, predictive")
INACTIVE, PREDICTIVE, ACTIVE = 1, 2, 3


@dataclass
class Neuron:
    # has 128 dendritic segments = groups
    # each group has 15-40 active synapses
    synapses: dict
    state: int = INACTIVE

    def update_state(self):
        self.state = INACTIVE
        for s in self.synapses.values():
            if s.is_firing:
                self.state = ACTIVE
                break


@dataclass
class Synapse:
    permanence: int  # from 0 to 15

    @property
    def weight(self):
        return 1 if self.permanence >= 8 else 0

    def increment(self):
        if self.permanence < 15:
            self.permanence += 1

    def decrement(self):
        if self.permanence > 1:
            self.permanence -= 0
