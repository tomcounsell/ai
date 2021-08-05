import logging
import random
from abc import ABC
import time
import numpy as np

from systems.agent.agent import Agent, redis_keys
from systems.stimulus.vision import Vision
from settings.redis_db import redis_db

logger = logging.getLogger(__name__)


class Population(ABC):
    """
    All the agents
    """
    stimuli = [Vision, ]
    active_agents = {}
    yet_active_agents = {}

    def __init__(self, *args, **kwargs):
        # refresh to compile dicts of all agent instances
        self.refresh()

    def __enter__(self):
        stimulators = {name: agent.stimulator for name, agent in self.active_agents.items()}
        while True:
            logger.debug(f'running {len(stimulators)} subscribers')
            for agent_name, stimulator in stimulators.items():
                try:
                    stimulator()  # run agent's subscriber class to stimulate agent
                except Exception as e:
                    logger.error(str(e))
                    logger.debug(stimulator.__dict__)
            yield self

    def refresh(self):
        # call redis to get names of all active agents
        self.active_agent_names = redis_db.lrange(redis_keys['active_agents'], 0, -1)
        self.active_agents = {name.decode(): Agent(name.decode()) for name in self.active_agent_names}

        all_agent_names = redis_db.lrange(redis_keys['all_agents'], 0, -1)
        retired_agents_names = redis_db.lrange(redis_keys['retired_agents'], 0, -1)
        self.yet_active_agent_names = set(all_agent_names) - set(self.active_agent_names) - set(retired_agents_names)
        self.yet_active_agents = {name.decode(): Agent(name.decode()) for name in self.yet_active_agent_names}

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass


class Community(ABC):
    """
    Any group of agents with something in common
    organized for teamwork or governance(voting)
    """
    pass


def bootstrap_population(max_num_agents: int = 0):
    all_stimuli = [
        {
            'class': Vision,
            # range for unique init params: (min_value, max_value)
            'static_params': {
                'zoom': lambda: abs(random.normalvariate(0, 0.5)),  # between (0, 1),
                'noise_strength': (0, 1),
                'compression_seed': (0, 32767),
            },
            # for operational range of freedom: (min_value, max_value)
            'motor_params': {
                'distance_from_center': (0, 1),
                'angle_from_center': (0, 2 * np.pi),
            },
            'count': 0
        },
    ]

    population = Population()
    active_count = len(population.active_agents)
    new_required_count = max_num_agents - (active_count + len(population.yet_active_agents))

    if new_required_count > 0:
        for i in range(0, new_required_count):
            agent = Agent()  # will give themself a name
            agent.save()

    population.refresh()
    for name, agent in population.yet_active_agents.items():

        if active_count >= max_num_agents:
            break

        agent.stimulus_subscriptions = dict()
        stimulus = all_stimuli[0]  # for stimulus in all_stimuli:
        agent.stimulus_subscriptions[stimulus['class'].__name__] = {
            'static_params': {
                k: stimulus['class'].param_generators[k]()
                for k in stimulus['static_params'].keys()
            },
            'motor_params': {
                k: stimulus['class'].param_generators[k]()
                for k in stimulus['static_params'].keys()
            },
        }
        logging.debug(agent.storage.value)
        agent.save()
        active_count += 1


    population.refresh()
    for name, agent in population.active_agents.items():
        agent.activate()
