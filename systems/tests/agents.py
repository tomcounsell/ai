import sys

from django.test import TestCase

from systems.agent.agent import Agent
from systems.agent.stimulus.stimulus import Vision

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
        self.lisa = Agent("Lisa")
        self.lisa.save()
        self.lisa.stimulus_subscriptions.append({
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

        self.rose = Agent("Rose")

    def test_can_stimulate(self):
        from systems.data.data_source import Camera
        with Camera() as webcam:
            vision = Vision(webcam)
            self.lisa.stimulate(Vision, data={'image': vision.image})

    def test_can_predict(self):
        pass

    def tearDown(self) -> None:
        from settings.redis_db import redis_db
        redis_db.delete(self.lisa.storage.get_db_key())
        redis_db.delete(self.rose.storage.get_db_key())
