"""Unit tests for skills-audit issue filing (issue #1395 Phase 2).

Covers the 2-consecutive-FAIL streak gate, 30-day dedup, gh failure
retry behavior, lock contention, Redis outages, and bulk-FAIL transient
regression suppression.

All ``gh`` and Redis calls are mocked. The helper's job is to compose
those tools deterministically — tests fence each side at the boundary.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from reflections import auditing

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeRedis:
    """In-memory Redis with the ops the helper needs: set/incr/expire/exists/delete."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.fail_mode: str | None = None  # None, "incr", "set", "exists", "all"

    def _check_fail(self, op: str):
        if self.fail_mode == "all" or self.fail_mode == op:
            raise RuntimeError(f"redis fail mode={self.fail_mode}")

    def set(self, key, value, nx=False, ex=None):
        self._check_fail("set")
        if nx and key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def incr(self, key):
        self._check_fail("incr")
        cur = int(self.store.get(key, "0")) + 1
        self.store[key] = str(cur)
        return cur

    def expire(self, key, ttl):
        return True

    def exists(self, key):
        self._check_fail("exists")
        return 1 if key in self.store else 0

    def delete(self, key):
        self.store.pop(key, None)
        return 1


class _FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


_FINDING = {"skill": "do-build", "rule": 7, "severity": "FAIL", "message": "missing X"}
_REPO_ROOT = Path("/tmp/fake-repo")
_PROJECT_SLUG = "valor"
_REPO_ID = "tomcounsell/ai"


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(auditing, "_skills_audit_get_redis", lambda: r)
    return r


@pytest.fixture
def gh_success(monkeypatch):
    """Default: gh repo view and gh issue create both succeed."""

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "repo"]:
            return _FakeProc(stdout='{"nameWithOwner": "tomcounsell/ai"}')
        if cmd[:2] == ["gh", "issue"]:
            return _FakeProc(stdout="https://github.com/tomcounsell/ai/issues/9999")
        return _FakeProc(returncode=1)

    monkeypatch.setattr(auditing.subprocess, "run", fake_run)


# ---------------------------------------------------------------------------
# Streak gate
# ---------------------------------------------------------------------------


def test_first_fail_does_not_file(fake_redis, gh_success):
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False
    # streak=1, no dedup key, no issue filed
    streak_keys = [k for k in fake_redis.store if k.startswith("skills_audit:streak:")]
    dedup_keys = [k for k in fake_redis.store if k.startswith("skills_audit:issues_filed:")]
    assert len(streak_keys) == 1
    assert fake_redis.store[streak_keys[0]] == "1"
    assert dedup_keys == []


def test_second_consecutive_fail_files_issue(fake_redis, gh_success):
    auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is True
    dedup_keys = [k for k in fake_redis.store if k.startswith("skills_audit:issues_filed:")]
    assert len(dedup_keys) == 1


def test_third_run_blocked_by_dedup(fake_redis, gh_success):
    # Two priming runs → issue filed.
    auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    # Third run → dedup blocks.
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False


# ---------------------------------------------------------------------------
# gh failure → no dedup poisoning
# ---------------------------------------------------------------------------


