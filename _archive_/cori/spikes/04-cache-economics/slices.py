"""Synthetic slice content, sized in tokens with the provider's own counter.
Deterministic: the same seed and sizes give the same bytes."""

from __future__ import annotations

import random

WORDS = (
    "objective budget ledger verifier persona correction belief report brief "
    "sandbox kernel gateway approval effect class deadline premise assumption "
    "criteria evidence summary thread inbox operator digest roll-up delegate"
).split()


def prose(seed: str, n_words: int) -> str:
    rng = random.Random(seed)
    out = []
    for i in range(n_words):
        w = rng.choice(WORDS)
        if i % 13 == 12:
            w += "."
        out.append(w)
    return " ".join(out)
