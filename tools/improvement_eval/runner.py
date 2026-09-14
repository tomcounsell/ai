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
   recorded baseline's ranked ids before the candidate arm is ever invoked.
8. **Paired trials** in the assigned order.
9. **Judges** on blinded envelopes, with the identity scan recorded, never
   suppressed.
10. **Statistics and Holm**, then the **stopping-rule** check.
11. **Verdict** and the single ``ImprovementEvaluation`` write.

Three disjoint handlers with no shared fall-through:

- :class:`~tools.improvement_eval.errors.InfraFailure` writes ``verdict="infra_failure"``.
  It is raised for exactly six conditions, each with a test: a Gate 0
  refusal (state or contract digest), an arm that would not spawn, unequal
  corpus digests between the arms and the export, a baseline parity miss, a
  judge provider that could not be reached (a ``skipped`` envelope), and an
  uncalibrated judge (a reference set below the floor).
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
The candidate arm receives only ``query_text`` per trial; the gold answers
stay in this process.

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
    failed: bool = False
    error: str | None = None


def _retrieve_job(export, project_key: str, query: dict, arm_params: dict) -> dict:
    job = {
        "mode": "retrieve",
        "jsonl": export.jsonl_text,
        "project_key": project_key,
        "query_text": query["query_text"],
    }
    job.update({k: v for k, v in arm_params.items() if k not in job and k != "mode"})
    return job


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


# ---------------------------------------------------------------------------
# Judges
# ---------------------------------------------------------------------------


def default_judges(project_key: str, charter: Any, *, judge_complete=None) -> list[JudgeFn]:
    """The default roster: a calibrated ``serves_charter`` judge.

    Calibration runs first and raises :class:`InfraFailure` below the
    reference-set floor, so an uncalibrated judge never scores a trial.
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

    calibrate(project_key, _calibration_probe)
    return [_judge]


@dataclass
class _JudgeOutcome:
    envelopes: list[dict] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    scans: int = 0


def _run_judges(
    *,
    judges: list[JudgeFn],
    experiment: Any,
    charter_digest: str,
    trials: list[tuple[TrialResult, TrialResult]],
    assignment: ArmAssignment,
    store,
) -> _JudgeOutcome:
    outcome = _JudgeOutcome()
    experiment_id = str(experiment.id)
    for incumbent_result, candidate_result in trials:
        for result in (incumbent_result, candidate_result):
            blinded_arm_id = assignment.blinded_ids[result.arm]
            judge_input = {
                "trial_id": result.trial_id,
                "blinded_arm_id": blinded_arm_id,
                "ranked_ids": result.ranked_ids,
                "metrics": result.metrics,
            }
            candidate_output = json.dumps(judge_input, sort_keys=True)
            scan = scan_for_identity(candidate_output, experiment)
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
                envelope_scan = scan_for_identity(serialized, experiment)
                outcome.scans += 1
                if envelope_scan.leaked:
                    outcome.leaks.extend(envelope_scan.hits)
                outcome.envelopes.append(wrapped)
    return outcome


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
) -> Any:
    """Run one frozen-input evaluation and write its single ``ImprovementEvaluation``.

    ``judges`` replaces the default roster (tests); ``judge_complete``
    injects the transport of the default ``serves_charter`` judge.
    ``_candidate_arm`` is the candidate arm invocation, exposed so the
    parity-gate test can prove the candidate was never called.
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


def _run_gates(ctx: _RunContext, *, judges, judge_complete, store, candidate_arm) -> None:
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
    infra_cap = int(protocol.get("infra_failure_cap", DEFAULT_INFRA_FAILURE_CAP))

    # Corpus export, hashed once; both arms restore from these bytes.
    export = export_corpus(project_key, store=store)

    # Arm assignment, recorded before either arm runs.
    assignment = assign_arms(str(experiment.id))
    ctx.arm_assignment_digest = assignment.digest

    # Judges are resolved before the arms spawn so an uncalibrated judge
    # fails the run before any Redis is started.
    roster = (
        judges
        if judges is not None
        else default_judges(project_key, charter, judge_complete=judge_complete)
    )

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
        # candidate arm is invoked at all. An incumbent-side error is a
        # harness error: the trial is excluded and counted toward the cap.
        baseline_digest = str(baseline_spec.get("corpus_digest"))
        incumbent_results: dict[str, TrialResult] = {}
        harness_errors = 0
        for query in queries:
            trial_id = str(query.get("trial_id"))
            try:
                ids = _run_arm(incumbent_arm, export, project_key, query, incumbent_params)
            except InfraFailure as exc:
                harness_errors += 1
                ctx.notes.append(f"harness error on trial {trial_id!r}: {exc}")
                if harness_errors > infra_cap:
                    raise InfraFailure(
                        f"{harness_errors} harness-errored trial(s) exceed the cap of {infra_cap}"
                    ) from exc
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
        if not queries:
            baseline_parity(
                [], RankedBaseline(ids=[], corpus_digest=baseline_digest), export.digest
            )

        # Paired trials: both arms run each input in the assigned order. The
        # incumbent's slot re-runs it and demands the Gate 1 ranking again,
        # so an incumbent that drifts within the run is caught, not averaged.
        paired: list[tuple[TrialResult, TrialResult]] = []
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
                    # The candidate arm broke: that scores against the candidate.
                    ctx.notes.append(f"candidate arm failed on trial {trial_id!r}: {exc}")
                    candidate = TrialResult(
                        trial_id=trial_id,
                        arm=CANDIDATE_ARM,
                        ranked_ids=[],
                        metrics={e: 0.0 for e in endpoints},
                        failed=True,
                        error=str(exc),
                    )
                    continue
                candidate = TrialResult(
                    trial_id=trial_id,
                    arm=CANDIDATE_ARM,
                    ranked_ids=candidate_ids,
                    metrics={e: score_endpoint(e, candidate_ids, gold_id) for e in endpoints},
                )
            paired.append((incumbent, candidate))

        # Judges on blinded envelopes; a leak is recorded, never suppressed.
        judged = _run_judges(
            judges=roster,
            experiment=experiment,
            charter_digest=charter.digest,
            trials=paired,
            assignment=assignment,
            store=store,
        )

    ctx.judge_records = judged.envelopes
    ctx.blinded = not judged.leaks
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
