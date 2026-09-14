"""``valor-improve-release``: the release lane's operator and incident binary (#3218).

One subcommand per lifecycle event, plus the drill, the gate, the recursive
comparison, the revision repair, and the claim report. Every subcommand
prints exactly one JSON object on stdout and exits 0 on success, 2 on a
refusal (``ReleaseRefused``, ``DrillRefused``, ``ComparisonRefused``,
``ArmRunnerAbsent``, or the ``ValueError`` a hand supersede raises on an
empty reason) as ``{"refused": true, "code": ..., "detail": ...}``, and 1 on
an unexpected error with the traceback on stderr.

This binary is an incident surface: ``rollback`` and ``drill`` must work
when the control journal is unreachable, so nothing here imports
``tools.improvement``, ``tools.improvement_ranking``, or
``tools.improvement_plan_arm`` at load. ``compare run --arm-runner
module:attr`` names lane 5's runner and resolves it lazily at execution
through ``arms.resolve_arm_runner``.

``--runner-log <path>`` builds ``SubprocessRunner(log_path=path)`` so a test
can inspect every subprocess call. ``--now <iso>`` on ``propose``,
``approve``, ``expose``, and ``close-window`` overrides the clock; it exists
for tests and is documented as such in ``--help``.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.improvement_release.evaluation_read import json_field

DEFAULT_PROJECT_KEY = "valor"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_REFUSED = 2

#: Row fields the CLI prints for a release; JSON-string fields are parsed.
RELEASE_FIELDS = (
    "id",
    "project_key",
    "created_at",
    "state",
    "kind",
    "evaluation_id",
    "case_id",
    "surfaces",
    "candidate_ref",
    "base_revision",
    "exposure",
    "exposed_at",
    "rollback_plan",
    "rollback_drill",
    "observation",
    "observation_window_ends_at",
    "promotion_gate",
    "approved_by",
    "approved_at",
    "outcome",
    "charter_digest",
)
JSON_FIELDS = frozenset(
    {
        "surfaces",
        "exposure",
        "rollback_plan",
        "rollback_drill",
        "observation",
        "promotion_gate",
        "outcome",
    }
)

EXPERIMENT_FIELDS = (
    "id",
    "project_key",
    "created_at",
    "state",
    "case_id",
    "hypothesis",
    "contract_digest",
    "candidate_surfaces",
    "manifest",
)
EVALUATION_FIELDS = (
    "id",
    "project_key",
    "created_at",
    "state",
    "verdict",
    "experiment_id",
    "evaluator_version",
    "contract_digest",
    "charter_digest",
    "effect",
    "confidence_interval",
    "correction",
    "notes",
)
REVISION_FIELDS = (
    "id",
    "project_key",
    "created_at",
    "state",
    "research_process_digest",
    "supersedes_id",
    "rationale",
)


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set | frozenset | tuple):
        return list(value)
    return str(value)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, sort_keys=True, default=_json_default) + "\n")
    sys.stdout.flush()


def _row(obj: Any, fields: tuple[str, ...], json_fields: frozenset[str] = frozenset()) -> dict:
    payload: dict[str, Any] = {}
    for name in fields:
        value = getattr(obj, name, None)
        if name in json_fields:
            value = json_field(value)
        elif name == "id" and value is not None:
            value = str(value)
        payload[name] = value
    return payload


def release_payload(release: Any) -> dict:
    """The release row with every JSON-string field parsed."""
    return _row(release, RELEASE_FIELDS, JSON_FIELDS)


def _experiment_payload(experiment: Any) -> dict:
    payload = _row(experiment, EXPERIMENT_FIELDS)
    manifest = json_field(payload.get("manifest"))
    if manifest is not None:
        payload["manifest"] = manifest
    return payload


def _evaluation_payload(evaluation: Any) -> dict:
    payload = _row(evaluation, EVALUATION_FIELDS)
    for name in ("effect", "confidence_interval"):
        parsed = json_field(payload.get(name))
        if parsed is not None:
            payload[name] = parsed
    return payload


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _parse_now(value: str | None) -> datetime | None:
    if value is None:
        return None
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=UTC)


def _load_json_file(path: str, *, what: str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise argparse.ArgumentTypeError(f"{what} {path!r}: {exc}") from exc


def _runner(args: argparse.Namespace):
    from tools.improvement_release.runner import SubprocessRunner

    return SubprocessRunner(log_path=getattr(args, "runner_log", None))


def _arm_digest(value: str | None) -> str | None:
    """An arm as a ``sha256:`` digest, or the digest of a process spec JSON file."""
    if value is None or not Path(value).is_file():
        return value
    from tools.improvement_recursion.process import ResearchProcessSpec, research_process_digest

    spec = _load_json_file(value, what="process spec")
    if not isinstance(spec, dict):
        raise argparse.ArgumentTypeError(f"process spec {value!r} is not a JSON object")
    return research_process_digest(ResearchProcessSpec(**spec))


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_propose(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import propose

    release = propose(
        evaluation_id=args.evaluation,
        kind=args.kind,
        candidate_ref=args.candidate_ref,
        surfaces=list(args.surfaces),
        rollback_plan=_load_json_file(args.rollback_plan, what="rollback plan"),
        observation=_load_json_file(args.observation, what="observation plan"),
        project_key=args.project_key,
        base_revision=args.base_revision,
        calibration_ref=args.calibration_ref,
        argument=args.argument,
        runner=_runner(args),
        now=_parse_now(args.now),
        repo=args.repo,
    )
    return release_payload(release)


def cmd_drill(args: argparse.Namespace) -> dict:
    from tools.improvement_release import drill
    from tools.improvement_release.lifecycle import get_release

    runner = _runner(args)
    if args.sweep:
        removed = drill.sweep(root=args.root, runner=runner, repo=args.repo)
        return {"swept": removed}
    if not args.release:
        raise argparse.ArgumentTypeError("--release is required unless --sweep is given")
    release = get_release(args.release, args.project_key)
    record = drill.run(release, runner=runner, root=args.root, repo=args.repo)
    payload = release_payload(get_release(args.release, args.project_key))
    payload["rollback_drill"] = record
    return payload


def cmd_approve(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import approve

    release = approve(
        args.release,
        approved_by=args.approved_by,
        project_key=args.project_key,
        now=_parse_now(args.now),
    )
    return release_payload(release)


def cmd_open_pr(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import open_pr

    release = open_pr(
        args.release,
        project_key=args.project_key,
        runner=_runner(args),
        base=args.base,
        title=args.title,
        repo=args.repo,
    )
    return release_payload(release)


def cmd_expose(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import expose

    release = expose(
        args.release,
        project_key=args.project_key,
        runner=_runner(args),
        now=_parse_now(args.now),
        repo=args.repo,
    )
    return release_payload(release)


def cmd_close_window(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import close_window, due_windows

    now = _parse_now(args.now)
    if args.due:
        rows = due_windows(args.project_key, now=now)
        return {
            "project_key": args.project_key,
            "due": [
                {
                    "id": str(row.id),
                    "state": row.state,
                    "kind": row.kind,
                    "exposed_at": row.exposed_at,
                    "observation_window_ends_at": row.observation_window_ends_at,
                }
                for row in rows
            ],
        }
    if not args.release:
        raise argparse.ArgumentTypeError("--release is required unless --due is given")
    release = close_window(
        args.release,
        project_key=args.project_key,
        now=now,
        force=args.force,
        reason=args.reason,
    )
    return release_payload(release)


def cmd_rollback(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import rollback

    release = rollback(
        args.release,
        project_key=args.project_key,
        reason=args.reason,
        runner=_runner(args),
        branch=args.branch,
        root=args.root,
        repo=args.repo,
    )
    payload = release_payload(release)
    record = (payload.get("outcome") or {}).get("rollback") or {}
    if args.branch:
        payload["pr_command"] = record.get("pr_command")
    return payload


def cmd_withdraw(args: argparse.Namespace) -> dict:
    from tools.improvement_release.lifecycle import withdraw

    release = withdraw(args.release, project_key=args.project_key, reason=args.reason)
    return release_payload(release)


def cmd_show(args: argparse.Namespace) -> dict:
    from tools.improvement_release.drill import drill_record, read_drill_log
    from tools.improvement_release.lifecycle import get_release

    release = get_release(args.release, args.project_key)
    payload = release_payload(release)
    payload["rollback_drill"] = drill_record(release)
    payload["drill_log"] = read_drill_log(release)
    return payload


def cmd_gate(args: argparse.Namespace) -> dict:
    from tools.improvement_release.promotion import promotion_gate

    return promotion_gate(args.project_key).as_dict()


def cmd_compare_fresh(args: argparse.Namespace) -> dict:
    from tools.improvement_recursion.freshness import fresh_opportunities

    fresh, excluded = fresh_opportunities(list(args.candidates), project_key=args.project_key)
    return {
        "project_key": args.project_key,
        "fresh": fresh,
        "excluded": [{"id": cid, "reason": reason} for cid, reason in excluded],
    }


def cmd_compare_freeze(args: argparse.Namespace) -> dict:
    from tools.improvement_recursion.budget import BudgetCap
    from tools.improvement_recursion.compare import freeze

    budget = _load_json_file(args.budget, what="budget cap")
    if not isinstance(budget, dict):
        raise argparse.ArgumentTypeError(f"budget cap {args.budget!r} is not a JSON object")
    experiment = freeze(
        arm_a=_arm_digest(args.arm_a),
        arm_b=_arm_digest(args.arm_b),
        opportunity_ids=list(args.opportunities),
        budget_cap=BudgetCap(**budget),
        project_key=args.project_key,
        minimum_worthwhile_effect=args.minimum_worthwhile_effect,
    )
    return _experiment_payload(experiment)


def cmd_compare_run(args: argparse.Namespace) -> dict:
    from tools.improvement_recursion.compare import run

    evaluation = run(
        args.experiment,
        arm_runner_spec=args.arm_runner,
        rng_seed=args.rng_seed,
        project_key=args.project_key,
    )
    return _evaluation_payload(evaluation)


def cmd_revision_supersede(args: argparse.Namespace) -> dict:
    from tools.improvement_recursion.compare import supersede_revision

    revision = supersede_revision(args.revision, project_key=args.project_key, reason=args.reason)
    return _row(revision, REVISION_FIELDS)


def cmd_report(args: argparse.Namespace) -> dict | str:
    from tools.improvement_recursion.report import claim_report, render

    report = claim_report(args.project_key)
    if args.render:
        return render(report)
    return report


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-key", default=DEFAULT_PROJECT_KEY, help="record partition (default: valor)"
    )


def _add_now(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--now",
        default=None,
        help="TEST-ONLY clock override, ISO-8601 (default: the wall clock)",
    )


def _add_runner_log(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--runner-log",
        default=None,
        help="append one JSON line per subprocess call to this path",
    )


def _add_repo(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", default=None, help="git repository to operate in (default: the cwd)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="valor-improve-release",
        description=(
            "Release lifecycle for qualified improvement candidates: propose, drill, "
            "approve, open-pr, expose, close-window, rollback, withdraw; the promotion "
            "gate; the recursive comparison; the claim report. One JSON object on stdout; "
            "exit 0 on success, 2 on a refusal, 1 on an unexpected error."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("propose", help="write a proposed release from an accept verdict")
    p.add_argument("--evaluation", required=True, help="ImprovementEvaluation id")
    p.add_argument("--kind", required=True, help="core_workflow | evaluator | infrastructure")
    p.add_argument("--candidate-ref", required=True, help="git ref of the candidate")
    p.add_argument("--surfaces", nargs="+", required=True, help="paths the candidate touches")
    p.add_argument("--rollback-plan", required=True, help="JSON file: {kind, verify, propagation}")
    p.add_argument(
        "--observation",
        required=True,
        help="JSON file: {window_days, baseline_window_days, metrics}",
    )
    p.add_argument("--base-revision", default=None, help="base SHA when the manifest lacks one")
    p.add_argument("--calibration-ref", default=None, help="evaluator releases only")
    p.add_argument("--argument", default=None, help="evaluator releases only: the written case")
    _add_repo(p)
    _add_runner_log(p)
    _add_now(p)
    _add_common(p)
    p.set_defaults(func=cmd_propose)

    p = subparsers.add_parser("drill", help="run the rollback drill; --sweep removes stale slots")
    p.add_argument("--release", default=None, help="ImprovementRelease id")
    p.add_argument("--root", default=None, help="retention root (default: the content path)")
    p.add_argument("--sweep", action="store_true", help="remove stale drill worktrees instead")
    _add_repo(p)
    _add_runner_log(p)
    _add_common(p)
    p.set_defaults(func=cmd_drill)

    p = subparsers.add_parser("approve", help="a human approves a drilled release")
    p.add_argument("--release", required=True)
    p.add_argument("--approved-by", required=True, help="the approving human's name")
    _add_now(p)
    _add_common(p)
    p.set_defaults(func=cmd_approve)

    p = subparsers.add_parser("open-pr", help="gh pr create from the candidate ref")
    p.add_argument("--release", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--title", default=None)
    _add_repo(p)
    _add_runner_log(p)
    _add_common(p)
    p.set_defaults(func=cmd_open_pr)

    p = subparsers.add_parser("expose", help="record the merged PR; anchors the window on it")
    p.add_argument("--release", required=True)
    _add_repo(p)
    _add_runner_log(p)
    _add_now(p)
    _add_common(p)
    p.set_defaults(func=cmd_expose)

    p = subparsers.add_parser(
        "close-window", help="score the observation window; --due lists windows past their end"
    )
    p.add_argument("--release", default=None)
    p.add_argument("--force", action="store_true", help="close before the window end")
    p.add_argument("--reason", default=None, help="required with --force")
    p.add_argument("--due", action="store_true", help="list observing releases past their end")
    _add_now(p)
    _add_common(p)
    p.set_defaults(func=cmd_close_window)

    p = subparsers.add_parser("rollback", help="revert the merge on origin and push it")
    p.add_argument("--release", required=True)
    p.add_argument("--reason", required=True)
    p.add_argument(
        "--branch",
        default=None,
        help="push the revert to this branch instead of main and print the gh pr create command",
    )
    p.add_argument("--root", default=None, help="retention root for the revert worktree")
    _add_repo(p)
    _add_runner_log(p)
    _add_common(p)
    p.set_defaults(func=cmd_rollback)

    p = subparsers.add_parser("withdraw", help="withdraw a proposed or approved release")
    p.add_argument("--release", required=True)
    p.add_argument("--reason", required=True)
    _add_common(p)
    p.set_defaults(func=cmd_withdraw)

    p = subparsers.add_parser("show", help="the release row, its drill record, and drill log")
    p.add_argument("--release", required=True)
    _add_common(p)
    p.set_defaults(func=cmd_show)

    p = subparsers.add_parser("gate", help="the promotion gate and its unmet preconditions")
    _add_common(p)
    p.set_defaults(func=cmd_gate)

    compare = subparsers.add_parser("compare", help="the recursive comparison of two processes")
    compare_sub = compare.add_subparsers(dest="compare_command", required=True)

    p = compare_sub.add_parser("fresh", help="split candidate case ids into fresh and excluded")
    p.add_argument("--candidates", nargs="+", required=True, help="ImprovementCase ids")
    _add_common(p)
    p.set_defaults(func=cmd_compare_fresh)

    p = compare_sub.add_parser("freeze", help="freeze a comparison contract")
    p.add_argument(
        "--arm-a",
        default=None,
        help="sha256: digest or process spec JSON file (default: the current revision)",
    )
    p.add_argument("--arm-b", required=True, help="sha256: digest or process spec JSON file")
    p.add_argument("--opportunities", nargs="+", required=True, help="fresh ImprovementCase ids")
    p.add_argument(
        "--budget",
        required=True,
        help="JSON file: {unit2_usd, unit3_usd, subscription_turns, wall_seconds}",
    )
    p.add_argument("--minimum-worthwhile-effect", type=float, default=0.0)
    _add_common(p)
    p.set_defaults(func=cmd_compare_freeze)

    p = compare_sub.add_parser("run", help="run a frozen comparison and write its evaluation")
    p.add_argument("--experiment", required=True, help="the frozen ImprovementExperiment id")
    p.add_argument(
        "--arm-runner",
        default=None,
        help="module:attr of an ArmRunner, imported at run time (default: the registry)",
    )
    p.add_argument("--rng-seed", type=int, default=None)
    _add_common(p)
    p.set_defaults(func=cmd_compare_run)

    revision = subparsers.add_parser("revision", help="model revision repairs")
    revision_sub = revision.add_subparsers(dest="revision_command", required=True)

    p = revision_sub.add_parser("supersede", help="move one current revision to superseded")
    p.add_argument("--revision", required=True, help="ImprovementModelRevision id")
    p.add_argument("--reason", required=True)
    _add_common(p)
    p.set_defaults(func=cmd_revision_supersede)

    p = subparsers.add_parser("report", help="the three-level claim report")
    p.add_argument("--render", action="store_true", help="plain text instead of JSON")
    _add_common(p)
    p.set_defaults(func=cmd_report)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _refusal_types() -> tuple[type[Exception], ...]:
    from tools.improvement_recursion.arms import ArmRunnerAbsent
    from tools.improvement_recursion.compare import ComparisonRefused
    from tools.improvement_release.drill import DrillRefused
    from tools.improvement_release.lifecycle import ReleaseRefused

    return (ReleaseRefused, DrillRefused, ComparisonRefused, ArmRunnerAbsent)


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv``, run one subcommand, print one JSON object, return the exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except _refusal_types() as exc:
        _emit({"refused": True, "code": exc.code, "detail": exc.detail})
        return EXIT_REFUSED
    except (ValueError, argparse.ArgumentTypeError) as exc:
        # A hand supersede without a reason, an unparsable --now, an unreadable
        # plan file, a process spec that fails validation: refused, never a crash.
        _emit({"refused": True, "code": "INVALID_ARGUMENT", "detail": str(exc)})
        return EXIT_REFUSED
    except Exception:  # noqa: BLE001 -- the CLI boundary: traceback to stderr, exit 1
        traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR
    if isinstance(result, str):
        sys.stdout.write(result.rstrip("\n") + "\n")
        sys.stdout.flush()
    else:
        _emit(result)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
