"""Experiments for the first complete research cycle (lane 5, #3217, task 6).

Four writers sit between a research session's hypothesis and the ranking
that decides what the loop tries next:

- :func:`propose_experiment` turns the accepted ``valor-improve propose``
  payload (``{"hypothesis", "mechanism", "falsifier", "candidate",
  "envelope"}``) into a ``proposed`` ``ImprovementExperiment`` and journals
  ``hypothesis_proposed`` on the case.
- :func:`freeze_experiment` runs the seven freeze steps in the plan's order
  and ends with ``state="frozen"`` and the ``experiment_frozen`` event. The
  hashing happens before any arm runs.
- :func:`evaluate_experiment` reserves the judge spend and hands the frozen
  experiment to lane 4's runner, the single writer of ``ImprovementEvaluation``.
- :func:`apply_verdict` moves the case per Data Flow step 6.

**The envelope.** This lane's candidates vary retrieval parameters only:
:data:`ENVELOPES` names the three keys ``handle_job`` forwards to
``retrieve_memories`` and the range each may take. ``retrieval_mode`` is an
environment setting the arena pins, never a call parameter, so a candidate
naming it is refused ``KEY_OUTSIDE_ENVELOPE`` (spike-2). The incumbent is
exactly :data:`INCUMBENT`: ``_retrieve_job`` copies every present key into
the arm job, and a present ``None`` is not "absent", so neither the incumbent
nor a candidate ever carries a ``None`` value.

**Case state.** ``ImprovementCase.state`` moves only through
``journal.set_state`` under the case lease, followed by ``projection.apply``;
non-state fields (``evaluation_ids``, ``rejected_reason``) are then written
with a plain ``save()``. A lease that cannot be taken is ``CASE_BUSY`` and
nothing is written in that call. Freeze moves an ``observed`` or
``investigating`` case to ``experimenting``; evaluation moves it to
``evaluating``; the verdict moves it per step 6 (``reject`` -> ``rejected``,
``accept`` -> ``evaluating``, ``inconclusive`` -> ``investigating``,
``infra_failure`` and ``invalidated`` leave it where it was).

**Money.** The known-item generation and the judge calls are unit-2 spend
and go through ``tools.paid_inference_meter``: a reservation before the
call, a settlement after. Both amounts are estimates sized from named
constants (:data:`KNOWN_ITEM_PRICE_ESTIMATE_USD`,
:data:`JUDGE_PRICE_ESTIMATE_USD`) and settle as ``metering="estimated"``,
which the qualified-result report names (charter §8: uncertain metering is
never zero cost). A refusal from the meter is ``UNIT2_UNAVAILABLE`` and the
experiment stays where it was.

Nothing here writes ``ImprovementEvaluation`` (lane 4's runner does) or a
release record (lane 6's), and nothing here places a credential.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models.improvement_case import ImprovementCase
from models.improvement_evaluation import ImprovementEvaluation
from models.improvement_experiment import ImprovementExperiment
from tools import paid_inference_meter
from tools.improvement_eval.corpus import export_corpus
from tools.improvement_eval.errors import InfraFailure
from tools.improvement_eval.runner import (
    CALIBRATION_NOTE_PREFIX,
    capture_baseline,
    compute_contract_digest,
    freeze_protocol,
    load_protocol,
    read_content,
    repair_wedged_experiment,
)
from tools.memory_eval.query_set import build_known_item_set

logger = logging.getLogger(__name__)

#: The candidate envelopes this lane admits and the range of every key.
ENVELOPES: dict[str, dict[str, tuple[float, float]]] = {
    "retrieval_parameters": {
        "limit": (1, 50),
        "rrf_k": (1, 200),
        "min_rrf_score": (0.0, 1.0),
    }
}

#: The production default for the one key ``handle_job`` read before this
#: lane, taken from ``retrieve_memories``'s signature. ``rrf_k`` and
#: ``min_rrf_score`` are omitted, never ``None``.
INCUMBENT: dict[str, int] = {"limit": 10}

#: Below this many known-item queries the freeze refuses ``KNOWN_ITEM_SHORTFALL``.
MIN_QUERIES = 20

#: The default request; the protocol discloses the count actually produced.
DEFAULT_N_QUERIES = 30
DEFAULT_SEED = 3217

#: The one prior paired evaluation of retrieval over this corpus. Cited on
#: every retrieval-parameter experiment; no heuristic decides relevance.
PRIOR_ANSWER_2082: dict[str, str] = {
    "ref": "#2082",
    "doc": "docs/features/hybrid-retrieval-eval.md",
    "plan": "docs/archive/plans-completed/hybrid-retrieval-eval.md",
    "why": "prior paired evaluation of retrieval over this corpus",
}

ENDPOINTS: tuple[str, ...] = ("recall_at_5", "mrr")
THRESHOLDS: dict[str, dict[str, float]] = {
    "mrr": {"margin": 0.02, "alpha": 0.05},
    "recall_at_5": {"margin": 0.02, "alpha": 0.05},
}
INFRA_FAILURE_CAP = 0

#: Estimated unit-2 cost of one known-item generation (one short Haiku-class
#: call through ``agent.llm.run_typed``). An estimate for the reservation,
#: settled as ``metering="estimated"``; the reconcile pass may correct it.
KNOWN_ITEM_PRICE_ESTIMATE_USD = 0.002

#: Estimated unit-2 cost of one judge call. Two calls per query (one per
#: arm), so the reservation is ``n_queries * 2 * JUDGE_PRICE_ESTIMATE_USD``.
#: An estimate, settled as ``metering="estimated"``.
JUDGE_PRICE_ESTIMATE_USD = 0.01

RESERVATION_KNOWN_ITEMS = "known_item_generation"
RESERVATION_JUDGES = "evaluation_judges"

#: Data Flow step 6: verdict -> the case state it selects. ``None`` leaves
#: the case where it was.
VERDICT_TO_CASE_STATE: dict[str, str | None] = {
    "reject": "rejected",
    "accept": "evaluating",
    "inconclusive": "investigating",
    "infra_failure": None,
    "invalidated": None,
}

_REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Outcome:
    """The result of one experiment write. An expected refusal is a reason
    code plus a sentence, never an exception."""

    accepted: bool
    reason: str
    experiment_id: str | None = None
    message: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class KnownItemRecord:
    """The three attributes ``build_known_item_set`` reads off a record."""

    memory_id: str
    content: str
    importance: float


def _refuse(reason: str, message: str = "", experiment_id: str | None = None) -> Outcome:
    return Outcome(False, reason, experiment_id, message or reason)


def _now() -> datetime:
    return datetime.now(UTC)


def _load_json(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (list, dict)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _digest(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _checkout_sha() -> str:
    """The checkout's git SHA at freeze; ``unknown`` when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 -- a missing git is a recorded "unknown"
        return "unknown"


