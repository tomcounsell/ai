from popoto.redis_db import POPOTO_REDIS_DB

from systems.theory.excitron import Excitron
from systems.theory.flection import Flection


def cycle(firing_excitrons: set):
    """
    1. excitrons fire
    2. all flections with strength > 7 send 1 energy to excitrons (unless excitron already fired)
    3. query all flections from fired excitrons to excitrons over threshold, strengthen flection
    4. all affected excitrons under threshold, -1 energy, weaken flection between

    Note: intersection of past firing an next firing excitrons should be empty set
    Also todo: re-balance excitrons (perhaps on a separate parallel schedule)
    """
    next_firing_excitrons = set()

    # 1. Excitrons fire and reset
    pipeline1 = POPOTO_REDIS_DB.pipeline()
    for excitron in firing_excitrons:
        pipeline1 = pipeline1.hset(excitron.db_key.redis_key, "energy", 0)
    pipeline1.execute()

    # 2. flections send energy to next excitrons
    pipeline2 = POPOTO_REDIS_DB.pipeline()
    for flection in Flection.query.filter(from_e__in=firing_excitrons):
        if flection.strength > 7 and flection.to_e not in firing_excitrons:
            pipeline2 = pipeline2.hincrby(flection.to_e.db_key.redis_key, "energy", 1)
    pipeline2.execute()

    # 3,4 strengthen and weaken flections
    pipeline3 = POPOTO_REDIS_DB.pipeline()
    for flection in Flection.query.filter(from_e__in=firing_excitrons):
        if flection.to_e.energy >= flection.to_e.inhibition_threshold:
            next_firing_excitrons.add(flection.to_e)
            pipeline3 = flection.strengthen(pipeline3)
        else:
            # all excitrons here either just fired OR had their energy increased
            # in BOTH cases, we want to decrease the energy and weaken the flection
            pipeline3 = pipeline3.hincrby(flection.to_e.db_key.redis_key, "energy", -1)
            pipeline3 = flection.weaken(pipeline3)
    pipeline3.execute()

    return next_firing_excitrons
