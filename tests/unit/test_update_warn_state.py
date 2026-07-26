"""Unit tests for scripts/update/warn_state.py (#2329, #2328).

The transition-based suppression that stops human-gated /update checks
(missing Google OAuth token; missing Full Disk Access) from re-warning every
30-minute cycle. The contract: warn once per state transition, stay silent
while unchanged, warn again on a detail change / regression, note a resolve once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.update import warn_state


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir()
    return tmp_path


class TestShouldEmit:
    def test_first_unresolved_emits_and_records(self, project_dir: Path) -> None:
        assert warn_state.should_emit("google-token", "unresolved:no token", project_dir) is True
        stored = json.loads((project_dir / "data" / "update_warn_state.json").read_text())
        assert stored["google-token"] == "unresolved:no token"

    def test_same_signature_is_suppressed(self, project_dir: Path) -> None:
        warn_state.should_emit("sms_reader", "unresolved:no FDA", project_dir)
        # A second identical cycle must stay silent.
        assert warn_state.should_emit("sms_reader", "unresolved:no FDA", project_dir) is False

    def test_changed_detail_emits_again(self, project_dir: Path) -> None:
        warn_state.should_emit("google-token", "unresolved:path A", project_dir)
        assert warn_state.should_emit("google-token", "unresolved:path B", project_dir) is True

    def test_resolve_emits_once_then_silent(self, project_dir: Path) -> None:
        warn_state.should_emit("google-token", "unresolved:no token", project_dir)
        # First resolved cycle: emit a one-time resolved note.
        assert warn_state.should_emit("google-token", "", project_dir) is True
        # Subsequent resolved cycles: silent.
        assert warn_state.should_emit("google-token", "", project_dir) is False
        stored = json.loads((project_dir / "data" / "update_warn_state.json").read_text())
        assert "google-token" not in stored

    def test_resolve_when_never_warned_is_silent(self, project_dir: Path) -> None:
        assert warn_state.should_emit("calendar-config", "", project_dir) is False

    def test_regression_after_resolve_warns_again(self, project_dir: Path) -> None:
        warn_state.should_emit("sms_reader", "unresolved:no FDA", project_dir)
        warn_state.should_emit("sms_reader", "", project_dir)  # resolved
        # It regresses (grant revoked / Python bumped) — must warn again.
        assert warn_state.should_emit("sms_reader", "unresolved:no FDA", project_dir) is True

    def test_independent_keys_do_not_interfere(self, project_dir: Path) -> None:
        assert warn_state.should_emit("google-token", "u:x", project_dir) is True
        assert warn_state.should_emit("sms_reader", "u:y", project_dir) is True
        # Re-asserting google-token unchanged stays silent while sms_reader is untouched.
        assert warn_state.should_emit("google-token", "u:x", project_dir) is False

    def test_corrupt_state_file_is_treated_as_empty(self, project_dir: Path) -> None:
        (project_dir / "data" / "update_warn_state.json").write_text("not json{{{")
        # Must not raise; a corrupt file reads as no prior state, so this emits.
        assert warn_state.should_emit("google-token", "u:x", project_dir) is True

    def test_missing_data_dir_does_not_crash(self, tmp_path: Path) -> None:
        # No data/ dir pre-created — should_emit must create it and succeed.
        assert warn_state.should_emit("google-token", "u:x", tmp_path) is True
        assert (tmp_path / "data" / "update_warn_state.json").exists()
