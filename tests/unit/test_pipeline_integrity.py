"""Tests for SDLC pipeline integrity fixes.

Covers:
A. Session continuation fallback metadata preservation
B. Deterministic URL construction in Observer
C. Merge guard hook blocking
"""

import json
import os
import subprocess
import sys

from utils.github_patterns import construct_canonical_url as _construct_canonical_url


class TestCanonicalUrlConstruction:
    """Test deterministic URL construction from worker-provided URLs."""

    def test_correct_repo_pr_url_preserved(self):
        url = "https://github.com/tomcounsell/ai/pull/42"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/42"

    def test_correct_repo_issue_url_preserved(self):
        url = "https://github.com/tomcounsell/ai/issues/17"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/17"

    def test_wrong_repo_url_corrected(self):
        """Worker provided wrong repo — number extracted, correct repo used."""
        url = "https://github.com/wrong-org/wrong-repo/pull/99"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/99"

    def test_wrong_repo_issue_url_corrected(self):
        url = "https://github.com/other-org/other-repo/issues/5"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/5"

    def test_none_url_returns_none(self):
        assert _construct_canonical_url(None, "tomcounsell/ai") is None

    def test_empty_string_returns_none(self):
        assert _construct_canonical_url("", "tomcounsell/ai") is None

    def test_whitespace_only_returns_none(self):
        assert _construct_canonical_url("   ", "tomcounsell/ai") is None

    def test_non_github_url_returns_none(self):
        """Non-GitHub URLs have no extractable number."""
        result = _construct_canonical_url("https://example.com/page", "tomcounsell/ai")
        assert result is None

    def test_github_url_without_number_returns_none(self):
        result = _construct_canonical_url("https://github.com/tomcounsell/ai", "tomcounsell/ai")
        assert result is None

    def test_no_gh_repo_returns_none(self):
        url = "https://github.com/tomcounsell/ai/pull/42"
        assert _construct_canonical_url(url, None) is None
        assert _construct_canonical_url(url, "") is None

    def test_pr_url_takes_priority_over_issue_path(self):
        """URLs with /pull/ should produce PR URLs."""
        url = "https://github.com/org/repo/pull/100"
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/pull/100"

    def test_url_with_trailing_whitespace(self):
        url = "  https://github.com/org/repo/issues/7  "
        result = _construct_canonical_url(url, "tomcounsell/ai")
        assert result == "https://github.com/tomcounsell/ai/issues/7"


