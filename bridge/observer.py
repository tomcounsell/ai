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
import os
import re
from typing import Any

import anthropic

from agent.job_queue import MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC
from agent.skill_outcome import parse_outcome_from_text
from bridge.pipeline_graph import STAGE_TO_SKILL, get_next_stage
from bridge.stage_detector import STAGE_ORDER, apply_transitions, detect_stages
from bridge.summarizer import extract_artifacts
from config.models import SONNET
from models.agent_session import AgentSession
from monitoring.telemetry import record_decision, record_interjection, record_tool_use
from utils.api_keys import get_anthropic_api_key

logger = logging.getLogger(__name__)

# Regex patterns for extracting issue/PR numbers from GitHub URLs
_ISSUE_NUMBER_RE = re.compile(r"/issues/(\d+)")
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _construct_canonical_url(url: str | None, gh_repo: str | None) -> str | None:
    """Construct a canonical GitHub URL from a worker-provided URL.

    Extracts the issue or PR number from the URL and constructs the canonical
    URL using the configured GH_REPO, preventing wrong-repo URLs from
    propagating through the system.

    Args:
        url: Worker-provided URL (may reference wrong repo or be malformed).
        gh_repo: GitHub repo in "owner/name" format (e.g., "tomcounsell/ai").

    Returns:
        Canonical URL if a number could be extracted and gh_repo is available,
        None if extraction fails or inputs are empty.
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

    # Try PR number first (more specific path)
    pr_match = _PR_NUMBER_RE.search(url)
    if pr_match:
        number = pr_match.group(1)
        return f"https://github.com/{gh_repo}/pull/{number}"

    # Try issue number
    issue_match = _ISSUE_NUMBER_RE.search(url)
    if issue_match:
        number = issue_match.group(1)
        return f"https://github.com/{gh_repo}/issues/{number}"

    # No extractable number — discard with warning
    logger.warning(f"Cannot extract issue/PR number from URL: {url}. URL discarded.")
    return None


# Maximum tool-use iterations to prevent infinite loops
MAX_TOOL_ITERATIONS = 5

# Observer system prompt — defines the decision framework
OBSERVER_SYSTEM_PROMPT = """\
You are the Observer Agent for an autonomous SDLC pipeline. Your job is to decide
what happens when the worker agent stops producing output.

You have access to the full AgentSession state and must make one of two decisions:
1. STEER: Send the worker back to work on the next pipeline stage
2. DELIVER: Send the output to the human on Telegram

## SDLC Pipeline Stages (in order, from bridge/pipeline_graph.py)
ISSUE -> PLAN -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE
Cycles: TEST(fail) -> PATCH -> TEST, REVIEW(fail) -> PATCH -> TEST -> REVIEW
MERGE is a human-gated stage — the agent presents merge readiness to the human.

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

### DELIVER message_for_user guidance:
- When delivering, use the `message_for_user` field to curate what the user sees
- Summarize findings, strip process narration, and present results clearly
- The `reason` field is for internal logging only — it is NEVER sent to the user
- If the worker output is already clean and substantive, you may omit message_for_user

### NEVER:
- Auto-continue more than 10 times consecutively
- Silently drop output — always either steer or deliver
- Ignore queued steering messages from the human
- Send the `reason` field content to the user — it is internal-only

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
                "Deliver output to the human on Telegram. "
                "Use when the pipeline is complete, the worker is asking a "
                "question, or an error needs human attention. "
                "Provide message_for_user to curate what the user sees — "
                "this replaces the raw worker output. If omitted, the raw "
                "worker output is sent as-is."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": (
                            "Internal reason for delivery (logged only, never sent to user)"
                        ),
                    },
                    "message_for_user": {
                        "type": "string",
                        "description": (
                            "Curated message for the user. Use this to craft "
                            "a clean, substantive message instead of sending "
                            "raw worker output. Optional — omit for simple "
                            "completed tasks where an emoji reaction suffices, "
                            "or when the raw output is already suitable."
                        ),
                    },
                },
                "required": ["reason"],
            },
        },
    ]


