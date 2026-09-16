"""The evaluation runner: the orchestration and the single writer of ``ImprovementEvaluation``.

One call, ``evaluate(experiment_id, project_key)``, composes every gate in the
order the plan fixes (``docs/plans/improvement-controller-lane-4-frozen-evaluation-inputs.md``,
Data Flow), exactly:

1. **Gate 0, contract re-check.** The contract digest is recomputed from the
   experiment record and compared to ``ImprovementExperiment.contract_digest``;
   a mismatch, or ``state != "frozen"``, ends the run as ``infra_failure``.
2. **Charter pin.** ``ImprovementCharter.pinned(project_key)`` supplies the
   digest written onto the evaluation and into every judge envelope, and the
   text the ``serves_charter`` judge quotes.
3. **Corpus export** and canonical digest (``corpus.py``).
4. **Arm assignment** with a recorded ``arm_assignment_digest`` (``blinding.py``).
5. **Arena spawn plus digest comparison**: two private Redis arms restored
   from the one export, each re-exporting and hashing its own corpus.
6. **Writer guard**, armed inside each arm worker after restore.
7. **Gate 1, incumbent baseline parity**: the incumbent reproduces the
   recorded baseline before the candidate arm is ever invoked -- ranked
   ids on retrieval protocols, per-task outcomes under the frozen
   tolerance on agent protocols (``agent`` mode).
8. **Paired trials** in the assigned order.
9. **Judges** on blinded envelopes, with the identity scan recorded, never
   suppressed.
10. **Statistics and Holm**, then the **stopping-rule** check.
11. **Verdict** and the single ``ImprovementEvaluation`` write.

Three disjoint handlers with no shared fall-through:

- :class:`~tools.improvement_eval.errors.InfraFailure` writes ``verdict="infra_failure"``.
  Its raise sites fall into six categories, each with a test: a Gate 0
  refusal (state or contract digest), an arm that would not spawn or whose
  worker broke (spawn failure, timeout, unparseable output, an escaped
  write, on either arm), unequal corpus digests between the arms and the
  export, a baseline parity miss, a judge provider that could not be
  reached (a ``skipped`` envelope), and an uncalibrated judge (a reference
  set below the floor). Both arms run the same worker code, so a
  candidate-side worker failure is harness breakage too: it counts toward
  ``infra_failure_cap`` exactly like an incumbent-side one and never
  scores against the candidate.
- :class:`~models.verifying_artifact_store.ArtifactIntegrityError` writes
  ``state="invalidated"`` and no verdict at all.
- Any other exception writes ``infra_failure`` with the exception type in ``notes``.

The experiment's ``state`` moves ``frozen`` -> ``running`` as the first write.
Popoto offers no compare-and-set, so the read-modify-write is a small race
(plan Race 1): the loser reads a state other than ``frozen``, writes an
``infra_failure`` evaluation naming the state it found, and touches nothing
else. A real lease belongs to lane 3 (#3215).

**Recovering a wedged experiment** (plan Race 1b). A crash between the
``running`` write and the verdict write leaves the experiment at
``state="running"`` and Gate 0 refuses every retry. Nothing here reclaims it
automatically, because the record carries no heartbeat to tell a live run
from a dead one. The repair is explicit, through the ORM, and this is the
same function the test executes::

    from tools.improvement_eval.runner import repair_wedged_experiment
    repair_wedged_experiment(project_key=project_key, experiment_id=experiment_id)

An ``infra_failure`` or invalidated run leaves the experiment ``aborted``
(the state the model reserves for "infra failure, never a result"); the same
repair returns it to ``frozen`` for a retry once the cause is fixed.

**The protocol artifact.** The experiment record carries the hypothesis,
mechanism, falsifier, surfaces, and the candidate manifest, and nothing
else; the endpoints, stopping rule, holdout query set, recorded baseline, and
per-arm retrieval parameters live in a *protocol* document frozen to the
verifying artifact store and referenced from the manifest as
``{"protocol_ref": "$CF:<sha256>:..."}``. The contract digest hashes the
manifest text, so it covers the protocol bytes transitively through the
reference's own digest, and a corrupted protocol surfaces as
``ArtifactIntegrityError`` like any other artifact. Keeping the protocol out
of the manifest is also what keeps blinding honest: ``blinding.identity_tokens``
treats every string in the manifest as candidate identity, and the holdout
queries and memory ids a judge legitimately sees must never read as a leak.
The scan reads the manifest's content, resolved through its store by
:func:`identity_source`, never the ``$CF:`` reference a queried row
hydrates. The candidate arm receives only ``query_text`` per trial (or
one task prompt per agent trial); the gold answers stay in this process.

**Calibration is recorded on the evaluation.** When the default roster
runs, the frozen reference set's artifact reference, digest, size, Cohen's
kappa, raw agreement, and position-swap consistency are written as one
``calibration: {...}`` JSON line in ``notes``; :func:`calibration_record`
reads it back.

Protocol shape (JSON)::

    {
      "batch_size": 2,
      "endpoints": ["recall_at_2", "mrr"],
      "thresholds": {"mrr": {"margin": 0.0, "alpha": 0.05}},
      "holdout_partition": "epoch-1",
      "queries": [{"trial_id": "t1", "query_text": "...", "gold_id": "<memory id>"}],
      "baseline": {"corpus_digest": "sha256:...", "ranked_ids": {"t1": ["...", "..."]}},
      "incumbent": {"limit": 10},
      "candidate": {"limit": 10},
      "infra_failure_cap": 0
    }

An agent protocol (``"mode": "agent"``) varies behavior, not parameters:
``tasks`` replaces ``queries``, the baseline stores per-task ``outcomes``
plus a ``tolerance`` (default ``{"kind": "pass_fail"}``), arm params name
the agent manifest (model, skill, persona, prompt hash, bounds), and each
endpoint names the rubric judge whose numeric ``score`` becomes the
trial's metric. Retrieval trials keep ranked-id scoring against gold ids,
untouched; agent trials dispatch the ``agent_run`` arm per trial and score
outcomes through the ``JudgeFn`` roster.

No Redis client is ever constructed here and the parent's pool is never
re-pointed: every arm read goes through ``arena.run_arm_job`` into a child
process whose own ``REDIS_URL`` names the arm socket.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from models.verifying_artifact_store import ArtifactIntegrityError
from tools.improvement_eval.blinding import (
    CANDIDATE_ARM,
    IDENTITY_FIELDS,
    INCUMBENT_ARM,
    ArmAssignment,
    assign_arms,
    scan_for_identity,
)
from tools.improvement_eval.correction import FixedBatchStoppingRule
from tools.improvement_eval.envelope import (
    EVALUATOR_VERSION,
    serialize_envelope,
    wrap_judge_envelope,
)
from tools.improvement_eval.errors import InfraFailure
from tools.improvement_eval.retrieval import RankedBaseline, baseline_parity
from tools.improvement_eval.statistics import EndpointThreshold, evaluate_family, winners
from tools.memory_eval.metrics import mrr, recall_at_k

logger = logging.getLogger(__name__)

#: The experiment fields whose normalized JSON is the frozen contract.
CONTRACT_FIELDS = ("hypothesis", "mechanism", "falsifier", "candidate_surfaces", "manifest")

#: ``ImprovementEvaluation.state`` values this runner writes.
STATE_COMPLETE = "complete"
STATE_INVALIDATED = "invalidated"

#: Verdicts, by name, so a typo cannot invent a fifth.
VERDICT_ACCEPT = "accept"
VERDICT_REJECT = "reject"
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_INFRA_FAILURE = "infra_failure"

#: Default harness-error budget per run: one errored trial is one too many.
DEFAULT_INFRA_FAILURE_CAP = 0

JudgeFn = Callable[..., dict]


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def _normalize_text(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\r\n", "\n")
    return value


def read_content(record: Any, name: str) -> Any:
    """Resolve a ``ContentField`` value to its content, verified on load.

    popoto hydrates a queried row lazily, so the attribute reads back as
    its ``$CF:`` store reference rather than as content. Resolving the
    reference through the field's own store is what re-hashes the bytes:
    a corrupted artifact raises ``ArtifactIntegrityError`` here.
    """
    value = getattr(record, name, None)
    if isinstance(value, str) and value.startswith("$CF:"):
        store = record._meta.fields[name].store
        return store.load(value).decode("utf-8")
    return value


def compute_contract_digest(experiment: Any) -> str:
    """``"sha256:<hex>"`` over the experiment's frozen contract fields.

    The same normalized-digest shape as ``tools/sdlc_verdict.py``: CRLF
    normalized, keys sorted, so the digest depends on what the contract
    says and never on how the bytes happened to be laid out. ``manifest``
    is resolved through its ``ContentField`` store and therefore verified.
    """
    payload = {name: _normalize_text(read_content(experiment, name)) for name in CONTRACT_FIELDS}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def freeze_protocol(protocol: dict, *, store=None) -> str:
    """Write a protocol document to the verifying store; return its reference.

    Content-addressed: the returned ``$CF:`` reference carries the sha256 of
    the canonical bytes, so a manifest that cites it pins the protocol.
    """
    from models.verifying_artifact_store import verifying_artifact_store

    target = store or verifying_artifact_store
    payload = json.dumps(protocol, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return target.save(
        payload, key=f"protocol-{digest[:16]}", model_class_name="ImprovementProtocol"
    )


def load_protocol(experiment: Any, *, store=None) -> dict:
    """Resolve the protocol the manifest cites, verified on load."""
    from models.verifying_artifact_store import verifying_artifact_store

    target = store or verifying_artifact_store
    manifest_text = read_content(experiment, "manifest")
    try:
        manifest = json.loads(manifest_text) if manifest_text else None
    except ValueError as exc:
        raise InfraFailure(f"manifest is not JSON: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("protocol_ref"), str):
        raise InfraFailure("manifest carries no 'protocol_ref'; the contract names no protocol")
    protocol = json.loads(target.load(manifest["protocol_ref"]).decode("utf-8"))
    if not isinstance(protocol, dict):
        raise InfraFailure("protocol artifact is not a JSON object")
    return protocol


# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------


def repair_wedged_experiment(*, project_key: str, experiment_id: str) -> Any:
    """Return a wedged experiment to ``frozen`` so Gate 0 admits a retry.

    The documented Race 1b repair, through the ORM. Returns the repaired
    record; raises ``LookupError`` when no such experiment exists.
    """
    from models.improvement_experiment import ImprovementExperiment

    record = ImprovementExperiment.query.filter(project_key=project_key, id=experiment_id).first()
    if record is None:
        raise LookupError(f"no ImprovementExperiment {experiment_id!r} under {project_key!r}")
    record.state = "frozen"
    record.save()
    return record


def has_verdict(evaluation: Any) -> bool:
    """True only for a completed evaluation; an invalidated row carries none."""
    return getattr(evaluation, "state", None) == STATE_COMPLETE


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def score_endpoint(name: str, ranked_ids: list[str], gold_id: str) -> float:
    """One endpoint's value for one trial. Unknown names are harness errors."""
    if name == "mrr":
        return mrr(gold_id, ranked_ids)
    if name.startswith("recall_at_"):
        try:
            k = int(name.removeprefix("recall_at_"))
        except ValueError as exc:
            raise InfraFailure(f"endpoint {name!r} names no integer k") from exc
        if k <= 0:
            raise InfraFailure(f"endpoint {name!r} needs k > 0")
        return recall_at_k(gold_id, ranked_ids, k)
    raise InfraFailure(f"unknown endpoint {name!r}; the harness measures 'mrr' and 'recall_at_<k>'")


