"""Regression tests for ``scripts/update/service.py::install_worker`` env injection (issue #1171).

The standalone ``scripts/install_worker.sh`` already injects ``.env`` vars into
the worker plist's ``EnvironmentVariables`` dict so launchd-spawned worker
processes can see ``VALOR_PROJECT_KEY`` and other secrets. ``/update --full``
calls ``scripts.update.service.install_worker()`` instead, which historically
only did template substitution. Without the env-injection block ported here,
``/update --full`` would write a worker plist that silently lacks
``VALOR_PROJECT_KEY`` and the recovery reflections would fall back to the wrong
namespace.

These tests catch a future refactor that drops or breaks the injection block.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.update.service import (
    _inject_env_into_plist,
    _launchctl_label_running,
    install_worker,
)


def _fake_run_cmd(list_pid: str | None = "12345", bootstrap_rc: int = 0):
    """Build a ``run_cmd`` side_effect that fakes launchctl without touching it.

    - ``launchctl list`` returns a single line ``<list_pid>\\t0\\tcom.valor.worker``
      when ``list_pid`` is a digit string, ``-\\t0\\tcom.valor.worker`` when
      ``list_pid`` is ``"-"``, or empty output when ``list_pid`` is ``None``
      (label absent). This is what the #2089 live-PID verify reads.
    - ``launchctl bootstrap`` returns ``bootstrap_rc`` (non-zero simulates the
      silent-bootstrap failure that used to be swallowed).
    - Everything else returns rc=0 with empty output.
    """

    def _run(cmd, *args, **kwargs):
        class _P:
            returncode = 0
            stdout = ""
            stderr = ""

        p = _P()
        if cmd[:2] == ["launchctl", "list"]:
            if list_pid is None:
                p.stdout = ""
            else:
                p.stdout = f"{list_pid}\t0\tcom.valor.worker\n"
        elif cmd[:2] == ["launchctl", "bootstrap"]:
            p.returncode = bootstrap_rc
            if bootstrap_rc != 0:
                p.stderr = "Bootstrap failed: 5: Input/output error"
        return p

    return _run


def _write_stub_plist(path: Path, label: str = "com.valor.worker") -> None:
    """Write a minimal plist matching the worker template's structure."""
    plist = {
        "Label": label,
        "ProgramArguments": ["/usr/bin/python3", "-m", "worker"],
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": "/Users/valorengels",
            "VALOR_LAUNCHD": "1",
        },
    }
    with open(path, "wb") as f:
        plistlib.dump(plist, f)


def _read_env_vars(path: Path) -> dict[str, str]:
    with open(path, "rb") as f:
        plist = plistlib.load(f)
    return plist.get("EnvironmentVariables", {})


# ---------------------------------------------------------------------------
# _inject_env_into_plist (helper) tests
# ---------------------------------------------------------------------------