# Heuristic patterns that signal the worker needs human input.
# When detected, the deterministic SDLC guard defers to the LLM Observer
# so it can decide whether to deliver or steer.
_HUMAN_INPUT_PATTERNS = [
    re.compile(r"## Open Questions", re.IGNORECASE),
    re.compile(r"(?:Should I|Should we|Do you want|Would you prefer)", re.IGNORECASE),
    re.compile(r"(?:FATAL|cannot proceed|nothing I can do)", re.IGNORECASE),
    re.compile(r"(?:requires? (?:human|your|a human))", re.IGNORECASE),
    re.compile(r"(?:I(?:'d| would) rather get your input)", re.IGNORECASE),
]


def _output_needs_human_input(text: str) -> bool:
    """Check if worker output contains signals that human input is needed.

    Uses lightweight regex heuristics rather than LLM calls to keep the
    deterministic guard fast. Falls back to the LLM Observer for nuanced
    judgment when these patterns are detected.
    """
    return any(p.search(text) for p in _HUMAN_INPUT_PATTERNS)


def _next_sdlc_skill(session) -> tuple[str, str] | None:
    """Determine the next SDLC skill to invoke based on stage progress.

    Uses the canonical pipeline graph from bridge.pipeline_graph for routing.
    Finds the last completed/failed stage and calls get_next_stage() to resolve
    the next stage via the directed graph, supporting cycle routing
    (e.g., TEST fail -> PATCH -> TEST).

    Falls back to a linear walk of STAGE_ORDER for first-run scenarios where
    no stages have completed yet.

    Returns:
        Tuple of (stage_name, skill_command) for the next stage,
        or None if all stages are complete or the graph has no transition.
    """
    progress = session.get_stage_progress()

    # Find the last completed or failed stage to determine graph routing
    last_completed = None
    last_outcome = "success"
    for stage in STAGE_ORDER:
        status = progress.get(stage, "pending")
        if status == "completed":
            last_completed = stage
            last_outcome = "success"
        elif status == "failed":
            last_completed = stage
            last_outcome = "fail"

    # Count PATCH cycles from history for the max-cycle safety valve
    cycle_count = 0
    history = session.get_history_list() if hasattr(session, "get_history_list") else []
    for entry in history:
        if isinstance(entry, dict) and entry.get("stage") == "PATCH":
            cycle_count += 1

    # Use graph routing if we have a completed/failed stage
    if last_completed:
        next_info = get_next_stage(last_completed, last_outcome, cycle_count=cycle_count)
        if next_info:
            next_stage, _skill = next_info
            # Guard: REVIEW requires a PR to exist. If REVIEW is pending
            # (not yet started) and no PR URL is tracked, route back to
            # BUILD which handles PR creation.
            if next_stage == "REVIEW" and not getattr(session, "pr_url", None):
                review_status = progress.get("REVIEW", "pending")
                if review_status == "pending":
                    logger.info(
                        "SDLC routing: REVIEW is next but no pr_url on session — "
                        "routing to BUILD to create PR first"
                    )
                    return ("BUILD", "/do-build")
            return next_info
        return None

    # Fallback: linear walk for first run (no completed stages yet)
    for stage in STAGE_ORDER:
        status = progress.get(stage, "pending")
        if status in ("pending", "in_progress"):
            skill = STAGE_TO_SKILL.get(stage, f"/do-{stage.lower()}")
            return (stage, skill)

    return None


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

    def _check_mandatory_gates(self) -> dict[str, Any] | None:
        """Check if mandatory REVIEW and DOCS gates are satisfied before delivery.

        Only applies to SDLC sessions with a work_item_slug. Returns None if
        gates are satisfied or enforcement should be skipped. Returns a steer
        result dict if gates are unsatisfied and delivery should be blocked.

        Results are cached per run() invocation via self._gate_cache.

        Cycle safety: if gate-forced steering has happened 3+ times for the
        same unsatisfied gate (tracked via session history), delivers with
        a warning instead of looping forever.
        """
        # Skip for non-SDLC sessions
        if not self.session.is_sdlc_job():
            return None

        slug = getattr(self.session, "work_item_slug", None)
        if not slug:
            return None

        # Use cached result if available
        if hasattr(self, "_gate_cache"):
            return self._gate_cache

        try:
            from agent.goal_gates import check_docs_gate, check_review_gate

            working_dir = getattr(self.session, "working_dir", None) or "."

            review_result = check_review_gate(slug, working_dir)
            docs_result = check_docs_gate(slug, working_dir)

            if review_result.satisfied and docs_result.satisfied:
                self._gate_cache = None
                return None

            # Determine which gate is unsatisfied and the corresponding skill
            if not review_result.satisfied:
                unsatisfied_gate = "REVIEW"
                next_skill = "/do-pr-review"
                missing = review_result.missing or "PR review"
            else:
                unsatisfied_gate = "DOCS"
                next_skill = "/do-docs"
                missing = docs_result.missing or "Documentation"

            # Cycle safety: count how many times we've forced steering for this gate
            gate_steer_count = 0
            history = self.session.get_history_list()
            gate_steer_marker = f"gate-forced-steer:{unsatisfied_gate}"
            for entry in history:
                if isinstance(entry, str) and gate_steer_marker in entry:
                    gate_steer_count += 1

            if gate_steer_count >= 3:
                logger.warning(
                    f"{self._log_prefix} Gate enforcement cycle limit reached for "
                    f"{unsatisfied_gate} ({gate_steer_count} steerings). "
                    f"Delivering with warning."
                )
                self._gate_cache = None
                return None

            # Record the gate-forced steer in history
            try:
                self.session.append_history(
                    "system",
                    f"Delivery blocked: {unsatisfied_gate} gate unsatisfied "
                    f"({gate_steer_marker}). Missing: {missing}",
                )
            except Exception as e:
                logger.warning(f"{self._log_prefix} Failed to record gate steer: {e}")

            coaching = (
                f"Delivery blocked: {unsatisfied_gate} gate unsatisfied. "
                f"Missing: {missing}. "
                f"Continue with {next_skill} to satisfy the gate. "
                f"If you encounter a critical blocker, state it clearly."
            )

            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.info(
                f"{self._log_prefix} Mandatory gate enforcement: blocking delivery, "
                f"steering to {unsatisfied_gate} ({next_skill})"
            )
            record_decision(
                self.session.session_id,
                cid,
                "steer",
                f"mandatory-gate: {unsatisfied_gate} unsatisfied",
            )

            result = {
                "action": "steer",
                "coaching_message": coaching,
                "mandatory_gate_enforcement": True,
                "unsatisfied_gate": unsatisfied_gate,
            }
            self._gate_cache = result
            return result

        except Exception as e:
            # Gate check failure should not crash the Observer — fall back to
            # current behavior (allow delivery)
            logger.warning(
                f"{self._log_prefix} Mandatory gate check failed: {e}. "
                f"Falling back to allow delivery."
            )
            self._gate_cache = None
            return None


        """Tool handler: read the current session state."""
        progress = self.session.get_stage_progress()
        links = self.session.get_links()
        history = self.session.get_history_list()
        # Peek at queued messages without clearing — pop happens in update_session
        raw = self.session.queued_steering_messages
        queued = list(raw) if isinstance(raw, list) else []
        if queued:
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.info(
                f"{self._log_prefix} INTERJECTION session={self.session.session_id} "
                f"correlation={cid} count={len(queued)} action=read"
            )
            record_interjection(self.session.session_id, cid, len(queued), "read")
        is_sdlc = self.session.is_sdlc_job()

        # Extract artifacts from worker output
        artifacts = extract_artifacts(self.worker_output)

        # Add gate status for SDLC sessions
        gate_status: dict[str, Any] = {}
        if is_sdlc:
            try:
                from agent.goal_gates import check_all_gates

                slug = getattr(self.session, "work_item_slug", None)
                if slug:
                    working_dir = getattr(self.session, "working_dir", None) or "."
                    gate_results = check_all_gates(slug, working_dir, self.session)
                    gate_status = {
                        stage: {
                            "satisfied": result.satisfied,
                            "evidence": result.evidence,
                            "missing": result.missing,
                        }
                        for stage, result in gate_results.items()
                    }
            except Exception as e:
                logger.warning(f"{self._log_prefix} Gate check failed: {e}")
                gate_status = {"error": str(e)}

        return {
            "session_id": self.session.session_id,
            "correlation_id": getattr(self.session, "correlation_id", None),
            "is_sdlc": is_sdlc,
            "classification_type": self.session.classification_type,
            "stage_progress": progress,
            "gate_status": gate_status,
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
            "stop_reason": self.stop_reason,
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
        # concurrent writes (e.g., queued_steering_messages appended by bridge).
        # Bug 3 fix (issue #374): Use deterministic record selection — filter
        # by active statuses first, sort by created_at desc to pick newest.
        try:
            all_sessions = list(AgentSession.query.filter(session_id=self.session.session_id))
            # Prefer running/active records; fall back to any record
            active = [s for s in all_sessions if s.status in ("running", "active", "pending")]
            candidates = active if active else all_sessions
            if candidates:
                candidates.sort(key=lambda s: s.created_at or 0, reverse=True)
                self.session = candidates[0]
                if len(all_sessions) > 1:
                    logger.info(
                        f"{self._log_prefix} Re-read session: selected "
                        f"status={self.session.status} from {len(all_sessions)} "
                        f"records for {self.session.session_id}"
                    )
        except Exception as e:
            logger.warning(f"{self._log_prefix} Failed to re-read session before update: {e}")

        # Clear queued steering messages now (deferred from read_session peek)
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
        if issue_url is not None:
            gh_repo = os.environ.get("GH_REPO")
            canonical = _construct_canonical_url(issue_url, gh_repo)
            if canonical:
                self.session.issue_url = canonical
                updated.append("issue_url")
            else:
                logger.info(f"{self._log_prefix} Discarded invalid issue_url: {issue_url}")
        if pr_url is not None:
            gh_repo = os.environ.get("GH_REPO")
            canonical = _construct_canonical_url(pr_url, gh_repo)
            if canonical:
                self.session.pr_url = canonical
                updated.append("pr_url")
            else:
                logger.info(f"{self._log_prefix} Discarded invalid pr_url: {pr_url}")

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
            # Include message_for_user if the observer provided it
            if tool_input.get("message_for_user"):
                result["message_for_user"] = tool_input["message_for_user"]
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
            f"{self._log_prefix} Session {self.session.session_id}: "
            f"is_sdlc={is_sdlc}, auto_continue={self.auto_continue_count}/{max_continues}, "
            f"remaining_stages={has_remaining}"
        )

        # Phase 1: Parse typed outcome (if present) and run stage detector
        outcome = parse_outcome_from_text(self.worker_output)
        transitions = detect_stages(self.worker_output)
        transitions_applied = apply_transitions(self.session, transitions, outcome=outcome)
        if transitions_applied > 0:
            # Refresh has_remaining after stage transitions were applied
            has_remaining = self.session.has_remaining_stages()
            logger.info(
                f"{self._log_prefix} Stage detector applied {transitions_applied} transitions "
                f"for session {self.session.session_id} (remaining_stages={has_remaining})"
            )
        if outcome is not None:
            logger.info(
                f"{self._log_prefix} Typed outcome found: "
                f"status={outcome.status}, stage={outcome.stage}"
            )
            cid = getattr(self.session, "correlation_id", None) or "unknown"

            # Store outcome artifacts in session metadata
            # Apply canonical URL construction to prevent wrong-repo URLs
            if outcome.artifacts:
                try:
                    gh_repo = os.environ.get("GH_REPO")
                    if outcome.artifacts.get("pr_url"):
                        canonical = _construct_canonical_url(outcome.artifacts["pr_url"], gh_repo)
                        if canonical:
                            self.session.pr_url = canonical
                    if outcome.artifacts.get("issue_url"):
                        canonical = _construct_canonical_url(
                            outcome.artifacts["issue_url"], gh_repo
                        )
                        if canonical:
                            self.session.issue_url = canonical
                    self.session.save()
                except Exception as e:
                    logger.warning(f"{self._log_prefix} Failed to save outcome artifacts: {e}")

            if outcome.status == "success" and self.session.has_remaining_stages():
                # Success with remaining stages: steer to next stage (skip LLM)
                if outcome.next_skill:
                    next_skill = outcome.next_skill
                else:
                    try:
                        next_info = _next_sdlc_skill(self.session)
                        next_skill = next_info[1] if next_info else "the next pipeline stage"
                    except Exception:
                        next_skill = "the next pipeline stage"
                coaching = (
                    f"{outcome.stage} completed successfully. "
                    f"{outcome.notes} Continue with {next_skill}."
                )
                logger.info(
                    f"{self._log_prefix} Typed outcome routing: steer (success, remaining stages)"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "steer",
                    f"typed-outcome: {outcome.stage} success",
                )
                return {
                    "action": "steer",
                    "coaching_message": coaching,
                    "transitions_applied": transitions_applied,
                    "typed_outcome": outcome.to_dict(),
                }

            if outcome.status == "success" and not self.session.has_remaining_stages():
                # Enforcement point 1: check mandatory gates before delivering
                gate_override = self._check_mandatory_gates()
                if gate_override:
                    gate_override["transitions_applied"] = transitions_applied
                    gate_override["typed_outcome"] = outcome.to_dict()
                    return gate_override

                # Success with no remaining stages: deliver to human
                logger.info(
                    f"{self._log_prefix} Typed outcome routing: deliver "
                    f"(success, all stages complete)"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "deliver",
                    f"typed-outcome: {outcome.stage} success, pipeline complete",
                )
                return {
                    "action": "deliver",
                    "reason": f"Pipeline complete. {outcome.notes}",
                    "transitions_applied": transitions_applied,
                    "typed_outcome": outcome.to_dict(),
                }

            if outcome.status == "fail":
                # Failure: deliver to human with failure context
                reason = f"{outcome.stage} failed: {outcome.failure_reason or outcome.notes}"
                logger.info(
                    f"{self._log_prefix} Typed outcome routing: deliver "
                    f"(fail, reason: {reason[:120]})"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "deliver",
                    f"typed-outcome: {outcome.stage} fail",
                )
                return {
                    "action": "deliver",
                    "reason": reason,
                    "transitions_applied": transitions_applied,
                    "typed_outcome": outcome.to_dict(),
                }

            # For partial/retry/skipped/unknown: fall through to LLM Observer
            logger.info(
                f"{self._log_prefix} Typed outcome status={outcome.status} "
                f"is ambiguous, falling through to LLM Observer"
            )

        # Phase 1.5: Deterministic routing based on stop_reason
        # These short-circuit the LLM Observer when the SDK reports a known stop condition.
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
                return {
                    "action": "deliver",
                    "reason": "Worker budget exceeded. Partial output delivered.",
                    "transitions_applied": transitions_applied,
                    "stop_reason": self.stop_reason,
                }

            if self.stop_reason == "rate_limited":
                logger.warning(
                    f"{self._log_prefix} Worker stopped due to rate_limited — steering with backoff"
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
                        "Rate limited by the API. Wait briefly, then resume where you left off. "
                        "Do not restart from scratch."
                    ),
                    "transitions_applied": transitions_applied,
                    "stop_reason": self.stop_reason,
                }

            # Unknown stop_reason — log and fall through to LLM
            logger.info(
                f"{self._log_prefix} Unknown stop_reason={self.stop_reason}, "
                f"falling through to LLM Observer"
            )

        # Phase 1.75: Deterministic SDLC stage guard
        # If this is an SDLC session with remaining stages, ALWAYS steer to the
        # next stage. The LLM Observer must not override stage tracking — this was
        # the root cause of SDLC flows stalling before reaching do-docs.
        #
        # Safety: Do NOT force-steer when:
        # - stop_reason is "fail" or "budget_exceeded" (must deliver to human)
        # - A stage has failed (has_failed_stage) — human needs to see the failure
        # - auto_continue_count >= max_continues (cap reached, deliver to human)
        # - Worker output signals it needs human input (questions, fatal errors)
        has_failed = self.session.has_failed_stage()
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
            next_stage_info = _next_sdlc_skill(self.session)
            if next_stage_info:
                stage_name, skill_cmd = next_stage_info
                cid = getattr(self.session, "correlation_id", None) or "unknown"
                coaching = (
                    f"Pipeline has remaining stages. Next: {stage_name}. "
                    f"Continue with {skill_cmd}. "
                    f"If you encounter a critical blocker requiring human input, "
                    f"state it clearly. Otherwise, press forward."
                )
                logger.info(
                    f"{self._log_prefix} Deterministic SDLC guard: forcing steer "
                    f"to {stage_name} ({skill_cmd}) — remaining stages exist, "
                    f"no failures, stop_reason={self.stop_reason}"
                )
                record_decision(
                    self.session.session_id,
                    cid,
                    "steer",
                    f"deterministic-sdlc-guard: {stage_name} pending",
                )
                return {
                    "action": "steer",
                    "coaching_message": coaching,
                    "transitions_applied": transitions_applied,
                    "deterministic_guard": True,
                }

        # Log when guard was bypassed due to safety conditions
        bypassed = has_failed or stop_is_terminal or cap_reached or needs_human
        if is_sdlc and has_remaining and bypassed:
            logger.info(
                f"{self._log_prefix} Deterministic SDLC guard bypassed: "
                f"has_failed={has_failed}, stop_reason={self.stop_reason}, "
                f"cap_reached={cap_reached}, needs_human={needs_human} "
                f"— falling through to LLM Observer"
            )

            # Enforcement point 2: even when the deterministic guard is bypassed
            # (e.g., needs_human), check if REVIEW/DOCS gates are unsatisfied.
            # If the "question" is from a stage before REVIEW, still steer.
            if needs_human and not has_failed and not stop_is_terminal and not cap_reached:
                gate_override = self._check_mandatory_gates()
                if gate_override:
                    gate_override["transitions_applied"] = transitions_applied
                    logger.info(
                        f"{self._log_prefix} Gate enforcement overrides guard bypass: "
                        f"steering to {gate_override.get('unsatisfied_gate', 'unknown')}"
                    )
                    return gate_override

        # Phase 2: Run the Observer LLM for judgment calls
        try:
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
            message_for_user = None

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
                        f"{self._log_prefix} Iteration {iteration + 1}/{MAX_TOOL_ITERATIONS}: "
                        f"tool={tool_use.name}, result={result_preview}"
                    )
                    cid = getattr(self.session, "correlation_id", None) or "unknown"
                    record_tool_use(self.session.session_id, cid, tool_use.name)
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
                        if result_data.get("message_for_user"):
                            message_for_user = result_data["message_for_user"]

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

            cid = getattr(self.session, "correlation_id", None) or "unknown"
            if self._action_taken == "steer":
                reason_preview = (coaching_message or "continue")[:120]
                logger.info(f"{self._log_prefix} Decision: steer (reason: {reason_preview})")
                record_decision(self.session.session_id, cid, "steer", reason_preview)
                return {
                    "action": "steer",
                    "coaching_message": coaching_message or "continue",
                    "transitions_applied": transitions_applied,
                }
            else:
                # Enforcement point 3: LLM decided to deliver — check mandatory gates
                gate_override = self._check_mandatory_gates()
                if gate_override:
                    gate_override["transitions_applied"] = transitions_applied
                    logger.info(
                        f"{self._log_prefix} Gate enforcement overrides LLM deliver: "
                        f"steering to {gate_override.get('unsatisfied_gate', 'unknown')}"
                    )
                    return gate_override

                reason_preview = (deliver_reason or "Observer decided to deliver")[:120]
                logger.info(f"{self._log_prefix} Decision: deliver (reason: {reason_preview})")
                record_decision(self.session.session_id, cid, "deliver", reason_preview)
                result = {
                    "action": "deliver",
                    "reason": deliver_reason or "Observer decided to deliver",
                    "transitions_applied": transitions_applied,
                }
                if message_for_user:
                    result["message_for_user"] = message_for_user
                return result

        except Exception as e:
            cid = getattr(self.session, "correlation_id", None) or "unknown"
            logger.error(f"{self._log_prefix} Observer failed: {e}", exc_info=True)
            record_decision(self.session.session_id, cid, "error", str(e))
            return {
                "action": "deliver",
                "reason": f"Observer error: {e}",
                "transitions_applied": transitions_applied,
                "error": str(e),
            }
