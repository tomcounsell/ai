"""Alert check for the schema-first PM turn routing fallback rate.

Plan #2000 Task 2.3 ("Schema routing"): the session runner
(``agent/session_runner/runner.py::SessionRunner._classify_turn``) prefers
the claude harness's ``--json-schema``-validated ``structured_output`` for
every top-level PM/teammate turn, falling back to the prefix-regex parser
only when that structured output is absent or invalid. A healthy schema
path has a fallback rate near 0%; a sustained non-trivial rate means the
schema contract has silently regressed (e.g. a claude CLI upgrade changing
``--json-schema`` semantics) and routing has degraded to the regex fallback.

Two analytics counters (``analytics/collector.py::record_metric``) feed this
check, both recorded by ``_classify_turn`` on every classified turn:

- :data:`agent.session_runner.router.SCHEMA_ROUTING_TURN_METRIC` — one per
  classified top-level turn (denominator).
- :data:`agent.session_runner.router.SCHEMA_ROUTING_FALLBACK_METRIC` — one
  per turn that fell back to the regex classifier (numerator).

:func:`check_schema_routing_fallback_rate` follows the same
``monitoring/alerts.py`` :class:`~monitoring.alerts.Alert` shape as
:meth:`monitoring.resource_monitor.ResourceMonitor.check_thresholds` and is
wired into :meth:`monitoring.alerts.AlertManager.check_all` alongside it.
"""

from __future__ import annotations

from monitoring.alerts import Alert, AlertLevel

# Rolling window (hours) and fallback-rate threshold (plan #2000 Task 2.3
# "Schema routing" Key Element — "a healthy schema path is ~0%"; a sustained
# breach above this threshold means the schema contract has silently
# regressed and routing has degraded to the prefix-regex fallback).
FALLBACK_RATE_WINDOW_HOURS: float = 1.0
FALLBACK_RATE_THRESHOLD: float = 0.05


def check_schema_routing_fallback_rate(
    window_hours: float = FALLBACK_RATE_WINDOW_HOURS,
    threshold: float = FALLBACK_RATE_THRESHOLD,
) -> Alert | None:
    """Return a WARNING :class:`Alert` iff the fallback rate over the
    trailing ``window_hours`` exceeds ``threshold``.

    Returns ``None`` when there is no turn volume in the window (nothing to
    alarm on — a cold-started worker or an idle period is not a schema
    regression) or the rate is within bounds. Never raises: an analytics
    query failure resolves to "no alert" (fail-quiet, matching
    ``analytics/query.py``'s own contract) rather than a spurious page.
    """
    try:
        from agent.session_runner.router import (
            SCHEMA_ROUTING_FALLBACK_METRIC,
            SCHEMA_ROUTING_TURN_METRIC,
        )
        from analytics.query import query_metric_count

        days = window_hours / 24.0
        turns = query_metric_count(SCHEMA_ROUTING_TURN_METRIC, days=days)
        if turns <= 0:
            return None
        fallbacks = query_metric_count(SCHEMA_ROUTING_FALLBACK_METRIC, days=days)
        rate = fallbacks / turns
        if rate <= threshold:
            return None
        return Alert(
            level=AlertLevel.WARNING,
            message=(
                f"Schema routing fallback rate {rate:.1%} over the last "
                f"{window_hours:g}h ({fallbacks}/{turns} turns) exceeds the "
                f"{threshold:.0%} threshold — the PM turn schema contract may "
                "have regressed"
            ),
            metric=SCHEMA_ROUTING_FALLBACK_METRIC,
            current_value=rate,
            threshold=threshold,
            recommendations=[
                "Check for a recent claude CLI version change "
                "(--json-schema / StructuredOutput tool semantics)",
                "Inspect recent PM turns' terminal result events for a missing "
                "structured_output key",
            ],
        )
    except Exception:  # noqa: BLE001 — alerting must never raise
        return None
