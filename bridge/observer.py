"""Observer Agent — steer/deliver classifier for SDLC pipeline.

Simplified from the original inference-based Observer. Now delegates all
stage tracking to PipelineStateMachine (bridge/pipeline_state.py) and
focuses solely on the steer/deliver decision.

The Observer:
1. Reads the AgentSession (stage_states, links, history, queued messages)
2. Uses the state machine to determine pipeline state
3. Decides: steer the agent to the next pipeline stage, OR deliver to Telegram
4. Updates the session with any extracted artifacts (context_summary, expectations)

Fallback: if the Observer errors, raw worker output is delivered to Telegram.
This ensures the pipeline never silently drops output.
"""

import json
import logging
import os
import re
import time
from typing import Any

import anthropic

from agent.job_queue import MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC
from bridge.pipeline_state import PipelineStateMachine
from bridge.summarizer import extract_artifacts
from config.models import SONNET
from models.agent_session import AgentSession
from monitoring.telemetry import record_decision, record_interjection, record_tool_use
from utils.api_keys import get_anthropic_api_key
from utils.github_patterns import ISSUE_NUMBER_RE as _ISSUE_NUMBER_RE
from utils.github_patterns import PR_NUMBER_RE as _PR_NUMBER_RE

logger = logging.getLogger(__name__)

# === Observer Circuit Breaker ===
# Tracks consecutive failures per session to implement escalating backoff.
# Retryable errors (API/outage) get exponential backoff before retry.
# Non-retryable errors (import, config, logic bugs) escalate immediately.
# Counters reset on success.

# {session_id: consecutive_failure_count}
_observer_failure_counts: dict[str, int] = {}
# {session_id: timestamp of last retry attempt}
_observer_last_retry: dict[str, float] = {}

# Backoff schedule: 30s, 60s, 120s, 240s, 480s
OBSERVER_BACKOFF_BASE = 30
OBSERVER_BACKOFF_MAX = 480
OBSERVER_MAX_RETRIES = 5


def _classify_observer_error(error: Exception) -> str:
    """Classify an observer error as retryable or non-retryable.

    Returns:
        'retryable' for API/outage errors that may resolve on their own.
        'non_retryable' for errors requiring human intervention.
    """
    error_str = str(error).lower()
    error_type = type(error).__name__

    # API errors that are typically transient
    retryable_patterns = [
        "overloaded",
        "rate_limit",
        "rate limit",
        "timeout",
        "timed out",
        "connection",
        "503",
        "502",
        "500",
        "529",
        "server error",
        "temporarily unavailable",
        "service unavailable",
    ]
    retryable_types = [
        "APIStatusError",
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
        "ConnectionError",
        "TimeoutError",
    ]

    if error_type in retryable_types:
        return "retryable"
    if any(p in error_str for p in retryable_patterns):
        return "retryable"

    # Everything else is non-retryable (import errors, config issues, logic bugs)
    return "non_retryable"


def _compute_observer_backoff(failure_count: int) -> float:
    """Compute backoff delay for observer retries.

    Returns delay in seconds: 30, 60, 120, 240, 480 (capped).
    """
    delay = OBSERVER_BACKOFF_BASE * (2 ** (failure_count - 1))
    return min(delay, OBSERVER_BACKOFF_MAX)


def observer_record_success(session_id: str) -> None:
    """Reset circuit breaker counters on successful observer run."""
    _observer_failure_counts.pop(session_id, None)
    _observer_last_retry.pop(session_id, None)


def clear_observer_state(session_id: str) -> None:
    """Remove all circuit breaker state for a completed/abandoned session.

    Should be called when a session reaches a terminal state to prevent
    memory leaks from accumulated failure counts and retry timestamps.
    """
    _observer_failure_counts.pop(session_id, None)
    _observer_last_retry.pop(session_id, None)


