"""Unit tests for scripts/update/redis_acl.py (#2645 Layer 2 planner).

All redis-cli invocations are faked via a patched `subprocess.run` — this
module NEVER touches a live Redis server from a test, and no test asserts
against production `ACL LIST` output. `/update` calling `apply_redis_acl()`
with no `apply` argument, and the marker-file + REDIS_ACL_APPLY double gate,
are what make this PR safe to merge without mutating the live server; these
tests exist to prove both, not merely to describe them.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.update.redis_acl as ra
from scripts.update.redis_acl import apply_redis_acl

_DEFAULT_ACL_LIST = "user default on nopass sanitize-payload ~* &* +@all\n"
_SECRET_SENTINEL = "sK9-do-not-leak-me-42"  # noqa: S105 -- test fixture value, not a real credential


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proc(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def _fake_run_factory(acl_list_stdout: str = _DEFAULT_ACL_LIST):
    """Return a fake `subprocess.run` that answers PING/ACL LIST/SETUSER/SAVE/GETUSER."""

    def fake_run(args, **kwargs):
        upper = [str(a).upper() for a in args]
        if "PING" in upper:
            return _proc(stdout="PONG")
        if "ACL" in upper and "LIST" in upper:
            return _proc(stdout=acl_list_stdout)
        if "ACL" in upper and "SETUSER" in upper:
            return _proc(stdout="OK")
        if "ACL" in upper and "SAVE" in upper:
            return _proc(stdout="OK")
        if "ACL" in upper and "GETUSER" in upper:
            return _proc(stdout="some getuser output")
        return _proc(stdout="OK")

    return fake_run


def _present_marker(tmp_path: Path) -> Path:
    marker = tmp_path / "redis-acl-enabled"
    marker.write_text("1")
    return marker


def _no_setuser_or_save_issued(mock_run: MagicMock) -> None:
    """Assert the faked redis-cli invocation list contains no mutating ACL call."""
    for call in mock_run.call_args_list:
        args = call.args[0] if call.args else call.kwargs.get("args", [])
        upper = [str(a).upper() for a in args]
        assert not ("ACL" in upper and "SETUSER" in upper), (
            f"apply gate violated: ACL SETUSER issued in {args!r}"
        )
        assert not ("ACL" in upper and "SAVE" in upper), (
            f"apply gate violated: ACL SAVE issued in {args!r}"
        )


# ---------------------------------------------------------------------------
# Import smoke test
# ---------------------------------------------------------------------------


def test_module_importable():
    from scripts.update import redis_acl  # noqa: F401


# ---------------------------------------------------------------------------
# Apply-gate matrix: every combination short of BOTH gates open -> skipped,
# ZERO ACL SETUSER / ACL SAVE calls. Asserted on the faked redis-cli
# invocation list, not merely the returned action string.
# ---------------------------------------------------------------------------


class TestApplyGateMatrix:
    def test_marker_absent_flag_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REDIS_ACL_APPLY", raising=False)
        absent = tmp_path / "nope-marker"
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", absent):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl(apply=True)

        assert result.action == "skipped"
        assert result.success is False
        mock_run.assert_not_called()  # gate short-circuits before any redis-cli call
        _no_setuser_or_save_issued(mock_run)

    def test_marker_present_flag_unset(self, tmp_path, monkeypatch):
        monkeypatch.delenv("REDIS_ACL_APPLY", raising=False)
        marker = _present_marker(tmp_path)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", marker):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl(apply=True)

        assert result.action == "skipped"
        assert result.success is False
        mock_run.assert_not_called()
        _no_setuser_or_save_issued(mock_run)

    def test_marker_absent_flag_set(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDIS_ACL_APPLY", "true")
        absent = tmp_path / "nope-marker"
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", absent):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl(apply=True)

        assert result.action == "skipped"
        assert result.success is False
        mock_run.assert_not_called()
        _no_setuser_or_save_issued(mock_run)

    def test_both_gates_open_reaches_write_path(self, tmp_path, monkeypatch):
        """Only marker present + REDIS_ACL_APPLY=true + apply=True reaches the write path."""
        monkeypatch.setenv("REDIS_ACL_APPLY", "true")
        monkeypatch.setenv("REDIS_APP_PASSWORD", _SECRET_SENTINEL)
        marker = _present_marker(tmp_path)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", marker):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl(apply=True)

        assert result.action in {"applied", "applied_with_warning"}
        assert result.success is True
        calls = [
            [str(a).upper() for a in (c.args[0] if c.args else c.kwargs.get("args", []))]
            for c in mock_run.call_args_list
        ]
        assert any("SETUSER" in c and "VALOR-APP" in c for c in calls), calls
        assert any("SETUSER" in c and "DEFAULT" in c for c in calls), calls
        assert any("SAVE" in c for c in calls), calls


# ---------------------------------------------------------------------------
# D8a password-gate pair
# ---------------------------------------------------------------------------


class TestPasswordGate:
    def test_report_path_plans_four_commands_with_placeholder_when_password_unset(
        self, monkeypatch
    ):
        monkeypatch.delenv("REDIS_APP_PASSWORD", raising=False)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch("subprocess.run", mock_run):
            result = apply_redis_acl()  # apply=False, the default

        assert len(result.planned_commands) == 4
        assert result.action != "skipped"
        valor_app_cmds = [c for c in result.planned_commands if "valor-app" in c]
        assert valor_app_cmds, result.planned_commands
        assert any("<REDIS_APP_PASSWORD>" in c for c in valor_app_cmds)

    def test_apply_path_skips_when_password_unset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REDIS_ACL_APPLY", "true")
        monkeypatch.delenv("REDIS_APP_PASSWORD", raising=False)
        marker = _present_marker(tmp_path)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", marker):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl(apply=True)

        assert result.action == "skipped"
        assert result.error == "REDIS_APP_PASSWORD unset"
        _no_setuser_or_save_issued(mock_run)


# ---------------------------------------------------------------------------
# No secret reaches stdout / the returned result
# ---------------------------------------------------------------------------


class TestNoSecretLeak:
    def test_report_path_never_contains_the_real_secret(self, monkeypatch):
        monkeypatch.setenv("REDIS_APP_PASSWORD", _SECRET_SENTINEL)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch("subprocess.run", mock_run):
            result = apply_redis_acl()  # report path — must not depend on the password

        joined = "\n".join(result.planned_commands)
        assert _SECRET_SENTINEL not in joined
        assert "<REDIS_APP_PASSWORD>" in joined


# ---------------------------------------------------------------------------
# /update never applies
# ---------------------------------------------------------------------------


class TestUpdateNeverApplies:
    def test_run_py_call_site_passes_no_apply_argument(self):
        """scripts/update/run.py must call apply_redis_acl() with no arguments.

        Regex over the source rather than importing run.py's Step 3.135 (a
        second, concurrently-edited builder owns run.py's wiring) — this is
        deliberately a source-level contract check, mirroring D8's own
        acceptance evidence ("A regression test asserts that
        scripts/update/run.py passes no apply argument at the call site").
        """
        run_py = Path(ra._PROJECT_DIR) / "scripts" / "update" / "run.py"
        source = run_py.read_text()
        import re

        calls = re.findall(r"redis_acl\.apply_redis_acl\(([^)]*)\)", source)
        if not calls:
            import pytest

            pytest.skip(
                "scripts/update/run.py does not yet call redis_acl.apply_redis_acl() -- "
                "Step 3.135 wiring is owned by a concurrently-running builder task."
            )
        for call_args in calls:
            assert call_args.strip() == "", (
                f"redis_acl.apply_redis_acl() must be called with no arguments from "
                f"scripts/update/run.py; found call with args: {call_args!r}"
            )

    def test_marker_and_flag_both_set_but_no_apply_kwarg_stays_report_only(
        self, tmp_path, monkeypatch
    ):
        """Even with both operator gates open, /update's no-argument call
        (apply defaults to False) must stay on the report path and issue no
        writes."""
        monkeypatch.setenv("REDIS_ACL_APPLY", "true")
        monkeypatch.setenv("REDIS_APP_PASSWORD", _SECRET_SENTINEL)
        marker = _present_marker(tmp_path)
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch.object(ra, "ACL_MARKER_FILE", marker):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl()  # mirrors /update's call site exactly

        assert result.action == "reported"
        _no_setuser_or_save_issued(mock_run)


# ---------------------------------------------------------------------------
# Non-fatal contract
# ---------------------------------------------------------------------------


class TestNonFatal:
    def test_redis_cli_absent(self):
        with patch("shutil.which", return_value=None):
            result = apply_redis_acl()

        assert result.success is False
        assert result.error is not None and "redis-cli" in result.error.lower()

    def test_redis_down(self):
        mock_run = MagicMock(return_value=_proc(returncode=1, stderr="Could not connect"))
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", mock_run):
                result = apply_redis_acl()

        assert result.success is False
        assert result.error is not None

    def test_acl_list_unparseable(self):
        def fake_run(args, **kwargs):
            upper = [str(a).upper() for a in args]
            if "PING" in upper:
                return _proc(stdout="PONG")
            return _proc(stdout="garbage, not an ACL LIST line at all")

        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", side_effect=fake_run):
                result = apply_redis_acl()

        assert result.success is False
        assert result.error is not None

    def test_oserror_on_ping_does_not_raise(self):
        with patch("shutil.which", return_value="/usr/bin/redis-cli"):
            with patch("subprocess.run", side_effect=OSError("boom")):
                result = apply_redis_acl()  # must not raise

        assert result.success is False
        assert result.error is not None


# ---------------------------------------------------------------------------
# aclfile directive is text-only, never a file write
# ---------------------------------------------------------------------------


class TestAclfileNeverWritten:
    def test_aclfile_directive_is_returned_as_text(self):
        mock_run = MagicMock(side_effect=_fake_run_factory())
        with patch("subprocess.run", mock_run):
            result = apply_redis_acl()

        assert isinstance(result.aclfile_directive, str)
        assert "aclfile" in result.aclfile_directive

    def test_module_never_opens_redis_conf_for_writing(self):
        """Anti-criterion (Finding 6 / round-3 critique): grep the module source
        for the two shapes that would indicate a redis.conf write. Count must
        be 0."""
        import re

        source = Path(ra.__file__).read_text()
        matches = re.findall(r"open\(.*redis\.conf", source) + re.findall(
            r"redis_conf.*write_text", source
        )
        assert len(matches) == 0, f"module appears to write redis.conf: {matches}"
