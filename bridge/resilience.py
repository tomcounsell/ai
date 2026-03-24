"""Reusable CircuitBreaker for dependency health management.

Implements the standard circuit breaker pattern with three states:
- CLOSED: Normal operation, requests pass through
- OPEN: Dependency is down, requests fail fast
- HALF_OPEN: Probing dependency with a single request

State transitions:
- CLOSED -> OPEN: failure_threshold failures within window
- OPEN -> HALF_OPEN: after half_open_interval elapses
- HALF_OPEN -> CLOSED: probe succeeds
- HALF_OPEN -> OPEN: probe fails
"""

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker with configurable thresholds and callbacks.

    Args:
        name: Human-readable name for logging (e.g., "anthropic", "telegram").
        failure_threshold: Number of failures within failure_window to open circuit.
        failure_window: Time window in seconds for counting failures.
        half_open_interval: Seconds to wait before probing after opening.
        on_open: Optional callback when circuit opens.
        on_close: Optional callback when circuit closes.

    Thread safety: Uses asyncio.Lock for state transitions. Single-value reads
    (state property) are GIL-atomic and don't need the lock.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        failure_window: float = 60.0,
        half_open_interval: float = 30.0,
        on_open: Callable[[], None] | None = None,
        on_close: Callable[[], None] | None = None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.failure_window = failure_window
        self.half_open_interval = half_open_interval
        self._on_open = on_open
        self._on_close = on_close

        self._state = CircuitState.CLOSED
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._lock = asyncio.Lock()
        self._total_failures = 0
        self._total_successes = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state. GIL-atomic read, no lock needed."""
        # Auto-transition from OPEN to HALF_OPEN if interval elapsed
        if self._state == CircuitState.OPEN and self._opened_at is not None:
            if time.time() - self._opened_at >= self.half_open_interval:
                return CircuitState.HALF_OPEN
        return self._state

    @property
    def is_open(self) -> bool:
        """True if circuit is OPEN (not HALF_OPEN)."""
        return self.state == CircuitState.OPEN

    @property
    def is_closed(self) -> bool:
        return self.state == CircuitState.CLOSED

    @property
    def stats(self) -> dict:
        """Return circuit breaker statistics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "recent_failures": len(self._failures),
            "total_failures": self._total_failures,
            "total_successes": self._total_successes,
            "failure_threshold": self.failure_threshold,
        }

    def allows_request(self) -> bool:
        """Check if a request should be allowed through.

        CLOSED: always allow
        OPEN: never allow (fail fast)
        HALF_OPEN: allow one probe request
        """
        current = self.state
        if current == CircuitState.CLOSED:
            return True
        if current == CircuitState.HALF_OPEN:
            return True  # Allow probe
        return False

    async def record_success(self) -> None:
        """Record a successful request. Closes circuit if in HALF_OPEN."""
        async with self._lock:
            self._total_successes += 1
            if self._state in (CircuitState.HALF_OPEN, CircuitState.OPEN):
                # Check if we're in the half-open window
                if self._opened_at and time.time() - self._opened_at >= self.half_open_interval:
                    old_state = self._state
                    self._state = CircuitState.CLOSED
                    self._failures.clear()
                    self._opened_at = None
                    logger.info(
                        "Circuit '%s' CLOSED (was %s, probe succeeded)",
                        self.name,
                        old_state.value,
                    )
                    if self._on_close:
                        try:
                            self._on_close()
                        except Exception:
                            pass

    async def record_failure(self, error: Exception | None = None) -> None:
        """Record a failed request. Opens circuit if threshold exceeded."""
        async with self._lock:
            now = time.time()
            self._total_failures += 1
            self._failures.append(now)

            # Prune old failures outside the window
            cutoff = now - self.failure_window
            while self._failures and self._failures[0] < cutoff:
                self._failures.popleft()

            # If in HALF_OPEN (probe failed), go back to OPEN
            if self._state == CircuitState.HALF_OPEN or (
                self._state == CircuitState.OPEN
                and self._opened_at
                and now - self._opened_at >= self.half_open_interval
            ):
                self._state = CircuitState.OPEN
                self._opened_at = now
                logger.warning(
                    "Circuit '%s' OPEN (probe failed, error=%s)",
                    self.name,
                    error,
                )
                return

            # Check threshold for CLOSED -> OPEN transition
            if self._state == CircuitState.CLOSED:
                if len(self._failures) >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = now
                    logger.warning(
                        "Circuit '%s' OPEN (%d failures in %.0fs, error=%s)",
                        self.name,
                        len(self._failures),
                        self.failure_window,
                        error,
                    )
                    if self._on_open:
                        try:
                            self._on_open()
                        except Exception:
                            pass

    async def reset(self) -> None:
        """Manually reset circuit to CLOSED state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failures.clear()
            self._opened_at = None
