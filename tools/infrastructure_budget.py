"""Unit 3's meter and admission gate (lane 7, #3274).

Charter section 8's second spending category — $50 per ISO week for RSI
infrastructure — exists as a number in ``ImprovementSettings`` and nothing
else. This module is the reader that number was missing: it computes the
window from ``budget_week_start`` and ``budget_day_boundary``, forecasts a
recurring charge for the remainder of the current window and the next,
refuses what it cannot forecast, and settles from a billing API or a
``spend_receipt`` row.

Three rules, enforced by tests rather than comments:

- **Refusal.** A resource whose charge cannot be forecast is refused, never
  defaulted to zero. ``None``, empty, non-numeric, negative, NaN, and infinite
  rates are each refused with the reason ``no forecastable rate``.
- **No transfer.** This module imports ``weekly_infrastructure_usd``,
  ``budget_week_start``, and ``budget_day_boundary``, and never
  ``daily_paid_inference_usd``. There is no code path between the units, so
  charter section 8's no-transfer rule holds by construction.
- **Uncertain metering is not zero cost.** Missing or uncertain metering
  settles at the forecast, never at zero, with a ``logger.warning`` that says
  so. The trial's live-window stop reads ``max(settled, forecast)`` for the
  same reason: a per-second metered provider settles after the fact, and a
  stop keyed on settled spend alone fires after the money is gone.

The window counter is a plain Redis string key,
``improvement:budget:unit3:{window_key}``. It is deliberately not a Popoto
model: Popoto offers no compare-and-set, so a counter modeled in the ORM
could not be reserved atomically at all, and a non-Popoto key sits outside
the "never use raw Redis on Popoto-managed keys" rule. Reservation is one Lua
``EVAL`` (reserve-then-check in a single atomic step); release is the
compensating ``EVAL`` floored at 0, idempotent by reservation id through the
``InfrastructureReservation`` row. The window key carries an ``EXPIRE`` set
past the audit horizon, never to the window length: a counter expiring
mid-window resets headroom to full, which is the same leak wearing the
opposite sign. When lane 3 (#3215) lands, this counter and its scripts migrate
into its control namespace as a named migration.

The teardown ladder (Technical Approach section 4 of the lane plan) lives here
too: admission closes on exhaustion, ``standing`` resources are torn down,
``trial`` resources continue to a bounded horizon with the overrun booked
forward, and every teardown is gated on a verified evidence export that fails
closed onto a named escalation record.
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: Namespace for the unit-3 window counter. Plain string key, not a Popoto
#: model — see the module docstring for why atomicity requires that.
WINDOW_KEY_PREFIX = "improvement:budget:unit3:"

#: Seconds in a 7-day window. Forecasts prorate against this.
WEEK_SECONDS = 7 * 24 * 3600

#: How long past a window's end its counter survives: the 30-day evidence TTL
#: (the audit horizon for settlements) plus one full window of margin.
WINDOW_KEY_EXPIRY_SECONDS = 30 * 86400 + WEEK_SECONDS

#: Refusal reasons. "no forecastable rate" and "week exhausted" are different
#: states and must not both render as "not acquired".
REFUSAL_NO_FORECAST = "no forecastable rate"
REFUSAL_EXHAUSTED = "week exhausted"

_LUA_RESERVE = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local cap = tonumber(ARGV[2])
if current + amount <= cap then
  redis.call('INCRBYFLOAT', KEYS[1], ARGV[1])
  redis.call('EXPIRE', KEYS[1], ARGV[3])
  return 1
end
return 0
"""

