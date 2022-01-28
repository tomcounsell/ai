import logging

import cv2
import numpy as np
from PIL import Image

from apps.common.utilities.compression import image_compresssion
from systems.data.camera import Camera
from systems.data.data_source import DataSource
from systems.stimulus.stimulus import Stimulus

logger = logging.getLogger(__name__)

static_params = {
    'zoom': 0,  # float between 0..1
    'noise_strength': 0.1,  # float between 0..1
    'compression_seed': 123,  # integer(small)
}
motor_params = {
    'distance_from_center': 0,  # float between 0..1
    'angle_from_center': 0,  # in radians between 0, 2*pi
}
default_params = {**static_params, **motor_params}


class Vision(Stimulus):
    param_generators = {
        'zoom': lambda: min(abs(np.random.normal(0, 0.5)), 1),  # float between 0..1
        'noise_strength': lambda: min(abs(np.random.normal(0, 0.5)), 1),  # float between 0..1
        'compression_seed': lambda: np.random.randint(1, 32767),  # integer(small)
        'distance_from_center': min(abs(np.random.normal(0, 0.5)), 1),  # float between 0..1
        'angle_from_center': np.random.uniform(0, 2*np.pi),  # in radians between 0, 2*pi
    }

    def __init__(self, data_source: DataSource, raw_input: bytes = b'', *args, **kwargs):
        self.data_source = data_source
        super().__init__(data_source, raw_input)
        self.image = self.get_image()
        self.sample = self.prepare_image(self.image)

    @classmethod
    def prepare_image(cls, image, params: dict = {}):
        params = {**default_params, **params}
        # ideally has increased zoom (0->1) or distance_from_center (0->1), not both
        image = image_compresssion.zoom_and_crop(
            image,
            params['zoom'], params['angle_from_center'], params['distance_from_center']
        )
        image = image_compresssion.add_random_noise(image=image, strength=params['noise_strength'])
        image = image_compresssion.add_random_compression(image=image, random_seed=params['compression_seed'])
        return image

    def show(self):
        self.image.show()

    def get_image_data(self):
        if isinstance(self.data_source, Camera):
            with self.data_source:
                frame = self.data_source.get_frame()
                greyscale_array = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
                return greyscale_array

    def get_image(self):
        return Image.fromarray(self.get_image_data())

    def publish_image_data(self, image_data=None):
        image_data = image_data or self.get_image_data()
        # add noise, so agents learn in a more analog style
        # publish via stimulus
        # it should do something with image like pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        self.publish({'image_data': image_data})
