"""Python reference implementation of the SDLC router dispatch algorithm.

The canonical human-readable router runbook lives in
``.claude/skills/sdlc/SKILL.md``. That markdown document is parsed by an LLM at
runtime to decide which sub-skill to dispatch. This module is the **Python
reference implementation of the same algorithm** — a pure function that takes
structured state (stage statuses + metadata) and returns the next dispatch.

Having a pure-Python version serves three purposes:

1. It lets a regression test replay the 12-step sequence from issue #1036 and
   assert the router terminates cleanly.
2. It lets a parity test (``tests/unit/test_sdlc_skill_md_parity.py``) cross-
   check the SKILL.md dispatch rows against this module's ``DISPATCH_RULES``
   list. Drift between the two is caught at CI time.
3. It provides a testable surface for the Legal Dispatch Guards (G1–G5)
   without requiring a live LLM invocation.

The algorithm:

    decide_next_dispatch(stage_states, meta, context)
        -> Dispatch | Blocked

    1. Evaluate guards (G1–G6). If any guard trips, return its decision.
    2. Otherwise, walk the ``DISPATCH_RULES`` list in row order and return
       the first rule whose ``state_predicate`` accepts ``(stage_states, meta,
       context)``.
    3. If no rule matches, return ``Blocked(reason="no matching rule")``.

The ``DISPATCH_RULES`` ordering mirrors the row numbers in SKILL.md's dispatch
table (1, 2, 3, 4a, 4b, 4c, 5, 6, 7, 8, 8b, 9, 10, 10b). Each rule carries a
``row_id`` string so the parity test can key rows between the two formats.
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

# Skill command strings. Keep in sync with SKILL.md dispatch table and
# ``agent/pipeline_graph.STAGE_TO_SKILL``.
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
    """
    count = meta.get("same_stage_dispatch_count", 0)
    if count < MAX_SAME_STAGE_DISPATCHES:
        return None

    skill = meta.get("last_dispatched_skill") or "<unknown>"
    return Blocked(
        reason=(f"G4: stage oscillation — {skill} dispatched {count} times without state change"),
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
    return plan_status in (STATUS_COMPLETED, "ready") and critique_status in (None, "pending")


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
    """Plan critiqued (READY TO BUILD, concerns), revision_applied not set."""
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_READY_TO_BUILD not in verdict or "WITH CONCERNS" not in verdict:
        return False
    return not bool(meta.get("revision_applied"))


def _rule_critique_ready_with_concerns_revision_applied(
    stage_states: dict, meta: dict, context: dict
) -> bool:
    """Plan critiqued (READY TO BUILD, concerns), revision_applied true."""
    verdict = _latest_critique_verdict(stage_states, meta).upper()
    if CRITIQUE_READY_TO_BUILD not in verdict or "WITH CONCERNS" not in verdict:
        return False
    return bool(meta.get("revision_applied"))


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


def decide_next_dispatch(
    stage_states: dict,
    meta: dict | None = None,
    context: dict | None = None,
) -> Dispatch | Blocked:
    """Decide which sub-skill the SDLC router should dispatch next.

    Algorithm:
      1. Evaluate guards G1–G5. If any guard trips, return its decision.
      2. Otherwise, walk ``DISPATCH_RULES`` in row order. Return the first
         rule whose ``state_predicate`` returns True.
      3. If no rule matches, return ``Blocked(reason="no matching rule")``.

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
        ``Dispatch`` with the chosen skill and rationale, or ``Blocked`` if
        the router escalates to human.
    """
    meta = meta or {}
    context = context or {}

    guard_result = evaluate_guards(stage_states, meta, context)
    if guard_result is not None:
        return guard_result

    for rule in DISPATCH_RULES:
        try:
            if rule.state_predicate(stage_states, meta, context):
                return Dispatch(
                    skill=rule.skill,
                    reason=rule.reason,
                    row_id=rule.row_id,
                )
        except Exception as e:
            # Predicates should never raise; log and continue so one bad rule
            # doesn't break the whole dispatch.
            logger.debug(f"DispatchRule {rule.row_id} predicate raised: {e}")

    return Blocked(
        reason="no matching dispatch rule",
        guard_id=None,
    )


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

    return (count, skill)
