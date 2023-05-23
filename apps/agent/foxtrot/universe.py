import logging
import time
from abc import ABC

from apps.agent.foxtrot.population import Population
from apps.data.camera import Camera
from apps.stimulus.vision import Vision

logger = logging.getLogger(__name__)


class Environment(ABC):
    """
    All the stimuli
    """

    stimuli = [
        Vision,
    ]

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


class Universe:
    def __init__(self):
        self.started_at = time.time()
        with Environment() as environment:
            with Population() as population:
                for i in range(100):
                    next(environment)
                    next(population)
                    input("Press Enter to continue...")
                    # time.sleep(0.1)  # be nice to the system :)