# ---------------------------------------------------------------------------
# The envelope
# ---------------------------------------------------------------------------


def validate_candidate(candidate, *, envelope: str = "retrieval_parameters") -> Outcome:
    """Admit a candidate inside ``envelope`` or refuse with one reason code.

    Codes, in the order they are checked: ``UNKNOWN_ENVELOPE``,
    ``EMPTY_CANDIDATE``, ``KEY_OUTSIDE_ENVELOPE`` (``retrieval_mode`` and any
    other name the arm does not accept), ``NONE_VALUE``,
    ``VALUE_OUTSIDE_RANGE`` (type or range; booleans are refused as values),
    ``IDENTICAL_TO_INCUMBENT``. An accepted outcome carries
    ``extra["surfaces"]``: the sorted envelope keys the candidate varies.
    """
    ranges = ENVELOPES.get(envelope)
    if ranges is None:
        return _refuse("UNKNOWN_ENVELOPE", f"{envelope!r} is not one of {sorted(ENVELOPES)}")
    if not isinstance(candidate, dict) or not candidate:
        return _refuse("EMPTY_CANDIDATE", "a candidate must name at least one envelope key")
    outside = sorted(set(candidate) - set(ranges))
    if outside:
        return _refuse(
            "KEY_OUTSIDE_ENVELOPE",
            f"{outside} are outside the {envelope} envelope {sorted(ranges)}",
        )
    none_keys = sorted(k for k, v in candidate.items() if v is None)
    if none_keys:
        return _refuse(
            "NONE_VALUE",
            f"{none_keys} carry None; omit a key the candidate does not vary",
        )
    for key, value in candidate.items():
        low, high = ranges[key]
        integral = isinstance(low, int) and isinstance(high, int)
        allowed = (int,) if integral else (int, float)
        if isinstance(value, bool) or not isinstance(value, allowed) or not low <= value <= high:
            return _refuse(
                "VALUE_OUTSIDE_RANGE",
                f"{key}={value!r} is outside [{low}, {high}]" + (" (integer)" if integral else ""),
            )
    if dict(candidate) == INCUMBENT:
        return _refuse("IDENTICAL_TO_INCUMBENT", f"the candidate equals the incumbent {INCUMBENT}")
    return Outcome(True, "OK", None, "candidate admitted", {"surfaces": sorted(candidate)})


def prior_answers_for(envelope: str) -> list[dict]:
    """The prior answers every experiment in ``envelope`` cites; #2082 is
    unconditional for retrieval parameters."""
    if envelope == "retrieval_parameters":
        return [dict(PRIOR_ANSWER_2082)]
    return []


# ---------------------------------------------------------------------------
# Rows and the lease
# ---------------------------------------------------------------------------


def _experiment(project_key: str, experiment_id: str):
    if not experiment_id:
        return None
    try:
        return ImprovementExperiment.query.get(project_key=project_key, id=experiment_id)
    except Exception:  # noqa: BLE001 -- an unkeyable id is a missing row
        return None


