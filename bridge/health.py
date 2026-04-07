"""Dependency health tracking for the bridge.

Centralizes health status of all external dependencies (Telegram, Anthropic, Redis)
via their circuit breakers. Provides a summary dict for diagnostics and the job
status CLI.
"""

import logging
from typing import Any

from bridge.resilience import CircuitBreaker

logger = logging.getLogger(__name__)


class DependencyHealth:
    """Registry of circuit breakers for all external dependencies.

    Usage:
        health = DependencyHealth()
        health.register("anthropic", anthropic_circuit)
        health.register("telegram", telegram_circuit)

        summary = health.summary()
        # {"anthropic": {"state": "closed", ...}, "telegram": {"state": "open", ...}}
    """

    def __init__(self) -> None:
        self._circuits: dict[str, CircuitBreaker] = {}

    def register(self, name: str, circuit: CircuitBreaker) -> None:
        """Register a circuit breaker for a dependency."""
        self._circuits[name] = circuit

    def get(self, name: str) -> CircuitBreaker | None:
        """Get a circuit breaker by dependency name."""
        return self._circuits.get(name)

    def summary(self) -> dict[str, Any]:
        """Return health summary for all registered dependencies."""
        return {name: cb.stats for name, cb in self._circuits.items()}

    def all_healthy(self) -> bool:
        """True if all circuits are closed."""
        return all(cb.is_closed for cb in self._circuits.values())

    def degraded_dependencies(self) -> list[str]:
        """Return names of dependencies with open or half-open circuits."""
        return [name for name, cb in self._circuits.items() if not cb.is_closed]


# Global singleton for bridge-wide health tracking
_health = DependencyHealth()


def get_health() -> DependencyHealth:
    """Get the global DependencyHealth instance."""
    return _health
