from abc import ABC
from popoto import Model

# import xarray as xr


class ReferenceFrame(Model):
    # data = fields.Field(type=xr.DataArray)
    # datetime = fields.Field(sort_key=True)

    class Meta:
        pass




"""
types of reference frames
 - ego-centric : I am the center and all things relate to me and my position
 - object-centric: anchored to object in space ('what' pathway)
 - vector: directional map ('where' pathway)
 - concept: anchored to a noun in idea space, with correlations to other concepts (correlates)

"""
