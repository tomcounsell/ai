from dataclasses import dataclass
import numpy as np

from systems.agent.delta.map_generator import generate_map


@dataclass
class Agent:
    """
    A list of energy states of cells
    A list of thresholds at which cells release energy and return to 0
    A mapping of cell connection weights (square matrix, size len(state)^2)
    """

    state: np.ndarray  # 1 dimensional
    thresholds: np.ndarray  # 1 dimensional
    mapping: np.ndarray  # 2 dimensional

    def __init__(self, size: int):
        self.state = np.zeros(size)
        self.mapping = generate_map(size)
        non_zeros = self.mapping[self.mapping != 0]

        std_dev = non_zeros.std()  # should be 24
        mean = np.average(non_zeros)  # should be size/2 = 128

        # z = (x – mean) / std_dev

        # threshold should be where chance of a value being over the threshold is 1/size

        self.thresholds = np.ones(size)

    def cycle(self, input: np.ndarray):
        # decay energy states by 1 unit per cell
        self.state -= np.ones_like(self.state)

        # fire cells
        output, self.state = np.divmod(self.state, self.thresholds)

        # add input to the firing output
        output[: len(input)] += input

        # use cell mapping to update state
        self.state += np.multiply(output, self.mapping)

        # update mapping weights
        # how? (fire together wire together)

        return output
