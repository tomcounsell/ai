"""Meta-tests for the un-awaited-coroutine leak guardrail (#2120).

The guardrail is a ``pytest_runtest_teardown`` hook in ``tests/conftest.py`` that
runs one ``gc.collect()`` inside a warning recorder and re-emits any captured
``coroutine '...' was never awaited`` RuntimeWarning as a loud, test-attributed
warning (fatal under ``-W error::RuntimeWarning``).

These tests call the hook directly with a fake ``item`` so we assert the
mechanism without needing a nested pytest run.
"""

import gc
import types
import warnings

import tests.conftest as ct


def _make_cycle_held_leak():
    """Create a cycle-held, un-awaited coroutine and drop the local name.

    The holder dict references itself, so the whole cycle (and the coroutine it
    carries) is unreachable but survives ordinary refcount collection — it is
    only finalized by an explicit ``gc.collect()``. That is exactly the
    teardown-wedge shape the guardrail targets.
    """

    async def leaky_meta_coro():
        return 1

    holder = {}
    holder["self"] = holder
    holder["coro"] = leaky_meta_coro()
    # Return without awaiting/closing; `holder` goes out of scope but the
    # self-reference keeps the cycle alive until gc.collect().


def test_guardrail_surfaces_cycle_held_leak():
    """A cycle-held leaked coroutine is re-emitted, attributed to the node."""
    fake_item = types.SimpleNamespace(nodeid="meta::cycle_held_leak")
    gc.disable()
    try:
        _make_cycle_held_leak()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ct.pytest_runtest_teardown(fake_item, None)
    finally:
        gc.enable()

    surfaced = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "never awaited" in str(w.message)
        and "meta::cycle_held_leak" in str(w.message)
    ]
    assert surfaced, (
        "guardrail did not re-emit a test-attributed RuntimeWarning for a "
        f"cycle-held un-awaited coroutine; captured: {[str(w.message) for w in caught]}"
    )


def test_guardrail_silent_when_no_leak():
    """No leak → the guardrail emits nothing (no false positives)."""
    fake_item = types.SimpleNamespace(nodeid="meta::no_leak")
    # Collect first so any unrelated cycle-held coroutines from earlier work are
    # cleared and cannot be misattributed to this call.
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ct.pytest_runtest_teardown(fake_item, None)
    surfaced = [
        w
        for w in caught
        if issubclass(w.category, RuntimeWarning)
        and "never awaited" in str(w.message)
        and "meta::no_leak" in str(w.message)
    ]
    assert not surfaced, (
        f"guardrail emitted a spurious leak warning with no leak present: "
        f"{[str(w.message) for w in surfaced]}"
    )


def test_guardrail_disabled_via_env(monkeypatch):
    """COROUTINE_LEAK_GUARD=0 short-circuits the hook (no gc, no warning)."""
    monkeypatch.setattr(ct, "_COROUTINE_LEAK_GUARD_ENABLED", False)
    fake_item = types.SimpleNamespace(nodeid="meta::disabled")
    gc.disable()
    try:
        _make_cycle_held_leak()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ct.pytest_runtest_teardown(fake_item, None)
        # With the guard disabled the hook does not gc.collect, so the leaked
        # coroutine is NOT surfaced by this call.
        surfaced = [
            w
            for w in caught
            if issubclass(w.category, RuntimeWarning) and "meta::disabled" in str(w.message)
        ]
        assert not surfaced
    finally:
        # Clean up the deliberate leak so it does not misattribute elsewhere.
        # Swallow its own "never awaited" warning inside a recorder — it is a
        # test artifact of the disabled-guard path, not a real product leak.
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            gc.collect()
        gc.enable()
