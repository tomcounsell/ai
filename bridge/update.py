"""Telegram /update command handlers.

Handles both normal (/update) and force (/update --force) updates.
Normal updates run remote-update.sh, which restarts the worker (and, on
bridge-relevant changes, the bridge itself — killing this process, in which
case the fresh bridge's boot flush reports instead). Force updates flush the
queue, kill running sessions, and restart immediately via run.py --full.

Issue #1898: the ``✅ update OK`` report is gated on a release verify — the
running bridge/worker must have no process-relevant commits behind pulled
HEAD before OK is printed.
"""

import asyncio
import json
import logging
import os
import platform
import subprocess
import time
import uuid
from pathlib import Path

from bridge.response import set_reaction

_PROJECT_DIR = Path(__file__).parent.parent

# Bounded beacon poll (Race 1 mitigation, issue #1898 Decision 20): 15 x 2s
# (30s), matching run.py's worker-heartbeat freshness poll.
UPDATE_POLL_ATTEMPTS = 15
UPDATE_POLL_INTERVAL_SECONDS = 2

# Shell subprocess budget: the historical 120s shell allowance plus the full
# 30s verify-poll window the shell's terminal verify_release step may burn —
# a near-limit worker-relevant update must not TimeoutExpired despite
# succeeding.
UPDATE_SHELL_TIMEOUT_SECONDS = 120 + UPDATE_POLL_ATTEMPTS * UPDATE_POLL_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


def _bridge_plist_exists() -> bool:
    """True when the bridge plist is installed on this machine (best-effort)."""
    try:
        from scripts.update.service import BRIDGE_PLIST_PATH

        return BRIDGE_PLIST_PATH.exists()
    except Exception:  # swallow-ok: plist probe is best-effort; absence handled by caller
        return False


