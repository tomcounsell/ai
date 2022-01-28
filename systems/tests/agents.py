import sys

from django.test import TestCase

from systems.agent.agent import Agent
from systems.stimulus.vision import Vision

lisa_info_dict = {
    'generic': "mammal:human:woman",
    'group': "BlackPink",
    'friends': ["Jisoo", "Rose", "Jenny", ],
    'age': 24,
    'specifics': {'favorite:color': "yellow", },
}

rose_info_dict = {
    'generic': "mammal:human:woman",
    'group': "BlackPink",
    'friends': ["Jisoo", "Lisa", "Jenny", ],
    'birthday_string': "February 11, 1997",
    'specifics': {'favorite:color': "baby pink", },
}


class test_agent(TestCase):

    def setup(self):
        self.assertEqual('utf-8', sys.getdefaultencoding())

    def test_can_create_agent(self):
        self.lisa = Agent(name="Lisa")
        self.lisa.stimuli.append({
            'class': Vision,
            'static_params': {
                'zoom': 0,
                'noise_strength': 0.12345,
                'compression_seed': 12461,
            },
            'motor_params': {
                'distance_from_center': 0,
                'angle_from_center': 0,
            },
        })
        self.lisa.save()

        # self.rose = Agent("Rose")

    def test_can_stimulate(self):
        self.lisa = Agent("Lisa")
        from systems.data.camera import Camera
        with Camera() as webcam:
            vision = Vision(webcam)
            self.lisa.stimulate(Vision, data={'image': vision.image})

    def test_can_predict(self):
        pass

    def tearDown(self) -> None:
        self.lisa.delete()
