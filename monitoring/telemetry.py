"""Redis-backed telemetry collector for the Observer Agent.

Records decisions, stage transitions, tool usage, and interjections.
All Redis writes are wrapped in try/except so telemetry never breaks callers.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import redis

logger = logging.getLogger(__name__)

# Module-level cached Redis connection
_redis_client: redis.Redis | None = None

# Redis key constants
KEY_DECISIONS = "telemetry:observer:decisions"
KEY_PIPELINE = "telemetry:pipeline:completions"
KEY_TOOL_USAGE = "telemetry:observer:tool_usage"
KEY_INTERJECTIONS = "telemetry:interjections"
DAILY_KEY_PREFIX = "telemetry:daily"
DAILY_TTL_SECONDS = 604800  # 7 days

# Health thresholds
ERROR_RATE_DEGRADED = 0.10
ERROR_RATE_UNHEALTHY = 0.25


def _get_redis() -> redis.Redis:
    """Get or create a cached Redis connection."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.Redis(host="localhost", port=6379, socket_timeout=2)
    return _redis_client


def _daily_key() -> str:
    """Return the daily rollup key for today."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    return f"{DAILY_KEY_PREFIX}:{today}"


def record_decision(
    session_id: str,
    correlation_id: str,
    action: str,
    reason: str = "",
) -> None:
    """Record an observer decision (steer, deliver, or error).

    Increments counters on both the main decisions hash and a daily rollup key.
    """
    try:
        r = _get_redis()
        field = f"{action}_count"
        r.hincrby(KEY_DECISIONS, field, 1)
        dk = _daily_key()
        r.hincrby(dk, field, 1)
        r.expire(dk, DAILY_TTL_SECONDS)
    except Exception:
        logger.debug(
            "Failed to record decision: session=%s correlation=%s action=%s",
            session_id,
            correlation_id,
            action,
        )


def record_stage_transition(
    session_id: str,
    correlation_id: str,
    stage: str,
    old_status: str,
    new_status: str,
) -> None:
    """Record a pipeline stage transition.

    Tracks started and completed counts per stage.
    """
    try:
        r = _get_redis()
        if new_status == "completed":
            field = f"{stage}_completed"
        else:
            field = f"{stage}_started"
        r.hincrby(KEY_PIPELINE, field, 1)
        dk = _daily_key()
        r.hincrby(dk, field, 1)
        r.expire(dk, DAILY_TTL_SECONDS)
    except Exception:
        logger.debug(
            "Failed to record stage transition: session=%s stage=%s %s->%s",
            session_id,
            stage,
            old_status,
            new_status,
        )


def record_tool_use(
    session_id: str,
    correlation_id: str,
    tool_name: str,
    duration_ms: int = 0,
) -> None:
    """Record a tool invocation by the observer."""
    try:
        r = _get_redis()
        r.hincrby(KEY_TOOL_USAGE, tool_name, 1)
    except Exception:
        logger.debug(
            "Failed to record tool use: session=%s tool=%s",
            session_id,
            tool_name,
        )


def record_interjection(
    session_id: str,
    correlation_id: str,
    message_count: int,
    action: str,
) -> None:
    """Record an observer interjection event.

    Stores the last 100 interjection events as JSON in a Redis list.
    """
    try:
        r = _get_redis()
        entry = json.dumps(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "session_id": session_id,
                "correlation_id": correlation_id,
                "message_count": message_count,
                "action": action,
            }
        )
        r.lpush(KEY_INTERJECTIONS, entry)
        r.ltrim(KEY_INTERJECTIONS, 0, 99)
    except Exception:
        logger.debug(
            "Failed to record interjection: session=%s action=%s",
            session_id,
            action,
        )


def _decode_hash(data: dict[bytes, bytes]) -> dict[str, int]:
    """Decode a Redis hash of bytes into str->int dict."""
    return {k.decode(): int(v) for k, v in data.items()}


def get_summary() -> dict[str, Any]:
    """Return a summary of all telemetry counters and recent interjections.

    Returns a zero-valued dict structure if no data exists or Redis is unavailable.
    """
    empty: dict[str, Any] = {
        "decisions": {},
        "pipeline": {},
        "tool_usage": {},
        "recent_interjections": [],
    }
    try:
        r = _get_redis()
        decisions = _decode_hash(r.hgetall(KEY_DECISIONS))
        pipeline = _decode_hash(r.hgetall(KEY_PIPELINE))
        tool_usage = _decode_hash(r.hgetall(KEY_TOOL_USAGE))
        raw_interjections = r.lrange(KEY_INTERJECTIONS, 0, 9)
        interjections = [json.loads(entry) for entry in raw_interjections]
        return {
            "decisions": decisions,
            "pipeline": pipeline,
            "tool_usage": tool_usage,
            "recent_interjections": interjections,
        }
    except Exception:
        logger.debug("Failed to get telemetry summary")
        return empty


def check_observer_health() -> dict[str, Any]:
    """Check observer health based on decision error rate.

    Returns status dict with: status, error_rate, total_decisions, violations.
    Thresholds: error_rate > 0.10 = degraded, > 0.25 = unhealthy.
    """
    try:
        r = _get_redis()
        data = _decode_hash(r.hgetall(KEY_DECISIONS))
        steer = data.get("steer_count", 0)
        deliver = data.get("deliver_count", 0)
        error = data.get("error_count", 0)
        total = steer + deliver + error

        if total == 0:
            return {
                "status": "ok",
                "error_rate": 0.0,
                "total_decisions": 0,
                "violations": [],
            }

        error_rate = error / total
        violations: list[str] = []

        if error_rate > ERROR_RATE_UNHEALTHY:
            status = "unhealthy"
            violations.append(
                f"error_rate {error_rate:.2%} exceeds {ERROR_RATE_UNHEALTHY:.0%} threshold"
            )
        elif error_rate > ERROR_RATE_DEGRADED:
            status = "degraded"
            violations.append(
                f"error_rate {error_rate:.2%} exceeds {ERROR_RATE_DEGRADED:.0%} threshold"
            )
        else:
            status = "ok"

        return {
            "status": status,
            "error_rate": error_rate,
            "total_decisions": total,
            "violations": violations,
        }
    except Exception:
        logger.debug("Failed to check observer health")
        return {
            "status": "unknown",
            "error_rate": 0.0,
            "total_decisions": 0,
            "violations": ["redis_unavailable"],
        }
