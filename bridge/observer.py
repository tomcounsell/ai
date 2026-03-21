"""Observer — deterministic steer/deliver router for SDLC pipeline.

Replaces the original LLM-based Observer with fully deterministic logic.
Delegates stage tracking to PipelineStateMachine and makes binary
steer/deliver decisions based on session state.

Rules:
1. If stop_reason is rate_limited/timeout: steer with backoff
2. If SDLC with remaining stages and no blockers: steer to next stage
3. If output needs human input (open questions, fatal errors): deliver
4. If all stages complete or no remaining stages: deliver
5. If ambiguous: deliver to human (no LLM fallback)

The Observer's role is absorbed into ChatSession orchestration in the
new architecture. This module provides the deterministic routing logic
that ChatSession uses to decide steer vs deliver.
"""

import logging
import re
import time
from typing import Any

from bridge.pipeline_state import PipelineStateMachine
from bridge.summarizer import extract_artifacts
from models.agent_session import AgentSession
from monitoring.telemetry import record_decision, record_interjection

logger = logging.getLogger(__name__)


# Heuristic patterns that signal the worker needs human input.
_HUMAN_INPUT_PATTERNS = [
    re.compile(r"##\s*Open\s+Questions", re.IGNORECASE),
    re.compile(
        r"(?:question|decision|input)\s+(?:for|from)\s+"
        r"(?:tom|the\s+(?:pm|architect|human))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:Should I|Should we|Do you want|Would you prefer)", re.IGNORECASE),
    re.compile(r"(?:should\s+I|your\s+(?:call|input|decision))", re.IGNORECASE),
    re.compile(r"(?:FATAL|unrecoverable|cannot\s+proceed)", re.IGNORECASE),
    re.compile(
        r"(?:API\s+key\s+has\s+been\s+(?:revoked|disabled|expired))",
        re.IGNORECASE,
    ),
    re.compile(r"(?:nothing\s+I\s+can\s+do|requires?\s+(?:a\s+)?human)", re.IGNORECASE),
    re.compile(r"(?:Option\s+[A-C]\))", re.IGNORECASE),
    re.compile(r"(?:I(?:'d| would) rather get your input)", re.IGNORECASE),
]


def _output_needs_human_input(text: str) -> bool:
    """Check if worker output contains signals that human input is needed."""
    return any(p.search(text) for p in _HUMAN_INPUT_PATTERNS)


def _build_sdlc_context(session: AgentSession) -> dict[str, str]:
    """Build resolved SDLC context variables from session fields.

    Returns a dict of variable name -> value for use in coaching messages.
    Only includes variables that have non-None, non-empty values.
    """
    import os

    from utils.github_patterns import ISSUE_NUMBER_RE as _ISSUE_NUMBER_RE
    from utils.github_patterns import PR_NUMBER_RE as _PR_NUMBER_RE

    ctx: dict[str, str] = {}

    pr_url = getattr(session, "pr_url", None)
    if isinstance(pr_url, str) and pr_url:
        pr_match = _PR_NUMBER_RE.search(pr_url)
        if pr_match:
            ctx["SDLC_PR_NUMBER"] = pr_match.group(1)

    branch = getattr(session, "branch_name", None)
    if isinstance(branch, str) and branch:
        ctx["SDLC_PR_BRANCH"] = branch

    slug = getattr(session, "slug", None) or getattr(session, "work_item_slug", None)
    if isinstance(slug, str) and slug:
        ctx["SDLC_SLUG"] = slug

    plan_url = getattr(session, "plan_url", None)
    if isinstance(plan_url, str) and plan_url:
        if "docs/plans/" in plan_url:
            ctx["SDLC_PLAN_PATH"] = "docs/plans/" + plan_url.split("docs/plans/")[-1]
        else:
            ctx["SDLC_PLAN_PATH"] = plan_url

    issue_url = getattr(session, "issue_url", None)
    if isinstance(issue_url, str) and issue_url:
        issue_match = _ISSUE_NUMBER_RE.search(issue_url)
        if issue_match:
            ctx["SDLC_ISSUE_NUMBER"] = issue_match.group(1)

    gh_repo = os.environ.get("GH_REPO")
    if gh_repo:
        ctx["SDLC_REPO"] = gh_repo

    return ctx


