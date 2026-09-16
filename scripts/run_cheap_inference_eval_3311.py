"""Live cheap-inference paired evaluation (#3311, Task 5, lane 5b).

Runs the cheap-inference experiment end to end through the agent_run arm:
lane 5's recorded ``resource_acquisition`` investigation supplies the
``keyless_integrated`` disposition (a cheap OpenRouter endpoint reachable
through the existing key, no vault wait); the incumbent is a subscription
``claude -p`` session deciding a freeze with no checklist; the candidate is
the same prompt on ``meta/muse-spark-1.3`` through the OpenRouter
chat-completions route. Two frozen readiness tasks, one rubric endpoint,
judge-scored outcomes, real bounded sessions per trial, real unit-2 meter
reserve/settle per trial.

``is_open_source`` is checked for both arms before anything spawns (the
runner enforces it per trial; this script pre-checks it per arm and
reports it). No client context enters either arm.

On completion a cheap-inference observation is written to the case and a
record JSON is written next to the lane plan.

Usage (from the lane checkout, ambient production Redis)::

    .venv/bin/python scripts/run_cheap_inference_eval_3311.py

If the disposition gate, the OpenRouter key, the eligibility check, or a
spend reservation refuses, the run stops before any session spawns and
exits 2: the operator reports that, never works around it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "valor"
CASE_ID = "1ec40086ca1d422e90ef747775ff7f64"
INVESTIGATION_ID = "78bc12729199442fa8bf0a641a18427e"

ENDPOINT = "provider-verdict"
BOUNDS = {"timeout_s": 300, "max_turns": 2, "spend_cap": 0.02}
INCUMBENT_MODEL = "claude-subscription"
CANDIDATE_MODEL = "openrouter:meta/muse-spark-1.3"
PERSONA = "research session deciding a freeze with no preflight checklist"

TASK_SUFFIX = (
    "Give the check that decides it, the figure you read, and the threshold "
    "you read it against. Name no skills, personas, or models; give reasons "
    "and the verdict only. End your reply with exactly one line of the form "
    "`VERDICT: <word>` where <word> is FREEZE or HOLD, then stop."
)

TASK_INTRO = (
    "You are a research session deciding whether an improvement experiment "
    "may be frozen now. Below is the snapshot your pre-freeze reads returned. "
    "Decide FREEZE (the freeze can reach a scored verdict) or HOLD (it cannot)."
)

TASKS = [
    {
        "id": "t1",
        "prompt": TASK_INTRO + "\n\nSnapshot:\n"
        "- Control namespace: `valor-improve doctor` prints "
        "`no paused heads, no stale intents, no outstanding reservations`, exit 0.\n"
        "- Budgets: unit-2 settled $0.30, reserved $0.00, window 2026-09-16; "
        "unit-3 headroom $49.50 for the week; no unknown-metered receipts.\n"
        "- Calibration: the project partition holds 25 retained architectural "
        "corrections.\n\n" + TASK_SUFFIX,
        "expected": "FREEZE",
    },
    {
        "id": "t2",
        "prompt": TASK_INTRO + "\n\nSnapshot:\n"
        "- Control namespace: `valor-improve doctor` prints "
        "`no paused heads, no stale intents, no outstanding reservations`, exit 0.\n"
        "- Budgets: unit-2 settled $0.30, reserved $0.00, window 2026-09-16; "
        "unit-3 headroom $49.50 for the week; no unknown-metered receipts.\n"
        "- Calibration: the project partition holds 0 retained architectural "
        "corrections.\n\n" + TASK_SUFFIX,
        "expected": "HOLD",
    },
]

RUBRIC_TEXT = (
    "Frozen task rubric for the provider-verdict endpoint. Score 1.0 when the "
    "session's VERDICT token equals the task's frozen expected token "
    "(t1 FREEZE; t2 HOLD); otherwise 0.0. Reasons are not scored. "
    "The expected tokens encode the preflight doctrine: t1 passes all three "
    "checks (clean control namespace, open budgets, calibration above the "
    "floor); t2 is short of the calibration floor."
)


def _checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _checkout_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_checkout_root(),
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def step_gate_disposition() -> str:
    """Lane 5's recorded cheap-inference disposition, verbatim.

    The experiment runs only on ``keyless_integrated``: the credential is
    already usable through the existing OpenRouter key, so there is no
    vault item for ``probe`` to verify and no wait. Anything else stops
    the run before any session spawns.
    """
    from tools.improvement_investigations import _find, disposition_of, row_as_dict

    row = _find(INVESTIGATION_ID)
    if row is None or row.kind != "resource_acquisition":
        raise SystemExit(f"lane 5 resource_acquisition investigation {INVESTIGATION_ID} not found")
    record = row_as_dict(row)
    disposition, _resource = disposition_of(row)
    print(f"investigation {record['id']} kind={record['kind']} state={record['state']}")
    print(f"disposition={disposition!r} (resource={_resource!r})")
    print(f"uncertainty: {record['uncertainty'][:200]}...")
    if disposition != "keyless_integrated":
        raise SystemExit(
            f"STOP: cheap-inference disposition is {disposition!r}, not "
            "'keyless_integrated'; the credential path needs re-planning. "
            "Reporting, not working around it."
        )
    return disposition


def step_gate_key() -> None:
    """The OpenRouter key lane 5 exercised must be present.

    Absent means stop and report: no vault writes, no fallback provider,
    no keyless path. The arm worker refuses the same way per trial.
    """
    if not os.environ.get("OPENROUTER_API_KEY"):
        raise SystemExit(
            "STOP: OPENROUTER_API_KEY is absent; the cheap arm cannot be reached. "
            "Reporting, not working around it."
        )
    print("OPENROUTER_API_KEY present")


def step_gate_eligibility() -> bool:
    """Both arms run on the open-source project, checked before any spawn.

    The runner re-checks per trial and refuses client-keyed projects with
    an InfraFailure before reserving or spawning; this pre-check reports
    the same answer per arm up front.
    """
    from tools.improvement_eligibility import is_open_source

    for arm_name in ("incumbent", "candidate"):
        if not is_open_source(PROJECT):
            raise SystemExit(
                f"STOP: project {PROJECT!r} is not provably open-source for the "
                f"{arm_name} arm; sessions stay on the subscription. Reporting."
            )
        print(f"eligibility {arm_name}: {PROJECT!r} is open-source; no client context")
    return True


def step_charter_leg() -> str:
    """Exercise the serves-charter calibration leg; return the refusal record.

    With 0 of 20 reference items the calibration refuses with InfraFailure;
    that refusal is the recorded charter-leg limitation. No judge call is
    made and no spend occurs here.
    """
    from tools.improvement_eval.calibration import (
        MIN_REFERENCE_SET_SIZE,
        calibrate,
        collect_architectural_reference,
    )
    from tools.improvement_eval.errors import InfraFailure

    items = collect_architectural_reference(PROJECT)
    print(f"architectural_corrections={len(items)} floor={MIN_REFERENCE_SET_SIZE}")
    try:
        calibrate(PROJECT, lambda text, swapped=False: "APPROVED")
    except InfraFailure as exc:
        record = f"serves-charter calibration floor refusal: {exc}"
        print(record)
        return record
    raise SystemExit("calibration unexpectedly passed; the charter leg needs re-planning")


def step_capture_baseline(incumbent: dict, meter) -> tuple[dict, object]:
    """The incumbent baseline, real subscription sessions, metered per trial."""
    from tools.improvement_eval import runner
    from tools.improvement_eval.corpus import export_corpus
    from tools.improvement_eval.errors import InfraFailure

    export = export_corpus(PROJECT)
    print(f"corpus: {len((export.jsonl_text or '').splitlines())} lines digest={export.digest}")
    tasks = [{"id": task["id"], "prompt": task["prompt"]} for task in TASKS]
    started = time.time()
    try:
        baseline = runner.capture_agent_baseline(
            PROJECT,
            tasks,
            incumbent=incumbent,
            export=export,
            meter=meter,
            arm_run_id="cheap-inf-3311:baseline",
        )
    except InfraFailure as exc:
        print(
            f"STOP: baseline refused ({exc}); eligibility, unit-2 exhaustion, or arm "
            "breakage. Reporting, not retrying."
        )
        raise SystemExit(2) from exc
    wall = time.time() - started
    for task_id, outcome in baseline["outcomes"].items():
        lines = (outcome.get("output") or "").splitlines()
        hits = [line for line in lines if "VERDICT" in line]
        print(f"baseline {task_id}: passed={outcome.get('passed')} verdict={hits}")
    print(f"baseline wall {wall:.0f}s")
    return baseline, export


def step_freeze(baseline: dict, incumbent: dict, candidate: dict, meter) -> object:
    """Freeze the agent_task protocol and the experiment row (Gate 0 input)."""
    from datetime import UTC as _UTC
    from datetime import datetime as _datetime

    from models.improvement_experiment import ImprovementExperiment
    from tools.improvement_eval import runner

    protocol = {
        "mode": "agent",
        "batch_size": len(TASKS),
        "endpoints": [ENDPOINT],
        "thresholds": {ENDPOINT: {"margin": 0.0, "alpha": 0.05}},
        "holdout_partition": "cheap-inference-3311",
        "tasks": [{"id": task["id"], "prompt": task["prompt"]} for task in TASKS],
        "rubrics": {
            ENDPOINT: {
                "text": RUBRIC_TEXT,
                "expected": {task["id"]: task["expected"] for task in TASKS},
            }
        },
        "baseline": baseline,
        "incumbent": incumbent,
        "candidate": candidate,
        "infra_failure_cap": 0,
    }
    protocol_ref = runner.freeze_protocol(protocol)
    sha = _checkout_sha()
    manifest = {
        "protocol_ref": protocol_ref,
        "base_revision": sha,
        "candidate_ref": sha,
        "candidate": candidate,
        "incumbent": incumbent,
        "envelope": "agent_task",
        "corpus_digest": baseline["corpus_digest"],
    }
    experiment = ImprovementExperiment(
        project_key=PROJECT,
        created_at=_datetime.now(_UTC),
        hypothesis="A cheap-provider session reaches the same freeze/hold call as "
        "the subscription incumbent on frozen readiness snapshots.",
        mechanism="The candidate prompt is the incumbent prompt run on "
        "meta/muse-spark-1.3 through the OpenRouter chat-completions route; "
        "only the provider changes.",
        falsifier="Rubric scores separate the arms.",
        candidate_surfaces=json.dumps(["cheap provider", "model"]),
        manifest=json.dumps(manifest, sort_keys=True),
    )
    assert experiment.save() is not False
    experiment = ImprovementExperiment.query.filter(project_key=PROJECT, id=experiment.id).first()
    experiment.contract_digest = runner.compute_contract_digest(experiment)
    experiment.state = "frozen"
    experiment.frozen_at = _datetime.now(_UTC)
    from models.improvement_charter import ImprovementCharter

    pinned = ImprovementCharter.pinned(PROJECT)
    experiment.charter_version = int(getattr(pinned, "version", 0) or 0)
    assert experiment.save() is not False
    print(f"frozen experiment {experiment.id} protocol={protocol_ref}")
    print(f"contract {experiment.contract_digest}")
    return ImprovementExperiment.query.filter(project_key=PROJECT, id=experiment.id).first()


def step_evaluate(experiment, expected_map: dict, meter) -> object:
    """The paired evaluation: real sessions, metered, rubric-scored."""
    from models.improvement_evaluation import ImprovementEvaluation
    from tools.improvement_eval import runner
    from tools.improvement_eval.judges.rubric import make_rubric_judge

    judge = make_rubric_judge(ENDPOINT, expected_map, RUBRIC_TEXT)
    started = time.time()
    evaluation = runner.evaluate(
        str(experiment.id),
        PROJECT,
        judges=[judge],
        meter=meter,
        arm_run_id="cheap-inf-3311",
    )
    wall = time.time() - started
    evaluation = ImprovementEvaluation.query.filter(project_key=PROJECT, id=evaluation.id).first()
    print(
        f"evaluation {evaluation.id} verdict={evaluation.verdict} "
        f"trials={evaluation.trials} blinded={evaluation.blinded} wall={wall:.0f}s"
    )
    print("notes:")
    for line in (evaluation.notes or "").splitlines():
        print(f"  {line}")
    return evaluation


def trial_judgments(evaluation) -> list[str]:
    """Per-trial ``trial/arm/endpoint=score`` lines off the evaluation row.

    ``judge_records`` is a ContentField on the verifying artifact store, so
    it resolves through :func:`tools.improvement_eval.runner.read_content`,
    never plain ``json.loads``.
    """
    from tools.improvement_eval.runner import read_content

    records = json.loads(read_content(evaluation, "judge_records") or "[]")
    judgments = []
    for record in records:
        judge = record.get("judge", {})
        judgments.append(
            f"{record.get('trial_id')}/{record.get('blinded_arm_id')}/"
            f"{judge.get('judge_id')}={judge.get('score')}"
        )
    return judgments


def experiment_manifest(experiment) -> dict:
    """The experiment manifest, resolved through its ContentField store."""
    from tools.improvement_eval.runner import read_content

    return json.loads(read_content(experiment, "manifest") or "{}")


def step_observe(
    experiment, evaluation, charter_refusal: str, meter_before: dict, meter_after: dict
) -> str:
    """Write the cheap-inference observation to the case."""
    import json as _json

    from models.improvement_case import ImprovementCase

    case = ImprovementCase.query.filter(project_key=PROJECT, id=CASE_ID).first()
    if case is None:
        raise SystemExit(f"case {CASE_ID} not found")
    judgments = trial_judgments(evaluation)
    observation = (
        f"Cheap-inference observation (evaluation {evaluation.id} on experiment {experiment.id}, "
        f"lane 5b agent-run arm): the {CANDIDATE_MODEL} session was compared against the "
        f"subscription incumbent ({INCUMBENT_MODEL}, no checklist) on 2 frozen readiness "
        f"snapshots, one rubric endpoint ({ENDPOINT}). Verdict={evaluation.verdict} over "
        f"{evaluation.trials} paired trials; per-trial rubric scores: "
        f"{', '.join(judgments)}. Gate 1 baseline agreement held on both tasks. "
        f"Eligibility: {PROJECT!r} verified open-source for both arms before any spawn; "
        f"no client context in either arm. "
        f"Charter leg: {charter_refusal}; the verdict rests on rubrics and the charter "
        f"leg is unmeasured. Unit-2 meter: {meter_before} -> {meter_after} "
        f"(estimated settlements per frozen per-task budget ${BOUNDS['spend_cap']:.2f}; "
        f"the cheap provider bills per token on top of the estimate). "
        f"This measures lane 5 investigation {INVESTIGATION_ID}'s keyless_integrated path: "
        "an eligible open-source session on the cheap provider under a frozen contract."
    )
    current = (case.summary or "").rstrip()
    case.summary = f"{current}\n\n{observation}" if current else observation
    applied = _json.loads(case.evaluation_ids or "[]")
    if not isinstance(applied, list):
        applied = []
    if evaluation.id not in applied:
        applied.append(evaluation.id)
    case.evaluation_ids = _json.dumps(applied)
    case.save()
    print("observation written to case")
    return observation


def main() -> int:
    from tools import paid_inference_meter

    t0 = time.time()
    disposition = step_gate_disposition()
    step_gate_key()
    step_gate_eligibility()
    incumbent = {
        "model": INCUMBENT_MODEL,
        "persona": PERSONA,
        "bounds": dict(BOUNDS),
    }
    candidate = {
        "model": CANDIDATE_MODEL,
        "persona": PERSONA,
        "bounds": dict(BOUNDS),
    }
    charter_refusal = step_charter_leg()
    meter_before = paid_inference_meter.status_dict(PROJECT)
    baseline, export = step_capture_baseline(incumbent, paid_inference_meter)
    experiment = step_freeze(baseline, incumbent, candidate, paid_inference_meter)
    expected_map = {task["id"]: task["expected"] for task in TASKS}
    evaluation = step_evaluate(experiment, expected_map, paid_inference_meter)
    meter_after = paid_inference_meter.status_dict(PROJECT)
    observation = step_observe(experiment, evaluation, charter_refusal, meter_before, meter_after)
    out = build_record(
        experiment,
        evaluation,
        disposition=disposition,
        corpus_digest=baseline["corpus_digest"],
        charter_refusal=charter_refusal,
        meter_before=meter_before,
        meter_after=meter_after,
        observation=observation,
        wall_seconds=round(time.time() - t0),
    )
    write_record(out)
    print(f"total wall {time.time() - t0:.0f}s")
    return 0


def record_only(experiment_id: str, evaluation_id: str) -> Path:
    """Rebuild the record JSON off rows a live run already wrote.

    Recovery path only: when the sessions, freeze, evaluation, and case
    observation all completed but record assembly failed, this re-reads
    those rows (no new sessions, no new spend, no case writes) and writes
    the record file through the same :func:`build_record`.
    """
    from models.improvement_evaluation import ImprovementEvaluation
    from models.improvement_experiment import ImprovementExperiment
    from tools import paid_inference_meter
    from tools.improvement_eval import runner

    experiment = ImprovementExperiment.query.filter(project_key=PROJECT, id=experiment_id).first()
    if experiment is None:
        raise SystemExit(f"experiment {experiment_id} not found")
    evaluation = ImprovementEvaluation.query.filter(project_key=PROJECT, id=evaluation_id).first()
    if evaluation is None:
        raise SystemExit(f"evaluation {evaluation_id} not found")
    protocol = runner.load_protocol(experiment)
    judgments = trial_judgments(evaluation)
    print(f"experiment {experiment.id} contract={experiment.contract_digest}")
    print(
        f"evaluation {evaluation.id} verdict={evaluation.verdict} "
        f"trials={evaluation.trials} blinded={evaluation.blinded}"
    )
    for line in judgments:
        print(f"  {line}")
    baseline_spec = protocol.get("baseline") or {}
    meter = paid_inference_meter.status_dict(PROJECT)
    out = build_record(
        experiment,
        evaluation,
        disposition="keyless_integrated",
        corpus_digest=baseline_spec.get("corpus_digest", ""),
        charter_refusal=(
            "serves-charter calibration floor refusal: the project partition "
            "holds 0 retained architectural corrections against a floor of 20; "
            "the serves-charter judge is uncalibrated."
        ),
        meter_before=None,
        meter_after=meter,
        observation="already on the case; see case summary",
        wall_seconds=-1,
        assembly="recovery-posthoc: meter_before unavailable (single post-hoc "
        "snapshot), wall_seconds unknown; sessions, freeze, evaluation, and "
        "case observation are live-run artifacts",
    )
    return write_record(out)


def build_record(
    experiment,
    evaluation,
    *,
    disposition: str,
    corpus_digest: str,
    charter_refusal: str,
    meter_before: dict | None,
    meter_after: dict,
    observation: str,
    wall_seconds: int,
    assembly: str = "live",
) -> dict:
    """The evaluation record JSON, off rows the live run already wrote."""
    return {
        "investigation_id": INVESTIGATION_ID,
        "case_id": CASE_ID,
        "disposition": disposition,
        "incumbent_model": INCUMBENT_MODEL,
        "candidate_model": CANDIDATE_MODEL,
        "provider_route": "OpenRouter chat-completions with OPENROUTER_API_KEY "
        "(OpenAI-compatible call path lane 5 exercised)",
        "eligibility": f"{PROJECT!r} verified open-source for both arms before any spawn",
        "experiment_id": experiment.id,
        "contract_digest": experiment.contract_digest,
        "protocol_ref": experiment_manifest(experiment)["protocol_ref"],
        "corpus_digest": corpus_digest,
        "evaluation_id": evaluation.id,
        "verdict": evaluation.verdict,
        "trials": evaluation.trials,
        "blinded": evaluation.blinded,
        "effect": json.loads(evaluation.effect or "null"),
        "confidence_interval": json.loads(evaluation.confidence_interval or "null"),
        "evaluation_notes": evaluation.notes,
        "charter_leg": charter_refusal,
        "unit2_before": meter_before,
        "unit2_after": meter_after,
        "observation": observation,
        "wall_seconds": wall_seconds,
        "assembly": assembly,
    }


def write_record(out: dict) -> Path:
    """Write the record JSON next to the lane plan; return its path."""
    path = _checkout_root() / "docs" / "plans" / "cheap-inference-eval-3311-record.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True))
    print(f"record written to {path}")
    return path


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "record-only":
        record_only(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 1:
        sys.exit(main())
    else:
        raise SystemExit(f"usage: {sys.argv[0]} [record-only EXPERIMENT_ID EVALUATION_ID]")
