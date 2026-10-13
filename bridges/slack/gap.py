"""Gap fill's budget and its per-chat progress.

Slack gives an internal app Tier 3 on `conversations.history` and
`conversations.replies`: about 50 calls a minute per method per
workspace. `Budget` holds each method to its share in a sliding window;
when a method's share is spent the pass stops, and the next tick resumes
it where it stopped, from the `Pass` kept in memory for that chat.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

THREAD_DAYS = 14  # threads with ledger activity this recent are read on each pass


class OutOfBudget(Exception):
    pass


class Budget:
    def __init__(self, calls: int = 50, window_s: float = 60.0):
        self.calls = calls
        self.window_s = window_s
        self._used: dict[str, deque[float]] = {}

    def take(self, method: str) -> None:
        """Spend one call of `method`; `OutOfBudget` when its share of the
        window is spent."""
        now = time.monotonic()
        used = self._used.setdefault(method, deque())
        while used and used[0] <= now - self.window_s:
            used.popleft()
        if len(used) >= self.calls:
            raise OutOfBudget(method)
        used.append(now)


@dataclass
class Pass:
    """One chat's pass in progress: it reads history from `start`, then
    each thread on `threads` from `start`, and only then moves the chat's
    seen `ts` to `top`."""

    channel: str  # Slack's channel id
    start: str
    inclusive: bool
    top: str
    cursor: str | None = None
    history_done: bool = False
    threads: list[str] = field(default_factory=list)
    thread_cursor: str | None = None

    def after(self, ts: str) -> bool:
        """Whether `ts` is one this pass reads."""
        return ts >= self.start if self.inclusive else ts > self.start