_LUA_RELEASE = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local rest = current - tonumber(ARGV[1])
if rest < 0 then rest = 0 end
redis.call('SET', KEYS[1], rest)
redis.call('EXPIRE', KEYS[1], ARGV[2])
return rest
"""


def _redis():
    """The shared Redis handle, bound lazily so tests see the test DB.

    Imported inside the function (the ``worker/__main__.py`` precedent):
    ``REDIS_URL`` is exported process-wide by the test harness before test
    modules import, and a top-level import would bind whichever URL happened
    to be current at first import instead.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def current_window(now: datetime, week_start: str) -> tuple[str, datetime, datetime]:
    """Return ``(window_key, window_start, window_end)`` for ``now``.

    Weeks start Monday 00:00 UTC by default, Sunday 00:00 UTC when
    ``budget_week_start`` is ``"sunday"``. The key derives from the window
    start so the two week conventions never share a counter.
    """
    if week_start not in ("monday", "sunday"):
        raise ValueError(f"unknown budget_week_start {week_start!r}")
    base = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    # Monday is 0; Sunday-start weeks begin one day earlier on the dial.
    shift = (base.weekday() + (1 if week_start == "sunday" else 0)) % 7
    start = base - timedelta(days=shift)
    end = start + timedelta(days=7)
    suffix = "" if week_start == "monday" else "-sun"
    return f"{start.isocalendar().year}-W{start.isocalendar().week:02d}{suffix}", start, end