def _construct_canonical_url(url: str | None, gh_repo: str | None) -> str | None:
    """Construct a canonical GitHub URL from a worker-provided URL.

    Extracts the issue or PR number from the URL and constructs the canonical
    URL using the configured GH_REPO, preventing wrong-repo URLs.
    """
    from utils.github_patterns import ISSUE_NUMBER_RE as _ISSUE_NUMBER_RE
    from utils.github_patterns import PR_NUMBER_RE as _PR_NUMBER_RE

    if not url or not isinstance(url, str):
        return None

    url = url.strip()
    if not url:
        return None

    if not gh_repo:
        logger.warning(
            f"Cannot construct canonical URL: GH_REPO not configured. Original URL discarded: {url}"
        )
        return None

    pr_match = _PR_NUMBER_RE.search(url)
    if pr_match:
        number = pr_match.group(1)
        return f"https://github.com/{gh_repo}/pull/{number}"

    issue_match = _ISSUE_NUMBER_RE.search(url)
    if issue_match:
        number = issue_match.group(1)
        return f"https://github.com/{gh_repo}/issues/{number}"

    logger.warning(f"Cannot extract issue/PR number from URL: {url}. URL discarded.")
    return None


class Observer:
    """Deterministic Observer that makes steer/deliver routing decisions.

    No LLM calls. Uses PipelineStateMachine for stage tracking and
    applies deterministic rules to decide next action.

    If the rules cannot determine an action, delivers to human.

    Args:
        session: The AgentSession for this pipeline run
        worker_output: The raw text output from the worker agent
        auto_continue_count: Current auto-continue count for this session
        send_cb: Async callback to send messages to Telegram
        enqueue_fn: Async function to enqueue a continuation job
        stop_reason: SDK stop reason for the worker (end_turn, rate_limited, etc.)
        model: Ignored (kept for backward compatibility)
    """

    def __init__(
        self,
        session: AgentSession,
        worker_output: str,
        auto_continue_count: int,
        send_cb,
        enqueue_fn,
        *,
        stop_reason: str | None = None,
        model: str | None = None,
    ):
        self.session = session
        self.worker_output = worker_output
        self.auto_continue_count = auto_continue_count
        self.send_cb = send_cb
        self.enqueue_fn = enqueue_fn
        self.stop_reason = stop_reason
        self._log_prefix = (
            f"[{session.correlation_id}]"
            if getattr(session, "correlation_id", None)
            else "[observer]"
        )
        # Initialize state machine for SDLC sessions
        self._state_machine: PipelineStateMachine | None = None
        if session.is_sdlc:
            try:
                self._state_machine = PipelineStateMachine(session)
            except Exception as e:
                logger.warning(f"{self._log_prefix} State machine init failed: {e}")

    async def run(self) -> dict[str, Any]:
        """Execute deterministic routing and return the decision.

        Returns:
            Dict with keys:
            - action: "steer" | "deliver"
            - coaching_message: str (if action is "steer")
            - reason: str (if action is "deliver")
            - resolved_stage: str | None
            - stage_outcome: str | None
            - next_stage: str | None
        """
        is_sdlc = self.session.is_sdlc
        sm = self._state_machine
        has_remaining = sm.has_remaining_stages() if sm else False
        has_failed = sm.has_failed_stage() if sm else False
        current = sm.current_stage() if sm else None
        cid = getattr(self.session, "correlation_id", None) or "unknown"

        logger.info(
            f"{self._log_prefix} Session {self.session.session_id}: "
            f"is_sdlc={is_sdlc}, auto_continue={self.auto_continue_count}, "
            f"remaining_stages={has_remaining}, current_stage={current}, "
            f"stop_reason={self.stop_reason}"
        )

        # Clear queued steering messages and log
        queued = self.session.pop_steering_messages()
        if queued:
            logger.info(
                f"{self._log_prefix} INTERJECTION session={self.session.session_id} "
                f"correlation={cid} count={len(queued)} action=cleared"
            )
            record_interjection(self.session.session_id, cid, len(queued), "cleared")

        base: dict[str, Any] = {
            "resolved_stage": None,
            "stage_outcome": None,
            "next_stage": None,
        }

        # Rule 1: Handle non-end_turn stop reasons
        if self.stop_reason and self.stop_reason not in ("end_turn", None):
            if self.stop_reason == "rate_limited":
                logger.warning(
                    f"{self._log_prefix} Worker stopped: rate_limited — steering with backoff"
                )
                record_decision(self.session.session_id, cid, "steer", "stop_reason: rate_limited")
                return {
                    "action": "steer",
                    "coaching_message": (
                        "Rate limited by the API. Wait briefly, then resume "
                        "where you left off. Do not restart from scratch."
                    ),
                    "stop_reason": self.stop_reason,
                    **base,
                    "next_stage": current,
                }

            if self.stop_reason == "timeout":
                logger.warning(f"{self._log_prefix} Worker stopped: timeout — delivering")
                record_decision(self.session.session_id, cid, "deliver", "stop_reason: timeout")
                return {
                    "action": "deliver",
                    "reason": "Session timed out",
                    "stop_reason": self.stop_reason,
                    **base,
                }

            # Unknown stop reason — deliver to human
            logger.info(f"{self._log_prefix} Unknown stop_reason={self.stop_reason} — delivering")
            record_decision(
                self.session.session_id, cid, "deliver", f"unknown_stop: {self.stop_reason}"
            )
            return {
                "action": "deliver",
                "reason": f"Worker stopped with reason: {self.stop_reason}",
                **base,
            }

        # Rule 2: Non-SDLC jobs always deliver
        if not is_sdlc:
            record_decision(self.session.session_id, cid, "deliver", "non-sdlc")
            return {"action": "deliver", "reason": "Non-SDLC job complete", **base}

        # Rule 3: State machine outcome classification
        resolved_stage = None
        stage_outcome = None
        next_stage_name = None
        next_skill = None

        if sm and current:
            stage_outcome = sm.classify_outcome(
                current,
                self.stop_reason,
                self.worker_output[-500:] if self.worker_output else "",
            )
            if stage_outcome in ("success", "fail"):
                resolved_stage = current
                next_info = sm.next_stage(stage_outcome)
                if next_info:
                    next_stage_name, next_skill = next_info

        base["resolved_stage"] = resolved_stage
        base["stage_outcome"] = stage_outcome

        # Rule 4: Output needs human input — deliver
        needs_human = _output_needs_human_input(self.worker_output)
        if needs_human:
            logger.info(f"{self._log_prefix} Output needs human input — delivering")
            record_decision(self.session.session_id, cid, "deliver", "needs_human_input")
            return {"action": "deliver", "reason": "Worker needs human input", **base}

        # Rule 5: Failed stage — deliver
        if has_failed:
            logger.info(f"{self._log_prefix} Pipeline has failed stage — delivering")
            record_decision(self.session.session_id, cid, "deliver", "stage_failed")
            return {"action": "deliver", "reason": "Pipeline stage failed", **base}

        # Rule 6: SDLC with remaining stages — steer to next
        if has_remaining:
            if next_stage_name and next_skill:
                stage_name, skill_cmd = next_stage_name, next_skill
            elif sm:
                next_info = sm.next_stage()
                if next_info:
                    stage_name, skill_cmd = next_info
                else:
                    stage_name, skill_cmd = None, None
            else:
                stage_name, skill_cmd = None, None

            if stage_name and skill_cmd:
                coaching = (
                    f"Pipeline has remaining stages. Next: {stage_name}. "
                    f"Continue with {skill_cmd}. "
                    f"If you encounter a critical blocker requiring human input, "
                    f"state it clearly. Otherwise, press forward."
                )

                # Append SDLC context if available
                sdlc_ctx = _build_sdlc_context(self.session)
                if sdlc_ctx:
                    ctx_str = ", ".join(f"{k}={v}" for k, v in sdlc_ctx.items())
                    coaching += f"\nContext: {ctx_str}"

                logger.info(
                    f"{self._log_prefix} Deterministic: steer to {stage_name} ({skill_cmd})"
                )
                record_decision(self.session.session_id, cid, "steer", f"next_stage: {stage_name}")
                return {
                    "action": "steer",
                    "coaching_message": coaching,
                    "deterministic_guard": True,
                    "next_stage": stage_name,
                    **{k: v for k, v in base.items() if k != "next_stage"},
                }

        # Rule 7: All stages complete or no remaining — deliver
        logger.info(f"{self._log_prefix} Pipeline complete or no remaining stages — delivering")
        record_decision(self.session.session_id, cid, "deliver", "pipeline_complete")
        return {
            "action": "deliver",
            "reason": "Pipeline complete or no remaining stages",
            **base,
        }


# === Circuit breaker state (simplified — no LLM means fewer failure modes) ===
# Kept for backward compatibility with callers that check these.


def observer_record_success(session_id: str) -> None:
    """No-op — deterministic observer doesn't fail like LLM calls."""
    pass


def clear_observer_state(session_id: str) -> None:
    """No-op — no circuit breaker state to clear."""
    pass


def observer_record_failure(session_id: str) -> dict[str, Any]:
    """No-op — deterministic observer doesn't have retryable failures."""
    return {
        "failure_count": 0,
        "should_retry": False,
        "retry_after": 0,
        "should_escalate": False,
    }


def get_observer_failure_count(session_id: str) -> int:
    """Always returns 0 — deterministic observer doesn't track failures."""
    return 0