def _case(project_key: str, case_id: str | None):
    if not case_id:
        return None
    try:
        return ImprovementCase.query.get(project_key=project_key, id=case_id)
    except Exception:  # noqa: BLE001 -- an unkeyable id is a missing row
        return None


def _evaluation(project_key: str, evaluation_id: str):
    if not evaluation_id:
        return None
    try:
        return ImprovementEvaluation.query.get(project_key=project_key, id=evaluation_id)
    except Exception:  # noqa: BLE001 -- an unkeyable id is a missing row
        return None


def _lease(project_key: str, case_id: str) -> tuple[str, int | None]:
    """Take the case lease; ``(key, generation)``, generation ``None`` when held.

    The generation is minted by the lease exactly as
    ``scheduler_adapter._tick_one_case`` mints it, so a write fenced by it
    is never refused ``STALE_GENERATION`` for a stale copy.
    """
    from config.settings import settings
    from tools.improvement_control import keys
    from tools.improvement_control.lease import default_lease

    key = keys.lease_key(project_key, case_id)
    generation = default_lease().acquire(key, ttl=settings.improvement.lease_ttl_seconds)
    return key, generation


def _release(key: str, generation: int) -> None:
    from tools.improvement_control.lease import default_lease

    default_lease().release(key, generation)


def _project(project_key: str, case_id: str) -> None:
    from tools.improvement_control.projection import apply

    apply(project_key, case_id)


def _head_revision(project_key: str, case_id: str) -> int:
    from tools.improvement_control.journal import read_head

    head = read_head(project_key, case_id)
    return head.revision if head is not None else 0


def _case_state(project_key: str, case) -> str:
    from tools.improvement_control.journal import read_head

    head = read_head(project_key, case.id)
    if head is not None and head.state:
        return head.state
    return case.state


def _set_experiment_state(experiment, state: str):
    """The one door for ``ImprovementExperiment.state`` in this module and the
    save that carries every field set before it. The experiment lifecycle is
    a plain ORM field (lane 4's runner moves it the same way); only the case
    lifecycle goes through the journal."""
    experiment.state = state
    return experiment.save()


def experiment_notes(experiment) -> dict:
    """The experiment's ``notes`` JSON as a dict (``{}`` when absent)."""
    parsed = _load_json(getattr(experiment, "notes", None), {})
    return parsed if isinstance(parsed, dict) else {}


# ---------------------------------------------------------------------------
# Propose
# ---------------------------------------------------------------------------


def propose_experiment(
    project_key: str,
    case_id: str,
    *,
    hypothesis: str,
    mechanism: str,
    falsifier: str,
    candidate: dict,
    envelope: str = "retrieval_parameters",
) -> Outcome:
    """Create the ``proposed`` experiment and journal ``hypothesis_proposed``.

    Refuses the :func:`validate_candidate` codes, ``INCOMPLETE_HYPOTHESIS``
    (an empty hypothesis, mechanism, or falsifier), ``CASE_NOT_FOUND``,
    ``CASE_BUSY``, and the journal's own reason. ``candidate_surfaces`` holds
    the envelope keys the candidate varies; ``notes`` holds the prior
    answers, the candidate, and the envelope until the freeze writes the
    manifest.
    """
    admitted = validate_candidate(candidate, envelope=envelope)
    if not admitted.accepted:
        return admitted
    if not all((text or "").strip() for text in (hypothesis, mechanism, falsifier)):
        return _refuse(
            "INCOMPLETE_HYPOTHESIS", "hypothesis, mechanism, and falsifier are all required"
        )
    case = _case(project_key, case_id)
    if case is None:
        return _refuse("CASE_NOT_FOUND", f"no case {case_id} in project {project_key}")

    from tools.improvement_control.journal import transition

    payload = {
        "hypothesis": hypothesis,
        "mechanism": mechanism,
        "falsifier": falsifier,
        "candidate": candidate,
        "envelope": envelope,
    }
    key, generation = _lease(project_key, case_id)
    if generation is None:
        return _refuse("CASE_BUSY", f"case {case_id} lease is held")
    try:
        result = transition(
            project_key,
            case_id,
            expected_revision=_head_revision(project_key, case_id),
            generation=generation,
            event="hypothesis_proposed",
            payload_digest=_digest(payload),
        )
        if not result.accepted:
            return _refuse(result.reason, f"journal refused hypothesis_proposed: {result.reason}")
        row = ImprovementExperiment.create(
            project_key=project_key,
            created_at=_now(),
            state="proposed",
            case_id=case_id,
            hypothesis=hypothesis,
            mechanism=mechanism,
            falsifier=falsifier,
            candidate_surfaces=json.dumps(admitted.extra["surfaces"]),
            notes=json.dumps(
                {
                    "prior_answers": prior_answers_for(envelope),
                    "candidate": candidate,
                    "envelope": envelope,
                },
                sort_keys=True,
            ),
        )
    finally:
        _release(key, generation)
    _project(project_key, case_id)
    return Outcome(True, "OK", row.id, "proposed", {"revision": result.revision})


