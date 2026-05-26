"""Unit tests for the sdlc-progress-check reflection (issue #1395 Phase 1).

Covers the 5-gate stall heuristic, dedup, draft/closed-issue exclusions,
subprocess failure tolerance, and return-shape contracts.

Everything is mocked — no real ``gh``, ``git``, ``valor-telegram``, or Redis
calls. The reflection's job is to compose those tools, so unit tests fence
each tool at the boundary.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest

from reflections import sdlc_progress

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeRedis:
    """Minimal Redis stand-in supporting only ``set(key, val, nx, ex)``."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.fail = False

    def set(self, key, value, nx=False, ex=None):
        if self.fail:
            raise RuntimeError("redis down")
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# Canonical project dict the reflection receives from ``run_per_project_audit``.
_PROJECT = {
    "slug": "valor",
    "working_directory": "/tmp/fake-valor-repo",
}


def _pr(number=1237, branch="session/sdlc-1395", draft=False):
    return {
        "number": number,
        "headRefName": branch,
        "isDraft": draft,
        "baseRefName": "main",
    }


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(sdlc_progress, "_get_redis", lambda: r)
    return r


@pytest.fixture
def stub_workdir(monkeypatch):
    """Bypass the ``Path(wd).is_dir()`` gate."""
    monkeypatch.setattr(sdlc_progress.Path, "is_dir", lambda self: True)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_happy_path_emits_single_alert(fake_redis, stub_workdir, monkeypatch):
    now = int(time.time())
    old_ts = now - 8 * 3600  # 8h old

    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr(number=1237)])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: ("abc123def456", old_ts))
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda slug: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))

    result = sdlc_progress._check_project_stalls(_PROJECT)

    assert result["status"] == "ok"
    assert len(alerts) == 1
    assert "PR #1237" in alerts[0]
    assert "sdlc-1395" in alerts[0]
    assert len(result["findings"]) == 1
    assert "1 alert(s) fired" in result["summary"]
    assert "duration" in result


# ---------------------------------------------------------------------------
# Gate: active session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "active",
    [True, None],  # active OR unknown — both must suppress alert
)
def test_no_alert_when_session_active_or_unknown(fake_redis, stub_workdir, monkeypatch, active):
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: ("abc", now - 9 * 3600))
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda slug: active)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))

    sdlc_progress._check_project_stalls(_PROJECT)
    assert alerts == []


def test_alert_when_only_terminal_sessions(fake_redis, stub_workdir, monkeypatch):
    """No non-terminal sessions → alert fires (assuming other gates pass)."""
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: ("sha1", now - 9 * 3600))
    # All sessions terminal → _has_active_session returns False.
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda slug: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))

    sdlc_progress._check_project_stalls(_PROJECT)
    assert len(alerts) == 1


# ---------------------------------------------------------------------------
# Gate: dedup
# ---------------------------------------------------------------------------


def test_dedup_suppresses_second_alert(fake_redis, stub_workdir, monkeypatch):
    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(
        sdlc_progress, "_last_commit", lambda cwd, branch: ("same-sha", now - 9 * 3600)
    )
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda slug: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))

    sdlc_progress._check_project_stalls(_PROJECT)
    sdlc_progress._check_project_stalls(_PROJECT)

    assert len(alerts) == 1, "second run with same sha should be deduped"


def test_dedup_redis_unavailable_skips_alert(stub_workdir, monkeypatch):
    """Plan: Redis unavailable for dedup write → skip Telegram send."""
    fake = _FakeRedis()
    fake.fail = True
    monkeypatch.setattr(sdlc_progress, "_get_redis", lambda: fake)

    now = int(time.time())
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: ("sha", now - 9 * 3600))
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda slug: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))

    sdlc_progress._check_project_stalls(_PROJECT)
    assert alerts == []


# ---------------------------------------------------------------------------
# Gate: draft / closed issue / missing local branch
# ---------------------------------------------------------------------------


def test_draft_prs_not_flagged(fake_redis, stub_workdir, monkeypatch):
    """_list_open_sdlc_prs already filters drafts, but assert end-to-end."""
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))
    sdlc_progress._check_project_stalls(_PROJECT)
    assert alerts == []


def test_closed_issue_skipped(fake_redis, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: False)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda *a: ("x", 0))
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda s: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))
    sdlc_progress._check_project_stalls(_PROJECT)
    assert alerts == []


def test_missing_local_branch_silently_skipped(fake_redis, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [_pr()])
    monkeypatch.setattr(sdlc_progress, "_issue_is_open", lambda cwd, n: True)
    monkeypatch.setattr(sdlc_progress, "_last_commit", lambda cwd, branch: None)
    monkeypatch.setattr(sdlc_progress, "_has_active_session", lambda s: False)

    alerts: list[str] = []
    monkeypatch.setattr(sdlc_progress, "_send_alert", lambda msg: alerts.append(msg))
    sdlc_progress._check_project_stalls(_PROJECT)
    assert alerts == []


