"""Unit 2's meter: the $10/day paid-inference pool (lane 3, #3215, Decision 7).

Mirrors ``tools/infrastructure_budget.py``'s shape (reserve-then-check in one
Lua ``EVAL`` on a plain, non-Popoto key; a paired idempotent release;
``spend_receipt`` evidence rows for settlement; the window key and its
boundaries disclosed on every decision) rather than reusing its code, because
unit 2 and unit 3 are charter §8's two separate pools and must never share a
code path that could let one refuse the other (No-Gos: no transfer between
units).

**Settlement, two branches, no network call** (Research):
``settle_from_response`` reads ``response.usage.cost`` (OpenRouter's
`usage.cost` field, included in every response since the deprecated
``usage: {include: true}`` request flag) when present
(``metering="exact"``), otherwise estimates from a dated price table
(``metering="estimated"``). A response with no usage at all leaves the
reservation open for the reconcile pass to receipt as ``metering="unknown"``
(charter §8: uncertain metering is not zero cost). This module imports no
HTTP client and names no OpenRouter URL: the ``GET /generation?id=`` lookup
belongs to whichever lane first routes a call through OpenRouter.

**Only ``purpose="rsi"`` counts against the pool.** ``tools/cross_vendor_judge.py``
calls :func:`record_receipt` with ``purpose="sdlc_review"`` for visibility —
ordinary review spend is never gated on RSI's daily pool.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: Namespace for the unit-2 day-window counter and per-reservation hashes.
#: Plain Redis keys, not Popoto models -- same atomicity rationale as unit 3.
WINDOW_KEY_PREFIX = "improve:{project}:budget:unit2:"
RESERVATION_KEY_PREFIX = "improve:{project}:budget:unit2:res:"

#: How long a reservation/window key survives past its day, matching unit 3's
#: 30-day audit horizon.
KEY_EXPIRY_SECONDS = 30 * 86400

REFUSAL_NO_FORECAST = "INVALID_AMOUNT"
REFUSAL_EXHAUSTED = "day_exhausted"

#: Price table retrieved 2026-09-14 from public per-provider pricing pages;
#: USD per million tokens. Used only when a response carries no
#: ``usage.cost`` -- see the module docstring's two-branch settlement.
PRICE_TABLE_RETRIEVED_AT = "2026-09-14"
PRICE_TABLE: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"usd_per_mtoken_in": 3.0, "usd_per_mtoken_out": 15.0},
    "claude-opus-4-1": {"usd_per_mtoken_in": 15.0, "usd_per_mtoken_out": 75.0},
    "gpt-5": {"usd_per_mtoken_in": 5.0, "usd_per_mtoken_out": 15.0},
}
#: Fallback rate for a model absent from the table -- Sonnet-class, the
#: conservative middle of the table rather than the cheapest or priciest row.
_DEFAULT_RATE = {"usd_per_mtoken_in": 3.0, "usd_per_mtoken_out": 15.0}

_LUA_RESERVE = """
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved_cents') or '0')
local settled = tonumber(redis.call('HGET', KEYS[1], 'settled_cents') or '0')
local amount = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if settled + reserved + amount <= cap then
  redis.call('HINCRBY', KEYS[1], 'reserved_cents', amount)
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return 1
end
return 0
"""

_LUA_RELEASE_RESERVED = """
local reserved = tonumber(redis.call('HGET', KEYS[1], 'reserved_cents') or '0')
local rest = reserved - tonumber(ARGV[1])
if rest < 0 then rest = 0 end
redis.call('HSET', KEYS[1], 'reserved_cents', rest)
return rest
"""


def _redis():
    """Private alias, never a raw Popoto client (Verification anti-criterion:
    this module is named alongside ``tools/improvement_control/`` in the
    "no Popoto client" grep). These are plain Redis hashes/strings, not
    Popoto-managed rows."""
    from utils.redis_client import text_redis

    return text_redis()


def current_day(now: datetime, boundary: str) -> tuple[str, datetime, datetime]:
    """Return ``(day_key, day_start, day_end)`` for ``now``. ``boundary`` is
    always ``"UTC"`` today (:class:`ImprovementSettings`'s only declared
    value); accepted as a parameter so a future boundary needs no signature
    change."""
    if boundary != "UTC":
        raise ValueError(f"unknown budget_day_boundary {boundary!r}")
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%d"), start, end


@dataclass
class Reservation:
    reservation_id: str
    project_key: str
    cents: int
    purpose: str
    case_id: str | None
    day_key: str


@dataclass
class Refusal:
    reason: str


def _valid_amount(usd: object) -> int | None:
    """USD to integer cents, or None when unforecastable (mirrors
    ``infrastructure_budget._valid_rate``): None, non-numeric, zero,
    negative, NaN, and infinite are all refused."""
    import math

    if isinstance(usd, bool):
        return None
    if isinstance(usd, (int, float)):
        value = float(usd)
    else:
        return None
    if value <= 0 or math.isnan(value) or math.isinf(value):
        return None
    return round(value * 100)


def reserve(
    project_key: str,
    requested_max_usd: float,
    *,
    purpose: str,
    case_id: str | None = None,
    daily_paid_inference_usd: float | None = None,
    now: datetime | None = None,
) -> Reservation | Refusal:
    """Reserve up to ``requested_max_usd`` against the day open now.

    Only ``purpose="rsi"`` reservations count against the pool in practice
    (the CLI and the research path are the only rsi-purpose callers); any
    purpose is accepted here so a caller's own labeling mistake is visible
    in the receipt rather than silently coerced.
    """
    cents = _valid_amount(requested_max_usd)
    if cents is None:
        return Refusal(REFUSAL_NO_FORECAST)
    if now is None:
        now = datetime.now(UTC)
    if daily_paid_inference_usd is None:
        from config.settings import settings

        daily_paid_inference_usd = settings.improvement.daily_paid_inference_usd
    day_key, _, _ = current_day(now, "UTC")
    cap_cents = round(daily_paid_inference_usd * 100)

    window_key = WINDOW_KEY_PREFIX.format(project=project_key) + day_key
    accepted = _redis().eval(_LUA_RESERVE, 1, window_key, cents, cap_cents, KEY_EXPIRY_SECONDS)
    if not accepted:
        return Refusal(REFUSAL_EXHAUSTED)

    reservation_id = uuid.uuid4().hex
    res_key = RESERVATION_KEY_PREFIX.format(project=project_key) + reservation_id
    _redis().hset(
        res_key,
        mapping={
            "cents": cents,
            "purpose": purpose,
            "case_id": case_id or "",
            "day_key": day_key,
            "state": "reserved",
        },
    )
    _redis().expire(res_key, KEY_EXPIRY_SECONDS)
    return Reservation(reservation_id, project_key, cents, purpose, case_id, day_key)


def _reservation_row(project_key: str, reservation_id: str) -> dict | None:
    key = RESERVATION_KEY_PREFIX.format(project=project_key) + reservation_id
    row = _redis().hgetall(key)
    return row or None


def release(project_key: str, reservation_id: str) -> None:
    """Idempotent: releasing an already-settled or already-released
    reservation is a no-op (the window counter is floored at 0)."""
    row = _reservation_row(project_key, reservation_id)
    if row is None or row.get("state") != "reserved":
        return
    window_key = WINDOW_KEY_PREFIX.format(project=project_key) + row["day_key"]
    _redis().eval(_LUA_RELEASE_RESERVED, 1, window_key, int(row["cents"]))
    res_key = RESERVATION_KEY_PREFIX.format(project=project_key) + reservation_id
    _redis().hset(res_key, "state", "released")


def settle(project_key: str, reservation_id: str, usd: float, *, metering: str) -> None:
    """Move the reservation's cents from reserved to settled and write the
    ``spend_receipt`` evidence row. ``metering`` is one of "exact",
    "estimated", "unknown" -- charter §8's distinction, never coerced."""
    row = _reservation_row(project_key, reservation_id)
    if row is None:
        logger.warning(
            "[paid-inference-meter] settle() on unknown reservation_id=%s", reservation_id
        )
        return
    cents = _valid_amount(usd) or 0
    window_key = WINDOW_KEY_PREFIX.format(project=project_key) + row["day_key"]
    _redis().eval(_LUA_RELEASE_RESERVED, 1, window_key, int(row["cents"]))
    _redis().hincrby(window_key, "settled_cents", cents)
    res_key = RESERVATION_KEY_PREFIX.format(project=project_key) + reservation_id
    _redis().hset(res_key, "state", "settled")

    record_receipt(
        project_key=project_key,
        purpose=row.get("purpose") or "rsi",
        model=None,
        prompt_tokens=None,
        completion_tokens=None,
        metering=metering,
        usd=cents / 100,
        case_id=row.get("case_id") or None,
    )


