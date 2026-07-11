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
from pathlib import Path
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
        mock_session.session_type = "eng"

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
        issue_session.session_type = "eng"

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
    """D4: _lookup_pr issue-search primary + branch-head fallback."""

    def test_issue_search_primary_path(self):
        from tools.sdlc_stage_query import _lookup_pr

        # The validated issue-search helper returns a PR whose body references
        # the issue; the branch-head fallback must not be consulted.
        with (
            patch("tools.sdlc_stage_query._gh_pr_search_issue_ref", return_value=55) as search,
            patch("tools.sdlc_stage_query._gh_pr_list") as gh,
        ):
            assert _lookup_pr(145, slug="some_slug") == 55
        search.assert_called_once()
        assert search.call_args.args[0] == 145
        gh.assert_not_called()

    def test_branch_head_fallback_when_issue_search_empty(self):
        from tools.sdlc_stage_query import _lookup_pr

        # Validated issue search returns None; branch-head returns 88.
        with (
            patch("tools.sdlc_stage_query._gh_pr_search_issue_ref", return_value=None),
            patch("tools.sdlc_stage_query._gh_pr_list", return_value=88) as gh,
        ):
            assert _lookup_pr(145, slug="my_slug") == 88
        branch_args = gh.call_args_list[0].args[0]
        assert "--head" in branch_args and "session/my_slug" in branch_args

    def test_no_slug_no_branch_fallback(self):
        from tools.sdlc_stage_query import _lookup_pr

        # Only the validated issue search runs (returns None); no branch-head attempt.
        with (
            patch("tools.sdlc_stage_query._gh_pr_search_issue_ref", return_value=None) as search,
            patch("tools.sdlc_stage_query._gh_pr_list") as gh,
        ):
            assert _lookup_pr(145, slug=None) is None
        search.assert_called_once()
        gh.assert_not_called()

    def test_gh_failure_returns_none(self):
        from tools.sdlc_stage_query import _gh_pr_list

        # subprocess raises -> None, never propagates.
        with patch("tools.sdlc_stage_query.subprocess.run", side_effect=OSError("boom")):
            assert _gh_pr_list(["--head", "session/x"]) is None

    def test_issue_1987_false_match_returns_none(self):
        """Regression #1987: a fuzzy hit whose body references a *different*
        issue must not be trusted; with no slug fallback the result is None."""
        from tools.sdlc_stage_query import _lookup_pr

        # Search surfaces PR #1984 (body: "Closes #1967") for issue 1950.
        proc = MagicMock(
            returncode=0, stdout=json.dumps([{"number": 1984, "body": "Closes #1967"}])
        )
        with patch("tools.sdlc_stage_query.subprocess.run", return_value=proc):
            assert _lookup_pr(1950, slug=None) is None

    def test_issue_search_validated_hit_returned(self):
        from tools.sdlc_stage_query import _gh_pr_search_issue_ref

        proc = MagicMock(returncode=0, stdout=json.dumps([{"number": 77, "body": "Closes #1950"}]))
        with patch("tools.sdlc_stage_query.subprocess.run", return_value=proc):
            assert _gh_pr_search_issue_ref(1950) == 77

    def test_issue_search_returns_first_validating_candidate(self):
        from tools.sdlc_stage_query import _gh_pr_search_issue_ref

        # Only the second candidate's body references the issue.
        proc = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"number": 10, "body": "Closes #1967"},
                    {"number": 20, "body": "Fixes #1950"},
                ]
            ),
        )
        with patch("tools.sdlc_stage_query.subprocess.run", return_value=proc):
            assert _gh_pr_search_issue_ref(1950) == 20

    def test_issue_search_empty_or_malformed_returns_none(self):
        from tools.sdlc_stage_query import _gh_pr_search_issue_ref

        # Empty list, non-list, and missing/None body all resolve to None.
        for stdout in ("[]", json.dumps({"not": "a list"}), json.dumps([{"number": 5}])):
            proc = MagicMock(returncode=0, stdout=stdout)
            with patch("tools.sdlc_stage_query.subprocess.run", return_value=proc):
                assert _gh_pr_search_issue_ref(1950) is None

    def test_issue_search_subprocess_error_returns_none(self):
        from tools.sdlc_stage_query import _gh_pr_search_issue_ref

        with patch("tools.sdlc_stage_query.subprocess.run", side_effect=OSError("boom")):
            assert _gh_pr_search_issue_ref(1950) is None


