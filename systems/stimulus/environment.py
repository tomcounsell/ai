import logging
import random
from abc import ABC
import time
import numpy as np

from systems.agent.agent import Agent, redis_keys
from systems.stimulus.vision import Vision
from settings.redis_db import redis_db

logger = logging.getLogger(__name__)




class Environment(ABC):
    """
    All the stimuli
    """
    stimuli = [Vision, ]

    def __init__(self, *args, **kwargs):
        # refresh to compile stimulus instances
        self.refresh()


    def refresh(self):
        pass


    def run_vision(self):
        from systems.data.camera import Camera
        with Camera() as webcam:
            while True:
                webcam.publish_image()
