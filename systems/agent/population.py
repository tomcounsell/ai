from abc import ABC

from systems.agent.stimulus.stimulus import Vision


class Population(ABC):
    """
    All the agents
    """
    stimuli = [Vision, ]





class Community(ABC):
    """
    Any group of agents with something in common
    organized for teamwork or governance(voting)
    """
    pass
