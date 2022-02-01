import os
from popoto import Model, Field, SetField, KeyField, Publisher, Subscriber, Relationship
import logging
import uuid
from systems.agent.stimulus_subscription import StimulusSubscriber
from systems.stimulus import Vision, Stimulus
from systems.structures.reference_frame import ReferenceFrame

import csv
SITE_ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), '..'))
with open(SITE_ROOT + '/agent/names.csv', newline='') as f:
    reader = csv.reader(f)
    global FRIENDLY_NAMES_LIST
    FRIENDLY_NAMES_LIST = [row[0] for row in list(reader)]


class Agent(Model):  # Publisher
    """
    A unique entity for storing experiences and making predictions
    - Subscribes to Stimuli (via StimulusSubscriber object)
    - Publishes surprises, predictions, and confidences
    - Has relationships with other Agents
    Evolution of subscriptions creates new instances. Only relationships are mutable.
    These can evolve but can and mostly do persist over generations.
    """

    id = KeyField(null=False)  # deterministic uuid, based on stimuli - see pre_save method
    state = KeyField(type=str, default="yet", null=False)  # choices: yet, active, retired, deleted
    stimuli = SetField()  # set of stimulus subscriptions

    # A Reference Frame is a stored structure of a model.
    # It is a map for storing knowledge and making judgements (predictions)
    # _reference_frame = DataFrameField(type=bytes)  # type=ReferenceFrame

    # Squads, Chapters, Tribes, and Guilds
    # Crew, Party, Unit, Faction, Troop, Lineup
    # (https://www.theproducthub.io/2019/10/20/agile-team-organisation-squads-chapters-tribes-and-guilds/)
    # groups = Relationship("Group", many=True)

    def pre_save(self, *args, **kwargs):
        self.id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(self.stimuli))).replace("-","")
        return super().pre_save(*args, **kwargs)

    def __init__(self, name: str = "", *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stimulator = StimulusSubscriber(self.stimuli or {}, callable=self.stimulate)

    def stimulate(self, stimulus_class_name, data):
        if stimulus_class_name == Vision.__name__:
            from PIL import Image
            image_data = data.get('image_data', [])
            image = Image.fromarray(image_data)
            logging.debug(f"I can see an image with info {image.__dict__}")

    @property
    def name(self):
        global FRIENDLY_NAMES_LIST
        return FRIENDLY_NAMES_LIST[hash(self.id) % len(FRIENDLY_NAMES_LIST)] + str(hash(self.id) % 100)
