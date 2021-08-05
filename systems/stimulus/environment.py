import logging
import random
from abc import ABC
import time
import numpy as np
import cv2
from systems.agent.agent import Agent, redis_keys
from systems.stimulus.vision import Vision
from systems.data.camera import Camera
from settings.redis_db import redis_db

logger = logging.getLogger(__name__)


class Environment(ABC):
    """
    All the stimuli
    """
    stimuli = [Vision, ]

    def __init__(self, *args, **kwargs):
        # refresh to compile stimulus instances
        pass

    def __enter__(self):
        with Camera() as webcam:
            vision = Vision(webcam)
            while True:
                vision.publish_image_data()
                yield self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
