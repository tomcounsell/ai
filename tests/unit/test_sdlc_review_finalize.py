"""Unit tests for tools.sdlc_review_finalize (#2193).

Covers the atomic finalize write+verify path, the shared
check_review_persistence read-back, the read-only selfcheck path, the named
error taxonomy, the #2769 head_sha field/verdict-token split (with its
permanent legacy-trailer read fallback), and fail-closed behavior on
gh/Redis errors. Mirrors the mock-at-the-lease-boundary conventions used in
tests/unit/test_sdlc_verdict.py and tests/unit/test_sdlc_stage_marker.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.sdlc_review_finalize import (
    HeadShaResolutionError,
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
    @pytest.fixture(autouse=True)
    def _stub_repo_resolution(self):
        """Keep the head-SHA repo resolution hermetic (#2377 Mode 1) — without
        this, check_review_persistence would peek a live lock / shell out to
        `gh repo view` in the test's cwd. Individual tests can re-patch it."""
        with patch(
            "tools.sdlc_review_finalize.resolve_target_repo_for_read",
            return_value="o/r",
        ):
            yield

    def test_no_verdict_recorded_is_the_load_bearing_incident_case(self):
        """The exact state the skill left when it wrote nothing at all
        (failure #1 in the incident): no session/ledger record resolves a
        verdict. selfcheck must report ok:false, verdict_present:false."""
        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result == {
            "ok": False,
            "verdict_present": False,
            "approved": False,
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
            # Issue #2451: the >=1-ok-write gate. Pass a run_id AND a positive
            # ok-write count so the healthy APPROVED path still reads ok:True.
            patch("tools._sdlc_marker_telemetry.marker_ok_write_count", return_value=1),
        ):
            result = check_review_persistence(pr=1, issue_number=42, run_id="run-1")

        assert result == {
            "ok": True,
            "verdict_present": True,
            "approved": True,
            "trailer_matches_head": True,
            "marker_completed": True,
            "reason": None,
        }

    def test_zero_ok_writes_fails_no_confirmed_marker_write(self):
        """Issue #2451: an APPROVED run whose trail was reconstructed by retry
        with ZERO confirmed ok marker writes fails selfcheck loud -- closing
        the 'degraded ledger reports success' gap (#2439)."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "completed"}
            ),
            patch("tools._sdlc_marker_telemetry.marker_ok_write_count", return_value=0),
        ):
            result = check_review_persistence(pr=1, issue_number=42, run_id="run-1")

        assert result["ok"] is False
        assert result["reason"] == "NO_CONFIRMED_MARKER_WRITE"
        # The earlier probes all passed -- only the ok-write gate failed.
        assert result["verdict_present"] is True
        assert result["trailer_matches_head"] is True
        assert result["marker_completed"] is True

    def test_selfcheck_no_run_id_resolves_lease_owner_for_ok_write_gate(self):
        """The read-only selfcheck path has no run_id: it resolves the current
        lease owner via peek, then checks that run's ok-write count."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        peek = MagicMock()
        peek.owner_run_id = "lease-owner-run"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "completed"}
            ),
            patch("models.session_lifecycle.touch_issue_lock", return_value=peek),
            patch("tools._sdlc_marker_telemetry.marker_ok_write_count") as mock_count,
        ):
            mock_count.return_value = 2
            result = check_review_persistence(pr=1, issue_number=42)  # no run_id

        assert result["ok"] is True
        # The gate was checked against the resolved lease-owner run_id.
        assert mock_count.call_args.args[1] == "lease-owner-run"

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
        """A head-SHA resolution failure (now a raised HeadShaResolutionError,
        #2377/#2394) must still fail closed on the read path -- reported as
        REVIEW_TRAILER_MISSING, never re-raised (selfcheck stays exit-0)."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch(
                "tools.sdlc_review_finalize._fetch_pr_head_sha",
                side_effect=HeadShaResolutionError("gh exploded"),
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is False
        assert result["trailer_matches_head"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"

    def test_head_sha_lookup_threads_resolved_target_repo(self):
        """#2377 Mode 1: the read-path head-SHA lookup must target the resolved
        repo (lease-first), not the tool's forced ~/src/ai cwd."""
        verdict = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict}),
            patch(
                "tools.sdlc_review_finalize.resolve_target_repo_for_read",
                return_value="yudame/psyoptimal",
            ),
            patch(
                "tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA
            ) as mock_sha,
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "completed"}
            ),
            # Issue #2451 >=1-ok-write gate: satisfy it so this repo-threading
            # assertion still reaches ok:True.
            patch("tools._sdlc_marker_telemetry.marker_ok_write_count", return_value=1),
        ):
            result = check_review_persistence(pr=669, issue_number=665, run_id="run-1")

        assert result["ok"] is True
        assert mock_sha.call_args.kwargs.get("repo") == "yudame/psyoptimal"


