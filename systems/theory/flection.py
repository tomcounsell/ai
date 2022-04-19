from popoto import Model, Relationship, IntField, KeyField, KeyField, GeoField
from popoto.fields.key_field_mixin import KeyFieldMixin
from redis.client import Pipeline

from systems.theory.excitron import Excitron


class KeyRelationship(KeyFieldMixin, Relationship):
    # todo: add to popoto shortcuts
    def __init__(self, *args, **kwargs):
        kwargs['key'] = True
        super().__init__(**kwargs)


class Flection(Model):
    """
    inspired by biological axons and synapses passing signals between neurons
    -flect- comes from Latin, where it has the meaning "bend.'' It is related to -flex-.
    deflection (acute deviation), genuflection (bow/honor), inflection (obtuse deviation), reflection (reversal)
    """
    from_e = KeyRelationship(Excitron)
    to_e = KeyRelationship(Excitron)
    strength = IntField(default=0, max_value=15)
    # _angle? = IntField(default=91, max_value=180)  # odd number [1, .., 179]

    def grow_stronger(self, pipeline: Pipeline, amount=1) -> Pipeline:
        pipeline.hincrby(self.db_key.redis_key, "strength", amount)
        return pipeline
