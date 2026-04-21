"""Email History Tool.

Pure Redis reads against the ``email:history:*`` and ``email:threads`` namespaces
populated by the bridge's IMAP poll loop (see ``bridge/email_bridge.py``
``_record_history`` / ``_record_thread``).

All public helpers return ``{"error": str}`` on Redis failure instead of raising
so the CLI can render a clean error without a traceback. Missing per-msg blobs
(Race 1 in the plan) are skipped defensively.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime

# Shared key schema with bridge.email_bridge — if the bridge's constants
# change we update both sites.
HISTORY_SET_KEY = "email:history:{mailbox}"
HISTORY_MSG_KEY = "email:history:msg:{message_id}"
HISTORY_THREADS_KEY = "email:threads"


def _get_redis():
    """Return a Redis connection using the standard project env var."""
    import redis

    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _ts_to_iso(ts: float | None) -> str | None:
    """Convert unix timestamp to ISO 8601 string, or None."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).isoformat()
    except (ValueError, OSError):
        return None


def _hydrate(r, message_id: str) -> dict | None:
    """Load and parse the per-msg JSON blob for a Message-ID.

    Returns None if the blob is missing (Race 1 tolerant) or malformed.
    """
    raw = r.get(HISTORY_MSG_KEY.format(message_id=message_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def get_recent_emails(
    mailbox: str = "INBOX",
    limit: int = 10,
    since_ts: float | None = None,
) -> dict:
    """Return the ``limit`` most recent emails from the history cache.

    Args:
        mailbox: IMAP mailbox name — only ``INBOX`` is populated in v1.
        limit: Maximum entries to return.
        since_ts: Optional unix timestamp; only entries with score >= this
            are considered.

    Returns:
        ``{"messages": [...], "count": N, "mailbox": "INBOX"}`` on success,
        or ``{"error": str}`` on Redis failure.
    """
    if mailbox != "INBOX":
        return {
            "error": (f"Only INBOX is supported in v1 (got '{mailbox}'); multi-mailbox is a No-Go.")
        }

    limit = max(1, min(500, int(limit)))
    set_key = HISTORY_SET_KEY.format(mailbox=mailbox)

    try:
        r = _get_redis()
        if since_ts is not None:
            # Score-based range, newest first via reverse slice afterward.
            raw_ids = r.zrangebyscore(set_key, since_ts, "+inf")
            # zrangebyscore returns ascending by score — reverse and slice.
            raw_ids = list(reversed(raw_ids))[:limit]
        else:
            raw_ids = r.zrevrange(set_key, 0, limit - 1)

        messages: list[dict] = []
        for msgid in raw_ids:
            data = _hydrate(r, msgid)
            if data is None:
                # Race 1: blob missing or evicted — skip defensively.
                continue
            messages.append(
                {
                    "message_id": data.get("message_id") or msgid,
                    "from_addr": data.get("from_addr", ""),
                    "subject": data.get("subject", ""),
                    "body": data.get("body", ""),
                    "timestamp": _ts_to_iso(data.get("timestamp")),
                    "in_reply_to": data.get("in_reply_to", ""),
                }
            )
    except Exception as e:
        return {"error": str(e)}

    return {
        "messages": messages,
        "count": len(messages),
        "mailbox": mailbox,
    }


def search_history(
    query: str,
    mailbox: str = "INBOX",
    max_results: int = 10,
    max_age_days: int = 7,
) -> dict:
    """Search the email history cache for messages containing ``query``.

    Args:
        query: Substring to match (case-insensitive) against subject and body.
        mailbox: IMAP mailbox name — only ``INBOX`` in v1.
        max_results: Maximum entries to return.
        max_age_days: Age window in days.

    Returns:
        ``{"results": [...], "total_matches": N, "query": str}`` on success,
        or ``{"error": str}`` on Redis failure / empty query.
    """
    if not query or not query.strip():
        return {"error": "Query cannot be empty"}
    if mailbox != "INBOX":
        return {
            "error": (f"Only INBOX is supported in v1 (got '{mailbox}'); multi-mailbox is a No-Go.")
        }

    max_results = max(1, min(100, int(max_results)))
    cutoff = time.time() - (int(max_age_days) * 86400)
    query_lower = query.lower()

    set_key = HISTORY_SET_KEY.format(mailbox=mailbox)
    try:
        r = _get_redis()
        # Fetch all IDs within the age window (newest first)
        ids_with_scores = r.zrevrangebyscore(set_key, "+inf", cutoff, withscores=True)
    except Exception as e:
        return {"error": str(e)}

    results: list[dict] = []
    for msgid, ts in ids_with_scores:
        data = _hydrate(r, msgid)
        if data is None:
            continue
        subject = data.get("subject", "") or ""
        body = data.get("body", "") or ""
        if query_lower not in subject.lower() and query_lower not in body.lower():
            continue
        results.append(
            {
                "message_id": data.get("message_id") or msgid,
                "from_addr": data.get("from_addr", ""),
                "subject": subject,
                "body": body,
                "timestamp": _ts_to_iso(ts),
                "in_reply_to": data.get("in_reply_to", ""),
            }
        )
        if len(results) >= max_results:
            break

    return {
        "query": query,
        "results": results,
        "total_matches": len(results),
        "mailbox": mailbox,
        "time_window_days": max_age_days,
    }


def list_threads() -> dict:
    """List known email threads from the ``email:threads`` hash.

    Returns:
        ``{"threads": [...], "count": N}`` on success, or
        ``{"error": str}`` on Redis failure.
    """
    try:
        r = _get_redis()
        raw = r.hgetall(HISTORY_THREADS_KEY)
    except Exception as e:
        return {"error": str(e)}

    threads: list[dict] = []
    for root, blob in raw.items():
        try:
            data = json.loads(blob)
        except (json.JSONDecodeError, TypeError):
            continue
        threads.append(
            {
                "root": data.get("root") or root,
                "subject": data.get("subject", "") or "",
                "message_count": int(data.get("message_count") or 0),
                "last_ts": _ts_to_iso(data.get("last_ts")),
                "_sort_ts": float(data.get("last_ts") or 0.0),
                "participants": list(data.get("participants") or []),
            }
        )

    threads.sort(key=lambda t: t["_sort_ts"], reverse=True)
    for t in threads:
        t.pop("_sort_ts", None)

    return {"threads": threads, "count": len(threads)}
