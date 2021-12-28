import ast
import logging
from collections import namedtuple

from settings import SITE_ROOT
from settings.redis_db import redis_db
from popoto import Model, Field, KeyField, Publisher, Subscriber
from systems.stimulus import Vision, Stimulus
from systems.structures.reference_frame import ReferenceFrame

stimulus_subscription = namedtuple('stimulus_subscription', 'stimulus_class params')

class StimulusSubscriber(Subscriber):
    def __init__(self, stimulus_subscriptions, callable, *args, **kwargs):
        self.classes_subscribing_to = stimulus_subscriptions.keys()
        self.callable = callable
        super().__init__(*args, **kwargs)

    def handle(self, channel, data, *args, **kwargs):  # for inherited Subscriber class
        logging.debug("running handler, callable")
        self.callable(channel, data)


class Agent(Model, Publisher):
    """
    A unique entity for storing experiences and making predictions
    - Subscribes to Stimuli (via StimulusSubscriber object)
    - Publishes surprises, predictions, and confidences
    - Has relationships with other Agents
    These can evolve but can and mostly do persist over generations.
    """

    name = KeyField(type=str, null=False, unique=True)
    state = KeyField(type=str, default="yet", null=False)  # choices: yet, active, retired, deleted

    stimuli = Field(type=set)  # set of stimulus subscriptions
    #todo refactor to many Relationships with Stimuli

    # A Reference Frame is a stored structure of a model.
    # It is a map for storing knowledge and making judgements (predictions)
    _reference_frame = Field(type=bytes)  # type=ReferenceFrame

    # Squads, Chapters, Tribes, and Guilds
    # Crew, Party, Unit, Faction, Troop, Lineup
    # (https://www.theproducthub.io/2019/10/20/agile-team-organisation-squads-chapters-tribes-and-guilds/)
    # groups = Relationship("Group", many=True)

    def __init__(self, name: str = "", *args, **kwargs):
        self.name = name or self._name_thy_self()
        super().__init__(*args, **kwargs)
        self.stimulator = StimulusSubscriber(self.stimuli or {}, callable=self.stimulate)

    def stimulate(self, stimulus_class_name, data):
        if stimulus_class_name == Vision.__name__:
            from PIL import Image
            image_data = data.get('image_data', [])
            image = Image.fromarray(image_data)
            logging.debug(f"I can see an image with info {image.__dict__}")

    def _name_thy_self(self):
        import csv
        from settings.redis_db import redis_db
        with open(SITE_ROOT+'/static/names.csv', newline='') as f:
            reader = csv.reader(f)
            all_names = list(reader)
        num_names = Agent.query.count()
        # get the next name, optionally add number if repeated. eg. Lisa5
        self.name = f"{all_names[num_names % len(all_names)][0]}{num_names // len(all_names)}"
        return self.name
