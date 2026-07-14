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
(1, 2, 2b, 2c, 3, 4a, 4b, 4c, 5, 6, 7, 8, 8b, 8c, 8d, 9, 10). Each rule
carries a ``row_id`` string for traceability in parity tests.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agent.pipeline_graph import MAX_CRITIQUE_CYCLES, STAGE_TO_SKILL

logger = logging.getLogger(__name__)


def normalize_verdict(text: str | None) -> str:
    """Normalize a verdict string to its canonical space-separated uppercase form.

    Canonical home (import-boundary contract): this function lives HERE, not
    in ``tools/``, because ``agent/sdlc_router.py`` is the ground-truth module
    that ``tools/sdlc_dispatch.py`` and ``tools/sdlc_verdict.py`` import — the
    router must never import from ``tools/`` in return (see
    ``tests/unit/test_architectural_constraints.py``). ``tools/_sdlc_utils``
    re-exports it for its existing importers.

    Idempotent: calling normalize_verdict on an already-normalized string
    returns the same string unchanged. The canonical form uses spaces (not
    underscores) as word separators, collapses internal whitespace, and is
    fully uppercased.

    Examples::

        normalize_verdict("CHANGES REQUESTED") -> "CHANGES REQUESTED"  # idempotent
        normalize_verdict("changes_requested")  -> "CHANGES REQUESTED"
        normalize_verdict("  Changes  Requested  ") -> "CHANGES REQUESTED"
        normalize_verdict(None) -> ""
        normalize_verdict("") -> ""

    Args:
        text: Raw verdict string from a skill or stored record. May contain
            underscores, mixed case, or extra whitespace. May be None.

    Returns:
        Canonical uppercase space-form string, or ``""`` for falsy/non-str input.
    """
    if not text or not isinstance(text, str):
        return ""
    # Replace underscores with spaces, collapse runs of whitespace, uppercase.
    return re.sub(r"\s+", " ", text.replace("_", " ")).strip().upper()


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

# Review verdict canonical strings. Matched via normalize_verdict() so
# underscore forms and mixed-case inputs still resolve correctly (#1638).
REVIEW_APPROVED = "APPROVED"
REVIEW_CHANGES_REQUESTED = "CHANGES REQUESTED"

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


def _latest_review_verdict(stage_states: dict, meta: dict) -> str:
    """Return the most recent review verdict text, or ``""``.

    Prefers ``meta["latest_review_verdict"]`` when populated by
    ``sdlc_stage_query``; falls back to reading ``_verdicts["REVIEW"]``.
    """
    if meta.get("latest_review_verdict"):
        return meta["latest_review_verdict"]
    verdicts = stage_states.get("_verdicts") or {}
    return _verdict_text(verdicts.get("REVIEW"))


