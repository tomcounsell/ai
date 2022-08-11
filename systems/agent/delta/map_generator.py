import pandas as pd
import numpy as np


def generate_map(self, size, random_seed=42) -> np.ndarray:
    np.random.seed(random_seed)

    # normal distribution random array
    weights = np.random.normal(size=size * size, loc=128, scale=32)

    # set floor and ceiling for unsigned int8
    weights[weights > 255] = 255
    weights[weights < 0] = 0

    # set datatype to unsigned int8 (0 .. 255) - 1 byte
    # "B" https://numpy.org/doc/stable/reference/arrays.scalars.html#numpy.uint8
    weights = weights.astype("B")

    # reshape into 2d array matrix
    weights = np.reshape(weights, (size, size))

    # set diagonal to 0
    np.fill_diagonal(weights, 0)

    # set value above diagonal to 0
    indices = np.arange(len(weights))
    weights[indices[:-1], indices[1:]] = [0] * (size - 1)

    return weights

    # # for a pandas df
    # df = pd.DataFrame(
    #     np.random.randint(64, 256 - 64, size=(size, size)),
    #     columns=list(range(size)),
    #     dtype=np.uint8,
    # )