def _thresholds(protocol: dict, endpoints: list[str]) -> dict[str, EndpointThreshold]:
    declared = protocol.get("thresholds") or {}
    resolved = {}
    for name in endpoints:
        raw = declared.get(name) or {}
        resolved[name] = EndpointThreshold(
            name=name,
            margin=float(raw.get("margin", EndpointThreshold.margin)),
            alpha=float(raw.get("alpha", EndpointThreshold.alpha)),
        )
    return resolved


# ---------------------------------------------------------------------------
# Arm jobs
# ---------------------------------------------------------------------------


@dataclass
class TrialResult:
    """One arm's answer for one trial."""

    trial_id: str
    arm: str
    ranked_ids: list[str]
    metrics: dict[str, float]
    #: Agent trials only: the arm's per-task outcome mapping. Retrieval
    #: trials leave this ``None`` and score ``metrics`` from gold ids.
    outcome: dict | None = None


#: Retrieval-parameter keys a ``retrieve`` protocol arm may name: the
#: retrieval-parameter envelope ``retrieve_memories`` accepts per call
#: (lane 5, #3217). ``retrieval_mode`` is an environment setting the arena
#: pins and is refused here. The worker also honors ``clock_skew_s``, which
#: is the clock-gap test's lever and is set only by
#: ``run_arm_job(clock_skew_s=...)``; a frozen protocol cannot name it, so
#: no contract input can skew a real arm's clock.
RETRIEVAL_PARAM_KEYS = frozenset({"limit", "rrf_k", "min_rrf_score"})

#: Agent-manifest keys an ``agent`` protocol arm may name: the session
#: identity the arm varies. Retrieval keys stay refused on agent protocols
#: and vice versa; each mode's validator rejects the other's keys.
AGENT_MANIFEST_KEYS = frozenset({"model", "skill", "persona", "prompt_hash"})

