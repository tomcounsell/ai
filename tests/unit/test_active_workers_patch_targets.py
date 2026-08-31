"""Patching `_active_workers` must target the binding the reader captured (#3055).

`_active_workers` is owned by `agent.session_state`. Consumers reach it in two
different ways, and the correct patch target differs per consumer:

- `agent.session_health` does `from agent.session_state import _active_workers`
  at MODULE scope, so it holds its own binding. Rebinding the name on any other
  module leaves the health check reading the real registry.
- `scripts.update.run._cleanup_stale_sessions` imports inside the function, so it
  re-reads `agent.session_state`'s attribute on every call.

Neither is reached by rebinding `agent.agent_session_queue._active_workers` — the
old re-export hub, which several test files named. Those patches were silent
fiction: the tests looked isolated and were not. It surfaced as the recurring
`test_live_worker_skips_session` nightly failure (#3056/#3058/#3060), where the
patch could not install the live worker the assertion needed.

`agent.agent_session_queue._cli_show_status` is the one legitimate hub target: it
lives in that module and reads that module's own global. So the rule is not "never
patch the hub" — it is "patch the binding the reader actually reads".
"""

from __future__ import annotations

from unittest.mock import patch


class TestOwnership:
    def test_session_state_is_the_owner(self):
        import agent.session_state as state

        assert isinstance(state._active_workers, dict)

    def test_session_health_holds_its_own_binding(self):
        """A module-scope `from X import y` copies the binding; it does not alias X."""
        import agent.session_health as health
        import agent.session_state as state

        assert health._active_workers is state._active_workers, (
            "both names must start out bound to the same dict object"
        )


class TestPatchTargetsReach:
    """Demonstrated red: the wrong target must be shown not to work, or the
    next author reintroduces it (#2658)."""

    def test_patching_the_hub_does_not_reach_session_health(self):
        import agent.session_health as health

        sentinel = {"sentinel-worker": object()}
        with patch("agent.agent_session_queue._active_workers", sentinel):
            assert health._active_workers is not sentinel, (
                "rebinding the hub must NOT change what session_health reads — "
                "if this ever passes, the vacuous-patch class is back"
            )

    def test_patching_session_health_reaches_session_health(self):
        import agent.session_health as health

        sentinel = {"sentinel-worker": object()}
        with patch("agent.session_health._active_workers", sentinel):
            assert health._active_workers is sentinel

    def test_patching_the_hub_does_not_reach_a_function_scope_importer(self):
        """`scripts.update.run` imports from `agent.session_state` at call time."""
        sentinel = {"sentinel-worker": object()}
        with patch("agent.agent_session_queue._active_workers", sentinel):
            from agent.session_state import _active_workers as seen

            assert seen is not sentinel

    def test_patching_session_state_reaches_a_function_scope_importer(self):
        sentinel = {"sentinel-worker": object()}
        with patch("agent.session_state._active_workers", sentinel):
            from agent.session_state import _active_workers as seen

            assert seen is sentinel

    def test_mutating_the_shared_dict_reaches_every_reader(self):
        """The alternative that always works, since all names point at one dict."""
        import agent.session_health as health
        import agent.session_state as state

        marker = object()
        state._active_workers["__probe__"] = marker
        try:
            assert health._active_workers.get("__probe__") is marker
        finally:
            state._active_workers.pop("__probe__", None)
