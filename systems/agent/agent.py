import ast
import logging
import random
from collections import namedtuple

from settings import SITE_ROOT
from systems.agent.stimulus.stimulus import Vision
from systems.structures.redis_storage.key_value import KeyValueStorage
from systems.structures.reference_frame import ReferenceFrame
from systems.structures.social_graph.node import AbstractNode

stimulus_subscription = namedtuple('stimulus_subscription', 'stimulus_class params')

redis_keys = {
    'all_agents': "list:Agents:all",  # redis list of agent keys
    'active_agents': "list:Agents:active",  # redis list of agent keys
    'retired_agents': "list:Agents:discarded",  # redis list of agent keys
}


class Agent(AbstractNode, ReferenceFrame):
    stimulus_subscriptions: dict = {}
    representation: dict = dict(name='empty')

    def __init__(self, name: str = "", *args, **kwargs):
        self.name = name or self._name_thy_self()
        self.storage = KeyValueStorage(key=self.__class__.__name__, key_suffix=self.name)
        self.state = "yet"
        if isinstance(self.storage.value, dict):
            for k, v in self.storage.value.items():
                setattr(self, k, v)

    def activate(self):
        # for stimulus in self.stimulus_subscriptions:
        #     pubsub.subscribe(
        #         stimulus['class'],
        #         motor_params=stimulus.get('motor_params', {}),
        #         activation=self.stimulate
        #     )
        if self.state != "active":
            self.state = "active"
            from settings.redis_db import redis_db
            redis_db.lpush(redis_keys['active_agents'], self.name)  # add name to the active list
            self.save()

    def retire(self):
        self.state = "retired"
        from settings.redis_db import redis_db
        redis_db.lrem(redis_keys['active_agents'], 0, self.name)
        redis_db.lpush(redis_keys['retired_agents'], self.name)
        self.save()

    def stimulate(self, stimulus_class, data):
        if stimulus_class.__name__ == Vision.__name__:
            from PIL.Image import Image
            image = data.get('image', None)
            if isinstance(image, Image):
                logging.debug(f"I can see an image with info {image.__dict__}")
                self.last_seen = image

    def set_partner(self, context: dict, agent: 'Agent') -> None:
        super()._set_relationship_to_graphnode(context, agent.graph_node)

    def publish_prediction(self):
        pass

    def update_representation(self):
        pass

    @classmethod
    def representation_from_string(cls, rep_string):
        return ast.literal_eval(rep_string)

    def describe(self):
        return self.__dict__

    def _name_thy_self(self):
        import csv
        from settings.redis_db import redis_db
        with open(SITE_ROOT+'/static/names.csv', newline='') as f:
            reader = csv.reader(f)
            all_names = list(reader)
        num_names = int(redis_db.llen(redis_keys['all_agents']))
        # get the next name, optionally add number if repeated. eg. Lisa5
        self.name = f"{all_names[num_names % len(all_names)][0]}{num_names // len(all_names)}"
        redis_db.lpush(redis_keys['all_agents'], self.name)  # add my name to the list, asap
        return self.name

    def save(self):
        # todo: filter out custom standard data types, eg. timestamps
        self.storage.value = {k: self.describe().get(k) for k in [
            'name', 'stimulus_subscriptions', 'representation', 'state'
        ]}
        self.storage.save()


class Concept(AbstractNode):

    def set_correlate(self, context: dict, concept: 'Concept') -> None:
        super()._set_relationship_to_graphnode(context, concept.graph_node)


class ThinkingFastAndSlow:
    """
    Are these separate categories of agents OR alternative modes for running any agent?

    Fast, Instinctual Mind
    - generalized guessing
    - trained on everything under the sun
    - like GPT-3
    - every response/guess has a confidence value
    - add quantum computer?
    Slow, Contemplative Mind
    Computation Engine - a toolset for certifying the validity of anything
    - algorithms for calculations in math and physics
    - Hard-coded methods and grammars
    - goal to comprehend the governing laws of all nations
    """
