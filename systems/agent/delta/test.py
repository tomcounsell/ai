import math

import numpy as np

from .agent import Agent


AGENT_SIZE = int(math.pow(2, 5))  # 2^19 == 2^7 neurons * 2^7 dendrites * 2^5 synapses
# 2^19 may require a ~200 GB size weights map
SPARSITY = 128  # density = 1/sparsity


agent = Agent(AGENT_SIZE)
