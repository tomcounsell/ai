import ast
import logging
from collections import namedtuple

import os
SITE_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..'))
from popoto import Model, Field, KeyField, Publisher, Subscriber, Relationship

# from systems.agent import Agent
# from systems.stimulus import Vision, Stimulus
from systems.structures.reference_frame import ReferenceFrame

# stimulus_subscription = namedtuple('stimulus_subscription', 'stimulus_class params')


class StimulusSubscription(Model):
    stimulus = Relationship(model='Stimulus')
    agent = Relationship(model='Agent')


class StimulusSubscriber(Subscriber):
    def __init__(self, stimulus_subscriptions, callable, *args, **kwargs):
        self.classes_subscribing_to = [sub.stimulator_class for sub in stimulus_subscriptions]
        self.callable = callable
        super().__init__(*args, **kwargs)

    def handle(self, channel, data, *args, **kwargs):  # for inherited Subscriber class
        logging.debug("running handler, callable")
        self.callable(channel, data)

