"""Agent response routing, retry logic, self-healing, and tracked work detection."""

import asyncio
import json
import logging
import os
import platform
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

# Feature flag for Claude Agent SDK migration
USE_CLAUDE_SDK = os.getenv("USE_CLAUDE_SDK", "false").lower() == "true"

if USE_CLAUDE_SDK:
    from agent import get_agent_response_sdk
    from agent.workflow_state import WorkflowState, generate_workflow_id

# Module-level config globals (set by telegram_bridge.py after config loading)
CONFIG = {}
DEFAULTS = {}

# Bridge project directory
_BRIDGE_PROJECT_DIR = Path(__file__).parent.parent

logger = logging.getLogger(__name__)


# =============================================================================
# Job Queue Integration
# =============================================================================


def _get_running_jobs_info() -> tuple[int, list[str]]:
    """Check for running jobs across all projects. Returns (count, descriptions).

    Note: This is a point-in-time check for user visibility only. The actual
    restart timing is handled by the job queue's restart flag system, which
    checks between jobs. Sessions may finish before the restart actually occurs.
    """
    try:
        from agent.job_queue import RedisJob

        running_jobs = RedisJob.query.filter(status="running")
        if not running_jobs:
            return 0, []

        descriptions = []
        for job in running_jobs:
            msg_preview = (job.message_text or "")[:50]
            if len(job.message_text or "") > 50:
                msg_preview += "..."
            descriptions.append(f"  • [{job.project_key}] {msg_preview}")

        return len(running_jobs), descriptions

    except Exception as e:
        # Redis unavailable or query failed - degrade gracefully
        logger.warning(f"Failed to check running jobs: {e}")
        return 0, []


# =============================================================================
# Update Command Handler
# =============================================================================


