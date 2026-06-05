"""Production SDLC router dispatch algorithm.

This module is the **canonical dispatch source of truth** for the SDLC pipeline.
The PM session (via ``.claude/skills/sdlc/SKILL.md``) calls the CLI wrapper
``sdlc-tool next-skill`` (implemented in ``tools/sdlc_next_skill.py``) which
delegates to ``decide_next_dispatch()`` in this module.

Two sources of truth in the pipeline:
- **Dispatch decisions**: ``agent/sdlc_router.py`` — ``DISPATCH_RULES`` + guards
  G1–G6. This module. The PM calls ``sdlc-tool next-skill`` to get the decision.
- **State-machine bookkeeping**: ``agent/pipeline_graph.py`` — ``PIPELINE_EDGES``,
  ``get_next_stage()``. Used by ``PipelineStateMachine`` to mark the next stage
  'ready' when one completes. Never consulted for dispatch decisions.

The algorithm:

    decide_next_dispatch(stage_states, meta, context)
        -> Dispatch | Blocked

    1. Evaluate guards (G1–G6). If any guard trips, return its decision.
    2. Otherwise, walk the ``DISPATCH_RULES`` list in row order and return
       the first rule whose ``state_predicate`` accepts ``(stage_states, meta,
       context)``.
    3. If no rule matches, return ``Blocked(reason="no matching rule")``.

The ``DISPATCH_RULES`` ordering mirrors the documented row numbers
(1, 2, 3, 4a, 4b, 4c, 5, 6, 7, 8, 8b, 9, 10, 10b). Each rule carries a
``row_id`` string for traceability in parity tests.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent.pipeline_graph import MAX_CRITIQUE_CYCLES

logger = logging.getLogger(__name__)

# Default maximum same-skill dispatches allowed before G4 trips. A value of 3
# means the router may dispatch the same sub-skill up to three times in a row
# without the pipeline state changing; the fourth would trip G4.
MAX_SAME_STAGE_DISPATCHES = 3

# Maximum number of router turns that G7 will wait for a /do-plan dispatch
# after the plan_revising lock is set. After this many turns with no /do-plan
# in the recent dispatch history, G7 escalates to Blocked so a human can
# intervene. Value of 2 allows one self-healing turn before escalating.
MAX_PLAN_REVISING_DISPATCHES = 2

# Maximum number of entries retained in ``_sdlc_dispatches``. Older entries are
# FIFO-evicted. Picked to be comfortably larger than ``MAX_SAME_STAGE_DISPATCHES``
# so G4 has enough history to detect sustained oscillation while still bounding
# memory growth on long-running sessions.
MAX_DISPATCH_HISTORY = 10

# Phase 1 (multi-dev fan-out): maximum number of concurrent Dev sub-sessions
# the PM may spawn for a single issue via ``sdlc-decompose``. Exceeding this cap
# causes ``sdlc-decompose`` to exit non-zero (fail closed; multi-wave queueing
# is explicitly out of scope -- see docs/plans/sdlc-1393.md "Rabbit Holes").
MAX_PARALLEL_DEVS = 3

# Phase 2 (DAG stage dispatch): set of stage-name pairs that may dispatch
# concurrently when BOTH are simultaneously ``ready`` AND both match a row in
# ``DISPATCH_RULES``. This is intentionally separate from ``PIPELINE_EDGES``
# (which is a state-machine transition table keyed by ``(stage, outcome)``);
# parallel-safety is a dispatch-time decision, not a graph topology fact.
#
# Seeded with {DOCS, PATCH} -- after REVIEW completes with findings, DOCS
# (write/refresh user-facing docs) and PATCH (fix review nits) have no data
# dependency on each other and can proceed in parallel. The PM session
# orchestrates the two skills via the existing ``pthread`` skill.
PARALLEL_SAFE_PAIRS: set[frozenset[str]] = {frozenset({"DOCS", "PATCH"})}

# Mapping from skill command to the stage that skill advances. Used by the
# router to identify the stage backing a chosen ``Dispatch`` so we can look it
# up in ``PARALLEL_SAFE_PAIRS``. Kept local to ``sdlc_router`` to avoid an
# import cycle with ``pipeline_graph``.
_SKILL_TO_STAGE: dict[str, str] = {
    "/do-issue": "ISSUE",
    "/do-plan": "PLAN",
    "/do-plan-critique": "CRITIQUE",
    "/do-build": "BUILD",
    "/do-test": "TEST",
    "/do-patch": "PATCH",
    "/do-pr-review": "REVIEW",
    "/do-docs": "DOCS",
    "/do-merge": "MERGE",
}

# Stages whose statuses are considered "complete" markers. A stage with any
# other value is still pending from the router's perspective.
STATUS_COMPLETED = "completed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_FAILED = "failed"

# Verdict strings the critique skill emits. Tested against "in" matches
# (case-insensitive) to accept future wording variants while remaining strict
# on the canonical tokens.
CRITIQUE_READY_TO_BUILD = "READY TO BUILD"
CRITIQUE_NEEDS_REVISION = "NEEDS REVISION"
CRITIQUE_MAJOR_REWORK = "MAJOR REWORK"

# Skill command strings. Keep in sync with ``agent/pipeline_graph.STAGE_TO_SKILL``
# and the ``DISPATCH_RULES`` list below. The SKILL.md hand-authored dispatch table
# no longer exists -- routing is now done via ``decide_next_dispatch()``.
SKILL_DO_ISSUE = "/do-issue"
SKILL_DO_PLAN = "/do-plan"
SKILL_DO_PLAN_CRITIQUE = "/do-plan-critique"
SKILL_DO_BUILD = "/do-build"
SKILL_DO_TEST = "/do-test"
SKILL_DO_PATCH = "/do-patch"
SKILL_DO_PR_REVIEW = "/do-pr-review"
SKILL_DO_DOCS = "/do-docs"
SKILL_DO_MERGE = "/do-merge"


# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Dispatch:
    """A routed dispatch decision."""

    skill: str
    reason: str
    row_id: str | None = None


@dataclass(frozen=True)
class Blocked:
    """An escalation decision. The router should stop and surface to the human."""

    reason: str
    guard_id: str | None = None


@dataclass(frozen=True)
class MultiDispatch:
    """Two or more Dispatch decisions that may run concurrently.

    Returned by :func:`decide_next_dispatch` when (a) the first matching
    ``DISPATCH_RULES`` row produces a Dispatch, AND (b) another ``ready``
    stage forms a :data:`PARALLEL_SAFE_PAIRS` pair with the first dispatch's
    stage AND that other stage also matches a dispatch row.

    Callers (notably ``tools/sdlc_next_skill.py`` and the SDLC skill) are
    expected to honour all guards on the *first* dispatch -- if a guard
    redirects or blocks, the entire ``MultiDispatch`` is replaced by the
    guard's decision. Guards are evaluated BEFORE the parallel-pair scan, so
    a single guard fire short-circuits the multi-dispatch path.
    """

    dispatches: list[Dispatch]
    reason: str


# Type alias for the predicate functions in DISPATCH_RULES. Each takes the
# stage_states dict, the _meta dict, and an optional context dict, and returns
# True if the rule applies.
StatePredicate = Callable[[dict, dict, dict], bool]


@dataclass
class DispatchRule:
    """One row of the dispatch table."""

    row_id: str
    state_predicate: StatePredicate
    skill: str
    reason: str


# ---------------------------------------------------------------------------
# Stage-snapshot canonicalization (used by G4 counter + dispatch-history write)
# ---------------------------------------------------------------------------


# Keys included in the stage_snapshot projection used for G4 equality checks.
# Timestamps, CI churn, and the dispatch history itself are EXCLUDED so that
# benign wall-clock drift between turns does not reset the counter.
_SNAPSHOT_PROJECTION_KEYS = frozenset(
    [
        "stages",
        "_verdicts",
        "_patch_cycle_count",
        "_critique_cycle_count",
        "pr_number",
    ]
)


def build_stage_snapshot(stage_states: dict, meta: dict) -> dict:
    """Build the narrow snapshot used to compare two router invocations.

    The snapshot deliberately excludes timestamps (recorded_at, dispatched_at,
    ISO8601 strings), CI check counters, and the ``_sdlc_dispatches`` list
    itself. Those are all fields that drift between runs for reasons
    unrelated to the router's decision, and including them would cause G4 to
    never fire.
    """
    # Reconstruct verdicts without timestamps so comparison is stable.
    stripped_verdicts: dict[str, Any] = {}
    verdicts = stage_states.get("_verdicts") or {}
    for stage, record in verdicts.items():
        if isinstance(record, dict):
            stripped = {k: v for k, v in record.items() if k not in ("recorded_at",)}
            stripped_verdicts[stage] = stripped
        else:
            stripped_verdicts[stage] = record

    return {
        "stages": {k: v for k, v in stage_states.items() if not k.startswith("_")},
        "_verdicts": stripped_verdicts,
        "_patch_cycle_count": stage_states.get("_patch_cycle_count", 0),
        "_critique_cycle_count": stage_states.get("_critique_cycle_count", 0),
        "pr_number": meta.get("pr_number"),
    }


def canonical_snapshot(snapshot: dict) -> str:
    """Canonicalize a snapshot dict for equality comparison.

    Uses ``json.dumps(snapshot, sort_keys=True, separators=(",", ":"))`` so
    that two equal snapshots always produce identical strings regardless of
    dict insertion order or JSON roundtrip. This matters because snapshots
    loaded from Redis go through a json.loads which may reorder keys.
    """
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Guards (G1–G5)
# ---------------------------------------------------------------------------


def _verdict_text(verdict: Any) -> str:
    """Extract the verdict string from a ``_verdicts`` record.

    ``_verdicts[stage]`` is typically ``{"verdict": "...", "recorded_at": ...,
    "artifact_hash": ...}`` but legacy records may store a bare string. Both
    are tolerated.
    """
    if isinstance(verdict, dict):
        text = verdict.get("verdict", "")
        if isinstance(text, str):
            return text
    elif isinstance(verdict, str):
        return verdict
    return ""


def _latest_critique_verdict(stage_states: dict, meta: dict) -> str:
    """Return the most recent critique verdict text, or ``""``.

    Prefers ``meta["latest_critique_verdict"]`` when populated by
    ``sdlc_stage_query``; falls back to reading ``_verdicts["CRITIQUE"]``.
    """
    if meta.get("latest_critique_verdict"):
        return meta["latest_critique_verdict"]
    verdicts = stage_states.get("_verdicts") or {}
    return _verdict_text(verdicts.get("CRITIQUE"))


def guard_g1_critique_loop(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G1: dispatch loop on a NEEDS REVISION / MAJOR REWORK critique.

    If the latest critique verdict is ``NEEDS REVISION`` or ``MAJOR REWORK``
    AND the last dispatched skill was ``/do-plan-critique``, the router MUST
    route to ``/do-plan`` instead of re-critiquing the unchanged plan.
    """
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_NEEDS_REVISION not in verdict and CRITIQUE_MAJOR_REWORK not in verdict:
        return None

    last = meta.get("last_dispatched_skill") or ""
    if last != SKILL_DO_PLAN_CRITIQUE:
        return None

    return Dispatch(
        skill=SKILL_DO_PLAN,
        reason=(
            f"G1: critique verdict is '{verdict}' and last dispatch was "
            f"/do-plan-critique — revise the plan before re-critiquing"
        ),
        row_id="G1",
    )


