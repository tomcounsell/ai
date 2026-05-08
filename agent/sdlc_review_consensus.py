"""Pure-Python consensus rules for the multi-judge Review gate.

This module is consumed by ``.claude/skills-global/do-pr-review/SKILL.md``
when ``SDLC_REVIEW_JUDGES`` enables ≥2 judges. The parent skill collects per-judge
dicts in memory, calls :func:`compute_consensus`, then makes a single
``record_verdict(... judges=[...], consensus=meta)`` call.

Pure function. No I/O. Fully unit-testable.

Reference: docs/plans/multi-judge-consensus-gates.md (rev1).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# Required keys on every per-judge dict.
_REQUIRED_KEYS = ("judge_id", "verdict", "blockers")

_VALID_RULES = frozenset(["any-blocker-wins", "unanimous-approved"])


def _validate_judge(j: dict[str, Any]) -> None:
    if not isinstance(j, dict):
        raise ValueError(f"judge entry must be a dict, got {type(j).__name__}")
    for key in _REQUIRED_KEYS:
        if key not in j:
            raise ValueError(f"judge entry missing required key {key!r}: {j!r}")
    if not isinstance(j["judge_id"], str) or not j["judge_id"].strip():
        raise ValueError(f"judge_id must be a non-empty string: {j!r}")
    if not isinstance(j["verdict"], str):
        raise ValueError(f"verdict must be a string: {j!r}")
    if not isinstance(j["blockers"], int):
        raise ValueError(f"blockers must be an int: {j!r}")


def _dedup_last_wins(judges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the LAST entry per judge_id (mirrors single-writer overwrite)."""
    by_id: dict[str, dict[str, Any]] = {}
    for j in judges:
        by_id[j["judge_id"]] = j
    # Deterministic sort by judge_id for test stability.
    return [by_id[k] for k in sorted(by_id.keys())]


def _empty_conservative_outcome(rule: str) -> dict[str, Any]:
    """Return a conservative CHANGES REQUESTED outcome for empty input.

    Matches the Failure Path strategy: the parent should never silently
    approve when zero judges returned data.
    """
    return {
        "verdict": "CHANGES REQUESTED",
        "blockers": 1,
        "tech_debt": 0,
        "consensus": {
            "rule": rule,
            "k": 0,
            "n": 0,
            "mean_confidence": 0.0,
            "blocker_aggregation": "max",
            "tied": False,
            "decided_at": datetime.now(UTC).isoformat(),
        },
    }


def compute_consensus(
    judges: list[dict[str, Any]],
    rule: str = "any-blocker-wins",
) -> dict[str, Any]:
    """Aggregate per-judge dicts into a single scalar verdict + consensus meta.

    Args:
        judges: list of per-judge dicts. Each dict must contain ``judge_id``
            (str), ``verdict`` (str), ``blockers`` (int). Optional keys:
            ``tech_debt`` (int), ``confidence`` (float in [0,1]),
            ``reasoning_summary`` (str), ``review_url`` (str).
        rule: consensus rule. Either ``"any-blocker-wins"`` (default — Review
            uses this) or ``"unanimous-approved"`` (opt-in alternative).

    Returns:
        dict with keys ``verdict``, ``blockers``, ``tech_debt``, ``consensus``
        (a metadata dict with ``rule``, ``k``, ``n``, ``mean_confidence``,
        ``blocker_aggregation``, ``tied``, ``decided_at``).

    Raises:
        ValueError: on unknown rule or malformed judge dict (missing required
            key, wrong types). Caller must catch and translate to a
            conservative outcome before any verdict write.
    """
    if rule not in _VALID_RULES:
        raise ValueError(f"unknown consensus rule {rule!r}; valid rules: {sorted(_VALID_RULES)}")

    if not judges:
        return _empty_conservative_outcome(rule)

    for j in judges:
        _validate_judge(j)

    deduped = _dedup_last_wins(judges)
    n = len(deduped)

    blockers_max = max(int(j["blockers"]) for j in deduped)
    tech_debt_max = max(int(j.get("tech_debt", 0) or 0) for j in deduped)
    confidences = [
        float(j["confidence"]) for j in deduped if isinstance(j.get("confidence"), (int, float))
    ]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    # Per-judge "approved" determination: verdict string is APPROVED *and* blockers == 0.
    approvals = [
        (j["verdict"].strip().upper() == "APPROVED" and int(j["blockers"]) == 0) for j in deduped
    ]
    all_approved = all(approvals)
    any_approved = any(approvals)

    if rule == "any-blocker-wins":
        # Any judge with blockers > 0 OR any non-APPROVED verdict → CHANGES REQUESTED.
        any_blocker = blockers_max > 0
        verdict = "APPROVED" if (all_approved and not any_blocker) else "CHANGES REQUESTED"
    else:  # unanimous-approved
        verdict = "APPROVED" if all_approved else "CHANGES REQUESTED"

    # "tied" semantics: any disagreement among judges. With K=2 and split
    # approve/block, this is the conservative-decided tie.
    tied = any_approved and not all_approved

    return {
        "verdict": verdict,
        "blockers": blockers_max,
        "tech_debt": tech_debt_max,
        "consensus": {
            "rule": rule,
            "k": n,
            "n": n,
            "mean_confidence": mean_confidence,
            "blocker_aggregation": "max",
            "tied": tied,
            "decided_at": datetime.now(UTC).isoformat(),
        },
    }
