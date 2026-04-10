"""Tests for Google Workspace OAuth authentication module.

Tests cover error handling, verify_token(), CLI flags, and edge cases
for the auth module at tools/google_workspace/auth.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.google_workspace.auth import (
    GoogleAuthError,
    _service_cache,
    clear_tokens,
    get_credentials,
    verify_token,
)


@pytest.fixture(autouse=True)
def _clean_service_cache():
    """Ensure service cache is clean before and after each test."""
    _service_cache.clear()
    yield
    _service_cache.clear()


class TestGoogleAuthError:
    """Tests for the GoogleAuthError exception class."""

    def test_str_includes_recovery_command(self):
        err = GoogleAuthError("Token revoked.", recovery_command="valor-calendar --reauth")
        assert "valor-calendar --reauth" in str(err)
        assert "Token revoked." in str(err)

    def test_str_without_recovery_command(self):
        err = GoogleAuthError("Network error.")
        assert "Network error." in str(err)

    def test_recovery_command_attribute(self):
        err = GoogleAuthError("msg", recovery_command="valor-calendar --reauth")
        assert err.recovery_command == "valor-calendar --reauth"

    def test_no_recovery_command_attribute(self):
        err = GoogleAuthError("msg")
        assert err.recovery_command is None

    def test_str_in_fstring_surfaces_recovery(self):
        """Verify f"...{e}" pattern surfaces the recovery command."""
        err = GoogleAuthError("Token expired.", recovery_command="valor-calendar --reauth")
        message = f"Auth failed: {err}"
        assert "valor-calendar --reauth" in message


class TestVerifyToken:
    """Tests for verify_token() function."""

    def test_missing_token_file(self, tmp_path):
        with patch("tools.google_workspace.auth.TOKEN_PATH", tmp_path / "nonexistent.json"):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "missing"

    def test_invalid_json_token_file(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text("not valid json {{{")
        with patch("tools.google_workspace.auth.TOKEN_PATH", token_file):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "invalid"

    def test_empty_token_file(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text("")
        with patch("tools.google_workspace.auth.TOKEN_PATH", token_file):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "invalid"

    def test_non_dict_json_token_file(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text('"just a string"')
        with patch("tools.google_workspace.auth.TOKEN_PATH", token_file):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "invalid"

    def test_valid_token_with_scopes(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_data = {
            "token": "access_token_value",
            "refresh_token": "refresh_token_value",
            "client_id": "client_id",
            "client_secret": "client_secret",
        }
        token_file.write_text(json.dumps(token_data))

        mock_creds = MagicMock()
        mock_creds.scopes = {"https://www.googleapis.com/auth/calendar"}
        mock_creds.expired = False
        mock_creds.valid = True
        mock_creds.refresh_token = "refresh_token_value"

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = verify_token()
        assert result["valid"] is True
        assert result["status"] == "valid"
        assert "https://www.googleapis.com/auth/calendar" in result["scopes"]

    def test_scope_mismatch(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.scopes = {"https://www.googleapis.com/auth/drive"}
        mock_creds.expired = False
        mock_creds.valid = True
        mock_creds.refresh_token = "r"

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "scope_mismatch"

    def test_scopes_none_treated_as_unknown(self, tmp_path):
        """When creds.scopes is None, should not be treated as mismatch."""
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.scopes = None
        mock_creds.expired = False
        mock_creds.valid = True
        mock_creds.refresh_token = "r"

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = verify_token()
        # Should be valid (not scope_mismatch) since scopes are unknown
        assert result["valid"] is True
        assert result["scopes"] is None

    def test_expired_without_refresh_token(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.scopes = None
        mock_creds.expired = True
        mock_creds.valid = False
        mock_creds.refresh_token = None

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "expired"

    def test_expired_with_refresh_token(self, tmp_path):
        """Expired token with refresh token is still considered valid (auto-refreshable)."""
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.scopes = None
        mock_creds.expired = True
        mock_creds.valid = False
        mock_creds.refresh_token = "refresh_token"

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = verify_token()
        assert result["valid"] is True
        assert result["status"] == "valid"
        assert result["has_refresh_token"] is True

    def test_credentials_dir_missing(self, tmp_path):
        """verify_token should handle nonexistent parent directory gracefully."""
        nonexistent = tmp_path / "does_not_exist" / "token.json"
        with patch("tools.google_workspace.auth.TOKEN_PATH", nonexistent):
            result = verify_token()
        assert result["valid"] is False
        assert result["status"] == "missing"


class TestGetCredentials:
    """Tests for get_credentials() error handling."""

    def test_refresh_error_raises_google_auth_error(self, tmp_path):
        from google.auth.exceptions import RefreshError

        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh"
        mock_creds.valid = False
        mock_creds.refresh.side_effect = RefreshError("Token has been revoked")

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            pytest.raises(GoogleAuthError, match="valor-calendar --reauth"),
        ):
            get_credentials()

    def test_transport_error_raises_google_auth_error(self, tmp_path):
        from google.auth.exceptions import TransportError

        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh"
        mock_creds.valid = False
        mock_creds.refresh.side_effect = TransportError("Connection refused")

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
            pytest.raises(GoogleAuthError, match="Network error"),
        ):
            get_credentials()

    def test_missing_credentials_file_raises_file_not_found(self, tmp_path):
        token_file = tmp_path / "nonexistent_token.json"
        creds_file = tmp_path / "nonexistent_creds.json"

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch("tools.google_workspace.auth.CREDENTIALS_PATH", creds_file),
            pytest.raises(FileNotFoundError, match="Google credentials not found"),
        ):
            get_credentials()

    def test_service_cache_cleared_after_refresh(self, tmp_path):
        """Verify _service_cache is cleared after successful token refresh."""
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh"
        mock_creds.valid = False
        mock_creds.refresh.return_value = None  # success
        mock_creds.to_json.return_value = '{"token": "new"}'

        # Pre-populate the service cache
        _service_cache[("calendar", "v3")] = MagicMock()
        assert len(_service_cache) == 1

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            get_credentials()

        assert len(_service_cache) == 0

    def test_successful_refresh_saves_token(self, tmp_path):
        token_file = tmp_path / "token.json"
        token_file.write_text(json.dumps({"token": "t", "client_id": "c", "client_secret": "s"}))

        mock_creds = MagicMock()
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh"
        mock_creds.valid = False
        mock_creds.refresh.return_value = None
        mock_creds.to_json.return_value = '{"token": "new_access_token"}'

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch(
                "tools.google_workspace.auth.Credentials.from_authorized_user_file",
                return_value=mock_creds,
            ),
        ):
            result = get_credentials()

        assert result is mock_creds
        saved = json.loads(token_file.read_text())
        assert saved["token"] == "new_access_token"


class TestClearTokens:
    """Tests for clear_tokens() function."""

    def test_removes_per_machine_token(self, tmp_path):
        token_file = tmp_path / "token.machine.json"
        token_file.write_text('{"token": "t"}')

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", token_file),
            patch("tools.google_workspace.auth._SHARED_TOKEN_PATH", tmp_path / "shared.json"),
        ):
            clear_tokens()

        assert not token_file.exists()

    def test_removes_shared_legacy_token(self, tmp_path):
        shared_file = tmp_path / "google_token.json"
        shared_file.write_text('{"token": "shared"}')

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", tmp_path / "per_machine.json"),
            patch("tools.google_workspace.auth._SHARED_TOKEN_PATH", shared_file),
        ):
            clear_tokens()

        assert not shared_file.exists()

    def test_clears_service_cache(self, tmp_path):
        _service_cache[("calendar", "v3")] = MagicMock()

        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", tmp_path / "nonexistent.json"),
            patch("tools.google_workspace.auth._SHARED_TOKEN_PATH", tmp_path / "shared.json"),
        ):
            clear_tokens()

        assert len(_service_cache) == 0


class TestCLIFlags:
    """Tests for --check and --reauth CLI flag handlers."""

    def test_check_flag_valid_exits_0(self, tmp_path):
        valid_result = {
            "valid": True,
            "status": "valid",
            "scopes": ["https://www.googleapis.com/auth/calendar"],
            "expired": False,
            "has_refresh_token": True,
        }
        with patch("tools.google_workspace.auth.verify_token", return_value=valid_result):
            from tools.valor_calendar import _handle_check

            with pytest.raises(SystemExit) as exc_info:
                _handle_check()
            assert exc_info.value.code == 0

    def test_check_flag_invalid_exits_1(self, tmp_path):
        invalid_result = {
            "valid": False,
            "status": "missing",
            "scopes": None,
            "expired": False,
            "has_refresh_token": False,
        }
        with patch("tools.google_workspace.auth.verify_token", return_value=invalid_result):
            from tools.valor_calendar import _handle_check

            with pytest.raises(SystemExit) as exc_info:
                _handle_check()
            assert exc_info.value.code == 1

    def test_reauth_calls_clear_and_get(self):
        mock_clear = MagicMock()
        mock_get = MagicMock()

        with (
            patch("tools.google_workspace.auth.clear_tokens", mock_clear),
            patch("tools.google_workspace.auth.get_credentials", mock_get),
        ):
            from tools.valor_calendar import _handle_reauth

            _handle_reauth()

        mock_clear.assert_called_once()
        mock_get.assert_called_once()

    def test_check_scope_mismatch_exits_1(self):
        mismatch_result = {
            "valid": False,
            "status": "scope_mismatch",
            "scopes": ["https://www.googleapis.com/auth/drive"],
            "expired": False,
            "has_refresh_token": True,
        }
        with patch("tools.google_workspace.auth.verify_token", return_value=mismatch_result):
            from tools.valor_calendar import _handle_check

            with pytest.raises(SystemExit) as exc_info:
                _handle_check()
            assert exc_info.value.code == 1

    def test_check_expired_no_refresh_exits_1(self):
        expired_result = {
            "valid": False,
            "status": "expired",
            "scopes": None,
            "expired": True,
            "has_refresh_token": False,
        }
        with patch("tools.google_workspace.auth.verify_token", return_value=expired_result):
            from tools.valor_calendar import _handle_check

            with pytest.raises(SystemExit) as exc_info:
                _handle_check()
            assert exc_info.value.code == 1