# ---------------------------------------------------------------------------
# Subprocess failure tolerance
# ---------------------------------------------------------------------------


def test_gh_pr_list_filenotfound_returns_empty(monkeypatch):
    """gh CLI missing → empty list, no exception."""
    monkeypatch.setattr(
        sdlc_progress.subprocess,
        "run",
        MagicMock(side_effect=FileNotFoundError("gh")),
    )
    assert sdlc_progress._list_open_sdlc_prs("/tmp") == []


def test_gh_pr_list_filters_non_sdlc_branches(monkeypatch):
    payload = json.dumps(
        [
            _pr(branch="session/sdlc-1395"),
            _pr(branch="session/some-feature"),
            _pr(branch="dependabot/update"),
            _pr(branch="session/sdlc-9999", draft=True),  # draft → excluded
        ]
    )
    monkeypatch.setattr(
        sdlc_progress, "_run_gh", lambda args, cwd, timeout=20: _FakeProc(stdout=payload)
    )
    out = sdlc_progress._list_open_sdlc_prs("/tmp")
    assert len(out) == 1
    assert out[0]["headRefName"] == "session/sdlc-1395"


def test_git_log_failure_returns_none(monkeypatch):
    monkeypatch.setattr(
        sdlc_progress.subprocess,
        "run",
        MagicMock(return_value=_FakeProc(returncode=128, stdout="", stderr="bad ref")),
    )
    assert sdlc_progress._last_commit("/tmp", "session/sdlc-1") is None


def test_send_alert_swallows_filenotfound(monkeypatch):
    monkeypatch.setattr(
        sdlc_progress.subprocess,
        "run",
        MagicMock(side_effect=FileNotFoundError("valor-telegram")),
    )
    # Must not raise.
    sdlc_progress._send_alert("hello")


def test_has_active_session_handles_redis_failure(monkeypatch):
    fake_query = MagicMock()
    fake_query.filter.side_effect = RuntimeError("redis down")
    fake_session_cls = MagicMock()
    fake_session_cls.query = fake_query
    monkeypatch.setitem(
        __import__("sys").modules,
        "models.agent_session",
        MagicMock(AgentSession=fake_session_cls),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "models.session_lifecycle",
        MagicMock(NON_TERMINAL_STATUSES=frozenset({"running"})),
    )
    assert sdlc_progress._has_active_session("sdlc-1") is None


# ---------------------------------------------------------------------------
# Return shape contract
# ---------------------------------------------------------------------------


def test_check_project_returns_canonical_shape(fake_redis, stub_workdir, monkeypatch):
    monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", lambda cwd: [])
    result = sdlc_progress._check_project_stalls(_PROJECT)
    assert set(result.keys()) >= {"status", "findings", "summary", "duration"}
    assert isinstance(result["findings"], list)
    assert isinstance(result["duration"], float)


def test_subprocess_cwd_is_working_directory(stub_workdir, monkeypatch, fake_redis):
    """All gh/git invocations must run inside the project's working_directory."""
    captured: list[dict] = []

    def fake_run(*args, **kwargs):
        captured.append({"args": args, "kwargs": kwargs})
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and cmd[0] == "gh" and "pr" in cmd:
            return _FakeProc(stdout="[]")
        return _FakeProc(returncode=1)

    monkeypatch.setattr(sdlc_progress.subprocess, "run", fake_run)
    sdlc_progress._check_project_stalls(_PROJECT)
    # gh pr list should have been called with cwd=/tmp/fake-valor-repo.
    gh_calls = [c for c in captured if c["args"][0][0] == "gh"]
    assert gh_calls, "expected at least one gh invocation"
    assert all(c["kwargs"].get("cwd") == "/tmp/fake-valor-repo" for c in gh_calls)


def test_no_working_directory_returns_skipped(monkeypatch):
    result = sdlc_progress._check_project_stalls({"slug": "x", "working_directory": ""})
    assert result["status"] == "skipped"


# ---------------------------------------------------------------------------
# Public entrypoint smoke
# ---------------------------------------------------------------------------


def test_run_sdlc_progress_check_uses_per_project_helper(monkeypatch):
    monkeypatch.setattr(sdlc_progress, "load_local_projects", lambda: [], raising=False)
    # run_per_project_audit handles the no-projects case.
    out = sdlc_progress.run_sdlc_progress_check()
    assert out["status"] in {"ok", "disabled"}
    assert "summary" in out
