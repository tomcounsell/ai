from popoto import Model, Relationship, IntField, KeyField, KeyField, GeoField
from redis.client import Pipeline

from systems.agent.sierra.excitron import Excitron


class Flection(Model):
    """
    inspired by biological axons and synapses passing signals between neurons
    -flect- comes from Latin, where it has the meaning "bend.'' It is related to -flex-.
        reflection: reversal back
        deflection: acute deviation
        inflection: obtuse deviation
        genuflection: pass forward, bow/honor
    """

    from_e = Relationship(Excitron)
    to_e = Relationship(Excitron)
    strength = IntField(default=0, max_value=15)
    # _angle? = IntField(default=91, max_value=180)  # odd number [1, .., 179]

    def strengthen(self, pipeline: Pipeline, amount=1) -> Pipeline:
        if self.strength == 15:
            return pipeline
        elif self.strength + amount > 15:
            amount = 15 - self.strength
        return pipeline.hincrby(self.db_key.redis_key, "strength", amount)

    def weaken(self, pipeline: Pipeline, amount=1) -> Pipeline:
        if self.strength == 0:
            return pipeline
        elif self.strength - amount < 0:
            amount = self.strength
        return pipeline.hincrby(self.db_key.redis_key, "strength", amount)
