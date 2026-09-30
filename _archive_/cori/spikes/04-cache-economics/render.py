"""Deterministic, volatility-ordered context render for spike 04.

State in, byte-identical system blocks out. Slices in volatility order:
persona -> operator digest -> objective roll-up -> thread summary -> recent
turns -> inbox. cache_control breakpoints after persona and after roll-up.
"""

from __future__ import annotations

import hashlib
import json

VOLATILITY_ORDER = ["persona", "digest", "rollup", "thread", "recent", "inbox"]
# "Importance" order a naive builder might choose: the urgent, volatile stuff
# first so the model "sees it first".
NAIVE_ORDER = ["inbox", "recent", "rollup", "thread", "persona", "digest"]
BREAK_AFTER = {"persona", "rollup"}


def render(state: dict[str, str], order: list[str]) -> list[dict]:
    blocks = []
    for name in order:
        blk = {"type": "text", "text": f"<{name}>\n{state[name]}\n</{name}>"}
        if name in BREAK_AFTER:
            blk["cache_control"] = {"type": "ephemeral"}
        blocks.append(blk)
    return blocks


def digest_of(blocks: list[dict]) -> str:
    return hashlib.sha256(json.dumps(blocks, sort_keys=True).encode()).hexdigest()[:16]
