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
Each ChatSession run creates a fresh state machine from the session.

Usage:
    from bridge.pipeline_state import PipelineStateMachine

    sm = PipelineStateMachine(session)
    sm.start_stage("BUILD")      # validates PLAN is completed
    sm.complete_stage("BUILD")   # marks BUILD completed, TEST ready
    sm.get_display_progress()    # returns {stage: status} for display
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, field_validator

from bridge.pipeline_graph import (
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

        data = dict(self.states)
        data["_patch_cycle_count"] = self.patch_cycle_count
        data["_critique_cycle_count"] = self.critique_cycle_count
        self.session.stage_states = json.dumps(data)
        try:
            self.session.save()
        except Exception as e:
            logger.warning(
                f"Failed to save stage_states for session "
                f"{getattr(self.session, 'session_id', '?')}: {e}"
            )

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
            self.states[stage] = "in_progress"
            self._save()
            return

        # PATCH is startable if TEST or REVIEW is failed/completed
        if stage == "PATCH":
            test_status = self.states.get("TEST", "pending")
            review_status = self.states.get("REVIEW", "pending")
            if test_status in ("failed", "completed") or review_status in ("failed", "completed"):
                self.states[stage] = "in_progress"
                self._save()
                return
            raise ValueError(
                f"Cannot start PATCH: neither TEST ({test_status}) "
                f"nor REVIEW ({review_status}) has completed or failed"
            )

        # For cycle re-entry: PLAN can restart after CRITIQUE fails
        if stage == "PLAN" and self.states.get("CRITIQUE") in ("failed",):
            self.states[stage] = "in_progress"
            self._save()
            return

        # For cycle re-entry: TEST can restart after PATCH completes
        if stage == "TEST" and self.states.get("PATCH") in ("completed", "in_progress"):
            self.states[stage] = "in_progress"
            self._save()
            return

        # Check predecessors
        predecessors = self._get_predecessors(stage)
        if not predecessors:
            # No known predecessors — allow start
            self.states[stage] = "in_progress"
            self._save()
            return

        for pred in predecessors:
            if self.states.get(pred) == "completed":
                self.states[stage] = "in_progress"
                self._save()
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

    def get_display_progress(self, slug: str | None = None) -> dict[str, str]:
        """Return stage statuses for display (excludes PATCH).

        When slug is provided, fills in "pending"/"ready" gaps by checking
        observable artifacts (plan file, PR existence, review status, etc.).
        Stored state that is not "pending"/"ready" always takes precedence
        over inferred state.

        Args:
            slug: Optional work item slug for artifact-based inference.
                  When None, returns stored state only (backward compatible).

        Returns:
            Dict mapping display stage names to their status strings.
            Only includes DISPLAY_STAGES (not PATCH).
        """
        stored = {stage: self.states.get(stage, "pending") for stage in DISPLAY_STAGES}

        if not slug:
            return stored

        # Merge inferred state into gaps
        inferred = self._infer_stage_from_artifacts(slug)
        for stage in DISPLAY_STAGES:
            if stored[stage] in ("pending", "ready") and stage in inferred:
                stored[stage] = inferred[stage]

        return stored

    def _infer_stage_from_artifacts(self, slug: str) -> dict[str, str]:
        """Infer stage statuses from observable artifacts.

        Checks filesystem and GitHub for evidence that stages completed:
        - ISSUE/PLAN: plan file exists at docs/plans/{slug}.md
        - CRITIQUE: plan frontmatter has status: Ready
        - BUILD/TEST/REVIEW/DOCS: single gh pr view call

        All checks are wrapped in try/except -- failures return an empty
        dict rather than crashing.

        Args:
            slug: Work item slug (e.g., "implicit-pipeline-tracking").

        Returns:
            Dict mapping stage names to inferred status strings.
            Only contains stages where inference found evidence.
        """
        inferred: dict[str, str] = {}

        # --- Filesystem checks (ISSUE, PLAN, CRITIQUE) ---
        try:
            plan_path = Path(f"docs/plans/{slug}.md")
            if plan_path.exists():
                inferred["PLAN"] = "completed"
                # If a plan exists, ISSUE is implicitly done
                inferred["ISSUE"] = "completed"

                # Check for critique completion via frontmatter
                try:
                    plan_text = plan_path.read_text()
                    # Check frontmatter status: Ready
                    if "status: Ready" in plan_text or "status: ready" in plan_text:
                        inferred["CRITIQUE"] = "completed"
                except Exception:
                    pass
        except Exception:
            pass

        # --- GitHub checks (BUILD, TEST, REVIEW, DOCS) via single gh call ---
        try:
            result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "view",
                    f"session/{slug}",
                    "--json",
                    "number,reviewDecision,state,statusCheckRollup,files",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                pr_data = json.loads(result.stdout)

                # BUILD: PR exists
                if pr_data.get("number"):
                    inferred["BUILD"] = "completed"

                # TEST: check statusCheckRollup for passing checks
                checks = pr_data.get("statusCheckRollup") or []
                for check in checks:
                    context = (check.get("context") or check.get("name") or "").lower()
                    conclusion = (check.get("conclusion") or check.get("status") or "").upper()
                    if ("test" in context or "ci" in context) and conclusion == "SUCCESS":
                        inferred["TEST"] = "completed"
                        break

                # REVIEW: reviewDecision indicates review happened
                review_decision = (pr_data.get("reviewDecision") or "").upper()
                if review_decision in ("APPROVED", "CHANGES_REQUESTED"):
                    inferred["REVIEW"] = "completed"

                # DOCS: files array contains docs/ paths
                files = pr_data.get("files") or []
                for f in files:
                    file_path = f.get("path") or ""
                    if file_path.startswith("docs/"):
                        inferred["DOCS"] = "completed"
                        break

                # MERGE: PR state is MERGED
                if (pr_data.get("state") or "").upper() == "MERGED":
                    inferred["MERGE"] = "completed"
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            pass
        except Exception:
            pass

        return inferred

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

        Two-tier approach:
        1. stop_reason from SDK: anything other than "end_turn" is a process
           failure (rate_limited, timeout, etc.)
        2. For "end_turn": deterministic tail patterns scoped to the known stage.

        Args:
            stage: The stage that just ran.
            stop_reason: SDK stop reason string.
            output_tail: Last ~500 chars of worker output.

        Returns:
            "success", "fail", or "ambiguous".
        """
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
