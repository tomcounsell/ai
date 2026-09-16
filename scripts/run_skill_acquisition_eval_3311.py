"""Live skill-acquisition paired evaluation (#3311, Task 4, lane 5b).

Runs the skill-acquisition evaluation end to end through the agent_run arm:
lane 5's recorded ``skill_acquisition`` investigation supplies the gap, the
candidates, and the integrated skill; the prior capability (a research
session deciding a freeze with no preflight checklist) is the incumbent; the
session guided by the frozen ``improve-preflight`` skill text is the
candidate. Four frozen readiness tasks, one rubric endpoint, judge-scored
outcomes, real bounded ``claude -p`` sessions per trial, real unit-2 meter
reserve/settle per trial.

On completion the reuse observation is written to the case (superseding the
deferred disposition) and a record JSON is written next to the lane plan.

Usage (from the lane checkout, ambient production Redis)::

    .venv/bin/python scripts/run_skill_acquisition_eval_3311.py

If a spend reservation refuses (unit-2 exhausted) the run stops before any
session spawns and exits 2: the operator reports that, never works around it.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT = "valor"
CASE_ID = "1ec40086ca1d422e90ef747775ff7f64"
INVESTIGATION_ID = "3dbf7e2f7c67457bb768a03050772558"

ENDPOINT = "readiness-verdict"
BOUNDS = {"timeout_s": 300, "max_turns": 2, "spend_cap": 0.02}
MODEL = "claude-subscription"
SKILL = "improve-preflight"

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
    {
        "id": "t3",
        "prompt": TASK_INTRO + "\n\nSnapshot:\n"
        "- Control namespace: `valor-improve doctor` prints "
        "`no paused heads, no stale intents, no outstanding reservations`, exit 0.\n"
        "- Budgets: unit-2 settled $9.80, reserved $0.20, window 2026-09-16; "
        "unit-3 headroom $49.50 for the week; no unknown-metered receipts. "
        "A freeze reserves known_item_generation and an evaluation reserves "
        "evaluation_judges; either refuses when the window cannot admit it.\n"
        "- Calibration: the project partition holds 30 retained architectural "
        "corrections.\n\n" + TASK_SUFFIX,
        "expected": "HOLD",
    },
    {
        "id": "t4",
        "prompt": TASK_INTRO + "\n\nSnapshot:\n"
        "- Control namespace: `valor-improve doctor` prints "
        "`1 paused head on the case; no stale intents, no outstanding "
        "reservations`, exit 0. The freeze writes an `experiment_frozen` event "
        "to the case journal.\n"
        "- Budgets: unit-2 settled $0.30, reserved $0.00, window 2026-09-16; "
        "unit-3 headroom $49.50 for the week; no unknown-metered receipts.\n"
        "- Calibration: the project partition holds 30 retained architectural "
        "corrections.\n\n" + TASK_SUFFIX,
        "expected": "HOLD",
    },
]

RUBRIC_TEXT = (
    "Frozen task rubric for the readiness-verdict endpoint. Score 1.0 when the "
    "session's VERDICT token equals the task's frozen expected token "
    "(t1 FREEZE; t2, t3, t4 HOLD); otherwise 0.0. Reasons are not scored. "
    "The expected tokens encode the improve-preflight doctrine: t1 passes all "
    "three checks; t2 is short of the calibration floor; t3 fills the unit-2 "
    "day window so freeze and judge reservations refuse; t4 carries a paused "
    "head the journal will refuse."
)


def _checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _skill_text_and_hash() -> tuple[str, str]:
    path = _checkout_root() / ".claude" / "skills" / SKILL / "SKILL.md"
    text = path.read_text()
    return text, "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


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


def step_load_investigation() -> dict:
    """Lane 5's recorded skill_acquisition investigation, as a dict."""
    from tools.improvement_investigations import _find, row_as_dict

    row = _find(INVESTIGATION_ID)
    if row is None or row.kind != "skill_acquisition":
        raise SystemExit(f"lane 5 skill_acquisition investigation {INVESTIGATION_ID} not found")
    record = row_as_dict(row)
    print(f"investigation {record['id']} kind={record['kind']} state={record['state']}")
    print(f"gap: {record['uncertainty'][:200]}...")
    print(f"assumption: {(record['provisional_assumption'] or '')[:200]}...")
    return record


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
    """The incumbent baseline, real sessions, metered per trial."""
    from tools.improvement_eval import runner
    from tools.improvement_eval.corpus import export_corpus
    from tools.improvement_eval.errors import InfraFailure

    export = export_corpus(PROJECT)
    print(f"corpus: {len((export.jsonl_text or '').splitlines())} lines digest={export.digest}")
    tasks = [{"id": t["id"], "prompt": t["prompt"]} for t in TASKS]
    started = time.time()
    try:
        baseline = runner.capture_agent_baseline(
            PROJECT,
            tasks,
            incumbent=incumbent,
            export=export,
            meter=meter,
            arm_run_id="skill-acq-3311:baseline",
        )
    except InfraFailure as exc:
        print(
            f"STOP: baseline refused ({exc}); unit-2 exhausted or arm broken. "
            "Reporting, not retrying."
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
        "holdout_partition": "skill-acquisition-3311",
        "tasks": [{"id": t["id"], "prompt": t["prompt"]} for t in TASKS],
        "rubrics": {
            ENDPOINT: {
                "text": RUBRIC_TEXT,
                "expected": {t["id"]: t["expected"] for t in TASKS},
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
        hypothesis="A research session guided by the improve-preflight skill reaches "
        "the right freeze/hold call on readiness snapshots; a session with no "
        "checklist does not.",
        mechanism="The candidate prompt carries the frozen preflight checklist "
        "(doctor line, unit-2 day window, calibration floor); the incumbent "
        "prompt carries only the snapshot.",
        falsifier="Rubric scores do not separate the arms.",
        candidate_surfaces=json.dumps(["improve-preflight skill", "persona"]),
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
        arm_run_id="skill-acq-3311",
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
    """Write the reuse observation to the case, superseding the deferral."""
    import json as _json

    from models.improvement_case import ImprovementCase

    case = ImprovementCase.query.filter(project_key=PROJECT, id=CASE_ID).first()
    if case is None:
        raise SystemExit(f"case {CASE_ID} not found")
    judgments = trial_judgments(evaluation)
    observation = (
        f"Reuse observation (evaluation {evaluation.id} on experiment {experiment.id}, "
        f"lane 5b agent-run arm): the improve-preflight skill was compared against the "
        f"prior capability (no checklist) on 4 frozen readiness snapshots, one rubric "
        f"endpoint ({ENDPOINT}). Verdict={evaluation.verdict} over "
        f"{evaluation.trials} paired trials; per-trial rubric scores: "
        f"{', '.join(judgments)}. Gate 1 baseline agreement held on all 4 tasks. "
        f"Charter leg: {charter_refusal}; the verdict rests on rubrics and the charter "
        f"leg is unmeasured. Unit-2 meter: {meter_before} -> {meter_after} "
        f"(estimated settlements per frozen per-task budget ${BOUNDS['spend_cap']:.2f}). "
        f"This overturns-or-confirms investigation {INVESTIGATION_ID}'s provisional "
        f"assumption by measurement: charter s5 stage 4 is now observed, and the "
        f"'deferred: no agent-run arm' disposition is cleared."
    )
    current = (case.summary or "").rstrip()
    applied = _json.loads(case.evaluation_ids or "[]")
    if not isinstance(applied, list):
        applied = []
    if evaluation.id in applied:
        print(f"observation for evaluation {evaluation.id} already recorded; not duplicating")
        return observation
    case.summary = f"{current}\n\n{observation}" if current else observation
    applied.append(evaluation.id)
    case.evaluation_ids = _json.dumps(applied)
    case.save()
    print("observation written to case")
    return observation


def main() -> int:
    from tools import paid_inference_meter

    t0 = time.time()
    step_load_investigation()
    skill_text, skill_hash = _skill_text_and_hash()
    print(f"skill {SKILL} sha={skill_hash} chars={len(skill_text)}")
    incumbent = {
        "model": MODEL,
        "persona": "research session deciding a freeze with no preflight checklist",
        "bounds": dict(BOUNDS),
    }
    candidate = {
        "model": MODEL,
        "skill": SKILL,
        "persona": "research session running the improve-preflight skill",
        "prompt_hash": skill_hash,
        "bounds": dict(BOUNDS),
    }
    charter_refusal = step_charter_leg()
    meter_before = paid_inference_meter.status_dict(PROJECT)
    baseline, export = step_capture_baseline(incumbent, paid_inference_meter)
    experiment = step_freeze(baseline, incumbent, candidate, paid_inference_meter)
    expected_map = {t["id"]: t["expected"] for t in TASKS}
    evaluation = step_evaluate(experiment, expected_map, paid_inference_meter)
    meter_after = paid_inference_meter.status_dict(PROJECT)
    observation = step_observe(experiment, evaluation, charter_refusal, meter_before, meter_after)
    out = build_record(
        experiment,
        evaluation,
        skill_hash=skill_hash,
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
    _, skill_hash = _skill_text_and_hash()
    baseline_spec = protocol.get("baseline") or {}
    meter = paid_inference_meter.status_dict(PROJECT)
    out = build_record(
        experiment,
        evaluation,
        skill_hash=skill_hash,
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
    skill_hash: str,
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
        "skill": SKILL,
        "skill_hash": skill_hash,
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
    path = _checkout_root() / "docs" / "plans" / "skill-acquisition-eval-3311-record.json"
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
