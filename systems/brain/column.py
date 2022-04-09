from popoto import Model, DictField, GeoField, IntField


class Column(Model):
    order = IntField()  # from 0 to COLUMNS_IN_A_BRAIN_COUNT
    neurons = DictField()  # NEURONS_IN_A_COLUMN_COUNT, key is int index


    # future research
    # coords = GeoField()  # replaces order, for 3D spherical topography
    # layer = IntField()  # for vertical layering of mini-columns
