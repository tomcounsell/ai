"""PreCompact hook — JSONL transcript backup + 5-minute cooldown + retention.

The Claude Code SDK fires this hook synchronously before it compacts a
session's conversation history. The hook provides three guarantees and one
bail-out contract:

1. **Backup guarantee.** On every fire where the transcript file exists on
   disk, we copy it byte-for-byte to
   ``{transcript_parent}/backups/{claude_session_uuid}-{int(utc_ts)}.jsonl.bak``.
   The `claude --resume` path can read this backup if the live session is
   corrupted mid-compaction.

2. **Cooldown guarantee.** A second PreCompact fire for the same
   ``claude_session_uuid`` within 300 seconds is a fast no-op — no second
   backup, no second AgentSession write. This prevents rapid compaction loops
   from thrashing disk I/O and from producing degraded summaries stacked on
   top of each other. The cooldown timestamp lives on
   ``AgentSession.last_compaction_ts``; there is no sibling Redis key.

3. **Retention guarantee.** After each successful write, the hook keeps the
   three most recent backups for the session (by filename-embedded timestamp)
   and unlinks older ones. ``claude --resume`` only benefits from recent
   backups; older backups have no recovery value once a session has
   progressed past them.

**Bail-out contract.** The hook NEVER raises. Every side-effectful call
(``shutil.copy2``, ``AgentSession.query.filter``, ``AgentSession.save``,
directory scans) is wrapped in an exception handler. On failure we log at
``warning`` level and return ``{}``. A raising hook would crash the SDK
session, which is strictly worse than a missed backup.

**Correlation key (B1 fix).** The SDK's ``input_data["session_id"]`` is the
Claude Code SDK's internal UUID, NOT our bridge-side
``AgentSession.session_id``. The mapping between the two is written by
``_store_claude_session_uuid`` at ``agent/sdk_client.py`` which sets
``AgentSession.claude_session_uuid``. This hook does the inverse lookup:
``AgentSession.query.filter(claude_session_uuid=input_data["session_id"])``.
Filtering by ``session_id`` would return zero rows because the two namespaces
do not overlap.

**FileNotFoundError handling (C3).** ``FileNotFoundError`` on the source
transcript is an expected condition — brand-new sessions whose first turn
hasn't flushed yet, path races where the SDK rotates the transcript between
hook fire and our read, or non-Valor sessions that never produced a
transcript. We handle it with a dedicated ``try/except FileNotFoundError:``
that logs at ``debug`` level (not ``warning``) and returns ``{}`` without
attempting a cooldown write. All other exceptions (disk-full, permission
denied, etc.) flow into the outer ``try/except Exception:`` handler and log
at ``warning``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import HookContext, PreCompactHookInput

logger = logging.getLogger(__name__)

# Per-session cooldown window, in seconds. A second PreCompact fire for the
# same claude_session_uuid within this window is a no-op.
COMPACTION_COOLDOWN_SECONDS = 300

# Retention: keep the last N JSONL backups per session. N=3 covers "most
# recent compaction" + "one before that in case the most recent is corrupted"
# + "one safety margin." See spike-2 in docs/plans/compaction-hardening.md.
BACKUP_RETENTION_COUNT = 3


def _snapshot_and_prune(
    src: Path,
    dst_dir: Path,
    dst_path: Path,
    claude_session_uuid: str,
) -> bool:
    """Synchronous worker body executed off the event loop.

    Performs the byte-for-byte ``shutil.copy2`` and the retention prune, both
    inside a single ``asyncio.to_thread`` call so the hook returns quickly.

    Raises ``FileNotFoundError`` if ``src`` does not exist — the caller
    handles this as an expected condition (C3). All other failures propagate
    to the caller's outer ``Exception`` handler, which logs at ``warning``.

    Returns True on successful copy. Retention failure is swallowed here
    (logged at ``warning``) because retention is strictly defense-in-depth
    — the backup already landed.
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    # Byte-for-byte copy preserving timestamps. FileNotFoundError here means
    # the transcript does not exist — propagate to the caller for C3 handling.
    shutil.copy2(str(src), str(dst_path))

    # Retention: list all backups for this session, sort by the
    # filename-embedded integer timestamp descending, unlink index N onward.
    try:
        prefix = f"{claude_session_uuid}-"
        suffix = ".jsonl.bak"
        entries = []
        with os.scandir(dst_dir) as it:
            for entry in it:
                name = entry.name
                if not name.startswith(prefix) or not name.endswith(suffix):
                    continue
                # Extract the integer timestamp between prefix and suffix.
                ts_part = name[len(prefix) : -len(suffix)]
                try:
                    ts_int = int(ts_part)
                except ValueError:
                    # Unrecognized suffix format — skip rather than delete.
                    continue
                entries.append((ts_int, entry.path))
        entries.sort(key=lambda e: e[0], reverse=True)
        for _ts, path in entries[BACKUP_RETENTION_COUNT:]:
            try:
                os.unlink(path)
            except OSError as exc:
                logger.warning("pre_compact: failed to unlink old backup %s: %s", path, exc)
    except Exception as exc:  # noqa: BLE001 - retention is best-effort
        logger.warning("pre_compact: retention prune failed for %s: %s", claude_session_uuid, exc)

    return True


