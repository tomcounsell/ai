import pandas as pd
import numpy as np


def generate_map(
    size: int,
    sparsity: float,
    random_seed: int = None,
    distribution_type: str = "normal",
) -> np.ndarray:
    """
    generate a square 2d matrix
    size:  matrix width (== height)
    sparsity: the avg ratio of values to zeros in the map
    random_seed to use for deterministic map generation eg. 0
    distribution_type: type of random number distribution

    TODO:
    1. create map of zeros for the given size
    2. given sparsity, generate an amount of random numbers
    3. insert those numbers into the map, excluding the diagonal and just above diagonal
    4. check 50% threshold is close to 128

    """
    if random_seed:
        np.random.seed(random_seed)

    # normal distribution random array
    if distribution_type == "normal":
        weights = np.random.normal(size=size * size, loc=128, scale=48)
    else:
        raise Exception("what else do you want? zeros? random? inverted normal?")

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
