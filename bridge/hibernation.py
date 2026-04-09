"""Bridge hibernation: structured recovery for Telegram auth and connectivity failures.

This module handles the distinction between two failure modes:
- Auth expiry (permanent): requires human intervention via `python scripts/telegram_login.py`
- Transient connectivity: handled by the existing retry loop in telegram_bridge.py

On auth failure, the bridge enters hibernation:
1. Writes `data/bridge-auth-required` flag file
2. Fires a macOS notification via osascript
3. Exits with code 2 (distinct from crash exit code 1)

The watchdog reads the flag file and suppresses its restart loop while hibernating.

On successful reconnect, the bridge clears the flag and replays buffered output from
`logs/worker/` to Telegram (last 24h, skipping files modified in the last 5 minutes).
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telethon import TelegramClient

logger = logging.getLogger(__name__)

# Flag file location — same data/ directory as flood-backoff
_PROJECT_DIR = Path(__file__).parent.parent
AUTH_REQUIRED_FLAG = _PROJECT_DIR / "data" / "bridge-auth-required"

# Worker output log directory (FileOutputHandler writes here)
_WORKER_LOGS_DIR = _PROJECT_DIR / "logs" / "worker"

# Permanent Telethon auth error types → hibernation
_PERMANENT_AUTH_ERRORS: tuple[type[Exception], ...] = ()

try:
    from telethon.errors import (
        AuthKeyError,
        AuthKeyInvalidError,
        AuthKeyPermEmptyError,
        AuthKeyUnregisteredError,
        SessionExpiredError,
        SessionRevokedError,
        UnauthorizedError,
    )

    _PERMANENT_AUTH_ERRORS = (
        AuthKeyUnregisteredError,
        AuthKeyError,
        AuthKeyInvalidError,
        AuthKeyPermEmptyError,
        SessionExpiredError,
        SessionRevokedError,
        UnauthorizedError,
    )
except ImportError:
    logger.warning("[hibernation] telethon not available — auth classifier disabled")


def is_auth_error(exc: BaseException | None) -> bool:
    """Return True if the exception signals permanent auth failure.

    Permanent auth errors require human intervention (re-running telegram_login.py)
    and should trigger hibernation rather than the transient retry loop.

    Transient errors (NetworkMigrateError, ConnectionError, OSError, FloodWaitError)
    return False and are handled by the existing retry loop.

    Args:
        exc: The exception to classify. None returns False safely.

    Returns:
        True for permanent auth errors, False for transient errors or None.
    """
    if exc is None:
        return False
    if not _PERMANENT_AUTH_ERRORS:
        return False
    return isinstance(exc, _PERMANENT_AUTH_ERRORS)


def enter_hibernation() -> None:
    """Write auth-required flag file and fire macOS notification.

    Called when the bridge detects permanent auth failure. Writes the flag file
    atomically (temp + os.replace) to prevent partial writes.

    Safe to call even if the data/ directory is missing or read-only — failures
    are caught and logged as warnings so the caller can still exit cleanly.
    """
    logger.error(
        "[hibernation] Bridge hibernating: auth required. "
        "Run 'python scripts/telegram_login.py' to authenticate, "
        "then './scripts/valor-service.sh restart' to resume."
    )

    # Write flag file atomically
    try:
        AUTH_REQUIRED_FLAG.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTH_REQUIRED_FLAG.with_suffix(".tmp")
        tmp.write_text("auth-required")
        os.replace(str(tmp), str(AUTH_REQUIRED_FLAG))
        logger.info("[hibernation] Wrote flag file: %s", AUTH_REQUIRED_FLAG)
    except OSError as e:
        logger.warning("[hibernation] Failed to write flag file: %s", e)

    # Fire macOS notification (non-fatal)
    _fire_notification()


def exit_hibernation() -> None:
    """Clear the auth-required flag file on successful reconnect.

    Safe to call when the flag file does not exist.
    """
    try:
        if AUTH_REQUIRED_FLAG.exists():
            AUTH_REQUIRED_FLAG.unlink(missing_ok=True)
            logger.info("[hibernation] Cleared auth-required flag — bridge reconnected")
    except OSError as e:
        logger.warning("[hibernation] Failed to clear flag file: %s", e)


def is_hibernating() -> bool:
    """Return True if the auth-required flag file is present.

    Used by the watchdog to suppress restart loops during hibernation.
    """
    return AUTH_REQUIRED_FLAG.exists()


async def replay_buffered_output(
    client: TelegramClient,
    max_age_hours: float = 24,
    recency_skip_minutes: float = 5,
) -> int:
    """Replay buffered FileOutputHandler logs to Telegram after reconnect.

    Scans logs/worker/*.log for files written during bridge downtime and re-delivers
    their content to Telegram. Files modified in the last `recency_skip_minutes`
    minutes are skipped to avoid replaying output from sessions still in progress.

    A `.replayed` marker file is written after successful replay to prevent
    duplicate delivery on subsequent reconnects.

    Args:
        client: Connected TelegramClient to send messages through.
        max_age_hours: Only replay files modified within this many hours ago.
        recency_skip_minutes: Skip files modified more recently than this (still active).

    Returns:
        Number of log entries successfully replayed.
    """
    if not _WORKER_LOGS_DIR.exists():
        logger.debug("[hibernation] No worker logs dir — nothing to replay")
        return 0

    log_files = list(_WORKER_LOGS_DIR.glob("*.log"))
    if not log_files:
        logger.debug("[hibernation] Worker logs dir is empty — nothing to replay")
        return 0

    now = time.time()
    max_age_seconds = max_age_hours * 3600
    recency_skip_seconds = recency_skip_minutes * 60

    replayed_count = 0

    for log_path in sorted(log_files):
        marker = log_path.with_suffix(".replayed")
        if marker.exists():
            logger.debug("[hibernation] Skipping already-replayed log: %s", log_path.name)
            continue

        try:
            mtime = log_path.stat().st_mtime
        except OSError:
            logger.warning("[hibernation] Cannot stat log file: %s", log_path.name)
            continue

        file_age = now - mtime
        if file_age > max_age_seconds:
            logger.debug(
                "[hibernation] Skipping old log (%.1fh): %s",
                file_age / 3600,
                log_path.name,
            )
            continue

        if file_age < recency_skip_seconds:
            logger.debug(
                "[hibernation] Skipping recent log (%.1fmin, may still be active): %s",
                file_age / 60,
                log_path.name,
            )
            continue

        entries = _parse_log_file(log_path)
        if not entries:
            logger.debug("[hibernation] No parseable entries in: %s", log_path.name)
            continue

        file_replayed = 0
        for chat_id, reply_to, text, timestamp in entries:
            try:
                header = f"--- Buffered output from {timestamp} ---\n"
                await client.send_message(int(chat_id), header + text, reply_to=reply_to)
                file_replayed += 1
            except Exception as e:
                logger.warning("[hibernation] Failed to replay entry (chat=%s): %s", chat_id, e)

        if file_replayed:
            replayed_count += file_replayed
            logger.info("[hibernation] Replayed %d entries from %s", file_replayed, log_path.name)
            # Write marker to prevent re-delivery
            try:
                marker.write_text(str(time.time()))
            except OSError as e:
                logger.warning("[hibernation] Failed to write replay marker: %s", e)

    return replayed_count


def _parse_log_file(
    log_path: Path,
) -> list[tuple[str, int | None, str, str]]:
    """Parse a FileOutputHandler log file into replay entries.

    The FileOutputHandler writes entries in this format:
        [YYYY-MM-DD HH:MM:SS] chat=CHAT_ID reply_to=MSG_ID
        <text content>
        ---

    Returns:
        List of (chat_id, reply_to, text, timestamp) tuples.
        Malformed blocks are skipped with a warning.
    """
    entries: list[tuple[str, int | None, str, str]] = []

    try:
        content = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("[hibernation] Cannot read log file %s: %s", log_path.name, e)
        return entries

    # Split on the "---" separator
    blocks = content.split("\n---\n")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        lines = block.splitlines()
        if not lines:
            continue

        header = lines[0]
        # Parse: [2024-01-15 10:30:00] chat=123456789 reply_to=42
        if not header.startswith("[") or "chat=" not in header:
            logger.debug("[hibernation] Skipping malformed header: %r", header[:80])
            continue

        try:
            # Extract timestamp
            ts_end = header.index("]")
            timestamp = header[1:ts_end].strip()

            rest = header[ts_end + 1 :].strip()
            parts = rest.split()

            chat_id = None
            reply_to: int | None = None

            for part in parts:
                if part.startswith("chat="):
                    chat_id = part[5:]
                elif part.startswith("reply_to="):
                    val = part[9:]
                    try:
                        reply_to = int(val)
                    except ValueError:
                        reply_to = None

            if not chat_id:
                logger.debug("[hibernation] No chat= in header: %r", header[:80])
                continue

            # Skip REACTION entries (no text to replay)
            if "REACTION" in header:
                continue

            text = "\n".join(lines[1:]).strip()
            if not text:
                continue

            entries.append((chat_id, reply_to, text, timestamp))

        except (ValueError, IndexError) as e:
            logger.debug("[hibernation] Failed to parse block header: %s", e)
            continue

    return entries


def _fire_notification() -> None:
    """Fire a macOS notification via osascript.

    Non-fatal: wrapped in try/except for non-macOS or permission denied.
    """
    message = "Bridge hibernating: auth required. Run: python scripts/telegram_login.py"
    script = (
        f'display notification "{message}" '
        f'with title "Valor Bridge" subtitle "Authentication Required"'
    )
    try:
        subprocess.run(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            timeout=5,
        )
        logger.info("[hibernation] macOS notification sent")
    except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning("[hibernation] Could not send macOS notification: %s", e)
