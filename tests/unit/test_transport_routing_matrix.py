"""Transport routing matrix (plan #1842).

Exercises the config-precedence resolver ``_resolve_role_transports`` across all
four transport combinations plus the invalid-config backstop, and confirms the
resolved map is what the dispatch seam threads into ``BridgeAdapter``.

The end-to-end executor path (message → worktree → BridgeAdapter) is covered in
``test_session_executor_granite.py``; here we isolate the resolution logic so
the matrix is deterministic and free of worktree/PTY provisioning.
"""

from __future__ import annotations

from agent.session_executor import _VALID_ROLE_TRANSPORTS, _resolve_role_transports


class TestResolveRoleTransports:
    """Precedence: project block > settings.granite defaults > literal 'pty'."""

    def test_default_no_config_is_both_pty(self):
        # Absent transport block → both roles default to pty (settings default).
        assert _resolve_role_transports({}) == {"pm": "pty", "dev": "pty"}

    def test_none_project_config_is_both_pty(self):
        assert _resolve_role_transports(None) == {"pm": "pty", "dev": "pty"}

    def test_explicit_both_pty(self):
        cfg = {"transport": {"pm": "pty", "dev": "pty"}}
        assert _resolve_role_transports(cfg) == {"pm": "pty", "dev": "pty"}

    def test_pty_headless(self):
        cfg = {"transport": {"pm": "pty", "dev": "headless"}}
        assert _resolve_role_transports(cfg) == {"pm": "pty", "dev": "headless"}

    def test_headless_pty(self):
        cfg = {"transport": {"pm": "headless", "dev": "pty"}}
        assert _resolve_role_transports(cfg) == {"pm": "headless", "dev": "pty"}

    def test_headless_headless(self):
        cfg = {"transport": {"pm": "headless", "dev": "headless"}}
        assert _resolve_role_transports(cfg) == {"pm": "headless", "dev": "headless"}

    def test_partial_block_fills_missing_role_from_default(self):
        # Only pm declared → dev falls through to the settings default (pty).
        cfg = {"transport": {"pm": "headless"}}
        assert _resolve_role_transports(cfg) == {"pm": "headless", "dev": "pty"}

    def test_non_dict_transport_block_falls_back_to_defaults(self):
        # A malformed block (caught loudly at validate time) resolves to the
        # safe both-pty default here rather than crashing the resolver.
        cfg = {"transport": "pty"}
        assert _resolve_role_transports(cfg) == {"pm": "pty", "dev": "pty"}

    def test_project_block_overrides_settings_default(self, monkeypatch):
        from config.settings import settings

        monkeypatch.setattr(settings.granite, "dev_transport", "headless", raising=False)
        # Project block wins over the settings default for pm (explicit pty),
        # while dev with no project entry inherits the (now headless) default.
        cfg = {"transport": {"pm": "pty"}}
        assert _resolve_role_transports(cfg) == {"pm": "pty", "dev": "headless"}


class TestInvalidConfigBackstop:
    """The executor's defensive fail-loud backstop on an invalid resolved value."""

    def test_valid_transports_constant(self):
        assert set(_VALID_ROLE_TRANSPORTS) == {"pty", "headless"}

    def test_settings_default_out_of_vocab_is_caught_by_backstop(self, monkeypatch):
        # If a settings default is somehow invalid, the resolver surfaces it
        # verbatim so the executor backstop can finalize the session failed.
        from config.settings import settings

        monkeypatch.setattr(settings.granite, "dev_transport", "bogus", raising=False)
        resolved = _resolve_role_transports({})
        assert resolved["dev"] == "bogus"
        bad = [v for v in resolved.values() if v not in _VALID_ROLE_TRANSPORTS]
        assert bad == ["bogus"]
