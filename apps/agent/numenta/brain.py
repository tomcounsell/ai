from dataclasses import dataclass, asdict
import random
from abc import ABC
from popoto.redis_db import POPOTO_REDIS_DB as REDIS

from apps.agent.numenta.column import Column
from apps.agent.numenta.neuron import Synapse, Neuron

COLUMNS_IN_A_BRAIN_COUNT = (
    32  # increase to 2048, actually about 2*10^8 in human neocortex
)
NEURONS_IN_A_COLUMN_COUNT = 32  # keep, actually about 100±20 in human neocortex
DENDRITES_IN_A_NEURON_COUNT = 128  # keep, actually about 300 in human neocortex
SYNAPSES_IN_A_DENDRITE_COUNT = 40  # keep, about right in human neocortex


class Brain(ABC):
    def __init__(self):
        self.columns = [
            Column(
                order=column_index,
                neurons={
                    neuron_index: asdict(
                        Neuron(
                            synapses={
                                dendrite_index: [
                                    asdict(Synapse(permanence=random.randint(0, 15)))
                                    for i in range(SYNAPSES_IN_A_DENDRITE_COUNT)
                                ]
                                for dendrite_index in range(DENDRITES_IN_A_NEURON_COUNT)
                            }
                        )
                    )
                    for neuron_index in range(NEURONS_IN_A_COLUMN_COUNT)
                },
            )
            for column_index in range(COLUMNS_IN_A_BRAIN_COUNT)
        ]

    def save(self):
        pipeline = REDIS.pipeline()
        for c in self.columns:
            c.save(pipeline=pipeline)
        pipeline.execute()

    def get_synapses_count(self):
        return sum(
            [
                sum(
                    [
                        sum(len(s) for s in n.synapses.values())
                        for n in c.neurons.values()
                    ]
                )
                for c in self.columns
            ]
        )

    def get_active_synapses_count(self):
        return sum(
            [
                sum(
                    [
                        sum(
                            sum(
                                [
                                    1 if s.permanence >= 8 else 0
                                    for s in dendrite_synapses
                                ]
                            )
                            for dendrite_synapses in n.synapses.values()
                        )
                        for n in c.neurons.values()
                    ]
                )
                for c in self.columns
            ]
        )


def delete_brains():
    pipeline = REDIS.pipeline()
    for c in Column.query.all():
        c.delete(pipeline=pipeline)
    pipeline.execute()
