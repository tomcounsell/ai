"""CLI tool for writing SDLC stage markers to the issue-keyed PipelineLedger.

Invoked by SDLC skills (do-issue, do-plan, do-plan-critique, do-pr-review, do-docs)
to record stage start/completion without depending on the bridge hooks.

Issue-keyed ledger (issue #2012 task 2): stage markers are durable state
about the (target_repo, issue_number) pair, not about whatever ephemeral
AgentSession happened to write them. This tool no longer resolves or writes
to an AgentSession at all -- it resolves the caller's ``run_id`` against the
per-issue lease (``models.session_lifecycle.touch_issue_lock``) and writes to
``PipelineStateMachine.for_issue(target_repo, issue_number)`` instead. See
``agent/pipeline_ledger.py`` and ``docs/plans/sdlc-issue-keyed-stage-ledger.md``.

Usage:
    python -m tools.sdlc_stage_marker --stage DOCS --status in_progress \
        --issue-number 941 --run-id <hex>
    python -m tools.sdlc_stage_marker --stage DOCS --status completed \
        --issue-number 941 --run-id <hex>
    python -m tools.sdlc_stage_marker --stage CRITIQUE --status skipped \
        --issue-number 941 --run-id <hex>
    python -m tools.sdlc_stage_marker --help

Run identity (issue #2003): this tool is state-mutating and REQUIRES
``--run-id`` (the run identity emitted by ``sdlc-tool session-ensure``).
Missing flag is a named non-zero error (``RUN_ID_REQUIRED``) — no mint, no
adopt. A foreign run_id refuses the write with an ``ISSUE_LOCKED``
diagnostic (exit 1).

``--issue-number`` is likewise REQUIRED for a real write: the ledger key is
``(target_repo, issue_number)`` and there is no session left to derive an
issue number from. ``--session-id`` is still accepted for CLI-flag backward
compatibility but is no longer used to resolve anything.

Degradation contract (D7 — loud failure, quiet absence), rebuilt around the
lease instead of a session (issue #2012 task 2):

    - ABSENT — Redis itself is unreachable (a genuine infra outage): emit a
      degraded marker ({"status": "degraded", ...}) and exit 0. This is the
      one case that stays QUIET.
    - LEASE_ABSENT / ISSUE_LOCKED / TARGET_REPO_MISSING — the lease for this
      run_id+issue is missing, foreign, or carries no pinned target_repo.
      There is no session to fall back to resolving anymore, so ALL of
      these are now LOUD: print a clear stderr diagnostic and exit 1. This
      replaces the old PRESENT_NO_SESSION quiet no-op.
    - STATE_MACHINE_REJECTED / STATE_MACHINE_RAISED — the lease is valid but
      the state-machine write itself rejects (misorder, or a predecessor
      backfill blocked by the verdict invariant) or raises: print a clear
      stderr diagnostic carrying the underlying message and exit NON-ZERO.
      The idempotent already-completed path stays exit 0.

Predecessor backfill (issue #1916): both the `in_progress` and `completed`
write paths opt into `PipelineStateMachine`'s predecessor backfill
(`start_stage(..., backfill_predecessors=True)` / `_backfill_predecessors()`)
because a marker write records reality, not an ordering decision — reaching a
stage implies its ISSUE-rooted spine of predecessors was reached too, even if
nothing ever wrote their markers. A fresh pipeline's first write (e.g. PLAN
`in_progress` while ISSUE is still `ready`) now persists instead of hitting
WRITE_FAILED. STATE_MACHINE_REJECTED still fires for a genuine misorder or a `failed`
predecessor — backfill never promotes over a `failed` state. See
"Predecessor Backfill (Opt-In)" in `docs/features/pipeline-state-machine.md`.

Backfill vs. the verdict invariant (issue #2554): when the spine the backfill
would promote contains a REVIEW or CRITIQUE with no recorded verdict, the
verdict invariant (#2415) WINS and the backfill (#2399) is refused. A REVIEW
`completed` marker is what `tools/merge_predicate.py` gates the merge on, so
allowing a backfill to mint one without a verdict would turn any
`--stage DOCS --status completed` call into a way to forge an approval. The
refusal is reported as STATE_MACHINE_REJECTED with the invariant's own remedy
text ("re-run REVIEW ... to record a verdict, then retry").

`--status skipped` (issue #2577) is the honest way past that refusal for work
that never entered the pipeline — a hand-authored fix, a review-derived
follow-up, a dependabot bump. Such an issue has no plan document, so CRITIQUE
has nothing to critique and no truthful verdict for it can ever exist; before
this status existed the only routes through the merge gate were to write a
synthetic CRITIQUE verdict (the forgery above) or to break-glass, and the gate
trained everyone to break glass. The skip is VERIFIED, never asserted:

    - Only PLAN and CRITIQUE are skippable (`pipeline_state.SKIPPABLE_STAGES`).
      REVIEW/DOCS/MERGE are refused as STAGE_NOT_SKIPPABLE — a skippable REVIEW
      would be a way to merge with no review at all.
    - The issue must have NO plan document, checked here rather than claimed by
      the caller; a resolvable plan is refused as PLAN_EXISTS_NOT_SKIPPABLE.
    - The stage must show no sign of having run: no recorded verdict, no
      recorded dispatch, and a `pending`/`ready` status. Otherwise
      STAGE_RAN_NOT_SKIPPABLE.
    - Every probe fails CLOSED: an unreadable ledger or an errored plan lookup
      refuses the skip rather than granting it.

The predecessor backfill never writes `skipped` itself — only this explicit,
lease-gated, precondition-verified path does. That asymmetry is what keeps the
verdict invariant load-bearing.

TOCTOU close (issue #2012, Risk 5): the lease is peeked once up front to
resolve ``target_repo``, then RE-VALIDATED non-peek immediately before the
actual mutation (right before ``start_stage``/``complete_stage``/
``_backfill_predecessors``) -- never trusting the earlier peek across the
gap. A foreign run that took the lease in that window refuses the write.

Exit codes:
    0 — success, degraded (Redis absent), or idempotent no-op
    1 — lease absent/foreign/repo-less, or a genuine state-machine write
        rejection (the loud cases)
    2 — invalid arguments (missing --run-id)

Output:
    {"status": "degraded", "stage": ..., "reason": ...} when Redis is
        unreachable (exit 0)
    {"stage": "DOCS", "status": "completed"} on success (exit 0)
    {"reason": "<NAMED_ERROR>"} + stderr diagnostic on genuine failure (exit 1)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid

# The lease helpers are reached through the MODULE object, never snapshotted
# into these globals with a `from`-import: a snapshot taken during a lazy
# first import that lands inside an active `unittest.mock` patch freezes the
# MagicMock here for the life of the process and silently inverts every lease
# check in this file (#2469, #2637). Guarded by
# tests/unit/test_sdlc_lease_helper_binding.py.
from tools import _sdlc_utils
from tools._sdlc_run_identity import heal_missing_run_id, maybe_heal_after_write

logger = logging.getLogger(__name__)

# Valid stages for marker writes (all pipeline stages including PATCH)
_VALID_STAGES = frozenset(
    ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
)

# Status values accepted by this tool (maps to state machine calls)
_VALID_STATUSES = frozenset(["in_progress", "completed", "skipped"])

# Dispatch-history skill name per skippable stage. A recorded dispatch is
# affirmative evidence the pipeline DID reach the stage, which disqualifies it
# from being recorded as never-dispatched.
_SKIP_STAGE_SKILL = {"PLAN": "/do-plan", "CRITIQUE": "/do-plan-critique"}

# Tri-state substrate probe outcomes (D7).
SUBSTRATE_ABSENT = "ABSENT"
SUBSTRATE_PRESENT = "PRESENT"

# Error sentinels already diagnosed (stderr message printed) at the point of
# failure -- main() must not print a second, contradictory generic message
# for these.
_DIAGNOSED_ERRORS = frozenset(
    [
        "lease_absent",
        "issue_locked",
        "target_repo_missing",
        "lease_lost",
        "review_verdict_missing",
        "review_trailer_missing",
        "review_artifact_missing",
        "critique_verdict_missing",
        "stage_not_skippable",
        "plan_exists_not_skippable",
        "stage_ran_not_skippable",
        "state_machine_rejected",
        "state_machine_raised",
    ]
)


def _review_verdict_readable(issue_number: int | None) -> bool:
    """Return True iff a substrate REVIEW verdict is readable for the issue.

    WS3c (issue #2062): backs the invariant *marker-completed ⇒
    verdict-readable* — the REVIEW ``completed`` marker is refused when this
    returns False, closing the "post GitHub APPROVED but skip the substrate
    ``verdict record``" hole by construction.

    Thin delegate to ``tools.sdlc_verdict._review_verdict_readable`` (issue
    #2305 defect 4) — the shared implementation used by both this direct
    ``write_marker`` completed-path gate and the
    ``_backfill_predecessors`` scan-phase gate, so the two paths cannot
    drift apart. Fails CLOSED (False → refusal) on any error.
    """
    from tools.sdlc_verdict import _review_verdict_readable as _shared

    return _shared(issue_number)


def _review_trailer_present(issue_number: int | None) -> bool:
    """Return True iff an APPROVED REVIEW verdict carries a well-formed head_sha trailer.

    Issue #2193: closes a gap in ``_review_verdict_readable`` -- that probe is
    truthiness-only, so an APPROVED verdict recorded WITHOUT a ``REVIEW_CONTEXT
    head_sha=<40-hex>`` trailer still reads as "readable" and let the REVIEW
    ``completed`` marker through.

    Thin delegate to ``tools.sdlc_verdict._review_trailer_present`` (issue
    #2305 defect 4) — see that module for the shared implementation. Fails
    CLOSED (False -> refusal) on any error.
    """
    from tools.sdlc_verdict import _review_trailer_present as _shared

    return _shared(issue_number)


def _critique_verdict_readable(issue_number: int | None) -> bool:
    """Return True iff a substrate CRITIQUE verdict is readable for the issue.

    WS-C (issue #2124): the structural twin of ``_review_verdict_readable`` —
    backs the invariant *CRITIQUE marker-completed ⇒ verdict-readable*.

    Thin delegate to ``tools.sdlc_verdict._critique_verdict_readable`` (issue
    #2305 defect 4) — see that module for the shared implementation. Fails
    CLOSED (False → refusal) on any error.
    """
    from tools.sdlc_verdict import _critique_verdict_readable as _shared

    return _shared(issue_number)


def _review_artifact_posted(issue_number: int | None, target_repo: str | None = None) -> bool:
    """Return True iff a posted REVIEW artifact is verifiable on GitHub.

    WS-D (issue #2124): backs the invariant *REVIEW marker-completed ⇒ a posted
    review artifact exists*. Even with a readable substrate verdict (WS3c), a fork
    that exited while its judge subagents were still in flight leaves no
    ``## Review:`` comment and no formal review on the PR — the #2112 miss. This
    probe queries the PR for either a formal GitHub review OR a ``## Review:``
    issue comment (the same artifact ``/do-merge`` reads and ``post-review.md``
    verifies) and refuses the completion marker when neither exists.

    Fails CLOSED (False → refusal) on any error — an unverifiable artifact must
    never let the completion marker through; the WS3b recovery row owns the
    refused no-artifact state (re-dispatch ``/do-pr-review``).
    """
    if not issue_number:
        return False
    try:
        import subprocess

        from config.settings import settings
        from tools.sdlc_stage_query import _lookup_pr

        gh_timeout = settings.timeouts.git_subprocess_s
        # state="all": the artifact question is historical, not in-flight. Under the
        # default "open" a merged PR resolves to None and this probe returned False
        # before ever looking for the artifact -- unreachable for ANY merged PR, in
        # exactly the state where the artifact is guaranteed to exist (issue #2539).
        pr_number = _lookup_pr(issue_number, repo=target_repo, state="all")
        if not pr_number:
            return False

        repo_args = ["--repo", target_repo] if target_repo else []

        # 1. A formal GitHub review with a non-empty state
        #    (APPROVED / CHANGES_REQUESTED / COMMENTED).
        rev = subprocess.run(
            ["gh", "pr", "view", str(pr_number), *repo_args, "--json", "reviews"],
            capture_output=True,
            text=True,
            timeout=gh_timeout,
        )
        if rev.returncode == 0:
            data = json.loads(rev.stdout or "{}")
            reviews = data.get("reviews") or []
            if any(isinstance(r, dict) and r.get("state") for r in reviews):
                return True

        # 2. A self-authored ``## Review:`` issue comment (post-review.md's own marker).
        repo_slug = target_repo
        if not repo_slug:
            slug_proc = subprocess.run(
                ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                capture_output=True,
                text=True,
                timeout=gh_timeout,
            )
            repo_slug = slug_proc.stdout.strip() if slug_proc.returncode == 0 else None
        if repo_slug:
            com = subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/{repo_slug}/issues/{pr_number}/comments",
                    "--jq",
                    '[.[] | select(.body | startswith("## Review:"))] | length',
                ],
                capture_output=True,
                text=True,
                timeout=gh_timeout,
            )
            if com.returncode == 0 and com.stdout.strip().isdigit():
                if int(com.stdout.strip()) > 0:
                    return True
        return False
    except Exception as e:
        logger.debug(
            f"sdlc_stage_marker: REVIEW artifact-presence probe failed for "
            f"issue #{issue_number}: {e} -- treating as not posted (refusal)"
        )
        return False


def _skip_precondition_error(
    stage: str, issue_number: int | None, ledger=None
) -> tuple[str, str] | None:
    """Verify a ``--status skipped`` write, returning ``(reason, message)`` on refusal.

    Issue #2577. Returns ``None`` only when every precondition holds. Each probe
    fails CLOSED: an unresolvable ledger, an errored plan lookup or a malformed
    dispatch history all refuse the skip, because "cannot confirm the stage never
    ran" must never read as "the stage never ran".

    The point of doing this here rather than trusting the caller is that a skip is
    otherwise indistinguishable from a claim. A caller cannot skip a CRITIQUE for
    an issue whose plan exists, nor one that already recorded a verdict — the two
    states where a real critique either is owed or already happened.

    ``ledger`` is the caller's already-resolved ``PipelineLedger`` for
    ``(target_repo, issue_number)``. Reading verdict/dispatch history straight
    off it rather than re-resolving keeps the probe on the exact record the
    write will land on, and avoids refusing a legitimate first skip on a ledger
    that has no persisted stage state yet.
    """
    from agent.pipeline_state import SKIPPABLE_STAGES

    if stage not in SKIPPABLE_STAGES:
        return (
            "STAGE_NOT_SKIPPABLE",
            f"stage {stage} can never be recorded skipped; only "
            f"{sorted(SKIPPABLE_STAGES)} may be. REVIEW, DOCS and MERGE are "
            "permanently excluded — each is a gate the merge predicate reads, so a "
            "skippable one would be a way to merge without the guarantee it exists "
            "to provide.",
        )

    if not issue_number:
        return (
            "STAGE_RAN_NOT_SKIPPABLE",
            "no issue number, so the skip preconditions (plan absence, verdict "
            "absence, dispatch absence) cannot be verified.",
        )

    # 1. A plan document means the stage genuinely applies.
    try:
        from tools._sdlc_utils import find_plan_path

        plan_path = find_plan_path(issue_number)
    except Exception as e:
        return (
            "PLAN_EXISTS_NOT_SKIPPABLE",
            f"the plan-document lookup for issue #{issue_number} failed ({e}); "
            "refusing the skip rather than assuming no plan exists.",
        )
    if plan_path is not None:
        return (
            "PLAN_EXISTS_NOT_SKIPPABLE",
            f"issue #{issue_number} has a plan document ({plan_path}), so {stage} "
            f"applies and must actually run. Dispatch "
            f"{_SKIP_STAGE_SKILL.get(stage, stage)} instead.",
        )

    # 2. A recorded verdict or dispatch is affirmative evidence the stage ran.
    try:
        from tools.sdlc_stage_query import _load_raw_states

        if ledger is None:
            return (
                "STAGE_RAN_NOT_SKIPPABLE",
                f"no pipeline ledger supplied for issue #{issue_number}, so the "
                "stage's dispatch/verdict history cannot be read; refusing the skip.",
            )
        raw = _load_raw_states(ledger)
    except Exception as e:
        return (
            "STAGE_RAN_NOT_SKIPPABLE",
            f"reading the pipeline ledger for issue #{issue_number} failed ({e}); "
            "refusing the skip rather than assuming the stage never ran.",
        )

    verdicts = raw.get("_verdicts")
    if isinstance(verdicts, dict) and verdicts.get(stage):
        return (
            "STAGE_RAN_NOT_SKIPPABLE",
            f"{stage} has a recorded verdict for issue #{issue_number}; a stage that "
            "produced a verdict actually ran and is never retroactively skippable.",
        )

    history = raw.get("_sdlc_dispatches")
    skill = _SKIP_STAGE_SKILL.get(stage)
    if skill and isinstance(history, list):
        if any(isinstance(d, dict) and d.get("skill") == skill for d in history):
            return (
                "STAGE_RAN_NOT_SKIPPABLE",
                f"{skill} was dispatched for issue #{issue_number}; {stage} was reached "
                "by the pipeline and is never retroactively skippable.",
            )

    return None


def probe_substrate() -> str:
    """Probe whether Redis (the issue-lock/ledger substrate) is reachable.

    Returns ``SUBSTRATE_PRESENT`` when a trivial Redis round-trip succeeds;
    ``SUBSTRATE_ABSENT`` on any connection error or import failure. Never
    raises. This is the one case that stays QUIET (degraded marker, exit
    0) -- a genuine infra outage, not an owner/lease problem.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB as _R

        _R.ping()
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: substrate probe failed: {e}")
        return SUBSTRATE_ABSENT
    return SUBSTRATE_PRESENT


def _degraded(stage: str, reason: str) -> dict:
    """Build a visible degraded-mode marker payload (D7)."""
    return {"status": "degraded", "stage": stage, "reason": reason}


def _record_marker_telemetry(
    result: dict, exit_code: int, stage: str, issue_number: int | None, run_id: str | None
) -> None:
    """Increment the per-run ok/fail counter for a completed write (issue #2451).

    Best-effort observability wrapper (Part 3). Rules:
    - Invalid stage/status early-returns are NOT real writes -> skip.
    - The degraded (Redis-absent) path (``status == "degraded"``) is the
      deliberate QUIET case -> NOT counted as a fail.
    - exit_code 0 (success / idempotent) -> ok increment.
    - exit_code != 0 and not degraded (state-machine rejection / LEASE_ABSENT /
      ISSUE_LOCKED) -> fail increment, recording the stage as last_failed_stage.

    A counter-write failure NEVER changes the marker outcome (the helper itself
    swallows all errors); this guard just decides ok-vs-fail-vs-skip.
    """
    try:
        if stage not in _VALID_STAGES:
            return
        if isinstance(result, dict) and result.get("status") == "degraded":
            return
        from tools._sdlc_marker_telemetry import record_marker_write

        record_marker_write(issue_number, run_id, ok=(exit_code == 0), stage=stage)
    except Exception:
        # Telemetry is strictly best-effort -- never let it perturb the marker.
        pass


def write_marker(
    stage: str,
    status: str,
    session_id: str | None = None,
    issue_number: int | None = None,
    run_id: str | None = None,
) -> tuple[dict, int]:
    """Write a stage marker to the issue-keyed PipelineLedger.

    Args:
        stage: Pipeline stage name (e.g., "DOCS", "REVIEW").
        status: "in_progress" or "completed".
        session_id: Unused — accepted only for CLI-flag backward compat.
        issue_number: The GitHub issue number. Required for a real write
            (the ledger key is ``(target_repo, issue_number)``).
        run_id: The caller's run identity (the CLI's ``--run-id``).

    Returns:
        A ``(result, exit_code)`` tuple (D7 tri-state contract, rebuilt
        around the lease):
        - success / degraded (Redis absent) / idempotent no-op → exit_code 0
        - lease absent/foreign/repo-less, or a genuine write rejection →
          exit_code 1 (the loud cases; stderr already carries a diagnostic)

    Every write outcome (except the invalid-arg and degraded paths) also
    increments a best-effort per-run ok/fail counter for run-health
    observability (issue #2451) -- see :func:`_record_marker_telemetry`.
    """
    result, exit_code = _write_marker_impl(
        stage=stage,
        status=status,
        session_id=session_id,
        issue_number=issue_number,
        run_id=run_id,
    )
    _record_marker_telemetry(result, exit_code, stage, issue_number, run_id)
    return result, exit_code


def _write_marker_impl(
    stage: str,
    status: str,
    session_id: str | None = None,
    issue_number: int | None = None,
    run_id: str | None = None,
) -> tuple[dict, int]:
    """The marker-write body (see :func:`write_marker` for the contract)."""
    del session_id  # unused -- CLI-flag backward compat only

    if stage not in _VALID_STAGES:
        logger.debug(f"sdlc_stage_marker: invalid stage {stage!r}")
        return {}, 0

    if status not in _VALID_STATUSES:
        logger.debug(f"sdlc_stage_marker: invalid status {status!r}")
        return {}, 0

    # ABSENT is the one QUIET case: Redis itself is unreachable.
    if probe_substrate() == SUBSTRATE_ABSENT:
        return _degraded(stage, "state not persisted — substrate absent"), 0

    target_repo, lease_error = _sdlc_utils.resolve_ledger_lease(issue_number, run_id)
    if lease_error is not None:
        reason = lease_error.get("reason", "LEASE_ABSENT")
        if reason == "ISSUE_LOCKED":
            print(
                f"[ERROR] ISSUE_LOCKED: issue lock held by a foreign run "
                f"(run_id={lease_error.get('owner_run_id')}, "
                f"session={lease_error.get('owner_session_id')}); marker write refused.",
                file=sys.stderr,
            )
            return {"error": "issue_locked", **lease_error}, 1
        print(
            f"[ERROR] LEASE_ABSENT: no live issue lease for issue #{issue_number} "
            f"owned by run_id={run_id!r}; run `sdlc-tool session-ensure` first. "
            "Marker write refused.",
            file=sys.stderr,
        )
        return {"error": "lease_absent", **lease_error}, 1

    if not target_repo:
        print(
            f"[ERROR] TARGET_REPO_MISSING: the issue lease for issue #{issue_number} "
            "has no pinned target_repo; refusing to write a PipelineLedger record "
            "with a None key component.",
            file=sys.stderr,
        )
        return {"error": "target_repo_missing", "reason": "TARGET_REPO_MISSING"}, 1

    try:
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine.for_issue(target_repo, issue_number)

        if status == "in_progress":
            if not _sdlc_utils.revalidate_ledger_lease(issue_number, run_id, target_repo):
                print(
                    f"[ERROR] ISSUE_LOCKED: lease for issue #{issue_number} was taken "
                    "by a foreign run between resolve and write; marker write refused.",
                    file=sys.stderr,
                )
                return {"error": "lease_lost", "reason": "ISSUE_LOCKED"}, 1
            try:
                sm.start_stage(stage, backfill_predecessors=True)
            except ValueError as e:
                # Predecessor not completed — inconsistent pipeline state, not a
                # lease failure. The message names the concrete rejection and
                # the remedy; print it (issue #2554: this was routed to
                # logger.debug, so the one actionable line never reached the
                # operator and the failure looked like a bare exit 1).
                # #2740: this says the MARKER write was refused and nothing
                # more. `write_marker` cannot see writes its callers already
                # committed — `finalize` records the verdict one step earlier —
                # so asserting non-persistence here is a claim this
                # function has no standing to make. Follow the
                # STATE_MACHINE_RAISED precedent below: name the refusal, point
                # at `stage-query`.
                print(
                    f"[ERROR] STATE_MACHINE_REJECTED: start_stage({stage}) refused "
                    f"for issue #{issue_number}: {e} Marker write refused; other "
                    "state may already have been persisted by the caller — re-read "
                    "with `sdlc-tool stage-query`.",
                    file=sys.stderr,
                )
                return {
                    "error": "state_machine_rejected",
                    "reason": "STATE_MACHINE_REJECTED",
                }, 1
        elif status == "skipped":
            # Issue #2577: record that the pipeline never dispatched this stage
            # because it does not apply to this issue. Verified, not asserted —
            # see _skip_precondition_error.
            if sm.states.get(stage) == "skipped":
                return {"stage": stage, "status": status}, 0
            refusal = _skip_precondition_error(stage, issue_number, ledger=sm._ledger)
            if refusal is not None:
                reason, message = refusal
                print(f"[ERROR] {reason}: {message} Marker write refused.", file=sys.stderr)
                return {"error": reason.lower(), "reason": reason}, 1
            if not _sdlc_utils.revalidate_ledger_lease(issue_number, run_id, target_repo):
                print(
                    f"[ERROR] ISSUE_LOCKED: lease for issue #{issue_number} was taken "
                    "by a foreign run between resolve and write; marker write refused.",
                    file=sys.stderr,
                )
                return {"error": "lease_lost", "reason": "ISSUE_LOCKED"}, 1
            try:
                sm.skip_stage(
                    stage,
                    reason=(
                        f"no plan document for issue #{issue_number}; {stage} was never "
                        "dispatched and does not apply to this work"
                    ),
                )
            except ValueError as e:
                # #2740: scoped to the marker write, for the same reason as the
                # start_stage site above.
                print(
                    f"[ERROR] STAGE_RAN_NOT_SKIPPABLE: skip_stage({stage}) refused for "
                    f"issue #{issue_number}: {e} Marker write refused; other state may "
                    "already have been persisted by the caller — re-read with "
                    "`sdlc-tool stage-query`.",
                    file=sys.stderr,
                )
                return {
                    "error": "stage_ran_not_skippable",
                    "reason": "STAGE_RAN_NOT_SKIPPABLE",
                }, 1
        elif status == "completed":
            # Ensure stage is in_progress before completing
            current = sm.states.get(stage, "pending")
            if current == "completed":
                # Already completed — idempotent no-op (exit 0). No write, no
                # need to re-validate the lease. (An already-completed REVIEW
                # with no verdict is a pre-fix state owned by the router's
                # no-verdict recovery row, not retroactively refused here.)
                return {"stage": stage, "status": status}, 0
            if stage == "REVIEW" and not _review_verdict_readable(issue_number):
                # WS3c (issue #2062): marker-completed ⇒ verdict-readable.
                # Refuse the completion write with a NAMED error; the WS3b
                # recovery row owns the resulting no-verdict state and routes
                # back to /do-pr-review instead of deadlocking.
                print(
                    f"[ERROR] REVIEW_VERDICT_MISSING: no readable REVIEW verdict for "
                    f"issue #{issue_number}; run `sdlc-tool verdict record --stage REVIEW ...` "
                    "before marking REVIEW completed. Marker write refused.",
                    file=sys.stderr,
                )
                return {
                    "error": "review_verdict_missing",
                    "reason": "REVIEW_VERDICT_MISSING",
                }, 1
            # Issue #2193: APPROVED-path-only conjunct (Risk 1) -- ANDed
            # alongside _review_verdict_readable, which is truthiness-only and
            # would otherwise let an APPROVED verdict with no head_sha trailer
            # through. _review_trailer_present pass-throughs (True) any
            # non-APPROVED verdict, so CHANGES REQUESTED / BLOCKED_ON_CONFLICT
            # / PR_CLOSED markers are unaffected and continue to pass as before.
            if stage == "REVIEW" and not _review_trailer_present(issue_number):
                print(
                    f"[ERROR] REVIEW_TRAILER_MISSING: recorded REVIEW verdict for "
                    f"issue #{issue_number} is APPROVED but carries no well-formed "
                    "`REVIEW_CONTEXT head_sha=<40-hex>` trailer; run `sdlc-tool "
                    "verdict finalize` (or re-record with the trailer) before "
                    "marking REVIEW completed. Marker write refused.",
                    file=sys.stderr,
                )
                return {
                    "error": "review_trailer_missing",
                    "reason": "REVIEW_TRAILER_MISSING",
                }, 1
            if stage == "REVIEW" and not _review_artifact_posted(issue_number, target_repo):
                # WS-D (issue #2124): marker-completed ⇒ posted review artifact.
                # A readable verdict is necessary but not sufficient — a fork that
                # exited with its judges still in flight (the #2112 miss) records
                # no ``## Review:`` comment and no formal review. Refuse with a
                # NAMED error; the WS3b recovery row owns the no-artifact state
                # (re-dispatch /do-pr-review) rather than deadlocking.
                print(
                    f"[ERROR] REVIEW_ARTIFACT_MISSING: no posted REVIEW artifact "
                    f"(GitHub review or `## Review:` comment) verifiable for issue "
                    f"#{issue_number}; post the review before marking REVIEW completed. "
                    "Marker write refused.",
                    file=sys.stderr,
                )
                return {
                    "error": "review_artifact_missing",
                    "reason": "REVIEW_ARTIFACT_MISSING",
                }, 1
            if stage == "CRITIQUE" and not _critique_verdict_readable(issue_number):
                # WS-C (issue #2124): marker-completed ⇒ verdict-readable, the
                # CRITIQUE twin of the REVIEW WS3c gate. Refuse the completion
                # write with a NAMED error; the refused state routes back to
                # /do-plan-critique (a re-dispatch) rather than deadlocking.
                print(
                    f"[ERROR] CRITIQUE_VERDICT_MISSING: no readable CRITIQUE verdict for "
                    f"issue #{issue_number}; run `sdlc-tool verdict record --stage CRITIQUE ...` "
                    "before marking CRITIQUE completed. Marker write refused.",
                    file=sys.stderr,
                )
                return {
                    "error": "critique_verdict_missing",
                    "reason": "CRITIQUE_VERDICT_MISSING",
                }, 1
            if not _sdlc_utils.revalidate_ledger_lease(issue_number, run_id, target_repo):
                print(
                    f"[ERROR] ISSUE_LOCKED: lease for issue #{issue_number} was taken "
                    "by a foreign run between resolve and write; marker write refused.",
                    file=sys.stderr,
                )
                return {"error": "lease_lost", "reason": "ISSUE_LOCKED"}, 1
            if current not in ("in_progress", "ready"):
                # Reaching this stage implies the ISSUE-rooted spine of
                # predecessors was reached too — backfill them directly
                # (NOT via start_stage, whose `current == "in_progress"`
                # no-op would otherwise skip backfill once we pre-set the
                # target stage) before forcing the target to in_progress so
                # complete_stage() accepts it.
                #
                # Contract precedence (issue #2554): this backfill (#2399) and
                # the verdict invariant (#2415) collide whenever the spine it
                # would promote includes a REVIEW or CRITIQUE with no recorded
                # verdict. THE VERDICT INVARIANT WINS. The backfill is a
                # convenience that heals a lost marker; the invariant is the
                # guarantee that a REVIEW `completed` marker means a review
                # actually happened, and `tools/merge_predicate.py` gates the
                # merge on exactly that. Letting the backfill mint an
                # unverified REVIEW completion would make any
                # `--stage DOCS --status completed` call a way to forge one.
                # So `_backfill_predecessors` raises, and this reports the
                # raise with its remedy instead of swallowing it.
                try:
                    sm._backfill_predecessors(stage)
                except ValueError as e:
                    # #2740: the site the issue reports. On the `finalize`
                    # path the verdict and its head SHA are ALREADY durable
                    # when this fires — only the marker is missing — so the
                    # old non-persistence claim cost a fresh investigation
                    # every time it printed. `finalize` re-narrates this
                    # accurately for its own callers; here we claim only what
                    # this function did.
                    print(
                        f"[ERROR] STATE_MACHINE_REJECTED: predecessor backfill for "
                        f"{stage} refused for issue #{issue_number}: {e} "
                        "Marker write refused; other state may already have been "
                        "persisted by the caller — re-read with `sdlc-tool stage-query`.",
                        file=sys.stderr,
                    )
                    return {
                        "error": "state_machine_rejected",
                        "reason": "STATE_MACHINE_REJECTED",
                    }, 1
                sm.states[stage] = "in_progress"
            sm.complete_stage(stage)

        return {"stage": stage, "status": status}, 0

    except Exception as e:
        # Issue #2554: this was `logger.debug`, which made every unexpected
        # write failure indistinguishable from a silent no-op. A marker write
        # is load-bearing; report the concrete exception with a traceback.
        logger.exception(f"sdlc_stage_marker: write_marker({stage}={status}) failed: {e}")
        print(
            f"[ERROR] STATE_MACHINE_RAISED: write_marker({stage}={status}) raised for "
            f"issue #{issue_number}: {type(e).__name__}: {e} "
            "Persisted state is INDETERMINATE; re-read with `sdlc-tool stage-query`.",
            file=sys.stderr,
        )
        return {"error": "state_machine_raised", "reason": "STATE_MACHINE_RAISED"}, 1


def write_issue_marker_cold(status: str, issue_number: int | None) -> tuple[dict, int]:
    """Write a cold ISSUE-stage marker **sessionlessly**, without spawning a session.

    A ``--stage ISSUE`` marker with no ``--run-id`` is issue-creation ledger
    metadata (``/do-issue`` Steps 6/7): there is no legitimate in-flight
    pipeline run to heal. The old path routed it through the #2144 self-heal,
    whose ``ensure_session`` fresh-mint fabricated a runnable-looking
    ``sdlc-local-{N}`` eng anchor (status=running, seeded with "Run the full
    SDLC pipeline") from nothing — the moment ``/do-issue`` filed an issue,
    before any human decided to plan it. That anchor is inert to the worker
    (``is_ledger=True``, #2042) but pollutes the dashboard as a phantom running
    pipeline and holds the issue lease.

    Instead we record the ISSUE marker directly against the issue-keyed ledger:
    contend the issue lease with a fresh run identity (no ``AgentSession`` row),
    write via :func:`write_marker`, and release. The ledger key
    ``(target_repo, issue_number)`` is written exactly as the healed path would
    have, so ``sdlc-tool stage-query`` still shows the marker — but no session
    is created.

    Concurrency: the lease is acquired NX. If a live run already owns it (a
    supervised ``/do-sdlc`` or an in-flight pipeline that lost its ``run_id``
    from context), we do NOT steal or release it — we write the idempotent
    ISSUE marker under that owner's ``run_id`` so the marker still lands without
    disturbing the live run. Only a genuinely unkeyed call (no issue number)
    falls back to the caller's ``RUN_ID_REQUIRED`` refusal.

    This path is deliberately scoped to the ISSUE stage: every later stage
    implies a pipeline already ran (a session exists), so a cold write there is
    pathological and correctly still routes through the normal self-heal.

    Returns:
        A ``(result, exit_code)`` tuple with the same shape as
        :func:`write_marker`.
    """
    if not issue_number:
        return {"error": "RUN_ID_REQUIRED"}, 2

    from models.session_lifecycle import (
        ISSUE_LOCK_TTL_SECONDS,
        release_issue_lock,
        touch_issue_lock,
    )
    from tools._sdlc_utils import _resolve_target_repo

    run_id = uuid.uuid4().hex
    synthetic_session_id = f"sdlc-cold-issue-marker-{issue_number}"
    target_repo = _resolve_target_repo()

    try:
        lock = touch_issue_lock(
            issue_number,
            run_id,
            session_id=synthetic_session_id,
            ttl=ISSUE_LOCK_TTL_SECONDS,
            target_repo=target_repo,
        )
    except Exception as e:
        logger.debug(f"write_issue_marker_cold: lease acquire failed: {e}")
        return {"error": "RUN_ID_REQUIRED"}, 2

    if lock.acquired:
        # Sole owner: write under our fresh identity, then release so the lease
        # never lingers to block a later /do-plan session-ensure for this issue.
        try:
            return write_marker(
                stage="ISSUE", status=status, issue_number=issue_number, run_id=run_id
            )
        finally:
            try:
                release_issue_lock(issue_number, run_id)
            except Exception as e:
                logger.debug(f"write_issue_marker_cold: lease release failed: {e}")

    # A live run already owns the lease. Write the idempotent ISSUE marker under
    # its identity (no release — the lease is not ours) so the marker still lands.
    if lock.owner_run_id:
        return write_marker(
            stage="ISSUE",
            status=status,
            issue_number=issue_number,
            run_id=lock.owner_run_id,
        )

    # No owner and not acquired (malformed/racing lease): defer to the caller's
    # RUN_ID_REQUIRED refusal rather than fabricating anything.
    return {"error": "RUN_ID_REQUIRED"}, 2


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write SDLC stage markers to the issue-keyed PipelineLedger",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--stage",
        required=True,
        help="Pipeline stage name (e.g., DOCS, REVIEW, PLAN)",
    )
    parser.add_argument(
        "--status",
        required=True,
        choices=["in_progress", "completed", "skipped"],
        help=(
            "Status to write. `skipped` (issue #2577) records that the pipeline "
            "never dispatched the stage because it does not apply to this issue; "
            "it is accepted only for PLAN/CRITIQUE and only when this tool can "
            "verify the issue has no plan document and the stage left no verdict "
            "or dispatch behind."
        ),
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Unused — accepted only for CLI-flag backward compatibility.",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number (required for a real write)",
    )
    parser.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help=(
            "Run identity emitted by `sdlc-tool session-ensure` (issue #2003). "
            "REQUIRED for this state-mutating tool; missing -> RUN_ID_REQUIRED."
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)

    stage = args.stage.upper()

    # Run-identity self-heal (issue #2144): a resumed pipeline turn loses the
    # run_id from its context, so a state-mutating write would refuse
    # (RUN_ID_REQUIRED with no flag, LEASE_ABSENT with a stale one) — silently,
    # because the skill wraps marker writes `2>/dev/null || true`. Re-establish
    # identity from the environment (.sdlc-run / active_run_id / live
    # supervisor) and retry, instead of no-op'ing. Only a genuinely unhealable
    # state (foreign live lease, no issue-number) still refuses.
    run_id = args.run_id
    healed_at_gate = False
    if not run_id and stage == "ISSUE":
        # Cold ISSUE marker (no --run-id): issue-creation ledger metadata with no
        # in-flight run to heal. Write it sessionlessly instead of self-healing —
        # the old path fresh-minted a phantom runnable sdlc-local-{N} pipeline
        # anchor from nothing (see write_issue_marker_cold / this hotfix).
        result, exit_code = write_issue_marker_cold(args.status, args.issue_number)
        stdout_result = {k: v for k, v in result.items() if k != "error"}
        print(json.dumps(stdout_result))
        if exit_code == 2:
            # exit 2 covers all three cold-write refusals: no issue number, a
            # touch_issue_lock error, or an unacquired-and-ownerless lease. Keep
            # the message accurate for every one rather than misattributing them
            # all to a missing issue number.
            print(
                "sdlc_stage_marker: RUN_ID_REQUIRED — cold ISSUE marker could not "
                "establish a run identity to write the ledger (no issue number, or "
                "the issue lease was unavailable).",
                file=sys.stderr,
            )
        sys.exit(exit_code)
    if not run_id:
        run_id = heal_missing_run_id(args.issue_number, "stage_marker")
        if not run_id:
            print(
                "sdlc_stage_marker: RUN_ID_REQUIRED — state-mutating calls must pass "
                "--run-id (emitted by `sdlc-tool session-ensure`).",
                file=sys.stderr,
            )
            print(json.dumps({"error": "RUN_ID_REQUIRED"}))
            sys.exit(2)
        healed_at_gate = True

    result, exit_code = write_marker(
        stage=stage,
        status=args.status,
        session_id=args.session_id,
        issue_number=args.issue_number,
        run_id=run_id,
    )
    # A stale run_id whose lease lapsed refuses with LEASE_ABSENT; heal once and
    # retry under the re-established id (at-most-once — skip if we already healed
    # at the front gate).
    if exit_code != 0 and not healed_at_gate:
        healed = maybe_heal_after_write(result, run_id, args.issue_number, "stage_marker")
        if healed:
            run_id = healed
            result, exit_code = write_marker(
                stage=stage,
                status=args.status,
                session_id=args.session_id,
                issue_number=args.issue_number,
                run_id=run_id,
            )
    # Strip internal sentinel keys before printing to stdout so JSON-parsing
    # callers always receive a clean dict (no "error" sentinel leaks out).
    stdout_result = {k: v for k, v in result.items() if k != "error"}
    print(json.dumps(stdout_result))

    if exit_code != 0:
        if result.get("error") in _DIAGNOSED_ERRORS:
            # The lease/target_repo guards already printed their diagnostic
            # above; do not emit a second, contradictory "state-machine
            # write rejected" message — no write was attempted on these
            # paths (or the write was refused before mutating).
            pass
        else:
            # A genuine state-machine write rejection (misorder, exception).
            # A clear stderr diagnostic so a forked sub-skill / operator sees
            # the genuine writeback failure instead of a silent no-op
            # (mirrors sdlc_dispatch's loud-failure path).
            # #2740: the fourth site, and the one outside write_marker. It sits
            # after `maybe_heal_after_write` may have retried the write, so it
            # is an overclaim for the same reason as the three inside. Kept
            # (not deleted) though currently unreachable — every error key
            # write_marker returns is in _DIAGNOSED_ERRORS — because it is what
            # a bare `sdlc-tool stage-marker` caller would see the moment an
            # undiagnosed error key is added.
            print(
                f"sdlc_stage_marker: FAILED to write {stage}={args.status} "
                "(lease resolved, but the state-machine write was rejected or "
                "raised). Persisted state is INDETERMINATE; re-read with "
                "`sdlc-tool stage-query`.",
                file=sys.stderr,
            )
    elif result.get("status") == "degraded":
        # Visible degraded-mode marker (quiet on stderr, but the stdout JSON
        # carries status: degraded so the PM/operator can see it).
        logger.debug(f"sdlc_stage_marker: degraded — {result.get('reason')}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
