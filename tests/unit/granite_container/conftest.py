"""Guard: granite unit tests must never spawn the real ``claude`` binary.

Issue #1632 (mode 3): granite unit tests that reached the real
``PTYDriver.spawn`` body (call-through spawn wrappers, import-time
``claude --print`` reachability probes) orphaned real ``claude``
processes at ~250 MB each and memory-crashed the machine.

Two layers of defense:

1. The autouse fixture below monkeypatches ``pexpect.spawn`` (the exec
   path ``PTYDriver.spawn`` bottoms out in) to raise. ``PTYDriver.spawn``
   itself stays real so its pre-spawn guard clauses (PTYDriverError on
   double-spawn etc.) remain testable — only the actual process exec is
   blocked. Tests opt into fakes by patching ``PTYDriver.spawn`` (or the
   whole driver class) as usual; the live smoke tests opt into REAL
   spawns by setting ``GRANITE_LIVE_SMOKE=1``.

2. The env-gated live tests (``test_pty_driver.py``,
   ``test_persona_priming.py``) gate their import-time reachability
   probes on ``GRANITE_LIVE_SMOKE=1`` — a fixture cannot intercept a
   ``@skipUnless`` decorator argument evaluated at module import.
"""

import os

import pytest

import agent.granite_container.pty_driver as _pty_driver_mod

LIVE_SMOKE_OPT_IN = os.environ.get("GRANITE_LIVE_SMOKE") == "1"


@pytest.fixture(autouse=True)
def _block_real_claude_spawn(monkeypatch):
    """Make the real ``claude`` exec path unreachable for every test.

    Raises with a pointer to the fake-patching convention unless the
    operator explicitly opted into live spawns via GRANITE_LIVE_SMOKE=1.
    """
    if LIVE_SMOKE_OPT_IN:
        yield
        return

    def _blocked_pexpect_spawn(*args, **kwargs):
        raise AssertionError(
            "Blocked a real `claude` TUI spawn inside "
            "tests/unit/granite_container (issue #1632: orphaned claude "
            "processes memory-crash the machine). Patch PTYDriver.spawn "
            "with a fake — e.g. patch('agent.granite_container.pty_pool."
            "PTYDriver.spawn', lambda self: None) — or set "
            "GRANITE_LIVE_SMOKE=1 to explicitly opt into live spawns."
        )

    monkeypatch.setattr(_pty_driver_mod.pexpect, "spawn", _blocked_pexpect_spawn)
    yield
