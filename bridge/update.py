"""Telegram /update command handlers.

Handles both normal (/update) and force (/update --force) updates.
Normal updates pull code and set a restart flag for graceful restart.
Force updates flush the queue, kill running sessions, and restart immediately.
"""

import logging
import platform
import subprocess
from pathlib import Path

from bridge.response import send_response_with_files, set_reaction

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


def _get_running_jobs_info() -> tuple[int, list[str]]:
    """Check for running sessions across all projects. Returns (count, descriptions)."""
    try:
        from models.agent_session import AgentSession

        running_jobs = AgentSession.query.filter(status="running")
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
        logger.warning(f"Failed to check running sessions: {e}")
        return 0, []


async def handle_update_command(tg_client, event):
    """Run remote update script and send results as standalone message.

    Pulls code and syncs deps but does NOT restart the bridge.
    If code changed, writes a restart flag that the session queue picks up
    between jobs for a graceful restart when idle.
    """
    machine = _get_machine_name()
    logger.info(f"[update] /update received from chat {event.chat_id}")
    try:
        await set_reaction(tg_client, event.chat_id, event.message.id, "👀")
    except Exception:
        pass

    # Check for running sessions before update
    running_count, running_descriptions = _get_running_jobs_info()
    sessions_notice = ""
    if running_count > 0:
        sessions_notice = (
            f"\n\n⚠️ {running_count} session(s) currently running:\n"
            + "\n".join(running_descriptions)
            + "\n\nRestart will be queued until all sessions complete."
        )
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
        output = result.stdout.strip() or result.stderr.strip() or "(no output)"
        if result.returncode != 0 and result.stderr.strip():
            output += f"\n\nSTDERR:\n{result.stderr.strip()}"

        output += sessions_notice
        output = f"{machine} - {output}"

        # Standalone message (no reply-to) so multi-instance responses
        # don't create a messy reply chain
        await send_response_with_files(
            tg_client,
            event=None,
            response=output,
            chat_id=event.chat_id,
            reply_to=None,
        )
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
    await send_response_with_files(
        tg_client,
        event=None,
        response=summary,
        chat_id=event.chat_id,
        reply_to=None,
    )