#: The full agent arm-param surface: the manifest plus the per-trial
#: ``bounds`` mapping the worker enforces.
AGENT_PARAM_KEYS = AGENT_MANIFEST_KEYS | frozenset({"bounds"})

#: Every protocol arm-param key that may reach an arm worker job spec, both
#: modes. Each mode's validator admits only its own slice of this union.
ARM_PARAM_KEYS = RETRIEVAL_PARAM_KEYS | AGENT_PARAM_KEYS

#: Agent-outcome keys that never reach a judge: the arm's own metadata, not
#: the session's work. The pre-judge scan still runs against the candidate
#: manifest, so output text naming the model, skill, or persona is recorded
#: as a leak; stripping the metadata keys keeps a hit a real finding about
#: the session's output, never construction noise.
AGENT_OUTCOME_ARM_KEYS = frozenset({"model", "scratch"})


def _validate_arm_params(arm_name: str, arm_params: dict, *, mode: str = "retrieval") -> None:
    """Reject protocol arm params the mode's arm does not accept, at load time.

    Run at protocol load (beside the ``endpoints`` check, before
    ``export_corpus`` and the arm spawn) so a contract-shape defect is a
    run-level ``InfraFailure`` unconditionally -- never a per-trial harness
    error whose outcome depends on ``infra_failure_cap``. ``_retrieve_job``
    and ``_agent_job`` still re-check per trial as the second line of
    defense.
    """
    allowed = AGENT_PARAM_KEYS if mode == "agent" else RETRIEVAL_PARAM_KEYS
    unknown = sorted(set(arm_params) - allowed)
    if unknown:
        raise InfraFailure(
            f"protocol {arm_name} params carry keys the arm does not accept "
            f"for mode {mode!r}: {unknown} (allowed: {sorted(allowed)})"
        )


def _validate_agent_manifest(arm_name: str, arm_params: dict) -> None:
    """Require the agent arm's session identity: a non-blank model.

    Skill, persona, and prompt hash ride along when the protocol names
    them. The worker refuses a missing model at job load; this refuses it
    at protocol load, before any arm spawns.
    """
    if not isinstance(arm_params.get("model"), str) or not arm_params["model"].strip():
        raise InfraFailure(f"protocol {arm_name} params need a non-blank 'model' for the agent arm")


def _retrieve_job(export, project_key: str, query: dict, arm_params: dict) -> dict:
    _validate_arm_params("arm", arm_params)
    return {
        "mode": "retrieve",
        "jsonl": export.jsonl_text,
        "project_key": project_key,
        "query_text": query["query_text"],
        **{k: arm_params[k] for k in RETRIEVAL_PARAM_KEYS if k in arm_params},
    }


def _run_arm(arm, export, project_key: str, query: dict, arm_params: dict) -> list[str]:
    from tools.improvement_eval.arena import run_arm_job

    response = run_arm_job(arm, project_key, _retrieve_job(export, project_key, query, arm_params))
    return [str(x) for x in response.get("ids", [])]


def capture_baseline(
    project_key: str,
    queries: list[dict],
    *,
    incumbent: dict | None = None,
    export=None,
) -> dict:
    """Record the incumbent's ranked ids on the frozen corpus, for the protocol.

    Runs the incumbent arm once per query in its own private Redis and
    returns ``{"corpus_digest", "ranked_ids"}`` in the shape
    ``protocol["baseline"]`` expects. An operator freezes this alongside the
    experiment; Gate 1 later demands the incumbent reproduce it.
    """
    from tools.improvement_eval.arena import arm_redis_server
    from tools.improvement_eval.corpus import export_corpus

    export = export or export_corpus(project_key)
    ranked: dict[str, list[str]] = {}
    with arm_redis_server() as arm:
        for query in queries:
            ranked[query["trial_id"]] = _run_arm(arm, export, project_key, query, incumbent or {})
    return {"corpus_digest": export.digest, "ranked_ids": ranked}


def _agent_job(export, project_key: str, task: dict, arm_params: dict) -> dict:
    """Build one single-task ``agent_run`` job spec from protocol arm params.

    The manifest carries the session identity (model, skill, persona,
    prompt hash); ``bounds`` travels beside it for the worker to enforce.
    """
    _validate_arm_params("arm", arm_params, mode="agent")
    bounds = arm_params.get("bounds") or {}
    if not isinstance(bounds, dict):
        raise InfraFailure(f"protocol arm 'bounds' must be a mapping, got {bounds!r}")
    return {
        "mode": "agent_run",
        "jsonl": export.jsonl_text,
        "project_key": project_key,
        "tasks": [{"id": task["id"], "prompt": task["prompt"]}],
        "manifest": {k: arm_params[k] for k in sorted(AGENT_MANIFEST_KEYS) if k in arm_params},
        "bounds": dict(bounds),
    }


def _run_agent_arm(
    arm, export, project_key: str, task: dict, arm_params: dict, *, arm_run_id=None, meter=None
) -> dict:
    """Run one agent trial on an arm; return its outcome mapping.

    With a meter and a frozen per-task spend cap, the task budget is
    reserved pre-trial (unit 2, ``arm:<arm_run_id>:<trial>``) and settled
    as estimated post-trial. A refused reservation is a harness error
    raised before anything spawns; a worker error leaves the reservation
    open for the reconcile pass and settles nothing.
    """
    from tools.improvement_eval.arena import run_arm_job

    trial_id = str(task.get("id", task.get("trial_id")))
    spend_cap = (arm_params.get("bounds") or {}).get("spend_cap")
    reservation_id = None
    if meter is not None and spend_cap is not None:
        case_id = f"arm:{arm_run_id}:{trial_id}" if arm_run_id else f"arm:{trial_id}"
        reservation = meter.reserve(project_key, spend_cap, purpose="agent_trial", case_id=case_id)
        reservation_id = getattr(reservation, "reservation_id", None)
        if reservation_id is None:
            raise InfraFailure(
                f"agent trial {trial_id} refused a unit-2 reservation: "
                f"{getattr(reservation, 'reason', 'unknown')}"
            )
    response = run_arm_job(arm, project_key, _agent_job(export, project_key, task, arm_params))
    trials = response.get("trials") or []
    if len(trials) != 1 or not isinstance(trials[0], dict):
        raise InfraFailure(
            f"agent arm returned {len(trials)} trial outcome(s) for task {task.get('id')!r}; "
            "expected exactly one outcome mapping"
        )
    if meter is not None and reservation_id is not None:
        meter.settle(project_key, reservation_id, spend_cap, metering="estimated")
    return trials[0]


