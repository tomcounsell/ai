"""Telegram /update command handlers.

Handles both normal (/update) and force (/update --force) updates.
Normal updates pull code and set a restart flag for graceful restart.
Force updates flush the queue, kill running sessions, and restart immediately.
"""

import logging
import platform
import subprocess
import uuid
from pathlib import Path

from bridge.response import set_reaction

_PROJECT_DIR = Path(__file__).parent.parent

logger = logging.getLogger(__name__)


def _get_machine_name() -> str:
    """Get the macOS Computer Name, falling back to hostname."""
    try:
        result = subprocess.run(
            ["scutil", "--get", "ComputerName"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return platform.node().split(".")[0]


def _get_running_sessions_info() -> tuple[int, list[str]]:
    """Check for running sessions across all projects. Returns (count, descriptions)."""
    try:
        from models.agent_session import AgentSession

        running_sessions = AgentSession.query.filter(status="running")
        if not running_sessions:
            return 0, []

        descriptions = []
        for entry in running_sessions:
            msg_preview = (entry.message_text or "")[:50]
            if len(entry.message_text or "") > 50:
                msg_preview += "..."
            descriptions.append(f"  • [{entry.project_key}] {msg_preview}")

        return len(running_sessions), descriptions

    except Exception as e:
        logger.warning(f"Failed to check running sessions: {e}")
        return 0, []


async def _queue_fix_session(event, machine: str, stdout: str, stderr: str, failed: bool) -> None:
    """Queue an agent session to diagnose and fix update issues."""
    try:
        from agent.agent_session_queue import enqueue_agent_session

        problem = "Update failed" if failed else "Update has warnings"
        message = (
            f"/update fix: {problem} on {machine}.\n"
            f"stdout:\n{stdout[:500]}\n"
            f"stderr:\n{stderr[:500]}\n\n"
            "Diagnose and fix the issue. Use the /update skill."
        )
        session_id = f"update_fix_{uuid.uuid4().hex[:8]}"
        await enqueue_agent_session(
            project_key="ai",
            session_id=session_id,
            working_dir=str(_PROJECT_DIR),
            message_text=message,
            sender_name="system",
            chat_id=str(event.chat_id),
            telegram_message_id=event.message.id,
            priority="low",
        )
        logger.info(f"[update] Queued fix session {session_id}")
    except Exception as e:
        logger.warning(f"[update] Failed to queue fix session: {e}")


async def handle_update_command(tg_client, event):
    """Run remote update script and send results as standalone message.

    Pulls code and syncs deps but does NOT restart the bridge.
    If code changed, writes a restart flag that the session queue picks up
    between sessions for a graceful restart when idle.
    """
    machine = _get_machine_name()
    logger.info(f"[update] /update received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "👀")
    except Exception:
        pass

    # Check for running sessions (affects restart behavior)
    running_count, _ = _get_running_sessions_info()
    if running_count > 0:
        logger.info(f"[update] {running_count} session(s) running, restart queued")

    script_path = _PROJECT_DIR / "scripts" / "remote-update.sh"
    if not script_path.exists():
        await tg_client.send_message(
            event.chat_id,
            f"{machine} - scripts/remote-update.sh not found",
        )
        return

    try:
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Extract short status from first line of stdout (skip <<FILE:>> markers)
        status_lines = [
            line
            for line in (stdout or "").split("\n")
            if line.strip() and not line.strip().startswith("<<FILE:")
        ]
        status = status_lines[0] if status_lines else "(no output)"

        failed = result.returncode != 0
        if failed:
            status = f"update failed: {stderr.split(chr(10))[0]}" if stderr else status

        if running_count > 0:
            plural = "s" if running_count != 1 else ""
            status += f" ({running_count} session{plural} running; restart queued)"

        # If update had warnings or failed, queue agent session to fix
        has_warnings = "warning" in status.lower()
        if failed or has_warnings:
            status += " — spawning agent session to fix"
            await _queue_fix_session(event, machine, stdout, stderr, failed)

        await tg_client.send_message(event.chat_id, f"{machine} - {status}")
    except subprocess.TimeoutExpired:
        await tg_client.send_message(
            event.chat_id,
            f"{machine} - update timed out after 120s",
        )
    except Exception as e:
        logger.error(f"[update] /update failed: {e}")
        await tg_client.send_message(
            event.chat_id,
            f"{machine} - update failed: {e}",
        )


async def handle_force_update_command(tg_client, event):
    """Force update: flush queue, kill running sessions, update, restart.

    Unlike normal /update which waits for running sessions to finish,
    this immediately kills everything and applies the update.
    """
    machine = _get_machine_name()
    logger.info(f"[update] /update --force received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "🔥")
    except Exception:
        pass

    steps = []

    # 1. Flush pending sessions from queue
    try:
        from models.agent_session import AgentSession

        pending = AgentSession.query.filter(status="pending")
        running = AgentSession.query.filter(status="running")
        pending_count = len(pending) if pending else 0
        running_count = len(running) if running else 0

        for entry in pending or []:
            try:
                entry.delete()
            except Exception:
                pass
        for entry in running or []:
            try:
                entry.delete()
            except Exception:
                pass

        steps.append(f"Flushed queue: {pending_count} pending, {running_count} running")
    except Exception as e:
        steps.append(f"Queue flush failed: {e}")

    # 2. Run update (git pull + dep sync)
    script_path = _PROJECT_DIR / "scripts" / "update" / "run.py"
    python_path = _PROJECT_DIR / ".venv" / "bin" / "python"
    try:
        result = subprocess.run(
            [str(python_path), str(script_path), "--full"],
            cwd=str(_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        output_lines = (result.stdout or "").strip().split("\n")
        for line in output_lines:
            if any(k in line for k in ["commit", "Already up to date", "FAIL", "ERROR"]):
                steps.append(line.strip().removeprefix("[update] "))
    except subprocess.TimeoutExpired:
        steps.append("Update timed out after 120s")
    except Exception as e:
        steps.append(f"Update failed: {e}")

    # 3. Bridge restart is handled by --full (service.install_service)
    steps.append("Bridge restarted")

    summary = f"{machine} - force update complete:\n" + "\n".join(f"  • {s}" for s in steps)
    await tg_client.send_message(event.chat_id, summary)