class TestFetchPrHeadSha:
    def test_gh_missing_raises_named_error(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        with patch("subprocess.run", side_effect=OSError("gh not found")):
            with pytest.raises(HeadShaResolutionError, match="head SHA"):
                _fetch_pr_head_sha(1)

    def test_all_sources_nonzero_exit_raises_named_error(self):
        """#2404: with resolution git-first + gh fallback, a nonzero exit on
        every source leaves the head SHA unresolvable -> fail LOUD (raises),
        never a falsy return."""
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=1, stdout="", stderr="could not resolve to a PR")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(HeadShaResolutionError, match="head SHA"):
                _fetch_pr_head_sha(1)

    def test_all_sources_empty_output_raises_named_error(self):
        """#2404: both git ls-remote and gh return zero-exit-but-no-SHA -> the
        head SHA is unresolvable -> fail LOUD."""
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=0, stdout="   \n", stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(HeadShaResolutionError, match="head SHA"):
                _fetch_pr_head_sha(1)

    def test_gh_success_strips_and_returns_sha(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=0, stdout=f"{_HEAD_SHA}\n", stderr="")
        with patch("subprocess.run", return_value=mock_proc):
            assert _fetch_pr_head_sha(1) == _HEAD_SHA

    def test_unscoped_call_omits_repo_flag(self):
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=0, stdout=f"{_HEAD_SHA}\n", stderr="")
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            _fetch_pr_head_sha(1)
        cmd = mock_run.call_args.args[0]
        assert "--repo" not in cmd

    def test_repo_slug_is_threaded_as_repo_flag(self):
        """#2377 Mode 1: a resolved slug must be threaded as `gh --repo <slug>`
        so the lookup targets the right repo regardless of cwd."""
        from tools.sdlc_review_finalize import _fetch_pr_head_sha

        mock_proc = MagicMock(returncode=0, stdout=f"{_HEAD_SHA}\n", stderr="")
        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            _fetch_pr_head_sha(669, repo="yudame/psyoptimal")
        cmd = mock_run.call_args.args[0]
        assert "--repo" in cmd
        assert cmd[cmd.index("--repo") + 1] == "yudame/psyoptimal"


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
        """Risk 2 + fail-loud (#2394): an unresolvable head SHA (now a raised
        HeadShaResolutionError) must never let a trailer-less verdict record,
        and must surface as the named REVIEW_TRAILER_MISSING carrying the
        concrete gh cause -- not a silent stall."""
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch(
                "tools.sdlc_review_finalize._fetch_pr_head_sha",
                side_effect=HeadShaResolutionError("`gh pr view` exited 1 for PR #1: no such PR"),
            ),
            patch("tools.sdlc_verdict.record_verdict") as mock_record,
        ):
            with pytest.raises(ReviewFinalizeError, match="REVIEW_TRAILER_MISSING.*no such PR"):
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")
        mock_record.assert_not_called()

    def test_finalize_threads_lease_target_repo_into_head_sha_lookup(self):
        """#2377 Mode 1: finalize resolves the head SHA against the lease's
        pinned target-repo slug, not the tool's forced ~/src/ai cwd."""
        lease_ok, revalidate_ok = self._patch_lease_ok(target_repo="yudame/psyoptimal")
        trailered = f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}"
        with (
            lease_ok,
            revalidate_ok,
            patch(
                "tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA
            ) as mock_sha,
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch("tools.sdlc_verdict.record_verdict", return_value={"verdict": trailered}),
            patch("tools.sdlc_stage_marker.write_marker", return_value=({}, 0)),
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={"ok": True, "reason": None},
            ),
        ):
            finalize(pr=669, issue_number=665, verdict="APPROVED", run_id="run-1")

        assert mock_sha.call_args.kwargs.get("repo") == "yudame/psyoptimal"

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

    def test_finalize_threads_its_pr_argument_into_the_marker_write(self):
        """``--pr`` must reach the marker's artifact probe.

        Without it the probe re-derives a PR number from closing keywords
        alone, so a PR that says ``Refs #N`` resolves to nothing and finalize
        dies with ``REVIEW_ARTIFACT_MISSING`` -- error text that blames a
        missing artifact when the real failure is PR resolution. The verdict is
        already durable at that point and the marker never lands, so the
        "re-running is idempotent" remedy in the message never converges.
        """
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
            patch("tools.sdlc_stage_marker.write_marker", return_value=({}, 0)) as mock_marker,
            patch(
                "tools.sdlc_review_finalize.check_review_persistence",
                return_value={"ok": True, "reason": None},
            ),
        ):
            finalize(pr=4242, issue_number=42, verdict="APPROVED", run_id="run-1")

        assert mock_marker.call_args.kwargs.get("pr") == 4242

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
        # #2769: the verdict token is recorded BARE and the head SHA rides in
        # its own `head_sha=` kwarg. Concatenating them was the bug -- the
        # writer's normalize_verdict mangled the trailer into the token.
        assert mock_record.call_args.kwargs["verdict"] == "APPROVED"
        assert mock_record.call_args.kwargs["head_sha"] == _HEAD_SHA
        assert "REVIEW_CONTEXT" not in mock_record.call_args.kwargs["verdict"]
        mock_marker.assert_called_once()
        assert mock_marker.call_args.kwargs["status"] == "completed"

    def test_already_trailered_input_is_split_into_bare_token_and_head_sha(self):
        """#2769: an already-trailered input is STRIPPED to the bare verdict
        token and its SHA lands in `head_sha=` exactly once -- never stored in
        both places, and never left inside the verdict string."""
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
        assert written_verdict == "APPROVED"
        assert "REVIEW_CONTEXT" not in written_verdict
        assert mock_record.call_args.kwargs["head_sha"] == _HEAD_SHA

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


