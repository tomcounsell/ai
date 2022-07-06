from popoto import Model, Relationship, IntField, KeyField, KeyField, GeoField


class Excitron(Model):
    """
    inspired by biological neurons with feedforward, feedback, and contextual dendrites of synapses
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
        unique_together = (
            "up",
            "down",
            "fore",
            "back",
            "right",
            "left",
            "happy",
            "sad",
        )

    def get_geohash(self, happy=0, sad=0):
        """
        bitwise coordinate hash of Q, R, S
        ref: https://www.redblobgames.com/grids/hexagons/#coordinates-cube
        """
        return

    def visualize(self):
        """
        render layers of semi-transparent Goldberg Icosahedral spheres.
        spiking patterns around the surface and up and down the layers
        see: https://en.wikipedia.org/wiki/Goldberg_polyhedron
        """
        return
