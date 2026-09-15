"""Data access layer for the improvement-controller dashboard partials (#3177).

Read-only. All functions are synchronous (``def``, not ``async def``) because
Popoto uses synchronous Redis calls and FastAPI runs sync handlers in a
threadpool.

**Renders only what a lane writes.** Views backed by ``ImprovementEvidence``,
the provisional-assumptions list, the goals record, the release lineage, and
(lane 3, #3215) the control namespace:

- **Coverage** — how much the system is actually observing, so a rate has a
  denominator. A falling correction count with a falling scan count is not an
  improvement, and this is the panel that makes the difference visible.
- **Intervention burden** — how often a human had to step in, split by
  classification, with the raw counts published beside anything normalized.
- **Release lineage** (#3218) — every release joined to its evaluation,
  experiment, case, drill, window, and outcome, beside the promotion gate's
  sentence. Lineage, never a tally.
- **Control status** (:func:`get_control_status`) — dispatch intents by
  state, lane slots, unit-2 spend, paused heads, and
  ``reconciliation_required`` wedges. Read through
  ``tools.improvement_control.intents.list_intents`` over each open case's
  own set, never a keyspace scan.
- **Ranking** (:func:`get_ranking`, lane 5, #3217) — the latest ranking
  snapshot's order with each case's movement against the previous snapshot,
  the cases that left, and the intake pool. A snapshot that does not verify
  is ``unavailable`` with the integrity error, never a stale order.
- **Hypotheses** (:func:`get_hypotheses`, lane 5) — experiments in flight
  (``proposed``, ``frozen``, ``running``) with hypothesis, mechanism,
  falsifier, contract digest, and when the contract froze.
- **Rejected approaches** (:func:`get_rejected_approaches`, lane 5) — cases
  in ``rejected`` with the reason, the latest evaluation's verdict, effect
  and interval per endpoint, and the snapshot in which the case left the
  order, found by walking the snapshot chain.

**Two things this module will never show.** Experiment count and merged-patch
count are activity, not improvement, and presenting either as improvement is
the specific dishonesty ``docs/plans/recursive-self-improvement.md`` names.
There is no function here that returns them.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)

#: How many snapshots :func:`get_rejected_approaches` walks back along
#: ``previous_ref`` before giving up on locating a departure.
SNAPSHOT_WALK_LIMIT = 500

#: Default window for the dashboard panels, in days. Shorter than the evidence
#: TTL (30 days) so the panels show a settled window rather than one whose tail
#: is being eaten by expiry while you look at it.
DEFAULT_WINDOW_DAYS = 14

#: How many evidence rows a panel reads. Bounded; the panels are summaries.
READ_LIMIT = 1000

#: Classifications shown in the intervention-burden breakdown, in the order a
#: reader should meet them: the one that matters most first.
BURDEN_ORDER = ("architectural", "scope", "preference", "clarification", "unknown")


def _within(row, cutoff: datetime) -> bool:
    stamp = getattr(row, "created_at", None)
    if not isinstance(stamp, datetime):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp >= cutoff


def _rows(project_key: str, window_days: int) -> list:
    from models.improvement_evidence import ImprovementEvidence

    try:
        rows = ImprovementEvidence.recent(project_key, limit=READ_LIMIT)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: evidence read failed for %s: %s", project_key, exc)
        return []
    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    return [r for r in rows if _within(r, cutoff)]


def get_coverage(project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """What the system observed in the window, and how recently it last looked.

    ``last_collected_at`` is the honest health signal for this panel: if the
    collection tick stopped running, every count below is a count of nothing
    and the panel says so rather than showing a comforting zero.
    """
    rows = _rows(project_key, window_days)

    by_kind: dict[str, int] = {}
    for row in rows:
        kind = getattr(row, "kind", None) or "other"
        by_kind[kind] = by_kind.get(kind, 0) + 1

    coverage_rows = [
        r for r in rows if (getattr(r, "source_ref", "") or "").startswith("coverage:")
    ]
    latest_coverage = coverage_rows[0] if coverage_rows else None

    last_collected_at = None
    if rows:
        stamps = [getattr(r, "created_at", None) for r in rows]
        stamps = [s for s in stamps if isinstance(s, datetime)]
        if stamps:
            last_collected_at = max(stamps)

    return {
        "project_key": project_key,
        "window_days": window_days,
        "total": len(rows),
        "by_kind": by_kind,
        "ticks_observed": len(coverage_rows),
        "latest_coverage_detail": getattr(latest_coverage, "detail", None),
        "latest_coverage_text": getattr(latest_coverage, "text", None),
        "last_collected_at": last_collected_at,
        "collector_silent": last_collected_at is None,
    }


def get_intervention_burden(
    project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS
) -> dict:
    """How often a human had to step in, and of what kind.

    The raw counts are published beside the share, deliberately. A share on its
    own hides the case that matters most here: fewer corrections because fewer
    sessions ran, or because the collector stopped, reads identically to fewer
    corrections because the work got better.
    """
    rows = _rows(project_key, window_days)
    corrections = [r for r in rows if getattr(r, "kind", None) == "correction"]

    counts = {name: 0 for name in BURDEN_ORDER}
    for row in corrections:
        name = getattr(row, "classification", None) or "unknown"
        counts[name] = counts.get(name, 0) + 1

    total = len(corrections)
    breakdown = []
    for name in BURDEN_ORDER:
        count = counts.get(name, 0)
        breakdown.append(
            {
                "classification": name,
                "count": count,
                "share": (count / total) if total else None,
            }
        )

    recent = []
    for row in corrections[:10]:
        recent.append(
            {
                "classification": getattr(row, "classification", None) or "unknown",
                "session_id": getattr(row, "source_session_id", None),
                "text": (getattr(row, "text", None) or "")[:180],
                "created_at": getattr(row, "created_at", None),
            }
        )

    return {
        "project_key": project_key,
        "window_days": window_days,
        "total_corrections": total,
        "architectural": counts.get("architectural", 0),
        "unclassified": counts.get("unknown", 0),
        "breakdown": breakdown,
        "recent": recent,
        # An empty window is not evidence of a low burden. Say which it is.
        "no_evidence_yet": total == 0,
    }


def get_provisional_assumptions(
    project_key: str = "valor", window_days: int = DEFAULT_WINDOW_DAYS
) -> list[dict]:
    """Things the system decided to proceed on without resolving.

    The controller asks no human anything, so an unresolved uncertainty becomes
    a recorded assumption rather than a question in someone's queue. Surfacing
    them is the whole compensating control: an assumption nobody can see is
    indistinguishable from a fact.

    A failed read propagates. Returning ``[]`` here would hand every caller an
    empty list that is indistinguishable from an honest zero, which is the one
    thing this list exists to prevent; the caller classifies the failure.

    Raises:
        Exception: whatever the investigation read raises.
    """
    from models.improvement_investigation import ImprovementInvestigation

    rows = list(ImprovementInvestigation.query.filter(project_key=project_key))

    cutoff = datetime.now(UTC) - timedelta(days=window_days)
    out = []
    for row in rows:
        assumption = getattr(row, "provisional_assumption", None)
        if not assumption or not _within(row, cutoff):
            continue
        out.append(
            {
                "assumption": assumption,
                "uncertainty": getattr(row, "uncertainty", None),
                "kind": getattr(row, "kind", None),
                "state": getattr(row, "state", None),
                "expires_at": getattr(row, "expires_at", None),
                "created_at": getattr(row, "created_at", None),
            }
        )
    out.sort(key=lambda d: d["created_at"] or datetime.min.replace(tzinfo=UTC), reverse=True)
    return out


#: Charter §3's early priorities, in the charter's own order. Strong starting
#: hypotheses, explicitly "not a fixed allocation or permanent ordering": the
#: partial says so, because a list rendered without that sentence reads as a
#: quota.
CHARTER_PRIORITIES = (
    "Discover free or inexpensive inference and integrate suitable models",
    "Improve token efficiency without losing needed context or reasoning",
    "Expand and improve the skill library",
    "Design narrow subagent personas for niche tasks",
    "Acquire cloud execution capacity for continuous operation",
)

#: The charter §11 headings this build cannot fill yet, and the lane that will.
#: Each renders as "nothing yet, written by lane N" rather than as a zero: a
#: zero claims a measurement was taken.
PENDING_SECTIONS = (
    ("Acquired abilities", "lane 3 records these as it acquires them"),
    ("Evaluations", "lane 4 writes the paired blinded evaluations"),
    (
        "Rejected approaches",
        "the rejected partial below lists what was tried and set aside, with each evaluation",
    ),
    ("Resource use by budget unit", "lane 3 meters paid inference and infrastructure"),
)


def get_control_status(project_key: str = "valor") -> dict:
    """The lane-3 control panel: intents by state, slots, unit-2 spend,
    paused heads, and reconciliation_required intents (#3215).

    Three-state rendering like every other panel in this module: content,
    "nothing yet, written by lane 3 when a case is admitted" on an empty
    namespace, and "unavailable" when the underlying read raised. Reads
    through ``intents.list_intents`` over each open case's own ``intents``
    set -- never a keyspace scan -- exactly like ``doctor`` and
    ``case explain``.
    """
    try:
        from models.improvement_case import OPEN_CASE_STATES, ImprovementCase
        from tools.improvement_control import keys
        from tools.improvement_control.intents import list_intents
        from tools.improvement_control.journal import read_head
        from utils.redis_client import text_redis

        cases = []
        for state in OPEN_CASE_STATES:
            cases.extend(ImprovementCase.query.filter(project_key=project_key, state=state))

        intents_by_state: dict[str, list[dict]] = {}
        paused_heads: list[dict] = []
        reconciliation_required: list[dict] = []
        for case in cases:
            head = read_head(project_key, case.id)
            if head is not None and head.paused:
                paused_heads.append({"case_id": case.id, "pause_reason": head.pause_reason})
            for intent in list_intents(project_key, case.id):
                row = {
                    "case_id": case.id,
                    "action_id": intent.action_id,
                    "action_type": intent.action_type,
                    "agent_session_id": intent.agent_session_id,
                }
                intents_by_state.setdefault(intent.state, []).append(row)
                if intent.state == "reconciliation_required":
                    reconciliation_required.append(row)

        r = text_redis()
        slots = r.hgetall(keys.slots_key(project_key))

        try:
            from tools.paid_inference_meter import status_dict as unit2_status_dict

            unit2 = unit2_status_dict(project_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement dashboard: unit-2 read failed: %s", exc)
            unit2 = None

        return {
            "unavailable": False,
            "empty": not cases and not slots,
            "project_key": project_key,
            "intents_by_state": intents_by_state,
            "slot_count": len(slots),
            "paused_heads": paused_heads,
            "reconciliation_required": reconciliation_required,
            "unit2": unit2,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: control status read failed: %s", exc)
        return {"unavailable": True, "empty": False, "project_key": project_key}


def get_goals(project_key: str = "valor") -> dict:
    """The charter §11 readable record: goals, ranking, and what is not measured.

    Three distinguishable states per section, never two. Content, "nothing yet,
    written by lane N", and "unavailable" when the underlying read raised.
    Collapsing the last two would let a broken query read as an honest zero,
    which is the specific dishonesty the charter warns against.
    """
    charter = None
    charter_unavailable = False
    try:
        from models.improvement_charter import ImprovementCharter

        pinned = ImprovementCharter.pinned(project_key=project_key)
        if pinned is not None:
            charter = {
                "version": getattr(pinned, "version", None),
                "effective": getattr(pinned, "effective", None),
                # Full digest, never truncated: comparing it against the file
                # by eye is the point of showing it at all.
                "digest": getattr(pinned, "digest", None),
                "created_at": getattr(pinned, "created_at", None),
            }
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: charter read failed: %s", exc)
        charter_unavailable = True

    # Positions come from the latest ranking snapshot when one exists. A
    # snapshot that is missing or does not verify leaves every position
    # ``None``; the ranking partial is where that failure is named.
    ranking = get_ranking(project_key=project_key)
    ranking_available = not ranking["unavailable"] and not ranking["no_snapshot_yet"]
    positions = (
        {entry["case_id"]: entry["position"] for entry in ranking["order"]}
        if ranking_available
        else {}
    )

    cases = []
    cases_unavailable = False
    try:
        from models.improvement_case import OPEN_CASE_STATES, ImprovementCase

        # One indexed lookup per open state, which is what the ``state``
        # IndexedField is declared for. Cases are immortal, so hydrating the
        # whole partition and filtering in Python would grow without bound on a
        # partial that refreshes every 60 seconds. The open-state vocabulary is
        # the model's, never restated here.
        open_rows = []
        for state in OPEN_CASE_STATES:
            open_rows.extend(ImprovementCase.query.filter(project_key=project_key, state=state))
        open_rows.sort(
            key=lambda r: getattr(r, "created_at", None) or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        # Ranked cases first in snapshot order, then the unranked newest first.
        open_rows.sort(key=lambda r: positions.get(getattr(r, "id", None), float("inf")))
        for row in open_rows:
            cases.append(
                {
                    "id": getattr(row, "id", None),
                    "position": positions.get(getattr(row, "id", None)),
                    "title": getattr(row, "title", None),
                    "state": getattr(row, "state", None),
                    "priority": getattr(row, "priority", None),
                    "priority_area": getattr(row, "priority_area", None),
                    "ranking_rationale": getattr(row, "ranking_rationale", None),
                    "charter_digest": getattr(row, "charter_digest", None),
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: case read failed: %s", exc)
        cases_unavailable = True

    assumptions_unavailable = False
    try:
        assumptions = get_provisional_assumptions(project_key=project_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: assumption read failed: %s", exc)
        assumptions = []
        assumptions_unavailable = True

    return {
        "project_key": project_key,
        "charter": charter,
        "charter_unavailable": charter_unavailable,
        "charter_missing": charter is None and not charter_unavailable,
        "priorities": list(CHARTER_PRIORITIES),
        "cases": cases,
        "cases_unavailable": cases_unavailable,
        "no_cases_yet": not cases and not cases_unavailable,
        "ranking_available": ranking_available,
        "assumptions": assumptions,
        "assumptions_unavailable": assumptions_unavailable,
        "pending_sections": [
            {"heading": heading, "written_by": written_by}
            for heading, written_by in PENDING_SECTIONS
        ],
    }


#: Human text for an ``outcome.reason`` code; a code with no entry renders as itself.
OUTCOME_REASON_TEXT = {
    "ZERO_DENOMINATOR": "no denominator",
    "EVIDENCE_TRUNCATED": "EVIDENCE_TRUNCATED (the read hit its limit inside the window)",
    "EVIDENCE_EXPIRED": "EVIDENCE_EXPIRED (the window's early evidence rows expired)",
    "EVIDENCE_UNAVAILABLE": "evidence unavailable; the read failed",
    "DETECTION_DECLINED": "detection declined against the baseline",
}


def _outcome_sentence(state: str | None, outcome: dict) -> str | None:
    """One sentence for a closed window; ``None`` while nothing has been scored."""
    verdict = outcome.get("verdict")
    if verdict is None:
        return None
    reason = outcome.get("reason")
    reason_text = OUTCOME_REASON_TEXT.get(reason, reason) if reason else None
    if state == "observing" and verdict == "regressed":
        return "window closed: regressed, rollback recommended"
    if state == "observing" and verdict == "undetermined":
        suffix = f" ({reason_text})" if reason_text else ""
        return f"window closed: undetermined{suffix}; rollback recommended"
    if reason_text:
        return f"window closed: {verdict} ({reason_text})"
    return f"window closed: {verdict}"


def _stamp_text(value) -> str | None:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M UTC")
    return str(value) if value else None


def get_release_lineage(project_key: str = "valor") -> dict:
    """Every release with its lineage, and the promotion gate as a sentence (#3218).

    Delegates to ``tools.improvement_release.lineage.release_lineage`` and
    shapes each row for the partial: a drill line, a window line, and one
    outcome sentence. ``unavailable`` means the release read raised (logged
    there); the gate travels regardless, so the partial can always say that
    automated promotion is disabled. There is no count here: a lineage is a
    list of what happened to each release, never a number of releases.
    """
    from tools.improvement_release.lineage import release_lineage as _release_lineage

    lineage = _release_lineage(project_key)
    gate = lineage.get("promotion_gate") or {}
    unmet = list(gate.get("unmet") or [])
    releases = []
    for row in lineage.get("releases") or []:
        drill = row.get("drill") or {}
        outcome = row.get("outcome") or {}
        window = row.get("window") or {}
        releases.append(
            {
                **row,
                "drilled": drill.get("result") is not None,
                "drilled_at_text": _stamp_text(drill.get("drilled_at")),
                "exposed": window.get("exposed_at") is not None,
                "exposed_at_text": _stamp_text(window.get("exposed_at")),
                "ends_at_text": _stamp_text(window.get("ends_at")),
                "outcome_sentence": _outcome_sentence(row.get("state"), outcome),
            }
        )
    return {
        "project_key": project_key,
        "releases": releases,
        "promotion_gate": {"automated": bool(gate.get("automated")), "unmet": unmet},
        "gate_sentence": (
            "Automated promotion: enabled"
            if gate.get("automated")
            else "Automated promotion: disabled; unmet: "
            + (", ".join(unmet) if unmet else "none recorded")
        ),
        "unavailable": bool(lineage.get("unavailable")),
        "no_releases_yet": bool(lineage.get("no_releases_yet")),
    }


# ---------------------------------------------------------------------------
# Lane 5 (#3217): ranking, hypotheses, rejected approaches
# ---------------------------------------------------------------------------


def _case_rows_by_id(project_key: str, case_ids) -> dict:
    """``{case_id: row}`` for the ids given; a missing or unreadable row is absent."""
    from tools.improvement_release.rows import lookup

    out = {}
    for case_id in case_ids:
        try:
            from models.improvement_case import ImprovementCase

            row = lookup(ImprovementCase, project_key, case_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement dashboard: case %s read failed: %s", case_id, exc)
            row = None
        if row is not None:
            out[case_id] = row
    return out


def get_ranking(project_key: str = "valor") -> dict:
    """The latest ranking snapshot with movement (lane 5, #3217).

    ``order`` is the snapshot's order, each entry carrying the case title
    and its ``movement`` against the previous snapshot (``entered``,
    ``moved from N``, or ``None``); ``left`` names the cases that dropped
    out and why; ``intake_pool`` is the inspiration evidence waiting for a
    case. ``no_snapshot_yet`` when the controller state names none;
    ``unavailable`` with ``error`` when the store read raised or the
    snapshot did not verify. A corrupted snapshot never renders as an
    order: the integrity error is the whole answer.
    """
    empty = {
        "project_key": project_key,
        "unavailable": False,
        "error": None,
        "no_snapshot_yet": False,
        "ref": None,
        "digest": None,
        "at": None,
        "charter_digest": None,
        "order": [],
        "entered": [],
        "left": [],
        "moved": [],
        "intake_pool": [],
    }
    try:
        from models.improvement_controller_state import ImprovementControllerState
        from tools.improvement_ranking import load_snapshot, snapshot_digest

        state = ImprovementControllerState.get(project_key)
        ref = getattr(state, "last_snapshot_ref", None) if state is not None else None
        if not ref:
            return {**empty, "no_snapshot_yet": True}
        doc = load_snapshot(ref)
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: ranking read failed for %s: %s", project_key, exc)
        return {**empty, "unavailable": True, "error": str(exc)}

    diff = doc.get("diff") or {}
    entered = list(diff.get("entered") or [])
    moved = {m.get("case_id"): m for m in (diff.get("moved") or [])}
    order = list(doc.get("order") or [])
    titles = _case_rows_by_id(project_key, [o.get("case_id") for o in order])
    entries = []
    for entry in order:
        case_id = entry.get("case_id")
        movement = None
        if case_id in entered:
            movement = "entered"
        elif case_id in moved:
            movement = f"moved from {moved[case_id].get('from')}: {moved[case_id].get('why')}"
        row = titles.get(case_id)
        entries.append(
            {
                "case_id": case_id,
                "position": entry.get("position"),
                "title": getattr(row, "title", None),
                "priority_area": getattr(row, "priority_area", None),
                "state": getattr(row, "state", None),
                "factors": dict(entry.get("factors") or {}),
                "reason": entry.get("reason"),
                "blocked_by": entry.get("blocked_by"),
                "movement": movement,
            }
        )
    return {
        **empty,
        "ref": ref,
        "digest": snapshot_digest(ref),
        "at": doc.get("at"),
        "charter_digest": doc.get("charter_digest"),
        "order": entries,
        "entered": entered,
        "left": [dict(e) for e in (diff.get("left") or [])],
        "moved": list(moved.values()),
        "intake_pool": list(doc.get("intake_pool") or []),
    }


#: Experiment states whose hypothesis is still in flight.
IN_FLIGHT_EXPERIMENT_STATES = ("proposed", "frozen", "running")


def get_hypotheses(project_key: str = "valor") -> dict:
    """Experiments in flight, each with the hypothesis it is testing (lane 5).

    Reads the ``state`` index once per in-flight state. Newest first.
    ``no_hypotheses_yet`` on an empty read, ``unavailable`` with ``error``
    when the read raised. Never a count.
    """
    try:
        from models.improvement_experiment import ImprovementExperiment
        from tools.improvement_release.rows import recency

        rows = []
        for state in IN_FLIGHT_EXPERIMENT_STATES:
            rows.extend(ImprovementExperiment.query.filter(project_key=project_key, state=state))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: experiment read failed for %s: %s", project_key, exc)
        return {
            "project_key": project_key,
            "unavailable": True,
            "error": str(exc),
            "no_hypotheses_yet": False,
            "experiments": [],
        }
    rows.sort(key=recency, reverse=True)
    cases = _case_rows_by_id(project_key, {getattr(r, "case_id", None) for r in rows})
    experiments = []
    for row in rows:
        case = cases.get(getattr(row, "case_id", None))
        experiments.append(
            {
                "id": getattr(row, "id", None),
                "state": getattr(row, "state", None),
                "case_id": getattr(row, "case_id", None),
                "case_title": getattr(case, "title", None),
                "priority_area": getattr(case, "priority_area", None),
                "hypothesis": getattr(row, "hypothesis", None),
                "mechanism": getattr(row, "mechanism", None),
                "falsifier": getattr(row, "falsifier", None),
                "contract_digest": getattr(row, "contract_digest", None),
                "frozen_at": getattr(row, "frozen_at", None),
                "frozen_at_text": _stamp_text(getattr(row, "frozen_at", None)),
            }
        )
    return {
        "project_key": project_key,
        "unavailable": False,
        "error": None,
        "no_hypotheses_yet": not experiments,
        "experiments": experiments,
    }


def _latest_evaluation(project_key: str, case) -> dict | None:
    """The newest evaluation the case's ``evaluation_ids`` names, shaped for
    the partial, or ``None`` when the case names none."""
    from models.improvement_evaluation import ImprovementEvaluation
    from tools.improvement_release.evaluation_read import json_field, notes_of
    from tools.improvement_release.rows import lookup

    ids = json_field(getattr(case, "evaluation_ids", None)) or []
    if not isinstance(ids, list) or not ids:
        return None
    evaluation = lookup(ImprovementEvaluation, project_key, ids[-1])
    if evaluation is None:
        return {"id": str(ids[-1]), "missing": True}
    effects_raw = json_field(getattr(evaluation, "effect", None))
    intervals_raw = json_field(getattr(evaluation, "confidence_interval", None))
    effects_raw = effects_raw if isinstance(effects_raw, dict) else {}
    intervals_raw = intervals_raw if isinstance(intervals_raw, dict) else {}
    endpoints = sorted(set(effects_raw) | set(intervals_raw))
    return {
        "id": str(evaluation.id),
        "missing": False,
        "verdict": getattr(evaluation, "verdict", None),
        "state": getattr(evaluation, "state", None),
        "blinded": getattr(evaluation, "blinded", None),
        "trials": getattr(evaluation, "trials", None),
        "effects": [
            {
                "endpoint": endpoint,
                "effect": effects_raw.get(endpoint),
                "interval": intervals_raw.get(endpoint)
                if isinstance(intervals_raw.get(endpoint), dict)
                else None,
            }
            for endpoint in endpoints
        ],
        "notes": notes_of(evaluation),
    }


def _departures(project_key: str) -> tuple[dict, str | None]:
    """``{case_id: {ref, digest, at, reason}}`` for every case a snapshot in
    the chain lists under ``diff.left``, newest departure winning, plus the
    error text when the walk stopped on an unreadable snapshot."""
    from models.improvement_controller_state import ImprovementControllerState
    from tools.improvement_ranking import load_snapshot, snapshot_digest

    found: dict = {}
    state = ImprovementControllerState.get(project_key)
    ref = getattr(state, "last_snapshot_ref", None) if state is not None else None
    steps = 0
    while ref and steps < SNAPSHOT_WALK_LIMIT:
        steps += 1
        try:
            doc = load_snapshot(ref)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement dashboard: snapshot %s unreadable: %s", ref, exc)
            return found, str(exc)
        for entry in (doc.get("diff") or {}).get("left") or []:
            case_id = entry.get("case_id")
            if case_id and case_id not in found:
                found[case_id] = {
                    "ref": ref,
                    "digest": snapshot_digest(ref),
                    "at": doc.get("at"),
                    "reason": entry.get("reason"),
                }
        ref = doc.get("previous_ref")
    return found, None


def get_rejected_approaches(project_key: str = "valor") -> dict:
    """Cases in ``rejected`` with why, the evaluation, and where they left (lane 5).

    One indexed read of the ``rejected`` state. Each row carries the
    ``rejected_reason``, the latest evaluation's verdict with effect and
    interval per endpoint, and ``left_in``: the snapshot whose diff lists
    the case under ``left``, found by walking the chain from the newest
    snapshot along ``previous_ref``. ``snapshot_error`` names the integrity
    or store error when that walk stopped early; the cases still render,
    with ``left_in`` unset for any departure the walk did not reach.
    ``unavailable`` with ``error`` when the case read itself raised.
    """
    try:
        from models.improvement_case import ImprovementCase
        from tools.improvement_release.rows import recency

        rows = list(ImprovementCase.query.filter(project_key=project_key, state="rejected"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("improvement dashboard: rejected read failed for %s: %s", project_key, exc)
        return {
            "project_key": project_key,
            "unavailable": True,
            "error": str(exc),
            "no_rejected_yet": False,
            "snapshot_error": None,
            "cases": [],
        }
    rows.sort(key=recency, reverse=True)
    departures: dict = {}
    snapshot_error = None
    if rows:
        try:
            departures, snapshot_error = _departures(project_key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("improvement dashboard: snapshot walk failed: %s", exc)
            snapshot_error = str(exc)
    cases = []
    for row in rows:
        try:
            evaluation = _latest_evaluation(project_key, row)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "improvement dashboard: evaluation read failed for case %s: %s", row.id, exc
            )
            evaluation = {"id": None, "missing": True}
        cases.append(
            {
                "id": getattr(row, "id", None),
                "title": getattr(row, "title", None),
                "priority_area": getattr(row, "priority_area", None),
                "rejected_reason": getattr(row, "rejected_reason", None),
                "dedup_identity": getattr(row, "dedup_identity", None),
                "evaluation": evaluation,
                "left_in": departures.get(getattr(row, "id", None)),
            }
        )
    return {
        "project_key": project_key,
        "unavailable": False,
        "error": None,
        "no_rejected_yet": not cases,
        "snapshot_error": snapshot_error,
        "cases": cases,
    }