def capture_agent_baseline(
    project_key: str,
    tasks: list[dict],
    *,
    incumbent: dict | None = None,
    export=None,
) -> dict:
    """Record the incumbent's per-task outcomes on the frozen corpus.

    The agent-mode sibling of :func:`capture_baseline`: runs the incumbent
    arm once per task in its own private Redis and returns
    ``{"corpus_digest", "outcomes"}`` in the shape an agent protocol's
    ``baseline`` expects. An operator freezes this alongside the experiment;
    Gate 1 later demands the incumbent agree with it under the frozen
    per-task tolerance.
    """
    from tools.improvement_eval.arena import arm_redis_server
    from tools.improvement_eval.corpus import export_corpus

    export = export or export_corpus(project_key)
    outcomes: dict[str, dict] = {}
    with arm_redis_server() as arm:
        for task in tasks:
            outcomes[str(task.get("id", task.get("trial_id")))] = _run_agent_arm(
                arm, export, project_key, task, incumbent or {}
            )
    return {"corpus_digest": export.digest, "outcomes": outcomes}


def _agent_baseline_agree(
    incumbent_outcome: dict, recorded_outcome: dict, tolerance: dict | None, *, trial_id: str = ""
) -> bool:
    """Agree the incumbent's outcome with its recorded baseline under tolerance.

    The default ``pass_fail`` kind compares the pass/fail bit per task; the
    named ``score_margin`` extension compares a numeric score within the
    frozen margin (``score_key`` defaults to ``"score"``). An unknown kind
    is a frozen-contract defect and fails the run, never a silent agree.
    """
    kind = (tolerance or {}).get("kind", "pass_fail")
    if kind == "pass_fail":
        return bool(incumbent_outcome.get("passed")) == bool(recorded_outcome.get("passed"))
    if kind == "score_margin":
        tolerance = tolerance or {}
        score_key = tolerance.get("score_key", "score")
        try:
            margin = float(tolerance.get("margin", 0.0))
            delta = abs(
                float(incumbent_outcome.get(score_key, 0.0))
                - float(recorded_outcome.get(score_key, 0.0))
            )
        except (TypeError, ValueError) as exc:
            raise InfraFailure(
                f"agent Gate 1: score_margin needs numeric {score_key!r} "
                f"on trial {trial_id!r}: {exc}"
            ) from exc
        return delta <= margin
    raise InfraFailure(f"agent Gate 1: unknown tolerance kind {kind!r} on trial {trial_id!r}")


def _load_agent_protocol(protocol: dict) -> tuple[list[dict], dict, dict]:
    """Read the agent trial inputs off a frozen protocol, verified at load.

    Returns ``(tasks, recorded_outcomes, tolerance)``. An agent protocol
    runs ``tasks``, never ``queries``: carrying both would run retrieval
    trials alongside agent trials, so ``queries`` here is refused outright.
    (The reverse is harmless -- retrieval simply never reads ``tasks`` --
    so it stays ignored like any other unknown protocol key.)
    """
    if "queries" in protocol:
        raise InfraFailure("agent protocol carries 'queries'; agent trials run 'tasks'")
    tasks = protocol.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise InfraFailure("agent protocol needs a non-empty 'tasks' list")
    for task in tasks:
        if not isinstance(task, dict):
            raise InfraFailure("agent protocol 'tasks' entries must be mappings")
        if not isinstance(task.get("id"), str) or not task["id"].strip():
            raise InfraFailure("agent protocol tasks need a non-blank 'id'")
        if not isinstance(task.get("prompt"), str) or not task["prompt"].strip():
            raise InfraFailure(f"agent protocol task {task.get('id')!r} needs a non-blank 'prompt'")
    baseline_spec = protocol.get("baseline") or {}
    outcomes = baseline_spec.get("outcomes")
    if not isinstance(outcomes, dict) or not outcomes:
        raise InfraFailure("agent protocol baseline needs a non-empty 'outcomes' mapping")
    missing = [str(task["id"]) for task in tasks if str(task["id"]) not in outcomes]
    if missing:
        raise InfraFailure(f"agent protocol baseline names no outcome for task(s) {missing}")
    tolerance = baseline_spec.get("tolerance") or {"kind": "pass_fail"}
    if not isinstance(tolerance, dict):
        raise InfraFailure("agent protocol baseline 'tolerance' must be a mapping")
    return tasks, outcomes, tolerance


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------


CALIBRATION_NOTE_PREFIX = "calibration: "


def calibration_note(result: Any) -> str:
    """The ``notes`` line that records a :class:`CalibrationResult` durably."""
    payload = {
        "artifact_ref": result.artifact_ref,
        "digest": result.digest,
        "size": result.size,
        "kappa": result.kappa,
        "raw_agreement": result.raw_agreement,
        "position_swap_consistency": result.position_swap_consistency,
    }
    return CALIBRATION_NOTE_PREFIX + json.dumps(payload, sort_keys=True)


def calibration_record(evaluation: Any) -> dict | None:
    """Read the calibration record back from an evaluation's ``notes``.

    ``None`` when the run never calibrated (an injected roster, or a run
    that failed before the judges were resolved).
    """
    for line in (getattr(evaluation, "notes", None) or "").splitlines():
        if line.startswith(CALIBRATION_NOTE_PREFIX):
            return json.loads(line[len(CALIBRATION_NOTE_PREFIX) :])
    return None


def default_judges(
    project_key: str, charter: Any, *, judge_complete=None
) -> tuple[list[JudgeFn], Any]:
    """The default roster: a calibrated ``serves_charter`` judge.

    Calibration runs first and raises :class:`InfraFailure` below the
    reference-set floor, so an uncalibrated judge never scores a trial.
    Returns the roster and the :class:`CalibrationResult` it was measured
    with, so the runner can record the numbers on the evaluation.
    ``judge_complete`` injects the provider transport (tests); the default
    routes per charter section 7.
    """
    from tools.improvement_eval.calibration import calibrate
    from tools.improvement_eval.judges.serves_charter import run_serves_charter_judge

    charter_text = read_content(charter, "text")
    charter_digest = charter.digest

    def _judge(candidate_output: str, *, blinded_arm_id: str, trial_id: str) -> dict:
        return run_serves_charter_judge(
            candidate_output,
            charter_text=charter_text,
            charter_digest=charter_digest,
            project_key=project_key,
            blinded_arm_id=blinded_arm_id,
            trial_id=trial_id,
            _complete=judge_complete,
        )

    def _calibration_probe(text: str, *, swapped: bool) -> str:
        envelope = _judge(text, blinded_arm_id="arm-b" if swapped else "arm-a", trial_id="cal")
        if envelope.get("status") != "ok":
            raise InfraFailure(f"calibration judge call skipped: {envelope.get('reason')}")
        return envelope["judge"]["verdict"]

    calibration = calibrate(project_key, _calibration_probe)
    return [_judge], calibration


@dataclass
class _JudgeOutcome:
    envelopes: list[dict] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    scans: int = 0