def latest_proposed_experiment(project_key: str, case_id: str):
    """The newest ``proposed`` experiment on the case, or ``None``."""
    rows = [
        e
        for e in ImprovementExperiment.query.filter(project_key=project_key, state="proposed")
        if getattr(e, "case_id", None) == case_id
    ]
    if not rows:
        return None
    rows.sort(key=lambda e: (getattr(e, "created_at", None) or _now()).isoformat())
    return rows[-1]


# ---------------------------------------------------------------------------
# Known-item records
# ---------------------------------------------------------------------------


def parse_known_item_records(jsonl_text: str) -> tuple[list[KnownItemRecord], int]:
    """Parse an export's body lines into records; return ``(records, skipped)``.

    Line one is the manifest and is never a record. A line that is not JSON,
    not an object, or lacks ``memory_id`` or ``content`` is skipped and
    counted rather than raised: the builder samples from what parsed.
    """
    records: list[KnownItemRecord] = []
    skipped = 0
    for line in (jsonl_text or "").splitlines()[1:]:
        if not line.strip():
            continue
        try:
            body = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        values = body.get("values") if isinstance(body, dict) else None
        if not isinstance(values, dict):
            skipped += 1
            continue
        memory_id = values.get("memory_id")
        content = values.get("content")
        if not memory_id or not isinstance(content, str):
            skipped += 1
            continue
        try:
            importance = float(values.get("importance") or 1.0)
        except (TypeError, ValueError):
            importance = 1.0
        records.append(KnownItemRecord(str(memory_id), content, importance))
    return records, skipped


def known_item_records(export) -> list[KnownItemRecord]:
    """The records ``build_known_item_set`` samples, from ``export.jsonl_text``."""
    records, skipped = parse_known_item_records(getattr(export, "jsonl_text", "") or "")
    if skipped:
        logger.warning("[improvement] known-item records: %d export line(s) skipped", skipped)
    return records


# ---------------------------------------------------------------------------
# Freeze
# ---------------------------------------------------------------------------


def _rejected_identity_peers(project_key: str, case) -> list[str]:
    """Rejected cases sharing this case's ``dedup_identity`` (step 1)."""
    identity = getattr(case, "dedup_identity", None) or ""
    if not identity:
        return []
    return sorted(
        other.id
        for other in ImprovementCase.query.filter(project_key=project_key, state="rejected")
        if other.id != case.id and (other.dedup_identity or "") == identity
    )


def _revision_in_force(project_key: str) -> str | None:
    from models.improvement_model_revision import ImprovementModelRevision

    rows = list(ImprovementModelRevision.query.filter(project_key=project_key, state="current"))
    if not rows:
        return None
    rows.sort(key=lambda r: (getattr(r, "created_at", None) or _now()).isoformat())
    return rows[-1].id


