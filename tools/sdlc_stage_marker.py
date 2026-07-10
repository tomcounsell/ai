"""CLI tool for writing SDLC stage markers to a PM session's PipelineStateMachine.

Invoked by SDLC skills (do-issue, do-plan, do-plan-critique, do-pr-review, do-docs)
to record stage start/completion without depending on the bridge hooks.

Skills use this as a belt-and-suspenders backup — the bridge pre_tool_use hook
remains the primary marker path for bridge-initiated sessions. This tool handles
local Claude Code sessions where hooks don't fire.

Usage:
    python -m tools.sdlc_stage_marker --stage DOCS --status in_progress --run-id <hex>
    python -m tools.sdlc_stage_marker --stage DOCS --status completed --run-id <hex>
    python -m tools.sdlc_stage_marker --stage REVIEW --status in_progress \
        --session-id <ID> --run-id <hex>
    python -m tools.sdlc_stage_marker --stage PLAN --status completed \
        --issue-number 941 --run-id <hex>
    python -m tools.sdlc_stage_marker --help

Run identity (issue #2003): this tool is state-mutating and REQUIRES
``--run-id`` (the run identity emitted by ``sdlc-tool session-ensure``).
Missing flag is a named non-zero error (``RUN_ID_REQUIRED``) — no mint, no
adopt. A foreign run_id refuses the write with an ``ISSUE_LOCKED``
diagnostic (exit 1).

Environment variables (checked in order if --session-id not provided):
    VALOR_SESSION_ID   — bridge-injected PM session ID
    AGENT_SESSION_ID   — alternative session ID env var

When no session ID is available (local Claude Code sessions), use --issue-number
to resolve the session by GitHub issue number.

Degradation contract (D7 — loud failure, quiet absence):
    A tri-state probe replaces the old binary present/absent check so a missing
    orchestration substrate degrades to a *visible* marker instead of silently
    lagging, while a session-less local invocation stays quiet:

    - ABSENT — cannot import models.agent_session / Redis unreachable
      (ImportError / redis.ConnectionError): emit a degraded marker
      ({"status": "degraded", ...}) and exit 0. This is the non-`ai`-repo case.
    - PRESENT_NO_SESSION — substrate imports and Redis is reachable, but no PM
      session resolves: emit a degraded marker and exit 0 (QUIET). The marker
      cannot tell a legitimate non-`ai` repo apart from an `ai` wiring bug, so
      it must not be noisy.
    - PRESENT_WRITE_FAILED — session resolved but start_stage/complete_stage
      rejects or raises: print a clear stderr diagnostic and exit NON-ZERO.
      Loud is reserved ONLY for this case. The idempotent already-completed
      path stays exit 0.

Predecessor backfill (issue #1916): both the `in_progress` and `completed`
write paths opt into `PipelineStateMachine`'s predecessor backfill
(`start_stage(..., backfill_predecessors=True)` / `_backfill_predecessors()`)
because a marker write records reality, not an ordering decision — reaching a
stage implies its ISSUE-rooted spine of predecessors was reached too, even if
nothing ever wrote their markers. A fresh pipeline's first write (e.g. PLAN
`in_progress` while ISSUE is still `ready`) now persists instead of hitting
PRESENT_WRITE_FAILED. PRESENT_WRITE_FAILED still fires for a genuine misorder
or a `failed` predecessor — backfill never promotes over a `failed` state.
See "Predecessor Backfill (Opt-In)" in `docs/features/pipeline-state-machine.md`.

Ownership gate (issue #1735): when ``--issue-number N`` is explicitly provided,
the resolved session is verified to own issue N via ``session_owns_issue()`` in
``tools._sdlc_utils``. If the check fails (the resolved session belongs to a
different issue — the artifact-divert residual case), the tool prints a stderr
diagnostic and returns exit code 1 with no marker write. The gate does not fire
when ``--issue-number`` is omitted (bridge PM sessions using env-var resolution
are unaffected).

Exit codes:
    0 — success, degraded (substrate absent / no session), or idempotent no-op
    1 — substrate present, session resolved, but the marker write genuinely
        failed (the only loud case; includes ownership-guard rejection)

Output:
    {"status": "degraded", "stage": ..., "reason": ...} when the substrate is
        absent or no session resolves (exit 0)
    {"stage": "DOCS", "status": "completed"} on success (exit 0)
    {} + stderr diagnostic on genuine write failure (exit 1)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from tools._sdlc_utils import (
    check_run_ownership,
    find_session,
    renew_issue_lock_for_session,
    session_owns_issue,
)

logger = logging.getLogger(__name__)

# Valid stages for marker writes (all pipeline stages including PATCH)
_VALID_STAGES = frozenset(
    ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
)

# Status values accepted by this tool (maps to state machine calls)
_VALID_STATUSES = frozenset(["in_progress", "completed"])

# Tri-state substrate probe outcomes (D7).
SUBSTRATE_ABSENT = "ABSENT"
SUBSTRATE_PRESENT = "PRESENT"


def probe_substrate() -> str:
    """Probe whether the orchestration substrate (models + Redis) is reachable.

    Returns ``SUBSTRATE_PRESENT`` when ``models.agent_session`` imports AND a
    trivial Redis-backed query succeeds; ``SUBSTRATE_ABSENT`` on ImportError or
    any connection error. Never raises.

    This distinguishes the genuinely-absent substrate (a non-`ai` repo, where a
    degraded marker is correct) from a present substrate that merely has no
    matching PM session (handled separately by the caller).
    """
    try:
        from models.agent_session import AgentSession
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: substrate import failed: {e}")
        return SUBSTRATE_ABSENT

    try:
        # A cheap reachability check that forces a Redis round-trip. ``count``
        # is evaluated eagerly (unlike the lazy ``filter`` QueryBuilder), so a
        # Redis connection error surfaces here rather than masquerading as
        # "no session".
        AgentSession.query.count(session_type="eng")
    except Exception as e:
        logger.debug(f"sdlc_stage_marker: substrate query failed (Redis unreachable?): {e}")
        return SUBSTRATE_ABSENT

    return SUBSTRATE_PRESENT


def _degraded(stage: str, reason: str) -> dict:
    """Build a visible degraded-mode marker payload (D7)."""
    return {"status": "degraded", "stage": stage, "reason": reason}


def write_marker(
    stage: str,
    status: str,
    session_id: str | None = None,
    issue_number: int | None = None,
    run_id: str | None = None,
) -> tuple[dict, int]:
    """Write a stage marker to the PipelineStateMachine.

    When ``issue_number`` is passed, the resolved session must own that issue
    (via ``session_owns_issue``). If it does not, the write is refused with
    exit_code 1 and a stderr diagnostic — preventing a silent artifact divert
    to the wrong session.

    Run identity (issue #2003): when the session has an issue context, the
    issue lock is peek-compared against ``run_id`` — a foreign live holder
    refuses the write (``ISSUE_LOCKED``, exit 1). The lock renewal side
    effect uses the same run_id (falling back to ``session.active_run_id``
    inside ``renew_issue_lock_for_session``).

    Args:
        stage: Pipeline stage name (e.g., "DOCS", "REVIEW").
        status: "in_progress" or "completed".
        session_id: Optional explicit session ID (falls back to env vars).
        issue_number: Optional issue number for local session resolution.
        run_id: The caller's run identity (the CLI's ``--run-id``).

    Returns:
        A ``(result, exit_code)`` tuple (D7 tri-state contract):
        - success / degraded / idempotent no-op → exit_code 0
        - genuine write failure (substrate present, session resolved) →
          exit_code 1 (the only loud case)
        - ownership guard refusal (issue divert or foreign run) → exit_code 1
          (write refused, stderr emitted)
    """
    if stage not in _VALID_STAGES:
        logger.debug(f"sdlc_stage_marker: invalid stage {stage!r}")
        return {}, 0

    if status not in _VALID_STATUSES:
        logger.debug(f"sdlc_stage_marker: invalid status {status!r}")
        return {}, 0

    # Tri-state probe. ABSENT → degraded marker, exit 0 (the non-`ai`-repo case).
    if probe_substrate() == SUBSTRATE_ABSENT:
        return _degraded(stage, "state not persisted — substrate absent"), 0

    # Substrate is present. A missing session is QUIET (a session-less local
    # invocation, or a non-`ai` repo with no PM session) — degraded, exit 0.
    # Writes opt into auto-ensure so a sessionless local invocation with issue
    # context still gets a PM session to persist into (#1558). caller_run_id
    # gates that auto-ensure (#2003 cycle-3): a run_id-carrying write that
    # resolves no session must not mint one.
    session = find_session(session_id, issue_number=issue_number, ensure=True, caller_run_id=run_id)
    if not session:
        return _degraded(stage, "state not persisted — no PM session resolved"), 0

    # Ownership guard: when issue_number is passed, the resolved session must own
    # that issue or we refuse the write to prevent a silent artifact divert.
    if issue_number is not None and not session_owns_issue(session, issue_number):
        print(
            f"[ERROR] Recorder ownership guard: resolved session does not own"
            f" issue #{issue_number}; write refused to prevent artifact divert.",
            file=sys.stderr,
        )
        return {"error": "ownership_divert"}, 1

    # Run-identity gate (issue #2003): refuse the write when a FOREIGN run
    # holds the issue lock. Peek-only check; never mints or renews.
    conflict = check_run_ownership(session, run_id, issue_number=issue_number)
    if conflict is not None:
        print(
            f"[ERROR] ISSUE_LOCKED: issue lock held by a foreign run "
            f"(run_id={conflict.get('owner_run_id')}, "
            f"session={conflict.get('owner_session_id')}); marker write refused.",
            file=sys.stderr,
        )
        return {"error": "issue_locked", **conflict}, 1

    # Issue-lock renewal (issues #1954/#2003): a stage-marker write is
    # evidence of an in-progress BUILD/TEST/REVIEW-stage recurrence, so touch
    # the per-issue SDLC ownership lock to keep it alive -- keyed by the
    # caller's run_id (falling back to session.active_run_id). Best-effort
    # side effect -- runs regardless of whether the state-machine write below
    # succeeds or fails.
    renew_issue_lock_for_session(session, run_id=run_id)

    # PRESENT_WRITE_FAILED is the ONLY loud case: the session resolved but the
    # state-machine write rejects or raises.
    try:
        from agent.pipeline_state import PipelineStateMachine

        sm = PipelineStateMachine(session)

        if status == "in_progress":
            try:
                sm.start_stage(stage, backfill_predecessors=True)
            except ValueError as e:
                # Predecessor not completed — inconsistent pipeline state, not a
                # substrate failure. Loud so the operator notices the misorder.
                logger.debug(f"sdlc_stage_marker: start_stage({stage}) rejected: {e}")
                return {}, 1
        elif status == "completed":
            # Ensure stage is in_progress before completing
            current = sm.states.get(stage, "pending")
            if current == "completed":
                # Already completed — idempotent no-op (exit 0)
                return {"stage": stage, "status": status}, 0
            if current not in ("in_progress", "ready"):
                # Reaching this stage implies the ISSUE-rooted spine of
                # predecessors was reached too — backfill them directly
                # (NOT via start_stage, whose `current == "in_progress"`
                # no-op would otherwise skip backfill once we pre-set the
                # target stage) before forcing the target to in_progress so
                # complete_stage() accepts it.
                sm._backfill_predecessors(stage)
                sm.states[stage] = "in_progress"
            sm.complete_stage(stage)

        return {"stage": stage, "status": status}, 0

    except Exception as e:
        logger.debug(f"sdlc_stage_marker: write_marker failed: {e}")
        return {}, 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write SDLC stage markers to PipelineStateMachine",
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
        choices=["in_progress", "completed"],
        help="Status to write",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="PM session ID (falls back to VALOR_SESSION_ID / AGENT_SESSION_ID env vars)",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        default=None,
        help="GitHub issue number (for local sessions without VALOR_SESSION_ID)",
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

    # Run-identity gate (issue #2003): a state-mutating call without --run-id
    # exits non-zero with a NAMED error -- no mint, no adopt.
    if not args.run_id:
        print(
            "sdlc_stage_marker: RUN_ID_REQUIRED — state-mutating calls must pass "
            "--run-id (emitted by `sdlc-tool session-ensure`).",
            file=sys.stderr,
        )
        print(json.dumps({"error": "RUN_ID_REQUIRED"}))
        sys.exit(2)

    stage = args.stage.upper()
    result, exit_code = write_marker(
        stage=stage,
        status=args.status,
        session_id=args.session_id,
        issue_number=args.issue_number,
        run_id=args.run_id,
    )
    # Strip internal sentinel keys before printing to stdout so JSON-parsing
    # callers always receive a clean dict (no "error" sentinel leaks out).
    stdout_result = {k: v for k, v in result.items() if k != "error"}
    print(json.dumps(stdout_result))

    if exit_code != 0:
        if result.get("error") in ("ownership_divert", "issue_locked"):
            # Ownership/run-identity guards already printed their diagnostic
            # in write_marker; do not emit a second, contradictory
            # "state-machine write rejected" message — no write was attempted
            # on these paths.
            pass
        else:
            # PRESENT_WRITE_FAILED — the only loud case. A clear stderr diagnostic
            # so a forked sub-skill / operator sees the genuine writeback failure
            # instead of a silent no-op (mirrors sdlc_dispatch's loud-failure path).
            print(
                f"sdlc_stage_marker: FAILED to write {stage}={args.status} "
                "(substrate present, session resolved, but the state-machine write "
                "was rejected or raised). State NOT persisted.",
                file=sys.stderr,
            )
    elif result.get("status") == "degraded":
        # Visible degraded-mode marker (quiet on stderr, but the stdout JSON
        # carries status: degraded so the PM/operator can see it).
        logger.debug(f"sdlc_stage_marker: degraded — {result.get('reason')}")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
