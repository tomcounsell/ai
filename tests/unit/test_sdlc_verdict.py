"""Unit tests for tools.sdlc_verdict — single-writer verdict recorder."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from tools._sdlc_utils import normalize_verdict
from tools.sdlc_verdict import (
    compute_plan_body_hash,
    compute_plan_hash,
    critique_table_has_findings,
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


class TestVerdictInvariantSatisfied:
    """Issue #2305 defect 4: verdict_invariant_satisfied is the ONE shared
    fail-closed predicate backing *stage marker-completed => verdict-readable*
    for both the direct write_marker completed-path (via the
    tools.sdlc_stage_marker delegating helpers) and the
    PipelineStateMachine._backfill_predecessors scan-phase gate."""

    def test_review_true_when_verdict_trailer_and_artifact_all_present(self):
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with (
            patch("tools.sdlc_verdict._review_verdict_readable", return_value=True),
            patch("tools.sdlc_verdict._review_trailer_present", return_value=True),
            patch("tools.sdlc_verdict._review_artifact_readable", return_value=True),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is True

    def test_review_false_when_verdict_not_readable(self):
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with (
            patch("tools.sdlc_verdict._review_verdict_readable", return_value=False),
            patch("tools.sdlc_verdict._review_trailer_present", return_value=True),
            patch("tools.sdlc_verdict._review_artifact_readable", return_value=True),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is False

    def test_review_false_when_trailer_missing(self):
        """Readable-but-untrailered APPROVED verdict: the AND must reject it."""
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with (
            patch("tools.sdlc_verdict._review_verdict_readable", return_value=True),
            patch("tools.sdlc_verdict._review_trailer_present", return_value=False),
            patch("tools.sdlc_verdict._review_artifact_readable", return_value=True),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is False

    def test_review_false_when_no_posted_artifact(self):
        """Issue #2577: the third conjunct. `finalize` records the verdict and
        trailer BEFORE it checks for a posted artifact, so a run that refused on
        REVIEW_ARTIFACT_MISSING leaves a verdict+trailer behind. Without this
        the backfill behind a later `--stage DOCS --status completed` read that
        as a satisfied invariant and force-completed a REVIEW nobody wrote."""
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with (
            patch("tools.sdlc_verdict._review_verdict_readable", return_value=True),
            patch("tools.sdlc_verdict._review_trailer_present", return_value=True),
            patch("tools.sdlc_verdict._review_artifact_readable", return_value=False),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is False

    def test_artifact_conjunct_delegates_to_the_marker_probe(self):
        """One implementation, two call sites — the backfill gate and
        write_marker's direct completed-path cannot disagree about the artifact."""
        from tools.sdlc_verdict import _review_artifact_readable

        with (
            patch("tools._sdlc_utils.resolve_target_repo_for_read", return_value="o/r"),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True) as probe,
        ):
            assert _review_artifact_readable(2577) is True
        probe.assert_called_once_with(2577, "o/r")

    def test_artifact_conjunct_fails_closed_on_error(self):
        from tools.sdlc_verdict import _review_artifact_readable

        with patch(
            "tools.sdlc_stage_marker._review_artifact_posted",
            side_effect=RuntimeError("gh exploded"),
        ):
            assert _review_artifact_readable(2577) is False

    def test_artifact_conjunct_false_without_an_issue_number(self):
        from tools.sdlc_verdict import _review_artifact_readable

        assert _review_artifact_readable(None) is False

    def test_critique_true_when_verdict_readable(self):
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with patch("tools.sdlc_verdict._critique_verdict_readable", return_value=True):
            assert verdict_invariant_satisfied("CRITIQUE", 2124) is True

    def test_critique_false_when_verdict_not_readable(self):
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with patch("tools.sdlc_verdict._critique_verdict_readable", return_value=False):
            assert verdict_invariant_satisfied("CRITIQUE", 2124) is False

    def test_other_stage_passes_through_true(self):
        """The predicate only gates REVIEW/CRITIQUE; any other stage is a
        pass-through so callers can invoke it uniformly."""
        from tools.sdlc_verdict import verdict_invariant_satisfied

        assert verdict_invariant_satisfied("BUILD", 1234) is True

    def test_fails_closed_on_unexpected_exception(self):
        """Even an exception raised inside the stage-dispatch itself (not
        just inside the sub-helpers, which already fail closed on their own)
        is caught and refused -- never propagates as a surprise crash."""
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with patch(
            "tools.sdlc_verdict._review_verdict_readable",
            side_effect=RuntimeError("boom"),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is False

    def test_review_end_to_end_true_with_real_helpers(self):
        """Integration-style check (no shared-helper mocking): a resolvable
        record with an APPROVED verdict + well-formed trailer satisfies the
        invariant through the real _review_verdict_readable/_review_trailer_present
        implementations."""
        from tools.sdlc_verdict import verdict_invariant_satisfied

        record = MagicMock()
        trailer_text = "APPROVED REVIEW_CONTEXT head_sha=" + "a" * 40
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": trailer_text},
            ),
            # The artifact probe needs a real PR on github.com; stub that seam
            # only. The verdict/trailer helpers still run for real.
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            assert verdict_invariant_satisfied("REVIEW", 2062) is True

    def test_review_end_to_end_false_when_no_record(self):
        from tools.sdlc_verdict import verdict_invariant_satisfied

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            assert verdict_invariant_satisfied("REVIEW", 2062) is False