def guard_g2_critique_cycle_cap(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G2: escalate when the critique cycle ceiling is reached.

    If ``critique_cycle_count >= MAX_CRITIQUE_CYCLES`` and CRITIQUE is still
    failing (not completed), the router blocks and surfaces to the human.
    """
    cycles = meta.get("critique_cycle_count", 0)
    if cycles < MAX_CRITIQUE_CYCLES:
        return None

    critique_status = stage_states.get("CRITIQUE")
    if critique_status == STATUS_COMPLETED:
        return None

    return Blocked(
        reason=(
            f"G2: critique cycle cap reached "
            f"({cycles}/{MAX_CRITIQUE_CYCLES}) with CRITIQUE={critique_status!r}. "
            f"Escalating to human."
        ),
        guard_id="G2",
    )


def _stages_completed(stage_states: dict, stages: list[str]) -> bool:
    return all(stage_states.get(s) == STATUS_COMPLETED for s in stages)


def guard_g3_pr_lock(stage_states: dict, meta: dict, context: dict) -> Dispatch | Blocked | None:
    """G3: once a PR exists, /do-plan and /do-plan-critique are not legal.

    If an open PR exists for this issue AND the most recent dispatch was
    ``/do-plan`` or ``/do-plan-critique`` (or the LLM is asking the router
    about a plan-stage dispatch), redirect to the PR-stage skill appropriate
    for the current state: ``/do-merge`` if review is APPROVED and docs are
    done; ``/do-patch`` if review requested changes; otherwise ``/do-pr-review``.
    """
    pr_number = meta.get("pr_number")
    if not pr_number:
        return None

    last = meta.get("last_dispatched_skill") or ""
    proposed = (context or {}).get("proposed_skill", "")

    # Only trip G3 if the proposed or prior action is in the plan-stage family.
    plan_family = {SKILL_DO_PLAN, SKILL_DO_PLAN_CRITIQUE}
    if last not in plan_family and proposed not in plan_family:
        return None

    # Determine the right redirection target.
    review_status = stage_states.get("REVIEW")
    docs_status = stage_states.get("DOCS")
    review_verdict = ""
    if meta.get("latest_review_verdict"):
        review_verdict = meta["latest_review_verdict"]
    else:
        verdicts = stage_states.get("_verdicts") or {}
        review_verdict = _verdict_text(verdicts.get("REVIEW"))
    review_verdict_upper = review_verdict.upper()

    if review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED:
        target = SKILL_DO_MERGE
        suffix = "review clean and docs complete"
    elif "CHANGES REQUESTED" in review_verdict_upper or review_status == STATUS_FAILED:
        target = SKILL_DO_PATCH
        suffix = "review requested changes"
    else:
        target = SKILL_DO_PR_REVIEW
        suffix = "PR exists — run review"

    return Dispatch(
        skill=target,
        reason=f"G3: open PR #{pr_number} locks plan-stage dispatch; {suffix}",
        row_id="G3",
    )


def guard_g4_oscillation(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G4 (universal): escalate when the same skill is dispatched N+ times.

    Applies to EVERY stage — including DOCS and MERGE. If the router has
    dispatched the same sub-skill ``MAX_SAME_STAGE_DISPATCHES`` times in a
    row without the stage_snapshot changing, something is stuck and a human
    needs to intervene.

    D5 — self-clearing + escape hatch: ``same_stage_dispatch_count`` resets
    to 0 when the live stage snapshot diverges from the last recorded
    dispatch (see ``compute_same_stage_count``), so G4 stops firing after a
    real stage/verdict correction. For the genuinely-latched recorded-history
    case, the operator clears the streak explicitly with
    ``sdlc-tool dispatch reset --issue-number N``.
    """
    count = meta.get("same_stage_dispatch_count", 0)
    if count < MAX_SAME_STAGE_DISPATCHES:
        return None

    skill = meta.get("last_dispatched_skill") or "<unknown>"
    return Blocked(
        reason=(
            f"G4: stage oscillation — {skill} dispatched {count} times without state change. "
            "If state has since moved, this self-clears; otherwise clear it with "
            "`sdlc-tool dispatch reset --issue-number N`."
        ),
        guard_id="G4",
    )


def guard_g5_artifact_hash_cache(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G5 (CRITIQUE only): reuse a prior verdict when the plan hash is unchanged.

    If ``_verdicts["CRITIQUE"]`` exists with an ``artifact_hash`` AND the
    current plan-file hash (provided by the caller via
    ``context["current_plan_hash"]``) matches, re-dispatching
    ``/do-plan-critique`` is prohibited. The router returns the cached verdict's
    downstream dispatch decision.

    G5 does NOT apply to REVIEW — a diff hash can match while CI status and
    human comments legitimately change. G4 covers REVIEW non-determinism.
    """
    verdicts = stage_states.get("_verdicts") or {}
    record = verdicts.get("CRITIQUE")
    if not isinstance(record, dict):
        return None

    cached_hash = record.get("artifact_hash")
    current_hash = (context or {}).get("current_plan_hash")
    if not cached_hash or not current_hash:
        return None
    if cached_hash != current_hash:
        return None

    verdict_text = _verdict_text(record).upper()
    if CRITIQUE_NEEDS_REVISION in verdict_text or CRITIQUE_MAJOR_REWORK in verdict_text:
        return Dispatch(
            skill=SKILL_DO_PLAN,
            reason="G5: cached CRITIQUE verdict is NEEDS REVISION on unchanged plan hash",
            row_id="G5",
        )

    if CRITIQUE_READY_TO_BUILD in verdict_text:
        # Plan-hash unchanged AND cached verdict says READY TO BUILD — route
        # straight to build.
        return Dispatch(
            skill=SKILL_DO_BUILD,
            reason="G5: cached CRITIQUE verdict is READY TO BUILD on unchanged plan hash",
            row_id="G5",
        )

    return None


def guard_g7_plan_revising(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G7: block /do-build while the plan-revising lock is set.

    The lock (``_meta["plan_revising"]``) is set by ``/do-plan-critique`` when
    its verdict is NEEDS REVISION, MAJOR REWORK, or READY TO BUILD (with
    concerns) and the revision pass has not yet run. It is cleared by
    ``/do-plan`` in the same step that writes ``revision_applied: true``.

    **Predicate (evaluated in order):**
    1. If ``pr_number`` is set → return None (G3/G6 own PR-stage routing; an
       already-shipped PR is never blocked by plan revisions).
    2. If ``plan_revising`` is falsy → return None (lock not set, fall through).
    3. Self-heal: if ``plan_revising`` is truthy AND ``revision_applied`` is also
       truthy → return None (plan was revised but the lock-clear step never ran,
       e.g. skill crashed mid-step; revision_applied is the source of truth).
    4. If lock is set AND ``last_dispatched_skill`` is ``/do-plan-critique``
       (critique just finished) → return Dispatch(/do-plan): the obvious next
       step is to apply the revision.
    5. If lock is set AND no ``/do-plan`` appears in the last
       ``MAX_PLAN_REVISING_DISPATCHES + 1`` dispatch history entries → return
       Blocked: the lock has been set for multiple turns with no plan dispatch,
       indicating the pipeline is stuck and a human should intervene.
    6. Otherwise → return None: a plan dispatch is already in the recent history;
       allow the dispatch table to route normally (the plan may still be in
       flight).

    **Ordering:** G7 is evaluated AFTER G6 (terminal merge fast-path) so that
    an already-mergeable PR is never blocked by a stale plan_revising flag.
    G7 is gated on ``pr_number is None`` for the same reason.

    **Deadlock backstop:** If the lock leaks (critique crashes after setting it
    but before /do-plan runs), G7 escalates to Blocked after
    ``MAX_PLAN_REVISING_DISPATCHES`` turns. The operator can clear the lock
    manually via ``sdlc-tool meta-set --key plan_revising --value false``.
    """
    # Gate 1: PR exists — G3/G6 own routing at this stage.
    if meta.get("pr_number"):
        return None

    # Gate 2: Lock not set — nothing to do.
    if not meta.get("plan_revising"):
        return None

    # Gate 3: Self-heal — revision_applied supersedes the lock.
    if meta.get("revision_applied"):
        return None

    # The lock is set and the plan has not been marked as revised.
    last_skill = meta.get("last_dispatched_skill") or ""
    history = stage_states.get("_sdlc_dispatches") or []
    if not isinstance(history, list):
        history = []

    # Gate 4: Critique just finished — route to plan revision.
    if last_skill == SKILL_DO_PLAN_CRITIQUE:
        return Dispatch(
            skill=SKILL_DO_PLAN,
            reason=(
                "G7: plan_revising lock is set and critique just ran — "
                "apply the revision pass before building"
            ),
            row_id="G7",
        )

    # Gate 5: Check recent dispatch history for a /do-plan entry.
    # Look back MAX_PLAN_REVISING_DISPATCHES + 1 entries so one self-healing
    # turn is allowed before escalating.
    recent = history[-(MAX_PLAN_REVISING_DISPATCHES + 1) :]
    recent_skills = [e.get("skill") for e in recent if isinstance(e, dict)]
    if SKILL_DO_PLAN not in recent_skills:
        return Blocked(
            reason=(
                f"G7: plan_revising lock set but no /do-plan dispatched in the "
                f"last {MAX_PLAN_REVISING_DISPATCHES + 1} turns. "
                f"Pipeline may be stuck. Clear the lock manually via "
                f"'sdlc-tool meta-set --key plan_revising --value false' if the "
                f"revision is already complete."
            ),
            guard_id="G7",
        )

    # A plan dispatch is already in the recent history — let dispatch table route.
    return None


def guard_g6_terminal_merge_ready(stage_states: dict, meta: dict, context: dict) -> Dispatch | None:
    """G6: PR is mergeable, CI green, DOCS done, review APPROVED — fast-path to /do-merge.

    Fires when all five conditions are met:
    - ``pr_number`` is set (a PR exists for this issue)
    - ``pr_merge_state == "CLEAN"`` (GitHub mergeStateStatus)
    - ``ci_all_passing is True`` (all statusCheckRollup conclusions are SUCCESS)
    - ``stage_states["DOCS"] == "completed"`` (docs gate has passed)
    - ``_verdicts["REVIEW"]`` contains "APPROVED"

    G6 is evaluated LAST (after G1-G5). Escalation guards (G2, G4) take priority
    over the merge fast-path — a stuck pipeline should escalate before merging.
    G3 (PR lock) redirects plan-stage dispatches but does not prevent G6.

    Returns ``None`` if any condition is not met, allowing the normal dispatch
    table to handle routing (e.g., to Row 9 ``/do-docs`` if docs aren't done).
    """
    pr_number = meta.get("pr_number")
    if not pr_number:
        return None
    if meta.get("pr_merge_state") != "CLEAN":
        return None
    if meta.get("ci_all_passing") is not True:
        return None
    # DOCS must be completed before dispatching merge — G6 must not bypass the docs gate.
    if stage_states.get("DOCS") != STATUS_COMPLETED:
        return None
    review_verdict = ""
    if meta.get("latest_review_verdict"):
        review_verdict = meta["latest_review_verdict"]
    else:
        verdicts = stage_states.get("_verdicts") or {}
        review_verdict = _verdict_text(verdicts.get("REVIEW"))
    if "APPROVED" not in review_verdict.upper():
        return None
    return Dispatch(
        skill=SKILL_DO_MERGE,
        reason="G6: PR is mergeable, CI green, DOCS done, review APPROVED — fast-path to merge",
        row_id="G6",
    )


GUARDS: list[Callable[[dict, dict, dict], Dispatch | Blocked | None]] = [
    guard_g1_critique_loop,
    guard_g2_critique_cycle_cap,
    guard_g3_pr_lock,
    guard_g4_oscillation,
    guard_g5_artifact_hash_cache,
    guard_g6_terminal_merge_ready,
    guard_g7_plan_revising,
]


def evaluate_guards(
    stage_states: dict, meta: dict, context: dict | None = None
) -> Dispatch | Blocked | None:
    """Walk the guard list, return the first tripped decision, or ``None``."""
    ctx = context or {}
    for guard in GUARDS:
        result = guard(stage_states, meta, ctx)
        if result is not None:
            return result
    return None


# ---------------------------------------------------------------------------
# Dispatch table (Rows 1–10b from SKILL.md)
# ---------------------------------------------------------------------------


def _rule_no_plan(stage_states: dict, meta: dict, context: dict) -> bool:
    """No plan exists."""
    # If an open PR exists, a plan must exist too — defer to PR-stage rows.
    if meta.get("pr_number"):
        return False
    plan_status = stage_states.get("PLAN")
    # "No plan exists" is the absence of a plan file OR a pending PLAN stage.
    return plan_status in (None, "pending")


def _rule_plan_not_critiqued(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan exists, not yet critiqued."""
    plan_status = stage_states.get("PLAN")
    critique_status = stage_states.get("CRITIQUE")
    return plan_status in (STATUS_COMPLETED, "ready") and critique_status in (
        None,
        "pending",
        "ready",
    )


def _rule_critique_needs_revision(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan critiqued (NEEDS REVISION)."""
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    return CRITIQUE_NEEDS_REVISION in verdict


def _rule_critique_ready_no_concerns(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan critiqued (READY TO BUILD, zero concerns), no branch/PR."""
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_READY_TO_BUILD not in verdict:
        return False
    if "WITH CONCERNS" in verdict:
        return False
    if meta.get("pr_number"):
        return False
    build_status = stage_states.get("BUILD")
    return build_status in (None, "pending", "ready")


def _rule_critique_ready_with_concerns_no_revision(
    stage_states: dict, meta: dict, context: dict
) -> bool:
    """Plan critiqued (READY TO BUILD, concerns), revision_applied not set.

    D3: defer to downstream PR-stage rows once a PR exists or BUILD has
    completed — a finished PR must never route back to plan/build.
    """
    if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED:
        return False
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_READY_TO_BUILD not in verdict or "WITH CONCERNS" not in verdict:
        return False
    if bool(meta.get("revision_applied")):
        return False
    # Once build has produced a PR (or BUILD is already done), this row must
    # release so routing can advance to review/merge. Without these guards the
    # row re-dispatches /do-plan forever for a with-concerns plan whose
    # revision flag never got set. Mirror the guards on rows 4a/4c.
    if meta.get("pr_number"):
        return False
    build_status = stage_states.get("BUILD")
    return build_status in (None, "pending", "ready")


def _rule_critique_ready_with_concerns_revision_applied(
    stage_states: dict, meta: dict, context: dict
) -> bool:
    """Plan critiqued (READY TO BUILD, concerns), revision_applied true.

    D3: defer to downstream PR-stage rows once a PR exists or BUILD has
    completed so row-4c stops re-proposing /do-build on a finished PR.
    """
    if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED:
        return False
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_READY_TO_BUILD not in verdict or "WITH CONCERNS" not in verdict:
        return False
    if not bool(meta.get("revision_applied")):
        return False
    # Once build has produced a PR (or BUILD is already done), this row must
    # release so routing can advance to review. Without these guards the row
    # re-dispatches /do-build forever for every with-concerns plan. Mirror the
    # guards on row 4a (_rule_critique_ready_no_concerns).
    if meta.get("pr_number"):
        return False
    build_status = stage_states.get("BUILD")
    return build_status in (None, "pending", "ready")


def _rule_branch_exists_no_pr(stage_states: dict, meta: dict, context: dict) -> bool:
    """Branch exists, no PR."""
    if meta.get("pr_number"):
        return False
    build_status = stage_states.get("BUILD")
    return build_status == STATUS_IN_PROGRESS or (context or {}).get("branch_exists") is True


def _rule_tests_failing(stage_states: dict, meta: dict, context: dict) -> bool:
    """Tests failing."""
    return stage_states.get("TEST") == STATUS_FAILED


def _rule_pr_exists_no_review(stage_states: dict, meta: dict, context: dict) -> bool:
    """PR exists, no review."""
    if not meta.get("pr_number"):
        return False
    review_status = stage_states.get("REVIEW")
    review_verdict = ""
    if meta.get("latest_review_verdict"):
        review_verdict = meta["latest_review_verdict"]
    else:
        verdicts = stage_states.get("_verdicts") or {}
        review_verdict = _verdict_text(verdicts.get("REVIEW"))
    return review_status in (None, "pending", "ready") and not review_verdict


def _rule_review_has_findings(stage_states: dict, meta: dict, context: dict) -> bool:
    """PR review has findings (blockers, nits, or tech debt)."""
    if not meta.get("pr_number"):
        return False
    review_verdict = ""
    if meta.get("latest_review_verdict"):
        review_verdict = meta["latest_review_verdict"]
    else:
        verdicts = stage_states.get("_verdicts") or {}
        review_verdict = _verdict_text(verdicts.get("REVIEW"))
    review_verdict_upper = review_verdict.upper()
    if not review_verdict:
        return False
    if "CHANGES REQUESTED" in review_verdict_upper:
        return True
    if "PARTIAL" in review_verdict_upper:
        return True
    # REVIEW failed status implies blockers
    if stage_states.get("REVIEW") == STATUS_FAILED:
        return True
    return False


def _rule_patch_applied_after_review(stage_states: dict, meta: dict, context: dict) -> bool:
    """Patch applied after review findings — re-review is required."""
    if not meta.get("pr_number"):
        return False
    # PATCH completed after REVIEW failed — need to re-review.
    if stage_states.get("PATCH") != STATUS_COMPLETED:
        return False
    last = meta.get("last_dispatched_skill") or ""
    return last == SKILL_DO_PATCH


def _rule_review_approved_docs_not_done(stage_states: dict, meta: dict, context: dict) -> bool:
    """Review APPROVED, zero findings, docs NOT done."""
    if not meta.get("pr_number"):
        return False
    if stage_states.get("REVIEW") != STATUS_COMPLETED:
        return False
    docs_status = stage_states.get("DOCS")
    return docs_status not in (STATUS_COMPLETED,)


def _rule_ready_to_merge(stage_states: dict, meta: dict, context: dict) -> bool:
    """Review APPROVED, zero findings, docs done, ready to merge."""
    if not meta.get("pr_number"):
        return False
    needed = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS"]
    return _stages_completed(stage_states, needed)


def _rule_stage_states_unavailable_pr_open(stage_states: dict, meta: dict, context: dict) -> bool:
    """stage_states unavailable AND an open PR exists for this issue."""
    if meta.get("pr_number") and not stage_states:
        return True
    return False


# Attach human-readable state strings as docstrings — the parity test uses
# these to cross-check SKILL.md row state cells. Keep in sync with SKILL.md.
_rule_no_plan.__doc__ = "No plan exists"
_rule_plan_not_critiqued.__doc__ = "Plan exists, not yet critiqued"
_rule_critique_needs_revision.__doc__ = "Plan critiqued (NEEDS REVISION)"
_rule_critique_ready_no_concerns.__doc__ = (
    "Plan critiqued (READY TO BUILD, zero concerns), no branch/PR"
)
_rule_critique_ready_with_concerns_no_revision.__doc__ = (
    "Plan critiqued (READY TO BUILD, concerns present), "
    "revision_applied not set in plan frontmatter"
)
_rule_critique_ready_with_concerns_revision_applied.__doc__ = (
    "Plan critiqued (READY TO BUILD, concerns present), revision_applied: true in plan frontmatter"
)
_rule_branch_exists_no_pr.__doc__ = "Branch exists, no PR"
_rule_tests_failing.__doc__ = "Tests failing"
_rule_pr_exists_no_review.__doc__ = "PR exists, no review"
_rule_review_has_findings.__doc__ = "PR review has findings (blockers, nits, OR tech debt)"
_rule_patch_applied_after_review.__doc__ = "Patch applied after review findings"
_rule_review_approved_docs_not_done.__doc__ = (
    "Review APPROVED with zero findings, docs NOT done (see Step 3)"
)
_rule_ready_to_merge.__doc__ = (
    "Review APPROVED with zero findings, docs done, "
    "AND all display stages show completed in stage_states "
    "(or stage_states unavailable), ready to merge"
)
_rule_stage_states_unavailable_pr_open.__doc__ = (
    "stage_states unavailable AND an open PR exists for this issue"
)


DISPATCH_RULES: list[DispatchRule] = [
    DispatchRule(
        row_id="1",
        state_predicate=_rule_no_plan,
        skill=SKILL_DO_PLAN,
        reason="Cannot build without a plan",
    ),
    DispatchRule(
        row_id="2",
        state_predicate=_rule_plan_not_critiqued,
        skill=SKILL_DO_PLAN_CRITIQUE,
        reason="Plan must pass critique before build",
    ),
    DispatchRule(
        row_id="3",
        state_predicate=_rule_critique_needs_revision,
        skill=SKILL_DO_PLAN,
        reason="Revise plan based on critique findings",
    ),
    DispatchRule(
        row_id="4a",
        state_predicate=_rule_critique_ready_no_concerns,
        skill=SKILL_DO_BUILD,
        reason="No revision needed — critique passed cleanly",
    ),
    DispatchRule(
        row_id="4b",
        state_predicate=_rule_critique_ready_with_concerns_no_revision,
        skill=SKILL_DO_PLAN,
        reason="Revision pass before build — embed Implementation Notes into plan text",
    ),
    DispatchRule(
        row_id="4c",
        state_predicate=_rule_critique_ready_with_concerns_revision_applied,
        skill=SKILL_DO_BUILD,
        reason="Revision pass already complete — proceed to build",
    ),
    DispatchRule(
        row_id="5",
        state_predicate=_rule_branch_exists_no_pr,
        skill=SKILL_DO_BUILD,
        reason="Build must create the PR — resume build",
    ),
    DispatchRule(
        row_id="6",
        state_predicate=_rule_tests_failing,
        skill=SKILL_DO_PATCH,
        reason="Fix what is broken",
    ),
    DispatchRule(
        row_id="7",
        state_predicate=_rule_pr_exists_no_review,
        skill=SKILL_DO_PR_REVIEW,
        reason="Code is ready for review",
    ),
    DispatchRule(
        row_id="8",
        state_predicate=_rule_review_has_findings,
        skill=SKILL_DO_PATCH,
        reason="ALL findings must be addressed",
    ),
    DispatchRule(
        row_id="8b",
        state_predicate=_rule_patch_applied_after_review,
        skill=SKILL_DO_PR_REVIEW,
        reason="Re-review is REQUIRED after every patch",
    ),
    DispatchRule(
        row_id="9",
        state_predicate=_rule_review_approved_docs_not_done,
        skill=SKILL_DO_DOCS,
        reason="Docs are required before merge",
    ),
    DispatchRule(
        row_id="10",
        state_predicate=_rule_ready_to_merge,
        skill=SKILL_DO_MERGE,
        reason="Execute programmatic merge gate",
    ),
    DispatchRule(
        row_id="10b",
        state_predicate=_rule_stage_states_unavailable_pr_open,
        skill=SKILL_DO_MERGE,
        reason=(
            "Fallback: if stage_states cannot confirm stages but an open PR "
            "exists after DOCS, dispatch merge"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _stage_is_ready(stage_states: dict, stage: str) -> bool:
    """Return True if a stage's status is ``ready``, ``pending``, or ``failed``.

    ``ready`` is the canonical "next stage may dispatch" status used by
    ``PipelineStateMachine``. ``pending`` and ``failed`` are also treated as
    dispatchable so that the parallel-pair scan does not miss a stage that
    needs a patch (e.g., PATCH stage with status ``failed`` after REVIEW).
    """
    status = stage_states.get(stage)
    return status in ("ready", "pending", "failed")


def _find_parallel_dispatch(
    primary: Dispatch,
    stage_states: dict,
    meta: dict,
    context: dict,
) -> Dispatch | None:
    """Find a second Dispatch that may run concurrently with ``primary``.

    Walks ``PARALLEL_SAFE_PAIRS`` looking for a pair containing the primary
    dispatch's stage. For the *other* stage in the pair, scans ``DISPATCH_RULES``
    in row order for a matching rule whose chosen skill resolves to that stage.

    Returns the second Dispatch, or ``None`` if no parallel-safe peer is ready.
    """
    primary_stage = _SKILL_TO_STAGE.get(primary.skill)
    if not primary_stage:
        return None

    for pair in PARALLEL_SAFE_PAIRS:
        if primary_stage not in pair:
            continue
        # The OTHER stage in the pair.
        peer_stages = pair - {primary_stage}
        if not peer_stages:
            continue
        peer_stage = next(iter(peer_stages))

        # The peer stage must be in a dispatchable state. We don't require
        # status == "ready" exactly because PATCH frequently sits at "failed"
        # before patching, and DOCS frequently sits at "pending".
        if not _stage_is_ready(stage_states, peer_stage):
            continue

        # Find a dispatch rule that picks the peer stage's skill.
        for rule in DISPATCH_RULES:
            if rule.row_id == primary.row_id:
                continue
            rule_stage = _SKILL_TO_STAGE.get(rule.skill)
            if rule_stage != peer_stage:
                continue
            try:
                if rule.state_predicate(stage_states, meta, context):
                    return Dispatch(
                        skill=rule.skill,
                        reason=rule.reason,
                        row_id=rule.row_id,
                    )
            except Exception as e:
                logger.debug(f"Parallel DispatchRule {rule.row_id} predicate raised: {e}")
    return None


def decide_next_dispatch(
    stage_states: dict,
    meta: dict | None = None,
    context: dict | None = None,
) -> Dispatch | MultiDispatch | Blocked:
    """Decide which sub-skill(s) the SDLC router should dispatch next.

    Algorithm:
      1. Evaluate guards G1–G7. If any guard trips, return its decision.
      2. Otherwise, walk ``DISPATCH_RULES`` in row order. Take the first
         rule whose ``state_predicate`` returns True as the *primary* dispatch.
      3. Scan ``PARALLEL_SAFE_PAIRS`` for a parallel-safe peer dispatch. If a
         second eligible Dispatch exists, wrap both into a ``MultiDispatch``;
         otherwise return the primary Dispatch alone.
      4. If no rule matches at all, return ``Blocked(reason="no matching rule")``.

    Args:
        stage_states: The stage-status dict from ``AgentSession.stage_states``.
            May also include underscore-prefixed metadata keys such as
            ``_verdicts``, ``_sdlc_dispatches``, ``_patch_cycle_count``,
            ``_critique_cycle_count``.
        meta: The ``_meta`` dict produced by ``sdlc_stage_query`` (patch cycle
            count, critique cycle count, latest verdicts, pr_number,
            same_stage_dispatch_count, last_dispatched_skill, revision_applied).
            Missing keys default sensibly.
        context: Optional extra caller context — e.g.,
            ``current_plan_hash`` for G5 and ``proposed_skill`` for G3.

    Returns:
        ``Dispatch`` with a single chosen skill, ``MultiDispatch`` when two
        parallel-safe stages are both ready, or ``Blocked`` if the router
        escalates to human.
    """
    meta = meta or {}
    context = context or {}

    guard_result = evaluate_guards(stage_states, meta, context)
    if guard_result is not None:
        return guard_result

    primary: Dispatch | None = None
    for rule in DISPATCH_RULES:
        try:
            if rule.state_predicate(stage_states, meta, context):
                primary = Dispatch(
                    skill=rule.skill,
                    reason=rule.reason,
                    row_id=rule.row_id,
                )
                break
        except Exception as e:
            # Predicates should never raise; log and continue so one bad rule
            # doesn't break the whole dispatch.
            logger.debug(f"DispatchRule {rule.row_id} predicate raised: {e}")

    if primary is None:
        return Blocked(
            reason="no matching dispatch rule",
            guard_id=None,
        )

    # Phase 2: look for a parallel-safe peer dispatch.
    peer = _find_parallel_dispatch(primary, stage_states, meta, context)
    if peer is not None:
        return MultiDispatch(
            dispatches=[primary, peer],
            reason=(
                f"parallel-safe pair: {primary.skill} ({primary.row_id}) + "
                f"{peer.skill} ({peer.row_id})"
            ),
        )

    return primary


def record_dispatch(
    stage_states: dict,
    skill: str,
    now: datetime | None = None,
    pr_number: int | None = None,
) -> dict:
    """Append a dispatch record to ``stage_states._sdlc_dispatches``.

    The list is FIFO-bounded to ``MAX_DISPATCH_HISTORY`` entries. The
    ``stage_snapshot`` projection excludes timestamps and the dispatch history
    itself, so repeated calls with unchanged state produce identical
    snapshots (enabling G4 detection).

    This function mutates the supplied ``stage_states`` in place AND returns
    it for callers that prefer a functional style. It does NOT persist to
    Redis — callers must wrap this in ``update_stage_states`` from
    ``tools.stage_states_helpers`` to get safe cross-process write semantics.

    Args:
        stage_states: The dict to mutate.
        skill: The skill string being dispatched.
        now: Optional timestamp for testability. Defaults to current UTC.
        pr_number: Optional PR number from the caller's ``_meta`` dict. Passed
            into ``build_stage_snapshot`` so the snapshot's ``pr_number`` field
            reflects the live PR state. If omitted, the snapshot falls back
            to ``stage_states.get("_pr_number")`` for callers that mirror the
            PR number into ``stage_states`` directly; otherwise it is ``None``.
            Explicit pass-through is preferred because ``sdlc_stage_query``
            puts the PR number into ``_meta.pr_number`` rather than
            mirroring it into ``stage_states``.

    Returns:
        The mutated stage_states dict.
    """
    timestamp = (now or datetime.now(UTC)).isoformat()
    # Build a snapshot from a stage_states view that EXCLUDES the history
    # list itself, otherwise the counter would never match across invocations.
    view = {k: v for k, v in stage_states.items() if k != "_sdlc_dispatches"}
    # Resolve the pr_number: explicit argument wins, else fall back to any
    # mirrored value in stage_states (opt-in convention for callers that
    # prefer to keep PR context alongside stage_states).
    resolved_pr_number = pr_number if pr_number is not None else stage_states.get("_pr_number")
    snapshot = build_stage_snapshot(view, meta={"pr_number": resolved_pr_number})

    history = stage_states.setdefault("_sdlc_dispatches", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "skill": skill,
            "at": timestamp,
            "stage_snapshot": snapshot,
        }
    )
    # FIFO-evict oldest entries to bound the list.
    if len(history) > MAX_DISPATCH_HISTORY:
        history = history[-MAX_DISPATCH_HISTORY:]
    stage_states["_sdlc_dispatches"] = history
    return stage_states


def compute_same_stage_count(
    stage_states: dict, current_snapshot: dict | None = None
) -> tuple[int, str | None]:
    """Compute the same-skill-same-state streak length from dispatch history.

    Walks ``_sdlc_dispatches`` from the most recent entry backward, counting
    how many consecutive entries share BOTH the same skill AND the same
    stage_snapshot.

    Args:
        stage_states: The stage_states dict (reads ``_sdlc_dispatches``).
        current_snapshot: Optional current stage_snapshot projection to
            compare against. If provided, the count includes a +1 for the
            "about to dispatch" turn when it matches the most recent history
            entry's snapshot. If None, counts only the already-recorded
            history.

    Returns:
        Tuple of (count, skill). Skill is the skill being repeated, or
        None if the history is empty.
    """
    history = stage_states.get("_sdlc_dispatches") or []
    if not isinstance(history, list) or not history:
        return (0, None)

    last = history[-1]
    if not isinstance(last, dict):
        return (0, None)
    skill = last.get("skill")
    last_snapshot = last.get("stage_snapshot")
    if skill is None or last_snapshot is None:
        return (0, None)

    last_snapshot_canonical = canonical_snapshot(last_snapshot)

    count = 0
    for entry in reversed(history):
        if not isinstance(entry, dict):
            break
        if entry.get("skill") != skill:
            break
        entry_snapshot = entry.get("stage_snapshot")
        if entry_snapshot is None:
            break
        if canonical_snapshot(entry_snapshot) != last_snapshot_canonical:
            break
        count += 1

    if current_snapshot is not None:
        if canonical_snapshot(current_snapshot) == last_snapshot_canonical:
            # The router is ABOUT to dispatch the same skill again on the
            # same state — count this impending turn too.
            count += 1
        else:
            # D5: the live state has moved past the last recorded dispatch
            # snapshot — the impending dispatch is a genuinely new stage, not
            # a repeat. Reset the streak so G4 self-clears on a real
            # transition (e.g. a stage/verdict correction recorded since the
            # last dispatch) instead of latching closed.
            return (0, skill)

    return (count, skill)
