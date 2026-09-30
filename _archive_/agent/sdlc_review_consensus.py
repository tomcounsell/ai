"""Pure-Python consensus rules for the multi-judge Review gate.

This module is consumed by ``.claude/skills-global/do-pr-review/SKILL.md``
when the repo addendum declares ≥2 judges. The parent skill collects per-judge
dicts in memory, calls :func:`compute_consensus`, then makes a single
``record_verdict(... judges=[...], consensus=meta)`` call.

**Quorum floor.** ``compute_consensus`` optionally learns how many judges
were *declared* (``expected_judges``, the mandatory roster size only — never
the number dispatched, never inclusive of optional judges) and refuses to
run the rule when fewer distinct judges reported, returning the same
conservative outcome the zero-judge case already used. The shortfall rides
in the returned ``consensus`` dict as ``quorum_shortfall`` (bool, always
present) and ``expected_n`` (the declared floor, or ``None``), so a
degraded single-judge run is recorded as a shortfall rather than read back
as agreement. The guard compares cardinality only, never membership: it
cannot tell a mandatory judge id from a misnamed or optional one, so a
shortfall proves under-reporting but a satisfied floor does not prove the
declared roster specifically ran.

Pure function. No I/O. Fully unit-testable.

Reference: docs/plans/multi-judge-consensus-gates.md (rev1),
docs/plans/sdlc-3197.md (quorum floor).
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


def _conservative_outcome(
    rule: str, *, n: int = 0, expected_n: int | None = None
) -> dict[str, Any]:
    """Return a conservative CHANGES REQUESTED outcome.

    Shared by two call sites that are the same failure shape: zero judges
    reported (``n=0``), and a declared roster fell short of its floor
    (``n`` between 1 and ``expected_n - 1``). Matches the Failure Path
    strategy: the parent should never silently approve when the reporting
    judges fall short of what was expected.
    """
    return {
        "verdict": "CHANGES REQUESTED",
        "blockers": 1,
        "tech_debt": 0,
        "consensus": {
            "rule": rule,
            "k": n,
            "n": n,
            "mean_confidence": 0.0,
            "blocker_aggregation": "max",
            "tied": False,
            "expected_n": expected_n,
            "quorum_shortfall": expected_n is not None and n < expected_n,
            "decided_at": datetime.now(UTC).isoformat(),
        },
    }


def _validate_expected_judges(expected_judges: int | None) -> None:
    if expected_judges is None:
        return
    # isinstance(True, int) is True in Python — reject bool explicitly before
    # the int check, or a caller passing `expected_judges=True` would silently
    # behave as `expected_judges=1`.
    if isinstance(expected_judges, bool) or not isinstance(expected_judges, int):
        raise ValueError(f"expected_judges must be an int >= 1 or None, got {expected_judges!r}")
    if expected_judges < 1:
        raise ValueError(f"expected_judges must be an int >= 1 or None, got {expected_judges!r}")


def compute_consensus(
    judges: list[dict[str, Any]],
    rule: str = "any-blocker-wins",
    *,
    expected_judges: int | None = None,
) -> dict[str, Any]:
    """Aggregate per-judge dicts into a single scalar verdict + consensus meta.

    Args:
        judges: list of per-judge dicts. Each dict must contain ``judge_id``
            (str), ``verdict`` (str), ``blockers`` (int). Optional keys:
            ``tech_debt`` (int), ``confidence`` (float in [0,1]),
            ``reasoning_summary`` (str), ``review_url`` (str).
        rule: consensus rule. Either ``"any-blocker-wins"`` (default — Review
            uses this) or ``"unanimous-approved"`` (opt-in alternative).
        expected_judges: the size of the *mandatory* declared judge roster
            (keyword-only). When set, ``compute_consensus`` refuses to run
            the rule and returns a conservative outcome if fewer distinct
            judges reported than this floor. ``None`` (the default)
            reproduces today's behavior byte-for-byte apart from the two
            additive metadata keys below. Optional judges (e.g. the
            cross-vendor judge) must never be counted in this number — an
            optional judge that returns can only raise ``n`` above the
            floor, and one that skips leaves the floor exactly where it was.
            **Limit:** this bounds *how many* distinct ``judge_id``s
            reported, never *which* — a misnamed, substituted, or optional
            judge id satisfies the floor identically to a mandatory one. A
            shortfall proves under-reporting; a satisfied floor does not
            prove the declared roster specifically ran.

    Returns:
        dict with keys ``verdict``, ``blockers``, ``tech_debt``, ``consensus``
        (a metadata dict with ``rule``, ``k``, ``n``, ``mean_confidence``,
        ``blocker_aggregation``, ``tied``, ``expected_n``,
        ``quorum_shortfall``, ``decided_at``). ``expected_n`` is ``None``
        when the caller declared no expectation; ``quorum_shortfall`` is
        always present as a bool (``True`` iff ``expected_n is not None and
        n < expected_n``).

    Raises:
        ValueError: on unknown rule, an invalid ``expected_judges`` (not
            ``None`` and not an ``int >= 1`` — a ``bool`` is rejected
            explicitly since ``isinstance(True, int)`` is ``True``), or a
            malformed judge dict (missing required key, wrong types).
            Caller must catch and translate to a conservative outcome before
            any verdict write.
    """
    if rule not in _VALID_RULES:
        raise ValueError(f"unknown consensus rule {rule!r}; valid rules: {sorted(_VALID_RULES)}")
    _validate_expected_judges(expected_judges)

    if not judges:
        return _conservative_outcome(rule, n=0, expected_n=expected_judges)

    for j in judges:
        _validate_judge(j)

    deduped = _dedup_last_wins(judges)
    n = len(deduped)

    if expected_judges is not None and n < expected_judges:
        return _conservative_outcome(rule, n=n, expected_n=expected_judges)

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
            "expected_n": expected_judges,
            "quorum_shortfall": False,
            "decided_at": datetime.now(UTC).isoformat(),
        },
    }
