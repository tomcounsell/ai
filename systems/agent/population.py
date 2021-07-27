from abc import ABC

import numpy as np

from systems.agent.agent import Agent, redis_keys
from systems.agent.stimulus.stimulus import Vision
from settings.redis_db import redis_db


class Population(ABC):
    """
    All the agents
    """
    stimuli = [Vision, ]
    active_agents = {}
    _yet_active_agents = None

    def __init__(self, *args, **kwargs):
        # call redis to get names of all active agents
        active_agent_names = redis_db.lrange(redis_keys['active_agents'], 0, -1)
        # create dict of all agent instances
        self.active_agents = {name: Agent(name) for name in active_agent_names}

    @property
    def yet_active_agents(self, rescan=False):
        if rescan or self._yet_active_agents is None:
            all_agent_names = redis_db.lrange(redis_keys['all_agents'], 0, -1)
            active_agent_names = redis_db.lrange(redis_keys['active_agents'], 0, -1)
            discarded_agent_names = redis_db.lrange(redis_keys['discarded_agents'], 0, -1)
            yet_active_agent_names = set(all_agent_names) - set(active_agent_names) - set(discarded_agent_names)
            self._yet_active_agents = {name: Agent(name) for name in yet_active_agent_names}
        return self._yet_active_agents



class Community(ABC):
    """
    Any group of agents with something in common
    organized for teamwork or governance(voting)
    """
    pass


def bootstrap_population():
    stimuli = [
        {
            'class': Vision,
            'parameters': { # param keyword: (min_value, max_value)
                'zoom': (0, 1),
                'distance_from_center': (0, 1),
                'angle_from_center': (0, 2*np.pi),
                'noise_strength': (0, 1),
                'compression_seed': (0, 32767),
            },
            'count': 0
        }
    ]


    population = Population()

    for name, agent in population.active_agents.items():


    for name, agent in population.yet_active_agents.items():

        for key, (min_value, max_value) in parameters:
