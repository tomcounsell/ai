"""Unit tests for tools.sdlc_verdict — single-writer verdict recorder."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tools._sdlc_utils import normalize_verdict
from tools.sdlc_verdict import (
    compute_plan_body_hash,
    compute_plan_hash,
    get_verdict,
    record_verdict,
)


class _FakeSession:
    """Minimal fake AgentSession for record_verdict round-trips.

    Stores stage_states as a JSON string like the real model.
    """

    def __init__(self, session_id="fake-1", stage_states=None, issue_url=None, message_text=""):
        self.session_id = session_id
        self.session_type = "eng"
        self.issue_url = issue_url
        self.message_text = message_text
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
        # normalize_verdict uppercases the stored verdict (#1638 write-boundary).
        assert got["verdict"] == "READY TO BUILD (NO CONCERNS)"

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


class TestCliRecordEnsure:
    """#1558: `verdict record` resolves through find_session(..., ensure=True)
    so a sessionless-but-issue-numbered record auto-creates a PM session and the
    verdict round-trips via `verdict get` (which stays ensure=False)."""

    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=1558,
            stage="CRITIQUE",
            verdict="READY TO BUILD",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_cli_record_passes_ensure_true(self, fake_session_reload_patched):
        from tools.sdlc_verdict import _cli_record

        session = fake_session_reload_patched
        # Ownership guard: the session must own issue 1558 for the write to proceed.
        session.issue_url = "https://github.com/x/y/issues/1558"
        with patch("tools.sdlc_verdict._find_session", return_value=session) as find_mock:
            result = _cli_record(self._args())

        assert result["verdict"] == "READY TO BUILD"
        find_mock.assert_called_once_with(session_id=None, issue_number=1558, ensure=True)

    def test_cli_get_stays_ensure_false(self, fake_session_reload_patched):
        from tools.sdlc_verdict import _cli_get, _cli_record

        session = fake_session_reload_patched
        # Ownership guard: the session must own issue 1558 for the write to proceed.
        session.issue_url = "https://github.com/x/y/issues/1558"
        # Record first so the get round-trips against the same in-memory session.
        with patch("tools.sdlc_verdict._find_session", return_value=session):
            _cli_record(self._args())

        with patch("tools.sdlc_verdict._find_session", return_value=session) as get_find_mock:
            got = _cli_get(self._args())

        assert got["verdict"] == "READY TO BUILD"
        # get must NOT pass ensure (reads stay pure).
        _, kwargs = get_find_mock.call_args
        assert "ensure" not in kwargs or kwargs.get("ensure") is False


class TestConvergenceUnderDivergentEnv:
    """#1671/#1672: a verdict recorded with --issue-number N under a divergent
    VALOR_SESSION_ID lands on the issue-scoped session and is readable via the
    issue-number read path. This is the direct regression for the skew."""

    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=1672,
            stage="CRITIQUE",
            verdict="NEEDS REVISION",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_verdict_lands_on_issue_session_not_env(self, fake_session_reload_patched, monkeypatch):
        from tools.sdlc_verdict import _cli_get, _cli_record

        # Env var points at a DIFFERENT session (the #1671 forked-subagent case).
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = fake_session_reload_patched  # the sdlc-local-1672 session
        # Ownership guard: give the session an issue_url that passes predicate 1.
        issue_session.issue_url = "https://github.com/x/y/issues/1672"

        # The REAL _find_session runs (not mocked). Its issue-first pass resolves
        # find_session_by_issue, which returns the issue session — NOT the env one.
        with patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session):
            recorded = _cli_record(self._args())
            # Read-after-write through the same issue-number path converges.
            got = _cli_get(self._args())

        assert recorded["verdict"] == "NEEDS REVISION"
        assert got["verdict"] == "NEEDS REVISION"


class TestNormalizeVerdict:
    """Unit tests for normalize_verdict helper (#1638)."""

    def test_none_returns_empty(self):
        assert normalize_verdict(None) == ""

    def test_empty_returns_empty(self):
        assert normalize_verdict("") == ""

    def test_whitespace_only_returns_empty(self):
        assert normalize_verdict("  ") == ""

    def test_underscore_form_converted(self):
        assert normalize_verdict("changes_requested") == "CHANGES REQUESTED"

    def test_idempotent_space_form(self):
        assert normalize_verdict("CHANGES REQUESTED") == "CHANGES REQUESTED"

    def test_mixed_case_uppercased(self):
        assert normalize_verdict("Changes Requested") == "CHANGES REQUESTED"

    def test_extra_whitespace_collapsed(self):
        assert normalize_verdict("  Changes  Requested  ") == "CHANGES REQUESTED"

    def test_non_str_returns_empty(self):
        assert normalize_verdict(42) == ""  # type: ignore[arg-type]

    def test_record_verdict_normalizes_underscore_form(self, fake_session_reload_patched):
        """Recording 'changes_requested' must store 'CHANGES REQUESTED' (#1638)."""
        session = fake_session_reload_patched
        record = record_verdict(session, "REVIEW", "changes_requested")
        assert record["verdict"] == "CHANGES REQUESTED"
        data = json.loads(session.stage_states)
        assert data["_verdicts"]["REVIEW"]["verdict"] == "CHANGES REQUESTED"


class TestOwnershipGate:
    """Tests for the ownership guard in _cli_record / main().

    The guard fires when --issue-number N is passed but the resolved session
    does not own issue N (via any of the three predicates). It raises
    OwnershipError, which main() catches, writes to stderr, and exits 1.
    """

    def _args(self, issue_number=42, **kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=issue_number,
            stage="CRITIQUE",
            verdict="READY TO BUILD",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def _owning_session(self, issue_number=42, via="issue_url"):
        """Build a _FakeSession that owns the given issue number."""
        if via == "issue_url":
            return _FakeSession(
                session_id="other-session",
                issue_url=f"https://github.com/x/y/issues/{issue_number}",
            )
        elif via == "session_id":
            return _FakeSession(session_id=f"sdlc-local-{issue_number}")
        elif via == "message_text":
            return _FakeSession(
                session_id="other-session",
                issue_url=None,
                message_text=f"SDLC issue #{issue_number} needs fixing",
            )
        raise ValueError(via)

    def _non_owning_session(self):
        """Build a _FakeSession that does NOT own issue 42."""
        return _FakeSession(
            session_id="different-session",
            issue_url="https://github.com/x/y/issues/99",
            message_text="working on issue 99",
        )

    def test_explicit_issue_non_owning_session_raises_ownership_error(self):
        """Non-owning session with --issue-number N raises OwnershipError."""
        from tools.sdlc_verdict import OwnershipError, _cli_record

        session = self._non_owning_session()
        with patch("tools.sdlc_verdict._find_session", return_value=session):
            with pytest.raises(OwnershipError) as exc_info:
                _cli_record(self._args(issue_number=42))

        err = str(exc_info.value)
        assert "42" in err
        assert "different-session" in err

    def test_explicit_issue_non_owning_session_main_exits_1(self, capsys):
        """main() with non-owning session exits 1 and writes issue + session to stderr."""
        import sys

        from tools.sdlc_verdict import main

        session = self._non_owning_session()
        with patch("tools.sdlc_verdict._find_session", return_value=session):
            with pytest.raises(SystemExit) as exc_info:
                sys.argv = [
                    "sdlc-verdict",
                    "record",
                    "--stage",
                    "CRITIQUE",
                    "--verdict",
                    "READY TO BUILD",
                    "--issue-number",
                    "42",
                ]
                main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "42" in captured.err
        assert "different-session" in captured.err

    def test_explicit_issue_owning_via_issue_url_succeeds(self, fake_session_reload_patched):
        """Session owning via issue_url → _cli_record returns the verdict record."""
        from tools.sdlc_verdict import _cli_record

        # Override with an owning session (predicate 1).
        session = self._owning_session(42, via="issue_url")
        # Patch _reload_session so verification passes in-memory.
        with (
            patch("tools.sdlc_verdict._find_session", return_value=session),
            patch("tools.stage_states_helpers._reload_session", return_value=session),
        ):
            result = _cli_record(self._args(issue_number=42))

        assert result.get("verdict") == "READY TO BUILD"

    def test_explicit_issue_owning_via_message_text_succeeds(self):
        """CRITICAL: predicate 3 (message_text) passes the ownership gate.

        This proves the third predicate is evaluated — a session with no issue_url
        and a non-matching session_id but a message_text containing 'issue #42'
        is permitted to write.
        """
        from tools.sdlc_verdict import _cli_record

        session = self._owning_session(42, via="message_text")
        # Predicate 3: session_id doesn't match sdlc-local-42, issue_url=None,
        # but message_text contains 'issue #42' — must NOT raise OwnershipError.
        with (
            patch("tools.sdlc_verdict._find_session", return_value=session),
            patch("tools.stage_states_helpers._reload_session", return_value=session),
        ):
            result = _cli_record(self._args(issue_number=42))

        assert result.get("verdict") == "READY TO BUILD"

    def test_no_issue_number_gate_not_triggered(self, fake_session_reload_patched):
        """Without --issue-number, the ownership gate is not triggered.

        A non-owning session is still allowed to write when no issue number is passed.
        """
        from tools.sdlc_verdict import _cli_record

        session = fake_session_reload_patched  # session_id="fake-1", no issue_url
        # Pass issue_number=None — gate must be bypassed entirely.
        args = self._args(issue_number=None)
        with patch("tools.sdlc_verdict._find_session", return_value=session):
            result = _cli_record(args)

        # Without an issue_number, the gate is skipped, write succeeds.
        assert result.get("verdict") == "READY TO BUILD"


class TestComputePlanBodyHash:
    """Unit tests for compute_plan_body_hash (#1761 Layer 3).

    The body-hash strips ONLY the ``revision_applied:`` frontmatter line so that
    writing ``revision_applied: true`` after a NEEDS REVISION round-trip does NOT
    bust the G5 critique-verdict cache.
    """

    def test_returns_sha256_prefixed_hex(self, tmp_path):
        f = tmp_path / "plan.md"
        f.write_text("# hello\n", encoding="utf-8")
        digest = compute_plan_body_hash(f)
        assert digest is not None
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64

    def test_returns_none_on_missing_file(self, tmp_path):
        assert compute_plan_body_hash(tmp_path / "missing.md") is None

    def test_crlf_normalized(self, tmp_path):
        a = tmp_path / "a.md"
        a.write_bytes(b"line1\nline2\n")
        b = tmp_path / "b.md"
        b.write_bytes(b"line1\r\nline2\r\n")
        assert compute_plan_body_hash(a) == compute_plan_body_hash(b)

    def test_no_frontmatter_hashes_whole_file(self, tmp_path):
        """Files with no YAML frontmatter are hashed unchanged."""
        f = tmp_path / "plan.md"
        f.write_text("# Title\n\nSome body text.\n", encoding="utf-8")
        # Should return a deterministic hash (not None).
        h = compute_plan_body_hash(f)
        assert h is not None
        assert h.startswith("sha256:")

    def test_revision_applied_true_stripped(self, tmp_path):
        """revision_applied: true is removed → same hash as if key were absent."""
        with_key = tmp_path / "with.md"
        without_key = tmp_path / "without.md"
        with_key.write_text(
            "---\nstatus: active\nrevision_applied: true\n---\n# body\n",
            encoding="utf-8",
        )
        without_key.write_text(
            "---\nstatus: active\n---\n# body\n",
            encoding="utf-8",
        )
        assert compute_plan_body_hash(with_key) == compute_plan_body_hash(without_key)

    def test_revision_applied_false_equiv_absent(self, tmp_path):
        """present-false and absent key produce the SAME hash."""
        with_false = tmp_path / "false.md"
        without = tmp_path / "absent.md"
        with_false.write_text(
            "---\nstatus: active\nrevision_applied: false\n---\n# body\n",
            encoding="utf-8",
        )
        without.write_text(
            "---\nstatus: active\n---\n# body\n",
            encoding="utf-8",
        )
        assert compute_plan_body_hash(with_false) == compute_plan_body_hash(without)

    def test_revision_applied_only_delta_unchanged(self, tmp_path):
        """Adding revision_applied: true to an otherwise unchanged plan must not change hash."""
        original = tmp_path / "original.md"
        after_apply = tmp_path / "after.md"
        original.write_text("---\nstatus: planning\n---\n# Plan\n\nSome text.\n", encoding="utf-8")
        after_apply.write_text(
            "---\nstatus: planning\nrevision_applied: true\n---\n# Plan\n\nSome text.\n",
            encoding="utf-8",
        )
        assert compute_plan_body_hash(original) == compute_plan_body_hash(after_apply)

    def test_status_key_change_still_busts_hash(self, tmp_path):
        """Other frontmatter keys (status:) must still produce different hashes."""
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text("---\nstatus: planning\n---\n# body\n", encoding="utf-8")
        b.write_text("---\nstatus: active\n---\n# body\n", encoding="utf-8")
        assert compute_plan_body_hash(a) != compute_plan_body_hash(b)

    def test_body_edit_busts_hash(self, tmp_path):
        """A prose body change must produce a different hash (G5 sensitivity)."""
        a = tmp_path / "a.md"
        b = tmp_path / "b.md"
        a.write_text(
            "---\nstatus: planning\nrevision_applied: true\n---\n# Original body\n",
            encoding="utf-8",
        )
        b.write_text(
            "---\nstatus: planning\nrevision_applied: true\n---\n# Modified body\n",
            encoding="utf-8",
        )
        assert compute_plan_body_hash(a) != compute_plan_body_hash(b)

    def test_only_revision_applied_in_frontmatter(self, tmp_path):
        """When revision_applied: is the only frontmatter key, the whole block is dropped."""
        with_only_key = tmp_path / "only.md"
        no_fm = tmp_path / "nofm.md"
        with_only_key.write_text("---\nrevision_applied: true\n---\n# body\n", encoding="utf-8")
        no_fm.write_text("# body\n", encoding="utf-8")
        # Both should hash to the same value (frontmatter block dropped entirely).
        assert compute_plan_body_hash(with_only_key) == compute_plan_body_hash(no_fm)

    def test_unterminated_frontmatter_degrades_gracefully(self, tmp_path):
        """Malformed frontmatter without closing --- hashes the whole file."""
        f = tmp_path / "plan.md"
        f.write_text("---\nstatus: active\nrevision_applied: true\n# body\n", encoding="utf-8")
        h = compute_plan_body_hash(f)
        # Should return a valid hash (not None).
        assert h is not None
        assert h.startswith("sha256:")


class TestG5TransparentMigration:
    """Guard G5 transparent migration: stored full-bytes hash rewritten to
    revision_applied-stripped hash when the only delta is revision_applied:.
    """

    def _make_states(self, cached_hash: str, verdict: str = "READY TO BUILD (NO CONCERNS)") -> dict:
        return {
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": verdict,
                    "artifact_hash": cached_hash,
                }
            }
        }

    def test_migration_rewrites_in_place_and_treats_as_hit(self, tmp_path):
        """When stored hash is legacy full-bytes and only revision_applied changed,
        the guard rewrites artifact_hash in-place and returns a cache-hit dispatch."""
        from agent.sdlc_router import guard_g5_artifact_hash_cache

        plan = tmp_path / "plan.md"
        # Write plan WITH revision_applied: true
        plan.write_text(
            "---\nstatus: active\nrevision_applied: true\n---\n# body\n", encoding="utf-8"
        )

        from tools.sdlc_verdict import compute_plan_body_hash, compute_plan_hash

        legacy_hash = compute_plan_hash(plan)  # full-bytes (old)
        body_hash = compute_plan_body_hash(plan)  # stripped (new)

        # Simulate: stored hash is the OLD legacy hash; current hash is the new body hash.
        assert legacy_hash != body_hash  # precondition: they differ
        states = self._make_states(legacy_hash, verdict="READY TO BUILD (NO CONCERNS)")
        meta = {}
        context = {
            "current_plan_hash": body_hash,
            "issue_number": 1761,
        }

        from unittest.mock import patch

        with patch("tools._sdlc_utils.find_plan_path", return_value=plan):
            result = guard_g5_artifact_hash_cache(states, meta, context)

        from agent.sdlc_router import SKILL_DO_BUILD, Dispatch

        # After migration, G5 should treat it as a cache hit and dispatch /do-build.
        assert isinstance(result, Dispatch)
        assert result.skill == SKILL_DO_BUILD
        # The in-place rewrite should have updated the record.
        assert states["_verdicts"]["CRITIQUE"]["artifact_hash"] == body_hash

    def test_no_migration_on_genuine_body_change(self, tmp_path):
        """When the plan body actually changed, G5 returns None (cache miss)."""
        from agent.sdlc_router import guard_g5_artifact_hash_cache

        original_plan = tmp_path / "original.md"
        original_plan.write_text("---\nstatus: active\n---\n# Original body\n", encoding="utf-8")

        modified_plan = tmp_path / "modified.md"
        modified_plan.write_text("---\nstatus: active\n---\n# DIFFERENT body\n", encoding="utf-8")

        from tools.sdlc_verdict import compute_plan_body_hash

        old_hash = compute_plan_body_hash(original_plan)
        new_hash = compute_plan_body_hash(modified_plan)
        assert old_hash != new_hash  # genuine content change

        states = self._make_states(old_hash)
        meta = {}
        context = {
            "current_plan_hash": new_hash,
            "issue_number": 1761,
        }

        with patch("tools._sdlc_utils.find_plan_path", return_value=modified_plan):
            result = guard_g5_artifact_hash_cache(states, meta, context)

        # No migration: genuine body change → cache miss → None.
        assert result is None

    def test_no_migration_when_issue_number_missing(self, tmp_path):
        """When issue_number is absent from context, migration is skipped."""
        from agent.sdlc_router import guard_g5_artifact_hash_cache

        plan = tmp_path / "plan.md"
        plan.write_text("---\nrevision_applied: true\n---\n# body\n", encoding="utf-8")

        from tools.sdlc_verdict import compute_plan_body_hash, compute_plan_hash

        legacy_hash = compute_plan_hash(plan)
        body_hash = compute_plan_body_hash(plan)

        states = self._make_states(legacy_hash)
        meta = {}
        # No issue_number in context → migration cannot run.
        context = {"current_plan_hash": body_hash}

        result = guard_g5_artifact_hash_cache(states, meta, context)

        # Without issue_number, no plan path can be resolved, so it's a cache miss.
        assert result is None
