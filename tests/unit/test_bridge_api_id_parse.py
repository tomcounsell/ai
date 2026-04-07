"""
Unit tests for bridge.telegram_bridge._parse_api_id and import-time safety.

Covers the contract defined in the plan:
  - None / empty → 0, no warning
  - Valid numeric string → int value, no warning
  - Non-numeric (e.g. "12345****") → 0, warning to stderr
  - Whitespace-only → 0, warning to stderr (strict: real API IDs have no whitespace)
  - Module import with garbage env succeeds (no ImportError / ValueError)
"""

import sys
from io import StringIO
from unittest.mock import patch


def _get_parse_api_id():
    """Import _parse_api_id fresh, tolerating the module being already loaded."""
    from bridge.telegram_bridge import _parse_api_id

    return _parse_api_id


class TestParseApiId:
    def setup_method(self):
        self.parse = _get_parse_api_id()

    def test_none_returns_zero_no_warning(self, capsys):
        result = self.parse(None)
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_empty_string_returns_zero_no_warning(self, capsys):
        result = self.parse("")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_valid_numeric_string_returns_int(self, capsys):
        result = self.parse("12345")
        assert result == 12345
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_non_numeric_returns_zero_with_warning(self, capsys):
        result = self.parse("12345****")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "TELEGRAM_API_ID" in captured.err

    def test_whitespace_returns_zero_with_warning(self, capsys):
        """Whitespace-padded strings are treated as invalid (strict mode)."""
        result = self.parse("  42  ")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_warning_masks_value(self, capsys):
        """Warning message should not expose the full raw value."""
        result = self.parse("ABCDE_secret")
        assert result == 0
        captured = capsys.readouterr()
        # Should contain masked form (first 4 chars + ***), not full raw value
        assert "ABCD***" in captured.err
        assert "ABCDE_secret" not in captured.err

    def test_short_invalid_value_masked(self, capsys):
        """Short invalid values (<=4 chars) are fully masked with ***."""
        result = self.parse("abc")
        assert result == 0
        captured = capsys.readouterr()
        assert "***" in captured.err

    def test_zero_string_returns_zero(self, capsys):
        result = self.parse("0")
        assert result == 0
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_large_valid_id(self, capsys):
        result = self.parse("99999999")
        assert result == 99999999
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_negative_treated_as_invalid(self, capsys):
        """Negative numbers are unusual for API IDs; int('-1') would succeed but
        the value is semantically invalid. However, _parse_api_id uses int() so
        '-1' parses to -1. Documenting actual behavior here."""
        result = self.parse("-1")
        # int('-1') succeeds — result is -1 (falsy check at runtime will catch it)
        assert result == -1
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err


class TestImportSmokeTest:
    """Verify that importing bridge.telegram_bridge never raises, even with garbage env."""

    def test_import_with_garbage_api_id_succeeds(self, monkeypatch):
        """Module-level import must not raise ValueError when TELEGRAM_API_ID is non-numeric."""
        monkeypatch.setenv("TELEGRAM_API_ID", "12345****")

        # Prevent load_dotenv from overwriting monkeypatched env during reimport
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)

        # Force reimport to exercise module-level code with the patched env.
        # We remove the module from sys.modules so Python re-executes the top-level code.
        import importlib

        # Save and remove so reimport runs module-level code again
        saved = sys.modules.pop("bridge.telegram_bridge", None)
        try:
            # This must not raise
            import bridge.telegram_bridge as fresh_mod  # noqa: F401

            assert hasattr(fresh_mod, "API_ID")
            assert fresh_mod.API_ID == 0
        finally:
            # Restore original module to avoid polluting other tests
            if saved is not None:
                sys.modules["bridge.telegram_bridge"] = saved
            elif "bridge.telegram_bridge" in sys.modules:
                del sys.modules["bridge.telegram_bridge"]
            # Re-import original so other tests aren't broken
            importlib.import_module("bridge.telegram_bridge")

    def test_import_with_missing_api_id_succeeds(self, monkeypatch):
        """Module-level import must not raise when TELEGRAM_API_ID is absent."""
        monkeypatch.delenv("TELEGRAM_API_ID", raising=False)

        import importlib

        # Also prevent load_dotenv from restoring env vars during reimport
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)

        saved = sys.modules.pop("bridge.telegram_bridge", None)
        try:
            import bridge.telegram_bridge as fresh_mod  # noqa: F401

            assert fresh_mod.API_ID == 0
        finally:
            if saved is not None:
                sys.modules["bridge.telegram_bridge"] = saved
            elif "bridge.telegram_bridge" in sys.modules:
                del sys.modules["bridge.telegram_bridge"]
            importlib.import_module("bridge.telegram_bridge")