def observer_record_failure(session_id: str) -> dict[str, Any]:
    """Record a failure and return circuit breaker state.

    Returns:
        Dict with keys: failure_count, should_retry, retry_after, should_escalate
    """
    count = _observer_failure_counts.get(session_id, 0) + 1
    _observer_failure_counts[session_id] = count
    _observer_last_retry[session_id] = time.time()

    if count >= OBSERVER_MAX_RETRIES:
        return {
            "failure_count": count,
            "should_retry": False,
            "retry_after": 0,
            "should_escalate": True,
        }

    return {
        "failure_count": count,
        "should_retry": True,
        "retry_after": _compute_observer_backoff(count),
        "should_escalate": False,
    }


def get_observer_failure_count(session_id: str) -> int:
    """Get the current failure count for a session's observer."""
    return _observer_failure_counts.get(session_id, 0)


def _build_sdlc_context(session: AgentSession) -> dict[str, str]:
    """Build resolved SDLC context variables from session fields.

    Returns a dict of variable name -> value for use in Observer coaching
    messages. Only includes variables that have non-None, non-empty values.
    """
    ctx: dict[str, str] = {}

    pr_url = getattr(session, "pr_url", None)
    if isinstance(pr_url, str) and pr_url:
        pr_match = _PR_NUMBER_RE.search(pr_url)
        if pr_match:
            ctx["SDLC_PR_NUMBER"] = pr_match.group(1)

    branch = getattr(session, "branch_name", None)
    if isinstance(branch, str) and branch:
        ctx["SDLC_PR_BRANCH"] = branch

    slug = getattr(session, "work_item_slug", None)
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


# Maximum tool-use iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 5


def _build_observer_system_prompt() -> str:
    """Build the Observer system prompt with principal context injected.

    Import of load_principal_context is guarded against ImportError —
    if the import fails (circular import or module not loaded), the
    prompt is built without principal context and a warning is logged.
    """
    principal_block = ""
    try:
        from agent.sdk_client import load_principal_context

        principal = load_principal_context(condensed=False)
        if principal:
            principal_block = (
                "\n## Principal Context (Supervisor's Strategic Priorities)\n\n"
                "The following is the supervisor's operating context. Use it for "
                "prioritization, scoping, and escalation decisions.\n\n"
                f"{principal}\n\n---\n"
            )
    except ImportError:
        logger.warning(
            "[observer] Failed to import load_principal_context — "
            "building prompt without principal context"
        )
    except Exception as e:
        logger.warning(
            "[observer] Error loading principal context: %s — "
            "building prompt without principal context",
            e,
        )

    return (
        "You are the Observer Agent for an autonomous SDLC pipeline. Your job is to decide\n"
        "what happens when the worker agent stops producing output.\n"
        f"{principal_block}\n"
        "You have access to the full AgentSession state and must make one of two decisions:\n"
        "1. STEER: Send the worker back to work on the next pipeline stage\n"
        "2. DELIVER: Send the output to the human on Telegram\n\n" + OBSERVER_SYSTEM_PROMPT_BODY
    )


OBSERVER_SYSTEM_PROMPT_BODY = """\
## Pipeline: ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
Cycles: TEST(fail) -> PATCH -> TEST, REVIEW(fail) -> PATCH -> TEST -> REVIEW

## STEER when: stages remain incomplete, worker paused with status update, \
worker finished a stage and needs the next one.
## DELIVER when: all stages complete, worker asks a genuine question, \
blocker needs human, error occurred, non-SDLC job, final completion with evidence.

## Tools: call read_session first, then exactly ONE of enqueue_continuation \
or deliver_to_telegram. Use update_session to persist context_summary/expectations.

## Coaching: acknowledge progress, reference the next /do-* skill, close with \
what success looks like. Include sdlc_context values from read_session when available. \
Never send bare "continue" or vague urgency.
"""


