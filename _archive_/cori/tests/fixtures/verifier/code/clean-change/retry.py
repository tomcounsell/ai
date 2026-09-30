"""Exponential backoff with a cap and full jitter."""

import random


def backoff_delays(attempts: int, base: float = 0.5, cap: float = 30.0) -> list[float]:
    """Delays before each retry. Attempt n waits a uniform draw in
    [0, min(cap, base * 2**n)]. Length is attempts; each value is within its bound."""
    if attempts < 0:
        raise ValueError("attempts must be non-negative")
    return [random.uniform(0, min(cap, base * 2**n)) for n in range(attempts)]