def _update_session_cooldown(claude_session_uuid: str, now_ts: float) -> str:
    """Look up the AgentSession by claude_session_uuid and update cooldown state.

    Returns a status string used by the caller for logging:
        "cooldown_skipped" — the existing last_compaction_ts is within the
            300s window, so we should not have snapshotted. Caller does NOT
            invoke this function until after verifying the cooldown; this
            return value is a backstop.
        "updated"          — last_compaction_ts and compaction_count were
            written via partial save.
        "no_session"       — no AgentSession row matched the UUID (non-Valor
            Claude session, or the first ResultMessage hasn't written the
            mapping yet). Snapshot still happened; cooldown is a miss.
        "error"            — the lookup or save raised. Snapshot still
            happened; cooldown is a miss.

    This function is NOT async — it is called inside ``asyncio.to_thread``
    because Popoto's Redis client is synchronous.
    """
    try:
        from models.agent_session import AgentSession

        rows = list(AgentSession.query.filter(claude_session_uuid=claude_session_uuid))
        if not rows:
            return "no_session"
        # Pick the most recently created row — defensive against hypothetical
        # duplicate UUIDs (should not happen; _store_claude_session_uuid writes
        # a stable mapping per session).
        session = sorted(rows, key=lambda s: s.created_at or 0, reverse=True)[0]
        session.last_compaction_ts = now_ts
        try:
            current = int(getattr(session, "compaction_count", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        session.compaction_count = current + 1
        # Partial save avoids the stale-save hazard documented in
        # nudge-stomp-append-event-bypass.md — only the two named fields are
        # overwritten, even if our local copy is stale on other fields.
        session.save(update_fields=["last_compaction_ts", "compaction_count"])
        return "updated"
    except Exception as exc:  # noqa: BLE001 - lookup/save must not crash the hook
        logger.warning(
            "pre_compact: AgentSession update failed for %s: %s", claude_session_uuid, exc
        )
        return "error"


def _check_cooldown(claude_session_uuid: str, now_ts: float) -> tuple[bool, float | None]:
    """Check whether the session is inside the 5-minute cooldown window.

    Returns (in_cooldown, last_ts). ``in_cooldown`` is True when the session
    exists and its ``last_compaction_ts`` is within ``COMPACTION_COOLDOWN_SECONDS``.
    ``last_ts`` is the read value (for logging) or None if no row matched.

    All errors swallowed — on any failure we return (False, None) so the hook
    proceeds with a snapshot. Missing a cooldown (extra backup + count) is
    strictly safer than silently skipping a backup.
    """
    try:
        from models.agent_session import AgentSession

        rows = list(AgentSession.query.filter(claude_session_uuid=claude_session_uuid))
        if not rows:
            return False, None
        session = sorted(rows, key=lambda s: s.created_at or 0, reverse=True)[0]
        last_ts = getattr(session, "last_compaction_ts", None)
        if last_ts is None:
            return False, None
        try:
            last_float = float(last_ts)
        except (TypeError, ValueError):
            return False, None
        in_cooldown = (now_ts - last_float) < COMPACTION_COOLDOWN_SECONDS
        return in_cooldown, last_float
    except Exception as exc:  # noqa: BLE001 - cooldown read must not crash the hook
        logger.warning("pre_compact: cooldown read failed for %s: %s", claude_session_uuid, exc)
        return False, None


def _increment_skipped_count(claude_session_uuid: str) -> None:
    """Bump ``compaction_skipped_count`` on the AgentSession for cooldown hits.

    Called when the hook short-circuits due to the 5-minute cooldown. Uses a
    partial save so we don't clobber concurrent writes. All errors swallowed.
    """
    try:
        from models.agent_session import AgentSession

        rows = list(AgentSession.query.filter(claude_session_uuid=claude_session_uuid))
        if not rows:
            return
        session = sorted(rows, key=lambda s: s.created_at or 0, reverse=True)[0]
        try:
            current = int(getattr(session, "compaction_skipped_count", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        session.compaction_skipped_count = current + 1
        session.save(update_fields=["compaction_skipped_count"])
    except Exception as exc:  # noqa: BLE001 - counter bump must not crash the hook
        logger.warning(
            "pre_compact: compaction_skipped_count bump failed for %s: %s",
            claude_session_uuid,
            exc,
        )


async def pre_compact_hook(
    input_data: PreCompactHookInput,
    tool_use_id: str | None,
    context: HookContext,
) -> dict[str, Any]:
    """Back up the JSONL transcript before SDK compaction runs.

    Receives ``input_data["session_id"]`` — the Claude Code SDK's internal
    UUID — and ``input_data["transcript_path"]`` — the on-disk JSONL file
    path.

    Guarantees:
        - Never raises. Always returns ``{}``.
        - On successful backup: writes
          ``{transcript_parent}/backups/{claude_session_uuid}-{int(utc_ts)}.jsonl.bak``,
          updates ``AgentSession.last_compaction_ts`` and bumps
          ``compaction_count`` (if a row matches), prunes retention to last
          ``BACKUP_RETENTION_COUNT`` backups.
        - On cooldown hit (< 300s since last backup for this UUID): skips
          the copy, bumps ``AgentSession.compaction_skipped_count``, logs at
          ``info`` level.
        - On missing transcript: silent ``debug`` log, no further work.
        - On any other exception: ``warning`` log, no further work.

    Races accepted:
        - Two concurrent hooks for the same UUID both see ``last_ts=None``
          and both snapshot. Worst outcome is one extra backup file (pruned
          next round) and a ``compaction_count`` that is off by one. See
          Risk 2 in docs/plans/compaction-hardening.md.
    """
    # --- Parse hook input defensively ---
    try:
        claude_session_uuid = input_data.get("session_id", "") or ""
        transcript_path_str = input_data.get("transcript_path", "") or ""
        trigger = input_data.get("trigger", "unknown")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pre_compact: failed to parse input_data: %s", exc)
        return {}

    if not claude_session_uuid:
        logger.warning("pre_compact: hook fired without session_id — no-op")
        return {}

    if not transcript_path_str:
        logger.warning(
            "pre_compact: hook fired without transcript_path for %s — no-op",
            claude_session_uuid,
        )
        return {}

    now_ts = time.time()

    # --- Cooldown check: a second fire within 300s for the same UUID is a no-op ---
    try:
        in_cooldown, last_ts = _check_cooldown(claude_session_uuid, now_ts)
    except Exception as exc:  # noqa: BLE001 - defensive; _check_cooldown already swallows
        logger.warning(
            "pre_compact: cooldown check raised unexpectedly for %s: %s",
            claude_session_uuid,
            exc,
        )
        in_cooldown, last_ts = False, None

    if in_cooldown:
        age = now_ts - (last_ts or now_ts)
        logger.info(
            "pre_compact: cooldown hit for %s (last backup %.1fs ago, "
            "window=%ds, trigger=%s) — skipping snapshot",
            claude_session_uuid,
            age,
            COMPACTION_COOLDOWN_SECONDS,
            trigger,
        )
        # Best-effort skipped-count bump for observability.
        try:
            await asyncio.to_thread(_increment_skipped_count, claude_session_uuid)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pre_compact: skipped-count bump raised for %s: %s",
                claude_session_uuid,
                exc,
            )
        return {}

    # --- Snapshot path ---
    src = Path(transcript_path_str)
    backup_dir = src.parent / "backups"
    dst = backup_dir / f"{claude_session_uuid}-{int(now_ts)}.jsonl.bak"

    # FileNotFoundError on the source transcript is an expected condition
    # (C3): brand-new session, path race, non-Valor session with no
    # transcript. Handle it with a dedicated try/except that logs at debug
    # (not warning), so it does NOT pollute production logs with noise.
    try:
        try:
            await asyncio.to_thread(_snapshot_and_prune, src, backup_dir, dst, claude_session_uuid)
        except FileNotFoundError:
            logger.debug(
                "pre_compact: transcript missing for %s at %s — skipping snapshot",
                claude_session_uuid,
                src,
            )
            return {}
    except Exception as exc:  # noqa: BLE001 - outer net for all other failures
        logger.warning(
            "pre_compact: snapshot failed for %s (trigger=%s) at %s: %s",
            claude_session_uuid,
            trigger,
            src,
            exc,
        )
        return {}

    # --- Cooldown write: update AgentSession.last_compaction_ts + bump count ---
    try:
        status = await asyncio.to_thread(_update_session_cooldown, claude_session_uuid, now_ts)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pre_compact: AgentSession update raised unexpectedly for %s: %s",
            claude_session_uuid,
            exc,
        )
        status = "error"

    if status == "no_session":
        logger.info(
            "pre_compact: no AgentSession row for claude_session_uuid=%s "
            "(trigger=%s) — snapshot written, cooldown state not tracked",
            claude_session_uuid,
            trigger,
        )
    elif status == "updated":
        logger.info(
            "pre_compact: backup written for %s at %s (trigger=%s)",
            claude_session_uuid,
            dst.name,
            trigger,
        )

    return {}