def _read_cost(response) -> float | None:
    """``response.usage.cost`` (attribute form) then ``response["usage"]["cost"]``
    (mapping form). Returns None on any shape mismatch -- never raises."""
    try:
        return float(response.usage.cost)
    except (AttributeError, TypeError, ValueError):
        pass
    try:
        return float(response["usage"]["cost"])
    except (KeyError, TypeError, ValueError):
        return None


def _read_tokens(response) -> tuple[str | None, int | None, int | None]:
    try:
        usage = response.usage
        model = getattr(response, "model", None)
        return (
            model,
            getattr(usage, "prompt_tokens", None),
            getattr(usage, "completion_tokens", None),
        )
    except AttributeError:
        pass
    try:
        usage = response["usage"]
        return response.get("model"), usage.get("prompt_tokens"), usage.get("completion_tokens")
    except (KeyError, TypeError):
        return None, None, None


def _estimate(model: str | None, prompt_tokens: int | None, completion_tokens: int | None) -> float:
    rate = PRICE_TABLE.get(model or "", _DEFAULT_RATE)
    prompt = prompt_tokens or 0
    completion = completion_tokens or 0
    return (prompt / 1_000_000) * rate["usd_per_mtoken_in"] + (completion / 1_000_000) * rate[
        "usd_per_mtoken_out"
    ]