# ---------------------------------------------------------------------------
# Issue #2447: Critique findings persistence contract
# ---------------------------------------------------------------------------

# Canonical table shapes reused across the parser + gate tests.
_TABLE_HEADER = (
    "| Severity | Critic | Finding | Addressed By | Implementation Note |\n"
    "|----------|--------|---------|--------------|---------------------|\n"
)
_PLACEHOLDER_ROW = "| CONCERN | [agent-type] | [The concern raised] | [x] | [gotcha] |\n"
_REAL_ROW = "| BLOCKER | Risk & Robustness | The parser splits naively | pending | fix it |\n"
_PIPE_ROW = r"| CONCERN | Risk | the value a \| b matters | pending | note |" + "\n"


def _plan_doc(*rows: str, header: str = "## Critique Results") -> str:
    """Assemble a minimal plan doc with a Critique Results section."""
    return (
        "---\nstatus: Ready\n---\n\n# Plan\n\n"
        f"{header}\n\n"
        "<!-- Populated by /do-plan-critique (war room). Leave empty until run. -->\n"
        f"{_TABLE_HEADER}"
        f"{''.join(rows)}"
        "\n---\n\n## Open Questions\n\nNone.\n"
    )


class TestCritiqueTableHasFindings:
    """Strict real-finding-row parser (issue #2447 Technical Approach step 1)."""

    def test_placeholder_only_is_empty(self, tmp_path):
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")
        assert critique_table_has_findings(p) is False

    def test_real_finding_row_is_non_empty(self, tmp_path):
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_REAL_ROW), encoding="utf-8")
        assert critique_table_has_findings(p) is True

    @pytest.mark.parametrize("severity", ["BLOCKER", "CONCERN", "NIT"])
    def test_each_real_severity_counts(self, tmp_path, severity):
        row = f"| {severity} | Some Critic | A concrete finding | pending | note |\n"
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(row), encoding="utf-8")
        assert critique_table_has_findings(p) is True

    def test_no_findings_line_is_empty(self, tmp_path):
        """A 'No findings from the war room.' line (READY path) is not a finding."""
        doc = (
            "# Plan\n\n## Critique Results\n\nNo findings from the war room.\n\n"
            "---\n\n## Open Questions\n"
        )
        p = tmp_path / "plan.md"
        p.write_text(doc, encoding="utf-8")
        assert critique_table_has_findings(p) is False

    def test_missing_file_is_empty(self, tmp_path):
        assert critique_table_has_findings(tmp_path / "does-not-exist.md") is False

    def test_no_critique_section_is_empty(self, tmp_path):
        p = tmp_path / "plan.md"
        p.write_text("# Plan\n\n## Solution\n\nStuff.\n", encoding="utf-8")
        assert critique_table_has_findings(p) is False

    def test_escaped_pipe_in_finding_is_one_cell(self, tmp_path):
        """A Finding cell containing an escaped pipe (``a \\| b``) is parsed as
        ONE cell — the row counts as a real finding, not mis-columned to empty
        (concern 1)."""
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PIPE_ROW), encoding="utf-8")
        assert critique_table_has_findings(p) is True

    def test_bracketed_placeholder_finding_is_empty(self, tmp_path):
        """A row whose Finding cell is wholly a bracketed placeholder is empty
        even with a valid severity."""
        row = "| BLOCKER | Critic | [describe the blocker] | pending | note |\n"
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(row), encoding="utf-8")
        assert critique_table_has_findings(p) is False

    def test_section_bounded_by_next_heading(self, tmp_path):
        """A real row that appears AFTER the next ``##`` heading must not count."""
        doc = (
            "# Plan\n\n## Critique Results\n\n"
            f"{_TABLE_HEADER}{_PLACEHOLDER_ROW}\n"
            "## Later Section\n\n"
            f"{_TABLE_HEADER}{_REAL_ROW}\n"
        )
        p = tmp_path / "plan.md"
        p.write_text(doc, encoding="utf-8")
        assert critique_table_has_findings(p) is False