def freeze_experiment(
    project_key: str,
    experiment_id: str,
    *,
    n_queries: int = DEFAULT_N_QUERIES,
    seed: int = DEFAULT_SEED,
    builder=None,
    exporter=None,
    baseline=None,
    store=None,
    meter=None,
) -> Outcome:
    """Freeze a ``proposed`` experiment into a contract, in the plan's order.

    1. Novelty check against ``rejected`` cases sharing the case's
       ``dedup_identity`` (``REJECTED_IDENTITY``).
    2. The prior answer, unconditional for the envelope (#2082).
    3. ``export_corpus``, then :func:`known_item_records` from its body.
    4. ``build_known_item_set`` under the ``known_item_generation``
       reservation (``UNIT2_UNAVAILABLE`` on a refusal); a builder failure is
       ``BUILDER_FAILED``; fewer than :data:`MIN_QUERIES` queries is
       ``KNOWN_ITEM_SHORTFALL`` naming produced versus requested.
    5. ``capture_baseline`` with the incumbent exactly :data:`INCUMBENT`
       (``BASELINE_FAILED`` on an exception).
    6. The protocol, with ``batch_size = len(queries)`` set after generation.
    7. ``freeze_protocol``, the manifest with ``base_revision`` and
       ``candidate_ref``, ``contract_digest``, ``state="frozen"``,
       ``frozen_at``, ``model_revision_id``, ``charter_version``, and the
       ``experiment_frozen`` event under the case lease.

    Every refusal before step 7 leaves the experiment ``proposed`` with no
    protocol written. ``builder``, ``exporter``, ``baseline``, ``store``,
    and ``meter`` are injection points for tests.
    """
    from models.improvement_charter import ImprovementCharter
    from tools.improvement_control.journal import set_state, transition

    experiment = _experiment(project_key, experiment_id)
    if experiment is None:
        return _refuse("EXPERIMENT_NOT_FOUND", f"no experiment {experiment_id}")
    if experiment.state != "proposed":
        return _refuse(
            "EXPERIMENT_NOT_PROPOSED",
            f"experiment {experiment_id} is {experiment.state!r}, not 'proposed'",
            experiment_id,
        )
    case = _case(project_key, getattr(experiment, "case_id", None))
    if case is None:
        return _refuse("CASE_NOT_FOUND", f"experiment {experiment_id} names no case", experiment_id)
    notes = experiment_notes(experiment)
    envelope = notes.get("envelope") or "retrieval_parameters"
    candidate = notes.get("candidate")
    admitted = validate_candidate(candidate, envelope=envelope)
    if not admitted.accepted:
        return Outcome(False, admitted.reason, experiment_id, admitted.message)
    meter = meter if meter is not None else paid_inference_meter

    # 1. Novelty.
    peers = _rejected_identity_peers(project_key, case)
    if peers:
        return _refuse(
            "REJECTED_IDENTITY",
            f"rejected case(s) {peers} share dedup identity {case.dedup_identity!r}",
            experiment_id,
        )
    # 2. Prior answers.
    prior_answers = prior_answers_for(envelope)

    # 3. Corpus export and the records the builder samples.
    try:
        export = (exporter or export_corpus)(project_key)
    except Exception as exc:  # noqa: BLE001 -- an export failure is a refusal
        return _refuse("EXPORT_FAILED", f"export_corpus failed: {exc}", experiment_id)
    records = known_item_records(export)

    # 4. Known-item generation, metered.
    reservation = meter.reserve(
        project_key,
        n_queries * KNOWN_ITEM_PRICE_ESTIMATE_USD,
        purpose=RESERVATION_KNOWN_ITEMS,
        case_id=case.id,
    )
    if isinstance(reservation, meter.Refusal):
        return _refuse(
            "UNIT2_UNAVAILABLE",
            f"known-item generation refused by the meter: {reservation.reason}",
            experiment_id,
        )
    try:
        items = (builder or build_known_item_set)(records, n_queries=n_queries, seed=seed)
    except Exception as exc:  # noqa: BLE001 -- the builder's failure is a refusal
        meter.release(project_key, reservation.reservation_id)
        return _refuse("BUILDER_FAILED", f"build_known_item_set failed: {exc}", experiment_id)
    items = list(items or [])
    if items:
        meter.settle(
            project_key,
            reservation.reservation_id,
            len(items) * KNOWN_ITEM_PRICE_ESTIMATE_USD,
            metering="estimated",
        )
    else:
        meter.release(project_key, reservation.reservation_id)
    queries = [
        {"trial_id": f"q{i:03d}", "query_text": item.query, "gold_id": str(item.gold_memory_id)}
        for i, item in enumerate(items)
    ]
    if len(queries) < MIN_QUERIES:
        return _refuse(
            "KNOWN_ITEM_SHORTFALL",
            f"{len(queries)} known-item queries produced of {n_queries} requested; "
            f"the minimum is {MIN_QUERIES}",
            experiment_id,
        )

    # 5. Baseline on the incumbent.
    incumbent = dict(INCUMBENT)
    try:
        recorded = (baseline or capture_baseline)(
            project_key, queries, incumbent=incumbent, export=export
        )
    except Exception as exc:  # noqa: BLE001 -- an arm failure is a refusal
        return _refuse("BASELINE_FAILED", f"capture_baseline failed: {exc}", experiment_id)

    # 6. The protocol; batch_size from the queries produced, never the request.
    protocol = {
        "batch_size": len(queries),
        "endpoints": list(ENDPOINTS),
        "thresholds": {k: dict(v) for k, v in THRESHOLDS.items()},
        "holdout_partition": f"known-item-{seed}",
        "queries": queries,
        "baseline": recorded,
        "incumbent": incumbent,
        "candidate": dict(candidate),
        "infra_failure_cap": INFRA_FAILURE_CAP,
    }

    # 7. Freeze under the case lease.
    key, generation = _lease(project_key, case.id)
    if generation is None:
        return _refuse("CASE_BUSY", f"case {case.id} lease is held", experiment_id)
    try:
        protocol_ref = freeze_protocol(protocol, store=store)
        sha = _checkout_sha()
        manifest = {
            "protocol_ref": protocol_ref,
            "base_revision": sha,
            "candidate_ref": sha,
            "candidate": dict(candidate),
            "incumbent": incumbent,
            "envelope": envelope,
            "corpus_digest": getattr(export, "digest", None),
        }
        experiment.manifest = json.dumps(manifest, sort_keys=True)
        contract_digest = compute_contract_digest(experiment)
        result = transition(
            project_key,
            case.id,
            expected_revision=_head_revision(project_key, case.id),
            generation=generation,
            event="experiment_frozen",
            payload_digest=contract_digest,
            artifact_ref=protocol_ref,
        )
        if not result.accepted:
            return _refuse(
                result.reason, f"journal refused experiment_frozen: {result.reason}", experiment_id
            )
        pinned = ImprovementCharter.pinned(project_key)
        experiment.contract_digest = contract_digest
        experiment.frozen_at = _now()
        experiment.model_revision_id = _revision_in_force(project_key)
        experiment.charter_version = (
            int(getattr(pinned, "version", 0) or 0) if pinned is not None else None
        )
        notes["prior_answers"] = prior_answers
        notes["frozen"] = {
            "n_queries_requested": n_queries,
            "n_queries_produced": len(queries),
            "seed": seed,
            "records_available": len(records),
        }
        experiment.notes = json.dumps(notes, sort_keys=True)
        if _set_experiment_state(experiment, "frozen") is False:
            return _refuse(
                "SAVE_FAILED", "ImprovementExperiment.save() returned False", experiment_id
            )
        if _case_state(project_key, case) in ("observed", "investigating"):
            moved = set_state(
                project_key, case.id, generation=generation, state="experimenting", by="freeze"
            )
            if not moved.accepted:
                logger.warning(
                    "[improvement] case %s stays put: set_state refused %s", case.id, moved.reason
                )
    finally:
        _release(key, generation)
    _project(project_key, case.id)
    return Outcome(
        True,
        "OK",
        experiment_id,
        "frozen",
        {
            "contract_digest": contract_digest,
            "protocol_ref": protocol_ref,
            "n_queries": len(queries),
            "revision": result.revision,
        },
    )


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------