# ---------------------------------------------------------------------------
# Closed verdict vocabulary (#2548)
# ---------------------------------------------------------------------------


class TestRecognizedVerdictVocabulary:
    """An off-vocabulary verdict must never claim the non-APPROVED exemption.

    The incident: `finalize` was called with "APPROVE WITH COMMENTS (0
    BLOCKERS; ALL FINDINGS ADDRESSED)". `"APPROVED" in "APPROVE WITH
    COMMENTS"` is False, so the string fell through to the non-APPROVED
    branch, which by contract skips both the head_sha trailer and the REVIEW
    completion marker. The readback then reported exactly:

        {"ok": true, "verdict_present": true, "trailer_matches_head": false,
         "marker_completed": false, "reason": null}

    -- success on the field a caller branches on, with the REVIEW marker never
    written and the router left re-dispatching REVIEW forever.
    """

    @pytest.fixture(autouse=True)
    def _stub_repo_resolution(self):
        with patch(
            "tools.sdlc_review_finalize.resolve_target_repo_for_read",
            return_value="o/r",
        ):
            yield

    _INCIDENT_VERDICT = (
        "APPROVE WITH COMMENTS (0 BLOCKERS; ALL FINDINGS ADDRESSED) "
        f"REVIEW_CONTEXT head_sha={_HEAD_SHA}"
    )

    def test_incident_verdict_is_not_approved_under_substring_match(self):
        """Guards the premise: the bug is a substring miss, not a
        normalization bug. normalize_verdict is idempotent and preserves the
        text; "APPROVE" is simply not "APPROVED"."""
        from agent.sdlc_router import normalize_verdict

        normalized = normalize_verdict(self._INCIDENT_VERDICT)
        assert "APPROVED" not in normalized
        assert normalize_verdict(normalized) == normalized

    def test_selfcheck_refuses_off_vocabulary_verdict_instead_of_ok_true(self):
        """The read path: the exact recorded incident text must now fail
        closed with a named reason, not report ok:true."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": self._INCIDENT_VERDICT},
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is False
        assert result["reason"] == "REVIEW_VERDICT_UNRECOGNIZED"
        assert result["verdict_present"] is True
        assert result["approved"] is False

    def test_finalize_refuses_off_vocabulary_verdict_before_any_write(self):
        """The write path: refusal happens ahead of the lease resolve, so no
        verdict record and no marker are written."""
        with (
            patch("tools._sdlc_utils.resolve_ledger_lease") as mock_lease,
            patch("tools.sdlc_verdict.record_verdict") as mock_record,
            patch("tools.sdlc_stage_marker.write_marker") as mock_marker,
            pytest.raises(ReviewFinalizeError) as exc_info,
        ):
            finalize(
                pr=1,
                issue_number=42,
                verdict="APPROVE WITH COMMENTS",
                run_id="run-1",
            )

        assert "REVIEW_VERDICT_UNRECOGNIZED" in str(exc_info.value)
        mock_lease.assert_not_called()
        mock_record.assert_not_called()
        mock_marker.assert_not_called()

    @pytest.mark.parametrize(
        "verdict",
        [
            "APPROVED",
            "APPROVED (0 BLOCKERS)",
            "CHANGES REQUESTED",
            "changes_requested",
            "BLOCKED_ON_CONFLICT",
            "PR_CLOSED",
        ],
    )
    def test_every_contract_verdict_is_still_recognized(self, verdict):
        """Anti-criterion: the gate must not narrow the sanctioned vocabulary.
        Decoration around a token, underscore forms, and lowercase all pass."""
        from agent.sdlc_router import normalize_verdict
        from tools.sdlc_review_finalize import _verdict_is_recognized

        assert _verdict_is_recognized(normalize_verdict(verdict)) is True

    def test_recognized_non_approved_verdict_still_takes_the_exemption(self):
        """Anti-criterion: the fix must not break the legitimate non-APPROVED
        path. CHANGES REQUESTED carries no trailer and leaves the marker
        in_progress by contract, and that is still ok:true -- now with
        approved:false saying why the other two booleans are false."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": "CHANGES REQUESTED (2 BLOCKERS)"},
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42)

        assert result["ok"] is True
        assert result["approved"] is False
        assert result["reason"] is None
        assert result["trailer_matches_head"] is False
        assert result["marker_completed"] is False

    def test_reason_is_populated_on_every_not_ok_return(self):
        """Acceptance criterion: `reason` is never left null when ok is
        false."""
        cases = [
            (None, {"verdict": ""}),
            (object(), {"verdict": "APPROVE WITH COMMENTS"}),
        ]
        for record, verdict_record in cases:
            with (
                patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
                patch("tools.sdlc_verdict.get_verdict", return_value=verdict_record),
            ):
                result = check_review_persistence(pr=1, issue_number=42)
            assert result["ok"] is False
            assert result["reason"] is not None


