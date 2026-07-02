"""Tests for the D1b startup contract-check gate (issue #1817).

`worker.__main__._evaluate_contract_check_gate` is the pure decision logic
behind the startup probe added after the `shutil.which("claude")` check: it
decides whether a scraped-TUI-marker mismatch (`verify_tui_marker_contract`,
tested separately in test_tui_marker_contract.py) should pass silently, log
a critical warning, or refuse to start — scoped to whether the fleet has any
PTY-transport role configured (a #1842 headless-only fleet is immune to
TUI-marker drift by construction).

`worker.__main__._any_pty_role_configured` resolves that PTY-transport
question across the global settings default AND every project's
`transport.pm`/`transport.dev` override.
"""

from __future__ import annotations

from unittest.mock import patch

import worker.__main__ as worker_main


class TestEvaluateContractCheckGate:
    def test_contract_ok_passes_regardless_of_other_flags(self):
        assert worker_main._evaluate_contract_check_gate(True, True, True) == "pass"
        assert worker_main._evaluate_contract_check_gate(True, False, False) == "pass"

    def test_mismatch_with_no_pty_role_skips_headless(self):
        """A fully-headless fleet must never hard-fail on a TUI marker mismatch."""
        assert worker_main._evaluate_contract_check_gate(False, False, True) == "skip_headless"
        assert worker_main._evaluate_contract_check_gate(False, False, False) == "skip_headless"

    def test_mismatch_with_pty_role_and_no_enforce_warns(self):
        assert worker_main._evaluate_contract_check_gate(False, True, False) == "warn"

    def test_mismatch_with_pty_role_and_enforce_hard_fails(self):
        assert worker_main._evaluate_contract_check_gate(False, True, True) == "hard_fail"


class TestAnyPtyRoleConfigured:
    def test_global_default_pty_returns_true(self):
        with patch("config.settings.settings") as mock_settings:
            mock_settings.granite.pm_transport = "pty"
            mock_settings.granite.dev_transport = "headless"
            assert worker_main._any_pty_role_configured() is True

    def test_global_default_fully_headless_and_no_project_override_returns_false(self):
        with (
            patch("config.settings.settings") as mock_settings,
            patch("bridge.routing.load_config", return_value={"projects": {}}),
        ):
            mock_settings.granite.pm_transport = "headless"
            mock_settings.granite.dev_transport = "headless"
            assert worker_main._any_pty_role_configured() is False

    def test_project_override_to_pty_returns_true_even_if_global_headless(self):
        cfg = {
            "projects": {
                "acme": {"transport": {"pm": "headless", "dev": "pty"}},
            }
        }
        with (
            patch("config.settings.settings") as mock_settings,
            patch("bridge.routing.load_config", return_value=cfg),
        ):
            mock_settings.granite.pm_transport = "headless"
            mock_settings.granite.dev_transport = "headless"
            assert worker_main._any_pty_role_configured() is True

    def test_config_read_error_fails_open_to_true(self):
        """A transient projects.json read failure must not silently disarm
        a load-bearing contract-check — fail open (assume PTY IS configured)."""
        with (
            patch("config.settings.settings") as mock_settings,
            patch("bridge.routing.load_config", side_effect=RuntimeError("boom")),
        ):
            mock_settings.granite.pm_transport = "headless"
            mock_settings.granite.dev_transport = "headless"
            assert worker_main._any_pty_role_configured() is True

    def test_malformed_project_entries_are_skipped_not_fatal(self):
        cfg = {"projects": {"bad": "not-a-dict", "ok": {"transport": "also-not-a-dict"}}}
        with (
            patch("config.settings.settings") as mock_settings,
            patch("bridge.routing.load_config", return_value=cfg),
        ):
            mock_settings.granite.pm_transport = "headless"
            mock_settings.granite.dev_transport = "headless"
            assert worker_main._any_pty_role_configured() is False
