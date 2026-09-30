"""Money, estimates, and the cache prefix rule. Plan 04, the budget
pre-check and the prefix rule.

Everything here is pure: a body in, a number or a hash out. The gateway
holds the per-brief history these functions are called against, and the
seat file holds the prices. Architecture §4's conversion lives in
`reserve`: the remaining money, at the resolved seat's prices, is the token
allowance for one call.

Budgets are money, never tokens (seams v3 §1.5). Tokens are what the
provider bills on, so `Usage` still carries all four counts, and `cost`
is the one place the two units meet.
"""

import hashlib
import json
from math import ceil

from infra.models import PRICE_OF_FIELD
from schemas.budget import Budget

# Bytes per token for an estimate. An overestimate delays a call by one
# re-anchor; an underestimate spends money nobody reserved, so the estimate
# leans high: 3 bytes per token is under what English and JSON actually
# reach (plan 04, the budget pre-check).
BYTES_PER_TOKEN = 3

# The fields of a body that make up the cached prefix, in the provider's
# order. A change to any of them moves the prefix, so the estimate
# re-anchors (plan 04, "Prefix" is defined).
STABLE_FIELDS = ("system", "tools", "tool_choice", "thinking")


def canonical(value) -> bytes:
    """One byte string per value: sorted keys, no spaces. The request hash
    and every byte count are taken from this, so two bodies that differ only
    in key order are one body."""
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Money


def cost(tokens, prices: dict[str, int]) -> int:
    """What the provider billed, in micro-dollars, rounded up per field.

    Rounding up per field rather than on the total is the fail-closed
    direction: the ledger never records less than the invoice will say
    (Properties P6).
    """
    counts = tokens if isinstance(tokens, dict) else tokens.model_dump()
    return sum(
        ceil(int(counts.get(field, 0)) * prices[price] / 1_000_000)
        for field, price in PRICE_OF_FIELD.items()
    )


def reserve(estimated_input: int, max_tokens: int, prices: dict[str, int]) -> Budget:
    """What one call could cost at worst: the input at the cache write rate
    and every output token the client allowed, so money is reserved at the
    most expensive input rate a call can bill (plan 04, decided there)."""
    return Budget(
        usd_micros=(
            ceil(estimated_input * prices["cache_write"] / 1_000_000)
            + ceil(max_tokens * prices["output"] / 1_000_000)
        )
    )


# ---------------------------------------------------------------------------
# The input estimate


def fallback_estimate(body: dict) -> int:
    """The estimate when `count_tokens` cannot be reached: the whole body's
    canonical bytes over 3 (plan 04, step 3)."""
    return ceil(len(canonical(body)) / BYTES_PER_TOKEN)


def is_prefix(previous: dict, body: dict) -> bool:
    """True when the body only appended to what the previous call sent:
    every stable field equal by canonical JSON, and the previous messages a
    list prefix of these. A changed system prompt, a compacted history, or
    an edited message is not a prefix, and re-anchors."""
    for field in STABLE_FIELDS:
        if canonical(previous.get(field)) != canonical(body.get(field)):
            return False
    old = previous.get("messages") or []
    new = body.get("messages") or []
    return len(old) <= len(new) and all(
        canonical(a) == canonical(b) for a, b in zip(old, new)
    )


def appended_estimate(previous: dict, body: dict, prev_billed_input: int) -> int:
    """The previous call's billed input plus the canonical bytes of the
    messages it did not contain, over 3."""
    old = previous.get("messages") or []
    new = body.get("messages") or []
    appended = sum(len(canonical(message)) for message in new[len(old) :])
    return prev_billed_input + ceil(appended / BYTES_PER_TOKEN)


def estimate_input(
    body: dict, *, prev_body: dict | None, prev_billed_input: int | None
) -> int | None:
    """The estimate, or `None` when the caller must re-anchor with
    `count_tokens`: the first call of a brief, or a body that is not an
    append to the previous one."""
    if prev_body is None or prev_billed_input is None:
        return None
    if not is_prefix(prev_body, body):
        return None
    return appended_estimate(prev_body, body, prev_billed_input)


# ---------------------------------------------------------------------------
# The cache prefix rule


def prefix_elements(body: dict) -> list:
    """The provider's cache order: each tool, then each system block, then
    each message content block."""
    elements: list = list(body.get("tools") or [])
    system = body.get("system")
    if isinstance(system, list):
        elements += list(system)
    elif system is not None:
        elements.append(system)
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            elements += list(content)
        elif content is not None:
            elements.append(content)
    return elements


def _has_breakpoint(element) -> bool:
    return isinstance(element, dict) and "cache_control" in element


def _without_breakpoints(value):
    if isinstance(value, dict):
        return {
            k: _without_breakpoints(v) for k, v in value.items() if k != "cache_control"
        }
    if isinstance(value, list):
        return [_without_breakpoints(v) for v in value]
    return value


def prefix_sha256(body: dict) -> str | None:
    """The hash of the cached prefix: every element up to and including the
    first one carrying `cache_control`, with the `cache_control` keys
    removed so the hash names the content, not the marking. `None` when the
    body carries no breakpoint."""
    elements = prefix_elements(body)
    for index, element in enumerate(elements):
        if _has_breakpoint(element):
            prefix = _without_breakpoints(elements[: index + 1])
            return hashlib.sha256(canonical(prefix)).hexdigest()
    return None


def strip_breakpoints(body: dict) -> dict:
    """The body with every `cache_control` removed. Sent upstream when the
    prefix is changing every call: a stripped request bills 100% where an
    unstable cached one bills 122% (spike 04)."""
    return _without_breakpoints(body)


def unstable(history: list[str], current: str) -> bool:
    """True when the prefix changed on this call and on the one before it.

    One change is legitimate: a roll-up landed, a system prompt changed
    once. Two consecutive changes is the pattern spike 04 measured, so the
    rule needs two, and a history shallower than two never fires.
    """
    return len(history) >= 2 and current != history[-1] and history[-1] != history[-2]


def cache_state_of(usage, stripped: bool) -> str:
    """The `response` row's `cache_state`, read from what the provider
    billed rather than from what was asked for."""
    if stripped:
        return "unstable"
    counts = usage if isinstance(usage, dict) else usage.model_dump()
    if int(counts.get("cache_read_input_tokens", 0)) > 0:
        return "read"
    if int(counts.get("cache_creation_input_tokens", 0)) > 0:
        return "write"
    return "none"
