"""Unified analytics system for metrics collection and querying.

Collects metrics from all subsystems (SDK client, session lifecycle,
SDLC pipeline, memory operations, crash tracker, health checks) and
stores them in SQLite (historical) and Redis (live counters).

Usage:
    from analytics.collector import record_metric
    record_metric("session.cost_usd", 0.05, {"session_id": "abc123"})
"""

from analytics.collector import record_metric

__all__ = ["record_metric"]