def _slot_held(project_key: str, case_id: str) -> tuple[bool, str]:
    """True when no session id is set (an operator at a terminal is
    break-glass) or when a ``running`` intent on the case names it."""
    session_id = os.environ.get("AGENT_SESSION_ID")
    if not session_id:
        return True, "break-glass: no AGENT_SESSION_ID"
    from tools.improvement_control.intents import list_intents

    for intent in list_intents(project_key, case_id):
        if intent.state == "running" and intent.agent_session_id == session_id:
            return True, intent.action_id
    return False, session_id


def _move_case(project_key: str, case_id: str, state: str, *, by: str) -> Outcome:
    key, generation = _lease(project_key, case_id)
    if generation is None:
        return _refuse("CASE_BUSY", f"case {case_id} lease is held")
    try:
        from tools.improvement_control.journal import set_state

        moved = set_state(project_key, case_id, generation=generation, state=state, by=by)
        if not moved.accepted:
            return _refuse(moved.reason, f"set_state({state}) refused: {moved.reason}")
    finally:
        _release(key, generation)
    _project(project_key, case_id)
    return Outcome(True, "OK", None, state)


def evaluate_experiment(
    project_key: str,
    experiment_id: str,
    *,
    meter=None,
    judges=None,
    require_slot: bool = True,
    store=None,
) -> Outcome:
    """Reserve the judge spend, move the case to ``evaluating``, run lane 4's
    ``evaluate``, settle, and apply the verdict.

    Refuses ``EXPERIMENT_NOT_FOUND``, ``EXPERIMENT_NOT_FROZEN``,
    ``CASE_NOT_FOUND``, ``SLOT_NOT_HELD``, ``PROTOCOL_UNREADABLE``,
    ``UNIT2_UNAVAILABLE``, and ``CASE_BUSY``, each before any judge is paid.
    ``SLOT_NOT_HELD`` applies only when ``AGENT_SESSION_ID`` is set and no
    ``running`` intent on the case names that session; an operator at a
    terminal with no session id is break-glass and admitted. Lane 4's runner
    owns every failure past that point: this function catches nothing of
    its own and writes no ``ImprovementEvaluation``.
    """
    from tools.improvement_eval import runner

    meter = meter if meter is not None else paid_inference_meter
    experiment = _experiment(project_key, experiment_id)
    if experiment is None:
        return _refuse("EXPERIMENT_NOT_FOUND", f"no experiment {experiment_id}")
    if experiment.state != "frozen":
        return _refuse(
            "EXPERIMENT_NOT_FROZEN",
            f"experiment {experiment_id} is {experiment.state!r}, not 'frozen'",
            experiment_id,
        )
    case = _case(project_key, getattr(experiment, "case_id", None))
    if case is None:
        return _refuse("CASE_NOT_FOUND", f"experiment {experiment_id} names no case", experiment_id)
    if require_slot:
        held, detail = _slot_held(project_key, case.id)
        if not held:
            return _refuse(
                "SLOT_NOT_HELD",
                f"no running intent on case {case.id} names session {detail}",
                experiment_id,
            )
    try:
        protocol = load_protocol(experiment, store=store)
    except InfraFailure as exc:
        return _refuse("PROTOCOL_UNREADABLE", str(exc), experiment_id)
    n_queries = len(protocol.get("queries") or [])

    reservation = meter.reserve(
        project_key,
        max(n_queries, 1) * 2 * JUDGE_PRICE_ESTIMATE_USD,
        purpose=RESERVATION_JUDGES,
        case_id=case.id,
    )
    if isinstance(reservation, meter.Refusal):
        return _refuse(
            "UNIT2_UNAVAILABLE",
            f"judge calls refused by the meter: {reservation.reason}",
            experiment_id,
        )
    moved = _move_case(project_key, case.id, "evaluating", by="evaluate_experiment")
    if not moved.accepted:
        meter.release(project_key, reservation.reservation_id)
        return Outcome(False, moved.reason, experiment_id, moved.message)

    evaluation = None
    try:
        evaluation = runner.evaluate(experiment_id, project_key, judges=judges, store=store)
    finally:
        trials = 0
        if evaluation is not None:
            try:
                trials = int(getattr(evaluation, "trials", 0) or 0)
            except (TypeError, ValueError):
                trials = 0
        if trials > 0:
            meter.settle(
                project_key,
                reservation.reservation_id,
                trials * 2 * JUDGE_PRICE_ESTIMATE_USD,
                metering="estimated",
            )
        else:
            meter.release(project_key, reservation.reservation_id)

    applied = apply_verdict(project_key, evaluation.id)
    return Outcome(
        True,
        "OK",
        experiment_id,
        "evaluated",
        {
            "evaluation_id": evaluation.id,
            "verdict": _verdict_of(evaluation),
            "applied": applied.as_dict(),
        },
    )