class _GateArgs:
    """Builds argparse-style namespaces for _cli_record gate tests."""

    @staticmethod
    def make(**kw):
        from types import SimpleNamespace

        base = dict(
            session_id=None,
            issue_number=2447,
            stage="CRITIQUE",
            verdict="NEEDS REVISION",
            blockers=None,
            tech_debt=None,
            judges_json=None,
            consensus_json=None,
            run_id="run-test",
        )
        base.update(kw)
        return SimpleNamespace(**base)


def _lease_ok(**kw):
    from models.session_lifecycle import IssueLockResult

    base = dict(acquired=True, owner_session_id="s", owner_run_id="run-test", target_repo="o/r")
    base.update(kw)
    return IssueLockResult(**base)


class TestCliRecordCritiqueFindingsGate:
    """Fail-closed CRITIQUE findings gate in _cli_record (issue #2447 step 2).

    Ownership is adjudicated first; the findings gate only fires once the write
    is authorized. Each test provides a valid lease via a mocked
    ``touch_issue_lock`` and a resolvable plan via a patched ``_find_plan_path``.
    """

    def _run(self, plan_path, monkeypatch=None, **arg_overrides):
        from tools import sdlc_verdict
        from tools.sdlc_verdict import _cli_record

        mock_touch = MagicMock(return_value=_lease_ok())
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch.object(sdlc_verdict, "_find_plan_path", return_value=plan_path),
        ):
            return _cli_record(_GateArgs.make(**arg_overrides))

    def test_needs_revision_empty_table_raises(self, tmp_path):
        from tools.sdlc_verdict import CritiqueFindingsMissingError

        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")
        with pytest.raises(CritiqueFindingsMissingError, match="CRITIQUE_FINDINGS_MISSING"):
            self._run(p, verdict="NEEDS REVISION")

    def test_needs_revision_populated_table_records(self, tmp_path):
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_REAL_ROW), encoding="utf-8")
        result = self._run(p, verdict="NEEDS REVISION")
        assert result["verdict"] == "NEEDS REVISION"

    @pytest.mark.parametrize(
        "verdict",
        [
            "READY TO BUILD",
            "READY TO BUILD (no concerns)",
            "READY TO BUILD (with concerns)",
        ],
    )
    def test_ready_to_build_empty_table_no_raise(self, tmp_path, verdict):
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")
        result = self._run(p, verdict=verdict)
        assert result["verdict"] == normalize_verdict(verdict)

    @pytest.mark.parametrize(
        "verdict",
        ["MAJOR REWORK", "MAJOR REWORK (CRITIQUE INCOMPLETE)"],
    )
    def test_major_rework_empty_table_no_raise(self, tmp_path, verdict):
        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")
        result = self._run(p, verdict=verdict)
        assert result["verdict"] == normalize_verdict(verdict)

    def test_unresolvable_plan_under_needs_revision_fails_closed(self):
        """An unresolvable plan under NEEDS REVISION now REFUSES the write.

        Plan resolution is one rung -- a ``tracking:`` frontmatter line naming
        the issue -- so "unresolvable" and "no findings" are no longer
        distinguishable: there is no document to read findings out of. Skipping
        was safe only while the deleted bare-mention fallback made an
        unresolvable plan rare.
        """
        from tools.sdlc_verdict import CritiqueFindingsMissingError

        with pytest.raises(CritiqueFindingsMissingError, match="CRITIQUE_PLAN_UNRESOLVABLE"):
            self._run(None, verdict="NEEDS REVISION")

    @pytest.mark.parametrize(
        "verdict",
        ["READY TO BUILD", "MAJOR REWORK", "MAJOR REWORK (CRITIQUE INCOMPLETE)"],
    )
    def test_unresolvable_plan_outside_needs_revision_does_not_raise(self, verdict):
        """The refusal stays exactly as narrow as the findings gate it extends."""
        result = self._run(None, verdict=verdict)
        assert result["verdict"] == normalize_verdict(verdict)

    def test_needs_revision_lowercase_variant_still_gates(self, tmp_path):
        """The gate triggers on the normalized form, so a lowercase/spacing
        variant of NEEDS REVISION with an empty table still fails closed."""
        from tools.sdlc_verdict import CritiqueFindingsMissingError

        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")
        with pytest.raises(CritiqueFindingsMissingError, match="CRITIQUE_FINDINGS_MISSING"):
            self._run(p, verdict="  needs   revision  ")