def identity_source(experiment: Any) -> dict[str, Any]:
    """The experiment's identity-bearing fields with content resolved.

    ``manifest`` is a ``ContentField``: a queried row hydrates it as its
    ``$CF:`` reference, and a scan over that reference would never see the
    branch name or files inside. Every field ``blinding.identity_tokens``
    reads is resolved through :func:`read_content` here, so the scan runs
    over what the manifest says.
    """
    return {name: read_content(experiment, name) for name in IDENTITY_FIELDS}


def _run_judges(
    *,
    judges: list[JudgeFn],
    experiment: Any,
    charter_digest: str,
    trials: list[tuple[TrialResult, TrialResult]],
    assignment: ArmAssignment,
    store,
    candidate_manifest: dict | None = None,
) -> _JudgeOutcome:
    """Score paired trials through the judge roster on blinded envelopes.

    ``candidate_manifest`` is the agent-mode identity carrier: when present,
    it rides the existing ``manifest`` IDENTITY_FIELD, so a model, skill, or
    persona string the session wrote into its output reads as a leak. The
    experiment manifest stays in the dict alongside it. A hit records
    ``blinded=False`` plus the note; it never fails the trial.
    """
    outcome = _JudgeOutcome()
    experiment_id = str(experiment.id)
    identity = identity_source(experiment)
    if candidate_manifest is not None:
        identity["manifest"] = [identity.get("manifest"), json.dumps(candidate_manifest)]
    for incumbent_result, candidate_result in trials:
        for result in (incumbent_result, candidate_result):
            blinded_arm_id = assignment.blinded_ids[result.arm]
            if result.outcome is not None:
                scrubbed = {
                    key: value
                    for key, value in result.outcome.items()
                    if key not in AGENT_OUTCOME_ARM_KEYS
                }
                judge_input = {
                    "trial_id": result.trial_id,
                    "blinded_arm_id": blinded_arm_id,
                    "outcome": scrubbed,
                }
            else:
                judge_input = {
                    "trial_id": result.trial_id,
                    "blinded_arm_id": blinded_arm_id,
                    "ranked_ids": result.ranked_ids,
                    "metrics": result.metrics,
                }
            candidate_output = json.dumps(judge_input, sort_keys=True)
            scan = scan_for_identity(candidate_output, identity)
            outcome.scans += 1
            if scan.leaked:
                outcome.leaks.extend(scan.hits)
            for judge in judges:
                envelope = judge(
                    candidate_output, blinded_arm_id=blinded_arm_id, trial_id=result.trial_id
                )
                if envelope.get("status") != "ok":
                    raise InfraFailure(
                        f"judge skipped on trial {result.trial_id!r}: {envelope.get('reason')}"
                    )
                raw_ref = store.save(
                    json.dumps(envelope, sort_keys=True).encode("utf-8"),
                    key=f"judge-{experiment_id}-{result.trial_id}-{blinded_arm_id}",
                    model_class_name="ImprovementJudgeResponse",
                )
                wrapped = wrap_judge_envelope(
                    envelope["judge"],
                    experiment_id=experiment_id,
                    contract_digest=experiment.contract_digest,
                    charter_digest=charter_digest,
                    trial_id=result.trial_id,
                    blinded_arm_id=blinded_arm_id,
                    raw_response_ref=raw_ref,
                )
                serialized = serialize_envelope(wrapped)
                envelope_scan = scan_for_identity(serialized, identity)
                outcome.scans += 1
                if envelope_scan.leaked:
                    outcome.leaks.extend(envelope_scan.hits)
                outcome.envelopes.append(wrapped)
    return outcome


def _run_agent_trials(
    *,
    ctx,
    export,
    project_key: str,
    tasks: list[dict],
    recorded_outcomes: dict,
    tolerance: dict,
    baseline_digest: str,
    incumbent_params: dict,
    candidate_params: dict,
    incumbent_arm,
    candidate_arm_server,
    candidate_agent_arm,
    assignment: ArmAssignment,
    harness_error,
    arm_run_id=None,
    meter=None,
) -> list[tuple[TrialResult, TrialResult]]:
    """Gate 1 plus paired trials for agent protocols.

    Gate 1 compares the incumbent's per-task outcomes against the recorded
    baseline under the frozen tolerance and requires all(agree), naming
    mismatching trial ids. The per-trial agreed bit lands in the evaluation
    notes next to the trial outcome the baseline record already stores. A
    worker error on either arm is a harness error exactly like retrieval:
    the trial is excluded and counted toward the cap. Paired trials then
    re-run the incumbent per task (drift within the run fails the run) and
    run the candidate through the injected arm.
    """
    if baseline_digest != export.digest:
        raise InfraFailure(
            f"agent Gate 1: baseline corpus digest {baseline_digest} differs from the "
            f"frozen {export.digest}; the corpus moved after the baseline was captured"
        )
    incumbent_outcomes: dict[str, dict] = {}
    agreement: dict[str, bool] = {}
    # Existing callers patch _run_agent_arm with five-arg fakes; spread the
    # spend kwargs only when metering is in play so those fakes keep working.
    arm_kwargs: dict = (
        {"arm_run_id": arm_run_id, "meter": meter}
        if (arm_run_id is not None or meter is not None)
        else {}
    )
    for task in tasks:
        trial_id = str(task["id"])
        try:
            outcome = _run_agent_arm(
                incumbent_arm, export, project_key, task, incumbent_params, **arm_kwargs
            )
        except InfraFailure as exc:
            harness_error(INCUMBENT_ARM, trial_id, exc)
            continue
        if not isinstance(outcome, dict):
            raise InfraFailure(
                f"agent Gate 1: incumbent outcome on trial {trial_id!r} is not a mapping"
            )
        recorded = recorded_outcomes.get(trial_id)
        if not isinstance(recorded, dict):
            raise InfraFailure(f"agent Gate 1: baseline names no outcome for trial {trial_id!r}")
        agreement[trial_id] = _agent_baseline_agree(outcome, recorded, tolerance, trial_id=trial_id)
        incumbent_outcomes[trial_id] = outcome
    if agreement:
        ctx.notes.append(
            f"agent baseline ({(tolerance or {}).get('kind', 'pass_fail')}): "
            + ", ".join(
                f"{task['id']}={'agree' if agreement.get(str(task['id'])) else 'differ'}"
                for task in tasks
            )
        )
    mismatched = sorted(trial_id for trial_id, agreed in agreement.items() if not agreed)
    if mismatched:
        raise InfraFailure(
            f"agent Gate 1: incumbent outcome differs from the recorded baseline on "
            f"trial(s) {mismatched} under tolerance {(tolerance or {}).get('kind', 'pass_fail')!r}"
        )
    paired: list[tuple[TrialResult, TrialResult]] = []
    for task in tasks:
        trial_id = str(task["id"])
        if trial_id not in incumbent_outcomes:
            continue
        incumbent_outcome = incumbent_outcomes[trial_id]
        candidate_outcome: dict | None = None
        for arm_name in assignment.run_order:
            if arm_name == INCUMBENT_ARM:
                again = _run_agent_arm(
                    incumbent_arm, export, project_key, task, incumbent_params, **arm_kwargs
                )
                if not _agent_baseline_agree(
                    again, incumbent_outcome, tolerance, trial_id=trial_id
                ):
                    raise InfraFailure(
                        f"incumbent outcome moved within the run on trial {trial_id!r}: "
                        f"{again} after {incumbent_outcome}"
                    )
                continue
            try:
                candidate_outcome = candidate_agent_arm(
                    candidate_arm_server, export, project_key, task, candidate_params
                )
            except InfraFailure as exc:
                harness_error(CANDIDATE_ARM, trial_id, exc)
                break
        if candidate_outcome is None:
            continue
        if not isinstance(candidate_outcome, dict):
            raise InfraFailure(f"candidate outcome on trial {trial_id!r} is not a mapping")
        paired.append(
            (
                TrialResult(
                    trial_id=trial_id,
                    arm=INCUMBENT_ARM,
                    ranked_ids=[],
                    metrics={},
                    outcome=incumbent_outcome,
                ),
                TrialResult(
                    trial_id=trial_id,
                    arm=CANDIDATE_ARM,
                    ranked_ids=[],
                    metrics={},
                    outcome=candidate_outcome,
                ),
            )
        )
    return paired


