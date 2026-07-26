"""Unit tests for check_google_token's absence-vs-permission distinction (#2329).

~/Desktop is TCC-protected; pathlib's `.exists()` swallows an EPERM and reports
False, which under the launchd->bash->python /update chain produced a false
"missing token, run OAuth" every cycle even when the token was present. The check
now uses os.stat to tell a true absence (FileNotFoundError -> OAuth step) from a
permission-denied stat (PermissionError -> TCC step) and emits the right human
action for each.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.update import verify


class TestCheckGoogleToken:
    def test_present_token_is_available(self) -> None:
        fake = Path("/Users/x/Desktop/Valor/google_token.mac.json")
        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", fake),
            patch("scripts.update.verify.os.stat", return_value=object()),
        ):
            check = verify.check_google_token(Path("."))
        assert check.available is True
        assert check.version == fake.name
        assert check.error is None

    def test_true_absence_emits_oauth_step(self) -> None:
        fake = Path("/Users/x/Desktop/Valor/google_token.mac.json")
        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", fake),
            patch("scripts.update.verify.os.stat", side_effect=FileNotFoundError()),
        ):
            check = verify.check_google_token(Path("."))
        assert check.available is False
        assert "gws auth" in check.error
        # A true absence must NOT claim the token might be present.
        assert "may be PRESENT" not in check.error

    def test_permission_denied_emits_tcc_step_not_oauth(self) -> None:
        """The #2329 false-negative: token present but unstattable under TCC. The
        escalation must say so, not tell the human to redo OAuth."""
        fake = Path("/Users/x/Desktop/Valor/google_token.mac.json")
        with (
            patch("tools.google_workspace.auth.TOKEN_PATH", fake),
            patch("scripts.update.verify.os.stat", side_effect=PermissionError()),
        ):
            check = verify.check_google_token(Path("."))
        assert check.available is False
        assert "may be PRESENT" in check.error
        assert "TCC" in check.error
        # Must not misdirect to a mandatory OAuth re-run when the token is present.
        assert "No OAuth is needed" in check.error
