"""Unified resilience module for the Telegram bridge.

Provides a reusable CircuitBreaker class that prevents resource waste
during dependency outages. Circuit breakers track failure rates and
short-circuit calls when a dependency is known to be down, periodically
probing for recovery.

States:
    CLOSED  - Normal operation; failures are counted.
    OPEN    - Dependency is down; calls fail immediately.
    HALF_OPEN - Probing recovery; one call is allowed through.

Usage:
    cb = CircuitBreaker("anthropic", failure_threshold=5, window_seconds=60)
    if cb.is_closed():
        try:
            result = await call_api()
            cb.record_success()
        except Exception:
            cb.record_failure()
    else:
        # dependency is down, use fallback
        ...
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Tracks failures for a dependency and short-circuits when threshold is exceeded.

    Args:
        name: Human-readable dependency name (used in logs).
        failure_threshold: Number of failures in the window to trip the circuit.
            Set to 0 to make the circuit always open (useful for testing).
        window_seconds: Rolling window for counting failures.
        probe_interval_seconds: How often to allow a probe call when circuit is open.
        on_open: Optional callback invoked when circuit transitions to OPEN.
        on_close: Optional callback invoked when circuit transitions to CLOSED.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        probe_interval_seconds: float = 30.0,
        on_open: Callable[[], Any] | None = None,
        on_close: Callable[[], Any] | None = None,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.probe_interval_seconds = probe_interval_seconds
        self.on_open = on_open
        self.on_close = on_close

        self._state = CircuitState.CLOSED
        self._failures: list[float] = []  # timestamps of failures within window
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0
        self._lock = asyncio.Lock()
        self._total_failures: int = 0
        self._total_successes: int = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state. Read is lock-free (GIL-safe for enum)."""
        return self._state

    def is_closed(self) -> bool:
        """Return True if calls should proceed normally."""
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.HALF_OPEN:
            return True
        # OPEN: check if probe interval has elapsed
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.probe_interval_seconds:
                # Transition to half-open for a probe
                self._state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit '%s' entering HALF_OPEN for probe after %.1fs",
                    self.name,
                    elapsed,
                )
                return True
        return False

    def is_open(self) -> bool:
        """Return True if the circuit is open (calls should be short-circuited)."""
        return not self.is_closed()

    def _prune_old_failures(self) -> None:
        """Remove failures outside the rolling window."""
        cutoff = time.monotonic() - self.window_seconds
        self._failures = [t for t in self._failures if t > cutoff]

    async def record_failure(self) -> None:
        """Record a failure. May transition circuit to OPEN."""
        async with self._lock:
            now = time.monotonic()
            self._failures.append(now)
            self._last_failure_time = now
            self._total_failures += 1
            self._prune_old_failures()

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed, re-open
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning("Circuit '%s' probe failed, returning to OPEN", self.name)
                if self.on_open:
                    _safe_callback(self.on_open)
                return

            if self._state == CircuitState.CLOSED:
                if len(self._failures) >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = now
                    logger.warning(
                        "Circuit '%s' OPENED after %d failures in %.0fs window",
                        self.name,
                        len(self._failures),
                        self.window_seconds,
                    )
                    if self.on_open:
                        _safe_callback(self.on_open)

    async def record_success(self) -> None:
        """Record a success. May transition circuit from HALF_OPEN to CLOSED."""
        async with self._lock:
            self._total_successes += 1

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failures.clear()
                logger.info("Circuit '%s' CLOSED after successful probe", self.name)
                if self.on_close:
                    _safe_callback(self.on_close)

    def reset(self) -> None:
        """Force-reset circuit to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failures.clear()
        self._opened_at = 0.0
        logger.info("Circuit '%s' manually reset to CLOSED", self.name)

    def status(self) -> dict[str, Any]:
        """Return current status as a dict (for health reporting)."""
        self._prune_old_failures()
        return {
            "name": self.name,
            "state": self._state.value,
            "failures_in_window": len(self._failures),
            "failure_threshold": self.failure_threshold,
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "last_failure_ago": (
                round(time.monotonic() - self._last_failure_time, 1)
                if self._last_failure_time > 0
                else None
            ),
            "opened_ago": (
                round(time.monotonic() - self._opened_at, 1) if self._opened_at > 0 else None
            ),
        }


def _safe_callback(cb: Callable[[], Any]) -> None:
    """Invoke a callback, catching and logging any exception."""
    try:
        result = cb()
        # If callback returns a coroutine, we can't await it here (we're
        # inside a sync helper called from an async context with a lock held).
        # Log a warning so callers know to use sync callbacks.
        if asyncio.iscoroutine(result):
            logger.warning(
                "Circuit breaker callback returned a coroutine; "
                "use sync callbacks or schedule via asyncio.create_task"
            )
            result.close()  # prevent RuntimeWarning
    except Exception as e:
        logger.error("Circuit breaker callback error: %s", e)