def guard_g1_critique_loop(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G1: dispatch loop on a NEEDS REVISION / MAJOR REWORK critique.

    If the latest critique verdict is ``NEEDS REVISION`` or ``MAJOR REWORK``
    AND the last dispatched skill was ``/do-plan-critique``, the router MUST
    route to ``/do-plan`` instead of re-critiquing the unchanged plan.

    Open-PR step-aside (#1932 gap b2): once a PR exists, this guard defers to
    G3 (``guard_g3_pr_lock``), the canonical open-PR plan-stage redirect —
    routing straight to ``/do-plan`` here would bypass G3's PR-aware target
    selection.
    """
    if meta.get("pr_number"):
        return None

    verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta))
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
    review_verdict_norm = normalize_verdict(review_verdict)

    if review_status == STATUS_COMPLETED and docs_status == STATUS_COMPLETED:
        target = SKILL_DO_MERGE
        suffix = "review clean and docs complete"
    elif REVIEW_CHANGES_REQUESTED in review_verdict_norm or review_status == STATUS_FAILED:
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


def guard_g8_artifact_verification(
    stage_states: dict, meta: dict, context: dict
) -> Dispatch | Blocked | None:
    """G8: re-dispatch when a claimed stage artifact fails live verification (#1267).

    The router advances on stage-completion markers that the executing agent
    *self-attests* (the ``<!-- OUTCOME {...} -->`` contract). Nothing upstream
    of this guard independently confirmed the claimed load-bearing artifact —
    a PR actually opened, a branch actually pushed, a plan actually committed
    — exists in the world. The live verification itself runs in the next-skill
    **context-assembly** path (``tools/sdlc_next_skill.py``, reusing #2003's
    live-ref helpers) — deterministic, no LLM, and outside the router so the
    router stays import-free of ``tools/`` (architectural constraint,
    ``tests/unit/test_architectural_constraints.py``). This guard makes no
    live calls itself; it only consumes the context flags that path sets:
    ``context["stage_artifacts_verified"]`` / ``context["unverified_stage"]``.

    Positioning is load-bearing: G8 is inserted into ``GUARDS`` immediately
    after G4 (``guard_g4_oscillation``), NOT before it. On a persistently
    false claim, G8 would re-dispatch the same stage's skill forever with
    nothing to stop it — G4 is the loop-bound backstop, and because G4 is
    evaluated first, it fires and returns ``Blocked`` once
    ``same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES`` before G8 ever
    gets a chance to re-dispatch again. The phase-1 false-claim policy is
    therefore "silent re-dispatch, then escalate via the existing G4 cap" —
    not an immediate Block on the first mismatch.

    Fires ONLY when ``context["stage_artifacts_verified"] is False`` (an
    explicit, verified mismatch). Absent/unset/``True`` is a no-op — this
    mirrors the context-assembly contract that a stage with no claimed
    artifact (or one that verified clean) never sets the flag to ``False``.
    """
    if context.get("stage_artifacts_verified") is not False:
        return None

    unverified_stage = context.get("unverified_stage")
    skill = STAGE_TO_SKILL.get(unverified_stage) if unverified_stage else None
    if skill is None:
        # A mismatch was flagged but the stage can't be mapped to a skill —
        # malformed context. Fail open (no dispatch decision here) rather
        # than guessing at a re-dispatch target.
        return None

    return Dispatch(
        skill=skill,
        reason=(
            f"G8: {unverified_stage} claims completed but its artifact failed "
            f"live verification — re-dispatching {skill} rather than advancing"
        ),
        row_id="G8",
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

    Transparent migration (issue #1761 Layer 3):
      If the cached hash is the OLD full-bytes hash (supplied by the caller
      via ``context["legacy_plan_hash"]`` — computed by
      ``tools.sdlc_verdict.compute_plan_hash``) and the only diff is the
      ``revision_applied:`` frontmatter line, the guard transparently rewrites
      the stored ``artifact_hash`` to the new ``compute_plan_body_hash``
      value.  The rewrite is idempotent — once written, subsequent calls use
      the new hash directly.  A WARNING is emitted on every rewrite.

      The legacy hash is caller-supplied (dependency inversion) because this
      module must not import from ``tools/`` — ``tools/sdlc_dispatch.py`` and
      ``tools/sdlc_verdict.py`` import this module, so a ``tools`` import here
      would create a cycle (see tests/unit/test_architectural_constraints.py).
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
        # Transparent migration: check if the stored hash is the legacy
        # full-bytes hash and the only delta is the revision_applied: line.
        # The legacy hash is caller-supplied via context (see docstring) so
        # this module stays free of tools/ imports.
        legacy_hash = (context or {}).get("legacy_plan_hash")
        if legacy_hash and legacy_hash == cached_hash:
            # Only delta is revision_applied — rewrite in-place.
            logger.warning(
                "G5 migration: rewriting artifact_hash from legacy "
                "full-bytes to revision_applied-stripped hash for "
                "issue %s (old=%s, new=%s)",
                (context or {}).get("issue_number"),
                cached_hash,
                current_hash,
            )
            record["artifact_hash"] = current_hash
            cached_hash = current_hash
            # Fall through to normal cache-hit evaluation below.
        if cached_hash != current_hash:
            return None

    verdict_text = normalize_verdict(_verdict_text(record))
    if CRITIQUE_NEEDS_REVISION in verdict_text or CRITIQUE_MAJOR_REWORK in verdict_text:
        # Open-PR step-aside (#1932 gap b3): mirrors the READY_TO_BUILD
        # branch's existing pr_number defer below — once a PR exists, a
        # cached NEEDS REVISION/MAJOR REWORK verdict must not route back to
        # /do-plan. Defer to G3 (guard_g3_pr_lock), the canonical open-PR
        # plan-stage redirect.
        if meta.get("pr_number"):
            return None
        return Dispatch(
            skill=SKILL_DO_PLAN,
            reason="G5: cached CRITIQUE verdict is NEEDS REVISION on unchanged plan hash",
            row_id="G5",
        )

    if CRITIQUE_READY_TO_BUILD in verdict_text:
        # D3: once BUILD has completed or a PR exists, the cached READY TO BUILD
        # verdict has already been consumed — defer to the downstream PR-stage
        # rows (TEST/REVIEW/PATCH/MERGE) instead of re-dispatching /do-build
        # forever on a finished build. Mirrors the D3 guard in rows 4a/4b/4c.
        if meta.get("pr_number") or stage_states.get("BUILD") == STATUS_COMPLETED:
            return None
        # #1871 present-gap short-circuit: G7 (guard_g7_plan_revising) now
        # precedes G5 in GUARDS list order, but G7's Gate 6 returns None
        # (falls through to G5) whenever the plan_revising lock is set, the
        # revision hasn't been applied yet, and a /do-plan dispatch already
        # appears in recent history — the lock may still be legitimately in
        # flight. Without this check, that fallthrough state would let G5's
        # cached READY-TO-BUILD verdict ship the pre-revision design via
        # /do-build. Only the READY-TO-BUILD branch needs this — the NEEDS
        # REVISION branch above already routes to /do-plan, which is correct
        # under a revision lock.
        if meta.get("plan_revising") and not meta.get("revision_applied"):
            return None
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

    **Ordering:** G7 is evaluated BEFORE G5 and G6 in ``GUARDS`` list order
    (issue #1871) — this is deliberate: G5's cached READY-TO-BUILD fast path
    does not read ``plan_revising`` on its own, so G7 must run first to
    intercept a stale-hash cache hit while a revision is pending. The
    "an already-mergeable PR is never blocked by a stale plan_revising flag"
    guarantee does NOT come from list position relative to G6 — it comes from
    Gate 1 above: G7 returns ``None`` immediately whenever ``pr_number`` is
    set. G6 only ever fires when ``pr_number`` is set, so in every state
    where G6 could dispatch ``/do-merge``, G7 has already deferred at Gate 1.
    G6 still wins regardless of where either guard sits in the list.

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
    if REVIEW_APPROVED not in normalize_verdict(review_verdict):
        return None
    # WS3d (#2062): never fast-path a head_sha-stale APPROVED verdict — a
    # commit landed after approval (or the live-head lookup failed, which
    # fails closed toward stale). Fall through to the dispatch table, where
    # row 8f routes to /do-pr-review at the new head. This makes G6 agree
    # with tools/merge_predicate's Group (c) freshness check.
    if _review_verdict_head_is_stale(stage_states, meta, context):
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
    guard_g8_artifact_verification,
    guard_g7_plan_revising,
    guard_g5_artifact_hash_cache,
    guard_g6_terminal_merge_ready,
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
# Dispatch table (Rows 1–10 from SKILL.md)
# ---------------------------------------------------------------------------


def _rule_no_plan(stage_states: dict, meta: dict, context: dict) -> bool:
    """No plan exists."""
    # If an open PR exists, a plan must exist too — defer to PR-stage rows.
    if meta.get("pr_number"):
        return False
    plan_status = stage_states.get("PLAN")
    # "No plan exists" is the absence of a plan file OR a pending PLAN stage.
    if plan_status in (None, "pending"):
        return True
    # Bootstrap case (#1640): PLAN="ready" but no plan doc on disk — the
    # state-machine pre-advanced the stage without a real plan existing yet.
    # Only treat this as "no plan" when an issue_number is available (so we
    # can verify) AND the plan file is actually absent.
    if plan_status == "ready" and meta.get("issue_number") and not meta.get("plan_exists"):
        return True
    return False


def _rule_plan_not_critiqued(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan exists and is ready to critique.

    Requires real evidence that a plan doc exists (#1640):
    - ``PLAN == "completed"`` → implies a plan doc was written (unchanged from #1275)
    - ``PLAN == "ready"``     → only counts if ``meta["plan_exists"]`` is True;
      without evidence, the state machine may have pre-advanced to "ready" before
      the plan doc was written (bootstrap race).
    """
    plan_status = stage_states.get("PLAN")
    critique_status = stage_states.get("CRITIQUE")
    if critique_status not in (None, "pending", "ready"):
        return False
    if plan_status == STATUS_COMPLETED:
        return True  # completed implies a plan doc exists (#1275 case intact)
    if plan_status == "ready":
        return bool(meta.get("plan_exists"))  # "ready" needs real evidence (#1640)
    return False


def _rule_critique_needs_revision(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan critiqued (NEEDS REVISION).

    Staleness step-aside (#1639): if the critique verdict predates the latest
    ``/do-plan`` dispatch, the plan was already revised, so this row steps aside
    (returns False) and lets row 2b re-dispatch ``/do-plan-critique`` for a
    fresh critique. Mirrors the ``_review_verdict_is_stale`` step-aside in
    ``_rule_review_has_findings``.

    Open-PR step-aside (#1932 gap b1): once a PR exists, a NEEDS REVISION
    critique verdict must never route back to ``/do-plan`` — this row steps
    aside and lets row 7 / G3 own PR-stage routing instead.
    """
    if meta.get("pr_number"):
        return False
    if _critique_verdict_is_stale(stage_states, meta):
        return False
    verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta))
    return CRITIQUE_NEEDS_REVISION in verdict


def _rule_critique_ready_no_concerns(stage_states: dict, meta: dict, context: dict) -> bool:
    """Plan critiqued (READY TO BUILD, zero concerns), no branch/PR."""
    verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta))
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
    verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta))
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
    verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta))
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


def _latest_dispatch_at(stage_states: dict, skill: str) -> str | None:
    """Return the 'at' timestamp of the most recent dispatch of the given skill, or None."""
    dispatches = stage_states.get("_sdlc_dispatches", [])
    result = None
    for entry in dispatches:
        if entry.get("skill") == skill:
            result = entry.get("at")
    return result


# The stored REVIEW verdict may carry a ``REVIEW_CONTEXT head_sha=<hex>``
# trailer naming the PR head commit it judged (emitted by /do-pr-review Step 5;
# the same trailer tools/merge_predicate's freshness check consumes). The text
# may have passed through ``normalize_verdict`` (uppercased, underscores mapped
# to spaces), so the pattern matches both the raw and normalized forms, and SHA
# comparison is case-insensitive. Kept in lockstep with
# ``tools.merge_predicate._HEAD_SHA_TRAILER_RE`` (duplicated here because the
# router must stay import-free of tools/ — the import-boundary contract).
_HEAD_SHA_TRAILER_RE = re.compile(
    r"REVIEW[_ ]CONTEXT\s+HEAD[_ ]SHA=([0-9A-Fa-f]{40})", re.IGNORECASE
)


def _review_verdict_head_is_stale(stage_states: dict, meta: dict, context: dict) -> bool:
    """Return True if the recorded REVIEW verdict is stale against the live PR head.

    WS3d (issue #2062): the router's freshness definition must agree with
    ``tools/merge_predicate``'s Group (c) check (which compares the verdict's
    ``REVIEW_CONTEXT head_sha=`` trailer to the PR head commit), ending the
    router↔predicate oscillation where G6/row 10 fast-path a verdict the merge
    predicate then refuses as stale.

    The live PR head arrives via ``context["pr_head_sha"]`` — assembled by
    ``tools/sdlc_next_skill._build_context`` (the G8-style live-verification
    seam; the router itself makes no ``gh`` calls). Contract:

    - key ABSENT from context → signal not supplied (no PR, no recorded
      verdict, or a non-CLI caller) → False (inert; other rules own routing)
    - no recorded verdict → False (the no-verdict recovery rows own that state)
    - key present but EMPTY (the fail-closed lookup-failure sentinel, set
      alongside ``pr_head_sha_lookup_failed``) → **True (stale)** — a lookup
      failure must route toward re-review, never silently pass as fresh
    - verdict has NO parseable head_sha trailer → **True (stale)** — an
      unattributable verdict is re-reviewed at the current head, never trusted
      as fresh (re-review records a fresh verdict WITH the trailer, so this
      converges; loop-bound by G4)
    - trailer present → stale iff it differs (case-insensitive) from the head
    """
    if "pr_head_sha" not in context:
        return False
    verdict = _latest_review_verdict(stage_states, meta)
    if not verdict.strip():
        return False
    head_sha = context.get("pr_head_sha") or ""
    if not head_sha:
        return True
    trailer = _HEAD_SHA_TRAILER_RE.search(verdict)
    if not trailer:
        return True
    return trailer.group(1).lower() != head_sha.lower()


def _review_verdict_is_stale(stage_states: dict) -> bool:
    """Return True if the REVIEW verdict predates the latest /do-patch dispatch (stale).

    Fails safe to False (not stale) on any missing data or parse error.

    Edge cases:
    - missing ``recorded_at`` → False (not stale)
    - no prior ``/do-patch`` dispatch → False (not stale)
    - equal timestamps → False (not stale, strict ``<``)
    - parse failure → False (not stale)
    """
    try:
        verdict_dict = stage_states.get("_verdicts", {}).get("REVIEW", {})
        if not isinstance(verdict_dict, dict):
            return False
        recorded_at = verdict_dict.get("recorded_at")
        if not recorded_at:
            return False
        latest_patch_at = _latest_dispatch_at(stage_states, SKILL_DO_PATCH)
        if not latest_patch_at:
            return False
        verdict_dt = datetime.fromisoformat(recorded_at)
        patch_dt = datetime.fromisoformat(latest_patch_at)
        return verdict_dt < patch_dt
    except Exception:
        return False


def _critique_verdict_is_stale(stage_states: dict, meta: dict | None = None) -> bool:
    """Return True if the CRITIQUE verdict predates the latest /do-plan dispatch (stale).

    Structural twin of :func:`_review_verdict_is_stale` (mirrors PR #1657's
    REVIEW pattern for the CRITIQUE path, #1639). A critique verdict is stale
    once the plan it judged has been revised — i.e. a ``/do-plan`` dispatch is
    recorded *after* the verdict's ``recorded_at``. A stale NEEDS REVISION
    verdict must route back to ``/do-plan-critique`` (row 2b) rather than
    dead-ending on ``/do-plan`` (row 3).

    Event-scoped convergence latch (#1760): ``meta["revision_applied_at"]`` is
    an ISO-8601 timestamp written by ``/do-plan`` on the settle-and-build
    revision pass (structural twin of the existing sticky ``revision_applied``
    boolean, but event-scoped rather than sticky — see
    :func:`tools.sdlc_stage_query._parse_revision_applied_at`). A bare boolean
    is insufficient: ``/do-plan`` sets ``revision_applied: true`` on *every*
    revision pass, so it can't distinguish "this is the settle-and-build
    dispatch" from "this is some later unrelated ``/do-plan`` dispatch".

    When present, staleness is suppressed ONLY when the latest ``/do-plan``
    dispatch (``_latest_dispatch_at``) is NOT LATER than
    ``revision_applied_at`` — i.e. the dispatch that produced the revision is
    the one being judged, not a subsequent one. Any ``/do-plan`` dispatch
    whose ``at`` postdates ``revision_applied_at`` re-stales NORMALLY,
    regardless of the (still sticky) boolean, so a later unrelated revision
    never gets a free pass to BUILD. When ``revision_applied_at`` is absent,
    unparseable, or ``meta`` is not supplied, the latch is inert and this
    falls back to the original timestamp-only staleness check (fail-safe to
    pre-#1760 behavior).

    **Verdict-kind gate (WS4, #2049):** the latch engages ONLY for verdicts
    that do not require a revision (the #1760 settle-and-build path, e.g.
    READY TO BUILD with concerns). For NEEDS REVISION / MAJOR REWORK the
    requested revision is exactly what invalidates the verdict, so the latch
    never suppresses: a settled revision leaves the verdict stale → row 2b →
    re-critique. Without this gate, suppression sent the state to row 3's
    ``/do-plan`` forever (``/do-plan`` re-writes ``revision_applied_at`` on
    every pass, re-arming the suppression each round — the #1925/#1968
    recurrence).

    Fails safe to False (not stale) on any missing data or parse error.

    Edge cases:
    - ``_verdicts.CRITIQUE`` absent / not a dict → False (not stale)
    - missing ``recorded_at`` → False (not stale)
    - no prior ``/do-plan`` dispatch → False (not stale)
    - equal timestamps → False (not stale, strict ``<``)
    - parse failure (non-iso timestamp) → False (not stale)
    - latch: latest ``/do-plan`` dispatch <= ``revision_applied_at`` → False
      (not stale, converged)
    - latch: latest ``/do-plan`` dispatch > ``revision_applied_at`` → normal
      timestamp staleness applies (re-stales if genuinely stale)
    - latch: malformed/absent ``revision_applied_at`` → latch inert, normal
      timestamp staleness applies
    """
    try:
        verdict_dict = stage_states.get("_verdicts", {}).get("CRITIQUE", {})
        if not isinstance(verdict_dict, dict):
            return False
        recorded_at = verdict_dict.get("recorded_at")
        if not recorded_at:
            return False
        latest_plan_at = _latest_dispatch_at(stage_states, SKILL_DO_PLAN)
        if not latest_plan_at:
            return False
        verdict_dt = datetime.fromisoformat(recorded_at)
        plan_dt = datetime.fromisoformat(latest_plan_at)

        # WS4 (#2049): the latch protects ONLY the settle-and-build path — a
        # READY TO BUILD (with concerns) verdict whose own settle revision
        # must not re-stale it back into critique (#1760). For a
        # revision-REQUIRING verdict (NEEDS REVISION / MAJOR REWORK) the
        # requested revision is exactly what invalidates the verdict, so the
        # latch must never suppress staleness there: suppression made row 2b
        # step aside and row 3 re-dispatch /do-plan forever (the #1925/#1968
        # deadlock — /do-plan re-writes revision_applied_at on every pass,
        # re-arming the suppression each round). Timestamp-only: the latch
        # consumes revision_applied_at exclusively; the sticky boolean is
        # never consulted (no "revised ever vs. revised since THIS verdict"
        # ambiguity).
        verdict_text = normalize_verdict(_verdict_text(verdict_dict))
        requires_revision = (
            CRITIQUE_NEEDS_REVISION in verdict_text or CRITIQUE_MAJOR_REWORK in verdict_text
        )

        revision_applied_at = (meta or {}).get("revision_applied_at")
        if revision_applied_at and not requires_revision:
            try:
                revision_dt = datetime.fromisoformat(revision_applied_at)
            except Exception:
                revision_dt = None  # malformed -> latch inert, fall through
            if revision_dt is not None and not (plan_dt > revision_dt):
                return False  # latest /do-plan dispatch settled this verdict

        return verdict_dt < plan_dt
    except Exception:
        return False


def _rule_critique_verdict_stale(stage_states: dict, meta: dict, context: dict) -> bool:
    """Critique verdict is stale (plan revised since the verdict was recorded).

    Fires row 2b (``/do-plan-critique``) when the CRITIQUE verdict predates the
    latest ``/do-plan`` dispatch AND a non-empty critique verdict text exists.
    Marker-agnostic by design: the #1639 dead-end leaves CRITIQUE at
    ``in_progress``, so this rule must NOT require any particular marker value.

    Loop-bound: G5 (``guard_g5_artifact_hash_cache``) runs before this row and
    short-circuits re-critique when the plan hash is unchanged, so this rule can
    only progress on a genuinely revised plan. See the docstring on row 2b.
    """
    if not _critique_verdict_is_stale(stage_states, meta):
        return False
    return bool(_latest_critique_verdict(stage_states, meta).strip())


def _rule_critique_in_progress_no_verdict(stage_states: dict, meta: dict, context: dict) -> bool:
    """CRITIQUE is in_progress but never recorded a verdict — re-dispatch critique.

    The #1668 dead-end: /do-plan-critique ran (CRITIQUE marker == "in_progress")
    but never persisted a verdict, so _verdicts.CRITIQUE is empty and
    latest_critique_verdict is None. With no PR yet, rows 2/2b/3/4* and G1 all
    miss, leaving Blocked('no matching dispatch rule'). Re-running the critique
    is the correct recovery (it is what the supervisor did manually).

    Distinct from row 2b (#1639): 2b owns the *recorded-but-stale* verdict (it
    requires a recorded_at timestamp); 2c owns the *empty* verdict. The two are
    disjoint.

    Narrowly gated so it cannot fire when:
      - a PR exists (defer to G3 / PR-stage rows 7-10)
      - any critique verdict IS recorded (let rows 2b/3/4a handle it)
      - CRITIQUE is not in_progress (None/pending → row 2; completed/failed → other rows)

    Loop-bound by G4 (guard_g4_oscillation): same_stage_dispatch_count caps
    re-dispatches and escalates to a human. G2 does not bound it (it keys off
    critique_cycle_count, which stays 0 with no recorded verdict).
    """
    if meta.get("pr_number"):
        return False
    if stage_states.get("CRITIQUE") != STATUS_IN_PROGRESS:
        return False
    # A recorded verdict (in _verdicts or meta) means another row owns this state.
    if _latest_critique_verdict(stage_states, meta).strip():
        return False
    return True


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
    """PR review has findings (blockers, nits, or tech debt).

    Returns True when the review verdict signals changes are needed.
    Underscore-form verdicts (``CHANGES_REQUESTED``) are handled transparently
    via ``normalize_verdict`` (#1638).

    Timestamp-staleness supersession (#1641): if the stored verdict predates the
    latest ``/do-patch`` dispatch, it is stale and this rule returns False so that
    row 8b (``_rule_patch_applied_after_review``) can re-dispatch ``/do-pr-review``
    for a fresh look.
    """
    if not meta.get("pr_number"):
        return False
    review_verdict = ""
    if meta.get("latest_review_verdict"):
        review_verdict = meta["latest_review_verdict"]
    else:
        verdicts = stage_states.get("_verdicts") or {}
        review_verdict = _verdict_text(verdicts.get("REVIEW"))
    review_verdict_norm = normalize_verdict(review_verdict)
    if not review_verdict:
        return False
    # Timestamp-staleness supersession (#1641): if this verdict predates the
    # latest /do-patch dispatch, it is stale; step aside so row 8b runs.
    if _review_verdict_is_stale(stage_states):
        return False
    if REVIEW_CHANGES_REQUESTED in review_verdict_norm:
        return True
    if "PARTIAL" in review_verdict_norm:
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


def _rule_review_in_progress_no_verdict(stage_states: dict, meta: dict, context: dict) -> bool:
    """REVIEW is in_progress but never recorded a verdict — re-dispatch review.

    Mirrors the #1668 pattern fixed on the CRITIQUE side (row 2c): /do-pr-review
    ran (REVIEW marker == "in_progress") but never persisted a verdict, so
    _verdicts.REVIEW is empty and latest_review_verdict is None. With rows 7/8/8b
    all requiring a verdict or completed PATCH, and G3 not matching, the router
    falls through to Blocked('no matching dispatch rule'). Re-running the review
    is the correct recovery.

    Distinct from row 8b (#1641): 8b owns the patch-applied-then-re-review path
    (it requires PATCH==completed AND last_dispatch==/do-patch); 8c owns the
    *empty* verdict with no patch applied. Disjoint predicates.

    Narrowly gated so it cannot fire when:
      - no PR exists (REVIEW only happens after BUILD opens a PR)
      - REVIEW is not in_progress (None/pending → row 7; completed → rows 9/10)
      - any review verdict IS recorded (let rows 8/8b handle it)
      - row 8b would own this state (PATCH completed after review — step aside)

    Loop-bound by G4 (guard_g4_oscillation): same_stage_dispatch_count caps
    re-dispatches and escalates to a human if the review keeps stalling.
    """
    if not meta.get("pr_number"):
        return False
    if stage_states.get("REVIEW") != STATUS_IN_PROGRESS:
        return False
    # A recorded verdict means another row owns this state.
    if _latest_review_verdict(stage_states, meta).strip():
        return False
    # Step aside for row 8b when a patch has already been applied after review.
    if _rule_patch_applied_after_review(stage_states, meta, context):
        return False
    return True


def _rule_review_crashed_after_dispatch(stage_states: dict, meta: dict, context: dict) -> bool:
    """PATCH completed, /do-pr-review was dispatched, but no verdict was ever recorded.

    The #1932 gap (a) crash: /do-pr-review is dispatched after PATCH completes,
    but the skill crashes (or partially writes) before persisting a REVIEW
    verdict. Depending on exactly where the crash lands, REVIEW is left marked
    either ``failed`` (dead-ends the router at ``Blocked('no matching dispatch
    rule')``) or ``completed`` (silently misroutes to row 9's ``/do-docs``,
    skipping review entirely). Both are recovered the same way: re-dispatch
    ``/do-pr-review``.

    Marker-agnostic by design (mirrors row 2c/8c): matches on the ABSENCE of a
    recorded verdict plus ``last_dispatched_skill == /do-pr-review``, not on a
    specific REVIEW marker value, since the crash can leave either marker.

    Disjoint from neighboring rows by construction (no defensive re-checks
    needed — mirrors the row-8c treatment in bb4366a4):
      - Row 7 (``_rule_pr_exists_no_review``) owns REVIEW in
        (None, pending, ready) with no verdict. The earlier
        ``stage_states.get("REVIEW") not in (STATUS_COMPLETED,
        STATUS_FAILED)`` restriction already excludes that band, so row 7's
        territory can never reach this point.
      - Row 8b (``_rule_patch_applied_after_review``) owns the case where the
        last dispatch was ``/do-patch`` (not ``/do-pr-review``). The earlier
        ``last != SKILL_DO_PR_REVIEW`` restriction already pins
        ``last_dispatched_skill`` to ``/do-pr-review``, so row 8b's territory
        can never reach this point.
      - Row 8c (``_rule_review_in_progress_no_verdict``) owns
        REVIEW == in_progress. No separate step-aside line is needed here:
        the earlier ``stage_states.get("REVIEW") not in (STATUS_COMPLETED,
        STATUS_FAILED)`` restriction already excludes the in_progress band
        by construction.
      - Row 9 (``_rule_review_approved_docs_not_done``) is disjoint "by
        verdict": a sibling fix (#1932 gap c) gates row 9 on a recorded
        APPROVED verdict, while row 8d requires NO recorded verdict at all —
        the two predicates can never both match the same state.

    Loop-bound by G4 (``guard_g4_oscillation`` via ``same_stage_dispatch_count``):
    repeated re-dispatch of ``/do-pr-review`` without a state change escalates
    to Blocked after ``MAX_SAME_STAGE_DISPATCHES`` turns, same as rows 2c/8c.

    See #1932.
    """
    if not meta.get("pr_number"):
        return False
    if stage_states.get("PATCH") != STATUS_COMPLETED:
        return False
    if stage_states.get("REVIEW") not in (STATUS_COMPLETED, STATUS_FAILED):
        return False
    # A recorded verdict means another row owns this state.
    if _latest_review_verdict(stage_states, meta).strip():
        return False
    last = meta.get("last_dispatched_skill") or ""
    if last != SKILL_DO_PR_REVIEW:
        return False
    return True


def _rule_review_completed_no_verdict(stage_states: dict, meta: dict, context: dict) -> bool:
    """REVIEW marked completed with NO recorded verdict — re-dispatch review.

    WS3b (issue #2062): the state observed on the #1897 lane —
    ``REVIEW=completed, DOCS=completed, PATCH=pending, no verdict,
    last=/do-build`` — was owned by nobody: row 8c requires
    ``REVIEW==in_progress``, row 8d requires ``PATCH==completed`` AND
    ``last_dispatched_skill == /do-pr-review``, and row 9 (post-#1932)
    requires a recorded APPROVED verdict. With row 10 previously ungated, it
    fell straight through to ``/do-merge``. This row owns every remaining
    ``REVIEW==completed`` + empty-verdict state (a superset of the "no
    ``/do-pr-review`` in dispatch history" instance — any no-verdict
    completion is unearned and must be re-reviewed) and re-dispatches
    ``/do-pr-review``.

    This is also the recovery row for the WS3c ``stage-marker`` refusal: a
    REVIEW ``completed`` marker is now unwritable without a readable verdict,
    and the refused no-verdict state redirects here to re-review instead of
    deadlocking (plan Risk 2).

    Disjoint from its neighbors:
      - row 8c owns ``REVIEW==in_progress`` (excluded by the ``completed``
        requirement here)
      - row 8d owns the ``PATCH==completed`` + ``last==/do-pr-review`` crash
        state (explicit step-aside below; 8d also precedes this row)
      - rows 9/10 require a recorded APPROVED verdict, which this row's
        empty-verdict requirement excludes

    Loop-bound by G4 (``same_stage_dispatch_count``), same as rows 2c/8c/8d.
    """
    if not meta.get("pr_number"):
        return False
    if stage_states.get("REVIEW") != STATUS_COMPLETED:
        return False
    if _latest_review_verdict(stage_states, meta).strip():
        return False
    # Step aside for row 8d's crash state (it precedes this row anyway).
    if _rule_review_crashed_after_dispatch(stage_states, meta, context):
        return False
    return True


def _rule_review_verdict_head_stale(stage_states: dict, meta: dict, context: dict) -> bool:
    """APPROVED verdict is head_sha-stale (post-approval commit) — re-review.

    WS3d (issue #2062): a recorded APPROVED verdict whose ``head_sha`` trailer
    does not match the live PR head (a commit landed after approval), whose
    trailer is absent/malformed, or whose live-head lookup failed (fail-closed)
    must route to ``/do-pr-review`` at the new head — never fast-path to
    ``/do-merge``. This is the dispatch-table twin of the G6 step-aside; the
    two together make the router agree with ``tools/merge_predicate``'s
    Group (c) freshness check and end the router↔predicate oscillation loop.

    Scoped to APPROVED verdicts: a head-stale CHANGES REQUESTED verdict still
    needs its findings patched first (rows 8/8b own that path). Inert when the
    ``pr_head_sha`` context signal is not supplied. Loop-bound by G4; a
    re-review records a fresh verdict with the current head's trailer, so the
    loop converges.
    """
    if not meta.get("pr_number"):
        return False
    if REVIEW_APPROVED not in normalize_verdict(_latest_review_verdict(stage_states, meta)):
        return False
    return _review_verdict_head_is_stale(stage_states, meta, context)


def _rule_review_approved_docs_not_done(stage_states: dict, meta: dict, context: dict) -> bool:
    """Review APPROVED, zero findings, docs NOT done."""
    if not meta.get("pr_number"):
        return False
    if stage_states.get("REVIEW") != STATUS_COMPLETED:
        return False
    # #1932 gap (c): gate on a recorded APPROVED verdict at the source, rather
    # than inferring "approved" from REVIEW==completed alone — REVIEW can be
    # marked completed with no verdict ever recorded (crash), which silently
    # misrouted here to /do-docs. Row 8d owns that no-verdict state instead.
    if REVIEW_APPROVED not in normalize_verdict(_latest_review_verdict(stage_states, meta)):
        return False
    docs_status = stage_states.get("DOCS")
    return docs_status not in (STATUS_COMPLETED,)


def _rule_ready_to_merge(stage_states: dict, meta: dict, context: dict) -> bool:
    """Review APPROVED, zero findings, docs done, ready to merge."""
    if not meta.get("pr_number"):
        return False
    # WS3a (#2062): mirror row 9's #1932 verdict gate — trusting
    # ``REVIEW==completed`` alone let the no-verdict crash state that rows
    # 8c/8d/9 correctly step aside from fall straight through to /do-merge
    # (the #1897 misroute). Row 8e owns the no-verdict state instead.
    if REVIEW_APPROVED not in normalize_verdict(_latest_review_verdict(stage_states, meta)):
        return False
    # WS3d (#2062): a head_sha-stale APPROVED verdict is not merge-ready —
    # row 8f owns it (re-review at the new head).
    if _review_verdict_head_is_stale(stage_states, meta, context):
        return False
    needed = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS"]
    return _stages_completed(stage_states, needed)


# Attach human-readable state strings as docstrings — the parity test uses
# these to cross-check SKILL.md row state cells. Keep in sync with SKILL.md.
_rule_no_plan.__doc__ = "No plan exists"
_rule_plan_not_critiqued.__doc__ = "Plan exists, not yet critiqued"
_rule_critique_verdict_stale.__doc__ = "Critique verdict is stale (plan revised since)"
_rule_critique_in_progress_no_verdict.__doc__ = (
    "Critique in_progress, no verdict recorded (stalled) — re-critique"
)
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
_rule_review_in_progress_no_verdict.__doc__ = (
    "Review in_progress, no verdict recorded (stalled) — re-review"
)
_rule_review_crashed_after_dispatch.__doc__ = (
    "PATCH completed, /do-pr-review dispatched, no verdict recorded (crashed) — re-run review"
)
_rule_review_completed_no_verdict.__doc__ = (
    "REVIEW marked completed with no verdict recorded (unearned marker) — re-run review"
)
_rule_review_verdict_head_stale.__doc__ = (
    "APPROVED verdict head_sha-stale against the live PR head — re-review at the new head"
)
_rule_review_approved_docs_not_done.__doc__ = (
    "Review APPROVED with zero findings, docs NOT done (see Step 3)"
)
_rule_ready_to_merge.__doc__ = (
    "Review APPROVED (recorded verdict, head_sha-fresh) with zero findings, docs done, "
    "AND all display stages show completed in stage_states "
    "(or stage_states unavailable), ready to merge"
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
    # Row 2b (#1639): a stale CRITIQUE verdict (recorded before the latest
    # /do-plan dispatch) means the plan was revised since the verdict — re-run
    # the critique rather than dead-ending on /do-plan. Placed BEFORE row 3 so
    # stale → re-critique wins over row 3's stale-text → /do-plan match. Mirrors
    # REVIEW row 8b. Disjoint from G1 (which fires only when the last dispatch
    # was /do-plan-critique; the #1639 dead-end has last dispatch = /do-plan),
    # and bounded by G5 (re-critique short-circuits on an unchanged plan hash).
    DispatchRule(
        row_id="2b",
        state_predicate=_rule_critique_verdict_stale,
        skill=SKILL_DO_PLAN_CRITIQUE,
        reason="Critique verdict is stale (plan revised since) — re-critique",
    ),
    # Row 2c (#1668): CRITIQUE is in_progress but NO verdict was ever recorded
    # (_verdicts.CRITIQUE empty, latest_critique_verdict None) and no PR exists —
    # the critique skill ran but never persisted a verdict, dead-ending the router
    # at Blocked('no matching dispatch rule'). Re-run the critique. Distinct from
    # row 2b (#1639): 2b = recorded-but-stale verdict; 2c = empty verdict. Disjoint
    # predicates. Placed after 2b (groups critique-staleness recovery) and before
    # row 3 (rows 3/4* all require a recorded verdict, so never match this state).
    # Loop-bound by G4 (oscillation), not G2 (which keys off recorded verdicts).
    DispatchRule(
        row_id="2c",
        state_predicate=_rule_critique_in_progress_no_verdict,
        skill=SKILL_DO_PLAN_CRITIQUE,
        reason="Critique stalled with no recorded verdict — re-run critique",
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
    # Row 8c: REVIEW is in_progress with no recorded verdict and row 8b does not
    # apply (no patch applied after review). Mirrors row 2c on the CRITIQUE side.
    # Re-dispatch /do-pr-review to recover from a stalled review. Loop-bound by G4.
    DispatchRule(
        row_id="8c",
        state_predicate=_rule_review_in_progress_no_verdict,
        skill=SKILL_DO_PR_REVIEW,
        reason="Review stalled with no recorded verdict — re-run review",
    ),
    # Row 8d (#1932 gap a): PATCH completed, /do-pr-review was dispatched, but
    # no verdict was ever recorded — the review skill crashed before
    # persisting a verdict, leaving REVIEW at either "failed" (dead-ends the
    # router at Blocked) or "completed" (silently misroutes to row 9). Both
    # are recovered by re-dispatching /do-pr-review. Disjoint from rows
    # 7/8b/8c via explicit step-asides in the predicate, and disjoint from
    # row 9 "by verdict" (row 9 will require a recorded APPROVED verdict; 8d
    # requires NO recorded verdict). Loop-bound by G4.
    DispatchRule(
        row_id="8d",
        state_predicate=_rule_review_crashed_after_dispatch,
        skill=SKILL_DO_PR_REVIEW,
        reason="Review dispatch crashed without recording a verdict — re-run review",
    ),
    # Row 8e (#2062 WS3b): REVIEW==completed with NO recorded verdict and 8d
    # not applicable — the #1897 no-owner state that previously fell through
    # to row 10's ungated /do-merge. Also the recovery row for the WS3c
    # stage-marker refusal (a refused REVIEW-completed write leaves exactly
    # this no-verdict state). Re-dispatch /do-pr-review; loop-bound by G4.
    DispatchRule(
        row_id="8e",
        state_predicate=_rule_review_completed_no_verdict,
        skill=SKILL_DO_PR_REVIEW,
        reason="REVIEW completed without a recorded verdict — re-run review",
    ),
    # Row 8f (#2062 WS3d): a recorded APPROVED verdict that is head_sha-stale
    # against the live PR head (post-approval commit, missing trailer, or a
    # failed live-head lookup — all fail toward stale). Re-review at the new
    # head instead of letting rows 9/10 or G6 treat the verdict as fresh.
    # Ordered before row 9 so a stale approval re-reviews before docs/merge.
    DispatchRule(
        row_id="8f",
        state_predicate=_rule_review_verdict_head_stale,
        skill=SKILL_DO_PR_REVIEW,
        reason="APPROVED verdict is stale against the PR head — re-review at the new head",
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


def decide_next_dispatch(
    stage_states: dict,
    meta: dict | None = None,
    context: dict | None = None,
) -> Dispatch | Blocked:
    """Decide which sub-skill the SDLC router should dispatch next.

    Algorithm:
      1. Evaluate guards G1–G7. If any guard trips, return its decision.
      2. Otherwise, walk ``DISPATCH_RULES`` in row order. Take the first
         rule whose ``state_predicate`` returns True as the primary dispatch.
      3. If no rule matches at all, return ``Blocked(reason="no matching rule")``.

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
        ``Dispatch`` with the chosen skill, or ``Blocked`` if the router
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
        # Distinguishable UNKNOWN / unresolvable merge-state blocked reason.
        # CRITICAL: Only fires when pr_merge_state is None or "UNKNOWN" —
        # not for DIRTY/BLOCKED (those are real states that route normally).
        # G6 still requires exactly "CLEAN"; this check does NOT change G6.
        pr_num = meta.get("pr_number")
        pr_state = meta.get("pr_merge_state")
        if pr_num and pr_state in (None, "UNKNOWN"):
            resolved_repo = meta.get("_resolved_target_repo") or "<none — using cwd>"
            return Blocked(
                reason=(
                    f"PR #{pr_num} merge state {pr_state!r} — could not resolve mergeability "
                    f"(target repo: {resolved_repo}; check GH_REPO / SDLC_TARGET_REPO env)"
                ),
                guard_id=None,
            )
        return Blocked(
            reason="no matching dispatch rule",
            guard_id=None,
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
            reflects the live PR state. If omitted, the snapshot's
            ``pr_number`` is ``None`` — the explicit argument is the sole
            provenance (``sdlc_stage_query`` resolves the PR number from the
            ``AgentSession.pr_number`` field into ``_meta.pr_number``).

    Returns:
        The mutated stage_states dict.
    """
    timestamp = (now or datetime.now(UTC)).isoformat()
    # Build a snapshot from a stage_states view that EXCLUDES the history
    # list itself, otherwise the counter would never match across invocations.
    view = {k: v for k, v in stage_states.items() if k != "_sdlc_dispatches"}
    snapshot = build_stage_snapshot(view, meta={"pr_number": pr_number})

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
