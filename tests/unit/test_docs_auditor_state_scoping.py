"""Tests for `DocsAuditor` per-repo Redis state key scoping (#1187 step 2a).

Under per-project iteration the global key `docs_auditor:last_audit_date`
would let the first project's write suppress every subsequent project for
7 days. The fix scopes the key as `docs_auditor:last_audit_date:{repo_name}`.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import redis

from scripts.docs_auditor import DocsAuditor


def _make_auditor(tmp_path: Path, repo_name: str) -> DocsAuditor:
    repo = tmp_path / repo_name
    repo.mkdir()
    return DocsAuditor(repo_root=repo, dry_run=True)


def test_state_key_is_per_repo(tmp_path):
    a = _make_auditor(tmp_path, "ai")
    b = _make_auditor(tmp_path, "popoto")
    assert a._state_key() == "docs_auditor:last_audit_date:ai"
    assert b._state_key() == "docs_auditor:last_audit_date:popoto"
    assert a._state_key() != b._state_key()


def test_load_state_reads_per_repo_key(tmp_path):
    auditor = _make_auditor(tmp_path, "ai")
    fake_redis = MagicMock()
    fake_redis.get.return_value = b"2026-05-01T12:00:00+00:00"

    with patch.object(redis.Redis, "from_url", return_value=fake_redis):
        state = auditor._load_state()

    fake_redis.get.assert_called_once_with("docs_auditor:last_audit_date:ai")
    assert state == {"last_audit_date": "2026-05-01T12:00:00+00:00"}


def test_record_audit_date_writes_per_repo_key(tmp_path):
    auditor = _make_auditor(tmp_path, "popoto")
    fake_redis = MagicMock()

    with patch.object(redis.Redis, "from_url", return_value=fake_redis):
        auditor._record_audit_date()

    fake_redis.set.assert_called_once()
    key, _value = fake_redis.set.call_args[0]
    assert key == "docs_auditor:last_audit_date:popoto"


def test_two_auditors_do_not_see_each_others_state(tmp_path):
    """Project A recording its date must not suppress project B's audit."""
    auditor_a = _make_auditor(tmp_path, "ai")
    auditor_b = _make_auditor(tmp_path, "popoto")

    storage: dict[str, bytes] = {}

    fake_redis = MagicMock()
    fake_redis.set.side_effect = lambda k, v: storage.__setitem__(k, v.encode())
    fake_redis.get.side_effect = lambda k: storage.get(k)

    with patch.object(redis.Redis, "from_url", return_value=fake_redis):
        auditor_a._record_audit_date()
        state_a = auditor_a._load_state()
        state_b = auditor_b._load_state()

    assert state_a.get("last_audit_date")
    assert state_b == {}
