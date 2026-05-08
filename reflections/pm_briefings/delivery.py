"""
reflections/pm_briefings/delivery.py — Voice-note + written-followup
delivery via direct Python imports (no subprocess).

Synthesizes audio via tools.tts.synthesize() and queues the voice-note via
the Redis-outbox payload pattern from tools/valor_telegram.py:780-816. The
written follow-up is queued as a separate text-only payload after the
voice-note. Subprocess is reserved for `gh` calls in collector.py.

TTS-failure contract (B1-R3 canonical -- corrected against tools/tts):
tools.tts.synthesize() does NOT raise; it ALWAYS returns a dict with `error`
populated on failure. Delivery checks result.get("error") after every call.
On truthy error, delivery enqueues a single text-only failure-notice payload
per target group AND raises BriefingTtsFailedError so the caller marks the
per-project Reflection record `last_status = "error"`. The follow-up payload
is NOT enqueued.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Any

logger = logging.getLogger("reflections.pm_briefings.delivery")


class BriefingTtsFailedError(RuntimeError):
    """Raised when tools.tts.synthesize() returns a truthy `error` field."""


class BriefingConfigError(RuntimeError):
    """Raised for hard configuration failures detected at startup.

    Declared at module top alongside BriefingTtsFailedError per plan N1-R4.
    Reserved for startup config-validation and future hard config failures.
    """


def _resolve_chat_id(project: dict, group_name: str) -> int | None:
    """Resolve the chat_id for a target group from project.telegram.groups.

    Returns None if the group is missing or has no chat_id. Caller is
    expected to log+skip in that case.
    """
    telegram = project.get("telegram") or {}
    groups = telegram.get("groups") or {}
    entry = groups.get(group_name)
    if entry is None:
        return None
    if isinstance(entry, dict):
        cid = entry.get("chat_id")
        if isinstance(cid, (int, str)) and str(cid).strip():
            try:
                return int(cid)
            except (TypeError, ValueError):
                return None
    elif isinstance(entry, (int, str)) and str(entry).strip():
        try:
            return int(entry)
        except (TypeError, ValueError):
            return None
    return None


def _get_redis_connection():  # noqa: ANN202 - matches valor_telegram convention
    """Lazy import of redis client (mirrors tools/valor_telegram.py)."""
    import redis

    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def _voice_note_payload(*, chat_id: int, audio_path: str, session_id: str, duration: float) -> dict:
    """Build a Redis-outbox payload for a voice-note delivery.

    Mirrors tools/valor_telegram.py:780-816 -- the relay drains
    telegram:outbox:<session_id> independently and uses voice_note=True to
    select Telethon's voice-bubble path.
    """
    return {
        "chat_id": int(chat_id),
        "reply_to": None,
        "text": "",
        "session_id": session_id,
        "timestamp": time.time(),
        "file_paths": [audio_path],
        "voice_note": True,
        "duration": float(duration or 0.0),
        "cleanup_file": True,
    }


def _text_payload(*, chat_id: int, text: str, session_id: str) -> dict:
    """Build a Redis-outbox payload for a plain text message."""
    return {
        "chat_id": int(chat_id),
        "reply_to": None,
        "text": text,
        "session_id": session_id,
        "timestamp": time.time(),
    }


def _enqueue(redis_conn, session_id: str, payload: dict) -> None:
    """Push a payload onto telegram:outbox:<session_id>; set 1h TTL."""
    queue_key = f"telegram:outbox:{session_id}"
    redis_conn.rpush(queue_key, json.dumps(payload))
    try:
        redis_conn.expire(queue_key, 3600)
    except Exception as e:
        # Non-fatal: the relay drains the queue regardless.
        logger.debug("Failed to set TTL on %s: %s", queue_key, e)


def send(
    transcript: str,
    written_followup: str,
    target_groups: list[str],
    project: dict,
    *,
    session_id: str | None = None,
    voice: str | None = None,
    dry_run: bool = False,
) -> dict[str, str]:
    """Synthesize the transcript, enqueue voice-note + follow-up per group.

    Args:
        transcript: Spoken brief (output of builder.build() audio).
        written_followup: Markdown follow-up (output of builder.build()).
            May be empty; an empty followup means "audio only".
        target_groups: List of group names from project.pm_briefing.target_groups.
        project: Full project dict (needed for telegram.groups chat_id
            resolution).
        session_id: Synthetic session id for the outbox; auto-generated when
            None.
        voice: Optional voice override (passed straight to tts.synthesize()).
            None means "let the synthesizer pick its default".
        dry_run: If True, skip TTS + enqueue; instead, write the transcript
            and follow-up to logs/reflections/pm-briefings-<slug>-<date>.txt
            for inspection. Used by DRY_RUN=1 testing.

    Returns:
        Dict[group_name, status] where status is "ok", "skipped" (no
        chat_id), or "tts_failed".

    Raises:
        BriefingTtsFailedError: If tools.tts.synthesize() returns a truthy
            error field. The caller is expected to record this on the
            per-project Reflection record.
    """
    if not transcript:
        # No-op: caller handed us an empty transcript (skip_when_empty case).
        return {g: "skipped" for g in target_groups}

    if dry_run:
        return _dry_run_dump(transcript, written_followup, project)

    # Resolve chat_ids ahead of TTS so we don't synthesize for an empty
    # destination set.
    resolved: dict[str, int] = {}
    skipped: dict[str, str] = {}
    for g in target_groups:
        cid = _resolve_chat_id(project, g)
        if cid is None:
            logger.warning(
                "No chat_id for group %r in project %r, skipping",
                g,
                project.get("slug"),
            )
            skipped[g] = "skipped"
        else:
            resolved[g] = cid

    if not resolved:
        return skipped

    # Synthesize once, deliver fan-out.
    sid = session_id or f"pm-briefing-{int(time.time())}"
    fd, audio_path = tempfile.mkstemp(suffix=".ogg", prefix="pm-briefing-")
    os.close(fd)

    from tools.tts import synthesize

    synthesize_kwargs: dict[str, Any] = {}
    if voice:
        synthesize_kwargs["voice"] = voice

    result = synthesize(transcript, audio_path, **synthesize_kwargs)
    if result.get("error"):
        # TTS failed: enqueue a failure-notice per group AND raise.
        try:
            r = _get_redis_connection()
        except Exception as e:
            logger.error("Redis unavailable while reporting TTS failure: %s", e)
            raise BriefingTtsFailedError(str(result.get("error"))) from None
        notice = "Daily briefing failed: TTS unavailable"
        for group, chat_id in resolved.items():
            _enqueue(r, sid, _text_payload(chat_id=chat_id, text=notice, session_id=sid))
            skipped[group] = "tts_failed"
        # Best-effort cleanup of the empty stub.
        try:
            os.unlink(audio_path)
        except OSError:
            pass
        raise BriefingTtsFailedError(str(result.get("error")))

    duration = float(result.get("duration") or 0.0)

    # Enqueue voice-note + follow-up per group.
    r = _get_redis_connection()
    statuses: dict[str, str] = dict(skipped)
    for group, chat_id in resolved.items():
        voice_payload = _voice_note_payload(
            chat_id=chat_id,
            audio_path=audio_path,
            session_id=sid,
            duration=duration,
        )
        _enqueue(r, sid, voice_payload)
        if written_followup:
            _enqueue(
                r,
                sid,
                _text_payload(chat_id=chat_id, text=written_followup, session_id=sid),
            )
        statuses[group] = "ok"

    return statuses


def _dry_run_dump(transcript: str, written_followup: str, project: dict) -> dict[str, str]:
    """Write transcript + follow-up to logs/reflections/ for DRY_RUN=1."""
    from datetime import datetime
    from pathlib import Path
    from zoneinfo import ZoneInfo

    pm = project.get("pm_briefing") or {}
    tz_name = pm.get("timezone") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # swallow-ok: bad/missing tz name falls back to UTC; not a fatal config error
        tz = ZoneInfo("UTC")
    today = datetime.now(tz=tz).date().isoformat()
    slug = project.get("slug") or "unknown"

    log_dir = Path(__file__).parent.parent.parent / "logs" / "reflections"
    log_dir.mkdir(parents=True, exist_ok=True)
    out_path = log_dir / f"pm-briefings-{slug}-{today}.txt"

    body = (
        f"# pm-briefings dry-run for {slug} on {today}\n\n"
        "## Audio transcript\n\n"
        f"{transcript}\n\n"
        "## Written follow-up\n\n"
        f"{written_followup or '(none)'}\n"
    )
    out_path.write_text(body)
    logger.info("DRY_RUN dumped briefing to %s", out_path)
    return {"_dry_run_path": str(out_path)}
