"""Unit tests for tools.sdlc_review_finalize (#2193).

Covers the atomic finalize write+verify path, the shared
check_review_persistence read-back, the read-only selfcheck path, the named
error taxonomy, idempotent trailer append, and fail-closed behavior on
gh/Redis errors. Mirrors the mock-at-the-lease-boundary conventions used in
tests/unit/test_sdlc_verdict.py and tests/unit/test_sdlc_stage_marker.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.sdlc_review_finalize import (
    ReviewFinalizeError,
    _cli_finalize,
    _cli_selfcheck,
    check_review_persistence,
    finalize,
)

_HEAD_SHA = "a" * 40


# ---------------------------------------------------------------------------
# check_review_persistence
# ---------------------------------------------------------------------------


class TestCheckReviewPersistence:
    def test_no_verdict_recorded_is_the_load_bearing_incident_case(self):
        """The exact state the skill left when it wrote nothing at all
        (failure #1 in the incident): no session/ledger record resolves a
        verdict. selfcheck must report ok:false, verdict_present:false."""
        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result == {
            "ok": False,
            "verdict_present": False,
            "trailer_matches_head": False,
            "marker_completed": False,
            "reason": "REVIEW_VERDICT_MISSING",
        }

    def test_verdict_present_but_empty_string_counts_as_missing(self):
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": ""}),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["verdict_present"] is False
        assert result["reason"] == "REVIEW_VERDICT_MISSING"
        assert result["ok"] is False

    def test_approved_verdict_no_trailer_at_all(self):
        """Failure #2 in the incident: verdict recorded, but no trailer."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["verdict_present"] is True
        assert result["trailer_matches_head"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"
        assert result["ok"] is False

    def test_approved_verdict_malformed_short_sha_trailer_does_not_false_match(self):
        """A short/non-hex SHA in the trailer must never false-match."""
        verdict = "APPROVED REVIEW_CONTEXT head_sha=deadbeef"  # 8 hex chars, not 40
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["trailer_matches_head"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"

    def test_approved_verdict_trailer_present_but_stale_head(self):
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={'b' * 40}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["trailer_matches_head"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"

    def test_approved_verdict_trailer_matches_but_marker_not_completed(self):
        """Failure #3 in the incident: verdict + trailer good, marker never set."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "in_progress"}
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["trailer_matches_head"] is True
        assert result["marker_completed"] is False
        assert result["reason"] == "REVIEW_MARKER_INCOMPLETE"
        assert result["ok"] is False

    def test_all_three_present_is_ok_true(self):
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "completed"}
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result == {
            "ok": True,
            "verdict_present": True,
            "trailer_matches_head": True,
            "marker_completed": True,
            "reason": None,
        }

    def test_non_approved_verdict_bypasses_trailer_and_marker_checks(self):
        """CHANGES REQUESTED legitimately has no trailer and leaves the
        marker in_progress -- must be ok:true the moment a verdict exists."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": "CHANGES REQUESTED"},
            ),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha") as mock_sha,
            patch("tools.sdlc_stage_query.query_stage_states") as mock_stages,
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is True
        assert result["verdict_present"] is True
        # Neither the head-SHA fetch nor the marker query is even needed.
        mock_sha.assert_not_called()
        mock_stages.assert_not_called()

    def test_fails_closed_on_resolve_issue_record_exception(self):
        """A Redis hiccup (or any unexpected error) must never read as a
        false pass -- ok stays False with a named reason."""
        with patch(
            "tools.sdlc_stage_query._resolve_issue_record",
            side_effect=RuntimeError("redis down"),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is False
        assert result["reason"] == "REVIEW_VERDICT_MISSING"

    def test_fails_closed_on_gh_error_computing_head_sha(self):
        """_fetch_pr_head_sha itself never raises (guards gh internally),
        but a downstream gh failure must still fail closed via None."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=None),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is False
        assert result["trailer_matches_head"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"


class TestFetchPrHeadSha:
    def test_gh_failure_returns_none_never_raises(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        with patch("subprocess.run", side_effect=OSError("gh not found")):
            assert _fetch_pr_head_sha(1) is None

    def test_gh_nonzero_exit_returns_none(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=1, stdout="", stderr="not found")
        with patch("subprocess.run", return_value=mock_proc):
            assert _fetch_pr_head_sha(1) is None

    def test_gh_success_strips_and_returns_sha(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=0, stdout=f"{_HEAD_SHA}\n", stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            assert _fetch_pr_head_sha(1) == _HEAD_SHA


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------


class TestFinalize:
    def _patch_lease_ok(self, target_repo="o/r"):
        return (
            patch(
                "tools._sdlc_utils.resolve_ledger_lease",
                return_value=(target_repo, None),
            ),
            patch("tools._sdlc_utils.revalidate_ledger_lease", return_value=True),
        )

    def test_rejects_empty_verdict_no_partial_write(self):
        with patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get:
            with pytest.raises(ReviewFinalizeError, match="REVIEW_VERDICT_MISSING"):
                finalize(pr=1, issue_number=42, verdict="", run_id="run-1")
        mock_get.assert_not_called()

    def test_rejects_none_verdict_no_partial_write(self):
        with patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get:
            with pytest.raises(ReviewFinalizeError, match="REVIEW_VERDICT_MISSING"):
                finalize(pr=1, issue_number=42, verdict=None, run_id="run-1")
        mock_get.assert_not_called()

    def test_rejects_whitespace_only_verdict_no_partial_write(self):
        with patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get:
            with pytest.raises(ReviewFinalizeError, match="REVIEW_VERDICT_MISSING"):
                finalize(pr=1, issue_number=42, verdict="   ", run_id="run-1")
        mock_get.assert_not_called()

    def test_missing_run_id_refuses_before_any_lease_call(self):
        with patch("tools._sdlc_utils.resolve_ledger_lease") as mock_lease:
            with pytest.raises(ReviewFinalizeError, match="LEASE_ABSENT"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id=None)
        mock_lease.assert_not_called()

    def test_foreign_lease_raises_issue_locked(self):
        with patch(
            "tools._sdlc_utils.resolve_ledger_lease",
            return_value=(None, {"reason": "ISSUE_LOCKED", "owner_run_id": "other"}),
        ):
            with pytest.raises(ReviewFinalizeError, match="ISSUE_LOCKED"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

    def test_unheld_lease_raises_lease_absent(self):
        with patch(
            "tools._sdlc_utils.resolve_ledger_lease",
            return_value=(None, {"reason": "LEASE_ABSENT"}),
        ):
            with pytest.raises(ReviewFinalizeError, match="LEASE_ABSENT"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

    def test_missing_target_repo_raises_and_never_writes(self):
        with (
            patch("tools._sdlc_utils.resolve_ledger_lease", return_value=(None, None)),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get,
        ):
            with pytest.raises(ReviewFinalizeError, match="TARGET_REPO_MISSING"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")
        mock_get.assert_not_called()

    def test_gh_failure_fails_closed_never_records_trailer_less_verdict(self):
        """Risk 2: gh unavailable must never let a trailer-less verdict record."""
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=None),
            patch("tools.sdlc_verdict.record_verdict") as mock_record,
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_TRAILER_MISSING"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")
        mock_record.assert_not_called()

    def test_lease_lost_between_resolve_and_write_refuses(self):
        with (
            patch("tools._sdlc_utils.resolve_ledger_lease", return_value=("o/r", None)),
            patch("tools._sdlc_utils.revalidate_ledger_lease", return_value=False),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("tools.sdlc_verdict.record_verdict") as mock_record,
        ):
            with pytest.raises(ReviewFinalizeError, match="ISSUE_LOCKED"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")
        mock_record.assert_not_called()

    def test_record_verdict_write_failure_raises_verdict_missing(self):
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch("tools.sdlc_verdict.record_verdict", return_value={}),
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_VERDICT_MISSING"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

    def test_marker_write_failure_raises_named_marker_error(self):
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch(
                "tools.sdlc_verdict.record_verdict",
                return_value={"verdict": f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"},
            ),
            patch(
                "tools.sdlc_stage_marker.write_marker",
                return_value=(
                    {"error": "review_artifact_missing", "reason": "REVIEW_ARTIFACT_MISSING"},
                    1,
                ),
            ),
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_ARTIFACT_MISSING"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

    def test_readback_failure_after_writes_raises_named_error(self):
        """Even if record+marker writes report success, a failed readback
        (e.g. the marker write silently no-op'd) must still refuse loudly."""
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch(
                "tools.sdlc_verdict.record_verdict",
                return_value={"verdict": f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"},
            ),
            patch(
                "tools.sdlc_stage_marker.write_marker",
                return_value=({"stage": "REVIEW", "status": "completed"}, 0),
            ),
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={
                    "ok": False,
                    "verdict_present": True,
                    "trailer_matches_head": True,
                    "marker_completed": False,
                    "reason": "REVIEW_MARKER_INCOMPLETE",
                },
            ),
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_MARKER_INCOMPLETE"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

    def test_approved_happy_path_writes_marker_and_returns_ok_result(self):
        lease_ok, revalidate_ok = self._patch_lease_ok()
        trailered = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch(
                "tools.sdlc_verdict.record_verdict", return_value={"verdict": trailered}
            ) as mock_record,
            patch(
                "tools.sdlc_stage_marker.write_marker",
                return_value=({"stage": "REVIEW", "status": "completed"}, 0),
            ) as mock_marker,
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={
                    "ok": True,
                    "verdict_present": True,
                    "trailer_matches_head": True,
                    "marker_completed": True,
                    "reason": None,
                },
            ),
        ):
            result = finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")

        assert result["ok"] is True
        # The trailer was appended once, idempotently, into the recorded call.
        assert mock_record.call_args.kwargs["verdict"] == trailered
        mock_marker.assert_called_once()
        assert mock_marker.call_args.kwargs["status"] == "completed"

    def test_idempotent_trailer_append_when_already_present(self):
        """A verdict string that already carries the trailer must not get a
        second trailer appended."""
        already_trailered = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch(
                "tools.sdlc_verdict.record_verdict", return_value={"verdict": already_trailered}
            ) as mock_record,
            patch("tools.sdlc_stage_marker.write_marker", return_value=({}, 0)),
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={"ok": True, "reason": None},
            ),
        ):
            finalize(pr=1, issue_number=42, verdict=already_trailered, run_id="run-1")

        written_verdict = mock_record.call_args.kwargs["verdict"]
        assert written_verdict == already_trailered
        assert written_verdict.count("REVIEW_CONTEXT") == 1

    def test_non_approved_verdict_skips_marker_write(self):
        """CHANGES REQUESTED must not attempt a REVIEW completed marker write."""
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch(
                "tools.sdlc_verdict.record_verdict",
                return_value={"verdict": f"CHANGES REQUESTED REVIEW_CONTEXT head_sha={_HEAD_SHA}"},
            ),
            patch("tools.sdlc_stage_marker.write_marker") as mock_marker,
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={"ok": True, "reason": None},
            ),
        ):
            result = finalize(pr=1, issue_number=42, verdict="CHANGES REQUESTED", run_id="run-1")

        mock_marker.assert_not_called()
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# CLI entry points
# ---------------------------------------------------------------------------