# ---------------------------------------------------------------------------
# Issue #2769: check_review_persistence reads the head SHA through
# `head_sha_of_record` -- field first, legacy in-token trailer second.
# The legacy fallback is PERMANENT: pre-split ledgers are never migrated.
# ---------------------------------------------------------------------------


class TestCheckReviewPersistenceHeadShaShapes:
    @pytest.fixture(autouse=True)
    def _stub_repo_resolution(self):
        with patch(
            "tools.sdlc_review_finalize.resolve_target_repo_for_read",
            return_value="o/r",
        ):
            yield

    def _run(self, verdict_record):
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value=verdict_record),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_stage_query.query_stage_states", return_value={"REVIEW": "completed"}
            ),
            patch("tools._sdlc_marker_telemetry.marker_ok_write_count", return_value=1),
        ):
            return check_review_persistence(pr=1, issue_number=42, run_id="run-1")

    def test_new_field_shape_passes_every_gate(self):
        result = self._run({"verdict": "APPROVED", "head_sha": _HEAD_SHA})
        assert result["ok"] is True
        assert result["trailer_matches_head"] is True

    def test_legacy_mangled_shape_still_passes_every_gate(self):
        """The pre-split record: normalize_verdict mangled the trailer into the
        token. It must keep parsing forever -- no migration exists."""
        result = self._run({"verdict": f"APPROVED REVIEW CONTEXT HEAD SHA={_HEAD_SHA.upper()}"})
        assert result["ok"] is True
        assert result["trailer_matches_head"] is True

    def test_field_wins_over_a_disagreeing_legacy_trailer(self):
        result = self._run(
            {
                "verdict": f"APPROVED REVIEW_CONTEXT head_sha={'b' * 40}",
                "head_sha": _HEAD_SHA,
            }
        )
        assert result["ok"] is True

    def test_stale_field_fails_closed_even_with_a_fresh_legacy_trailer(self):
        result = self._run(
            {
                "verdict": f"APPROVED REVIEW_CONTEXT head_sha={_HEAD_SHA}",
                "head_sha": "b" * 40,
            }
        )
        assert result["ok"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"

    def test_read_helper_raising_still_fails_closed_with_a_preserved_reason(self):
        """The outer `except Exception` is a deliberate fail-closed catch: an
        exploding read helper must never read as a pass."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch(
                "tools.sdlc_review_finalize.head_sha_of_record",
                side_effect=RuntimeError("ledger exploded"),
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42, run_id="run-1")

        assert result["ok"] is False
        assert result["reason"] == "REVIEW_VERDICT_MISSING"

    def test_head_sha_resolution_failure_fails_closed_with_its_own_reason(self):
        """`_fetch_pr_head_sha` raising is a HANDLED branch with its own explicit
        reason -- distinct from the `except Exception` path covered above."""
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=object()),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": "APPROVED"},
            ),
            patch(
                "tools.sdlc_review_finalize._fetch_pr_head_sha",
                side_effect=HeadShaResolutionError("gh down"),
            ),
        ):
            result = check_review_persistence(pr=1, issue_number=42, run_id="run-1")
        assert result["ok"] is False
        assert result["reason"] == "REVIEW_TRAILER_MISSING"


# ---------------------------------------------------------------------------
# Issue #2740: the refusal must report what actually landed.
#
# `finalize` is a composite -- record_verdict commits, THEN write_marker runs.
# When the marker write is refused, `write_marker` is locally correct that IT
# wrote nothing, but it cannot see the verdict its caller already committed.
# On the observed instance (issue #2711 / PR #2728) a reviewing agent read
# "State NOT persisted", concluded finalize had lost its atomicity guarantee,
# and nearly filed a duplicate issue. A wrong message on a refusal path costs
# an investigation every time it fires.
# ---------------------------------------------------------------------------


class TestMarkerRefusalReportsWhatPersisted:
    def _refuse_marker(self, reason="STATE_MACHINE_REJECTED"):
        return patch(
            "tools.sdlc_stage_marker.write_marker",
            return_value=(
                {"error": reason.lower(), "reason": reason},
                1,
            ),
        )

    def _run_and_capture(self, reason="STATE_MACHINE_REJECTED"):
        lease_ok, revalidate_ok = self._patch_lease_ok()
        with (
            lease_ok,
            revalidate_ok,
            patch("tools.sdlc_review_finalize._fetch_pr_head_sha", return_value=_HEAD_SHA),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create", return_value=MagicMock()),
            patch("tools.sdlc_verdict.record_verdict", return_value={"verdict": "APPROVED"}),
            self._refuse_marker(reason),
        ):
            with pytest.raises(ReviewFinalizeError) as excinfo:
                finalize(pr=1, issue_number=42, verdict="APPROVED", run_id="run-1")
        return str(excinfo.value)

    _patch_lease_ok = TestFinalize._patch_lease_ok

    def test_message_does_not_claim_the_state_was_not_persisted(self):
        message = self._run_and_capture()
        assert "State NOT persisted" not in message
        assert "NOT persisted" not in message

    def test_message_states_the_verdict_did_persist(self):
        """AC2: a reader must be able to determine what landed WITHOUT running
        a separate stage-query."""
        message = self._run_and_capture()
        assert "ARE persisted" in message
        assert "verdict" in message.lower()

    def test_message_names_only_the_marker_as_missing(self):
        message = self._run_and_capture()
        assert "marker was not" in message.lower()

    def test_message_names_the_idempotent_rerun_remedy(self):
        """Risk 4: exactly ONE remedy, so the message stays skimmable."""
        message = self._run_and_capture()
        assert "idempotent" in message.lower()

    def test_named_reason_stays_the_prefix(self):
        """The /do-sdlc supervisor and existing tests match on the leading
        taxon -- the rewording must not move it."""
        for reason in ("STATE_MACHINE_REJECTED", "ISSUE_LOCKED", "REVIEW_MARKER_INCOMPLETE"):
            assert self._run_and_capture(reason).startswith(f"{reason}: ")


def test_no_module_in_tools_or_agent_claims_state_not_persisted():
    """#2740 AC3 / the Verification anti-criterion, as a test rather than a
    one-off grep: the overclaiming sentence is gone from all FOUR sites --
    three inside `write_marker` plus `main()`'s non-zero-exit wrapper.

    Scoped to TRACKED files via `git grep` (#2807): the assertion is about
    the source tree, so build artifacts must not count -- a stale pre-fix
    `.pyc` under `__pycache__` embeds the string literal and made the old
    `grep -r` fail a clean checkout. `git grep` also pins the tool to the
    repo's own index instead of whatever `grep` PATH resolves to (ugrep
    honors .gitignore; /usr/bin/grep does not), and shares the old exit
    contract: 1 means no match."""
    import pathlib
    import subprocess

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    hits = subprocess.run(
        ["git", "grep", "-In", "State NOT persisted", "--", "tools", "agent"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert hits.returncode == 1, f"overclaim survives at:\n{hits.stdout}"