def settle_from_response(project_key: str, reservation_id: str, response) -> None:
    """Settle exactly, or estimate from tokens, or leave open for the
    reconcile pass. No network call, no HTTP client import (module docstring)."""
    cost = _read_cost(response)
    if cost is not None:
        settle(project_key, reservation_id, cost, metering="exact")
        return
    model, prompt_tokens, completion_tokens = _read_tokens(response)
    if prompt_tokens is None and completion_tokens is None:
        logger.warning(
            "[paid-inference-meter] response for reservation_id=%s carries no usage at all; "
            "leaving open for the reconcile pass",
            reservation_id,
        )
        return
    estimated = _estimate(model, prompt_tokens, completion_tokens)
    settle(project_key, reservation_id, estimated, metering="estimated")


def record_receipt(
    *,
    project_key: str,
    purpose: str,
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    metering: str,
    usd: float | None = None,
    case_id: str | None = None,
) -> None:
    """Write one ``spend_receipt`` evidence row. ``purpose="sdlc_review"``
    receipts (the cross-vendor judge) are record-only -- this function never
    checks or touches the unit-2 pool itself."""
    import json

    from models.improvement_evidence import ImprovementEvidence

    ImprovementEvidence.create(
        project_key=project_key,
        created_at=datetime.now(UTC),
        kind="spend_receipt",
        classification="unknown",
        source_ref=f"unit2-{purpose}-{uuid.uuid4().hex}",
        text=model,
        detail=json.dumps(
            {
                "unit": 2,
                "purpose": purpose,
                "model": model,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "metering": metering,
                "usd": usd,
                "case_id": case_id,
            }
        ),
    )


def status_dict(project_key: str, *, now: datetime | None = None) -> dict:
    """Unit 2's status for ``valor-improve budget``: window boundaries plus
    reserved/settled cents for the day open now."""
    if now is None:
        now = datetime.now(UTC)
    day_key, start, end = current_day(now, "UTC")
    window_key = WINDOW_KEY_PREFIX.format(project=project_key) + day_key
    raw = _redis().hgetall(window_key)
    return {
        "day_key": day_key,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "reserved_usd": int(raw.get("reserved_cents", 0) or 0) / 100,
        "settled_usd": int(raw.get("settled_cents", 0) or 0) / 100,
    }


def sweep_unsettled_reservations(project_key: str, *, now: float | None = None) -> list[str]:
    """Receipt any reservation whose day window has closed unsettled, at
    ``metering="unknown"``. Called by the reconcile pass (best-effort import
    from there, since this module may not yet exist in every deployment)."""
    import time

    if now is None:
        now = time.time()
    # Tech debt fix (#3315 review): `now` was accepted (and passed by
    # `recovery._sweep_unit2`) but ignored, so the reconcile pass's
    # deterministic-clock seam did nothing for this branch.
    today_key, _, _ = current_day(datetime.fromtimestamp(now, UTC), "UTC")
    receipted: list[str] = []
    pattern = RESERVATION_KEY_PREFIX.format(project=project_key) + "*"
    for key in _redis().scan_iter(match=pattern):
        row = _redis().hgetall(key)
        if not row or row.get("state") != "reserved":
            continue
        if row.get("day_key") == today_key:
            continue  # today's window is still open
        reservation_id = key.rsplit(":", 1)[-1]
        settle(project_key, reservation_id, int(row["cents"]) / 100, metering="unknown")
        receipted.append(reservation_id)
    return receipted