class TestCritiqueGateOwnershipPrecedence:
    """Ownership errors (LEASE_ABSENT/ISSUE_LOCKED) are raised BEFORE the
    findings gate (issue #2447 concern 2) — a foreign/absent lease must never
    be masked by CRITIQUE_FINDINGS_MISSING."""

    def test_absent_lease_raises_lease_absent_not_findings_missing(self, tmp_path):
        from tools import sdlc_verdict
        from tools.sdlc_verdict import (
            CritiqueFindingsMissingError,
            OwnershipError,
            _cli_record,
        )

        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")  # empty table
        # No run_id → resolve_ledger_lease fails LEASE_ABSENT before the gate.
        with patch.object(sdlc_verdict, "_find_plan_path", return_value=p):
            with pytest.raises(OwnershipError, match="LEASE_ABSENT"):
                _cli_record(_GateArgs.make(run_id=None, verdict="NEEDS REVISION"))
        assert not issubclass(OwnershipError, CritiqueFindingsMissingError)

    def test_foreign_lease_raises_issue_locked_not_findings_missing(self, tmp_path):
        from models.session_lifecycle import IssueLockResult
        from tools import sdlc_verdict
        from tools.sdlc_verdict import OwnershipError, _cli_record

        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_PLACEHOLDER_ROW), encoding="utf-8")  # empty table
        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch.object(sdlc_verdict, "_find_plan_path", return_value=p),
        ):
            with pytest.raises(OwnershipError) as exc_info:
                _cli_record(_GateArgs.make(verdict="NEEDS REVISION"))
        assert "ISSUE_LOCKED" in str(exc_info.value)


