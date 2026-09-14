"""Tests for the nightly-tests install outcome contract and staleness warning
(issue #2823).

``install_nightly_tests()`` returns a three-way ``Literal["installed",
"skipped", "failed"]`` instead of a bool, classified by matching the
installer's own stable success-line marker in stdout — never by exit code
alone, since the worktree refusal and the role-gate skip both legally exit 0.

``_nightly_tests_staleness_warning()`` is the only check in the update
pipeline that observes the *absence* of a run. It is keyed on
``max(plist_mtime, run_at)``, never on file-absence, so it must not warn on
the very ``/update`` run that installs the detector.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.update.run import _nightly_tests_staleness_warning
from scripts.update.service import install_nightly_tests


class TestInstallNightlyTestsOutcome:
    def _fake_result(self, returncode: int, stdout: str):
        r = MagicMock()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = ""
        return r

    def test_installed_on_success_marker(self, tmp_path: Path) -> None:
        installer = tmp_path / "scripts" / "install_nightly_tests.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text("#!/bin/bash\n")
        with patch(
            "scripts.update.service.run_cmd",
            return_value=self._fake_result(
                0, "...\nNightly regression test service installed successfully.\n"
            ),
        ):
            assert install_nightly_tests(tmp_path) == "installed"

    def test_skipped_on_zero_exit_without_marker(self, tmp_path: Path) -> None:
        installer = tmp_path / "scripts" / "install_nightly_tests.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text("#!/bin/bash\n")
        with patch(
            "scripts.update.service.run_cmd",
            return_value=self._fake_result(0, "Skipping nightly-tests install: worktree\n"),
        ):
            assert install_nightly_tests(tmp_path) == "skipped"

    def test_failed_on_nonzero_exit(self, tmp_path: Path) -> None:
        installer = tmp_path / "scripts" / "install_nightly_tests.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text("#!/bin/bash\n")
        with patch(
            "scripts.update.service.run_cmd",
            return_value=self._fake_result(1, "ERROR: something broke\n"),
        ):
            assert install_nightly_tests(tmp_path) == "failed"

    def test_failed_when_installer_missing(self, tmp_path: Path) -> None:
        assert install_nightly_tests(tmp_path) == "failed"

    def test_failed_on_exception(self, tmp_path: Path) -> None:
        installer = tmp_path / "scripts" / "install_nightly_tests.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text("#!/bin/bash\n")
        with patch("scripts.update.service.run_cmd", side_effect=OSError("boom")):
            assert install_nightly_tests(tmp_path) == "failed"


class TestNightlyTestsStalenessWarning:
    def _write_state(self, project_dir: Path, run_at: str | None) -> None:
        data_dir = project_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        state = {"run_at": run_at} if run_at is not None else {}
        (data_dir / "nightly_tests_last_run.json").write_text(json.dumps(state))

    def test_missing_state_but_fresh_plist_mtime_no_warning(self, tmp_path: Path) -> None:
        """The plist install that just happened must not warn about staleness —
        this is the case a file-absence-keyed check would get wrong."""
        fake_home = tmp_path / "home"
        plist_dir = fake_home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist = plist_dir / "com.valor.nightly-tests.plist"
        plist.write_text("<plist/>")
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is None

    def test_missing_state_and_stale_plist_mtime_warns(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        plist_dir = fake_home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist = plist_dir / "com.valor.nightly-tests.plist"
        plist.write_text("<plist/>")
        old = (datetime.now(UTC) - timedelta(days=5)).timestamp()
        import os as _os

        _os.utime(plist, (old, old))
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is not None
        assert "2+ days" in warning

    def test_malformed_run_at_falls_back_to_plist_mtime(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        plist_dir = fake_home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist = plist_dir / "com.valor.nightly-tests.plist"
        plist.write_text("<plist/>")  # fresh mtime
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        (project_dir / "data").mkdir()
        (project_dir / "data" / "nightly_tests_last_run.json").write_text("not json{{{")
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is None  # falls back to the fresh plist mtime

    def test_stale_run_at_warns(self, tmp_path: Path) -> None:
        """An old plist (installed a while ago, never reinstalled by /update
        since) plus a stale run_at must warn — the clock is
        max(plist_mtime, run_at), so both anchors need to be old."""
        fake_home = tmp_path / "home"
        plist_dir = fake_home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist = plist_dir / "com.valor.nightly-tests.plist"
        plist.write_text("<plist/>")
        old = (datetime.now(UTC) - timedelta(days=5)).timestamp()
        import os as _os

        _os.utime(plist, (old, old))
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        stale = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        self._write_state(project_dir, stale)
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is not None

    def test_same_day_run_at_no_warning(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        plist_dir = fake_home / "Library" / "LaunchAgents"
        plist_dir.mkdir(parents=True)
        plist = plist_dir / "com.valor.nightly-tests.plist"
        plist.write_text("<plist/>")
        old = (datetime.now(UTC) - timedelta(days=5)).timestamp()
        import os as _os

        _os.utime(plist, (old, old))
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        fresh = datetime.now(UTC).isoformat()
        self._write_state(project_dir, fresh)
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is None

    def test_no_plist_and_no_state_warns(self, tmp_path: Path) -> None:
        fake_home = tmp_path / "home"
        (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        with patch("scripts.update.run.Path.home", return_value=fake_home):
            warning = _nightly_tests_staleness_warning(project_dir)
        assert warning is not None
