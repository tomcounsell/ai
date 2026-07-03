"""Gap B throttle (#1843).

The per-iteration ``read_until_idle`` callback can fire many times per second
under verbose PTY output, so the freshness writer wired into it is gated behind
a minimum-interval throttle. ``_throttle`` is a pure, clock-injectable helper
whose call-frequency bound is asserted directly (no real-time sleeps).

The operator-visible dashboard surface for ``last_pty_read_loop_at`` is out of
scope here (dashboard/analytics surfaces are owned by #1842); this file covers
only the throttle mechanism that Gap B's callback runs through.
"""

from __future__ import annotations

from agent.granite_container.container import (
    PTY_READ_ITER_MIN_INTERVAL_S,
    _throttle,
)


# --------------------------------------------------------------------------
# Note #2 — call-frequency bound
# --------------------------------------------------------------------------
def test_throttle_bounds_call_frequency_under_rapid_calls():
    """100 rapid calls inside one window collapse to a single underlying call."""
    clock = {"t": 0.0}
    calls: list[str] = []
    wrapped = _throttle(calls.append, 1.0, clock=lambda: clock["t"])

    for i in range(100):
        clock["t"] = i * 0.005  # 0.000 .. 0.495s — all inside a 1s window
        wrapped(f"buf{i}")

    assert len(calls) == 1, "throttled callback must fire once per min-interval window"
    assert calls == ["buf0"], "the first call in the window is the one that fires"


def test_throttle_allows_one_call_per_window():
    clock = {"t": 0.0}
    calls: list[str] = []
    wrapped = _throttle(calls.append, 1.0, clock=lambda: clock["t"])

    # One call at the start of each successive window.
    for t in (0.0, 1.0, 2.0, 3.0):
        clock["t"] = t
        wrapped(f"t{t}")

    assert len(calls) == 4


def test_throttle_first_call_always_fires():
    calls: list[str] = []
    wrapped = _throttle(calls.append, PTY_READ_ITER_MIN_INTERVAL_S, clock=lambda: 42.0)
    wrapped("first")
    assert calls == ["first"]
