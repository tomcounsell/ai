"""Tests for the ORM-free messenger callbacks (issue #1036).

The messenger in `agent/messenger.py` exposes two optional liveness callbacks:
  * on_heartbeat_tick() — fires on each 60s _watchdog tick.
  * on_stdout_event() — fires on each stdout event from the SDK.

(Durability plan #2494: the former ``on_sdk_started`` / ``on_sdk_finished`` pid
callbacks were deleted — dead code after the headless-runner cutover, which now
stamps the fenced execution record itself.)

Contract:
  * None callbacks are safe no-ops.
  * Callback exceptions are caught and logged; messenger resilience is NOT
    affected by ORM-side failures.
  * messenger.py imports NOTHING from `models/` — architectural boundary.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock


def _make_messenger(**kwargs):
    """Build a BossMessenger with a no-op send callback."""
    from agent.messenger import BossMessenger

    async def _send(_msg):
        return None

    return BossMessenger(_send_callback=_send, **kwargs)


class TestCallbackInvocation:
    def test_notify_heartbeat_tick_invokes_callback(self):
        cb = MagicMock()
        m = _make_messenger(on_heartbeat_tick=cb)
        m.notify_heartbeat_tick()
        cb.assert_called_once_with()

    def test_notify_stdout_event_invokes_callback(self):
        cb = MagicMock()
        m = _make_messenger(on_stdout_event=cb)
        m.notify_stdout_event()
        cb.assert_called_once_with()

    def test_none_callbacks_are_safe_defaults(self):
        """No callbacks provided → messenger works identically to today."""
        m = _make_messenger()  # no callbacks
        # All notify_* are safe no-ops
        m.notify_heartbeat_tick()
        m.notify_stdout_event()
        # No exception raised; test passes.


class TestCallbackExceptionResilience:
    """Callback exceptions must NEVER crash the messenger."""

    def test_on_heartbeat_tick_exception_does_not_propagate(self, caplog):
        def _boom():
            raise RuntimeError("boom")

        m = _make_messenger(on_heartbeat_tick=_boom)
        m.notify_heartbeat_tick()
        assert any("on_heartbeat_tick callback raised" in r.message for r in caplog.records)

    def test_on_stdout_event_exception_does_not_propagate(self, caplog):
        def _boom():
            raise RuntimeError("boom")

        m = _make_messenger(on_stdout_event=_boom)
        m.notify_stdout_event()
        assert any("on_stdout_event callback raised" in r.message for r in caplog.records)


class TestMessengerArchitecturalBoundary:
    """Messenger must import nothing from models/ (ORM boundary)."""

    def test_messenger_has_no_models_import(self):
        path = Path(__file__).resolve().parent.parent.parent / "agent" / "messenger.py"
        src = path.read_text()
        tree = ast.parse(src)
        imports_models = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("models"):
                    imports_models = True
                    break
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("models"):
                        imports_models = True
                        break
        assert not imports_models, (
            "agent/messenger.py must import nothing from models/ — "
            "the two-tier detector uses callbacks to keep messenger ORM-free"
        )


class TestBackgroundTaskCallbackWiring:
    """The BackgroundTask watchdog must call notify_heartbeat_tick."""

    def test_watchdog_source_invokes_heartbeat_callback(self):
        """Static check: _watchdog body references notify_heartbeat_tick."""
        import inspect

        from agent.messenger import BackgroundTask

        src = inspect.getsource(BackgroundTask._watchdog)
        assert "notify_heartbeat_tick" in src, (
            "BackgroundTask._watchdog must invoke notify_heartbeat_tick on each tick (#1036)"
        )
