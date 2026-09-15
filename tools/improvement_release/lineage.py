"""Release lineage for the dashboard and ``show`` (#3218, lane 6).

:func:`release_lineage` joins each release to its evaluation, experiment, and
case, newest first, and returns the promotion gate beside them. Every read
of an evaluation's ``effect`` or ``confidence_interval`` goes through
``evaluation_read`` for the protocol's primary endpoint. A read failure marks
the result ``unavailable`` (the ``get_goals`` pattern) and still returns the
gate, so the partial can say "automated promotion: disabled" even when the
release table cannot be read.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from tools.improvement_release.evaluation_read import effect_of, interval_of, json_field
from tools.improvement_release.rows import aware, lookup, recency

logger = logging.getLogger(__name__)


def primary_endpoint_of(protocol: dict | None) -> str | None:
    """The protocol's ``primary_endpoint``, or its first ``endpoints`` entry."""
    if not isinstance(protocol, dict):
        return None
    primary = protocol.get("primary_endpoint")
    if isinstance(primary, str) and primary:
        return primary
    endpoints = protocol.get("endpoints")
    if isinstance(endpoints, list) and endpoints and isinstance(endpoints[0], str):
        return endpoints[0]
    return None


def load_primary_endpoint(experiment: Any) -> str | None:
    """``primary_endpoint`` from the experiment's frozen protocol; ``None`` when unreadable."""
    if experiment is None:
        return None
    from tools.improvement_eval.runner import load_protocol

    try:
        return primary_endpoint_of(load_protocol(experiment))
    except Exception as exc:  # noqa: BLE001 -- a lineage row with no endpoint, never a crash
        logger.warning(
            "release lineage: protocol read failed for experiment %s: %s",
            getattr(experiment, "id", None),
            exc,
        )
        return None


def _window(release: Any, now: datetime) -> dict:
    exposed_at = aware(getattr(release, "exposed_at", None))
    ends_at = aware(getattr(release, "observation_window_ends_at", None))
    days_remaining = None
    if ends_at is not None:
        days_remaining = round(max((ends_at - now).total_seconds(), 0.0) / 86400.0, 2)
    return {"exposed_at": exposed_at, "ends_at": ends_at, "days_remaining": days_remaining}


def _release_row(release: Any, project_key: str, now: datetime) -> dict:
    from models.improvement_case import ImprovementCase
    from models.improvement_evaluation import ImprovementEvaluation
    from models.improvement_experiment import ImprovementExperiment

    evaluation = lookup(ImprovementEvaluation, project_key, getattr(release, "evaluation_id", None))
    experiment = lookup(
        ImprovementExperiment, project_key, getattr(evaluation, "experiment_id", None)
    )
    case_id = getattr(release, "case_id", None) or getattr(experiment, "case_id", None)
    case = lookup(ImprovementCase, project_key, case_id)
    endpoint = load_primary_endpoint(experiment)
    drill = json_field(getattr(release, "rollback_drill", None)) or {}
    outcome = json_field(getattr(release, "outcome", None)) or {}
    return {
        "id": str(release.id),
        "state": getattr(release, "state", None),
        "kind": getattr(release, "kind", None),
        "surfaces": json_field(getattr(release, "surfaces", None)) or [],
        "candidate_ref": getattr(release, "candidate_ref", None),
        "created_at": aware(getattr(release, "created_at", None)),
        "evaluation": {
            "id": str(evaluation.id) if evaluation is not None else None,
            "verdict": getattr(evaluation, "verdict", None),
            "primary_endpoint": endpoint,
            "effect": effect_of(evaluation, endpoint) if endpoint else None,
            "confidence_interval": interval_of(evaluation, endpoint) if endpoint else None,
        },
        "experiment": {
            "id": str(experiment.id) if experiment is not None else None,
            "hypothesis": getattr(experiment, "hypothesis", None),
            "contract_digest": getattr(experiment, "contract_digest", None),
        },
        "case": {
            "id": str(case.id) if case is not None else None,
            "title": getattr(case, "title", None),
            "priority_area": getattr(case, "priority_area", None),
        },
        "drill": {"result": drill.get("result"), "drilled_at": drill.get("drilled_at")},
        "window": _window(release, now),
        "outcome": {
            "verdict": outcome.get("verdict"),
            "reason": outcome.get("reason"),
            "claim_level_2_supported": outcome.get("claim_level_2_supported"),
            "rollback_recommended": outcome.get("rollback_recommended"),
        },
    }


def release_lineage(project_key: str = "valor", *, now: datetime | None = None) -> dict:
    """Every release for ``project_key`` joined to its lineage, newest first.

    Returns ``releases``, ``promotion_gate`` (``{automated, unmet}``),
    ``unavailable`` (a read raised; logged), and ``no_releases_yet``.
    """
    from tools.improvement_release.promotion import promotion_gate

    now = aware(now) or datetime.now(UTC)
    releases: list[dict] = []
    unavailable = False
    try:
        from models.improvement_release import ImprovementRelease

        rows = list(ImprovementRelease.query.filter(project_key=project_key))
        rows.sort(key=recency, reverse=True)
        releases = [_release_row(row, project_key, now) for row in rows]
    except Exception as exc:  # noqa: BLE001 -- the dashboard pattern: unavailable, never a crash
        logger.warning("release lineage: read failed for %s: %s", project_key, exc)
        unavailable = True
        releases = []

    try:
        gate = promotion_gate(project_key)
        gate_payload = {"automated": gate.automated, "unmet": list(gate.unmet)}
    except Exception as exc:  # noqa: BLE001 -- the gate answer degrades to "unknown", never to "enabled"
        logger.warning("release lineage: promotion gate read failed for %s: %s", project_key, exc)
        gate_payload = {"automated": False, "unmet": ["gate_unavailable"]}

    return {
        "project_key": project_key,
        "releases": releases,
        "promotion_gate": gate_payload,
        "unavailable": unavailable,
        "no_releases_yet": not releases and not unavailable,
    }


__all__ = ["load_primary_endpoint", "primary_endpoint_of", "release_lineage"]