class TestBodyReferencesIssue:
    """#1987: closing-keyword body validation with word-boundary matching."""

    def test_word_boundary_prevents_prefix_match(self):
        from tools.sdlc_stage_query import _body_references_issue

        # #195 must NOT match a body that says "Closes #1950".
        assert _body_references_issue("Closes #1950", 195) is False

    def test_closing_keyword_variants_match(self):
        from tools.sdlc_stage_query import _body_references_issue

        for body in (
            "Closes #1950",
            "closes #1950",
            "Closed #1950",
            "Fixes #1950",
            "Fix #1950",
            "Fixed #1950",
            "Resolves #1950",
            "Resolve #1950",
            "Resolved #1950",
            "Closed: #1950",
            "This PR fixes #1950 finally.",
        ):
            assert _body_references_issue(body, 1950) is True, body

    def test_bare_mention_and_empty_do_not_match(self):
        from tools.sdlc_stage_query import _body_references_issue

        assert _body_references_issue("See #1950 for context", 1950) is False
        assert _body_references_issue("", 1950) is False
        assert _body_references_issue(None, 1950) is False


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
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
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

    def test_stages_exposes_router_underscore_keys(self):
        """Enriched ``stages`` must carry ``_verdicts``/``_sdlc_dispatches``.

        The router's staleness rules (``_critique_verdict_is_stale`` →
        row 2b, ``_latest_dispatch_at``) read these underscore keys directly
        off the ``stage_states`` arg. If ``query_enriched`` filters them out,
        a revised plan with a stale NEEDS REVISION verdict can never route to
        re-critique and dead-ends on ``/do-plan`` until G4 oscillation fires.
        """
        from tools.sdlc_stage_query import query_enriched

        dispatches = [{"skill": "/do-plan", "at": "2026-06-16T05:27:16+00:00"}]
        verdicts = {
            "CRITIQUE": {
                "verdict": "NEEDS REVISION",
                "recorded_at": "2026-06-16T05:16:03+00:00",
                "artifact_hash": "sha256:abc",
            }
        }
        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(
            {
                "ISSUE": "completed",
                "PLAN": "completed",
                "CRITIQUE": "in_progress",
                "_verdicts": verdicts,
                "_sdlc_dispatches": dispatches,
            }
        )
        mock_session.pr_number = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert result["stages"]["_verdicts"] == verdicts
        assert result["stages"]["_sdlc_dispatches"] == dispatches
        # And the router's staleness helper must see the verdict as stale.
        from agent.sdlc_router import _critique_verdict_is_stale

        assert _critique_verdict_is_stale(result["stages"]) is True

    def test_pr_number_resolved_from_session_field(self):
        """#2003 T1.7: the AgentSession.pr_number FIELD is the first rung —
        when set, the read-only recovery rungs (gh lookup) are never needed."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "completed", "PLAN": "completed"})
        mock_session.pr_number = 777
        mock_session.slug = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None) as lookup:
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        assert result["_meta"]["pr_number"] == 777
        lookup.assert_not_called()

    def test_pr_number_meta_rung_deleted(self):
        """#2003 T1.7 hard cutover: a stale `_pr_number` stage_states key is
        IGNORED — resolution goes session field → gh recovery rungs only."""
        from tools.sdlc_stage_query import query_enriched

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps(
            {"ISSUE": "completed", "PLAN": "completed", "_pr_number": 777}
        )
        mock_session.pr_number = None  # field unset → falls through to gh lookup
        mock_session.slug = None

        with patch("tools.sdlc_stage_query._find_session_by_id", return_value=mock_session):
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=555) as lookup:
                with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                    result = query_enriched(session_id="sid")

        # The legacy meta key must not win; the recovery ladder resolves 555.
        assert result["_meta"]["pr_number"] == 555
        lookup.assert_called_once()

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
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
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
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
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
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
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
            with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                with patch(
                    "tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)
                ):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        result = query_enriched(session_id="sid")

        assert result["_meta"]["plan_hash_at_build_start"] is None

    def test_compute_meta_parses_revision_applied_at_from_frontmatter(self, tmp_path):
        """_compute_meta surfaces revision_applied_at parsed from plan frontmatter (#1760)."""
        from tools.sdlc_stage_query import _compute_meta

        plan_path = tmp_path / "plan.md"
        plan_path.write_text(
            "---\n"
            "status: Ready\n"
            "revision_applied: true\n"
            "revision_applied_at: 2026-07-11T16:19:28Z\n"
            "---\n\n# Plan\n"
        )

        with patch("tools.sdlc_stage_query._resolve_target_repo", return_value=None):
            with patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)):
                with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=plan_path):
                        meta = _compute_meta({}, None, 1760)

        assert meta["revision_applied_at"] == "2026-07-11T16:19:28Z"

    def test_compute_meta_revision_applied_at_none_when_absent(self, tmp_path):
        """_compute_meta surfaces revision_applied_at=None when the frontmatter field
        is absent (latch inert, fail-safe)."""
        from tools.sdlc_stage_query import _compute_meta

        plan_path = tmp_path / "plan.md"
        plan_path.write_text("---\nstatus: Ready\nrevision_applied: true\n---\n\n# Plan\n")

        with patch("tools.sdlc_stage_query._resolve_target_repo", return_value=None):
            with patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)):
                with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=plan_path):
                        meta = _compute_meta({}, None, 1760)

        assert meta["revision_applied_at"] is None

    def test_compute_meta_revision_applied_at_none_when_unparseable(self, tmp_path):
        """A malformed revision_applied_at value fails safe to None."""
        from tools.sdlc_stage_query import _compute_meta

        plan_path = tmp_path / "plan.md"
        plan_path.write_text("---\nstatus: Ready\nrevision_applied_at: not-a-date\n---\n\n# Plan\n")

        with patch("tools.sdlc_stage_query._resolve_target_repo", return_value=None):
            with patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)):
                with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=plan_path):
                        meta = _compute_meta({}, None, 1760)

        assert meta["revision_applied_at"] is None

    def test_default_meta_includes_revision_applied_at(self):
        """_default_meta always includes revision_applied_at, defaulting to None."""
        from tools.sdlc_stage_query import _default_meta

        meta = _default_meta()
        assert "revision_applied_at" in meta
        assert meta["revision_applied_at"] is None

    def test_do_plan_documented_writer_format_round_trips_through_reader_and_latch(self, tmp_path):
        """#1760 blocker fix: the exact `date -u` timestamp format
        `.claude/skills-global/do-plan/SKILL.md` instructs the agent to write
        for `revision_applied_at` must round-trip through the REAL frontmatter
        parser (`_parse_revision_applied_at`) AND engage the router's
        convergence latch (`_critique_verdict_is_stale`) end-to-end.

        Prior tests in this class only prove the reader accepts a
        hand-synthesized ISO-8601 string -- they say nothing about whether
        the documented writer instruction actually produces that string. This
        test extracts the literal `date -u ...` command from SKILL.md, runs
        it (the same command an agent following the skill would run), writes
        the result into a plan frontmatter using the exact key the skill
        documents, and proves the write -> parse -> latch pipeline agrees.
        A drift between the documented format and the parser's regex would
        fail this test even though the synthesized-field tests above stay
        green.
        """
        import re

        skill_path = Path(REPO_ROOT) / ".claude" / "skills-global" / "do-plan" / "SKILL.md"
        skill_text = skill_path.read_text(encoding="utf-8")

        match = re.search(
            r"REVISION_APPLIED_AT=\$\((date -u [^)]+)\)",
            skill_text,
        )
        assert match, (
            "SKILL.md must document a `date -u` command assigned to "
            "REVISION_APPLIED_AT for /do-plan step 2a (#1760 writer)"
        )
        date_cmd = match.group(1)

        # Run the ACTUAL documented command, exactly as the agent would.
        proc = subprocess.run(date_cmd, shell=True, capture_output=True, text=True, timeout=5)
        assert proc.returncode == 0, proc.stderr
        documented_timestamp = proc.stdout.strip()
        assert documented_timestamp, "documented date command produced no output"

        # Write it into a plan file using the exact sibling-key shape the
        # skill instructs (revision_applied + revision_applied_at together).
        plan_path = tmp_path / "plan.md"
        plan_path.write_text(
            "---\n"
            "status: Ready\n"
            "revision_applied: true\n"
            f"revision_applied_at: {documented_timestamp}\n"
            "---\n\n# Plan\n"
        )

        from tools.sdlc_stage_query import _parse_revision_applied_at

        parsed = _parse_revision_applied_at(plan_path)
        assert parsed == documented_timestamp, (
            "the real parser must accept the exact format the skill documents writing"
        )

        # Full round trip: feed the parsed value into the router's actual
        # convergence latch and prove it suppresses staleness for the
        # settle-and-build dispatch case (#1760's core scenario).
        from datetime import datetime, timedelta

        from agent.sdlc_router import _critique_verdict_is_stale

        revision_dt = datetime.fromisoformat(documented_timestamp.replace("Z", "+00:00"))
        dispatch_dt = revision_dt - timedelta(minutes=30)  # /do-plan dispatch predates the revision
        verdict_dt = dispatch_dt - timedelta(minutes=30)  # critique verdict predates the dispatch

        stage_states = {
            "_verdicts": {
                "CRITIQUE": {
                    "verdict": "READY TO BUILD",
                    "recorded_at": verdict_dt.isoformat(),
                }
            },
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "at": dispatch_dt.isoformat()},
            ],
        }
        meta = {"revision_applied_at": documented_timestamp}

        assert _critique_verdict_is_stale(stage_states, meta) is False, (
            "the documented writer format must engage the #1760 latch and "
            "suppress staleness end-to-end"
        )


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
                with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
                    with patch("tools.sdlc_stage_query._find_plan_path", return_value=None):
                        from tools.sdlc_stage_query import _compute_meta

                        mock_session = MagicMock()
                        mock_session.pr_number = None
                        mock_session.slug = None
                        _compute_meta({}, mock_session, None)
        assert len(call_count) == 1, (
            f"_resolve_target_repo called {len(call_count)} times, expected 1"
        )