# ---------------------------------------------------------------------------
# Apply verdict
# ---------------------------------------------------------------------------


def _verdict_of(evaluation) -> str:
    if getattr(evaluation, "state", None) == "invalidated":
        return "invalidated"
    return getattr(evaluation, "verdict", None) or "inconclusive"


def _rationale(evaluation) -> str:
    """The runner's one-line rationale: the last note line that is not the
    calibration record."""
    lines = [
        line.strip()
        for line in (getattr(evaluation, "notes", None) or "").splitlines()
        if line.strip() and not line.startswith(CALIBRATION_NOTE_PREFIX)
    ]
    if lines:
        return lines[-1]
    return f"verdict {_verdict_of(evaluation)}"


def apply_verdict(project_key: str, evaluation_id: str) -> Outcome:
    """Move the case per Data Flow step 6 and record the evaluation on it.

    Order: the ``verdict_applied`` event (its ``payload_digest`` names the
    evaluation id and the verdict), then ``set_state`` for a verdict that
    moves the case, both under one lease-minted generation; then
    ``projection.apply``; then the plain ``save()`` of ``evaluation_ids`` and
    ``rejected_reason``. ``infra_failure`` and ``invalidated`` leave the case
    where it was, mark the experiment ``aborted``, and open a ``probe``
    investigation naming the failure. An evaluation already in
    ``evaluation_ids`` is ``ALREADY_APPLIED`` and nothing is written.
    """
    from tools.improvement_control.journal import set_state, transition

    evaluation = _evaluation(project_key, evaluation_id)
    if evaluation is None:
        return _refuse("EVALUATION_NOT_FOUND", f"no evaluation {evaluation_id}")
    experiment = _experiment(project_key, getattr(evaluation, "experiment_id", None))
    if experiment is None:
        return _refuse(
            "EXPERIMENT_NOT_FOUND", f"evaluation {evaluation_id} names no experiment on record"
        )
    case = _case(project_key, getattr(experiment, "case_id", None))
    if case is None:
        return _refuse("CASE_NOT_FOUND", f"experiment {experiment.id} names no case", experiment.id)
    applied_ids = _load_json(getattr(case, "evaluation_ids", None), [])
    if not isinstance(applied_ids, list):
        applied_ids = []
    if evaluation.id in applied_ids:
        return _refuse(
            "ALREADY_APPLIED",
            f"evaluation {evaluation.id} is already on case {case.id}",
            experiment.id,
        )
    verdict = _verdict_of(evaluation)
    if verdict not in VERDICT_TO_CASE_STATE:
        return _refuse("UNKNOWN_VERDICT", f"verdict {verdict!r} is not one this lane applies")
    target = VERDICT_TO_CASE_STATE[verdict]
    rationale = _rationale(evaluation)

    key, generation = _lease(project_key, case.id)
    if generation is None:
        return _refuse("CASE_BUSY", f"case {case.id} lease is held", experiment.id)
    try:
        result = transition(
            project_key,
            case.id,
            expected_revision=_head_revision(project_key, case.id),
            generation=generation,
            event="verdict_applied",
            payload_digest=f"evaluation:{evaluation.id}:{verdict}",
        )
        if not result.accepted:
            return _refuse(
                result.reason, f"journal refused verdict_applied: {result.reason}", experiment.id
            )
        if target is not None:
            moved = set_state(
                project_key, case.id, generation=generation, state=target, by="apply_verdict"
            )
            if not moved.accepted:
                return _refuse(
                    moved.reason, f"set_state({target}) refused: {moved.reason}", experiment.id
                )
    finally:
        _release(key, generation)
    _project(project_key, case.id)

    case = _case(project_key, case.id)
    applied_ids = _load_json(getattr(case, "evaluation_ids", None), [])
    if not isinstance(applied_ids, list):
        applied_ids = []
    applied_ids.append(evaluation.id)
    case.evaluation_ids = json.dumps(applied_ids)
    if verdict == "reject":
        case.rejected_reason = rationale
    case.save()

    extra: dict[str, Any] = {
        "verdict": verdict,
        "evaluation_id": evaluation.id,
        "case_id": case.id,
        "case_state": case.state,
    }
    if target is None:
        if experiment.state != "aborted":
            _set_experiment_state(experiment, "aborted")
        from tools.improvement_investigations import open_investigation

        probe = open_investigation(
            project_key,
            kind="probe",
            case_id=case.id,
            uncertainty=(
                f"evaluation {evaluation.id} of experiment {experiment.id} ended in {verdict}"
            ),
            query=f"probe the {verdict} of experiment {experiment.id}: {rationale}",
            decision_affected=f"whether experiment {experiment.id} can be repaired and retried",
            expected_information_value="the cause of the harness failure and its repair",
        )
        extra["investigation_id"] = probe.investigation_id
        if not probe.accepted:
            extra["findings"] = [f"probe investigation refused: {probe.reason}"]
    return Outcome(True, "OK", experiment.id, f"applied {verdict}", extra)