def _score_agent_trials(
    paired: list[tuple[TrialResult, TrialResult]],
    envelopes: list[dict],
    endpoints: list[str],
    assignment: ArmAssignment,
) -> None:
    """Fill each agent trial's metrics from the rubric-judge envelopes.

    Each protocol endpoint names the rubric judge that scores it: the
    envelope whose ``judge_id`` equals the endpoint carries its numeric
    ``score``. A roster that scores no endpoint is a contract mismatch and
    fails the run rather than scoring a silent zero. The default
    serves-charter judge never matches a rubric endpoint, so it stays a
    recorded leg without scoring.
    """
    by_key: dict[tuple, dict] = {}
    for envelope in envelopes:
        judge = envelope.get("judge")
        if not isinstance(judge, dict):
            continue
        by_key[
            (envelope.get("trial_id"), envelope.get("blinded_arm_id"), judge.get("judge_id"))
        ] = judge
    for incumbent_result, candidate_result in paired:
        for result in (incumbent_result, candidate_result):
            metrics: dict[str, float] = {}
            for endpoint in endpoints:
                key = (result.trial_id, assignment.blinded_ids[result.arm], endpoint)
                judge = by_key.get(key)
                if judge is None:
                    raise InfraFailure(
                        f"no judge scored endpoint {endpoint!r} on trial {result.trial_id!r}; "
                        "each agent endpoint names the rubric judge that scores it"
                    )
                score = judge.get("score")
                if isinstance(score, bool) or not isinstance(score, (int, float)):
                    raise InfraFailure(
                        f"judge {judge.get('judge_id')!r} returned no numeric 'score' "
                        f"for endpoint {endpoint!r} on trial {result.trial_id!r}"
                    )
                metrics[endpoint] = float(score)
            result.metrics = metrics


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------


def decide_verdict(outcomes, thresholds: dict[str, EndpointThreshold]) -> tuple[str, str]:
    """Map endpoint outcomes to a verdict and a one-line rationale.

    ``accept`` when every endpoint clears its margin, its Holm-adjusted
    alpha, and a CI lower bound above zero. ``reject`` when at least one
    endpoint's whole interval sits below its margin: the candidate
    measurably fails to clear it. Otherwise the measurement could not tell
    the arms apart: ``inconclusive``.
    """
    cleared = set(winners(outcomes, thresholds))
    failed = [o.name for o in outcomes if o.upper < thresholds[o.name].margin]
    if outcomes and len(cleared) == len(outcomes):
        return VERDICT_ACCEPT, f"all endpoints cleared: {sorted(cleared)}"
    if failed:
        return VERDICT_REJECT, f"endpoints below margin across the whole interval: {failed}"
    undetermined = sorted(o.name for o in outcomes if o.name not in cleared)
    return VERDICT_INCONCLUSIVE, f"endpoints not distinguished: {undetermined}"


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class _RunContext:
    experiment: Any
    project_key: str
    charter_digest: str | None = None
    arm_assignment_digest: str | None = None
    holdout_partition: str | None = None
    trials: int = 0
    effect: dict | None = None
    confidence_interval: dict | None = None
    correction: str | None = None
    judge_records: list[dict] = field(default_factory=list)
    blinded: bool | None = None
    notes: list[str] = field(default_factory=list)
    owns_experiment: bool = False
    verdict: str | None = None
    rationale: str | None = None


def _write_evaluation(ctx: _RunContext, *, state: str, verdict: str | None) -> Any:
    from models.improvement_evaluation import ImprovementEvaluation

    fields: dict[str, Any] = {
        "project_key": ctx.project_key,
        "created_at": datetime.now(UTC),
        "state": state,
        "experiment_id": str(ctx.experiment.id) if ctx.experiment is not None else None,
        "contract_digest": getattr(ctx.experiment, "contract_digest", None),
        "charter_digest": ctx.charter_digest,
        "evaluator_version": EVALUATOR_VERSION,
        "holdout_partition": ctx.holdout_partition,
        "blinded": ctx.blinded,
        "arm_assignment_digest": ctx.arm_assignment_digest,
        "trials": ctx.trials,
        "effect": json.dumps(ctx.effect, sort_keys=True) if ctx.effect is not None else None,
        "confidence_interval": (
            json.dumps(ctx.confidence_interval, sort_keys=True)
            if ctx.confidence_interval is not None
            else None
        ),
        "correction": ctx.correction,
        "judge_records": json.dumps(ctx.judge_records, sort_keys=True) if ctx.judge_records else "",
        "notes": "\n".join(ctx.notes) if ctx.notes else None,
    }
    if verdict is not None:
        fields["verdict"] = verdict
    evaluation = ImprovementEvaluation(**fields)
    if evaluation.save() is False:
        raise RuntimeError("ImprovementEvaluation.save() returned False")
    return evaluation


def _finish_experiment(ctx: _RunContext, state: str) -> None:
    if not ctx.owns_experiment:
        return
    ctx.experiment.state = state
    ctx.experiment.save()


def _load_experiment(project_key: str, experiment_id: str):
    from models.improvement_experiment import ImprovementExperiment

    record = ImprovementExperiment.query.filter(project_key=project_key, id=experiment_id).first()
    if record is None:
        raise InfraFailure(f"no ImprovementExperiment {experiment_id!r} under {project_key!r}")
    return record


