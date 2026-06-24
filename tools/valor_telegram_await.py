"""Synchronous bot-reply awaiter for `valor-telegram send --await-reply` (issue #1574).

The awaiter is a PURE READER of the `TelegramMessage` history store. It never
opens a Telethon client (the bridge holds the SQLite session lock) — it polls
the records the bridge writes and upserts (via `update_message_text`) for a
registered bot peer.

## Why silence decides "done", not pattern-matching

A Hermes bot reply is three distinct on-wire streams (issue #1574):
  1. The real answer is STREAMED via repeated in-place edits on a stable
     message_id. The terminal "done" flag is internal — never emitted on-wire.
  2. Progress/status chatter (`⏳ Working...`, tool bubbles) arrives as separate
     messages.
  3. The trailing `⚠️` footer is GLUED inside the final answer message — a test
     signal, never stripped.

Because there is NO on-wire done-marker, the only robust settle signal is
SILENCE: when no new message arrives AND no edit changes any tracked body for a
quiet window, the turn is settled. Two independent timers are load-bearing:

  - quiet_window: short (~5s) "stopped streaming" window. Reset on every new
    message OR content change.
  - timeout: generous overall cap (~600s+). Hermes turns run minutes; conflating
    the two timers is the #1 bug — a short overall timeout kills the await
    mid-think.

Status patterns ONLY clean the displayed prose. They NEVER decide when to
return — a drifted pattern yields at worst a stray interim line, never a
premature settle.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

# Defaults — provisional, tunable. Overridable per-bot via the registry
# settle_profile and per-invocation via the CLI --timeout flag. (Grain of salt:
# these are starting points observed against Hermes v0.14.0, not hard limits.)
DEFAULT_QUIET_WINDOW_SECONDS = 5.0
DEFAULT_TIMEOUT_SECONDS = 600.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.5

# Status/tool-bubble patterns used ONLY to clean the displayed prose. A leading
# ⚠️ is deliberately NOT in this list: the glued footer rides inside the final
# answer and a terse answer can legitimately be footer-only.
DEFAULT_STATUS_PATTERNS = [
    r"^⏳",
    r"^(💻|🔎|🔧|📖|⚙️|📝) \w+:",
]


@dataclass
class AwaitResult:
    """Structured result of an await-reply probe (the --json schema source)."""

    settled: bool = False
    timed_out: bool = False
    settled_text: str = ""
    footer_present: bool = False
    message_ids: list[int] = field(default_factory=list)
    edit_count: int = 0
    started_ts: float | None = None
    settled_ts: float | None = None
    elapsed_s: float = 0.0
    transcript: list[dict] = field(default_factory=list)


def _classify(text: str, status_patterns: list[str]) -> str:
    """Classify a message body as 'status'/'tool' vs. 'answer' for DISPLAY only.

    This never feeds the settle decision; it only filters the printed prose.
    """
    stripped = (text or "").strip()
    for pat in status_patterns:
        if re.search(pat, stripped):
            return "status"
    return "answer"


def _is_footer_present(text: str) -> bool:
    """Detect the glued ⚠️ footer riding inside the final answer message."""
    return "⚠️" in (text or "")


def await_bot_reply(
    chat_id: str,
    send_ts: float,
    quiet_window: float = DEFAULT_QUIET_WINDOW_SECONDS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    status_patterns: list[str] | None = None,
    *,
    _now=time.monotonic,
    _sleep=time.sleep,
    _fetch=None,
) -> AwaitResult:
    """Block until the bot's reply settles on silence, or until timeout.

    Args:
        chat_id: The bot peer's chat id (string form, matching store_message).
        send_ts: Wall-clock epoch of the probe send. Only inbound records with
            timestamp >= send_ts are considered the reply.
        quiet_window: Seconds of no-change that mark "stopped streaming".
        timeout: Overall wall-clock cap on the await.
        poll_interval: Seconds between history polls.
        status_patterns: Regexes that mark a line as status/tool for DISPLAY.
        _now / _sleep / _fetch: Injected for testing. `_fetch(chat_id)` must
            return a list of dicts with keys: message_id, content, ts,
            direction. Defaults to the live `TelegramMessage` store.

    Returns:
        AwaitResult — settled prose, footer flag, transcript, timing.
    """
    patterns = status_patterns if status_patterns is not None else DEFAULT_STATUS_PATTERNS
    fetch = _fetch or _default_fetch

    start = _now()
    # Wall-clock anchor so we can compute absolute started/settled timestamps for
    # the --json schema while using monotonic time for the actual settle logic.
    wall_start = time.time()

    # message_id -> latest content seen. Edit-aware: a changed body for a known
    # id resets the quiet timer exactly like a brand-new message.
    seen: dict[int, str] = {}
    edit_count = 0
    last_change = start
    started_ts: float | None = None

    while True:
        loop_now = _now()
        elapsed = loop_now - start

        try:
            records = fetch(chat_id)
        except Exception:
            # Transient store error: do not abort the await. Retry next poll.
            # (A failed read simply doesn't advance state; the quiet timer keeps
            # running, bounded by the overall timeout.)
            records = None

        if records:
            for rec in records:
                if rec.get("direction") != "in":
                    continue
                rec_ts = rec.get("ts")
                if rec_ts is not None and rec_ts < send_ts:
                    continue
                mid = rec.get("message_id")
                if mid is None:
                    continue
                content = rec.get("content") or ""
                if mid not in seen:
                    seen[mid] = content
                    last_change = loop_now
                    if started_ts is None:
                        started_ts = rec_ts or wall_start + elapsed
                elif seen[mid] != content:
                    seen[mid] = content
                    edit_count += 1
                    last_change = loop_now

        # Settle: we have ≥1 captured message and the quiet window has elapsed
        # with no change.
        if seen and (loop_now - last_change) >= quiet_window:
            return _build_result(
                seen,
                patterns,
                settled=True,
                timed_out=False,
                started_ts=started_ts,
                settled_ts=wall_start + (loop_now - start),
                elapsed_s=loop_now - start,
                edit_count=edit_count,
            )

        # Hard timeout: return whatever we have (possibly nothing).
        if elapsed >= timeout:
            return _build_result(
                seen,
                patterns,
                settled=False,
                timed_out=True,
                started_ts=started_ts,
                settled_ts=None,
                elapsed_s=elapsed,
                edit_count=edit_count,
            )

        _sleep(poll_interval)


def _build_result(
    seen: dict[int, str],
    patterns: list[str],
    *,
    settled: bool,
    timed_out: bool,
    started_ts: float | None,
    settled_ts: float | None,
    elapsed_s: float,
    edit_count: int,
) -> AwaitResult:
    """Assemble an AwaitResult from the captured per-message bodies."""
    # Preserve capture order (dict insertion order == arrival order).
    transcript = []
    answer_parts = []
    footer_present = False
    for mid, content in seen.items():
        kind = _classify(content, patterns)
        transcript.append({"message_id": mid, "kind": kind, "text": content})
        if kind == "answer":
            answer_parts.append(content)
            if _is_footer_present(content):
                footer_present = True

    settled_text = "\n\n".join(p for p in answer_parts if p.strip())

    return AwaitResult(
        settled=settled,
        timed_out=timed_out,
        settled_text=settled_text,
        footer_present=footer_present,
        message_ids=list(seen.keys()),
        edit_count=edit_count,
        started_ts=started_ts,
        settled_ts=settled_ts,
        elapsed_s=round(elapsed_s, 2),
        transcript=transcript,
    )


def _default_fetch(chat_id: str) -> list[dict]:
    """Read inbound records for a chat from the live TelegramMessage store."""
    from models.telegram import TelegramMessage

    records = TelegramMessage.query.filter(chat_id=str(chat_id))
    out = []
    for msg in records:
        out.append(
            {
                "message_id": msg.message_id,
                "content": msg.content,
                "ts": msg.timestamp,
                "direction": msg.direction,
            }
        )
    # Sort by timestamp ascending so transcript order matches arrival order.
    out.sort(key=lambda r: r.get("ts") or 0.0)
    return out
