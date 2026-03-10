"""Observer Agent — stage-aware SDLC steerer.

Replaces the fragmented classifier -> coach -> routing chain with a single
Sonnet-powered agent that has full AgentSession context. Runs synchronously
inside send_to_chat() at the same call site where classify_output() +
classify_routing_decision() + build_coaching_message() were called before.

The Observer:
1. Reads the AgentSession (stages, links, history, queued messages)
2. Runs the stage detector to update stage progress deterministically
3. Decides: steer the agent to the next pipeline stage, OR deliver to Telegram
4. Updates the session with any extracted artifacts (links, context_summary)

Fallback: if the Observer errors, raw worker output is delivered to Telegram.
This ensures the pipeline never silently drops output.
"""

import json
import logging
from typing import Any

import anthropic

from agent.job_queue import MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC
from bridge.stage_detector import apply_transitions, detect_stages
from bridge.summarizer import extract_artifacts
from config.models import SONNET
from models.agent_session import AgentSession

logger = logging.getLogger(__name__)

# Maximum tool-use iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 5

# Observer system prompt — defines the decision framework
OBSERVER_SYSTEM_PROMPT = """\
You are the Observer Agent for an autonomous SDLC pipeline. Your job is to decide
what happens when the worker agent stops producing output.

You have access to the full AgentSession state and must make one of two decisions:
1. STEER: Send the worker back to work on the next pipeline stage
2. DELIVER: Send the output to the human on Telegram

## SDLC Pipeline Stages (in order)
ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS

## Decision Framework

### STEER when:
- Pipeline stages remain incomplete (pending or in_progress)
- The worker paused with a status update, not a question
- The worker finished one stage and needs to move to the next
- Missing links (issue URL, PR URL) that should have been created

### DELIVER when:
- All pipeline stages are complete
- The worker is asking the human a genuine question (needs a decision)
- The worker hit a blocker that requires human intervention
- An error occurred that the worker cannot recover from
- This is a non-SDLC job (casual conversation, Q&A)
- The worker produced a final completion with evidence

### NEVER:
- Auto-continue more than 10 times consecutively
- Silently drop output — always either steer or deliver
- Ignore queued steering messages from the human

## Tool Usage Order
1. ALWAYS call read_session first to get current state
2. Check for queued_steering_messages — human replies take priority
3. Make your decision based on session state + worker output
4. Call exactly ONE of: enqueue_continuation OR deliver_to_telegram
5. Optionally call update_session to persist any extracted data

## Coaching Messages
When steering, craft a message that encourages the worker to continue with \
discernment. The worker is a skilled agent — speak to its competence, not \
its compliance.

Good coaching messages:
- Acknowledge what was done, then encourage forward progress
- Give the worker permission to raise genuine critical questions to the \
architect or project manager — but make it a narrow opening, not an invitation \
to stop
- Reference the current or next /do-* skill when appropriate, but don't be \
purely mechanical about it
- Close with what success looks like for this step — a concrete target, not \
a vague aspiration. E.g. "Success here means clean, tested code with no \
silent assumptions."
- If assumptions need checking, say so specifically: "verify X before \
proceeding" rather than vague "think carefully"

Example: "Good progress on the plan. Continue with the build — invoke \
/do-build. Prioritize correctness over speed. If you encounter a critical \
architecture question that needs human input, state it clearly and directly. \
Otherwise, press forward. Success here means working code with tests that \
pass on the first run."

Bad coaching messages (avoid these):
- Bare "continue" with no context
- Purely mechanical: "Invoke /do-test to run the test suite."
- Over-explaining what the agent already knows
- Vague urgency: "think hard", "be very careful" — specify what to check
- Threats or artificial pressure — they degrade output quality, not improve it
"""


def _build_tools() -> list[dict]:
    """Build the tool definitions for the Observer agent."""
    return [
        {
            "name": "read_session",
            "description": (
                "Read the current AgentSession state including stages, links, "
                "history, and queued steering messages. MUST be called first."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "update_session",
            "description": (
                "Update the AgentSession with extracted data. Call after making "
                "your decision to persist context_summary, expectations, or links."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "context_summary": {
                        "type": ["string", "null"],
                        "description": "One-sentence summary of what this session is about",
                    },
                    "expectations": {
                        "type": ["string", "null"],
                        "description": "What the agent needs from the human, or null",
                    },
                    "issue_url": {
                        "type": ["string", "null"],
                        "description": "GitHub issue URL if detected in output",
                    },
                    "pr_url": {
                        "type": ["string", "null"],
                        "description": "GitHub PR URL if detected in output",
                    },
                },
                "required": [],
            },
        },
        {
            "name": "enqueue_continuation",
            "description": (
                "Steer the worker back to work. Provide a coaching message "
                "that tells it exactly what to do next. This re-enqueues the "
                "job with the coaching message as the new prompt."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "coaching_message": {
                        "type": "string",
                        "description": (
                            "Clear instruction for the worker. Reference the "
                            "specific /do-* skill to invoke next."
                        ),
                    },
                },
                "required": ["coaching_message"],
            },
        },
        {
            "name": "deliver_to_telegram",
            "description": (
                "Deliver the worker's output to the human on Telegram. "
                "Use when the pipeline is complete, the worker is asking a "
                "question, or an error needs human attention."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Why this output is being delivered to the human",
                    },
                },
                "required": ["reason"],
            },
        },
    ]