class TestRecordVerdictApiUnchangedByGate:
    """The gate lives in the CLI path only; record_verdict (Python API) keeps
    its graceful ``{}``-on-failure, never-raise contract (issue #2447)."""

    def test_record_verdict_needs_revision_never_raises(self, fake_session_reload_patched):
        """A NEEDS REVISION verdict the CLI gate would reject records fine
        through the Python API — the gate does not touch this path."""
        session = fake_session_reload_patched
        record = record_verdict(session, "CRITIQUE", "NEEDS REVISION")
        assert record["verdict"] == "NEEDS REVISION"

    def test_record_verdict_returns_empty_on_failure_not_raise(self):
        """update_stage_states failure still yields {} — never an exception."""
        session = _FakeSession()
        with patch(
            "tools.stage_states_helpers.update_stage_states",
            side_effect=RuntimeError("boom"),
        ):
            assert record_verdict(session, "CRITIQUE", "NEEDS REVISION") == {}


class TestNormalizeVerdictNeedsRevisionCanonicalization:
    """Risk 3: the gate's exact ``== 'NEEDS REVISION'`` match relies on
    normalize_verdict canonicalizing every variant to that exact form."""

    @pytest.mark.parametrize(
        "variant",
        [
            "NEEDS REVISION",
            "needs revision",
            "Needs Revision",
            "  needs   revision  ",
            "needs_revision",
            "NEEDS_REVISION",
        ],
    )
    def test_variants_canonicalize_to_needs_revision(self, variant):
        assert normalize_verdict(variant) == "NEEDS REVISION"


class TestCritiqueGateOrphanedTableRecovery:
    """Concern 3: a record-time failure AFTER the gate passes (populated table)
    must leave a re-runnable state — no half-written verdict."""

    def test_record_failure_after_gate_leaves_no_verdict(self, tmp_path):
        from tools import sdlc_verdict
        from tools.sdlc_verdict import _cli_record

        p = tmp_path / "plan.md"
        p.write_text(_plan_doc(_REAL_ROW), encoding="utf-8")  # populated: gate passes
        mock_touch = MagicMock(return_value=_lease_ok())
        # Simulate a record-time failure downstream of the gate: record_verdict
        # returns {} (its graceful-failure contract) — no partial verdict lands.
        with (
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch.object(sdlc_verdict, "_find_plan_path", return_value=p),
            patch.object(sdlc_verdict, "record_verdict", return_value={}) as mock_record,
        ):
            result = _cli_record(_GateArgs.make(verdict="NEEDS REVISION"))
        # Gate passed (record_verdict was reached) but the write failed cleanly.
        mock_record.assert_called_once()
        assert result == {}


class TestCliGetEmptyResultDiagnostics:
    """`verdict get` must distinguish its two empty outcomes (#2588).

    A bare `{}` was returned both when no SDLC session or ledger existed at all
    and when one existed but carried no verdict yet. The remedies differ
    (`session-ensure` vs `verdict finalize`), and a reviewer facing the first
    case had no way to learn that from the tool -- they saw an empty dict,
    concluded the substrate was broken, and stalled.
    """

    def _args(self, **kw):
        from argparse import Namespace

        defaults = {"stage": "REVIEW", "session_id": None, "issue_number": 999123}
        defaults.update(kw)
        return Namespace(**defaults)

    def test_no_substrate_names_session_ensure(self, capsys):
        from tools.sdlc_verdict import _cli_get

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            result = _cli_get(self._args())

        captured = capsys.readouterr()
        assert result == {}, "stdout contract must not change"
        assert captured.out == "", "the diagnostic belongs on stderr, not stdout"
        assert "NO_VERDICT_SUBSTRATE" in captured.err
        assert "session-ensure --issue-number 999123" in captured.err

    def test_substrate_without_verdict_names_finalize(self, capsys):
        from tools.sdlc_verdict import _cli_get

        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={}),
        ):
            result = _cli_get(self._args())

        captured = capsys.readouterr()
        assert result == {}
        assert "NO_VERDICT_RECORDED" in captured.err
        assert "verdict finalize" in captured.err
        assert "session-ensure" not in captured.err, (
            "a substrate that exists must not send the reader to session-ensure"
        )

    def test_a_present_verdict_prints_no_diagnostic(self, capsys):
        """Anti-criterion: the happy path stays quiet."""
        from tools.sdlc_verdict import _cli_get

        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
        ):
            result = _cli_get(self._args())

        assert result == {"verdict": "APPROVED"}
        assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# Issue #2769: head_sha is its OWN record field, never concatenated into the
