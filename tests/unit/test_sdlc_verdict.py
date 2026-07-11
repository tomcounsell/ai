"""Unit tests for tools.sdlc_verdict — single-writer verdict recorder."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

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

    def test_record_verdict_does_not_touch_issue_lock(self, fake_session_reload_patched):
        """Issue #1954 scope-narrowing: verdict record fires during PLAN/CRITIQUE
        bookkeeping with no established recurrence path through an in-progress
        BUILD/TEST/REVIEW stage, so it must NOT renew the issue-level SDLC
        ownership lock. touch_issue_lock() must never be called from this path."""
        session = fake_session_reload_patched
        with patch("models.session_lifecycle.touch_issue_lock") as mock_touch:
            record_verdict(session, "CRITIQUE", "NEEDS REVISION")

        mock_touch.assert_not_called()


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


class TestCliRecordLease:
    """Issue #2012 task 2: `verdict record` writes the issue-keyed
    PipelineLedger, authorized SOLELY by the run_id-keyed issue lease --
    there is no session left to resolve or auto-ensure."""

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
            run_id="run-test",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def _lock_result(self, **kw):
        from models.session_lifecycle import IssueLockResult

        base = dict(acquired=True, owner_session_id="s", owner_run_id="run-test", target_repo="o/r")
        base.update(kw)
        return IssueLockResult(**base)

    def test_cli_record_writes_via_ledger_with_valid_lease(self):
        from tools.sdlc_verdict import _cli_record

        mock_touch = MagicMock(return_value=self._lock_result())
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            result = _cli_record(self._args())

        assert result["verdict"] == "READY TO BUILD"
        # Two lock touches: the read-only peek, then the non-peek
        # revalidation immediately before the write (Risk 5 TOCTOU close).
        assert mock_touch.call_count == 2
        peek_calls = [c for c in mock_touch.call_args_list if c.kwargs.get("peek")]
        revalidate_calls = [c for c in mock_touch.call_args_list if not c.kwargs.get("peek")]
        assert len(peek_calls) == 1
        assert len(revalidate_calls) == 1
        assert revalidate_calls[0].kwargs.get("target_repo") == "o/r"

    def test_cli_get_reads_back_the_recorded_ledger_verdict(self):
        from tools.sdlc_verdict import _cli_get, _cli_record

        mock_touch = MagicMock(return_value=self._lock_result())
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            _cli_record(self._args())
            got = _cli_get(self._args())

        assert got["verdict"] == "READY TO BUILD"

    def test_missing_run_id_or_issue_number_raises_lease_absent(self):
        from tools.sdlc_verdict import OwnershipError, _cli_record

        with pytest.raises(OwnershipError, match="LEASE_ABSENT"):
            _cli_record(self._args(run_id=None))

    def test_unheld_lease_raises_lease_absent(self):
        from tools.sdlc_verdict import OwnershipError, _cli_record

        mock_touch = MagicMock(return_value=self._lock_result(owner_run_id=None, target_repo=None))
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            with pytest.raises(OwnershipError, match="LEASE_ABSENT"):
                _cli_record(self._args())

    def test_target_repo_missing_raises_and_never_writes(self):
        """Risk 5 (writer side): a valid lease with no pinned target_repo
        must hard-fail and never write a PipelineLedger record."""
        from tools.sdlc_verdict import OwnershipError, _cli_record

        mock_touch = MagicMock(return_value=self._lock_result(target_repo=None))
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            with pytest.raises(OwnershipError, match="TARGET_REPO_MISSING"):
                _cli_record(self._args())

        mock_get_or_create.assert_not_called()


class TestForeignRunIdRefused:
    """#2003/#2012: a foreign run holding the issue lock refuses the verdict
    write with an ISSUE_LOCKED diagnostic (raised as OwnershipError so
    main() exits 1). No session is ever resolved in this path."""

    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=42,
            stage="CRITIQUE",
            verdict="READY TO BUILD",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
            run_id="intruder-run",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_foreign_run_id_raises_issue_locked(self):
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_verdict import OwnershipError, _cli_record

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )

        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
            with pytest.raises(OwnershipError) as exc_info:
                _cli_record(self._args())

        err = str(exc_info.value)
        assert "ISSUE_LOCKED" in err
        assert "foreign-run" in err
        # Only the read-only peek fires -- no write is ever attempted.
        for call in mock_touch.call_args_list:
            assert call.kwargs.get("peek") is True

    def test_main_exits_1_with_issue_locked_diagnostic(self, capsys):
        import sys

        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_verdict import main

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )
        with patch("models.session_lifecycle.touch_issue_lock", mock_touch):
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
                    "--run-id",
                    "intruder-run",
                ]
                main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "ISSUE_LOCKED" in captured.err
        assert "foreign-run" in captured.err


class TestCrossVendorJudgeRoundTrip:
    """Verifies that a cross-vendor judge dict round-trips correctly through record_verdict."""

    def test_cross_vendor_judge_dict_round_trips_into_judges_field(
        self, fake_session_reload_patched
    ):
        from tools.cross_vendor_judge import CROSS_VENDOR_JUDGE_ID

        session = fake_session_reload_patched
        judge_dict = {
            "judge_id": CROSS_VENDOR_JUDGE_ID,
            "verdict": "CHANGES REQUESTED",
            "blockers": 1,
            "tech_debt": 0,
            "confidence": 0.8,
        }
        consensus = {
            "rule": "any-blocker-wins",
            "k": 1,
            "n": 1,
            "mean_confidence": 0.8,
            "blocker_aggregation": "max",
            "tied": False,
            "decided_at": "2026-01-01T00:00:00+00:00",
        }
        record_verdict(
            session,
            "REVIEW",
            "CHANGES REQUESTED",
            blockers=1,
            tech_debt=0,
            judges=[judge_dict],
            consensus=consensus,
        )
        data = json.loads(session.stage_states)
        stored_judges = data["_verdicts"]["REVIEW"]["_judges"]
        # The cross-vendor judge dict must be present.
        cross_vendor_entries = [
            j for j in stored_judges if j.get("judge_id") == CROSS_VENDOR_JUDGE_ID
        ]
        assert len(cross_vendor_entries) == 1
        assert cross_vendor_entries[0]["verdict"] == "CHANGES REQUESTED"
        assert cross_vendor_entries[0]["blockers"] == 1


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

        # Simulate: stored hash is the OLD legacy hash; current hash is the new
        # body hash. The legacy hash is caller-supplied via context (import
        # boundary: the router must not import tools/ to compute it itself).
        assert legacy_hash != body_hash  # precondition: they differ
        states = self._make_states(legacy_hash, verdict="READY TO BUILD (NO CONCERNS)")
        meta = {}
        context = {
            "current_plan_hash": body_hash,
            "legacy_plan_hash": legacy_hash,
            "issue_number": 1761,
        }

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

        from tools.sdlc_verdict import compute_plan_body_hash, compute_plan_hash

        old_hash = compute_plan_body_hash(original_plan)
        new_hash = compute_plan_body_hash(modified_plan)
        assert old_hash != new_hash  # genuine content change

        states = self._make_states(old_hash)
        meta = {}
        context = {
            "current_plan_hash": new_hash,
            # Caller-supplied legacy hash of the CURRENT plan file — with a
            # genuine body change it does not match the stored hash, so the
            # migration must not fire.
            "legacy_plan_hash": compute_plan_hash(modified_plan),
            "issue_number": 1761,
        }

        result = guard_g5_artifact_hash_cache(states, meta, context)

        # No migration: genuine body change → cache miss → None.
        assert result is None

    def test_no_migration_when_legacy_hash_missing(self, tmp_path):
        """When legacy_plan_hash is absent from context, migration is skipped.

        The router never computes the legacy hash itself (it must not import
        tools/); a caller that omits ``legacy_plan_hash`` — e.g. because no
        plan path resolved — gets plain cache-miss behavior.
        """
        from agent.sdlc_router import guard_g5_artifact_hash_cache

        plan = tmp_path / "plan.md"
        plan.write_text("---\nrevision_applied: true\n---\n# body\n", encoding="utf-8")

        from tools.sdlc_verdict import compute_plan_body_hash, compute_plan_hash

        legacy_hash = compute_plan_hash(plan)
        body_hash = compute_plan_body_hash(plan)

        states = self._make_states(legacy_hash)
        meta = {}
        # No legacy_plan_hash in context → migration cannot run.
        context = {"current_plan_hash": body_hash}

        result = guard_g5_artifact_hash_cache(states, meta, context)

        # Without the caller-supplied legacy hash, it's a plain cache miss.
        assert result is None