def _build_tools() -> list[dict]:
    """Build the tool definitions for the Observer agent."""
    return [
        {
            "name": "read_session",
            "description": "Read current session state (stages, links, history). Call first.",
            "input_schema": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "update_session",
            "description": "Persist context_summary or expectations after your decision.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "context_summary": {
                        "type": ["string", "null"],
                        "description": "Session summary",
                    },
                    "expectations": {
                        "type": ["string", "null"],
                        "description": "What agent needs from human",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "enqueue_continuation",
            "description": "Steer the worker back to work with a coaching message.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "coaching_message": {
                        "type": "string",
                        "description": "Instruction for the worker",
                    },
                },
                "required": ["coaching_message"],
            },
        },
        {
            "name": "deliver_to_telegram",
            "description": "Deliver output to Telegram. Curate via message_for_user.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Internal reason (not sent to user)",
                    },
                    "message_for_user": {
                        "type": "string",
                        "description": "Curated message for user (optional)",
                    },
                },
                "required": ["reason"],
            },
        },
    ]


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


class Observer:
    """Observer Agent that makes routing decisions with full session context.

    Uses PipelineStateMachine for stage tracking instead of parsing
    transcripts. Focuses on the binary steer/deliver decision.

    Args:
        session: The AgentSession for this pipeline run
        worker_output: The raw text output from the worker agent
        auto_continue_count: Current auto-continue count for this session
        send_cb: Async callback to send messages to Telegram
        enqueue_fn: Async function to enqueue a continuation job
        stop_reason: SDK stop reason for the worker (end_turn, budget_exceeded, etc.)
        model: Override the default model for the Observer LLM
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
        self.model = model or SONNET
        self._decision_made = False
        self._action_taken: str | None = None
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

    def _handle_read_session(self) -> dict[str, Any]:
        """Tool handler: read the current session state."""
        # Use state machine for stage progress when available
        if self._state_machine:
            progress = self._state_machine.get_display_progress()
            has_remaining = self._state_machine.has_remaining_stages()
            has_failed = self._state_machine.has_failed_stage()
            current_stage = self._state_machine.current_stage()
            next_info = self._state_machine.next_stage()
        else:
            progress = self.session.get_stage_progress()
            has_remaining = self.session.has_remaining_stages()
            has_failed = self.session.has_failed_stage()
            current_stage = None
            next_info = None

        links = self.session.get_links()
        history = self.session.get_history_list()
        # Peek at queued messages without clearing
        raw = self.session.queued_steering_messages
        queued = list(raw) if isinstance(raw, list) else []
        if queued:
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.info(
                f"{self._log_prefix} INTERJECTION session={self.session.session_id} "
                f"correlation={cid} count={len(queued)} action=read"
            )
            record_interjection(self.session.session_id, cid, len(queued), "read")
        is_sdlc = self.session.is_sdlc

        # Extract artifacts from worker output
        artifacts = extract_artifacts(self.worker_output)

        # Build resolved SDLC context vars
        sdlc_context = _build_sdlc_context(self.session)

        result = {
            "session_id": self.session.session_id,
            "correlation_id": getattr(self.session, "correlation_id", None),
            "is_sdlc": is_sdlc,
            "classification_type": self.session.classification_type,
            "stage_progress": progress,
            "links": links,
            "sdlc_context": sdlc_context,
            "history": history[-10:],
            "queued_steering_messages": queued,
            "auto_continue_count": self.auto_continue_count,
            "max_auto_continues": MAX_AUTO_CONTINUES_SDLC if is_sdlc else MAX_AUTO_CONTINUES,
            "has_remaining_stages": has_remaining,
            "has_failed_stage": has_failed,
            "current_stage": current_stage,
            "next_stage": next_info[0] if next_info else None,
            "next_skill": next_info[1] if next_info else None,
            "worker_output_preview": self.worker_output[:500] if self.worker_output else "",
            "artifacts": artifacts,
            "context_summary": self.session.context_summary,
            "expectations": self.session.expectations,
            "stop_reason": self.stop_reason,
        }
        return result

    def _handle_update_session(
        self,
        context_summary: str | None = None,
        expectations: str | None = None,
        **kwargs,
    ) -> dict[str, str]:
        """Tool handler: update session with extracted data."""
        # Re-read session from Redis before writing
        try:
            all_sessions = list(AgentSession.query.filter(session_id=self.session.session_id))
            active = [s for s in all_sessions if s.status in ("running", "active", "pending")]
            candidates = active if active else all_sessions
            if candidates:
                candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
                self.session = candidates[0]
        except Exception as e:
            logger.warning(f"{self._log_prefix} Failed to re-read session before update: {e}")

        # Clear queued steering messages
        cleared_messages = False
        queued = self.session.queued_steering_messages
        if isinstance(queued, list) and queued:
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.info(
                f"{self._log_prefix} INTERJECTION session={self.session.session_id} "
                f"correlation={cid} count={len(queued)} action=cleared"
            )
            record_interjection(self.session.session_id, cid, len(queued), "cleared")
            self.session.queued_steering_messages = []
            cleared_messages = True

        updated = []
        if context_summary is not None:
            self.session.context_summary = context_summary
            updated.append("context_summary")
        if expectations is not None:
            self.session.expectations = expectations
            updated.append("expectations")

        if updated or cleared_messages:
            try:
                self.session.save()
            except Exception as e:
                logger.error(f"{self._log_prefix} Failed to save session updates: {e}")
                return {"status": "error", "error": str(e)}

        return {"status": "ok", "updated_fields": updated}

    def _dispatch_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch a tool call and return the result as a JSON string."""
        if tool_name == "read_session":
            result = self._handle_read_session()
        elif tool_name == "update_session":
            result = self._handle_update_session(**tool_input)
        elif tool_name == "enqueue_continuation":
            self._decision_made = True
            self._action_taken = "steer"
            result = {
                "status": "ok",
                "action": "enqueue_continuation",
                "coaching_message": tool_input.get("coaching_message", "continue"),
            }
        elif tool_name == "deliver_to_telegram":
            self._decision_made = True
            self._action_taken = "deliver"
            result = {
                "status": "ok",
                "action": "deliver_to_telegram",
                "reason": tool_input.get("reason", ""),
            }
            if tool_input.get("message_for_user"):
                result["message_for_user"] = tool_input["message_for_user"]
        else:
            result = {"status": "error", "error": f"Unknown tool: {tool_name}"}

        return json.dumps(result)

    async def run(self) -> dict[str, Any]:
        """Execute the Observer agent and return the routing decision.

        Simplified flow:
        1. Use state machine to classify outcome and determine stage state
        2. Deterministic guard: if SDLC with remaining stages, steer
        3. Fall through to LLM Observer for judgment calls

        Returns:
            Dict with keys:
            - action: "steer" | "deliver"
            - coaching_message: str (if action is "steer")
            - reason: str (if action is "deliver")
            - resolved_stage: str | None (stage just resolved)
            - stage_outcome: str | None ("success", "fail", or "ambiguous")
            - next_stage: str | None (stage to start next)
        """
        is_sdlc = self.session.is_sdlc
        max_continues = MAX_AUTO_CONTINUES_SDLC if is_sdlc else MAX_AUTO_CONTINUES

        # Use state machine for stage queries
        sm = self._state_machine
        has_remaining = sm.has_remaining_stages() if sm else False
        has_failed = sm.has_failed_stage() if sm else False
        current = sm.current_stage() if sm else None

        logger.info(
            f"{self._log_prefix} Session {self.session.session_id}: "
            f"is_sdlc={is_sdlc}, auto_continue={self.auto_continue_count}/{max_continues}, "
            f"remaining_stages={has_remaining}, current_stage={current}"
        )

        # Phase 1: Deterministic routing based on stop_reason
        if self.stop_reason and self.stop_reason not in ("end_turn", None):
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            if self.stop_reason == "budget_exceeded":
                logger.warning(f"{self._log_prefix} Worker stopped: budget_exceeded — delivering")
                record_decision(
                    self.session.session_id,
                    cid,
                    "deliver",
                    "stop_reason: budget_exceeded",
                )
                # Mark current stage as failed
                if sm and current:
                    try:
                        sm.fail_stage(current)
                    except Exception:
                        pass
                return {
                    "action": "deliver",
                    "reason": "Worker budget exceeded. Partial output delivered.",
                    "stop_reason": self.stop_reason,
                    "resolved_stage": None,
                    "stage_outcome": None,
                    "next_stage": None,
                }

            if self.stop_reason == "rate_limited":
                logger.warning(
                    f"{self._log_prefix} Worker stopped: rate_limited — steering with backoff"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "steer",
                    "stop_reason: rate_limited",
                )
                return {
                    "action": "steer",
                    "coaching_message": (
                        "Rate limited by the API. Wait briefly, then resume "
                        "where you left off. Do not restart from scratch."
                    ),
                    "stop_reason": self.stop_reason,
                    "resolved_stage": None,
                    "stage_outcome": None,
                    "next_stage": current,
                }

            logger.info(
                f"{self._log_prefix} Unknown stop_reason={self.stop_reason}, "
                f"falling through to LLM Observer"
            )

        # Phase 2: State machine outcome classification
        resolved_stage = None
        stage_outcome = None
        next_stage_name = None
        next_skill = None

        if sm and current:
            stage_outcome = sm.classify_outcome(
                current, self.stop_reason, self.worker_output[-500:] if self.worker_output else ""
            )
            if stage_outcome in ("success", "fail"):
                resolved_stage = current
                next_info = sm.next_stage(stage_outcome)
                if next_info:
                    next_stage_name, next_skill = next_info

        # Phase 3: Deterministic SDLC guard
        # If SDLC with remaining stages: steer to next stage unless blocked
        stop_is_terminal = self.stop_reason in ("fail", "budget_exceeded")
        cap_reached = self.auto_continue_count >= max_continues
        needs_human = _output_needs_human_input(self.worker_output)

        if (
            is_sdlc
            and has_remaining
            and not has_failed
            and not stop_is_terminal
            and not cap_reached
            and not needs_human
        ):
            # Determine next stage from state machine or fallback
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
                cid = getattr(self.session, "correlation_id", None) or "unknown"
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
                    f"{self._log_prefix} Deterministic guard: steer to {stage_name} ({skill_cmd})"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "steer",
                    f"state-machine-guard: {stage_name} pending",
                )
                return {
                    "action": "steer",
                    "coaching_message": coaching,
                    "deterministic_guard": True,
                    "resolved_stage": resolved_stage,
                    "stage_outcome": stage_outcome,
                    "next_stage": stage_name,
                }

        # Log when guard was bypassed
        if (
            is_sdlc
            and has_remaining
            and (has_failed or stop_is_terminal or cap_reached or needs_human)
        ):
            logger.info(
                f"{self._log_prefix} Guard bypassed: has_failed={has_failed}, "
                f"stop_reason={self.stop_reason}, cap_reached={cap_reached}, "
                f"needs_human={needs_human}"
            )

        # Phase 4: LLM Observer for judgment calls
        return await self._run_llm_observer(resolved_stage, stage_outcome, next_stage_name)

    async def _run_llm_observer(
        self,
        resolved_stage: str | None,
        stage_outcome: str | None,
        next_stage_name: str | None,
    ) -> dict[str, Any]:
        """Run the LLM Observer for ambiguous routing decisions."""
        base = {
            "resolved_stage": resolved_stage,
            "stage_outcome": stage_outcome,
            "next_stage": next_stage_name,
        }
        try:
            api_key = get_anthropic_api_key()
            if not api_key:
                logger.error(f"{self._log_prefix} No API key, falling back to deliver")
                return {"action": "deliver", "reason": "No API key for Observer", **base}

            client = anthropic.Anthropic(api_key=api_key)
            user_message = (
                f"The worker agent has stopped. Output ({len(self.worker_output)} chars):\n\n"
                f"{self.worker_output[:3000]}"
            )
            if len(self.worker_output) > 3000:
                user_message += (
                    f"\n\n[...truncated, {len(self.worker_output) - 3000} more chars...]"
                )

            messages = [{"role": "user", "content": user_message}]
            tools = _build_tools()
            coaching_message = deliver_reason = message_for_user = None

            for iteration in range(MAX_TOOL_ITERATIONS):
                response = client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=_build_observer_system_prompt(),
                    messages=messages,
                    tools=tools,
                )
                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    break

                tool_results = []
                for tu in tool_uses:
                    result_str = self._dispatch_tool(tu.name, tu.input)
                    logger.info(f"{self._log_prefix} LLM iter {iteration + 1}: tool={tu.name}")
                    cid = getattr(self.session, "correlation_id", None) or "unknown"
                    record_tool_use(self.session.session_id, cid, tu.name)
                    tool_results.append(
                        {"type": "tool_result", "tool_use_id": tu.id, "content": result_str}
                    )
                    data = json.loads(result_str)
                    if data.get("action") == "enqueue_continuation":
                        coaching_message = data.get("coaching_message", "continue")
                    elif data.get("action") == "deliver_to_telegram":
                        deliver_reason = data.get("reason", "")
                        message_for_user = data.get("message_for_user") or message_for_user

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                if self._decision_made:
                    break

            if not self._decision_made:
                logger.warning(
                    f"{self._log_prefix} Observer did not converge, defaulting to deliver"
                )
                return {"action": "deliver", "reason": "Observer did not converge", **base}

            # Success — reset circuit breaker
            observer_record_success(self.session.session_id)

            cid = getattr(self.session, "correlation_id", None) or "unknown"
            if self._action_taken == "steer":
                record_decision(
                    self.session.session_id, cid, "steer", (coaching_message or "")[:120]
                )
                return {
                    "action": "steer",
                    "coaching_message": coaching_message or "continue",
                    **base,
                }

            record_decision(
                self.session.session_id, cid, "deliver", (deliver_reason or "deliver")[:120]
            )
            result = {
                "action": "deliver",
                "reason": deliver_reason or "Observer decided to deliver",
                **base,
            }
            if message_for_user:
                result["message_for_user"] = message_for_user
            return result

        except Exception as e:
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.error(f"{self._log_prefix} Observer failed: {e}", exc_info=True)
            record_decision(self.session.session_id, cid, "error", str(e))

            # Circuit breaker: classify error and decide retry vs escalate
            error_class = _classify_observer_error(e)
            cb_state = observer_record_failure(self.session.session_id)

            result = {
                "action": "deliver",
                "reason": f"Observer error: {e}",
                "error": str(e),
                "error_class": error_class,
                "failure_count": cb_state["failure_count"],
                **base,
            }

            if error_class == "retryable" and cb_state["should_retry"]:
                result["retry_after"] = cb_state["retry_after"]
                logger.info(
                    "%s Observer retryable error (count=%d), retry after %.0fs: %s",
                    self._log_prefix,
                    cb_state["failure_count"],
                    cb_state["retry_after"],
                    e,
                )
            elif cb_state["should_escalate"]:
                result["should_escalate"] = True
                logger.warning(
                    "%s Observer circuit breaker tripped (count=%d), escalating: %s",
                    self._log_prefix,
                    cb_state["failure_count"],
                    e,
                )
            elif error_class == "non_retryable":
                result["should_escalate"] = True
                logger.warning(
                    "%s Observer non-retryable error, escalating immediately: %s",
                    self._log_prefix,
                    e,
                )

            return result