class TestInjectEnvIntoPlist:
    def test_injects_env_vars_into_empty_environment_dict(self, tmp_path: Path) -> None:
        plist_path = tmp_path / "worker.plist"
        env_path = tmp_path / ".env"

        # Plist with NO EnvironmentVariables key
        with open(plist_path, "wb") as f:
            plistlib.dump({"Label": "com.valor.worker"}, f)

        env_path.write_text("VALOR_PROJECT_KEY=valor\nFOO=bar\n")

        injected = _inject_env_into_plist(plist_path, env_path)
        assert injected == 2

        env_vars = _read_env_vars(plist_path)
        assert env_vars["VALOR_PROJECT_KEY"] == "valor"
        assert env_vars["FOO"] == "bar"

    def test_injects_valor_project_key_alongside_other_vars(self, tmp_path: Path) -> None:
        plist_path = tmp_path / "worker.plist"
        env_path = tmp_path / ".env"

        _write_stub_plist(plist_path)
        env_path.write_text(
            "VALOR_PROJECT_KEY=valor\nANTHROPIC_API_KEY=sk-test\nREDIS_URL=redis://localhost:6379\n"
        )

        injected = _inject_env_into_plist(plist_path, env_path)
        assert injected == 3

        env_vars = _read_env_vars(plist_path)
        # The B1 regression check: VALOR_PROJECT_KEY MUST land in the plist.
        assert env_vars["VALOR_PROJECT_KEY"] == "valor", (
            f"VALOR_PROJECT_KEY missing from plist; got keys {sorted(env_vars)}"
        )
        # And the placeholder vars should still be present.
        assert env_vars["PATH"] == "/usr/local/bin:/usr/bin:/bin"
        assert env_vars["HOME"] == "/Users/valorengels"
        assert env_vars["VALOR_LAUNCHD"] == "1"

    def test_does_not_overwrite_existing_keys(self, tmp_path: Path) -> None:
        plist_path = tmp_path / "worker.plist"
        env_path = tmp_path / ".env"

        _write_stub_plist(plist_path)
        # .env tries to override PATH — must NOT clobber the placeholder.
        env_path.write_text("PATH=/should/not/win\nVALOR_PROJECT_KEY=valor\n")

        injected = _inject_env_into_plist(plist_path, env_path)
        assert injected == 1, "PATH was already in plist; only VALOR_PROJECT_KEY should be added"

        env_vars = _read_env_vars(plist_path)
        assert env_vars["PATH"] == "/usr/local/bin:/usr/bin:/bin"  # placeholder preserved
        assert env_vars["VALOR_PROJECT_KEY"] == "valor"

    def test_skips_none_values(self, tmp_path: Path) -> None:
        plist_path = tmp_path / "worker.plist"
        env_path = tmp_path / ".env"

        _write_stub_plist(plist_path)
        # An entry like ``KEY`` (no =) becomes None in dotenv_values
        env_path.write_text("BARE_KEY\nVALOR_PROJECT_KEY=valor\n")

        injected = _inject_env_into_plist(plist_path, env_path)
        # Only VALOR_PROJECT_KEY should be injected; BARE_KEY is None.
        assert injected == 1

        env_vars = _read_env_vars(plist_path)
        assert "BARE_KEY" not in env_vars
        assert env_vars["VALOR_PROJECT_KEY"] == "valor"

    def test_missing_plist_returns_zero(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text("VALOR_PROJECT_KEY=valor\n")
        assert _inject_env_into_plist(tmp_path / "absent.plist", env_path) == 0

    def test_missing_env_returns_zero(self, tmp_path: Path) -> None:
        plist_path = tmp_path / "worker.plist"
        _write_stub_plist(plist_path)
        assert _inject_env_into_plist(plist_path, tmp_path / ".env-absent") == 0

    def test_empty_string_value_lands_in_plist(self, tmp_path: Path) -> None:
        """``KEY=`` produces an empty-string value, which IS injected.

        This documents the failure mode the C2 empty-string defense in the
        recovery code mitigates: a misconfigured ``VALOR_PROJECT_KEY=`` line
        would land an empty string in the plist, but the reader code at
        ``agent.sustainability._get_project_key()`` strips and falls back
        to ``"valor"``.
        """
        plist_path = tmp_path / "worker.plist"
        env_path = tmp_path / ".env"
        _write_stub_plist(plist_path)
        env_path.write_text("VALOR_PROJECT_KEY=\n")

        injected = _inject_env_into_plist(plist_path, env_path)
        assert injected == 1
        env_vars = _read_env_vars(plist_path)
        assert env_vars["VALOR_PROJECT_KEY"] == ""


# ---------------------------------------------------------------------------
# install_worker integration test (skip launchctl side-effects)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInstallWorkerEnvInjection:
    """Black-box: call ``install_worker`` against a stub project_dir and assert
    the destination plist ends up with ``VALOR_PROJECT_KEY=valor`` injected.

    This is the regression check for B1 (issue #1171). Without the injection
    block ported into ``service.py``, a future refactor could silently regress
    ``/update --full`` to template-only mode.
    """

    def test_install_worker_injects_valor_project_key(self, tmp_path: Path, monkeypatch) -> None:
        # Build a fake project dir with a worker plist source + .env
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        plist_src = project_dir / "com.valor.worker.plist"
        plist_src.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>__SERVICE_LABEL__</string>
    <key>WorkingDirectory</key><string>__PROJECT_DIR__</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>__HOME_DIR__/.bin</string>
    </dict>
</dict>
</plist>
"""
        )

        env_file = project_dir / ".env"
        env_file.write_text(
            "VALOR_PROJECT_KEY=valor\n"
            "ANTHROPIC_API_KEY=sk-fake\n"
            "PATH=/should/not/clobber/placeholder\n"
        )

        # Redirect HOME so plist_dst lands in tmp_path
        fake_home = tmp_path / "home"
        (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
        monkeypatch.setattr("scripts.update.service.Path.home", lambda: fake_home)

        # Stub run_cmd so we never touch real launchctl. `launchctl list` must
        # report the worker label with a live PID so the #2089 live-PID verify
        # in install_worker() passes; all other commands return rc=0.
        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="12345")):
            ok = install_worker(project_dir)

        assert ok, "install_worker returned False"

        plist_dst = fake_home / "Library" / "LaunchAgents" / "com.valor.worker.plist"
        assert plist_dst.exists(), "destination plist was not written"

        env_vars = _read_env_vars(plist_dst)
        # B1 REGRESSION ASSERTION: VALOR_PROJECT_KEY must be present.
        assert env_vars.get("VALOR_PROJECT_KEY") == "valor", (
            f"B1 regression: VALOR_PROJECT_KEY missing from plist after install_worker; "
            f"got EnvironmentVariables keys {sorted(env_vars)}"
        )
        assert env_vars.get("ANTHROPIC_API_KEY") == "sk-fake"
        # Placeholder PATH wins over .env PATH (template precedence).
        assert "should/not/clobber" not in env_vars.get("PATH", "")


def _make_worker_project(tmp_path: Path, monkeypatch) -> Path:
    """Minimal worker project_dir + redirected HOME for install_worker tests."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "com.valor.worker.plist").write_text(
        '<?xml version="1.0"?><plist version="1.0"><dict>'
        "<key>Label</key><string>__SERVICE_LABEL__</string>"
        "</dict></plist>"
    )
    fake_home = tmp_path / "home"
    (fake_home / "Library" / "LaunchAgents").mkdir(parents=True)
    monkeypatch.setattr("scripts.update.service.Path.home", lambda: fake_home)
    return project_dir


