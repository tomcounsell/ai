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


def _fake_run_cmd(
    list_pid: str | None = "12345",
    bootstrap_rc: int = 0,
    bootstrap_stderr: str | None = None,
    bootstrap_calls: list | None = None,
):
    """Build a ``run_cmd`` side_effect that fakes launchctl without touching it.

    - ``launchctl list`` returns a single line ``<list_pid>\\t0\\tcom.valor.worker``
      when ``list_pid`` is a digit string, ``-\\t0\\tcom.valor.worker`` when
      ``list_pid`` is ``"-"``, or empty output when ``list_pid`` is ``None``
      (label absent). This is what the #2089 live-PID verify reads.
    - ``launchctl bootstrap`` returns ``bootstrap_rc`` (non-zero simulates the
      silent-bootstrap failure that used to be swallowed). On failure it emits
      ``bootstrap_stderr`` if given, else the errno-5 EIO message (loop A's retry
      trigger). Pass a list to ``bootstrap_calls`` to record each bootstrap
      invocation (for asserting loop A's attempt count).
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
            if bootstrap_calls is not None:
                bootstrap_calls.append(cmd)
            p.returncode = bootstrap_rc
            if bootstrap_rc != 0:
                p.stderr = bootstrap_stderr or "Bootstrap failed: 5: Input/output error"
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

    def test_non_eio_bootstrap_failure_skips_retry(self, tmp_path: Path, monkeypatch) -> None:
        """Loop A gate parity: a non-errno-5 bootstrap failure must NOT be retried.

        Mirrors the shell helper's errno-5-only gating — a genuine plist error
        short-circuits to exactly ONE bootstrap attempt, then the kickstart -k
        fallback, rather than burning LAUNCHCTL_BOOTSTRAP_RETRIES attempts + sleeps.
        A regression widening the gate to retry all failures would make this count
        jump to LAUNCHCTL_BOOTSTRAP_RETRIES.
        """
        project_dir = _make_worker_project(tmp_path, monkeypatch)
        monkeypatch.setattr("scripts.update.service.LAUNCHCTL_BOOTSTRAP_RETRY_SLEEP", 0)
        bootstrap_calls: list = []
        # Non-EIO failure; label ends up live (as if kickstart -k recovered it).
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=_fake_run_cmd(
                list_pid="9999",
                bootstrap_rc=1,
                bootstrap_stderr="Bootstrap failed: 112: Could not find specified service",
                bootstrap_calls=bootstrap_calls,
            ),
        ):
            assert install_worker(project_dir) is True
        # Exactly one bootstrap attempt — loop A did not retry the non-EIO failure.
        assert len(bootstrap_calls) == 1, bootstrap_calls


# ---------------------------------------------------------------------------
# .worktrees/ install guard (issue #2100, AC6)
# ---------------------------------------------------------------------------


class TestWorktreeInstallGuard:
    """The real ``scripts/install_worker.sh`` refuses to install the global
    worker service from a ``.worktrees/`` checkout unless
    ``ALLOW_WORKTREE_WORKER_INSTALL=1``.

    Runs the real script in a constructed temp path (its ``PROJECT_DIR`` is
    derived from the script's own location), so the guard block is exercised
    verbatim. The guard runs FIRST — before any ``source lib/launchctl.sh`` or
    venv/plist checks — so a worktree path aborts at the guard, and a
    main-checkout path passes the guard (then fails later at the missing
    ``lib/launchctl.sh``, which is fine: the guard let it through).
    """

    _GUARD_MSG = "refusing to install the worker from a git worktree"

    def _staged_script(self, tmp_path: Path, project_rel: str) -> Path:
        """Copy the real install_worker.sh under ``{tmp}/{project_rel}/scripts/``."""
        import shutil

        real = Path(__file__).resolve().parents[2] / "scripts" / "install_worker.sh"
        assert real.exists(), f"real install_worker.sh not found at {real}"
        scripts_dir = tmp_path / project_rel / "scripts"
        scripts_dir.mkdir(parents=True)
        dst = scripts_dir / "install_worker.sh"
        shutil.copy(real, dst)
        return dst

    def _run(self, script: Path, env_extra: dict | None = None):
        import os as _os
        import subprocess

        env = dict(_os.environ)
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

    def test_worktree_path_is_blocked(self, tmp_path: Path) -> None:
        """A PROJECT_DIR under ``.worktrees/`` aborts with exit 1 + guard message."""
        script = self._staged_script(tmp_path, "repo/.worktrees/slug")
        result = self._run(script)
        assert result.returncode == 1, result.stderr
        assert self._GUARD_MSG in result.stderr

    def test_worktree_path_allowed_with_override(self, tmp_path: Path) -> None:
        """``ALLOW_WORKTREE_WORKER_INSTALL=1`` lets the worktree install past the guard."""
        script = self._staged_script(tmp_path, "repo/.worktrees/slug")
        result = self._run(script, {"ALLOW_WORKTREE_WORKER_INSTALL": "1"})
        # The guard no longer blocks — the hard-refusal message is absent.
        assert self._GUARD_MSG not in result.stderr
        # It emits the override WARNING and proceeds past the guard.
        assert "installing worker from a worktree" in result.stderr

    def test_main_checkout_passes_guard(self, tmp_path: Path) -> None:
        """A normal (non-worktree) PROJECT_DIR is allowed past the guard."""
        script = self._staged_script(tmp_path, "repo")
        result = self._run(script)
        # The guard let it through — the refusal message never appears. (The
        # script then fails later on the absent lib/launchctl.sh, which is fine.)
        assert self._GUARD_MSG not in result.stderr


class TestInstallWorkerIdempotency:
    """#2161: injection-aware idempotency + drain-before-bootout.

    The old text compare (on-disk WITH injected env vs template WITHOUT)
    false-negatived every run and bootout/bootstrapped a healthy worker each
    update cycle, bypassing the #2141 drain entirely.
    """

    def _recording_run_cmd(self, calls: list, list_pid: str | None = "12345"):
        inner = _fake_run_cmd(list_pid=list_pid)

        def _run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            return inner(cmd, *args, **kwargs)

        return _run

    def _project(self, tmp_path: Path, monkeypatch) -> Path:
        project_dir = _make_worker_project(tmp_path, monkeypatch)
        (project_dir / ".env").write_text("VALOR_PROJECT_KEY=valor\n")
        return project_dir

    def test_unchanged_second_run_skips_bootout(self, tmp_path: Path, monkeypatch) -> None:
        """Same template + same .env + loaded worker → no restart commands."""
        project_dir = self._project(tmp_path, monkeypatch)

        first_calls: list = []
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=self._recording_run_cmd(first_calls),
        ):
            assert install_worker(project_dir) is True

        second_calls: list = []
        with patch(
            "scripts.update.service.run_cmd",
            side_effect=self._recording_run_cmd(second_calls),
        ):
            assert install_worker(project_dir) is True

        joined = [" ".join(c) for c in second_calls]
        assert not any("bootout" in c or "bootstrap" in c or "kickstart" in c for c in joined), (
            f"#2161 regression: unchanged second run issued restart commands: {joined}"
        )

    def test_env_change_triggers_rebuild(self, tmp_path: Path, monkeypatch) -> None:
        """A NEW .env key makes the expected plist differ → rebuild path runs."""
        project_dir = self._project(tmp_path, monkeypatch)

        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="12345")):
            assert install_worker(project_dir) is True

        (project_dir / ".env").write_text("VALOR_PROJECT_KEY=valor\nNEW_KEY=added\n")
        calls: list = []
        with (
            patch(
                "scripts.update.service.run_cmd",
                side_effect=self._recording_run_cmd(calls),
            ),
            patch("scripts.update.drain.wait_for_idle", return_value=True),
        ):
            assert install_worker(project_dir) is True

        joined = [" ".join(c) for c in calls]
        assert any("bootout" in c for c in joined), "env change must trigger the rebuild path"

    def test_busy_drain_defers_restart(self, tmp_path: Path, monkeypatch) -> None:
        """Genuine change + sessions still running → DEFER: True, no bootout."""
        project_dir = self._project(tmp_path, monkeypatch)

        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="12345")):
            assert install_worker(project_dir) is True

        (project_dir / ".env").write_text("VALOR_PROJECT_KEY=valor\nNEW_KEY=added\n")
        calls: list = []
        with (
            patch(
                "scripts.update.service.run_cmd",
                side_effect=self._recording_run_cmd(calls),
            ),
            patch("scripts.update.drain.wait_for_idle", return_value=False),
        ):
            assert install_worker(project_dir) is True  # deferred, not failed

        joined = [" ".join(c) for c in calls]
        assert not any("bootout" in c for c in joined), "busy drain must defer the bootout"

    def test_drain_error_fails_open_to_restart(self, tmp_path: Path, monkeypatch) -> None:
        project_dir = self._project(tmp_path, monkeypatch)

        with patch("scripts.update.service.run_cmd", side_effect=_fake_run_cmd(list_pid="12345")):
            assert install_worker(project_dir) is True

        (project_dir / ".env").write_text("VALOR_PROJECT_KEY=valor\nNEW_KEY=added\n")
        calls: list = []
        with (
            patch(
                "scripts.update.service.run_cmd",
                side_effect=self._recording_run_cmd(calls),
            ),
            patch(
                "scripts.update.drain.wait_for_idle",
                side_effect=RuntimeError("redis down"),
            ),
        ):
            assert install_worker(project_dir) is True

        joined = [" ".join(c) for c in calls]
        assert any("bootout" in c for c in joined), "drain error must fail open to restart"

    def test_first_install_unaffected(self, tmp_path: Path, monkeypatch) -> None:
        """No existing plist + label absent → bootstrap proceeds, no bootout.

        The fake launchctl is stateful: `list` reports the label absent until
        a `bootstrap` call is seen, then reports a live PID so the #2089
        post-bootstrap verify passes (mirrors real launchd behavior).
        """
        project_dir = self._project(tmp_path, monkeypatch)
        calls: list = []
        bootstrapped = {"done": False}
        absent = _fake_run_cmd(list_pid=None)
        loaded = _fake_run_cmd(list_pid="12345")

        def _run(cmd, *args, **kwargs):
            calls.append(list(cmd))
            if "bootstrap" in cmd:
                bootstrapped["done"] = True
            inner = loaded if bootstrapped["done"] else absent
            return inner(cmd, *args, **kwargs)

        with patch("scripts.update.service.run_cmd", side_effect=_run):
            assert install_worker(project_dir) is True
        joined = [" ".join(c) for c in calls]
        assert any("bootstrap" in c for c in joined)
        assert not any("bootout" in c for c in joined)
