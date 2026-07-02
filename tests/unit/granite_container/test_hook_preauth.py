"""Startup pre-authorization + fallback-signal tests (plan #1688, Task 3 + concern #3).

Task 3 (companion): the per-session settings file pre-answers the permission
surface (``permissions.defaultMode = "bypassPermissions"``) so the permission
bar is authorized via the settings source, shrinking the ``startup_parser``
scrape surface. The trust-folder dialog dismissal stays in ``startup_parser``
as a fallback (shrink, not delete — plan Rabbit Holes), so this test also
asserts the existing ``startup_login_wedge`` / trust-folder parse is unaffected.

Concern #3: ``record_hook_fallback`` is the observable fleet signal that the
hook contract is degrading (silent-hook fallback), guarding the [ORDERED]
No-Go against removing the idle fallback prematurely.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent.granite_container import hook_edge
from agent.granite_container.hook_edge import (
    generate_hook_settings,
    generate_pair_hook_settings,
    hook_fallback_count,
    record_hook_fallback,
)
from agent.granite_container.startup_parser import StartupEvent, parse_startup_frame


class TestStartupPreAuthorization(unittest.TestCase):
    def test_preauth_writes_bypass_permission_mode(self) -> None:
        with TemporaryDirectory() as d:
            settings_path, _ = generate_hook_settings(d, Path(d) / "e.ndjson", pre_authorize=True)
            data = json.loads(Path(settings_path).read_text())
            self.assertEqual(data["permissions"]["defaultMode"], "bypassPermissions")

    def test_preauth_off_omits_permissions_block(self) -> None:
        with TemporaryDirectory() as d:
            settings_path, _ = generate_hook_settings(d, Path(d) / "e.ndjson", pre_authorize=False)
            data = json.loads(Path(settings_path).read_text())
            self.assertNotIn("permissions", data)

    def test_pair_settings_preauthorize_both_ptys(self) -> None:
        with TemporaryDirectory() as d:
            paths = generate_pair_hook_settings(d, pre_authorize=True)
            for settings in (paths.pm_settings, paths.dev_settings):
                data = json.loads(Path(settings).read_text())
                self.assertEqual(data["permissions"]["defaultMode"], "bypassPermissions")
            # Distinct edge files so a PM Stop never lands in the Dev edge file.
            self.assertNotEqual(paths.pm_edge, paths.dev_edge)

    def test_trust_folder_dismissal_retained_as_fallback(self) -> None:
        """Pre-auth shrinks the scrape surface but the trust-folder parse stays."""
        match = parse_startup_frame("Do you trust this folder?\n1. Yes, I trust this folder")
        self.assertEqual(match.event, StartupEvent.TRUST_FOLDER_PROMPT)
        self.assertEqual(match.response, "1")


class TestHookFallbackSignal(unittest.TestCase):
    def test_record_fallback_increments_and_never_raises(self) -> None:
        before = hook_fallback_count()
        count = record_hook_fallback("sid", "stop_wait_timeout")
        self.assertEqual(count, before + 1)
        self.assertEqual(hook_fallback_count(), before + 1)

    def test_record_fallback_tolerates_none_session(self) -> None:
        # Must never raise regardless of args (fail-silent contract).
        self.assertIsInstance(record_hook_fallback(None, ""), int)


class TestPreAuthModuleContract(unittest.TestCase):
    def test_generate_hook_settings_default_pre_authorizes(self) -> None:
        # The bridge_adapter path calls generate_hook_settings without an explicit
        # pre_authorize, relying on the default-on companion (Task 3 wiring).
        with TemporaryDirectory() as d:
            settings_path, _ = generate_hook_settings(d, Path(d) / "e.ndjson")
            data = json.loads(Path(settings_path).read_text())
            self.assertIn("permissions", data)

    def test_forwarder_path_constant_is_absolute(self) -> None:
        self.assertTrue(Path(hook_edge._FORWARDER_PATH).is_absolute())


if __name__ == "__main__":
    unittest.main()