# ---------------------------------------------------------------------------
# Repair and show
# ---------------------------------------------------------------------------


def repair(project_key: str, experiment_id: str) -> Outcome:
    """Return a ``running`` or ``aborted`` experiment to ``frozen`` (lane 4's
    Race 1b repair) so Gate 0 admits a retry."""
    try:
        record = repair_wedged_experiment(project_key=project_key, experiment_id=experiment_id)
    except LookupError as exc:
        return _refuse("EXPERIMENT_NOT_FOUND", str(exc))
    return Outcome(True, "OK", experiment_id, "repaired", {"state": record.state})


def latest_evaluation(project_key: str, experiment_id: str):
    """The newest evaluation of the experiment across both states, or ``None``."""
    rows = []
    for state in ("complete", "invalidated", "pending"):
        rows.extend(
            e
            for e in ImprovementEvaluation.query.filter(project_key=project_key, state=state)
            if getattr(e, "experiment_id", None) == experiment_id
        )
    if not rows:
        return None
    rows.sort(key=lambda e: (getattr(e, "created_at", None) or _now()).isoformat())
    return rows[-1]


def show_experiment(project_key: str, experiment_id: str) -> dict:
    """State, the latest evaluation's verdict, and the notes of both.

    Raises ``LookupError`` when no such experiment exists. An ``aborted``
    experiment's dict carries the evaluation notes naming the infra failure.
    """
    experiment = _experiment(project_key, experiment_id)
    if experiment is None:
        raise LookupError(f"EXPERIMENT_NOT_FOUND: no experiment {experiment_id}")
    notes = experiment_notes(experiment)
    evaluation = latest_evaluation(project_key, experiment_id)
    manifest: dict | None
    try:
        manifest_text = read_content(experiment, "manifest")
        manifest = json.loads(manifest_text) if manifest_text else None
    except Exception as exc:  # noqa: BLE001 -- an unverifiable manifest is reported, not raised
        manifest = {"unverifiable": str(exc)}
    frozen_at = getattr(experiment, "frozen_at", None)
    return {
        "experiment_id": experiment.id,
        "case_id": getattr(experiment, "case_id", None),
        "state": experiment.state,
        "hypothesis": experiment.hypothesis,
        "mechanism": experiment.mechanism,
        "falsifier": experiment.falsifier,
        "candidate_surfaces": _load_json(experiment.candidate_surfaces, []),
        "contract_digest": experiment.contract_digest,
        "frozen_at": frozen_at.isoformat() if isinstance(frozen_at, datetime) else frozen_at,
        "model_revision_id": getattr(experiment, "model_revision_id", None),
        "charter_version": getattr(experiment, "charter_version", None),
        "manifest": manifest,
        "prior_answers": notes.get("prior_answers", []),
        "frozen": notes.get("frozen"),
        "evaluation_id": evaluation.id if evaluation is not None else None,
        "evaluation_state": getattr(evaluation, "state", None),
        "verdict": _verdict_of(evaluation) if evaluation is not None else None,
        "trials": getattr(evaluation, "trials", None),
        "notes": getattr(evaluation, "notes", None) or "",
    }


def render_show(shown: dict) -> str:
    lines = [
        f"experiment {shown['experiment_id']} case={shown['case_id'] or '-'} "
        f"state={shown['state']}",
        f"hypothesis: {shown['hypothesis'] or ''}",
        f"mechanism: {shown['mechanism'] or ''}",
        f"falsifier: {shown['falsifier'] or ''}",
        f"contract_digest: {shown['contract_digest'] or 'unfrozen'}",
        f"frozen_at: {shown['frozen_at'] or '-'}",
        f"prior_answers: {', '.join(p.get('ref', '?') for p in shown['prior_answers']) or 'none'}",
        f"evaluation: {shown['evaluation_id'] or 'none'} verdict={shown['verdict'] or '-'}"
        f" trials={shown['trials'] if shown['trials'] is not None else '-'}",
    ]
    if shown["notes"]:
        lines.append("notes:")
        lines.extend(f"  {line}" for line in shown["notes"].splitlines())
    return "\n".join(lines)
