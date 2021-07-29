import sys


from django.test import TestCase

from systems.agent.agent import Agent

lisa_info_dict = {
    'generic': "mammal:human:woman",
    'group': "BlackPink",
    'friends': ["Jisoo", "Rose", "Jenny", ],
    'age': 24,
    'specifics': {'favorite:color': "yellow", },
}


class test_key_value(TestCase):
    basic_agent = Agent(name="Lisa")

    def setup(self):
        self.assertEqual('utf-8', sys.getdefaultencoding())

    def test_can_manage_db_key(self):
        pass