def evaluate(
    experiment_id: str,
    project_key: str,
    *,
    judges: list[JudgeFn] | None = None,
    judge_complete=None,
    store=None,
    _candidate_arm=_run_arm,
    _candidate_agent_arm=_run_agent_arm,
) -> Any:
    """Run one frozen-input evaluation and write its single ``ImprovementEvaluation``.

    ``judges`` replaces the default roster (tests); ``judge_complete``
    injects the transport of the default ``serves_charter`` judge.
    ``_candidate_arm`` is the candidate arm invocation, exposed so the
    parity-gate test can prove the candidate was never called.
    ``_candidate_agent_arm`` is the agent-mode sibling: one agent trial per
    task, returning its outcome mapping.
    """
    from models.verifying_artifact_store import verifying_artifact_store

    store = store or verifying_artifact_store
    ctx = _RunContext(experiment=None, project_key=project_key)
    try:
        ctx.experiment = _load_experiment(project_key, experiment_id)
        _run_gates(
            ctx,
            judges=judges,
            judge_complete=judge_complete,
            store=store,
            candidate_arm=_candidate_arm,
            candidate_agent_arm=_candidate_agent_arm,
        )
    except InfraFailure as exc:
        logger.warning("evaluation infra_failure for %s: %s", experiment_id, exc)
        ctx.notes.append(f"infra_failure: {exc}")
        _finish_experiment(ctx, "aborted")
        return _write_evaluation(ctx, state=STATE_COMPLETE, verdict=VERDICT_INFRA_FAILURE)
    except ArtifactIntegrityError as exc:
        logger.error("evaluation invalidated for %s: %s", experiment_id, exc)
        ctx.notes.append(f"invalidated: {exc}")
        _finish_experiment(ctx, "aborted")
        return _write_evaluation(ctx, state=STATE_INVALIDATED, verdict=None)
    except Exception as exc:
        logger.exception("evaluation crashed for %s", experiment_id)
        ctx.notes.append(f"infra_failure: unexpected {type(exc).__name__}: {exc}")
        _finish_experiment(ctx, "aborted")
        return _write_evaluation(ctx, state=STATE_COMPLETE, verdict=VERDICT_INFRA_FAILURE)
    ctx.notes.append(ctx.rationale or "")
    _finish_experiment(ctx, "complete")
    return _write_evaluation(ctx, state=STATE_COMPLETE, verdict=ctx.verdict)


