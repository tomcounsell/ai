"""Dependency health tracking for the bridge.

Aggregates circuit breaker states from bridge/resilience.py into a
unified health summary that the watchdog and status reporting can consume.

NOTE: The watchdog (monitoring/bridge_watchdog.py) runs as a separate
process and cannot access in-memory circuit breaker state from this module.
Watchdog health checks use their own HTTP/process-level probes.  If
cross-process circuit state sharing is ever needed, consider writing
summary() output to a file (e.g. data/health.json) that the watchdog can
read.  See GitHub issue #495 for context.

Usage:
    from bridge.health import DependencyHealth

    health = DependencyHealth()
    health.register(anthropic_cb)
    health.register(telegram_cb)

    summary = health.summary()
    # {"overall": "degraded", "circuits": {...}}
"""

from __future__ import annotations

import logging
from typing import Any

from bridge.resilience import CircuitBreaker, CircuitState

logger = logging.getLogger(__name__)

# Module-level singleton so all parts of the bridge share one instance.
_instance: DependencyHealth | None = None


def get_health() -> DependencyHealth:
    """Return the module-level DependencyHealth singleton."""
    global _instance
    if _instance is None:
        _instance = DependencyHealth()
    return _instance


class DependencyHealth:
    """Tracks per-dependency circuit breaker states.

    Provides a summary dict for watchdog consumption and formatted
    status for logging.
    """

    def __init__(self) -> None:
        self._circuits: dict[str, CircuitBreaker] = {}

    def register(self, circuit: CircuitBreaker) -> None:
        """Register a circuit breaker for health tracking."""
        self._circuits[circuit.name] = circuit
        logger.debug("Registered circuit '%s' for health tracking", circuit.name)

    def unregister(self, name: str) -> None:
        """Remove a circuit breaker from tracking."""
        self._circuits.pop(name, None)

    def get_circuit(self, name: str) -> CircuitBreaker | None:
        """Look up a registered circuit by name."""
        return self._circuits.get(name)

    def summary(self) -> dict[str, Any]:
        """Return a summary dict of all dependency health states.

        Returns:
            {
                "overall": "healthy" | "degraded" | "down",
                "circuits": {
                    "name": { ...circuit status dict... },
                    ...
                }
            }
        """
        if not self._circuits:
            return {"overall": "healthy", "circuits": {}}

        circuits = {}
        open_count = 0
        for name, cb in self._circuits.items():
            status = cb.status()
            circuits[name] = status
            if status["state"] == CircuitState.OPEN.value:
                open_count += 1

        total = len(self._circuits)
        if open_count == 0:
            overall = "healthy"
        elif open_count < total:
            overall = "degraded"
        else:
            overall = "down"

        return {"overall": overall, "circuits": circuits}

    def formatted_status(self) -> str:
        """Return a human-readable status string for logging.

        Example:
            "healthy | anthropic:closed telegram:closed"
            "degraded | anthropic:open(5 failures) telegram:closed"
        """
        s = self.summary()
        overall = s["overall"]

        if not s["circuits"]:
            return f"{overall} | no circuits registered"

        parts = []
        for name, info in s["circuits"].items():
            state = info["state"]
            failures = info.get("failures_in_window", 0)
            if state == "closed":
                parts.append(f"{name}:closed")
            elif state == "open":
                parts.append(f"{name}:open({failures} failures)")
            else:
                parts.append(f"{name}:{state}")

        return f"{overall} | {' '.join(parts)}"
