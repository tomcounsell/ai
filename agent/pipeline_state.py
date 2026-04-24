"""Pipeline State Machine for SDLC stage tracking.

Replaces the inference-based stage detection system (stage_detector.py,
skill_outcome.py, checkpoint.py) with a programmatic state machine that
records transitions at the points where they actually happen.

The state machine wraps pipeline_graph.py and manages stage statuses:
- pending: stage has not started
- ready: predecessor completed, this stage can start
- in_progress: stage is currently running
- completed: stage finished successfully
- failed: stage finished with failure

State is persisted as a JSON dict on AgentSession.stage_states.
Each PM session run creates a fresh state machine from the session.

Usage:
    from agent.pipeline_state import PipelineStateMachine

    sm = PipelineStateMachine(session)
    sm.start_stage("BUILD")      # validates PLAN is completed
    sm.complete_stage("BUILD")   # marks BUILD completed, TEST ready
    sm.get_display_progress()    # returns {stage: status} for display
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from agent.pipeline_graph import (
    DISPLAY_STAGES,
    PIPELINE_EDGES,
    get_next_stage,
)

if TYPE_CHECKING:
    from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# All known stages including PATCH (routing-only) and CRITIQUE
ALL_STAGES = ["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]

# Valid status values
VALID_STATUSES = frozenset({"pending", "ready", "in_progress", "completed", "failed"})

# Set of known stage names for fast lookup
_ALL_STAGES_SET = frozenset(ALL_STAGES)


class StageStates(BaseModel):
    """Validated container for stage_states JSON data.

    Enforces that stage names are from ALL_STAGES and status values are
    from VALID_STATUSES. Unknown stage names are dropped. Unknown status
    values default to 'pending' for backward compatibility.

    Used at read/write boundaries in PipelineStateMachine to ensure data
    integrity of stage_states persisted in Redis.
    """

    stages: dict[str, str]

    @field_validator("stages", mode="before")
    @classmethod
    def validate_stages(cls, v: dict) -> dict:
        """Drop unknown stage names; coerce unknown statuses to 'pending'."""
        if not isinstance(v, dict):
            return {}
        validated = {}
        for stage, status in v.items():
            # Skip internal metadata keys (e.g. _patch_cycle_count)
            if stage.startswith("_"):
                continue
            if stage not in _ALL_STAGES_SET:
                logger.debug(f"StageStates: dropping unknown stage {stage!r}")
                continue
            if status not in VALID_STATUSES:
                logger.debug(
                    f"StageStates: unknown status {status!r} for {stage}, defaulting to 'pending'"
                )
                status = "pending"
            validated[stage] = status
        return validated

    @classmethod
    def from_dict(cls, data: dict) -> StageStates:
        """Create StageStates from a raw dict, filtering out metadata keys."""
        return cls(stages=data)

    def to_dict(self) -> dict[str, str]:
        """Return the validated stages dict."""
        return dict(self.stages)


# Regex to match <!-- OUTCOME {...} --> blocks in agent output
_OUTCOME_RE = re.compile(r"<!-- OUTCOME (\{.*?\}) -->")


def _parse_outcome_contract(output_tail: str) -> dict | None:
    """Parse an OUTCOME contract from agent output tail.

    Scans for ``<!-- OUTCOME {...} -->`` blocks and parses the JSON payload.
    If multiple blocks exist, uses the last one (most recent).

    Args:
        output_tail: Last ~500 chars of agent output.

    Returns:
        Parsed dict with at least a ``status`` key, or None if no valid
        OUTCOME block is found.
    """
    if not output_tail:
        return None

    matches = _OUTCOME_RE.findall(output_tail)
    if not matches:
        return None

    # Use last match (most recent OUTCOME block)
    raw = matches[-1]
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.debug("_parse_outcome_contract: malformed JSON in OUTCOME block")
        return None

    if not isinstance(parsed, dict) or "status" not in parsed:
        logger.debug("_parse_outcome_contract: missing 'status' key in OUTCOME block")
        return None

    return parsed


def _record_stage_metric(metric_name: str, stage: str) -> None:
    """Record an analytics metric for a stage transition. Best-effort."""
    try:
        from analytics.collector import record_metric

        record_metric(metric_name, 1, {"stage": stage})
    except Exception:
        pass


# Canonical critique verdict strings, matched case-insensitively against the
# dev-session output tail. Order matters: the longer "ready to build (with
# concerns)" must be tested before the generic "ready to build" prefix so we
# always capture the richer form.
_CRITIQUE_VERDICT_PATTERNS = [
    ("READY TO BUILD (with concerns)", "ready to build (with concerns)"),
    ("READY TO BUILD (no concerns)", "ready to build (no concerns)"),
    ("MAJOR REWORK", "major rework"),
    ("NEEDS REVISION", "needs revision"),
    ("READY TO BUILD", "ready to build"),
]

# Canonical review verdicts.
_REVIEW_VERDICT_PATTERNS = [
    ("CHANGES REQUESTED", "changes requested"),
    ("APPROVED", "approved"),
    ("REVIEW PASSED", "review passed"),
    ("REVIEW FAILED", "review failed"),
]


def _extract_critique_verdict(output_tail: str) -> str | None:
    """Extract the canonical critique verdict string from the output tail."""
    if not output_tail:
        return None
    lower = output_tail.lower()
    for canonical, needle in _CRITIQUE_VERDICT_PATTERNS:
        if needle in lower:
            return canonical
    return None


def _extract_review_verdict(output_tail: str) -> str | None:
    """Extract the canonical review verdict string from the output tail.

    Prefers values carried by an ``<!-- OUTCOME {...} -->`` block when
    available, since the review skill emits structured outcome contracts
    (``status=success|partial|fail``). Falls back to literal string matching.
    """
    if not output_tail:
        return None
    contract = _parse_outcome_contract(output_tail)
    if contract and contract.get("stage") == "REVIEW":
        status = contract.get("status", "")
        artifacts = contract.get("artifacts") or {}
        blockers = artifacts.get("blockers", 0) or 0
        tech_debt = artifacts.get("tech_debt", 0) or 0
        if status == "success" and not blockers and not tech_debt:
            return "APPROVED"
        if status in ("partial", "fail") or blockers or tech_debt:
            return "CHANGES REQUESTED"
    lower = output_tail.lower()
    for canonical, needle in _REVIEW_VERDICT_PATTERNS:
        if needle in lower:
            return canonical
    return None


def _review_counts(output_tail: str) -> tuple[int | None, int | None]:
    """Extract blocker / tech-debt counts from the OUTCOME contract if present."""
    contract = _parse_outcome_contract(output_tail)
    if not contract or contract.get("stage") != "REVIEW":
        return (None, None)
    artifacts = contract.get("artifacts") or {}
    blockers = artifacts.get("blockers")
    tech_debt = artifacts.get("tech_debt")
    try:
        blockers_i = int(blockers) if blockers is not None else None
    except (TypeError, ValueError):
        blockers_i = None
    try:
        tech_debt_i = int(tech_debt) if tech_debt is not None else None
    except (TypeError, ValueError):
        tech_debt_i = None
    return (blockers_i, tech_debt_i)


def _record_verdict_from_output(session, stage: str, output_tail: str) -> None:
    """Best-effort: write the extracted verdict via tools.sdlc_verdict.

    This is the unification path called from ``classify_outcome()``. It is the
    ONLY indirect writer to ``_verdicts`` — the CLI path and this path both
    funnel through ``tools.sdlc_verdict.record_verdict``, which in turn uses
    the optimistic-retry helper. If the verdict cannot be extracted or
    recording fails, this function silently returns. It never raises.
    """
    if stage not in ("CRITIQUE", "REVIEW"):
        return
    if session is None:
        return
    try:
        if stage == "CRITIQUE":
            verdict = _extract_critique_verdict(output_tail)
            if not verdict:
                return
            from tools.sdlc_verdict import record_verdict

            record_verdict(session, "CRITIQUE", verdict)
        else:
            verdict = _extract_review_verdict(output_tail)
            if not verdict:
                return
            blockers, tech_debt = _review_counts(output_tail)
            from tools.sdlc_verdict import record_verdict

            record_verdict(
                session,
                "REVIEW",
                verdict,
                blockers=blockers,
                tech_debt=tech_debt,
            )
    except Exception as e:
        logger.debug(f"_record_verdict_from_output({stage}) failed: {e}")


class PipelineStateMachine:
    """Manages SDLC pipeline stage transitions with ordering enforcement.

    Reads/writes stage_states on the AgentSession. The state machine is
    stateless across requests -- each invocation loads fresh state from
    the session.

    Attributes:
        session: The AgentSession this state machine operates on.
        states: Dict mapping stage name to status string.
        patch_cycle_count: Number of PATCH -> TEST cycles completed.
        critique_cycle_count: Number of CRITIQUE -> PLAN -> CRITIQUE cycles completed.
    """

    def __init__(self, session: AgentSession) -> None:
        """Initialize from an AgentSession.

        Loads stage_states from the session's field. If the field is
        None or empty, initializes all stages to pending with ISSUE
        set to ready.

        Args:
            session: AgentSession instance to read/write state from.
        """
        self.session = session
        self.states: dict[str, str] = {}
        self.patch_cycle_count: int = 0
        self.critique_cycle_count: int = 0

        # Load existing state from session
        raw = getattr(session, "stage_states", None)
        if raw and isinstance(raw, str):
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    self.states = {k: v for k, v in data.items() if k in ALL_STAGES}
                    self.patch_cycle_count = data.get("_patch_cycle_count", 0)
                    self.critique_cycle_count = data.get("_critique_cycle_count", 0)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    f"Invalid stage_states JSON on session "
                    f"{getattr(session, 'session_id', '?')}, initializing defaults"
                )
        elif raw and isinstance(raw, dict):
            self.states = {k: v for k, v in raw.items() if k in ALL_STAGES}
            self.patch_cycle_count = raw.get("_patch_cycle_count", 0)
            self.critique_cycle_count = raw.get("_critique_cycle_count", 0)

        # Initialize defaults for any missing stages
        for stage in ALL_STAGES:
            if stage not in self.states:
                self.states[stage] = "pending"

        # If nothing has started yet, mark ISSUE as ready
        if all(v == "pending" for v in self.states.values()):
            self.states["ISSUE"] = "ready"

    def _save(self) -> None:
        """Persist state back to the session.

        Validates stage_states via the StageStates Pydantic model before
        serializing. Validation errors log a warning but do not crash --
        the data is still saved to avoid losing progress.

        Metadata preservation invariant (regression #1040 blocker 1):
        ``_save()`` is a write path that only knows about ``self.states``
        plus the two cycle counters (``_patch_cycle_count`` and
        ``_critique_cycle_count`` — explicitly re-added below). Any OTHER
        underscore-prefixed metadata key (``_verdicts``, ``_sdlc_dispatches``,
        or any future ``_*`` key) would be silently dropped if we serialized
        ``self.states`` alone. To protect cross-writer invariants — especially
        the verdict recorder in ``tools.sdlc_verdict`` and the dispatch
        recorder in ``agent.sdlc_router.record_dispatch`` — we reload the
        latest raw ``stage_states`` from the session BEFORE writing and merge
        every ``_*`` key we did not manage ourselves. This makes ``_save()``
        a safe participant in the cross-process stage_states write protocol.
        """
        # Validate states before saving
        try:
            validated = StageStates.from_dict(self.states)
            self.states = validated.to_dict()
            # Re-add any missing stages as pending after validation
            for stage in ALL_STAGES:
                if stage not in self.states:
                    self.states[stage] = "pending"
        except Exception as e:
            logger.warning(
                f"StageStates validation failed for session "
                f"{getattr(self.session, 'session_id', '?')}: {e}. Saving anyway."
            )

        # Load any concurrent metadata writes from the live session so we can
        # preserve underscore-prefixed keys we don't own (see invariant in
        # docstring above). This avoids clobbering ``_verdicts`` /
        # ``_sdlc_dispatches`` that the verdict/dispatch recorders wrote
        # between __init__ and this save.
        preserved_metadata = self._load_preserved_metadata()

        data = dict(self.states)
        # Owned metadata keys — re-applied explicitly each save.
        data["_patch_cycle_count"] = self.patch_cycle_count
        data["_critique_cycle_count"] = self.critique_cycle_count
        # Unowned underscore metadata keys — merged back in without
        # overwriting the owned keys above.
        for key, value in preserved_metadata.items():
            if key in ("_patch_cycle_count", "_critique_cycle_count"):
                continue
            data[key] = value

        self.session.stage_states = json.dumps(data)
        try:
            self.session.save()
        except Exception as e:
            logger.warning(
                f"Failed to save stage_states for session "
                f"{getattr(self.session, 'session_id', '?')}: {e}"
            )

    def _load_preserved_metadata(self) -> dict:
        """Return underscore-prefixed metadata keys from the live session.

        ``_save()`` calls this to pick up writes other writers (e.g.
        ``tools.sdlc_verdict.record_verdict``, ``agent.sdlc_router.record_dispatch``)
        may have made between when this state machine was constructed and
        the current save. Returns only ``_*`` keys other than the two cycle
        counters owned by the state machine itself. Never raises.
        """
        try:
            raw = getattr(self.session, "stage_states", None)
            if not raw:
                return {}
            if isinstance(raw, str):
                data = json.loads(raw)
            elif isinstance(raw, dict):
                data = raw
            else:
                return {}
            if not isinstance(data, dict):
                return {}
            return {
                k: v
                for k, v in data.items()
                if k.startswith("_") and k not in ("_patch_cycle_count", "_critique_cycle_count")
            }
        except Exception as e:
            logger.debug(
                f"_load_preserved_metadata: failed on session "
                f"{getattr(self.session, 'session_id', '?')}: {e}"
            )
            return {}

    def _get_predecessors(self, stage: str) -> list[str]:
        """Get stages that must be completed before this stage can start.

        Uses PIPELINE_EDGES to find which stages have edges leading to
        the given stage.
        """
        predecessors = []
        for (src, outcome), dst in PIPELINE_EDGES.items():
            if dst == stage and outcome == "success":
                predecessors.append(src)
        return predecessors

    def _activate_stage(self, stage: str) -> None:
        """Set stage to in_progress, save, and record analytics."""
        self.states[stage] = "in_progress"
        self._save()
        _record_stage_metric("sdlc.stage_started", stage)

    def start_stage(self, stage: str) -> None:
        """Mark a stage as in_progress.

        Validates that at least one predecessor is completed (via success
        edge in PIPELINE_EDGES). ISSUE can always be started. PATCH can
        be started if TEST or REVIEW failed.

        Args:
            stage: Stage name to start.

        Raises:
            ValueError: If stage is invalid or predecessor not completed.
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "in_progress":
            logger.info(f"Stage {stage} already in_progress, no-op")
            return
        if current == "completed":
            # Allow re-entry for cycles (TEST can restart after PATCH)
            logger.info(f"Stage {stage} re-entering from completed (cycle)")

        # ISSUE is always startable (it's the first stage)
        if stage == "ISSUE":
            self._activate_stage(stage)
            return

        # PATCH is startable if TEST or REVIEW is failed/completed
        if stage == "PATCH":
            test_status = self.states.get("TEST", "pending")
            review_status = self.states.get("REVIEW", "pending")
            if test_status in ("failed", "completed") or review_status in ("failed", "completed"):
                self._activate_stage(stage)
                return
            raise ValueError(
                f"Cannot start PATCH: neither TEST ({test_status}) "
                f"nor REVIEW ({review_status}) has completed or failed"
            )

        # For cycle re-entry: PLAN can restart after CRITIQUE fails
        if stage == "PLAN" and self.states.get("CRITIQUE") in ("failed",):
            self._activate_stage(stage)
            return

        # For cycle re-entry: TEST can restart after PATCH completes
        if stage == "TEST" and self.states.get("PATCH") in ("completed", "in_progress"):
            self._activate_stage(stage)
            return

        # Check predecessors
        predecessors = self._get_predecessors(stage)
        if not predecessors:
            # No known predecessors — allow start
            self._activate_stage(stage)
            return

        for pred in predecessors:
            if self.states.get(pred) == "completed":
                self._activate_stage(stage)
                return

        pred_statuses = {p: self.states.get(p, "pending") for p in predecessors}
        raise ValueError(
            f"Cannot start {stage}: no predecessor completed. Predecessors: {pred_statuses}"
        )

    def complete_stage(self, stage: str) -> None:
        """Mark a stage as completed.

        Sets the stage to completed and marks the next stage as ready
        (based on success edge in PIPELINE_EDGES).

        Args:
            stage: Stage name to complete.

        Raises:
            ValueError: If stage is invalid or not in_progress.
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "completed":
            logger.info(f"Stage {stage} already completed, no-op")
            return
        if current != "in_progress" and current != "ready":
            raise ValueError(
                f"Cannot complete stage {stage}: current status is "
                f"{current!r}, expected 'in_progress' or 'ready'"
            )

        self.states[stage] = "completed"

        # Track PATCH cycles
        if stage == "PATCH":
            self.patch_cycle_count += 1

        # Mark next stage as ready
        next_info = get_next_stage(
            stage, "success", self.patch_cycle_count, self.critique_cycle_count
        )
        if next_info:
            next_stage = next_info[0]
            next_current = self.states.get(next_stage, "pending")
            if next_current in ("pending", "failed"):
                self.states[next_stage] = "ready"

        self._save()
        _record_stage_metric("sdlc.stage_completed", stage)
        logger.info(
            f"Stage {stage} completed. "
            f"Patch cycles: {self.patch_cycle_count}. "
            f"Next: {next_info[0] if next_info else 'terminal'}"
        )

    def fail_stage(self, stage: str) -> None:
        """Mark a stage as failed.

        Failed stages can trigger PATCH cycles (for TEST and REVIEW).
        Failing an already-completed stage is a no-op with warning.

        Args:
            stage: Stage name to fail.
        """
        if stage not in ALL_STAGES:
            raise ValueError(f"Invalid stage: {stage!r}. Valid stages: {ALL_STAGES}")

        current = self.states.get(stage, "pending")
        if current == "completed":
            logger.warning(f"Stage {stage} already completed, fail_stage is no-op")
            return

        self.states[stage] = "failed"

        # Track CRITIQUE cycles (incremented on failure since it triggers PLAN revision)
        if stage == "CRITIQUE":
            self.critique_cycle_count += 1

        # Mark next stage based on failure edge
        next_info = get_next_stage(stage, "fail", self.patch_cycle_count, self.critique_cycle_count)
        if next_info:
            next_stage = next_info[0]
            next_current = self.states.get(next_stage, "pending")
            if next_current in ("pending", "completed", "failed"):
                self.states[next_stage] = "ready"

        self._save()
        logger.info(
            f"Stage {stage} failed. Next: {next_info[0] if next_info else 'terminal (escalate)'}"
        )

    def get_display_progress(self) -> dict[str, str]:
        """Return stage statuses for display (excludes PATCH).

        Returns stored state only. Stage completion is exclusively determined
        by PipelineStateMachine stored state — no artifact inference.

        Returns:
            Dict mapping display stage names to their status strings.
            Only includes DISPLAY_STAGES (not PATCH).
        """
        return {stage: self.states.get(stage, "pending") for stage in DISPLAY_STAGES}

    def current_stage(self) -> str | None:
        """Return the stage currently in_progress, or None.

        If multiple stages are in_progress (shouldn't happen normally),
        returns the latest one in pipeline order.
        """
        for stage in reversed(ALL_STAGES):
            if self.states.get(stage) == "in_progress":
                return stage
        return None

    def next_stage(self, outcome: str = "success") -> tuple[str, str] | None:
        """Determine the next stage to transition to.

        Delegates to pipeline_graph.get_next_stage() using the current
        in_progress or last completed stage.

        Args:
            outcome: Result of current stage ("success", "fail", "partial").

        Returns:
            Tuple of (stage_name, skill_command), or None if pipeline
            is complete or should escalate to human.
        """
        current = self.current_stage()
        if current:
            return get_next_stage(
                current, outcome, self.patch_cycle_count, self.critique_cycle_count
            )

        # No stage in_progress — find the last completed stage
        last_completed = None
        for stage in ALL_STAGES:
            if self.states.get(stage) == "completed":
                last_completed = stage

        if last_completed:
            return get_next_stage(
                last_completed, outcome, self.patch_cycle_count, self.critique_cycle_count
            )

        # Nothing started yet — return first stage
        return get_next_stage(None)

    def has_remaining_stages(self) -> bool:
        """Check if any display stages are not yet completed.

        Returns True if pipeline progression should continue.
        Returns False when MERGE is completed or no transitions remain.
        """
        # If MERGE is completed, pipeline is done
        if self.states.get("MERGE") == "completed":
            return False

        # Check if any display stage is not completed
        for stage in DISPLAY_STAGES:
            status = self.states.get(stage, "pending")
            if status != "completed":
                return True

        return False

    def has_failed_stage(self) -> bool:
        """Check if any stage has failed.

        Returns True if any stage is in failed status.
        """
        return any(self.states.get(stage) == "failed" for stage in DISPLAY_STAGES)

    def classify_outcome(
        self,
        stage: str,
        stop_reason: str | None,
        output_tail: str = "",
    ) -> str:
        """Classify a stage's outcome from SDK stop_reason and output patterns.

        Three-tier approach:
        0. OUTCOME contract: structured ``<!-- OUTCOME {...} -->`` block in output.
           If found with a valid status, returns immediately.
        1. stop_reason from SDK: anything other than "end_turn" is a process
           failure (rate_limited, timeout, etc.)
        2. For "end_turn": deterministic tail patterns scoped to the known stage.

        Args:
            stage: The stage that just ran.
            stop_reason: SDK stop reason string.
            output_tail: Last ~500 chars of worker output.

        Returns:
            "success", "fail", "partial", or "ambiguous".
        """
        # Tier 0: OUTCOME contract parsing
        contract = _parse_outcome_contract(output_tail)
        if contract:
            status = contract.get("status", "")
            contract_stage = contract.get("stage", "")
            if contract_stage and contract_stage != stage:
                logger.warning(
                    f"classify_outcome({stage}): OUTCOME contract stage mismatch "
                    f"(expected {stage}, got {contract_stage}) — falling through to Tier 1/2"
                )
            elif status in ("success", "fail", "partial"):
                # Record the verdict for CRITIQUE/REVIEW before returning so
                # structured OUTCOME blocks also populate _verdicts.
                _record_verdict_from_output(self.session, stage, output_tail)
                logger.info(f"classify_outcome({stage}): OUTCOME contract -> {status}")
                return status
            else:
                logger.debug(
                    f"classify_outcome({stage}): OUTCOME contract has unknown status "
                    f"{status!r} — falling through to Tier 1/2"
                )

        # Tier 1: SDK stop_reason
        if stop_reason and stop_reason != "end_turn":
            logger.info(f"classify_outcome({stage}): stop_reason={stop_reason} -> fail")
            return "fail"

        # Tier 2: deterministic output patterns per stage
        tail = output_tail.lower() if output_tail else ""

        if stage == "ISSUE":
            if "issues/" in tail or "issue created" in tail or "issue #" in tail:
                return "success"
        elif stage == "CRITIQUE":
            # Record the verdict before returning so the SDLC router can
            # consume it via `_verdicts["CRITIQUE"]`. This is the unification
            # point: bridge-initiated sessions funnel through the same
            # `tools.sdlc_verdict.record_verdict` writer as the CLI path.
            _record_verdict_from_output(self.session, "CRITIQUE", output_tail)
            if "ready to build" in tail:
                return "success"
            if "needs revision" in tail:
                return "fail"
            if "major rework" in tail:
                # Major rework escalates to human — return ambiguous so caller
                # can inspect and decide (typically escalate rather than auto-loop)
                return "ambiguous"
        elif stage == "PLAN":
            if "docs/plans/" in tail or "plan created" in tail or "plan finalized" in tail:
                return "success"
        elif stage == "BUILD":
            if "pull/" in tail or "pr created" in tail or "pr #" in tail:
                return "success"
            if "outcome" in tail and '"status":"success"' in tail:
                return "success"
        elif stage == "TEST":
            if "passed" in tail and "failed" not in tail:
                return "success"
            if "failed" in tail or "error" in tail:
                return "fail"
        elif stage == "PATCH":
            # PATCH succeeds if it produced commits
            if "commit" in tail or "pushed" in tail:
                return "success"
        elif stage == "REVIEW":
            # Record the verdict before returning (see CRITIQUE above for
            # rationale — unifies bridge and CLI write paths).
            _record_verdict_from_output(self.session, "REVIEW", output_tail)
            if "approved" in tail or "review passed" in tail:
                return "success"
            if "changes requested" in tail or "review failed" in tail:
                return "fail"
        elif stage == "DOCS":
            if "documentation" in tail and ("created" in tail or "updated" in tail):
                return "success"
        elif stage == "MERGE":
            if "merged" in tail:
                return "success"

        return "ambiguous"

    def to_dict(self) -> dict[str, Any]:
        """Serialize full state for debugging/logging."""
        return {
            "states": dict(self.states),
            "patch_cycle_count": self.patch_cycle_count,
            "critique_cycle_count": self.critique_cycle_count,
            "current_stage": self.current_stage(),
            "has_remaining": self.has_remaining_stages(),
        }

    @classmethod
    def derive_from_durable_signals(cls, session) -> dict[str, str]:
        """FALLBACK: Derive pipeline progress from durable artifacts.

        This is a FALLBACK path, not the primary signal source. It is only
        consulted by ``/do-merge`` when ``get_display_progress()`` returns
        an empty/all-``pending`` dict on a cold Redis (fresh machine, eviction,
        cleared Popoto session). The primary path remains Redis-backed
        ``stage_states`` written by the PipelineStateMachine itself.

        Signals consulted (per stage):
        - **PLAN** — plan file exists at ``origin/{branch}:docs/plans/{slug}.md``
          with a ``tracking:`` URL.
        - **BUILD** — ``gh pr list --search "#{issue}" --state open`` returns
          at least one PR whose ``headRefName`` equals ``session/{slug}``.
        - **TEST** — ``gh pr view --json statusCheckRollup`` shows all
          checks passing (no ``FAILURE``/``TIMED_OUT``/``CANCELLED``).
        - **REVIEW** — most recent ``## Review:`` issue comment on the PR
          starts with ``## Review: Approved``. Stale reviews are filtered
          using the commit-SHA filter (comments before the latest commit's
          ``committer_date`` are dropped), consistent with item 2's filter.
        - **DOCS** — **tri-OR derivation**: returns ``completed`` if ANY of:
          (a) ``gh pr diff --name-only`` shows at least one ``docs/`` file,
          (b) every ``- [ ]`` checkbox in the plan's ``## Documentation``
          section is ticked (``- [x]``),
          (c) the latest ``## Review:`` comment body matches
          ``docs (complete|updated|verified|reviewed)`` case-insensitively.
          If none of the three fire, DOCS returns ``pending``.

        **Note on downstream routing**: ``/do-merge`` is a terminal gate,
        NOT a router. When this function returns ``pending`` for a stage,
        the gate prints that state and returns GATES_FAILED; the PM session
        then reads the output and dispatches the appropriate remediation
        skill on its next turn. This is a two-step loop through the PM
        session — this function never dispatches anything.

        **Failure semantics**: Any subprocess error (``gh api``/``git show``
        failure, network error, JSON parse error, missing binaries) is
        caught at the top level and the corresponding stage is recorded as
        ``pending`` — equivalent to cold-Redis behavior. The function never
        raises. A warning is logged for each swallowed error.

        Args:
            session: AgentSession instance. The ``slug`` attribute is the
                sole required field; everything else is read via the
                subprocess helpers.

        Returns:
            Dict mapping DISPLAY_STAGES to one of ``"completed"``,
            ``"pending"``, or ``"failed"``. On complete subprocess failure,
            returns ``{}`` (matches the cold-Redis return shape).
        """
        slug = getattr(session, "slug", None)
        if not slug:
            logger.debug("derive_from_durable_signals: session has no slug")
            return {}

        states: dict[str, str] = {stage: "pending" for stage in DISPLAY_STAGES}
        states["ISSUE"] = "completed"  # Session exists → ISSUE has completed

        branch = f"session/{slug}"
        plan_path = f"docs/plans/{slug}.md"

        try:
            # --- PLAN ----------------------------------------------------
            plan_text = _durable_git_show(f"origin/{branch}:{plan_path}")
            if plan_text is None:
                plan_text = _durable_git_show(f"origin/main:{plan_path}")
            if plan_text and "tracking:" in plan_text:
                states["PLAN"] = "completed"

            # CRITIQUE: we treat CRITIQUE as completed if PLAN is completed
            # and the plan text contains a ``## Critique Results`` section
            # with any content (matches Rule 1 of the PM persona's artifact
            # verification table).
            if states["PLAN"] == "completed" and plan_text:
                if _plan_has_critique_results(plan_text):
                    states["CRITIQUE"] = "completed"

            # --- BUILD: look for an open PR on session/{slug} -------------
            pr_info = _durable_gh_pr_for_branch(branch)
            if pr_info:
                states["BUILD"] = "completed"

            # --- TEST: check statusCheckRollup on the PR -----------------
            latest_commit_date: str | None = None
            if pr_info:
                pr_number = pr_info.get("number")
                check_verdict = _durable_pr_checks_verdict(pr_number)
                if check_verdict == "success":
                    states["TEST"] = "completed"
                elif check_verdict == "failure":
                    states["TEST"] = "failed"
                # "pending"/"unknown" stays as pending

                latest_commit_date = _durable_pr_latest_commit_date(pr_number)

            # --- REVIEW: latest ``## Review:`` comment, SHA-filtered ------
            latest_review_body: str | None = None
            if pr_info:
                latest_review_body = _durable_latest_review_comment(
                    pr_info.get("number"), latest_commit_date
                )
                if latest_review_body:
                    if latest_review_body.startswith("## Review: Approved"):
                        states["REVIEW"] = "completed"
                    elif latest_review_body.startswith("## Review: Changes Requested"):
                        states["REVIEW"] = "failed"

            # --- DOCS: tri-OR derivation ---------------------------------
            # (a) docs/ files in PR diff
            # (b) all ## Documentation checkboxes ticked in plan
            # (c) latest ## Review: comment body mentions docs
            docs_completed = False
            if pr_info:
                if _durable_pr_diff_has_docs(pr_info.get("number")):
                    docs_completed = True

            if not docs_completed and plan_text and _plan_docs_checkboxes_all_ticked(plan_text):
                docs_completed = True

            if not docs_completed and latest_review_body:
                if _review_comment_mentions_docs(latest_review_body):
                    docs_completed = True

            if docs_completed:
                states["DOCS"] = "completed"

        except Exception as exc:  # top-level fail-closed guard
            logger.warning(
                "derive_from_durable_signals: unexpected error deriving signals for slug %s: %s",
                slug,
                exc,
            )
            # Leave any already-populated states in place; the rest stays
            # ``pending`` so the gate treats them as unknown-not-completed.
        return states


# ---------------------------------------------------------------------------
# Durable-signal helpers (item 1 of sdlc-1155)
#
# These helpers are NOT part of the PipelineStateMachine contract — they are
# private subprocess-based readers consulted only from
# ``PipelineStateMachine.derive_from_durable_signals``. Each helper returns a
# plain Python value (or ``None``) and catches every exception internally; the
# caller treats ``None`` as "signal absent" and marks the corresponding stage
# as ``pending``. No function here writes anything to disk or Redis.
# ---------------------------------------------------------------------------


def _durable_run(cmd: list[str], timeout: int = 15) -> str | None:
    """Run a subprocess, return stdout on exit 0, ``None`` on any failure.

    Never raises. All errors (missing binary, non-zero exit, timeout, decode
    failure) are caught and logged at WARNING/DEBUG and result in ``None``.
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        logger.warning("derive_from_durable_signals: binary not found: %s", exc)
        return None
    except subprocess.TimeoutExpired as exc:
        logger.warning("derive_from_durable_signals: timeout running %s: %s", cmd[:2], exc)
        return None
    except Exception as exc:  # defensive catch-all; must never raise upward
        logger.warning("derive_from_durable_signals: unexpected error running %s: %s", cmd[:2], exc)
        return None

    if result.returncode != 0:
        logger.debug(
            "derive_from_durable_signals: %s exited %d (%s)",
            cmd[:2],
            result.returncode,
            (result.stderr or "").strip()[:200],
        )
        return None
    return result.stdout


def _durable_git_show(spec: str) -> str | None:
    """Return the contents of ``git show {spec}`` or ``None``."""
    return _durable_run(["git", "show", spec])


def _durable_gh_pr_for_branch(branch: str) -> dict | None:
    """Return a dict describing the (most recent) PR on ``branch``, or None.

    Reads open PRs first; if no open PR exists, falls back to any state so
    that closed/merged PRs still signal BUILD completion (they demonstrate
    the artifact was produced).
    """
    for state in ("open", "all"):
        out = _durable_run(
            [
                "gh",
                "pr",
                "list",
                "--head",
                branch,
                "--state",
                state,
                "--json",
                "number,headRefName,state",
                "--limit",
                "5",
            ]
        )
        if out is None:
            continue
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, list) and parsed:
            return parsed[0]
    return None


def _durable_pr_checks_verdict(pr_number) -> str:
    """Return ``"success"``/``"failure"``/``"pending"``/``"unknown"``."""
    if pr_number is None:
        return "unknown"
    out = _durable_run(["gh", "pr", "view", str(pr_number), "--json", "statusCheckRollup"])
    if out is None:
        return "unknown"
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        return "unknown"
    checks = parsed.get("statusCheckRollup") or []
    if not checks:
        # PRs without CI configured default to success (no checks to fail).
        return "success"
    seen_failure = False
    seen_pending = False
    for check in checks:
        conclusion = (check.get("conclusion") or "").upper()
        state = (check.get("state") or "").upper()
        if conclusion in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED"}:
            seen_failure = True
        elif conclusion in {"", "PENDING", "IN_PROGRESS", "QUEUED"} and state in {
            "",
            "PENDING",
            "IN_PROGRESS",
            "QUEUED",
        }:
            # No conclusion yet → still running.
            seen_pending = True
    if seen_failure:
        return "failure"
    if seen_pending:
        return "pending"
    return "success"


def _durable_pr_latest_commit_date(pr_number) -> str | None:
    """Return the ISO-8601 ``committer.date`` of the PR's latest commit, or None."""
    if pr_number is None:
        return None
    out = _durable_run(
        [
            "gh",
            "api",
            f"repos/:owner/:repo/pulls/{pr_number}/commits",
            "--jq",
            ".[-1].commit.committer.date",
        ]
    )
    if out is None:
        return None
    date = out.strip()
    return date or None


def _durable_latest_review_comment(pr_number, latest_commit_date: str | None) -> str | None:
    """Return the body of the most recent ``## Review:`` issue comment.

    If ``latest_commit_date`` is provided, comments with ``created_at`` strictly
    older than that are dropped (matches item 2's commit-SHA filter semantics
    so the durable fallback and the gate's comment check agree). Exact-time
    ties are kept (``>=`` comparison).
    """
    if pr_number is None:
        return None
    out = _durable_run(
        [
            "gh",
            "api",
            f"repos/:owner/:repo/issues/{pr_number}/comments",
            "--paginate",
            "--jq",
            ".[] | {body: .body, created_at: .created_at}",
        ]
    )
    if out is None:
        return None
    candidate_body: str | None = None
    for raw_line in out.splitlines():
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        body = entry.get("body") or ""
        if not body.startswith("## Review:"):
            continue
        created_at = entry.get("created_at") or ""
        if latest_commit_date and created_at and created_at < latest_commit_date:
            continue  # stale review, drop
        candidate_body = body  # gh api returns in ASC order → last kept = newest
    return candidate_body


def _durable_pr_diff_has_docs(pr_number) -> bool:
    """Return True if the PR diff touches at least one ``docs/`` file."""
    if pr_number is None:
        return False
    out = _durable_run(["gh", "pr", "diff", str(pr_number), "--name-only"])
    if out is None:
        return False
    for line in out.splitlines():
        if line.strip().startswith("docs/"):
            return True
    return False


def _plan_has_critique_results(plan_text: str) -> bool:
    """Return True if the plan contains a non-empty ``## Critique Results`` section."""
    match = re.search(r"(?m)^##\s+Critique Results\s*$", plan_text)
    if not match:
        return False
    tail = plan_text[match.end() :]
    # Non-empty = some non-whitespace content before next ## heading
    next_heading = re.search(r"(?m)^##\s+\S", tail)
    body = tail[: next_heading.start()] if next_heading else tail
    return bool(body.strip())


def _plan_docs_checkboxes_all_ticked(plan_text: str) -> bool:
    """Return True if every ``- [ ]`` in the plan's ``## Documentation`` section is ticked."""
    match = re.search(r"(?m)^##\s+Documentation\s*$", plan_text)
    if not match:
        return False
    tail = plan_text[match.end() :]
    next_heading = re.search(r"(?m)^##\s+\S", tail)
    body = tail[: next_heading.start()] if next_heading else tail
    # Any unticked checkbox → False
    if re.search(r"^\s*-\s+\[\s\]\s", body, flags=re.MULTILINE):
        return False
    # Must have at least one ticked checkbox to count as "all ticked"
    return bool(re.search(r"^\s*-\s+\[x\]\s", body, flags=re.MULTILINE | re.IGNORECASE))


_DOCS_REVIEW_ACK_RE = re.compile(
    r"docs\s+(complete|completed|updated|verified|reviewed)",
    re.IGNORECASE,
)


def _review_comment_mentions_docs(comment_body: str) -> bool:
    """Return True if the ``## Review:`` comment body acknowledges docs.

    Matches ``docs (complete|completed|updated|verified|reviewed)``
    case-insensitively (per Open Question #5 resolution).
    """
    if not comment_body:
        return False
    return bool(_DOCS_REVIEW_ACK_RE.search(comment_body))