class TestResolveIssueRecord:
    """Issue #2012 task 2: the reader's issue-keyed resolution --
    ledger-first with a retained session fallback, guarded against ever
    reading a phantom ``PipelineLedger[(None, issue)]`` key (Risk 5,
    reader side / the BLOCKER round-2 gap)."""

    def test_target_repo_unresolved_returns_none_never_touches_ledger(self):
        """The defined empty-ledger outcome: when target_repo cannot be
        resolved at all, _resolve_issue_record returns None without ever
        constructing a PipelineLedger key."""
        from tools.sdlc_stage_query import _resolve_issue_record

        with (
            patch("tools.sdlc_stage_query._resolve_target_repo_for_read", return_value=None),
            patch("agent.pipeline_ledger.PipelineLedger.get_or_create") as mock_get_or_create,
        ):
            result = _resolve_issue_record(999888)

        assert result is None
        mock_get_or_create.assert_not_called()

    def test_query_stage_states_returns_empty_dict_when_target_repo_unresolved(self):
        """CLI contract: stage-query on an unresolvable repo context stays
        {} -- unchanged shape, never a crash."""
        from tools.sdlc_stage_query import query_stage_states

        with patch("tools.sdlc_stage_query._resolve_target_repo_for_read", return_value=None):
            result = query_stage_states(issue_number=999888)

        assert result == {}

    def test_query_enriched_returns_default_meta_when_target_repo_unresolved(self):
        """CLI contract: enriched query on an unresolvable repo context
        stays {"stages": {}, "_meta": {...defaults}} -- unchanged shape."""
        from tools.sdlc_stage_query import _default_meta, query_enriched

        with patch("tools.sdlc_stage_query._resolve_target_repo_for_read", return_value=None):
            result = query_enriched(issue_number=999888)

        assert result == {"stages": {}, "_meta": _default_meta()}

    def test_reads_ledger_when_target_repo_resolves_and_ledger_has_data(self):
        """A ledger that resolves and carries recorded stage state is read
        directly -- no session fallback needed."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_stage_query import _resolve_issue_record

        ledger = PipelineLedger.get_or_create("owner/resolve-issue-record", 700501)
        ledger.stage_states_json = json.dumps({"ISSUE": "completed", "PLAN": "in_progress"})
        ledger.save()

        with (
            patch(
                "tools.sdlc_stage_query._resolve_target_repo_for_read",
                return_value="owner/resolve-issue-record",
            ),
            patch("tools.sdlc_stage_query._find_session_by_issue") as mock_find_session,
        ):
            result = _resolve_issue_record(700501)

        assert result.ledger_key == ledger.ledger_key
        mock_find_session.assert_not_called()

    def test_falls_back_to_session_when_ledger_resolves_but_empty(self):
        """target_repo resolves and the ledger loads (get_or_create never
        fails), but it carries no recorded stage state yet -- retained
        cold-path session fallback (issues that started before this
        migration, or a session created between a backfill run and this
        deploy)."""
        from tools.sdlc_stage_query import _resolve_issue_record

        class _FakeSession:
            stage_states = json.dumps({"ISSUE": "completed"})

        fallback_session = _FakeSession()

        with (
            patch(
                "tools.sdlc_stage_query._resolve_target_repo_for_read",
                return_value="owner/empty-ledger-fallback",
            ),
            patch(
                "tools.sdlc_stage_query._find_session_by_issue", return_value=fallback_session
            ) as mock_find_session,
        ):
            result = _resolve_issue_record(700502)

        assert result is fallback_session
        mock_find_session.assert_called_once_with(700502)

    def test_query_stage_states_reads_via_ledger_end_to_end(self):
        """query_stage_states(issue_number=...) reads a real PipelineLedger
        end to end -- no session involved at all."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_stage_query import query_stage_states

        ledger = PipelineLedger.get_or_create("owner/qss-ledger", 700503)
        ledger.stage_states_json = json.dumps({"ISSUE": "completed", "PLAN": "ready"})
        ledger.save()

        with patch(
            "tools.sdlc_stage_query._resolve_target_repo_for_read",
            return_value="owner/qss-ledger",
        ):
            result = query_stage_states(issue_number=700503)

        assert result["ISSUE"] == "completed"
        assert result["PLAN"] == "ready"

    def test_query_enriched_reads_pr_number_from_ledger_field(self):
        """_compute_meta's session-derived pr_number lookup works
        unmodified against a PipelineLedger (field-compatible via
        getattr)."""
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_stage_query import query_enriched

        ledger = PipelineLedger.get_or_create("owner/qse-ledger", 700504)
        ledger.stage_states_json = json.dumps({"ISSUE": "completed"})
        ledger.pr_number = 777
        ledger.save()

        with (
            patch(
                "tools.sdlc_stage_query._resolve_target_repo_for_read",
                return_value="owner/qse-ledger",
            ),
            patch("tools.sdlc_stage_query._fetch_pr_merge_state", return_value=(None, None)),
        ):
            result = query_enriched(issue_number=700504)

        assert result["_meta"]["pr_number"] == 777
