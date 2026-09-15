"""Ordinal ranking of open improvement cases, and the immutable snapshot of it.

Charter §3 says to rank opportunities "considering opportunity cost, quality,
resource cost, uncertainty, and the capacity they unlock for subsequent
improvements", and the parent plan says the ranking stays ordinal until
calibration supports numbers. So: five ordinals in ``{1, 2, 3}`` (low, medium,
high), no weights, no sums, and one lexicographic key. Every factor is derived
from named record fields by the rule stated here; ``rank()`` is pure and
reads no Redis.

**The factor rules** (each pinned by a test in
``tests/unit/test_improvement_ranking.py``):

- ``opportunity_cost``: 3 when the case's ``priority_area`` is one of the five
  charter §3 starting priorities (:data:`STARTING_PRIORITIES`) and no other
  open case in that area ranks above it (the area's leader under the rest of
  the key); 2 for a later case in a starting area and for the five named
  means of §3's closing sentence (:data:`NAMED_MEANS`); 1 for ``other``.
- ``quality``: 3 when the case holds at least one ``architectural``
  correction or three or more evidence rows; 2 for two rows; 1 for one (or
  none).
- ``resource_cost``: 1 when the case's likely action is an investigation (the
  default); 2 when a ``frozen`` or ``running`` experiment exists for it
  inside this lane's envelope; 3 when the case needs an arm shape this lane
  does not have (an experiment whose ``candidate_surfaces`` names
  ``agent_run``) or a credential that is not in the vault (``blocked_by`` set).
- ``uncertainty``: 3 when no investigation has resolved for the case; 2 when
  one has; 1 when a model revision cites the case (the revision's
  ``evidence_ids`` intersect the case's). An ``inconclusive`` verdict on any
  evaluation of one of the case's experiments resets it to 3.
- ``unlocked_capacity``: 3 for ``inference``, ``cloud_execution``,
  ``research_process``; 2 for ``skills``, ``evaluators``, ``memory``,
  ``orchestration``; 1 otherwise.

**The order** is the key ``(blocked, -opportunity_cost, -unlocked_capacity,
-quality, resource_cost, -uncertainty, created_at)`` with
``blocked = bool(case.blocked_by)``. A blocked case keeps its position in the
printed list with its ``blocked_by`` text; the tick's single proposal goes to
the first unblocked position. ``rank()`` reads ``blocked_by`` off the case
row and no investigation row, so a 30-day investigation TTL cannot lift a
block.

**The snapshot** is canonical JSON saved content-addressed through the
verifying artifact store, the same call shape as
``tools.improvement_eval.runner.freeze_protocol`` (spike-4), so a corrupted
snapshot raises ``ArtifactIntegrityError`` on load rather than rendering.
The newest reference lives on ``ImprovementControllerState.last_snapshot_ref``.

**This module hashes no process spec.** :func:`process_spec_json` produces
the canonical JSON text whose UTF-8 bytes lane 6's
``tools.improvement_recursion.process.research_process_digest`` hashes; the
digest function is lane 6's and only lane 6's.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from models.improvement_case import PRIORITY_AREAS

#: Charter §3, "Early priorities are expected to favor": the five bullets, in
#: the charter's order, mapped onto ``PRIORITY_AREAS``.
STARTING_PRIORITIES: tuple[str, ...] = (
    "inference",
    "token_efficiency",
    "skills",
    "personas",
    "cloud_execution",
)

#: Charter §3's closing sentence: "Research process improvements, better
#: evaluators, memory, orchestration, and new infrastructure are all eligible
#: means."
NAMED_MEANS: tuple[str, ...] = (
    "research_process",
    "evaluators",
    "memory",
    "orchestration",
    "infrastructure",
)

_HIGH_UNLOCK = frozenset({"inference", "cloud_execution", "research_process"})
_MEDIUM_UNLOCK = frozenset({"skills", "evaluators", "memory", "orchestration"})

FACTOR_NAMES: tuple[str, ...] = (
    "opportunity_cost",
    "quality",
    "resource_cost",
    "uncertainty",
    "unlocked_capacity",
)

SNAPSHOT_SCHEMA = 1
SNAPSHOT_MODEL_CLASS_NAME = "ImprovementRankingSnapshot"

assert set(STARTING_PRIORITIES) | set(NAMED_MEANS) | {"other"} == set(PRIORITY_AREAS)


@dataclass(frozen=True)
class RankedCase:
    """One position in the order."""

    case_id: str
    position: int
    factors: dict[str, int]
    reason: str
    blocked_by: str | None

    def as_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "position": self.position,
            "factors": dict(self.factors),
            "reason": self.reason,
            "blocked_by": self.blocked_by,
        }


# ---------------------------------------------------------------------------
# Small readers over ORM rows (or any object with the same attributes)
# ---------------------------------------------------------------------------


def _json_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _created_at_key(case: Any) -> float:
    value = getattr(case, "created_at", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.timestamp()
    return 0.0


def _needs_missing_arm(experiment: Any) -> bool:
    surfaces = getattr(experiment, "candidate_surfaces", None) or ""
    if isinstance(surfaces, list):
        return "agent_run" in surfaces
    return "agent_run" in str(surfaces)


# ---------------------------------------------------------------------------
# rank
# ---------------------------------------------------------------------------


def _base_factors(case, *, evidence_by_id, investigations, experiments, evaluations, revisions):
    """Every factor except ``opportunity_cost``, which needs the area order."""
    case_id = case.id
    evidence_ids = _json_list(getattr(case, "evidence_ids", None))
    rows = [evidence_by_id[eid] for eid in evidence_ids if eid in evidence_by_id]
    architectural = any(
        getattr(r, "kind", "correction") == "correction"
        and getattr(r, "classification", None) == "architectural"
        for r in rows
    )
    row_count = max(len(rows), len(evidence_ids))
    if architectural or row_count >= 3:
        quality = 3
    elif row_count == 2:
        quality = 2
    else:
        quality = 1

    case_experiments = [e for e in experiments if getattr(e, "case_id", None) == case_id]
    if getattr(case, "blocked_by", None) or any(_needs_missing_arm(e) for e in case_experiments):
        resource_cost = 3
    elif any(getattr(e, "state", None) in ("frozen", "running") for e in case_experiments):
        resource_cost = 2
    else:
        resource_cost = 1

    resolved = any(
        getattr(i, "case_id", None) == case_id and getattr(i, "state", None) == "resolved"
        for i in investigations
    )
    evidence_set = set(evidence_ids)
    cited = any(evidence_set & set(_json_list(getattr(r, "evidence_ids", None))) for r in revisions)
    experiment_ids = {e.id for e in case_experiments}
    inconclusive = any(
        getattr(v, "experiment_id", None) in experiment_ids
        and getattr(v, "verdict", None) == "inconclusive"
        for v in evaluations
    )
    if inconclusive:
        uncertainty = 3
    elif cited:
        uncertainty = 1
    elif resolved:
        uncertainty = 2
    else:
        uncertainty = 3

    area = getattr(case, "priority_area", None) or "other"
    if area in _HIGH_UNLOCK:
        unlocked = 3
    elif area in _MEDIUM_UNLOCK:
        unlocked = 2
    else:
        unlocked = 1

    return {
        "quality": quality,
        "resource_cost": resource_cost,
        "uncertainty": uncertainty,
        "unlocked_capacity": unlocked,
    }


def _rest_of_key(case, factors: dict[str, int]) -> tuple:
    return (
        bool(getattr(case, "blocked_by", None)),
        -factors["unlocked_capacity"],
        -factors["quality"],
        factors["resource_cost"],
        -factors["uncertainty"],
        _created_at_key(case),
    )


def _sort_key(case, factors: dict[str, int]) -> tuple:
    return (
        bool(getattr(case, "blocked_by", None)),
        -factors["opportunity_cost"],
        -factors["unlocked_capacity"],
        -factors["quality"],
        factors["resource_cost"],
        -factors["uncertainty"],
        _created_at_key(case),
    )


def _reason(case, factors: dict[str, int], charter) -> str:
    area = getattr(case, "priority_area", None) or "other"
    if area in STARTING_PRIORITIES:
        passage = f"charter §3 starting priority ({area})"
    elif area in NAMED_MEANS:
        passage = f"charter §3 eligible means ({area})"
    else:
        passage = "charter §3, outside the named priorities and means"
    digest = getattr(charter, "digest", None) or "unpinned"
    factor_text = ", ".join(f"{name}={factors[name]}" for name in FACTOR_NAMES)
    blocked = getattr(case, "blocked_by", None)
    tail = f"; blocked_by={blocked}" if blocked else ""
    return f"{passage}; {factor_text}; charter {digest[:23]}{tail}"


def rank(
    cases,
    *,
    evidence,
    investigations,
    experiments,
    charter,
    evaluations=(),
    revisions=(),
) -> list[RankedCase]:
    """Order ``cases`` by the module's lexicographic key. Pure.

    ``evidence``, ``investigations``, ``experiments``, ``evaluations``, and
    ``revisions`` are the rows the factor rules read (ORM rows or any objects
    carrying the same attributes). ``charter`` is the pinned charter row (or
    ``None``); only its digest reaches the reason text. ``rank([])`` is ``[]``.
    """
    cases = list(cases)
    if not cases:
        return []
    evidence_by_id = {getattr(r, "id", None): r for r in evidence}
    partial = {
        c.id: _base_factors(
            c,
            evidence_by_id=evidence_by_id,
            investigations=list(investigations),
            experiments=list(experiments),
            evaluations=list(evaluations),
            revisions=list(revisions),
        )
        for c in cases
    }

    # opportunity_cost: the leader of each starting area (by the rest of the
    # key) is 3; every later case in that area is 2, like the named means.
    leaders: dict[str, str] = {}
    for c in sorted(cases, key=lambda c: _rest_of_key(c, partial[c.id])):
        area = getattr(c, "priority_area", None) or "other"
        if area in STARTING_PRIORITIES and area not in leaders:
            leaders[area] = c.id
    factors: dict[str, dict[str, int]] = {}
    for c in cases:
        area = getattr(c, "priority_area", None) or "other"
        if area in STARTING_PRIORITIES:
            opportunity = 3 if leaders.get(area) == c.id else 2
        elif area in NAMED_MEANS:
            opportunity = 2
        else:
            opportunity = 1
        factors[c.id] = {"opportunity_cost": opportunity, **partial[c.id]}
        factors[c.id] = {name: factors[c.id][name] for name in FACTOR_NAMES}

    ordered = sorted(cases, key=lambda c: _sort_key(c, factors[c.id]))
    return [
        RankedCase(
            case_id=c.id,
            position=position,
            factors=factors[c.id],
            reason=_reason(c, factors[c.id], charter),
            blocked_by=getattr(c, "blocked_by", None) or None,
        )
        for position, c in enumerate(ordered, start=1)
    ]


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def _default_store():
    from models.verifying_artifact_store import VerifyingArtifactStore

    # Constructed per call (not the module singleton) so the retention root
    # is read from the environment at call time, as `cmd_propose` does.
    return VerifyingArtifactStore()


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def snapshot_digest(ref: str) -> str:
    """``sha256:<hex>`` of the snapshot a ``$CF:`` reference names."""
    content_hash, _relative = ref[len("$CF:") :].split(":", 1)
    return f"sha256:{content_hash}"


def _why_moved(before: dict, after: dict) -> str:
    changes = [
        f"{name} {before['factors'].get(name)}->{after['factors'].get(name)}"
        for name in FACTOR_NAMES
        if before["factors"].get(name) != after["factors"].get(name)
    ]
    if (before.get("blocked_by") or None) != (after.get("blocked_by") or None):
        changes.append(f"blocked_by {before.get('blocked_by')}->{after.get('blocked_by')}")
    return "factors changed: " + ", ".join(changes) if changes else "order shifted around it"


def _left_reason(case_id: str, cases_by_id: dict, order_empty: bool) -> str:
    row = cases_by_id.get(case_id)
    if row is not None and getattr(row, "state", None) == "rejected":
        evaluation_ids = _json_list(getattr(row, "evaluation_ids", None))
        eid = evaluation_ids[-1] if evaluation_ids else "unrecorded"
        return f"rejected: evaluation {eid}"
    if order_empty:
        return "no open cases"
    return "left open set"


def compute_diff(order: list[dict], previous: dict | None, *, cases=()) -> dict:
    """``{"entered", "left", "moved"}`` of ``order`` against ``previous``."""
    cases_by_id = {getattr(c, "id", None): c for c in cases}
    prev_by_id = {o["case_id"]: o for o in (previous or {}).get("order", [])}
    now_by_id = {o["case_id"]: o for o in order}
    entered = [cid for cid in now_by_id if cid not in prev_by_id]
    left = [
        {"case_id": cid, "reason": _left_reason(cid, cases_by_id, not order)}
        for cid in prev_by_id
        if cid not in now_by_id
    ]
    moved = [
        {
            "case_id": cid,
            "from": prev_by_id[cid]["position"],
            "to": now_by_id[cid]["position"],
            "why": _why_moved(prev_by_id[cid], now_by_id[cid]),
        }
        for cid in now_by_id
        if cid in prev_by_id and prev_by_id[cid]["position"] != now_by_id[cid]["position"]
    ]
    return {"entered": entered, "left": left, "moved": moved}


def write_snapshot(
    ranked: list[RankedCase],
    *,
    previous_ref: str | None,
    charter_digest: str,
    store=None,
    intake_pool=(),
    at: datetime | None = None,
    cases=(),
) -> str:
    """Write the immutable snapshot; return its ``$CF:`` reference.

    The diff is computed against ``load_snapshot(previous_ref)`` when one is
    given. ``cases`` (rows in any state) lets a ``left`` entry name the
    evaluation that rejected a case. An empty ``ranked`` still writes, with
    every previously ranked case under ``left``.
    """
    target = store or _default_store()
    previous = load_snapshot(previous_ref, store=target) if previous_ref else None
    order = [r.as_dict() for r in ranked]
    payload = {
        "schema": SNAPSHOT_SCHEMA,
        "charter_digest": charter_digest,
        "at": (at or datetime.now(UTC)).isoformat(),
        "order": order,
        "intake_pool": list(intake_pool),
        "previous_ref": previous_ref,
        "diff": compute_diff(order, previous, cases=cases),
    }
    payload_bytes = _canonical(payload)
    digest = hashlib.sha256(payload_bytes).hexdigest()
    return target.save(
        payload_bytes, key=f"ranking-{digest[:16]}", model_class_name=SNAPSHOT_MODEL_CLASS_NAME
    )


def load_snapshot(ref: str, *, store=None) -> dict:
    """The verified snapshot document. ``ArtifactIntegrityError`` propagates."""
    target = store or _default_store()
    return json.loads(target.load(ref).decode("utf-8"))


def latest_snapshot(project_key: str, *, store=None) -> dict | None:
    """The newest snapshot, by ``ImprovementControllerState.last_snapshot_ref``."""
    from models.improvement_controller_state import ImprovementControllerState

    state = ImprovementControllerState.get(project_key)
    ref = getattr(state, "last_snapshot_ref", None) if state is not None else None
    if not ref:
        return None
    return load_snapshot(ref, store=store)


# ---------------------------------------------------------------------------
# Process spec bytes (lane 6 hashes them; this lane only produces them)
# ---------------------------------------------------------------------------

PROCESS_SPEC_FIELDS: tuple[str, ...] = (
    "selection_rule",
    "investigation_budget_split",
    "revision_cadence_seconds",
    "planner_prompt_digest",
    "skill_digest",
    "extra",
)


def process_spec_json(spec) -> str:
    """Canonical JSON text of a research process spec: ``json.dumps(asdict(spec),
    sort_keys=True, separators=(",", ":"))``. Its UTF-8 encoding is exactly
    what ``tools.improvement_recursion.process.research_process_digest``
    hashes (that module's ``canonical_bytes``), and the text is what
    ``ImprovementModelRevision.research_process_spec`` stores. Accepts any
    dataclass or plain dict carrying :data:`PROCESS_SPEC_FIELDS`."""
    payload = dataclasses.asdict(spec) if dataclasses.is_dataclass(spec) else dict(spec)
    missing = [name for name in PROCESS_SPEC_FIELDS if name not in payload]
    if missing:
        raise ValueError(f"process spec is missing {missing}")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# CLI: valor-improve ranking [--at REF]
# ---------------------------------------------------------------------------


def render_snapshot(doc: dict) -> str:
    lines = [f"ranking at {doc.get('at')} under charter {doc.get('charter_digest')}"]
    order = doc.get("order") or []
    if not order:
        lines.append("  (no open cases)")
    for entry in order:
        factors = ", ".join(f"{k}={v}" for k, v in (entry.get("factors") or {}).items())
        blocked = f" [blocked: {entry['blocked_by']}]" if entry.get("blocked_by") else ""
        lines.append(f"  {entry['position']}. {entry['case_id']}{blocked}  {factors}")
        lines.append(f"     {entry.get('reason', '')}")
    pool = doc.get("intake_pool") or []
    lines.append("intake pool: " + (", ".join(pool) if pool else "empty"))
    diff = doc.get("diff") or {}
    lines.append("entered: " + (", ".join(diff.get("entered") or []) or "none"))
    left = diff.get("left") or []
    lines.append("left: " + (", ".join(f"{e['case_id']} ({e['reason']})" for e in left) or "none"))
    moved = diff.get("moved") or []
    lines.append(
        "moved: "
        + (
            ", ".join(f"{m['case_id']} {m['from']}->{m['to']} ({m['why']})" for m in moved)
            or "none"
        )
    )
    return "\n".join(lines)


def cmd_ranking(args, *, project_key: str) -> int:
    """Print the latest snapshot, or the one ``--at`` names.

    A reference that does not verify prints the ``ArtifactIntegrityError``
    to stderr and exits 2; no snapshot yet prints "no snapshot yet", exit 0.
    """
    from models.verifying_artifact_store import ArtifactIntegrityError

    try:
        doc = load_snapshot(args.at) if args.at else latest_snapshot(project_key)
    except ArtifactIntegrityError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if doc is None:
        if getattr(args, "json", False):
            print(json.dumps({"snapshot": None}))
        else:
            print("no snapshot yet")
        return 0
    if getattr(args, "json", False):
        print(json.dumps({"snapshot": doc}))
    else:
        print(render_snapshot(doc))
    return 0