async def _verify_release_after_update(
    subprocess_start_ts: float, worker_restarted: bool = True
) -> dict:
    """Poll for a fresh worker beacon, then classify the running releases.

    Race 1 mitigation on the inline (no-bridge-restart) path: a worker
    kickstarted by remote-update.sh may still be booting when the shell
    returns, so wait (bounded 15 x 2s) for its beacon to freshen past the
    subprocess start moment — or exhaustion, after which the pre-restart
    beacon still classifies correctly (matches on a no-op cycle, stale when
    process-relevant commits landed).

    ``worker_restarted=False`` skips the poll entirely (the shell's
    ``--since 0`` principle): when no worker restart happened this cycle the
    beacon can never freshen past ``subprocess_start_ts``, so polling would
    burn the full 30s window on every no-op /update for nothing — classify
    the existing beacon directly. Raises on import/verify errors; the caller
    degrades gracefully to the shell result.
    """
    from scripts.update.git import get_short_sha
    from scripts.update.service import read_boot_beacon, verify_running_release
    from scripts.update.verify import check_machine_identity

    if worker_restarted:
        beacon_path = _PROJECT_DIR / "data" / "worker_boot_sha"
        for _ in range(UPDATE_POLL_ATTEMPTS):
            beacon = read_boot_beacon(beacon_path)
            if beacon is not None and beacon[1] > subprocess_start_ts:
                break
            await asyncio.sleep(UPDATE_POLL_INTERVAL_SECONDS)
    head_short = get_short_sha(_PROJECT_DIR)
    machine_check = check_machine_identity(_PROJECT_DIR)
    return verify_running_release(_PROJECT_DIR, head_short, machine_check)


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
    """Run remote-update.sh and send the release-verified result (#1898).

    The shell pulls code, restarts the worker on a worker-relevant diff, and —
    on a bridge-relevant diff with the bridge plist installed — kickstarts the
    bridge as its FINAL act, which SIGKILLs this coroutine by process-group
    semantics. On that path the fresh bridge's boot flush
    (:func:`run_boot_release_check`) is the reporter, replying via the chat
    context exported into the shell env. When the shell returns (worker-only /
    no-op update), the ``✅`` is gated on a release verify: shell exit 0 AND
    no in-role process running positively-stale code; a stale process reports
    ``❌ update FAILED`` naming its lagging short-SHA, with per-process reload
    state appended.
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

    # Interim notice (issue #1898, Decision 21): on a bridge-plist machine a
    # bridge-relevant update kickstarts the bridge, SIGKILLing this coroutine
    # mid-update — the human would otherwise stare at a bare 👀 reaction for
    # minutes. Best-effort: a send failure never blocks the update.
    if _bridge_plist_exists():
        try:
            await tg_client.send_message(
                event.chat_id,
                f"{machine} - ⏳ updating — if this update restarts the bridge, "
                "confirmation will follow from the fresh bridge",
            )
        except Exception:  # swallow-ok: interim notice best-effort, never blocks update
            pass

    # Export the originating chat context so a bridge-relevant run can stage
    # data/update-pending-report before self-restarting (the fresh bridge's
    # boot flush replies to this chat).
    env = os.environ.copy()
    env["UPDATE_REPORT_CHAT_ID"] = str(event.chat_id)
    env["UPDATE_REPORT_REPLY_TO"] = str(event.message.id)

    try:
        subprocess_start_ts = time.time()
        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(_PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=UPDATE_SHELL_TIMEOUT_SECONDS,
            env=env,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()

        # Non-marker stdout lines — ALL are scanned for warnings below
        # (issue #1898: a swallowed restart ERROR on a non-first line used to
        # slip past the old first-line-only scan).
        status_lines = [
            line
            for line in (stdout or "").split("\n")
            if line.strip() and not line.strip().startswith("<<FILE:")
        ]
        first_line = status_lines[0] if status_lines else "(no output)"

        failed = result.returncode != 0

        # Resulting commit SHA — concrete proof the pull actually landed. Derive
        # the outcome from the return code, not from stdout's banner first line
        # (which says nothing about success/failure).
        try:
            sha = (
                subprocess.run(
                    ["git", "-C", str(_PROJECT_DIR), "rev-parse", "--short", "HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                or "?"
            )
        except Exception:
            sha = "?"

        # Release-verify gate (issue #1898): ✅ is printed only when the shell
        # exit code is 0 AND no in-role process runs positively-stale code —
        # a green pull with a stale running process is the exact bug #1898
        # was filed against. Degrades gracefully (shell result only) if the
        # verify import/call raises. Note: if the shell restarted the bridge,
        # this coroutine was SIGKILLed and never reaches here — the fresh
        # bridge's boot flush (run_boot_release_check) is the reporter.
        stale_details: list[str] = []
        reload_states: list[str] = []
        try:
            # Poll only when the shell actually restarted the worker (its
            # "[update] Worker restarted" stdout marker) — the shell's
            # --since 0 principle: on a no-op cycle the beacon can never
            # freshen, so polling would burn the full 30s window for nothing.
            worker_restarted = any("Worker restarted" in line for line in status_lines)
            check = await _verify_release_after_update(
                subprocess_start_ts, worker_restarted=worker_restarted
            )
            for name, info in check.items():
                classification = info.get("classification")
                boot_sha = info.get("boot_sha") or "?"
                if classification == "stale":
                    stale_details.append(f"{name} running {boot_sha} but HEAD is {sha}")
                    reload_states.append(f"{name} STALE {boot_sha}")
                elif classification == "unknown":
                    reload_states.append(f"{name} unknown")
                elif (info.get("beacon_ts") or 0) > subprocess_start_ts:
                    reload_states.append(f"{name} restarted")
                else:
                    reload_states.append(f"{name} current")
        except Exception as e:
            logger.warning(f"[update] release verify degraded — reporting shell result only: {e}")

        failed = failed or bool(stale_details)
        if failed:
            if stale_details:
                status = f"❌ update FAILED @ {sha}: {'; '.join(stale_details)}"
            else:
                first_err = stderr.split(chr(10))[0] if stderr else first_line
                status = f"❌ update FAILED @ {sha}: {first_err}"
        else:
            status = f"✅ update OK @ {sha}"
        if reload_states:
            status += f" ({', '.join(reload_states)})"

        if running_count > 0:
            plural = "s" if running_count != 1 else ""
            status += f" ({running_count} session{plural} running; restart queued)"

        # If update had warnings or failed, queue agent session to fix.
        # ALL stdout lines are scanned (issue #1898), not only the first.
        has_warnings = any(
            "warning" in line.lower() or "error" in line.lower() for line in status_lines
        )
        if failed or has_warnings:
            status += " — spawning agent session to fix"
            await _queue_fix_session(event, machine, stdout, stderr, failed)

        await tg_client.send_message(event.chat_id, f"{machine} - {status}")
    except subprocess.TimeoutExpired:
        await tg_client.send_message(
            event.chat_id,
            f"{machine} - update timed out after {UPDATE_SHELL_TIMEOUT_SECONDS}s",
        )
    except Exception as e:
        logger.error(f"[update] /update failed: {e}")
        await tg_client.send_message(
            event.chat_id,
            f"{machine} - update failed: {e}",
        )


async def run_boot_release_check(tg_client) -> None:
    """Fresh-bridge boot self-check + pending-report flush (issue #1898).

    Called at bridge startup right after the boot-SHA beacon write. Two
    INDEPENDENT steps, both best-effort (never crash startup):

    1. UNCONDITIONALLY verify the running release (fresh bridge beacon +
       worker beacon). A stale self-classification writes the
       ``data/update-release-failed`` sentinel for the watchdog — with or
       without a pending report (the pure 30-min cron path stages none: the
       exact #1898 trigger path gets the backstop too). The planned-restart
       marker ``data/update-restart-in-progress`` is cleared now that the
       fresh bridge is up.
    2. Only if ``data/update-pending-report`` exists (a Telegram-triggered
       bridge-relevant update staged it before the self-kill): reuse the check
       to compose the OK/FAILED reply, send it to the staged chat/reply-to,
       and delete the file — but leave it in place when the fresh bridge
       classified stale, so the watchdog's undrained-report read can escalate.
    """
    machine = _get_machine_name()
    check: dict = {}
    head_short = "?"
    bridge_stale = False

    # Step 1: unconditional self-check + sentinel + marker clear.
    try:
        from scripts.update.git import get_short_sha
        from scripts.update.service import verify_running_release
        from scripts.update.verify import check_machine_identity

        head_short = get_short_sha(_PROJECT_DIR)
        machine_check = check_machine_identity(_PROJECT_DIR)
        check = verify_running_release(_PROJECT_DIR, head_short, machine_check)
        bridge_info = check.get("bridge") or {}
        bridge_stale = bridge_info.get("classification") == "stale"
        if bridge_stale:
            sentinel = _PROJECT_DIR / "data" / "update-release-failed"
            sentinel.write_text(
                json.dumps(
                    {
                        "process": "bridge",
                        "boot_sha": bridge_info.get("boot_sha"),
                        "head_sha": head_short,
                        "ts": time.time(),
                    }
                )
                + "\n"
            )
            logger.error(
                "[update] fresh bridge booted STALE: running %s but HEAD is %s — "
                "wrote update-release-failed sentinel",
                bridge_info.get("boot_sha"),
                head_short,
            )
        elif bridge_info.get("classification") == "matches":
            # Fleet recovered — clear any earlier sentinel so the watchdog
            # stops surfacing a resolved failure every 60s forever. Positive
            # `matches` only: an `unknown` boot must not erase a genuine
            # failure record.
            (_PROJECT_DIR / "data" / "update-release-failed").unlink(missing_ok=True)
    except Exception as e:
        logger.warning(f"[update] boot release self-check failed (non-fatal): {e}")
    try:
        (_PROJECT_DIR / "data" / "update-restart-in-progress").unlink(missing_ok=True)
    except Exception:  # swallow-ok: marker cleanup best-effort, TTL covers misses
        pass

    # Step 2: conditional pending-report flush.
    report_path = _PROJECT_DIR / "data" / "update-pending-report"
    try:
        if not report_path.exists():
            return
        report = json.loads(report_path.read_text())
        sha = head_short if head_short != "?" else report.get("sha", "?")
        worker_state = report.get("worker_state") or "worker unknown"

        stale_details = [
            f"{name} running {info.get('boot_sha') or '?'} but HEAD is {sha}"
            for name, info in check.items()
            if info.get("classification") == "stale"
        ]
        if bridge_stale:
            bridge_state = f"bridge STALE {(check.get('bridge') or {}).get('boot_sha') or '?'}"
        else:
            bridge_state = "bridge restarted"
        worker_info = check.get("worker") or {}
        if worker_info.get("classification") == "stale":
            worker_state = f"worker STALE {worker_info.get('boot_sha') or '?'}"
        states = f"({bridge_state}, {worker_state})"

        if stale_details:
            status = f"❌ update FAILED @ {sha}: {'; '.join(stale_details)} {states}"
        else:
            status = f"✅ update OK @ {sha} {states}"
        message = f"{machine} - {status}"

        chat_id = int(report["chat_id"])
        try:
            await tg_client.send_message(chat_id, message, reply_to=int(report["reply_to"]))
        except Exception:  # swallow-ok: reply target may be gone; falls back to a plain send below
            # Reply target may be gone — fall back to a plain send.
            await tg_client.send_message(chat_id, message)

        if bridge_stale:
            logger.warning(
                "[update] pending report left in place (fresh bridge stale) for the watchdog"
            )
        else:
            report_path.unlink(missing_ok=True)
        logger.info(f"[update] flushed pending update report to chat {chat_id}")
    except Exception as e:
        logger.warning(f"[update] pending update report flush failed (non-fatal): {e}")


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
