"""Unit tests for ``tools.sdlc_session_release`` (issue #2714, L1).

``release_run`` is a THIN wrapper over two already-ownership-checked
compare-and-delete primitives (``release_issue_lock`` and
``clear_supervised_run_signal``). These tests therefore assert two things:

1. The reason taxonomy is exactly ``released`` / ``not_owner`` / ``no_lease`` /
   ``missing_args`` / ``error`` and maps onto real Redis lease states. The
   lease/signal keys are plain (non-Popoto-managed) Redis keys, so the raw
   client is the correct read path for assertions here.
2. The subcommand is reachable **through the ``sdlc-tool`` dispatcher**, not
   merely importable — without the ``ALLOWED_SUBCOMMANDS`` entry the skill step
   is a silent no-op (issue #2714 Agent Integration).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ISSUE = 992714


def _redis():
    from popoto.redis_db import POPOTO_REDIS_DB

    return POPOTO_REDIS_DB


def _lock_key(issue_number: int) -> str:
    return f"session:issuelock:{issue_number}"


def _signal_key(issue_number: int) -> str:
    return f"session:supervisedrun:{issue_number}"


@pytest.fixture
def clean_keys():
    """Drop the (non-Popoto) lease + signal keys before and after each test."""
    r = _redis()
    for key in (_lock_key(ISSUE), _signal_key(ISSUE)):
        r.delete(key)
    yield
    for key in (_lock_key(ISSUE), _signal_key(ISSUE)):
        r.delete(key)


def _acquire(run_id: str) -> None:
    """Take the real issue lease + publish the supervised-run signal."""
    from agent.supervised_run import write_supervised_run_signal
    from models.session_lifecycle import touch_issue_lock

    result = touch_issue_lock(ISSUE, run_id, session_id="sess-1", target_repo="ai")
    assert result.acquired, "test setup failed to acquire the issue lease"
    write_supervised_run_signal(ISSUE, run_id, session_id="sess-1")
    assert _redis().get(_signal_key(ISSUE)) is not None


class TestReleaseRun:
    """The reason taxonomy, exercised against real Redis lease state."""

    def test_releases_when_owner(self, clean_keys):
        from tools.sdlc_session_release import release_run

        _acquire("run-owner")
        result = release_run(ISSUE, "run-owner")

        assert result == {
            "released": True,
            "reason": "released",
            "issue_number": ISSUE,
            "run_id": "run-owner",
        }
        assert _redis().get(_lock_key(ISSUE)) is None

    def test_clears_the_supervised_run_signal(self, clean_keys):
        from tools.sdlc_session_release import release_run

        _acquire("run-owner")
        release_run(ISSUE, "run-owner")

        assert _redis().get(_signal_key(ISSUE)) is None

    def test_foreign_run_id_is_a_no_op(self, clean_keys):
        """A wrong run_id must neither release the lease nor clear the signal."""
        from tools.sdlc_session_release import release_run

        _acquire("run-owner")
        result = release_run(ISSUE, "run-intruder")

        assert result["released"] is False
        assert result["reason"] == "not_owner"
        assert _redis().get(_lock_key(ISSUE)) is not None
        assert _redis().get(_signal_key(ISSUE)) is not None

    def test_absent_lease_reports_no_lease(self, clean_keys):
        from tools.sdlc_session_release import release_run

        result = release_run(ISSUE, "run-nobody")

        assert result["released"] is False
        assert result["reason"] == "no_lease"

    @pytest.mark.parametrize(
        "issue_number,run_id",
        [
            (None, "run-1"),
            (0, "run-1"),
            (ISSUE, None),
            (ISSUE, ""),
            (ISSUE, "   "),
            (None, None),
        ],
    )
    def test_missing_args_never_reaches_the_primitives(self, issue_number, run_id):
        """Empty/whitespace input short-circuits BEFORE any Redis primitive."""
        from tools.sdlc_session_release import release_run

        release_mock = MagicMock()
        clear_mock = MagicMock()
        with (
            patch("models.session_lifecycle.release_issue_lock", release_mock),
            patch("agent.supervised_run.clear_supervised_run_signal", clear_mock),
        ):
            result = release_run(issue_number, run_id)

        assert result["released"] is False
        assert result["reason"] == "missing_args"
        release_mock.assert_not_called()
        clear_mock.assert_not_called()

    def test_redis_error_is_swallowed_into_a_named_reason(self, clean_keys):
        """A raising primitive yields a printable reason, never an empty dict."""
        from tools.sdlc_session_release import release_run

        boom = MagicMock(side_effect=RuntimeError("redis down"))
        with patch("models.session_lifecycle.release_issue_lock", boom):
            result = release_run(ISSUE, "run-owner")

        assert result == {
            "released": False,
            "reason": "error",
            "issue_number": ISSUE,
            "run_id": "run-owner",
        }


class TestNoRawDelete:
    """Anti-criterion: the tool must delegate, never delete on its own."""

    def test_module_source_contains_no_raw_delete_path(self):
        import tools.sdlc_session_release as mod

        with open(mod.__file__) as fh:
            source = fh.read()

        assert ".delete(" not in source
        assert "DEL " not in source


class TestDispatcherIntegration:
    """The agent reaches this via ``sdlc-tool``; importing it is not enough."""

    def test_subcommand_is_allowlisted_in_the_dispatcher(self):
        with open(os.path.join(REPO_ROOT, "scripts", "sdlc-tool")) as fh:
            source = fh.read()

        allowlist_line = next(
            line for line in source.splitlines() if line.startswith("ALLOWED_SUBCOMMANDS=")
        )
        assert "session-release" in allowlist_line
        assert "session-release" in source  # also described in usage()

    def test_help_is_reachable_through_the_dispatcher(self):
        proc = subprocess.run(
            [os.path.join(REPO_ROOT, "scripts", "sdlc-tool"), "session-release", "--help"],
            capture_output=True,
            text=True,
            timeout=90,
            env={**os.environ, "AI_REPO_ROOT": REPO_ROOT},
        )

        assert proc.returncode == 0, proc.stderr
        assert "--issue-number" in proc.stdout
        assert "--run-id" in proc.stdout


class TestCLI:
    def test_main_emits_typed_json_and_exits_zero(self, clean_keys, capsys):
        from tools.sdlc_session_release import main

        _acquire("run-cli")
        with patch.object(
            sys,
            "argv",
            ["sdlc_session_release", "--issue-number", str(ISSUE), "--run-id", "run-cli"],
        ):
            code = main()

        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload == {
            "released": True,
            "reason": "released",
            "issue_number": ISSUE,
            "run_id": "run-cli",
        }

    def test_main_without_args_is_missing_args_exit_zero(self, capsys):
        from tools.sdlc_session_release import main

        with patch.object(sys, "argv", ["sdlc_session_release"]):
            code = main()

        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["released"] is False
        assert payload["reason"] == "missing_args"