def _run_gates(
    ctx: _RunContext, *, judges, judge_complete, store, candidate_arm, candidate_agent_arm
) -> None:
    from models.improvement_charter import ImprovementCharter
    from tools.improvement_eval.arena import arm_redis_server
    from tools.improvement_eval.corpus import export_corpus

    experiment = ctx.experiment
    project_key = ctx.project_key

    # Gate 0: the contract is frozen and unaltered.
    found_state = getattr(experiment, "state", None)
    if found_state != "frozen":
        raise InfraFailure(
            f"Gate 0: experiment {experiment.id} is in state {found_state!r}, not 'frozen'"
        )
    recomputed = compute_contract_digest(experiment)
    if recomputed != experiment.contract_digest:
        raise InfraFailure(
            f"Gate 0: contract digest {experiment.contract_digest} does not match the "
            f"recomputed {recomputed}; the contract moved after freezing"
        )
    experiment.state = "running"
    experiment.save()
    ctx.owns_experiment = True

    # Charter pin.
    charter = ImprovementCharter.pinned(project_key)
    if charter is None:
        raise InfraFailure(f"no ImprovementCharter is pinned for {project_key!r}")
    ctx.charter_digest = charter.digest
    charter_text = read_content(charter, "text")
    if not charter_text:
        raise InfraFailure(f"pinned charter {charter.digest} has no text")

    # Protocol (verified on load) and the stopping rule it declares.
    protocol = load_protocol(experiment, store=store)
    queries = list(protocol.get("queries") or [])
    endpoints = list(protocol.get("endpoints") or [])
    if not endpoints:
        raise InfraFailure("protocol declares no endpoints")
    batch_size = int(protocol.get("batch_size") or 0)
    rule = FixedBatchStoppingRule(batch_size=batch_size, n_endpoints=len(endpoints))
    ctx.correction = rule.describe()
    ctx.holdout_partition = protocol.get("holdout_partition")
    thresholds = _thresholds(protocol, endpoints)
    baseline_spec = protocol.get("baseline") or {}
    baseline_ids = baseline_spec.get("ranked_ids") or {}
    incumbent_params = dict(protocol.get("incumbent") or {})
    candidate_params = dict(protocol.get("candidate") or {})
    mode = protocol.get("mode", "retrieval")
    if mode not in ("retrieval", "agent"):
        raise InfraFailure(f"unknown protocol mode {mode!r}; expected 'retrieval' or 'agent'")
    _validate_arm_params("incumbent", incumbent_params, mode=mode)
    _validate_arm_params("candidate", candidate_params, mode=mode)
    agent_tasks: list[dict] = []
    agent_recorded_outcomes: dict = {}
    agent_tolerance: dict = {"kind": "pass_fail"}
    if mode == "agent":
        _validate_agent_manifest("incumbent", incumbent_params)
        _validate_agent_manifest("candidate", candidate_params)
        agent_tasks, agent_recorded_outcomes, agent_tolerance = _load_agent_protocol(protocol)
    infra_cap = int(protocol.get("infra_failure_cap", DEFAULT_INFRA_FAILURE_CAP))

    # Corpus export, hashed once; both arms restore from these bytes.
    export = export_corpus(project_key, store=store)

    # Arm assignment, recorded before either arm runs.
    assignment = assign_arms(str(experiment.id))
    ctx.arm_assignment_digest = assignment.digest

    # Judges are resolved before the arms spawn so an uncalibrated judge
    # fails the run before any Redis is started. The default roster's
    # calibration numbers are recorded on the evaluation, not only logged.
    if judges is not None:
        roster = judges
    else:
        roster, calibration = default_judges(project_key, charter, judge_complete=judge_complete)
        ctx.notes.append(calibration_note(calibration))

    with arm_redis_server() as incumbent_arm, arm_redis_server() as candidate_arm_server:
        # Arena digest comparison: each arm re-exports and hashes its own corpus.
        from tools.improvement_eval.arena import run_arm_job

        digest_job = {"mode": "digest", "jsonl": export.jsonl_text, "project_key": project_key}
        arm_digests = [
            run_arm_job(incumbent_arm, project_key, digest_job),
            run_arm_job(candidate_arm_server, project_key, digest_job),
        ]
        for response in arm_digests:
            if response["digest"] != export.digest:
                detail = ""
                if response.get("manifest") != export.manifest_canon:
                    detail = (
                        f"; arm manifest {response.get('manifest')!r} differs from the "
                        f"export's {export.manifest_canon!r}"
                    )
                raise InfraFailure(
                    f"arm corpus digest {response['digest']} differs from the export's "
                    f"{export.digest}; the arms did not read the frozen corpus{detail}"
                )
        if arm_digests[0]["manifest"] != arm_digests[1]["manifest"]:
            raise InfraFailure("arm corpus manifests differ between arms")

        # Gate 1: the incumbent reproduces its recorded baseline before the
        # candidate arm is invoked at all. A worker error on either arm is a
        # harness error: the trial is excluded and counted toward the cap.
        # Both arms run the same worker code, so a candidate-side failure
        # is harness breakage by construction and never scores as a zero.
        # (Retrieval Gate 1; agent Gate 1 ran in the branch above.)
        baseline_digest = str(baseline_spec.get("corpus_digest"))
        incumbent_results: dict[str, TrialResult] = {}
        harness_errors = 0

        def _harness_error(arm_name: str, trial_id: str, exc: InfraFailure) -> None:
            nonlocal harness_errors
            harness_errors += 1
            ctx.notes.append(f"harness error on {arm_name} arm, trial {trial_id!r}: {exc}")
            if harness_errors > infra_cap:
                raise InfraFailure(
                    f"{harness_errors} harness-errored trial(s) exceed the cap of {infra_cap}"
                ) from exc

        # Agent trials run their own Gate 1 plus paired loop through the
        # injected arm. Retrieval protocols carry no tasks, and agent
        # protocols carry no queries (refused at load), so the retrieval
        # loops below are no-ops in agent mode.
        paired: list[tuple[TrialResult, TrialResult]] = []
        if mode == "agent":
            paired = _run_agent_trials(
                ctx=ctx,
                export=export,
                project_key=project_key,
                tasks=agent_tasks,
                recorded_outcomes=agent_recorded_outcomes,
                tolerance=agent_tolerance,
                baseline_digest=str(baseline_spec.get("corpus_digest")),
                incumbent_params=incumbent_params,
                candidate_params=candidate_params,
                incumbent_arm=incumbent_arm,
                candidate_arm_server=candidate_arm_server,
                candidate_agent_arm=candidate_agent_arm,
                assignment=assignment,
                harness_error=_harness_error,
            )

        for query in queries:
            trial_id = str(query.get("trial_id"))
            try:
                ids = _run_arm(incumbent_arm, export, project_key, query, incumbent_params)
            except InfraFailure as exc:
                _harness_error(INCUMBENT_ARM, trial_id, exc)
                continue
            recorded = RankedBaseline(
                ids=[str(x) for x in baseline_ids.get(trial_id, [])],
                corpus_digest=baseline_digest,
            )
            baseline_parity(ids, recorded, export.digest)
            incumbent_results[trial_id] = TrialResult(
                trial_id=trial_id,
                arm=INCUMBENT_ARM,
                ranked_ids=ids,
                metrics={e: score_endpoint(e, ids, str(query["gold_id"])) for e in endpoints},
            )
        if mode == "retrieval" and not queries:
            baseline_parity(
                [], RankedBaseline(ids=[], corpus_digest=baseline_digest), export.digest
            )

        # Paired trials: both arms run each input in the assigned order. The
        # incumbent's slot re-runs it and demands the Gate 1 ranking again,
        # so an incumbent that drifts within the run is caught, not averaged.
        # (Retrieval paired trials; agent pairs came from the branch above,
        # and this loop is a no-op in agent mode.)
        for query in queries:
            trial_id = str(query.get("trial_id"))
            if trial_id not in incumbent_results:
                continue
            gold_id = str(query["gold_id"])
            incumbent = incumbent_results[trial_id]
            candidate: TrialResult | None = None
            for arm_name in assignment.run_order:
                if arm_name == INCUMBENT_ARM:
                    again = _run_arm(incumbent_arm, export, project_key, query, incumbent_params)
                    if again != incumbent.ranked_ids:
                        raise InfraFailure(
                            f"incumbent ranking moved within the run on trial {trial_id!r}: "
                            f"{again} after {incumbent.ranked_ids}"
                        )
                    continue
                try:
                    candidate_ids = candidate_arm(
                        candidate_arm_server, export, project_key, query, candidate_params
                    )
                except InfraFailure as exc:
                    _harness_error(CANDIDATE_ARM, trial_id, exc)
                    break
                candidate = TrialResult(
                    trial_id=trial_id,
                    arm=CANDIDATE_ARM,
                    ranked_ids=candidate_ids,
                    metrics={e: score_endpoint(e, candidate_ids, gold_id) for e in endpoints},
                )
            if candidate is None:
                continue
            paired.append((incumbent, candidate))

        # Judges on blinded envelopes; a leak is recorded, never suppressed.
        # In agent mode the candidate manifest rides the pre-judge scan, and
        # the rubric roster's scores become each trial's metrics afterwards.
        candidate_manifest = None
        if mode == "agent":
            candidate_manifest = {
                key: candidate_params[key] for key in AGENT_MANIFEST_KEYS if key in candidate_params
            }
        judged = _run_judges(
            judges=roster,
            experiment=experiment,
            charter_digest=charter.digest,
            trials=paired,
            assignment=assignment,
            store=store,
            candidate_manifest=candidate_manifest,
        )
        if mode == "agent":
            _score_agent_trials(paired, judged.envelopes, endpoints, assignment)

    ctx.judge_records = judged.envelopes
    # blinded=True only when a scan ran and found nothing; a run with no
    # judged trials has no blinding fact to state and leaves the field null.
    ctx.blinded = (not judged.leaks) if judged.scans else None
    if judged.leaks:
        ctx.notes.append(
            f"blinding leak: identity tokens reached a judge: {sorted(set(judged.leaks))}"
        )
    ctx.trials = len(paired)

    # Stopping rule: a short batch is inconclusive, never a partial verdict.
    if not rule.is_complete(len(paired)):
        ctx.verdict = VERDICT_INCONCLUSIVE
        ctx.rationale = (
            f"fixed batch short by {rule.shortfall(len(paired))}: "
            f"{len(paired)} of {rule.batch_size} trials completed"
        )
        return

    # Statistics: paired deltas per endpoint, clustered by project, Holm-adjusted.
    deltas_by_endpoint = {
        e: {project_key: [c.metrics[e] - i.metrics[e] for i, c in paired]} for e in endpoints
    }
    outcomes = evaluate_family(deltas_by_endpoint)
    ctx.effect = {o.name: o.mean for o in outcomes}
    ctx.confidence_interval = {
        o.name: {
            "lower": o.lower,
            "upper": o.upper,
            "n": o.n,
            "raw_p_value": o.raw_p_value,
            "adjusted_p_value": o.adjusted_p_value,
        }
        for o in outcomes
    }
    ctx.verdict, ctx.rationale = decide_verdict(outcomes, thresholds)
