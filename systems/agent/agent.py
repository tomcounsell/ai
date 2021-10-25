import ast
import logging
from collections import namedtuple

from settings import SITE_ROOT
from settings.redis_db import redis_db
from popoto import models, pubsub, fields
from systems.stimulus import Vision, Stimulus
from systems.structures.reference_frame import ReferenceFrame
from systems.structures.social_graph.node import AbstractNode

stimulus_subscription = namedtuple('stimulus_subscription', 'stimulus_class params')

redis_keys = {
    'all_agents': "list:Agents:all",  # redis list of agent keys
    'active_agents': "list:Agents:active",  # redis list of agent keys
    'retired_agents': "list:Agents:discarded",  # redis list of agent keys
}

class AgentStimulator(pubsub.Subscriber):
    def __init__(self, stimulus_subscriptions, callable, *args, **kwargs):
        self.classes_subscribing_to = stimulus_subscriptions.keys()
        self.callable = callable
        super().__init__(*args, **kwargs)

    def handle(self, channel, data, *args, **kwargs):  # for inherited Subscriber class
        logging.debug("running handler, callable")
        self.callable(channel, data)


class Agent(models.Model):
    """
    A subscriber to
    - at least one Stimulus
    A Publisher of
    - predictions
    With relationships to other Agents
    All above can evolve but can and mostly do persist over generations.
    The attributes above are as critical as the
    """
    # todo: inherit GraphModel or add NodeField

    name = fields.KeyField(key_prefix="Agent")
    # stimulus = fields.Field(type=Stimulus)
    stimulus_subscriptions = fields.Field(type=dict)
    reference_frame = fields.Field(type=ReferenceFrame)
    state = fields.Field(type=str, default="yet")

    def __init__(self, name: str = "", *args, **kwargs):
        super().__init__()
        self.name = name or self._name_thy_self()
        self.load_from_db()
        self.pubsub = redis_db.pubsub()
        self.stimulator = AgentStimulator(self.stimulus_subscriptions or {}, callable=self.stimulate)

    def activate(self):
        if self.state != "active":
            self.state = "active"
            # todo: move this to feature in Field(index=True) (with choices?)
            redis_db.lpush(redis_keys['active_agents'], self.name)  # add name to the active list
            self.save()

    def retire(self):
        self.state = "retired"
        # todo: move this to feature in Field(index=True) (with choices?)
        redis_db.lrem(redis_keys['active_agents'], 0, self.name)
        redis_db.lpush(redis_keys['retired_agents'], self.name)
        self.save()

    def stimulate(self, stimulus_class_name, data):
        if stimulus_class_name == Vision.__name__:
            from PIL import Image
            image_data = data.get('image_data', [])
            image = Image.fromarray(image_data)
            logging.debug(f"I can see an image with info {image.__dict__}")

    # def set_partner(self, context: dict, agent: 'Agent') -> None:
    #     super()._set_relationship_to_graphnode(context, agent.graph_node)

    def publish_prediction(self):
        pass

    def update_representation(self):
        """

        """
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
    #
    # def save(self):
    #     # todo: filter out custom standard data types, eg. timestamps
    #     self.storage.value = {k: self.describe().get(k) for k in [
    #         'name', 'stimulus_subscriptions', 'representation', 'state'
    #     ]}
    #     self.storage.save()


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