class Observer:
    """Observer Agent that makes routing decisions with full session context.

    Runs synchronously inside send_to_chat(). Uses Claude API directly
    (not Claude Code subprocess) with tool_use for structured decisions.

    Args:
        session: The AgentSession for this pipeline run
        worker_output: The raw text output from the worker agent
        auto_continue_count: Current auto-continue count for this session
        send_cb: Async callback to send messages to Telegram
        enqueue_fn: Async function to enqueue a continuation job
    """

    def __init__(
        self,
        session: AgentSession,
        worker_output: str,
        auto_continue_count: int,
        send_cb,
        enqueue_fn,
        *,
        model: str | None = None,
    ):
        self.session = session
        self.worker_output = worker_output
        self.auto_continue_count = auto_continue_count
        self.send_cb = send_cb
        self.enqueue_fn = enqueue_fn
        self.model = model or SONNET
        self._decision_made = False
        self._action_taken: str | None = None
        self._log_prefix = (
            f"[{session.correlation_id}]"
            if getattr(session, "correlation_id", None)
            else "[observer]"
        )

    def _handle_read_session(self) -> dict[str, Any]:
        """Tool handler: read the current session state."""
        progress = self.session.get_stage_progress()
        links = self.session.get_links()
        history = self.session.get_history_list()
        # Peek at queued messages without clearing — pop happens in update_session
        raw = self.session.queued_steering_messages
        queued = list(raw) if isinstance(raw, list) else []
        is_sdlc = self.session.is_sdlc_job()

        # Extract artifacts from worker output
        artifacts = extract_artifacts(self.worker_output)

        return {
            "session_id": self.session.session_id,
            "correlation_id": getattr(self.session, "correlation_id", None),
            "is_sdlc": is_sdlc,
            "classification_type": self.session.classification_type,
            "stage_progress": progress,
            "links": links,
            "history": history[-10:],  # Last 10 entries for context
            "queued_steering_messages": queued,
            "auto_continue_count": self.auto_continue_count,
            "max_auto_continues": MAX_AUTO_CONTINUES_SDLC if is_sdlc else MAX_AUTO_CONTINUES,
            "has_remaining_stages": self.session.has_remaining_stages(),
            "has_failed_stage": self.session.has_failed_stage(),
            "worker_output_preview": self.worker_output[:500] if self.worker_output else "",
            "artifacts": artifacts,
            "context_summary": self.session.context_summary,
            "expectations": self.session.expectations,
        }

    def _handle_update_session(
        self,
        context_summary: str | None = None,
        expectations: str | None = None,
        issue_url: str | None = None,
        pr_url: str | None = None,
    ) -> dict[str, str]:
        """Tool handler: update session with extracted data."""
        # Re-read session from Redis before writing to avoid clobbering
        # concurrent writes (e.g., queued_steering_messages appended by bridge)
        try:
            fresh = list(AgentSession.query.filter(session_id=self.session.session_id))
            if fresh:
                self.session = fresh[0]
        except Exception as e:
            logger.warning(f"{self._log_prefix} Failed to re-read session before update: {e}")

        # Clear queued steering messages now (deferred from read_session peek)
        cleared_messages = False
        queued = self.session.queued_steering_messages
        if isinstance(queued, list) and queued:
            self.session.queued_steering_messages = []
            cleared_messages = True

        updated = []
        if context_summary is not None:
            self.session.context_summary = context_summary
            updated.append("context_summary")
        if expectations is not None:
            self.session.expectations = expectations
            updated.append("expectations")
        if issue_url is not None:
            self.session.issue_url = issue_url
            updated.append("issue_url")
        if pr_url is not None:
            self.session.pr_url = pr_url
            updated.append("pr_url")

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
            # The actual enqueue happens after the Observer completes
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
        else:
            result = {"status": "error", "error": f"Unknown tool: {tool_name}"}

        return json.dumps(result)

    async def run(self) -> dict[str, Any]:
        """Execute the Observer agent and return the routing decision.

        Returns:
            Dict with keys:
            - action: "steer" | "deliver"
            - coaching_message: str (if action is "steer")
            - reason: str (if action is "deliver")
            - transitions_applied: int (stage transitions detected)
        """
        # Log session context at start of run
        is_sdlc = self.session.is_sdlc_job()
        max_continues = MAX_AUTO_CONTINUES_SDLC if is_sdlc else MAX_AUTO_CONTINUES
        has_remaining = self.session.has_remaining_stages()
        logger.info(
            f"[observer] Session {self.session.session_id}: "
            f"is_sdlc={is_sdlc}, auto_continue={self.auto_continue_count}/{max_continues}, "
            f"remaining_stages={has_remaining}"
        )

        # Phase 1: Run deterministic stage detector BEFORE the Observer
        transitions = detect_stages(self.worker_output)
        transitions_applied = apply_transitions(self.session, transitions)
        if transitions_applied > 0:
            logger.info(
                f"{self._log_prefix} Stage detector applied {transitions_applied} transitions "
                f"for session {self.session.session_id}"
            )

        # Phase 2: Run the Observer LLM for judgment calls
        try:
            from utils.api_keys import get_anthropic_api_key

            api_key = get_anthropic_api_key()
            if not api_key:
                logger.error(f"{self._log_prefix} No API key available, falling back to deliver")
                return {
                    "action": "deliver",
                    "reason": "No API key for Observer",
                    "transitions_applied": transitions_applied,
                }

            client = anthropic.Anthropic(api_key=api_key)

            # Build the user message with worker output context
            user_message = (
                f"The worker agent has stopped. Here is its output "
                f"({len(self.worker_output)} chars):\n\n"
                f"{self.worker_output[:3000]}"
            )
            if len(self.worker_output) > 3000:
                remaining = len(self.worker_output) - 3000
                user_message += f"\n\n[...truncated, {remaining} more chars...]"

            messages = [{"role": "user", "content": user_message}]
            tools = _build_tools()
            coaching_message = None
            deliver_reason = None

            # Tool-use loop with iteration cap
            for iteration in range(MAX_TOOL_ITERATIONS):
                response = client.messages.create(
                    model=self.model,
                    max_tokens=1024,
                    system=OBSERVER_SYSTEM_PROMPT,
                    messages=messages,
                    tools=tools,
                )

                # Check if the model wants to use tools
                tool_uses = [b for b in response.content if b.type == "tool_use"]

                if not tool_uses:
                    # No tool calls — the model is done
                    break

                # Process each tool call
                tool_results = []
                for tool_use in tool_uses:
                    result_str = self._dispatch_tool(tool_use.name, tool_use.input)
                    # Log each iteration with tool name and result preview
                    result_preview = result_str[:120] if result_str else ""
                    logger.info(
                        f"[observer] Iteration {iteration + 1}/{MAX_TOOL_ITERATIONS}: "
                        f"tool={tool_use.name}, result={result_preview}"
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "content": result_str,
                        }
                    )

                    # Extract decision data
                    result_data = json.loads(result_str)
                    if result_data.get("action") == "enqueue_continuation":
                        coaching_message = result_data.get("coaching_message", "continue")
                    elif result_data.get("action") == "deliver_to_telegram":
                        deliver_reason = result_data.get("reason", "")

                # Append assistant response and tool results for next iteration
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

                # If a decision was made, we can stop the loop
                if self._decision_made:
                    break

            # If the Observer didn't make a decision, default to deliver
            if not self._decision_made:
                logger.warning(
                    f"{self._log_prefix} Observer did not make a routing decision after "
                    f"{MAX_TOOL_ITERATIONS} iterations, defaulting to deliver"
                )
                return {
                    "action": "deliver",
                    "reason": "Observer did not converge on a decision",
                    "transitions_applied": transitions_applied,
                }

            if self._action_taken == "steer":
                reason_preview = (coaching_message or "continue")[:120]
                logger.info(f"[observer] Decision: steer (reason: {reason_preview})")
                return {
                    "action": "steer",
                    "coaching_message": coaching_message or "continue",
                    "transitions_applied": transitions_applied,
                }
            else:
                reason_preview = (deliver_reason or "Observer decided to deliver")[:120]
                logger.info(f"[observer] Decision: deliver (reason: {reason_preview})")
                return {
                    "action": "deliver",
                    "reason": deliver_reason or "Observer decided to deliver",
                    "transitions_applied": transitions_applied,
                }

        except Exception as e:
            logger.error(f"{self._log_prefix} Observer failed: {e}", exc_info=True)
            return {
                "action": "deliver",
                "reason": f"Observer error: {e}",
                "transitions_applied": transitions_applied,
                "error": str(e),
            }
