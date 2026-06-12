"""Unit tests for tools.sdlc_stage_query CLI tool.

Tests cover:
- query_stage_states with valid session data
- Graceful handling of missing sessions
- Graceful handling of malformed stage_states
- CLI argument parsing and output format
- Fallback to issue number lookup
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch  # noqa: F401 - patch used in tests below

# Resolve the repo root for subprocess cwd
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestQueryStageStates:
    """Tests for the query_stage_states function."""

    def test_returns_empty_dict_when_no_args(self):
        from tools.sdlc_stage_query import query_stage_states

        result = query_stage_states()
        assert result == {}

    def test_returns_empty_dict_when_session_not_found(self):
        from tools.sdlc_stage_query import query_stage_states

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=None):
            result = query_stage_states(session_id="nonexistent")
        assert result == {}

    def test_returns_stage_states_from_session(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "in_progress",
            "TEST": "pending",
            "PATCH": "pending",
            "REVIEW": "pending",
            "DOCS": "pending",
            "MERGE": "pending",
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "completed"
        assert result["BUILD"] == "in_progress"
        assert result["TEST"] == "pending"

    def test_filters_out_metadata_keys(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "_patch_cycle_count": 2,
            "_critique_cycle_count": 0,
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert "_patch_cycle_count" not in result
        assert "_critique_cycle_count" not in result
        assert result["ISSUE"] == "completed"

    def test_handles_malformed_json_gracefully(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = "not-valid-json"

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result == {}

    def test_handles_none_stage_states(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result == {}

    def test_issue_number_fallback(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {"ISSUE": "completed", "PLAN": "in_progress"}
        mock_session.stage_states = json.dumps(stages)

        with (
            patch("tools.sdlc_stage_query._find_session_by_id", return_value=None),
            patch("tools.sdlc_stage_query._find_session_by_issue", return_value=mock_session),
        ):
            result = query_stage_states(session_id="missing", issue_number=704)

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "in_progress"

    def test_handles_dict_stage_states(self):
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        mock_session.stage_states = {"ISSUE": "completed", "PLAN": "ready"}

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "ready"

    def test_only_returns_known_stages(self):
        """Verify unknown stage names are filtered out."""
        from tools.sdlc_stage_query import query_stage_states

        mock_session = MagicMock()
        stages = {
            "ISSUE": "completed",
            "UNKNOWN_STAGE": "completed",
            "BOGUS": "in_progress",
        }
        mock_session.stage_states = json.dumps(stages)

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            result = query_stage_states(session_id="test-session")

        assert "ISSUE" in result
        assert "UNKNOWN_STAGE" not in result
        assert "BOGUS" not in result


class TestFindSessionByIssue:
    """Tests for _find_session_by_issue."""

    def test_matches_issue_url_suffix(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/704"
        mock_session.session_type = "pm"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result == mock_session

    def test_returns_none_when_no_match(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/999"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result is None

    def test_read_converges_with_write_under_divergent_env(self, monkeypatch):
        """#1671/#1672: the read path resolves the same issue-scoped session a
        writer (with a divergent VALOR_SESSION_ID) targeted. The read path is
        env-independent — it goes straight through find_session_by_issue — so a
        divergent env var set on the reader has no effect."""
        from tools.sdlc_stage_query import _find_session_by_issue

        # A divergent env var must NOT affect the read resolution.
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = MagicMock()
        issue_session.issue_url = "https://github.com/tomcounsell/ai/issues/1672"
        issue_session.session_type = "pm"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [issue_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = _find_session_by_issue(1672)

        # The read resolves the issue session regardless of the env var.
        assert result is issue_session

    def test_handles_redis_exception_gracefully(self):
        from tools.sdlc_stage_query import _find_session_by_issue

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = ConnectionError("Redis down")

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = _find_session_by_issue(704)

        assert result is None


class TestFindSessionById:
    """Tests for _find_session_by_id."""

    def test_prefers_eng_session(self):
        from tools.sdlc_stage_query import _find_session_by_id

        eng_session = MagicMock()
        eng_session.session_type = "eng"
        teammate_session = MagicMock()
        teammate_session.session_type = "teammate"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [teammate_session, eng_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("test-session")

        assert result == eng_session

    def test_returns_first_session_when_no_eng(self):
        from tools.sdlc_stage_query import _find_session_by_id

        teammate_session = MagicMock()
        teammate_session.session_type = "teammate"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [teammate_session]

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("test-session")

        assert result == teammate_session

    def test_returns_none_for_empty_results(self):
        from tools.sdlc_stage_query import _find_session_by_id

        mock_as = MagicMock()
        mock_as.query.filter.return_value = []

        with patch("models.agent_session.AgentSession", mock_as):
            result = _find_session_by_id("nonexistent")

        assert result is None


class TestLookupPrNumber:
    """D4: _lookup_pr_number issue-search primary + branch-head fallback."""

    def test_issue_search_primary_path(self):
        from tools.sdlc_stage_query import _lookup_pr_number

        with patch("tools.sdlc_stage_query._gh_pr_list", return_value=55) as gh:
            assert _lookup_pr_number(145, slug="some_slug") == 55
        # First call is the issue-number search.
        first_args = gh.call_args_list[0].args[0]
        assert "--search" in first_args and "#145" in first_args

    def test_branch_head_fallback_when_issue_search_empty(self):
        from tools.sdlc_stage_query import _lookup_pr_number

        # Issue search returns None, branch-head returns 88.
        with patch("tools.sdlc_stage_query._gh_pr_list", side_effect=[None, 88]) as gh:
            assert _lookup_pr_number(145, slug="my_slug") == 88
        branch_args = gh.call_args_list[1].args[0]
        assert "--head" in branch_args and "session/my_slug" in branch_args

    def test_no_slug_no_branch_fallback(self):
        from tools.sdlc_stage_query import _lookup_pr_number

        # Only the issue search runs (returns None); no branch-head attempt.
        with patch("tools.sdlc_stage_query._gh_pr_list", side_effect=[None]) as gh:
            assert _lookup_pr_number(145, slug=None) is None
        assert gh.call_count == 1

    def test_gh_failure_returns_none(self):
        from tools.sdlc_stage_query import _gh_pr_list

        # subprocess raises -> None, never propagates.
        with patch("tools.sdlc_stage_query.subprocess.run", side_effect=OSError("boom")):
            assert _gh_pr_list(["--head", "session/x"]) is None


class TestCLIOutput:
    """Tests for CLI invocation and output format."""

    def test_no_args_returns_empty_json(self):
        # Strip session env vars: the tool falls back to VALOR_SESSION_ID /
        # AGENT_SESSION_ID when no args are given, so inheriting the parent
        # env would cause a real Redis query and a non-empty result when
        # this test runs inside an SDLC session.
        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_query", "--format", "legacy"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout.strip()) == {}

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_query", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--session-id" in result.stdout
        assert "--issue-number" in result.stdout


class TestEnrichedPayload:
    """Tests for the enriched ``query_enriched`` output."""

    def test_returns_stages_and_meta_keys(self):
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "_patch_cycle_count": 1,
                "_critique_cycle_count": 2,
                "_verdicts": {
                    "CRITIQUE": {"verdict": "NEEDS REVISION"},
                    "REVIEW": {"verdict": "APPROVED"},
                },
            }
        )
        mock_session.pr_number = 42

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert "stages" in result
        assert "_meta" in result
        assert result["stages"]["ISSUE"] == "completed"
        assert result["_meta"]["patch_cycle_count"] == 1
        assert result["_meta"]["critique_cycle_count"] == 2
        assert result["_meta"]["latest_critique_verdict"] == "NEEDS REVISION"
        assert result["_meta"]["latest_review_verdict"] == "APPROVED"
        assert result["_meta"]["pr_number"] == 42

    def test_pr_number_resolved_from_meta_key(self):
        """D4: _compute_meta resolves pr_number from the _pr_number meta key."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(
            {"ISSUE": "completed", "PLAN": "completed", "_pr_number": 777}
        )
        mock_session.pr_number = None  # no session attribute
        mock_session.slug = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            # gh lookup must NOT be needed — the meta key wins.
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None) as lookup:
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert result["_meta"]["pr_number"] == 777
        lookup.assert_not_called()

    def test_defaults_when_session_missing(self):
        from tools.sdlc_stage_query import query_enriched

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=None):
            with patch("tools.sdlc_stage_query._find_session_by_issue", return_value=None):
                result = query_enriched(session_id="missing")

        assert result["stages"] == {}
        assert result["_meta"]["patch_cycle_count"] == 0
        assert result["_meta"]["critique_cycle_count"] == 0
        assert result["_meta"]["latest_critique_verdict"] is None
        assert result["_meta"]["revision_applied"] is False
        assert result["_meta"]["pr_number"] is None
        assert result["_meta"]["same_stage_dispatch_count"] == 0
        assert result["_meta"]["last_dispatched_skill"] is None

    def test_legacy_flat_shape_preserved(self):
        """--format legacy returns the old flat shape."""
        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_stage_query",
                "--format",
                "legacy",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout.strip())
        assert parsed == {}  # no session → empty flat dict

    def test_default_json_shape_includes_stages_and_meta(self):
        """Default (no --format flag) returns the enriched shape."""
        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_query"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        assert result.returncode == 0
        parsed = json.loads(result.stdout.strip())
        assert "stages" in parsed
        assert "_meta" in parsed

    def test_enriched_meta_includes_pr_merge_state_and_ci_all_passing(self):
        """_meta includes pr_merge_state and ci_all_passing fields."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed"})
        mock_session.pr_number = 42

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch(
                "tools.sdlc_stage_query._fetch_pr_merge_state",
                return_value=("CLEAN", True),
            ):
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert "pr_merge_state" in result["_meta"]
        assert "ci_all_passing" in result["_meta"]
        assert result["_meta"]["pr_merge_state"] == "CLEAN"
        assert result["_meta"]["ci_all_passing"] is True

    def test_enriched_meta_defaults_pr_merge_state_to_none_on_gh_failure(self):
        """When gh CLI fails, pr_merge_state and ci_all_passing default to None."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed"})
        mock_session.pr_number = 42

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch(
                "tools.sdlc_stage_query._fetch_pr_merge_state",
                return_value=(None, None),
            ):
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert result["_meta"]["pr_merge_state"] is None
        assert result["_meta"]["ci_all_passing"] is None

    def test_default_meta_includes_pr_merge_state_and_ci_all_passing(self):
        """_default_meta always includes pr_merge_state and ci_all_passing keys."""
        from tools.sdlc_stage_query import _default_meta

        meta = _default_meta()
        assert "pr_merge_state" in meta
        assert "ci_all_passing" in meta
        assert meta["pr_merge_state"] is None
        assert meta["ci_all_passing"] is None

    def test_default_meta_includes_plan_revising_and_hash(self):
        """_default_meta includes plan_revising (False) and plan_hash_at_build_start (None)."""
        from tools.sdlc_stage_query import _default_meta

        meta = _default_meta()
        assert "plan_revising" in meta
        assert "plan_hash_at_build_start" in meta
        assert meta["plan_revising"] is False
        assert meta["plan_hash_at_build_start"] is None

    def test_compute_meta_plan_revising_defaults_false_when_absent(self):
        """_compute_meta surfaces plan_revising=False when _plan_revising key is absent."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed"})
        mock_session.pr_number = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                with patch(
                    "tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)
                ):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        result = query_enriched(session_id="sid")

        assert result["_meta"]["plan_revising"] is False

    def test_compute_meta_plan_revising_true_when_set(self):
        """_compute_meta surfaces plan_revising=True when _plan_revising=True in stage_states."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed", "_plan_revising": True})
        mock_session.pr_number = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                with patch(
                    "tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)
                ):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        result = query_enriched(session_id="sid")

        assert result["_meta"]["plan_revising"] is True

    def test_compute_meta_plan_hash_at_build_start_surfaced(self):
        """_compute_meta surfaces plan_hash_at_build_start from raw stage_states."""
        from tools.sdlc_stage_query import query_enriched

        test_hash = "abc123def456"
        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(
            {"ISSUE": "completed", "_plan_hash_at_build_start": test_hash}
        )
        mock_session.pr_number = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                with patch(
                    "tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)
                ):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        result = query_enriched(session_id="sid")

        assert result["_meta"]["plan_hash_at_build_start"] == test_hash

    def test_compute_meta_plan_hash_defaults_none_when_absent(self):
        """_compute_meta returns plan_hash_at_build_start=None when key is absent."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed"})
        mock_session.pr_number = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                with patch(
                    "tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)
                ):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        result = query_enriched(session_id="sid")

        assert result["_meta"]["plan_hash_at_build_start"] is None


class TestFetchPrMergeState:
    """Tests for the _fetch_pr_merge_state helper."""

    def test_returns_none_tuple_when_no_pr_number(self):
        from tools.sdlc_stage_query import _fetch_pr_merge_state

        result = _fetch_pr_merge_state(None)
        assert result == (None, None)

    def test_returns_none_tuple_on_gh_failure(self):
        from tools.sdlc_stage_query import _fetch_pr_merge_state

        with patch("tools.sdlc_stage_query.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
            result = _fetch_pr_merge_state(264)

        assert result == (None, None)

    def test_parses_clean_merge_state_and_passing_ci(self):
        import json as _json

        from tools.sdlc_stage_query import _fetch_pr_merge_state

        gh_output = _json.dumps(
            {
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [
                    {"conclusion": "SUCCESS"},
                    {"conclusion": "SUCCESS"},
                ],
            }
        )
        with patch("tools.sdlc_stage_query.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=gh_output)
            merge_state, ci_passing = _fetch_pr_merge_state(264)

        assert merge_state == "CLEAN"
        assert ci_passing is True

    def test_ci_all_passing_false_when_any_check_fails(self):
        import json as _json

        from tools.sdlc_stage_query import _fetch_pr_merge_state

        gh_output = _json.dumps(
            {
                "mergeStateStatus": "BLOCKED",
                "statusCheckRollup": [
                    {"conclusion": "SUCCESS"},
                    {"conclusion": "FAILURE"},
                ],
            }
        )
        with patch("tools.sdlc_stage_query.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=gh_output)
            merge_state, ci_passing = _fetch_pr_merge_state(264)

        assert merge_state == "BLOCKED"
        assert ci_passing is False

    def test_empty_status_check_rollup_means_ci_passing(self):
        """Empty statusCheckRollup (no required checks) -> ci_all_passing=True."""
        import json as _json

        from tools.sdlc_stage_query import _fetch_pr_merge_state

        gh_output = _json.dumps(
            {
                "mergeStateStatus": "CLEAN",
                "statusCheckRollup": [],
            }
        )
        with patch("tools.sdlc_stage_query.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout=gh_output)
            merge_state, ci_passing = _fetch_pr_merge_state(264)

        assert merge_state == "CLEAN"
        assert ci_passing is True

    def test_returns_none_tuple_on_exception(self):
        from tools.sdlc_stage_query import _fetch_pr_merge_state

        with patch("tools.sdlc_stage_query.subprocess.run", side_effect=OSError("not found")):
            result = _fetch_pr_merge_state(264)

        assert result == (None, None)

    def test_fetch_pr_merge_state_threads_repo(self):
        """When repo= is passed, gh pr view includes --repo <slug>."""
        import json as _json

        from tools.sdlc_stage_query import _fetch_pr_merge_state

        gh_output = _json.dumps({"mergeStateStatus": "CLEAN", "statusCheckRollup": []})
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = gh_output
        with patch("tools.sdlc_stage_query.subprocess.run", return_value=mock_proc) as mock_run:
            result = _fetch_pr_merge_state(42, repo="tomcounsell/popoto")
        cmd = mock_run.call_args[0][0]
        assert "--repo" in cmd
        assert "tomcounsell/popoto" in cmd
        assert result[0] == "CLEAN"


class TestResolveTargetRepo:
    """Tests for _resolve_target_repo in tools._sdlc_utils."""

    def test_gh_repo_env_short_circuits(self, monkeypatch):
        """GH_REPO set → return it directly, subprocess never called."""
        monkeypatch.setenv("GH_REPO", "tomcounsell/popoto")
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("subprocess.run") as mock_run:
            from tools._sdlc_utils import _resolve_target_repo

            result = _resolve_target_repo()
        assert result == "tomcounsell/popoto"
        mock_run.assert_not_called()

    def test_sdlc_target_repo_used_as_cwd_not_slug(self, monkeypatch):
        """SDLC_TARGET_REPO is a filesystem PATH used as cwd, never as --repo slug."""
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setenv("SDLC_TARGET_REPO", "/some/path")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "tomcounsell/popoto\n"
        with patch("tools._sdlc_utils.subprocess.run", return_value=mock_proc) as mock_run:
            from tools._sdlc_utils import _resolve_target_repo

            result = _resolve_target_repo()
        # Returned value is the slug from stdout
        assert result == "tomcounsell/popoto"
        # subprocess called with cwd="/some/path", NOT with "--repo /some/path"
        call_kwargs = mock_run.call_args
        cwd = call_kwargs.kwargs.get("cwd") or call_kwargs[1].get("cwd")
        assert cwd == "/some/path"
        # "/some/path" must NOT appear as a --repo value
        cmd = call_kwargs[0][0]
        assert "--repo" not in cmd or "/some/path" not in cmd

    def test_neither_env_uses_git_toplevel_as_cwd(self, monkeypatch):
        """Both envs unset → git toplevel used as cwd for gh repo view."""
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "tomcounsell/ai\n"
        with patch("tools._sdlc_utils.subprocess.run", return_value=mock_proc) as mock_run:
            with patch("tools._sdlc_utils._git_toplevel", return_value="/users/tom/src/ai"):
                from tools._sdlc_utils import _resolve_target_repo

                result = _resolve_target_repo()
        assert result == "tomcounsell/ai"
        call_kwargs = mock_run.call_args
        cwd = call_kwargs.kwargs.get("cwd") or call_kwargs[1].get("cwd")
        assert cwd == "/users/tom/src/ai"

    def test_returns_none_on_gh_failure_with_warning(self, monkeypatch, caplog):
        """gh repo view failure → returns None, emits logger.warning."""
        import logging

        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setenv("SDLC_TARGET_REPO", "/some/path")
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        with patch("tools._sdlc_utils.subprocess.run", return_value=mock_proc):
            with caplog.at_level(logging.WARNING):
                from tools._sdlc_utils import _resolve_target_repo

                result = _resolve_target_repo()
        assert result is None
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_empty_sdlc_target_falls_through_to_git_toplevel(self, monkeypatch):
        """SDLC_TARGET_REPO='' (empty) falls through to _git_toplevel, never cwd=''."""
        monkeypatch.delenv("GH_REPO", raising=False)
        monkeypatch.setenv("SDLC_TARGET_REPO", "")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "tomcounsell/ai\n"
        with patch("tools._sdlc_utils.subprocess.run", return_value=mock_proc) as mock_run:
            with patch("tools._sdlc_utils._git_toplevel", return_value="/users/tom/src/ai"):
                from tools._sdlc_utils import _resolve_target_repo

                result = _resolve_target_repo()
        # subprocess called with cwd from git_toplevel, not cwd=""
        call_kwargs = mock_run.call_args
        cwd = call_kwargs.kwargs.get("cwd") or call_kwargs[1].get("cwd")
        assert cwd != ""
        assert result == "tomcounsell/ai"

    def test_compute_meta_resolves_repo_once(self, monkeypatch):
        """_resolve_target_repo called exactly once per _compute_meta invocation."""
        call_count = []

        def fake_resolve():
            call_count.append(1)
            return "tomcounsell/ai"

        with patch("tools.sdlc_stage_query._resolve_target_repo", side_effect=fake_resolve):
            with patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)):
                with patch("tools.sdlc_stage_query._lookup_pr_number", return_value=None):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        from tools.sdlc_stage_query import _compute_meta

                        mock_session = MagicMock()
                        mock_session.pr_number = None
                        mock_session.slug = None
                        _compute_meta({}, mock_session, None)
        assert len(call_count) == 1, (
            f"_resolve_target_repo called {len(call_count)} times, expected 1"
        )