class TestLaunchctlLabelRunning:
    """Unit tests for the #2089 live-PID verify helper."""

    def test_numeric_pid_is_running(self) -> None:
        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="4321")):
            assert _launchctl_label_running("com.valor.worker") is True

    def test_dash_pid_is_not_running(self) -> None:
        # Stale registration: label loaded but no process (PID column is "-").
        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="-")):
            assert _launchctl_label_running("com.valor.worker") is False

    def test_absent_label_is_not_running(self) -> None:
        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid=None)):
            assert _launchctl_label_running("com.valor.worker") is False


class TestInstallWorkerBootstrapVerify:
    """#2089: install_worker must not report success on a silent bootstrap fail."""

    def test_failed_bootstrap_unrecovered_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        project_dir = _make_worker_project(tmp_path, monkeypatch)
        # Loop A retries the EIO bootstrap RETRIES times; zero the sleep so the
        # test doesn't wait between attempts.
        monkeypatch.setattr("scripts.update.service.LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP", 0)
        # Bootstrap fails AND the label never shows a live PID (kickstart also
        # fails to bring it up) -> install_worker must return False, not True.
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=_fake_run_cmd(list_pid="-", bootstrap_rc=1),
        ):
            assert install_worker(project_dir) is False

    def test_failed_bootstrap_recovered_by_kickstart_returns_true(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        project_dir = _make_worker_project(tmp_path, monkeypatch)
        monkeypatch.setattr("scripts.update.service.LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP", 0)
        # Bootstrap fails, but the label ends up running with a live PID (as if
        # kickstart -k recovered it) -> install_worker returns True.
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=_fake_run_cmd(list_pid="9999", bootstrap_rc=1),
        ):
            assert install_worker(project_dir) is True

    def test_clean_bootstrap_with_live_pid_returns_true(self, tmp_path: Path, monkeypatch) -> None:
        project_dir = _make_worker_project(tmp_path, monkeypatch)
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=_fake_run_cmd(list_pid="1234", bootstrap_rc=0),
        ):
            assert install_worker(project_dir) is True