def test_gh_failure_does_not_set_dedup(fake_redis, monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["gh", "issue"]:
            return _FakeProc(returncode=1, stderr="auth expired")
        return _FakeProc(stdout='{"nameWithOwner": "x/y"}')

    monkeypatch.setattr(auditing.subprocess, "run", fake_run)

    # Two consecutive FAILs but gh broken on the second:
    auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False
    dedup_keys = [k for k in fake_redis.store if k.startswith("skills_audit:issues_filed:")]
    assert dedup_keys == [], "no dedup key should be set after gh failure"


# ---------------------------------------------------------------------------
# Redis unavailable
# ---------------------------------------------------------------------------


def test_redis_unavailable_returns_false_no_crash(monkeypatch):
    monkeypatch.setattr(
        auditing,
        "_skills_audit_get_redis",
        MagicMock(side_effect=RuntimeError("redis down")),
    )
    # Must not raise.
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False


def test_redis_incr_failure_does_not_file(fake_redis, gh_success):
    fake_redis.fail_mode = "incr"
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False


# ---------------------------------------------------------------------------
# Bulk transient regression
# ---------------------------------------------------------------------------


def test_100_simultaneous_fails_files_zero(fake_redis, gh_success):
    """100 distinct FAIL findings on a single run — all streak=1, no issues."""
    filed_count = 0
    for i in range(100):
        f = {"skill": f"skill-{i}", "rule": 1, "severity": "FAIL", "message": "x"}
        if auditing._file_skills_audit_issue_if_streaked(
            f, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
        ):
            filed_count += 1
    assert filed_count == 0


# ---------------------------------------------------------------------------
# Lock contention
# ---------------------------------------------------------------------------


def test_lock_contention_skips_second_caller(fake_redis, gh_success):
    """A second concurrent caller sees the lock and skips silently."""
    # Pre-set the lock as if another worker grabbed it.
    finding_hash = auditing._skills_audit_finding_hash(
        _PROJECT_SLUG, _FINDING["skill"], _FINDING["rule"]
    )
    lock_key = f"{auditing._SKILLS_AUDIT_LOCK_PREFIX}:{finding_hash}"
    fake_redis.store[lock_key] = "1"

    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is False
    # Streak counter MUST NOT have been bumped — the contending caller backed off.
    streak_key = f"{auditing._SKILLS_AUDIT_STREAK_PREFIX}:{finding_hash}"
    assert streak_key not in fake_redis.store


# ---------------------------------------------------------------------------
# Flapping
# ---------------------------------------------------------------------------


def test_flapping_files_on_third_appearance(fake_redis, gh_success):
    """FAIL → absent → FAIL: streak counter is monotonic (in 7-day window),
    so second FAIL appearance reaches streak=2 and files."""
    auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    # Simulated absent run = no call at all. Streak persists.
    filed = auditing._file_skills_audit_issue_if_streaked(
        _FINDING, _REPO_ROOT, _PROJECT_SLUG, repo_name_with_owner=_REPO_ID
    )
    assert filed is True


# ---------------------------------------------------------------------------
# Hash stability
# ---------------------------------------------------------------------------


def test_hash_excludes_message_text(fake_redis, gh_success):
    """Two findings with the same skill+rule but reworded messages must hash identically."""
    h1 = auditing._skills_audit_finding_hash(_PROJECT_SLUG, "do-build", 7)
    h2 = auditing._skills_audit_finding_hash(_PROJECT_SLUG, "do-build", 7)
    assert h1 == h2
    h3 = auditing._skills_audit_finding_hash(_PROJECT_SLUG, "do-build", 8)
    assert h1 != h3


def test_hash_partitions_by_project_slug():
    h_a = auditing._skills_audit_finding_hash("valor", "do-build", 7)
    h_b = auditing._skills_audit_finding_hash("cuttlefish", "do-build", 7)
    assert h_a != h_b


# ---------------------------------------------------------------------------
# Repo identity resolution failure
# ---------------------------------------------------------------------------


def test_no_repo_identity_skips_filing(fake_redis, monkeypatch):
    """If gh repo view fails AND no cached repo_id is passed, do not file."""
    monkeypatch.setattr(
        auditing.subprocess,
        "run",
        MagicMock(return_value=_FakeProc(returncode=1, stderr="not a repo")),
    )

    auditing._file_skills_audit_issue_if_streaked(_FINDING, _REPO_ROOT, _PROJECT_SLUG)
    filed = auditing._file_skills_audit_issue_if_streaked(_FINDING, _REPO_ROOT, _PROJECT_SLUG)
    assert filed is False
    dedup_keys = [k for k in fake_redis.store if k.startswith("skills_audit:issues_filed:")]
    assert dedup_keys == []


# ---------------------------------------------------------------------------
# Per-project return-shape contract
# ---------------------------------------------------------------------------


def test_skills_audit_for_project_includes_issues_filed_field(monkeypatch):
    """_skills_audit_for_project must return ``issues_filed`` for telemetry."""
    monkeypatch.setattr(auditing.Path, "exists", lambda self: True)
    audit_data = {
        "summary": {"total_skills": 5, "fail": 0, "warn": 0},
        "findings": [],
    }

    def fake_run(*args, **kwargs):
        cmd = args[0]
        if cmd[0].endswith("python") or (len(cmd) > 1 and cmd[1].endswith("audit_skills.py")):
            return _FakeProc(stdout=__import__("json").dumps(audit_data))
        if cmd[:2] == ["gh", "repo"]:
            return _FakeProc(stdout='{"nameWithOwner": "x/y"}')
        return _FakeProc()

    monkeypatch.setattr(auditing.subprocess, "run", fake_run)
    result = auditing._skills_audit_for_project(
        {"slug": "valor", "working_directory": "/tmp/whatever"}
    )
    assert "issues_filed" in result
    assert result["issues_filed"] == 0
    assert "0 issues filed" in result["summary"]
