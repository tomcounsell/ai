"""Unit tests for tools.sdlc_verdict — single-writer verdict recorder."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools.sdlc_verdict import (
    compute_plan_hash,
    get_verdict,
    record_verdict,
)


class _FakeSession:
    """Minimal fake AgentSession for record_verdict round-trips.

    Stores stage_states as a JSON string like the real model.
    """

    def __init__(self, session_id="fake-1", stage_states=None):
        self.session_id = session_id
        self.session_type = "pm"
        if stage_states is None:
            self.stage_states = "{}"
        elif isinstance(stage_states, dict):
            self.stage_states = json.dumps(stage_states)
        else:
            self.stage_states = stage_states

    def save(self):
        pass  # no-op — update_stage_states verifies via reload


@pytest.fixture
def fake_session_reload_patched():
    """Patch _reload_session so verification trivially matches in-memory state."""
    # update_stage_states reloads via models.AgentSession.query. Patch it to
    # return the same object so verification succeeds.
    with patch("tools.stage_states_helpers._reload_session") as mock_reload:
        session = _FakeSession()
        mock_reload.return_value = session
        yield session


class TestRecordVerdict:
    def test_rejects_unknown_stage(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        result = record_verdict(session, "BOGUS", "NEEDS REVISION")
        assert result == {}

    def test_rejects_empty_verdict(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        result = record_verdict(session, "CRITIQUE", "")
        assert result == {}

    def test_rejects_none_session(self):
        result = record_verdict(None, "CRITIQUE", "NEEDS REVISION")
        assert result == {}

    def test_writes_critique_verdict(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        record = record_verdict(session, "CRITIQUE", "NEEDS REVISION")
        assert record
        assert record["verdict"] == "NEEDS REVISION"
        assert "recorded_at" in record
        # Persisted into stage_states
        data = json.loads(session.stage_states)
        assert data["_verdicts"]["CRITIQUE"]["verdict"] == "NEEDS REVISION"

    def test_writes_review_verdict_with_counts(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        record = record_verdict(
            session,
            "REVIEW",
            "CHANGES REQUESTED",
            blockers=2,
            tech_debt=1,
        )
        assert record["verdict"] == "CHANGES REQUESTED"
        assert record["blockers"] == 2
        assert record["tech_debt"] == 1
        data = json.loads(session.stage_states)
        assert data["_verdicts"]["REVIEW"]["blockers"] == 2

    def test_get_verdict_round_trip(self, fake_session_reload_patched):
        session = fake_session_reload_patched
        record_verdict(session, "CRITIQUE", "READY TO BUILD (no concerns)")
        got = get_verdict(session, "CRITIQUE")
        assert got["verdict"] == "READY TO BUILD (no concerns)"

    def test_get_verdict_returns_empty_for_unknown_stage(self):
        session = _FakeSession()
        assert get_verdict(session, "BOGUS") == {}

    def test_get_verdict_returns_empty_when_none_recorded(self):
        session = _FakeSession()
        assert get_verdict(session, "CRITIQUE") == {}

    def test_get_verdict_handles_legacy_bare_string(self):
        """Legacy records may store a bare verdict string."""
        session = _FakeSession(stage_states={"_verdicts": {"CRITIQUE": "READY TO BUILD"}})
        got = get_verdict(session, "CRITIQUE")
        assert got["verdict"] == "READY TO BUILD"


class TestComputePlanHash:
    def test_returns_sha256_prefixed_hex(self, tmp_path):
        f = tmp_path / "plan.md"
        f.write_text("# hello\n", encoding="utf-8")
        digest = compute_plan_hash(f)
        assert digest is not None
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_normalizes_line_endings(self, tmp_path):
        a = tmp_path / "a.md"
        a.write_bytes(b"line1\nline2\n")
        b = tmp_path / "b.md"
        b.write_bytes(b"line1\r\nline2\r\n")
        # CRLF should normalize to LF and produce the same hash as LF-only.
        assert compute_plan_hash(a) == compute_plan_hash(b)

    def test_includes_frontmatter(self, tmp_path):
        # Different frontmatter → different hash (frontmatter edits bust cache).
        a = tmp_path / "a.md"
        a.write_text("---\nrevision_applied: false\n---\n# body\n")
        b = tmp_path / "b.md"
        b.write_text("---\nrevision_applied: true\n---\n# body\n")
        assert compute_plan_hash(a) != compute_plan_hash(b)

    def test_preserves_internal_whitespace(self, tmp_path):
        # Reflowed paragraphs must change the hash.
        a = tmp_path / "a.md"
        a.write_text("line with  two spaces\n")
        b = tmp_path / "b.md"
        b.write_text("line with one space\n")
        assert compute_plan_hash(a) != compute_plan_hash(b)

    def test_returns_none_on_missing_file(self, tmp_path):
        assert compute_plan_hash(tmp_path / "missing.md") is None


class TestGracefulFailure:
    def test_corrupt_stage_states_does_not_crash(self, fake_session_reload_patched):
        """Writing a verdict into a session with malformed stage_states must
        not crash — the helper treats it as empty."""
        session = fake_session_reload_patched
        session.stage_states = "{not json"
        # Should not raise
        record = record_verdict(session, "CRITIQUE", "NEEDS REVISION")
        # Because update_stage_states re-wrote from empty, it should succeed.
        assert record["verdict"] == "NEEDS REVISION"