# verdict token (normalize_verdict runs over the whole verdict string and was
# mangling `APPROVED REVIEW_CONTEXT head_sha=<hex>` into
# `APPROVED REVIEW CONTEXT HEAD SHA=<HEX>`).
# ---------------------------------------------------------------------------

_2769_SHA = "abcdef0123456789abcdef0123456789abcdef01"


class TestRecordVerdictHeadSha:
    def test_head_sha_is_stored_as_its_own_field_verdict_stays_bare(
        self, fake_session_reload_patched
    ):
        session = fake_session_reload_patched
        record = record_verdict(session, "REVIEW", "APPROVED", head_sha=_2769_SHA)

        assert record["verdict"] == "APPROVED"
        assert record["head_sha"] == _2769_SHA
        got = get_verdict(session, "REVIEW")
        assert got["verdict"] == "APPROVED"
        assert got["head_sha"] == _2769_SHA

    def test_head_sha_is_not_normalized(self, fake_session_reload_patched):
        """It is a raw 40-hex commit SHA -- uppercasing it would break nothing
        semantically (comparisons are case-insensitive) but the field must be
        stored verbatim, not routed through normalize_verdict."""
        session = fake_session_reload_patched
        record = record_verdict(session, "REVIEW", "approved", head_sha=_2769_SHA)
        assert record["verdict"] == "APPROVED"  # the verdict IS normalized
        assert record["head_sha"] == _2769_SHA  # the SHA is NOT

    @pytest.mark.parametrize("falsy", [None, "", "   ", "\n\t"])
    def test_falsy_head_sha_writes_no_field_at_all(self, fake_session_reload_patched, falsy):
        session = fake_session_reload_patched
        record = record_verdict(session, "REVIEW", "APPROVED", head_sha=falsy)
        assert "head_sha" not in record
        assert "head_sha" not in get_verdict(session, "REVIEW")

    def test_malformed_head_sha_never_reads_as_a_valid_trailer(self, fake_session_reload_patched):
        """A non-40-hex value is stored but can never masquerade as a real head
        SHA: it matches no PR head, so every freshness gate fails CLOSED."""
        from tools._sdlc_utils import _HEAD_SHA_TRAILER_RE, head_sha_of_record

        session = fake_session_reload_patched
        record = record_verdict(session, "REVIEW", "APPROVED", head_sha="not-a-sha")

        # Stored verbatim -- record_verdict does not validate, so the ledger
        # keeps whatever the caller wrote.
        assert record["head_sha"] == "not-a-sha"
        # The verdict token itself carries no trailer-shaped text.
        assert _HEAD_SHA_TRAILER_RE.search(record["verdict"]) is None
        # ...but the READ path treats a malformed field exactly as it treats a
        # malformed trailer: absent. Otherwise arbitrary text would satisfy
        # _review_trailer_present, the gate that refuses a malformed trailer.
        assert head_sha_of_record(record) == ""

    def test_head_sha_lands_in_the_same_single_write(self, fake_session_reload_patched):
        """Single-writer invariant: one update_stage_states call, never two."""
        session = fake_session_reload_patched
        with patch(
            "tools.stage_states_helpers.update_stage_states", return_value=True
        ) as mock_update:
            record_verdict(session, "REVIEW", "APPROVED", head_sha=_2769_SHA, blockers=0)
        assert mock_update.call_count == 1


