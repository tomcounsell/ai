from dataclasses import dataclass

from popoto import Model, Relationship, IntField, KeyField, KeyField, GeoField

# @dataclass
# class Location:
#


class Excitron(Model):
    """
    traversal is locked to Q+R+S == 0
    dQ+ => stimulates | dQ- => predicts | dQ= => contextualizes
    intersection(Q1.routes,Q2.routes) == {}
    """
    up = KeyField(type=int, unique=False)  # Q1
    down = KeyField(type=int, unique=False)  # Q2
    fore = KeyField(type=int, unique=False)  # R1
    back = KeyField(type=int, unique=False)  # R2
    right = KeyField(type=int, unique=False)  # S1
    left = KeyField(type=int, unique=False)  # S2
    happy = KeyField(type=int, default=0, unique=False)  # L1 open
    sad = KeyField(type=int, default=0, unique=False)  # L2 open

    energy = IntField(default=0, max_value=15)
    inhibition_threshold = IntField(default=1)

    class Meta:
        unique_together = ('up', 'down', 'fore', 'back', 'right', 'left', 'happy', 'sad')

    @property
    def state(self):
        return 1 if sum(
            [1 if unit.state else 0 for unit in self.stimuli] +
            [1 if unit.state else 0 for unit in self.context] +
            [1 if unit.state else 0 for unit in self.predictors]
        ) > self.inhibition_threshold else 0

    # def split(self):
    #     self.inhibition_threshold += 1 if self.inhibition_threshold % 2 == 1 else 0
    #     nong_a = Excitron(inhibition_threshold=self.inhibition_threshold/2)
    #     nong_a.context = self.context
    #     nong_a.predictors = self.predictors
    #
    #     nong_b = Excitron(inhibition_threshold=self.inhibition_threshold/2)
    #     nong_b.context = self.context
    #     nong_b.predictors = self.predictors
    #
    #     for s_count, unit in enumerate(self.stimuli):
    #         if s_count % 2 == 0:
    #             nong_a.stimuli.add(unit)
    #         else:
    #             nong_b.stimuli.add(unit)
    #
    #     nong_a.save()
    #     nong_b.save()
    #     self.delete()

    def get_geohash(self, happy=0, sad=0):
        """
        bitwise coordinate hash of Q, R, S
        """
        return

    def visualize(self):
        """
        render layers of semi-transparent Goldberg Icosahedral spheres.
        spiking patterns around the surface and up and down the layers
        see: https://en.wikipedia.org/wiki/Goldberg_polyhedron
        """
        return