class TestMergeGuardHook:
    """Test the merge guard PreToolUse hook."""

    HOOK_PATH = ".claude/hooks/validators/validate_merge_guard.py"

    def _run_hook(self, tool_name: str, command: str) -> dict | None:
        """Run the hook with given input and return parsed output or None."""
        hook_input = json.dumps(
            {
                "tool_name": tool_name,
                "tool_input": {"command": command},
            }
        )
        result = subprocess.run(
            [sys.executable, self.HOOK_PATH],
            input=hook_input,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        )
        assert result.returncode == 0, f"Hook failed: {result.stderr}"
        if result.stdout.strip():
            return json.loads(result.stdout.strip())
        return None

    def test_blocks_gh_pr_merge(self):
        """End-to-end block: PR 42 is long merged, so the live predicate fails
        (state != OPEN); if `gh` is unavailable the hook fails closed. Either
        way the direct merge call is blocked with a /do-merge pointer."""
        result = self._run_hook("Bash", "gh pr merge 42")
        assert result is not None
        assert result["decision"] == "block"
        assert "/do-merge" in result["reason"]

    def test_allows_gh_pr_merge_help(self):
        result = self._run_hook("Bash", "gh pr merge --help")
        assert result is None

    def test_allows_echo_containing_merge(self):
        result = self._run_hook("Bash", 'echo "gh pr merge"')
        assert result is None

    def test_allows_non_bash_tool(self):
        result = self._run_hook("Read", "gh pr merge 42")
        assert result is None

    def test_allows_unrelated_command(self):
        result = self._run_hook("Bash", "git status")
        assert result is None

    def test_allows_gh_pr_list(self):
        result = self._run_hook("Bash", "gh pr list")
        assert result is None

    def test_allows_break_glass_override_merge(self):
        """A `override: <reason>` auth file short-circuits the predicate and
        allows the merge (#2003 break-glass contract)."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        auth_file = os.path.join(project_root, "data", "merge_authorized_424242")
        os.makedirs(os.path.dirname(auth_file), exist_ok=True)
        try:
            with open(auth_file, "w") as f:
                f.write("override: pipeline-integrity test break-glass\n")
            result = self._run_hook("Bash", "gh pr merge 424242 --squash")
            assert result is None  # Allowed
        finally:
            os.unlink(auth_file)

    def test_empty_auth_file_no_longer_authorizes(self):
        """The pre-#2003 bypass: an empty touch-file used to authorize any
        merge (the PR #2005 incident). It must now be treated as absent — the
        live predicate runs (and fails closed for a nonexistent PR)."""
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        auth_file = os.path.join(project_root, "data", "merge_authorized_424243")
        os.makedirs(os.path.dirname(auth_file), exist_ok=True)
        try:
            with open(auth_file, "w") as f:
                f.write("")
            result = self._run_hook("Bash", "gh pr merge 424243 --squash")
            assert result is not None
            assert result["decision"] == "block"
        finally:
            os.unlink(auth_file)

    def test_blocks_merge_without_pr_number(self):
        """Merge without a PR number is blocked (can't check authorization)."""
        result = self._run_hook("Bash", "gh pr merge --squash")
        assert result is not None
        assert result["decision"] == "block"


class TestEnqueueContinuationFallback:
    """Test that the fallback path preserves session metadata."""

    def test_extract_agent_session_fields_includes_metadata(self):
        """Verify _AGENT_SESSION_FIELDS includes all critical session metadata."""
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        critical_fields = [
            "context_summary",
            "issue_url",
            "pr_url",
            "session_events",
            "correlation_id",
            "slug",
        ]
        for field in critical_fields:
            assert field in _AGENT_SESSION_FIELDS, (
                f"Critical field {field!r} missing from _AGENT_SESSION_FIELDS"
            )

    def test_diagnose_missing_session_returns_dict(self):
        """Verify _diagnose_missing_session returns diagnostic info."""
        from agent.agent_session_queue import _diagnose_missing_session

        result = _diagnose_missing_session("nonexistent-session-id-12345")
        assert isinstance(result, dict)
        # Should have hash_exists, popoto_query_matches, or error (if Redis not available)
        assert "hash_exists" in result or "error" in result


def _non_autokey_model_fields() -> set[str]:
    """AgentSession field names eligible to appear in a `create(**fields)` payload.

    AutoKeyFields are excluded because Popoto generates them; passing one into
    a create payload is an error, so their absence from `_AGENT_SESSION_FIELDS`
    is correct rather than drift. The exclusion is derived by ``isinstance``
    against ``AutoKeyField`` and NOT by subtracting a literal ``{"id"}``, so a
    second auto-key field added later cannot silently reopen the same
    off-by-one.
    """
    from popoto import AutoKeyField

    from models.agent_session import AgentSession

    return {
        name
        for name, field in AgentSession._meta.fields.items()
        if not isinstance(field, AutoKeyField)
    }


# AgentSession fields deliberately NOT copied by `_extract_agent_session_fields`
# as of today. This is an honest record of an existing gap, not an approval of
# it: the helper serves two incompatible contracts (delete-and-recreate of the
# SAME session, where dropping a field is data loss, vs. creation of a NEW
# session, where the #2518 execution fence must be reset), and resolving that
# conflict is tracked in issue #2563.
#
# TERMINATION CONDITION: when #2563 ships, entries leave this set and it empties.
# An empty KNOWN_GAP is a valid, meaningful state — the guard below asserts set
# EQUALITY, so it becomes a plain completeness assertion at that point and a
# newly added model field can never be silently absorbed into the excuse list.
#
# The left-hand side of the comparison excludes auto-key fields; see
# `_non_autokey_model_fields` for why and how.
KNOWN_GAP = frozenset(
    {
        "active_run_id",
        "auto_resume_attempts",
        "budget_tripped",
        "budget_tripped_reason",
        "chat_message_log",
        "claude_version",
        "continuation_depth",
        "crash_outcome_attributed",
        "crash_signature",
        "current_tool_name",
        "current_tool_timeout_s",
        "dev_agent_id",
        "exec_cwd",
        "exec_harness",
        "exec_pid",
        "exit_reason",
        "exit_returncode",
        "is_ledger",
        "issue_number",
        "last_authored_at",
        "last_compaction_ts",
        "last_tool_use_at",
        "last_turn_at",
        "model",
        "owned_run_ids",
        "pid_create_time",
        "pr_number",
        "project_config",
        "recent_thinking_excerpt",
        "requires_real_chrome",
        "response_delivered_at",
        "retain_for_resume",
        "rework_triggered",
        "runner_cwd",
        "spawn_history",
        "task_type",
        "thread_first_created_at",
        "thread_run_count",
        "thread_tool_call_count",
        "thread_turn_count",
        "tool_timeout_count_default",
        "tool_timeout_count_internal",
        "tool_timeout_count_mcp",
        "total_cache_read_tokens",
        "total_cost_usd",
        "total_input_tokens",
        "total_output_tokens",
        "unhealthy_reason",
        "user_facing_routed",
        "worker_pid",
    }
)


class TestAgentSessionFieldContractDrift:
    """Guard `_AGENT_SESSION_FIELDS` against drift in both directions.

    `df6097fe6` deleted the `expectations` field and nothing detected that the
    list guarding it had drifted; these two tests are what would have caught it.
    Five other modules assert *membership* in `_AGENT_SESSION_FIELDS`
    (`test_health_check_recovery_finalization.py`, `test_agent_session_hierarchy.py`,
    `test_agent_session.py`, `test_nudge_loop.py`, `test_session_completion_zombie.py`);
    these are the completeness counterparts.
    """

    def test_no_phantom_names_in_field_list(self):
        """Every name in `_AGENT_SESSION_FIELDS` still exists on the model.

        A phantom name makes `_extract_agent_session_fields` raise
        AttributeError at runtime on the delete-and-recreate path.
        """
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        phantoms = sorted(set(_AGENT_SESSION_FIELDS) - _non_autokey_model_fields())
        assert not phantoms, (
            f"_AGENT_SESSION_FIELDS names field(s) that no longer exist on "
            f"AgentSession: {phantoms}. Remove them from the list in "
            f"agent/agent_session_queue.py, or restore the model field."
        )

    def test_unlisted_model_fields_match_known_gap(self):
        """Model fields absent from the list equal the frozen KNOWN_GAP exactly."""
        from agent.agent_session_queue import _AGENT_SESSION_FIELDS

        actual_gap = _non_autokey_model_fields() - set(_AGENT_SESSION_FIELDS)
        unclassified = sorted(actual_gap - KNOWN_GAP)
        newly_classified = sorted(KNOWN_GAP - actual_gap)
        assert actual_gap == KNOWN_GAP, (
            f"AgentSession field coverage drifted. Unclassified field(s) missing "
            f"from _AGENT_SESSION_FIELDS and from KNOWN_GAP: {unclassified}. "
            f"KNOWN_GAP entries no longer in the gap: {newly_classified}. "
            f"Decide explicitly whether each field should be copied by "
            f"_extract_agent_session_fields (see issue #2563) and update the "
            f"list or KNOWN_GAP accordingly."
        )


class TestMergeStageTracking:
    """Test MERGE stage is properly tracked across modules."""

    def test_merge_in_display_stages(self):
        from agent.pipeline_graph import DISPLAY_STAGES

        assert "MERGE" in DISPLAY_STAGES

    def test_merge_in_stage_to_skill(self):
        from agent.pipeline_graph import STAGE_TO_SKILL

        assert "MERGE" in STAGE_TO_SKILL
        assert STAGE_TO_SKILL["MERGE"] == "/do-merge"

    def test_merge_in_stage_constants(self):
        from models.agent_session import SDLC_STAGES

        assert "MERGE" in SDLC_STAGES

    def test_docs_routes_to_merge(self):
        from agent.pipeline_graph import get_next_stage

        result = get_next_stage("DOCS", "success")
        assert result == ("MERGE", "/do-merge")

    def test_merge_skill_mapped(self):
        from agent.pipeline_graph import STAGE_TO_SKILL

        assert STAGE_TO_SKILL["MERGE"] == "/do-merge"
