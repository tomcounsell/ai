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


class TestActive:
    """The retrieval surface for Risk 4: a suppressed condition must stay
    discoverable by an operator who missed the one emission (#2845)."""

    def test_active_returns_empty_on_fresh_project(self, project_dir: Path) -> None:
        assert warn_state.active(project_dir) == {}

    def test_active_returns_currently_suppressed_map(self, project_dir: Path) -> None:
        warn_state.should_emit("gws-auth", "needs_auth:none", project_dir)
        warn_state.should_emit("env-completeness", "missing:1", project_dir)
        result = warn_state.active(project_dir)
        assert result == {"gws-auth": "needs_auth:none", "env-completeness": "missing:1"}

    def test_active_fails_soft_on_corrupt_state_file(self, project_dir: Path) -> None:
        (project_dir / "data" / "update_warn_state.json").write_text("not json{{{")
        assert warn_state.active(project_dir) == {}

    def test_active_reflects_resolution(self, project_dir: Path) -> None:
        warn_state.should_emit("gws-auth", "needs_auth:none", project_dir)
        warn_state.should_emit("gws-auth", "", project_dir)  # resolved
        assert warn_state.active(project_dir) == {}


class TestStatePath:
    def test_state_path_resolves_to_repo_data_dir(self) -> None:
        assert (
            warn_state._state_path(warn_state.PROJECT_ROOT)
            == warn_state.PROJECT_ROOT / "data" / "update_warn_state.json"
        )
        # Do NOT assert the live map is non-empty — machine-dependent for
        # the same reason the 27-vs-64 env-completeness count is.


class TestSuppressedPrefixConstant:
    def test_suppressed_prefix_is_the_bare_stdout_spelling(self) -> None:
        # log() prepends "[update] " — this constant must NOT carry that
        # prefix, since bridge/update.py matches stdout-derived status_lines.
        assert not warn_state.SUPPRESSED_PREFIX.startswith("[update]")
        assert warn_state.SUPPRESSED_PREFIX.startswith("suppressed")


class TestMainCLI:
    def test_main_prints_state_path_first(self, project_dir: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(warn_state, "PROJECT_ROOT", project_dir)
        monkeypatch.setattr("sys.argv", ["warn_state"])
        exit_code = warn_state._main()
        out = capsys.readouterr().out
        assert exit_code == 0
        first_line = out.splitlines()[0]
        assert first_line == f"state: {warn_state._state_path(project_dir)}"

    def test_main_prints_nothing_suppressed_on_clean_state(
        self, project_dir: Path, monkeypatch, capsys
    ) -> None:
        monkeypatch.setattr(warn_state, "PROJECT_ROOT", project_dir)
        monkeypatch.setattr("sys.argv", ["warn_state"])
        warn_state._main()
        out = capsys.readouterr().out
        assert "nothing suppressed" in out

    def test_main_prints_every_suppressed_key(self, project_dir: Path, monkeypatch, capsys) -> None:
        warn_state.should_emit("gws-auth", "needs_auth:none", project_dir)
        monkeypatch.setattr("sys.argv", ["warn_state", "--project-dir", str(project_dir)])
        warn_state._main()
        out = capsys.readouterr().out
        assert "gws-auth" in out
        assert "needs_auth:none" in out

    def test_main_respects_project_dir_override(
        self, project_dir: Path, tmp_path: Path, monkeypatch, capsys
    ) -> None:
        """A wrong root must be visibly wrong (the state path line), not a
        silent empty map indistinguishable from 'nothing suppressed'."""
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        (other_dir / "data").mkdir()
        warn_state.should_emit("gws-auth", "needs_auth:none", other_dir)

        monkeypatch.setattr("sys.argv", ["warn_state", "--project-dir", str(project_dir)])
        warn_state._main()
        out = capsys.readouterr().out
        assert str(project_dir) in out.splitlines()[0]
        assert "gws-auth" not in out