class TestReviewTrailerPresentReadsBothShapes:
    """`_review_trailer_present` gates the REVIEW completed marker. It must
    accept BOTH the new field shape and the legacy mangled-token shape."""

    @pytest.mark.parametrize(
        "verdict_record",
        [
            pytest.param({"verdict": "APPROVED", "head_sha": _2769_SHA}, id="field-shape"),
            pytest.param(
                {"verdict": f"APPROVED REVIEW CONTEXT HEAD SHA={_2769_SHA.upper()}"},
                id="legacy-mangled-token",
            ),
            pytest.param(
                {"verdict": f"APPROVED REVIEW_CONTEXT head_sha={_2769_SHA}"},
                id="legacy-raw-token",
            ),
        ],
    )
    def test_accepts_every_recorded_shape(self, verdict_record):
        from tools.sdlc_verdict import _review_trailer_present

        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=MagicMock()),
            patch("tools.sdlc_verdict.get_verdict", return_value=verdict_record),
        ):
            assert _review_trailer_present(2769) is True

    def test_refuses_an_approved_verdict_attributed_to_no_head_sha(self):
        from tools.sdlc_verdict import _review_trailer_present

        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=MagicMock()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
        ):
            assert _review_trailer_present(2769) is False


# ---------------------------------------------------------------------------
# Issue #2767(a): `--blocker-count` / `--tech-debt-count`.
#
# The old bare `--blockers` had `type=int` and NO help text, on a call
# /do-pr-review declares "mandatory and terminal". An agent passing findings
# prose got `invalid int value:` and a hard STOP, leaving the review posted to
# GitHub but absent from the ledger (observed on the popoto #537 pipeline).
# ---------------------------------------------------------------------------


class TestFinalizeCountFlagSpellings:
    def _parse(self, argv):
        from tools.sdlc_verdict import _build_parser

        return _build_parser().parse_args(argv)

    _BASE = ["finalize", "--pr", "1", "--issue-number", "42", "--verdict", "APPROVED"]

    def test_new_spellings_parse(self):
        args = self._parse(self._BASE + ["--blocker-count", "3", "--tech-debt-count", "5"])
        assert args.blockers == 3
        assert args.tech_debt == 5

    def test_old_spellings_still_parse_to_the_same_dest(self):
        """The cross-machine propagation window: a machine that has not yet run
        /update still emits the old spellings against a merged sdlc-tool."""
        args = self._parse(self._BASE + ["--blockers", "3", "--tech-debt", "5"])
        assert args.blockers == 3
        assert args.tech_debt == 5

    def test_spellings_are_one_argument_not_two(self):
        """Aliases on a single `add_argument`, so the last one wins rather than
        two independent dests drifting apart."""
        args = self._parse(self._BASE + ["--blockers", "3", "--blocker-count", "9"])
        assert args.blockers == 9

    def test_zero_survives_the_rename_and_is_not_conflated_with_absent(self):
        """`0` means 'assessed, none found'; absent means 'not assessed'. The
        `default=None` distinction must survive (record_verdict writes the key
        only when the value is not None)."""
        assert self._parse(self._BASE + ["--blocker-count", "0"]).blockers == 0
        assert self._parse(self._BASE).blockers is None
        assert self._parse(self._BASE).tech_debt is None

    def test_prose_is_still_a_loud_failure_never_a_derived_count(self):
        """Deliberately unchanged: a silently-wrong count corrupts the ledger,
        which is strictly worse than the loud failure."""
        with pytest.raises(SystemExit):
            self._parse(self._BASE + ["--blocker-count", "1) mkdocs build --strict fails"])

    def test_help_text_says_count_and_says_not_findings_text(self):
        import contextlib
        import io

        from tools.sdlc_verdict import _build_parser

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
            _build_parser().parse_args(["finalize", "--help"])
        # argparse hard-wraps help bodies, so compare against whitespace-
        # normalized text rather than the raw column layout.
        out = " ".join(buf.getvalue().split())
        assert "--blocker-count" in out and "--tech-debt-count" in out
        assert "--blockers" in out and "--tech-debt" in out
        assert "COUNT" in out
        assert "NOT the findings text" in out