def _valid_rate(value: object) -> float | None:
    """Return the rate as a float, or None when it cannot be forecast.

    ``None``, empty strings, non-numeric types, bools, negatives, NaN, and
    infinities are all unforecastable. Each is refused rather than defaulted.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        rate = float(value)
    elif isinstance(value, str):
        if not value.strip():
            return None
        try:
            rate = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if rate < 0 or math.isnan(rate) or math.isinf(rate):
        return None
    return rate


@dataclass
class ResourceDecl:
    """A resource offered for admission.

    ``weekly_rate_usd`` is the recurring charge for a full week at
    ``duty_cycle`` 1.0. A credit covers spend until ``credit_expires_at``;
    after that ``credit_paid_rate_usd`` applies. A credit with no expiry
    covers only the current window — an indefinite free tier is not a thing
    this meter believes in.
    """

    name: str
    weekly_rate_usd: object
    duty_cycle: float = 1.0
    credit_expires_at: datetime | None = None
    credit_paid_rate_usd: object = None


@dataclass
class Admission:
    """One admission decision, with both boundaries disclosed."""

    admitted: bool
    reason: str
    forecast_usd: float
    reservation_id: str | None = None
    window_key: str = ""
    window_start: str = ""
    window_end: str = ""
    budget_week_start: str = "monday"
    budget_day_boundary: str = "UTC"
    reserved_usd: float = 0.0
    headroom_usd: float = 0.0


def _horizon_seconds(now: datetime, window_end: datetime) -> float:
    """Seconds from ``now`` to the end of the *next* window."""
    return (window_end - now).total_seconds() + WEEK_SECONDS


def forecast_usd(
    resource: ResourceDecl,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
) -> float | None:
    """Forecast the resource's charge over the admission horizon.

    The horizon is the remainder of the current window plus the whole next
    window. Returns None when the charge cannot be forecast.
    """
    rate = _valid_rate(resource.weekly_rate_usd)
    if rate is None:
        return None
    if not (0.0 < resource.duty_cycle <= 1.0):
        return None
    horizon = max(_horizon_seconds(now, window_end), 0.0)
    paid_rate = rate
    free_until = None
    if resource.credit_expires_at is not None:
        paid_after = _valid_rate(resource.credit_paid_rate_usd)
        if paid_after is None:
            return None
        paid_rate = paid_after
        expiry = resource.credit_expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        free_until = max((expiry - now).total_seconds(), 0.0)
    elif resource.credit_paid_rate_usd is not None:
        # A paid rate with no expiry covers only the current window: a credit
        # with no expiry is treated as expiring at the end of the current
        # window rather than as indefinite.
        paid_after = _valid_rate(resource.credit_paid_rate_usd)
        if paid_after is None:
            return None
        paid_rate = paid_after
        free_until = max((window_end - now).total_seconds(), 0.0)
    if free_until is None:
        return rate * resource.duty_cycle * horizon / WEEK_SECONDS
    free_seconds = min(free_until, horizon)
    paid_seconds = horizon - free_seconds
    return paid_rate * resource.duty_cycle * paid_seconds / WEEK_SECONDS


def _settings_values(settings=None) -> tuple[float, str, str]:
    """Read the three unit-3 settings this module may see. Nothing else."""
    if settings is None:
        from config.settings import settings as app_settings

        settings = app_settings
    improvement = settings.improvement
    return (
        float(improvement.weekly_infrastructure_usd),
        str(improvement.budget_week_start),
        str(improvement.budget_day_boundary),
    )


def window_reserved_usd(window_key: str) -> float:
    """Dollars currently reserved against a window counter."""
    raw = _redis().get(WINDOW_KEY_PREFIX + window_key)
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning("unit-3 window counter %r is not numeric; reading as 0", window_key)
        return 0.0


def admit(
    resource: ResourceDecl,
    *,
    project_key: str = "default",
    settings=None,
    now: datetime | None = None,
) -> Admission:
    """Admit or refuse a resource against unit 3.

    Refusals (unforecastable charge, exhausted week) are recorded as
    ``InfrastructureReservation`` rows with state ``refused`` so the report
    can tell "no forecastable rate" apart from "week exhausted". Every
    failure exit releases nothing because nothing was reserved yet; the
    reservation, once made, is released only through :func:`release`.
    """
    from models.improvement_infrastructure_ledger import InfrastructureReservation

    if not project_key or not project_key.strip():
        raise ValueError("project_key must be a non-empty string")
    cap, week_start, day_boundary = _settings_values(settings)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    window_key, window_start, window_end = current_window(moment, week_start)
    base = dict(
        window_key=window_key,
        window_start=window_start.isoformat(),
        window_end=window_end.isoformat(),
        budget_week_start=week_start,
        budget_day_boundary=day_boundary,
    )
    forecast = forecast_usd(resource, moment, window_start, window_end)
    reservation_id = uuid.uuid4().hex
    if forecast is None:
        InfrastructureReservation.record_decision(
            project_key,
            window_key=window_key,
            resource=resource.name,
            amount_usd=0.0,
            forecast_usd=0.0,
            state="refused",
            reservation_id=reservation_id,
            reason=REFUSAL_NO_FORECAST,
            budget_week_start=week_start,
            budget_day_boundary=day_boundary,
        )
        return Admission(
            admitted=False,
            reason=REFUSAL_NO_FORECAST,
            forecast_usd=0.0,
            reservation_id=reservation_id,
            **base,
        )
    admitted = bool(
        _redis().eval(
            _LUA_RESERVE,
            1,
            WINDOW_KEY_PREFIX + window_key,
            repr(forecast),
            repr(cap),
            WINDOW_KEY_EXPIRY_SECONDS,
        )
    )
    headroom = cap - window_reserved_usd(window_key)
    if not admitted:
        InfrastructureReservation.record_decision(
            project_key,
            window_key=window_key,
            resource=resource.name,
            amount_usd=0.0,
            forecast_usd=forecast,
            state="refused",
            reservation_id=reservation_id,
            reason=REFUSAL_EXHAUSTED,
            budget_week_start=week_start,
            budget_day_boundary=day_boundary,
        )
        return Admission(
            admitted=False,
            reason=REFUSAL_EXHAUSTED,
            forecast_usd=forecast,
            reservation_id=reservation_id,
            headroom_usd=headroom,
            **base,
        )
    credit_expiry = resource.credit_expires_at
    if credit_expiry is not None and credit_expiry.tzinfo is None:
        credit_expiry = credit_expiry.replace(tzinfo=UTC)
    credit_paid = _valid_rate(resource.credit_paid_rate_usd)
    InfrastructureReservation.record_decision(
        project_key,
        window_key=window_key,
        resource=resource.name,
        amount_usd=forecast,
        forecast_usd=forecast,
        state="reserved",
        reservation_id=reservation_id,
        reason="admitted",
        credit_expires_at=credit_expiry,
        credit_paid_rate_usd=credit_paid,
        budget_week_start=week_start,
        budget_day_boundary=day_boundary,
    )
    return Admission(
        admitted=True,
        reason="admitted",
        forecast_usd=forecast,
        reservation_id=reservation_id,
        reserved_usd=forecast,
        headroom_usd=headroom,
        **base,
    )


def release(reservation_id: str, *, project_key: str = "default") -> bool:
    """Return a reservation's headroom exactly once.

    Releases run from ``except`` and ``finally`` on the acquisition path —
    as ``finally: if not acquired: release(reservation_id)``, never as a bare
    ``finally``, which would return headroom for money actually spent. The
    release is idempotent by reservation id: once the ledger row shows
    ``released`` (or ``settled``), replaying is a no-op. The compensating
    ``EVAL`` floors the counter at 0 inside the script so a double release
    cannot raise headroom, which is the expensive direction.
    """
    from models.improvement_infrastructure_ledger import InfrastructureReservation

    rows = list(
        InfrastructureReservation.query.filter(
            project_key=project_key, reservation_id=reservation_id
        )
    )
    row = next((r for r in rows if r.reservation_id == reservation_id), None)
    if row is None:
        logger.warning("unit-3 release: unknown reservation %r", reservation_id)
        return False
    if row.state in ("released", "settled"):
        return True
    if row.state != "reserved":
        logger.warning(
            "unit-3 release: reservation %r in state %r; not releasing",
            reservation_id,
            row.state,
        )
        return False
    _redis().eval(
        _LUA_RELEASE,
        1,
        WINDOW_KEY_PREFIX + row.window_key,
        repr(float(row.amount_usd or 0.0)),
        WINDOW_KEY_EXPIRY_SECONDS,
    )
    row.state = "released"
    row.reason = (row.reason or "") + "; released"
    row.save()
    return True


def settle(
    reservation_id: str,
    billing_reader=None,
    *,
    project_key: str = "default",
) -> float:
    """Settle a reservation: actual spend from the billing API or the forecast.

    ``billing_reader`` is a zero-argument callable returning dollars spent, or
    None when the provider reports nothing. When it raises, returns None, or
    is absent entirely, settlement equals the forecast — missing metering is
    not zero cost — and a ``logger.warning`` says so. The settlement is
    recorded as a ``spend_receipt`` evidence row keyed by the reservation, so
    the dashboard and the report read the same figure.
    """
    from models.improvement_evidence import ImprovementEvidence
    from models.improvement_infrastructure_ledger import InfrastructureReservation

    rows = list(
        InfrastructureReservation.query.filter(
            project_key=project_key, reservation_id=reservation_id
        )
    )
    row = next((r for r in rows if r.reservation_id == reservation_id), None)
    if row is None:
        raise ValueError(f"unknown reservation {reservation_id!r}")
    if row.state == "settled":
        return float(row.settled_usd or 0.0)
    if row.state != "reserved":
        raise ValueError(f"reservation {reservation_id!r} is {row.state}, not reserved")
    forecast = float(row.forecast_usd or 0.0)
    settled = None
    if billing_reader is not None:
        try:
            settled = billing_reader()
        except Exception as exc:  # noqa: BLE001 — degraded to forecast below
            logger.warning(
                "unit-3 settlement: billing reader failed for %r (%s); settling at forecast %.2f",
                row.resource,
                exc,
                forecast,
            )
            settled = None
    if settled is None:
        if billing_reader is not None:
            logger.warning(
                "unit-3 settlement: no metering for %r; settling at forecast %.2f",
                row.resource,
                forecast,
            )
        settled = forecast
    try:
        settled = float(settled)
    except (TypeError, ValueError):
        logger.warning(
            "unit-3 settlement: billing reader returned non-numeric %r for %r; "
            "settling at forecast %.2f",
            settled,
            row.resource,
            forecast,
        )
        settled = forecast
    if settled < 0 or math.isnan(settled) or math.isinf(settled):
        logger.warning(
            "unit-3 settlement: billing reader returned %r for %r; settling at forecast %.2f",
            settled,
            row.resource,
            forecast,
        )
        settled = forecast
    row.state = "settled"
    row.settled_usd = settled
    row.save()
    import json as _json

    ImprovementEvidence.record_once(
        project_key,
        "spend_receipt",
        source_ref=f"unit3-settlement-{reservation_id}",
        text=f"unit-3 settlement: {row.resource} settled ${settled:.2f} "
        f"(forecast ${forecast:.2f}) in window {row.window_key}",
        detail=_json.dumps(
            {
                "reservation_id": reservation_id,
                "resource": row.resource,
                "window_key": row.window_key,
                "settled_usd": settled,
                "forecast_usd": forecast,
                "budget_week_start": row.budget_week_start,
                "budget_day_boundary": row.budget_day_boundary,
            }
        ),
    )
    return settled


def stop_now(settled_usd: float, forecast_to_date_usd: float, cap_usd: float) -> bool:
    """The trial's live-window stop: ``max(settled, forecast) >= cap``.

    Evaluated on the caller's own watchdog tick rather than on settlement
    arrival. With a per-second metered provider settlement lags, so a stop
    keyed on settled spend alone fires after the money is gone.
    """
    live = max(settled_usd, forecast_to_date_usd)
    return live >= cap_usd


# --- Teardown policy --------------------------------------------------------


@dataclass
class TeardownOutcome:
    """What the teardown ladder did with one resource."""

    resource: str
    classification: str  # "trial" or "standing"
    action: str  # "torn_down", "continues", or "kept_running"
    escalated: bool = False
    detail: str = ""


def classify_resource(resource: dict, open_sessions: list[dict]) -> str:
    """Classify a resource as ``trial`` or ``standing``.

    A resource with any claimed-and-unfinished session is ``trial`` regardless
    of its declared attachment: an in-flight write blocks teardown twice over
    (here, and again at the export-verification guard). Uncertainty resolves
    toward leaving it running, which costs money; the alternative costs
    evidence.
    """
    name = resource.get("name", "")
    for session in open_sessions:
        if session.get("resource", "") == name and not session.get("done", False):
            return "trial"
    if resource.get("trial_ref"):
        return "trial"
    return "standing"


def _escalate_overrun(
    *,
    project_key: str,
    resource: str,
    window_key: str,
    forecast_usd: float,
    attempt: str,
    failure_mode: str,
    budget_week_start: str = "monday",
    budget_day_boundary: str = "UTC",
) -> None:
    """Book a continued charge as a forecast overrun. The record, not a verb.

    The surface of record is a ``spend_receipt`` row carrying the resource id,
    the teardown attempt, the verifier's failure mode, and the forecast
    amount. No third evidence kind is introduced. Where lane 5's
    ``improvement-assumption-digest`` reflection exists, ``on_escalation``
    carries the same payload as an optional second sink.
    """
    import json as _json

    from models.improvement_evidence import ImprovementEvidence

    ImprovementEvidence.record_once(
        project_key,
        "spend_receipt",
        source_ref=f"unit3-overrun-{window_key}-{resource}-{attempt}",
        text=f"unit-3 overrun: {resource} keeps running past {attempt}; "
        f"forecast ${forecast_usd:.2f} booked against window {window_key}",
        detail=_json.dumps(
            {
                "resource": resource,
                "window_key": window_key,
                "teardown_attempt": attempt,
                "verifier_failure_mode": failure_mode,
                "forecast_usd": forecast_usd,
                "budget_week_start": budget_week_start,
                "budget_day_boundary": budget_day_boundary,
            }
        ),
    )


def apply_teardown(
    resources: list[dict],
    open_sessions: list[dict],
    *,
    export_verifier,
    destroy,
    continuation_forecast_usd: float = 0.0,
    next_window_key: str = "",
    project_key: str = "default",
    on_escalation=None,
) -> list[TeardownOutcome]:
    """Run the teardown ladder over every resource.

    - ``export_verifier(resource)`` must return True before anything is torn
      down. A verifier that raises or returns False fails closed: the
      resource keeps running and the escalation record is written.
    - ``destroy(resource)`` performs the teardown once export is verified. A
      resource ``destroy`` cannot confirm is treated as still running and
      still charging.
    - ``open_sessions`` are ``{"resource": name, "done": bool}`` dicts.
    - ``continuation_forecast_usd`` books a continuing trial's charge as a
      forecast overrun against ``next_window_key`` before it accrues.
    """
    outcomes: list[TeardownOutcome] = []
    for resource in resources:
        name = resource.get("name", "")
        classification = classify_resource(resource, open_sessions)
        if classification == "trial" and not resource.get("trial_ended", False):
            if next_window_key:
                _escalate_overrun(
                    project_key=project_key,
                    resource=name,
                    window_key=next_window_key,
                    forecast_usd=continuation_forecast_usd,
                    attempt="window_rollover",
                    failure_mode="continuation_booked",
                )
            if on_escalation is not None:
                on_escalation(
                    {
                        "resource": name,
                        "action": "continues",
                        "window_key": next_window_key,
                        "forecast_usd": continuation_forecast_usd,
                    }
                )
            outcomes.append(
                TeardownOutcome(
                    resource=name,
                    classification="trial",
                    action="continues",
                    detail="trial continues to its bounded horizon; overrun booked forward",
                )
            )
            continue
        try:
            verified = export_verifier(resource)
        except Exception as exc:  # noqa: BLE001 — fail closed, record, continue
            failure_mode = f"raised:{type(exc).__name__}"
            verified = False
        else:
            failure_mode = "returned_False" if not verified else ""
        if not verified:
            _escalate_overrun(
                project_key=project_key,
                resource=name,
                window_key=next_window_key or resource.get("window_key", ""),
                forecast_usd=continuation_forecast_usd,
                attempt="teardown",
                failure_mode=failure_mode,
            )
            if on_escalation is not None:
                on_escalation(
                    {
                        "resource": name,
                        "action": "kept_running",
                        "failure_mode": failure_mode,
                        "forecast_usd": continuation_forecast_usd,
                    }
                )
            outcomes.append(
                TeardownOutcome(
                    resource=name,
                    classification=classification,
                    action="kept_running",
                    escalated=True,
                    detail=f"export not verified ({failure_mode}); "
                    "resource left running and escalated",
                )
            )
            continue
        try:
            confirmed = destroy(resource)
        except Exception as exc:  # noqa: BLE001 — unconfirmed means running
            logger.warning("unit-3 teardown: destroy(%r) raised %s", name, exc)
            confirmed = False
        if not confirmed:
            _escalate_overrun(
                project_key=project_key,
                resource=name,
                window_key=next_window_key or resource.get("window_key", ""),
                forecast_usd=continuation_forecast_usd,
                attempt="teardown",
                failure_mode="unconfirmed_destroy",
            )
            outcomes.append(
                TeardownOutcome(
                    resource=name,
                    classification=classification,
                    action="kept_running",
                    escalated=True,
                    detail="teardown unconfirmed; treated as still running and still charging",
                )
            )
            continue
        outcomes.append(
            TeardownOutcome(
                resource=name,
                classification=classification,
                action="torn_down",
                detail="export verified, teardown confirmed",
            )
        )
    return outcomes


# --- CLI --------------------------------------------------------------------


def status_dict(*, project_key: str = "default", settings=None, now=None) -> dict:
    """Snapshot the current window: cap, reserved, headroom, boundaries."""
    cap, week_start, day_boundary = _settings_values(settings)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    window_key, window_start, window_end = current_window(moment, week_start)
    reserved = window_reserved_usd(window_key)
    return {
        "window_key": window_key,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "budget_week_start": week_start,
        "budget_day_boundary": day_boundary,
        "weekly_infrastructure_usd": cap,
        "reserved_usd": reserved,
        "headroom_usd": cap - reserved,
    }


def main(argv: list[str] | None = None) -> int:
    """Entry point the agent uses: ``python -m tools.infrastructure_budget``.

    Admission itself is a library call (the controller ticks in-process); the
    CLI reports status. No standalone console script is added — when lane 3's
    ``valor-improve`` lands, ``budget`` becomes a subcommand there.
    """
    parser = argparse.ArgumentParser(
        prog="infrastructure_budget",
        description=(
            "Charter section 8 unit-3 meter: window, cap, reserved headroom, "
            "and the disclosed boundaries. Refuses nothing and spends nothing; "
            "admission runs in-process through admit()."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="status",
        choices=("status",),
        help="status: print the current window and its headroom as JSON",
    )
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "status":
        import json as _json

        print(_json.dumps(status_dict(), indent=2))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