class TestCliEntryPoints:
    def _args(self, **kw):
        from types import SimpleNamespace

        base = dict(
            pr=1,
            issue_number=42,
            verdict="APPROVED",
            blockers=None,
            tech_debt=None,
            run_id="run-1",
        )
        base.update(kw)
        return SimpleNamespace(**base)

    def test_cli_finalize_delegates_to_finalize_and_propagates_error(self):
        with patch(
            "tools.sdlc_review_finalize.finalize",
            side_effect=ReviewFinalizeError("REVIEW_TRAILER_MISSING: nope"),
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_TRAILER_MISSING"):
                _cli_finalize(self._args())

    def test_cli_finalize_delegates_to_finalize_success(self):
        with patch(
            "tools.sdlc_review_finalize.finalize", return_value={"ok": True}
        ) as mock_finalize:
            result = _cli_finalize(self._args())

        assert result == {"ok": True}
        mock_finalize.assert_called_once_with(
            pr=1,
            issue_number=42,
            verdict="APPROVED",
            run_id="run-1",
            blockers=None,
            tech_debt=None,
        )

    def test_cli_selfcheck_never_raises_and_returns_check_result(self):
        args = self._args()
        with patch(
            "tools.sdlc_review_finalize.check_review_persistence",
            return_value={"ok": False, "reason": "REVIEW_VERDICT_MISSING"},
        ) as mock_check:
            result = _cli_selfcheck(args)

        assert result == {"ok": False, "reason": "REVIEW_VERDICT_MISSING"}
        mock_check.assert_called_once_with(1, 42)


# ---------------------------------------------------------------------------
# sdlc_verdict.main() subparser registration + full CLI round-trip
# ---------------------------------------------------------------------------


class TestSdlcVerdictMainWiring:
    def test_finalize_and_selfcheck_subparsers_are_registered(self, capsys):

        from tools.sdlc_verdict import main

        with patch("sys.argv", ["sdlc-verdict", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert "finalize" in out
        assert "selfcheck" in out

    def test_finalize_help_exits_0(self, capsys):
        from tools.sdlc_verdict import main

        with patch("sys.argv", ["sdlc-verdict", "finalize", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_selfcheck_help_exits_0(self, capsys):
        from tools.sdlc_verdict import main

        with patch("sys.argv", ["sdlc-verdict", "selfcheck", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0

    def test_main_finalize_named_error_exits_1_and_prints_to_stderr(self, capsys):
        from tools.sdlc_verdict import main

        argv = [
            "sdlc-verdict",
            "finalize",
            "--pr",
            "1",
            "--issue-number",
            "42",
            "--verdict",
            "APPROVED",
            "--run-id",
            "run-1",
        ]
        with (
            patch("sys.argv", argv),
            patch(
                "tools.sdlc_review_finalize.finalize",
                side_effect=ReviewFinalizeError("REVIEW_TRAILER_MISSING: no head sha"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "REVIEW_TRAILER_MISSING" in captured.err

    def test_main_selfcheck_always_exits_0_ok_field_carries_verdict(self, capsys):
        """selfcheck's exit code must never encode failure -- only the JSON
        `ok` field does, mirroring stage-query/verdict-get's read-only
        contract."""
        from tools.sdlc_verdict import main

        argv = ["sdlc-verdict", "selfcheck", "--pr", "1", "--issue-number", "42"]
        with (
            patch("sys.argv", argv),
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={
                    "ok": False,
                    "verdict_present": False,
                    "trailer_matches_head": False,
                    "marker_completed": False,
                    "reason": "REVIEW_VERDICT_MISSING",
                },
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert '"ok": false' in captured.out
        assert "REVIEW_VERDICT_MISSING" in captured.out
