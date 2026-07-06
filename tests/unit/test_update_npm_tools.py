"""Unit tests for scripts/update/npm_tools.py invariants.

The `claude` CLI is installed via the NATIVE installer
(~/.local/bin/claude -> ~/.local/share/claude/versions/<version>/), never npm.
This guard survives the #1924 teardown of the TUI-contract version-pin check
(`check_claude_version_pin` died with the scraped-TUI substrate it protected);
the native-install invariant is substrate-independent.
"""

from __future__ import annotations

import pytest


class TestNativeInstallerNotManagedByNpm:
    def test_claude_not_in_npm_managed_packages(self):
        """claude must never be added to npm_tools.MANAGED_PACKAGES (native install only)."""
        from scripts.update.npm_tools import MANAGED_PACKAGES

        names = {pkg for pkg, _version in MANAGED_PACKAGES}
        assert "claude" not in names
        assert "@anthropic-ai/claude-code" not in names


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