async def _handle_update_command(tg_client, event):
    """Run remote update script and reply with results.

    The script pulls code and syncs deps but does NOT restart the bridge.
    If code changed, it writes a restart flag that the job queue picks up
    between jobs for a graceful restart when idle.

    If sessions are currently running, notifies the user that the restart
    will be queued until all work completes.
    """
    # Import here to avoid circular dependency
    from bridge.response import send_response_with_files, set_reaction

    machine = platform.node().split(".")[0]  # e.g. "toms-macbook-pro"
    logger.info(f"[bridge] /update command received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "👀")
    except Exception:
        pass  # Reaction is nice-to-have

    # Check for running sessions before update
    running_count, running_descriptions = _get_running_jobs_info()
    sessions_notice = ""
    if running_count > 0:
        sessions_notice = (
            f"\n\n⚠️ {running_count} session(s) currently running:\n"
            + "\n".join(running_descriptions)
            + "\n\nRestart will be queued until all sessions complete."
        )
        logger.info(
            f"[bridge] /update: {running_count} session(s) running, restart will be queued"
        )

    script_path = _BRIDGE_PROJECT_DIR / "scripts" / "remote-update.sh"
    if not script_path.exists():
        await tg_client.send_message(
            event.chat_id,
            f"[{machine}] scripts/remote-update.sh not found.",
            reply_to=event.message.id,
        )
        return

    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(_BRIDGE_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n\nSTDERR:\n{result.stderr.strip()}"

        # Append sessions notice if any were running
        output += sessions_notice

        # Prepend machine name for multi-instance identification
        output = f"[{machine}] {output}"

        # Send via send_response_with_files so <<FILE:>> markers get
        # parsed and the log file is uploaded as an attachment
        await send_response_with_files(
            tg_client,
            event=None,
            response=output,
            chat_id=event.chat_id,
            reply_to=event.message.id,
        )
    except subprocess.TimeoutExpired:
        await tg_client.send_message(
            event.chat_id,
            f"[{machine}] Update timed out after 120s",
            reply_to=event.message.id,
        )
    except Exception as e:
        logger.error(f"[bridge] /update command failed: {e}")
        await tg_client.send_message(
            event.chat_id,
            f"[{machine}] Update failed: {e}",
            reply_to=event.message.id,
        )


async def _handle_force_update_command(tg_client, event):
    """Force update: flush queue, kill running jobs, update, restart.

    Unlike normal /update which waits for running jobs to finish,
    this immediately kills everything and applies the update.
    """
    from bridge.response import send_response_with_files, set_reaction

    machine = platform.node().split(".")[0]
    logger.info(f"[bridge] /update --force received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "🔥")
    except Exception:
        pass

    steps = []

    # 1. Flush pending jobs from queue
    try:
        from agent.job_queue import RedisJob

        pending = RedisJob.query.filter(status="pending")
        running = RedisJob.query.filter(status="running")
        pending_count = len(pending) if pending else 0
        running_count = len(running) if running else 0

        for job in pending or []:
            try:
                job.delete()
            except Exception:
                pass
        for job in running or []:
            try:
                job.delete()
            except Exception:
                pass

        steps.append(f"Flushed queue: {pending_count} pending, {running_count} running")
    except Exception as e:
        steps.append(f"Queue flush failed: {e}")

    # 2. Run update (git pull + dep sync)
    script_path = _BRIDGE_PROJECT_DIR / "scripts" / "update" / "run.py"
    python_path = _BRIDGE_PROJECT_DIR / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python_path), str(script_path), "--full"],
            cwd=str(_BRIDGE_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        # Extract key info from output
        output_lines = (result.stdout or "").strip().split("\n")
        for line in output_lines:
            if any(
                k in line for k in ["commit", "Already up to date", "FAIL", "ERROR"]
            ):
                steps.append(line.strip().removeprefix("[update] "))
    except subprocess.TimeoutExpired:
        steps.append("Update timed out after 120s")
    except Exception as e:
        steps.append(f"Update failed: {e}")

    # 3. Bridge restart is handled by the --full update above (service.install_service)
    # Just report what happened
    steps.append("Bridge restarted")

    summary = f"[{machine}] Force update complete:\n" + "\n".join(
        f"  • {s}" for s in steps
    )
    await send_response_with_files(
        tg_client,
        event=None,
        response=summary,
        chat_id=event.chat_id,
        reply_to=event.message.id,
    )


# =============================================================================
# Agent Response Functions
# =============================================================================


async def get_agent_response_clawdbot(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
) -> str:
    """Call clawdbot agent and get response (legacy implementation)."""
    # Import here to avoid circular dependency
    from bridge.context import (
        build_activity_context,
        build_context_prefix,
        is_status_question,
    )
    from bridge.telegram_bridge import log_event

    start_time = time.time()
    request_id = f"{session_id}_{int(start_time)}"

    # CRITICAL: Determine working directory to prevent agent from wandering into wrong directories
    if project:
        working_dir = project.get(
            "working_directory", DEFAULTS.get("working_directory")
        )
    else:
        working_dir = DEFAULTS.get("working_directory")

    # Fallback to current directory if not configured (shouldn't happen)
    if not working_dir:
        working_dir = str(Path(__file__).parent.parent)
        logger.warning(
            f"[{request_id}] No working_directory configured, using {working_dir}"
        )

    try:
        # Build context-enriched message (includes user permission restrictions)
        context = build_context_prefix(project, chat_title is None, sender_id)

        # Note: Recent conversation history is NOT injected by default.
        # The agent should use valor-history CLI to fetch relevant context
        # when subtle cues suggest prior messages may be relevant.
        # Users can also use Telegram's reply-to feature for explicit threading.

        # Check if this is a status question - inject activity context
        activity_context = ""
        if is_status_question(message):
            activity_context = build_activity_context(working_dir)
            logger.debug(
                f"[{request_id}] Status question detected, injecting activity context"
            )

        enriched_message = context
        if activity_context:
            enriched_message += f"\n\n{activity_context}"
        enriched_message += f"\n\nFROM: {sender_name}"
        if chat_title:
            enriched_message += f" in {chat_title}"
        enriched_message += f"\nMESSAGE: {message}"

        project_name = project.get("name", "Valor") if project else "Valor"

        # Use subprocess to call clawdbot agent
        # Use --json to get clean output without tool execution logs mixed in
        cmd = [
            "clawdbot",
            "agent",
            "--local",
            "--session-id",
            session_id,
            "--message",
            enriched_message,
            "--thinking",
            "medium",
            "--json",
        ]

        # Log full request details
        logger.info(f"[{request_id}] Calling clawdbot agent for {project_name}")
        logger.debug(f"[{request_id}] Session: {session_id}")
        logger.debug(f"[{request_id}] Working directory: {working_dir}")
        logger.debug(f"[{request_id}] Command: {' '.join(cmd[:6])}...")
        logger.debug(f"[{request_id}] Enriched message:\n{enriched_message}")

        # Log structured event
        log_event(
            "agent_request",
            request_id=request_id,
            session_id=session_id,
            project=project_name,
            working_dir=working_dir,
            sender=sender_name,
            chat=chat_title,
            message_length=len(message),
            enriched_length=len(enriched_message),
        )

        timeout = DEFAULTS.get("response", {}).get("timeout_seconds", 300)

        # Run with timeout - CRITICAL: cwd ensures agent works in correct project directory
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")},
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            # Kill the process and try to capture partial output
            elapsed = time.time() - start_time
            logger.error(f"[{request_id}] Agent request timed out after {elapsed:.1f}s")

            # Try to terminate gracefully first
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()

            # Log structured timeout event
            log_event(
                "agent_timeout",
                request_id=request_id,
                session_id=session_id,
                elapsed_seconds=elapsed,
                timeout_seconds=timeout,
            )

            return "Request timed out. Please try again."

        elapsed = time.time() - start_time

        if process.returncode != 0:
            stderr_text = stderr.decode()
            logger.error(
                f"[{request_id}] Clawdbot error (exit {process.returncode}) after {elapsed:.1f}s"
            )
            logger.error(f"[{request_id}] Stderr: {stderr_text[:500]}")

            log_event(
                "agent_error",
                request_id=request_id,
                session_id=session_id,
                exit_code=process.returncode,
                elapsed_seconds=elapsed,
                stderr_preview=stderr_text[:200],
            )

            return f"Error processing request: {stderr_text[:200]}"

        raw_output = stdout.decode().strip()
        stderr_text = stderr.decode().strip()

        # Parse JSON response from clawdbot --json mode
        # Structure: {"payloads": [{"text": "...", "mediaUrl": null}], "meta": {...}}
        try:
            result = json.loads(raw_output)
            payloads = result.get("payloads", [])
            if payloads and payloads[0].get("text"):
                response = payloads[0]["text"]
            else:
                # Fallback to raw output if JSON parsing succeeds but no text
                response = raw_output
                logger.warning(f"[{request_id}] JSON response had no text payload")
        except json.JSONDecodeError:
            # Fallback to raw output if not valid JSON (shouldn't happen with --json)
            response = raw_output
            logger.warning(
                f"[{request_id}] Failed to parse JSON response, using raw output"
            )

        # Log success with timing
        logger.info(
            f"[{request_id}] Agent responded in {elapsed:.1f}s ({len(response)} chars)"
        )
        logger.debug(f"[{request_id}] Response preview: {response[:200]}...")
        if stderr_text:
            logger.debug(f"[{request_id}] Stderr: {stderr_text[:200]}")

        log_event(
            "agent_response",
            request_id=request_id,
            session_id=session_id,
            elapsed_seconds=elapsed,
            response_length=len(response),
            has_stderr=bool(stderr_text),
        )

        return response

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[{request_id}] Error calling agent after {elapsed:.1f}s: {e}")
        logger.exception(f"[{request_id}] Full traceback:")

        log_event(
            "agent_exception",
            request_id=request_id,
            session_id=session_id,
            elapsed_seconds=elapsed,
            error=str(e),
            error_type=type(e).__name__,
        )

        return f"Error: {str(e)}"


async def get_agent_response(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    sender_id: int | None = None,
) -> str:
    """
    Route to appropriate agent backend based on USE_CLAUDE_SDK flag.

    When USE_CLAUDE_SDK=true, uses the Claude Agent SDK directly.
    Otherwise, uses the legacy clawdbot subprocess approach.
    """
    if USE_CLAUDE_SDK:
        logger.debug(f"Using Claude Agent SDK for session {session_id}")
        return await get_agent_response_sdk(
            message, session_id, sender_name, chat_title, project, chat_id, sender_id
        )
    else:
        return await get_agent_response_clawdbot(
            message, session_id, sender_name, chat_title, project, chat_id, sender_id
        )


# =============================================================================
# Retry with Self-Healing (Legacy - for Clawdbot backend)
# =============================================================================

# How long to wait before sending "I'm working on this" acknowledgment
# Only sends if no message has been sent to the chat yet
ACKNOWLEDGMENT_TIMEOUT_SECONDS = 180  # 3 minutes

# Message to send when work is taking a while
ACKNOWLEDGMENT_MESSAGE = "I'm working on this."

# Retry configuration
MAX_RETRIES = 3
RETRY_DELAYS = [5, 15, 30]  # Seconds between retries


async def attempt_self_healing(error: str, session_id: str) -> None:
    """
    Attempt to fix the cause of failure before retry.

    This runs basic diagnostics and cleanup to improve retry success.
    """
    logger.info(f"Attempting self-healing for session {session_id}: {error[:100]}")

    try:
        # Kill any stuck clawdbot processes
        kill_result = await asyncio.create_subprocess_exec(
            "pkill",
            "-f",
            "clawdbot agent",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(kill_result.wait(), timeout=5)
        logger.debug("Killed stuck clawdbot processes")
    except Exception as e:
        logger.debug(f"No stuck processes to kill: {e}")

    # Brief pause to let processes terminate
    await asyncio.sleep(1)


async def create_failure_plan(message: str, error: str, session_id: str) -> None:
    """
    Create a plan doc for failures that couldn't be self-healed.

    Instead of showing errors to the user, we document them for later review.
    """
    # Import here to avoid circular dependency
    from bridge.telegram_bridge import log_event

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    plan_path = (
        Path(__file__).parent.parent
        / "docs"
        / "plans"
        / f"fix-bridge-failure-{timestamp}.md"
    )

    # Ensure plans directory exists
    plan_path.parent.mkdir(parents=True, exist_ok=True)

    content = f"""# Fix Bridge Failure

**Status**: Todo
**Created**: {datetime.now().strftime("%Y-%m-%d %H:%M")}
**Session**: {session_id}

## Error
{error}

## Original Message
{message[:500]}{"..." if len(message) > 500 else ""}

## Investigation Needed
- [ ] Review logs for this session
- [ ] Identify root cause
- [ ] Implement fix
- [ ] Test with similar message
"""

    plan_path.write_text(content)
    logger.info(f"Created failure plan: {plan_path.name}")

    # Log structured event
    log_event(
        "failure_plan_created",
        session_id=session_id,
        plan_file=plan_path.name,
        error_preview=error[:200],
    )


async def get_agent_response_with_retry(
    message: str,
    session_id: str,
    sender_name: str,
    chat_title: str | None,
    project: dict | None,
    chat_id: str | None = None,
    client=None,
    msg_id: int | None = None,
    sender_id: int | None = None,
) -> str:
    """
    Call agent with retry and self-healing on failure.

    On timeout or error:
    1. Attempt self-healing (kill stuck processes)
    2. Wait with progressive backoff
    3. Retry up to MAX_RETRIES times

    If all retries fail, create a plan doc instead of showing error to user.
    """
    # Import here to avoid circular dependency
    from bridge.response import filter_tool_logs, set_reaction

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            # Update reaction to show retry attempt
            # Note: 🔄 is not a valid Telegram reaction, use 🔥 (fire/trying hard) instead
            if attempt > 0 and client and msg_id:
                await set_reaction(client, int(chat_id) if chat_id else 0, msg_id, "🔥")
                logger.info(f"Retry attempt {attempt + 1}/{MAX_RETRIES}")

            response = await get_agent_response(
                message,
                session_id,
                sender_name,
                chat_title,
                project,
                chat_id,
                sender_id,
            )

            # Check if response looks like an error
            if response.startswith("Error:") or response.startswith(
                "Request timed out"
            ):
                last_error = response
                if attempt < MAX_RETRIES - 1:
                    await attempt_self_healing(response, session_id)
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue

            # Check if response is just tool logs (will be filtered to empty)
            filtered = filter_tool_logs(response)
            if not filtered and response:
                # Response was just logs - could indicate an issue
                last_error = "Response contained only tool logs"
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAYS[attempt])
                    continue

            return response

        except TimeoutError:
            last_error = "timeout"
            if attempt < MAX_RETRIES - 1:
                await attempt_self_healing("timeout", session_id)
                await asyncio.sleep(RETRY_DELAYS[attempt])

        except Exception as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                await attempt_self_healing(str(e), session_id)
                await asyncio.sleep(RETRY_DELAYS[attempt])

    # All retries failed - create plan doc for future fix
    await create_failure_plan(message, last_error or "Unknown error", session_id)

    # Return empty response - reaction will indicate status
    return ""


# =============================================================================
# Tracked Work Detection
# =============================================================================


def _get_github_repo_url(working_dir: str) -> str | None:
    """Get GitHub repo URL from git remote."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=working_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            # Convert git@github.com:user/repo.git to https://github.com/user/repo
            if url.startswith("git@github.com:"):
                url = url.replace("git@github.com:", "https://github.com/")
            # Remove .git suffix
            if url.endswith(".git"):
                url = url[:-4]
            return url
    except Exception:
        pass
    return None


def _match_plan_by_name(message_text: str, working_dir: str) -> str | None:
    """
    Match plan files by natural language name.

    Examples:
        "workflow state persistence plan" -> docs/plans/workflow-state-persistence.md
        "the auth system plan" -> docs/plans/auth-system.md
        "issue classification" -> docs/plans/issue-classification-commands.md
    """
    plans_dir = Path(working_dir) / "docs" / "plans"
    if not plans_dir.exists():
        return None

    plan_files = list(plans_dir.glob("*.md"))
    if not plan_files:
        return None

    message_lower = message_text.lower()

    # First try exact .md filename match
    plan_pattern = r"(?:docs/plans/)?([a-z0-9-]+)\.md"
    plan_match = re.search(plan_pattern, message_lower)
    if plan_match:
        plan_name = plan_match.group(1)
        plan_path = plans_dir / f"{plan_name}.md"
        if plan_path.exists():
            return f"docs/plans/{plan_name}.md"

    # Try natural language matching
    best_match = None
    best_score = 0

    for plan_file in plan_files:
        # Convert filename to words:
        # "workflow-state-persistence.md" -> ["workflow", "state", "persistence"]
        plan_name = plan_file.stem  # without .md
        plan_words = plan_name.replace("-", " ").split()

        # Count how many plan words appear in the message
        matches = sum(1 for word in plan_words if word in message_lower)

        # Require at least 2 matching words (or all words if plan name is short)
        min_required = min(2, len(plan_words))
        if matches >= min_required and matches > best_score:
            best_score = matches
            best_match = f"docs/plans/{plan_name}.md"

    return best_match


def _detect_issue_number(message_text: str, working_dir: str) -> str | None:
    """
    Detect issue number references and convert to full GitHub URL.

    Matches: #55, issue 55, issue #55, issue-55, issue55
    """
    # Patterns for issue references
    patterns = [
        r"#(\d+)",  # #55
        r"issue\s*#?(\d+)",  # issue 55, issue #55, issue55
        r"issue-(\d+)",  # issue-55
    ]

    for pattern in patterns:
        match = re.search(pattern, message_text.lower())
        if match:
            issue_num = match.group(1)
            repo_url = _get_github_repo_url(working_dir)
            if repo_url:
                return f"{repo_url}/issues/{issue_num}"

    return None


def detect_tracked_work(
    message_text: str, working_dir: str
) -> tuple[str | None, str | None]:
    """
    Detect if message references tracked work (plan file + tracking URL).

    Workflows are only created for tracked work that has both:
    - A plan document in docs/plans/*.md
    - A tracking issue (GitHub) or task (Notion)

    Detection is smart about natural language:
    - "issue 55" or "#55" -> expands to full GitHub URL
    - "workflow state plan" -> matches docs/plans/workflow-state-persistence.md

    Args:
        message_text: The message text to analyze
        working_dir: Working directory to check for plan files

    Returns:
        Tuple of (plan_file, tracking_url) or (None, None) if not tracked work
    """
    # Detect plan file (supports natural language matching)
    plan_file = _match_plan_by_name(message_text, working_dir)

    # Detect tracking URL
    tracking_url = None

    # First try full URLs
    github_pattern = r"https://github\.com/[^/]+/[^/]+/issues/\d+"
    notion_pattern = r"https://www\.notion\.so/[^\s]+"

    github_match = re.search(github_pattern, message_text)
    notion_match = re.search(notion_pattern, message_text)

    if github_match:
        tracking_url = github_match.group(0)
    elif notion_match:
        tracking_url = notion_match.group(0)
    else:
        # Try issue number shorthand (#55, issue 55, etc.)
        tracking_url = _detect_issue_number(message_text, working_dir)

    # Only return if we have BOTH plan file and tracking URL
    if plan_file and tracking_url:
        return plan_file, tracking_url

    return None, None


def create_workflow_for_tracked_work(
    message_text: str,
    working_dir: str,
    chat_id: str | None,
) -> str | None:
    """
    Create workflow state for tracked work if detected.

    Args:
        message_text: The message text to analyze
        working_dir: Working directory to check for plan files
        chat_id: Telegram chat ID for notifications

    Returns:
        workflow_id if workflow created, None otherwise
    """
    if not USE_CLAUDE_SDK:
        return None

    plan_file, tracking_url = detect_tracked_work(message_text, working_dir)

    if not plan_file or not tracking_url:
        return None

    try:
        workflow_id = generate_workflow_id()
        workflow = WorkflowState(workflow_id)

        # Initialize workflow state
        workflow.update(
            plan_file=plan_file,
            tracking_url=tracking_url,
            telegram_chat_id=int(chat_id) if chat_id else None,
        )

        # Save with initial phase
        workflow.save(phase="plan")

        logger.info(
            f"Created workflow {workflow_id} for tracked work: {plan_file} -> {tracking_url}"
        )

        return workflow_id

    except Exception as e:
        logger.error(f"Failed to create workflow state: {e}")
        return None
