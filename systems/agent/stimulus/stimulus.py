import logging
from abc import ABC

from systems.data.data_source import DataSource, Camera, Muscle, AgentPrediction
from apps.common.utilities.compression import image_compresssion

logger = logging.getLogger(__name__)


class Stimulus(ABC):
    data = bytes()
    params: dict = {}
    source: DataSource = None

    def __init__(self, source: DataSource, raw_input: bytes, *args, **kwargs):
        self.source = source

    def publish(self, *args, **kwargs):
        # open kafka channel, push self.data
        pass

    def prepare(self, *args, **kwargs):
        # overwrite me
        logger.warning("data preparation undefined")
        return self.data


class Vision(Stimulus):

    def __init__(self, source: DataSource, raw_input: bytes = b''):
        super().__init__(source, raw_input)
        if isinstance(source, Camera):
            self.image = source.get_sample()

    def prepare_image(self, params: dict = {}):
        default_params = {
            'zoom': 0,  # float between 0..1
            'distance_from_center': 0,  # float between 0..1
            'angle_from_center': 0,  # in radians between 0, 2*pi
            'noise_strength': 0.1,  # float between 0..1
            'compression_seed': 123,  # integer(small)
        }
        params = {**default_params, **params}

        # python 3.9 can do params = params | default_params
        # ideally has increased zoom (0->1) or distance_from_center (0->1), not both
        self.image = image_compresssion.zoom_and_crop(
            self.image,
            params['zoom'], params['angle_from_center'], params['distance_from_center']
        )
        self.image = image_compresssion.add_random_noise(image=self.image, strength=params['noise_strength'])
        self.image = image_compresssion.add_random_compression(image=self.image, random_seed=params['compression_seed'])
        # return self.image

    def prepare(self, params: dict = None):
        return self.prepare_image(params)

    def show(self):
        self.image.show()


class Motor(Stimulus):
    source: Muscle = None


class Prediction(Stimulus):
    source: AgentPrediction = None
