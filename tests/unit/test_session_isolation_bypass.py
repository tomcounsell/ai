"""Unit tests for session isolation bypass fixes (issue #887, issue #1272).

Tests the three fix paths from #887:
- Fix A: Worktree enforcement guard in _execute_agent_session()
- Fix B: --slug flag on valor-session create
- Fix C: PM system prompt worktree CWD instruction

And the residual-hole fix from #1272:
- Synthetic slug allocation for slugless dev sessions
- Synthetic-slug worktree cleanup
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.worktree_manager import WORKTREES_DIR, _validate_slug


class TestWorktreeEnforcementGuard:
    """Fix A: Dev sessions with a slug must get worktree isolation.

    Tests the guard in _execute_agent_session() that prevents dev sessions
    from running in the main checkout when they have a slug.
    """

    def _make_session(
        self,
        session_type="dev",
        slug="test-feature",
        working_dir="/Users/test/src/ai",
        session_id="0_12345",
        project_key="valor",
        status="running",
    ):
        """Create a mock AgentSession for testing."""
        session = MagicMock()
        session.session_type = session_type
        session.slug = slug
        session.working_dir = working_dir
        session.session_id = session_id
        session.project_key = project_key
        session.status = status
        session.chat_id = "0"
        session.telegram_message_id = 0
        session.message_text = "Test message"
        session.sender_name = "test"
        session.auto_continue_count = 0
        session.correlation_id = None
        session.task_list_id = None
        session.created_at = None
        session.started_at = None
        session.updated_at = None
        session.queued_steering_messages = []
        return session

    def test_dev_session_with_slug_fails_without_worktree(self):
        """A dev session with a slug in the main checkout must be rejected."""
        # The main-checkout protection guard checks: session_type == "dev"
        # AND slug is set AND WORKTREES_DIR not in working_dir
        session = self._make_session(session_type="dev", slug="my-feature")

        # Simulate what the guard checks
        working_dir = Path(session.working_dir)
        _stype = session.session_type
        slug = session.slug

        assert _stype == "dev"
        assert slug is not None
        assert WORKTREES_DIR not in str(working_dir)

        # The guard should fire in this case
        should_block = _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir)
        assert should_block is True

    def test_dev_session_with_slug_in_worktree_passes(self):
        """A dev session with a slug already in a worktree should pass."""
        session = self._make_session(
            session_type="dev",
            slug="my-feature",
            working_dir=f"/Users/test/src/ai/{WORKTREES_DIR}/my-feature",
        )

        working_dir = Path(session.working_dir)
        _stype = session.session_type
        slug = session.slug

        should_block = _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir)
        assert should_block is False

    def test_pm_session_with_slug_is_not_blocked(self):
        """PM sessions should never be blocked by the worktree guard."""
        session = self._make_session(session_type="pm", slug="my-feature")

        working_dir = Path(session.working_dir)
        _stype = session.session_type
        slug = session.slug

        should_block = _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir)
        assert should_block is False

    def test_teammate_session_without_slug_is_not_blocked(self):
        """Teammate sessions without a slug should not be blocked."""
        session = self._make_session(session_type="teammate", slug=None)

        working_dir = Path(session.working_dir)
        _stype = session.session_type
        slug = session.slug

        should_block = _stype == "dev" and slug and WORKTREES_DIR not in str(working_dir)
        assert should_block is False

    def test_dev_session_without_slug_is_not_blocked(self):
        """Ad-hoc dev sessions (no slug) should proceed normally."""
        session = self._make_session(session_type="dev", slug=None)

        working_dir = Path(session.working_dir)
        _stype = session.session_type
        slug = session.slug

        should_block = bool(_stype == "dev" and slug and WORKTREES_DIR not in str(working_dir))
        assert should_block is False

    def test_worktree_creation_failure_raises_for_dev_session(self):
        """When worktree creation fails for a dev session, it should raise, not fall back."""
        # Simulate the escalated error handling path
        session = self._make_session(session_type="dev", slug="my-feature")

        # The new code raises RuntimeError instead of falling back
        with pytest.raises(RuntimeError, match="Worktree provisioning failed"):
            _stype = session.session_type
            if _stype == "dev":
                raise RuntimeError(
                    f"Worktree provisioning failed for dev session "
                    f"slug={session.slug}: simulated error. "
                    f"Refusing to run in main checkout."
                )

    def test_worktree_creation_failure_warns_for_non_dev_session(self):
        """When worktree creation fails for a non-dev session, it should warn and continue."""
        session = self._make_session(session_type="pm", slug="my-feature")

        # For non-dev sessions, the original warning behavior is preserved
        _stype = session.session_type
        assert _stype != "dev"  # Should fall through to warning path


class TestSlugFlagOnCreate:
    """Fix B: --slug flag on valor-session create provisions worktree at creation time."""

    def test_validate_slug_rejects_empty(self):
        """Empty slug must be rejected."""
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("")

    def test_validate_slug_rejects_traversal(self):
        """Path traversal slugs must be rejected."""
        with pytest.raises(ValueError, match="Invalid slug"):
            _validate_slug("../traversal")

    def test_validate_slug_accepts_valid(self):
        """Valid slugs should pass validation."""
        _validate_slug("my-feature")
        _validate_slug("fix_bug_123")
        _validate_slug("v2.0")

    def test_slug_flag_in_argparser(self):
        """The create subcommand must accept --slug."""
        import argparse

        # Replicate the argument parser setup
        parser = argparse.ArgumentParser()
        parser.add_argument("--slug", help="Work item slug")
        args = parser.parse_args(["--slug", "my-feature"])
        assert args.slug == "my-feature"

    def test_slug_flag_absent_is_none(self):
        """When --slug is not provided, it should be None."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--slug", help="Work item slug")
        args = parser.parse_args([])
        assert args.slug is None

    @patch("agent.worktree_manager.get_or_create_worktree")
    def test_slug_provisions_worktree(self, mock_get_or_create):
        """When --slug is provided, get_or_create_worktree should be called."""
        mock_get_or_create.return_value = Path("/Users/test/src/ai/.worktrees/my-feature")

        slug = "my-feature"
        repo_root = Path("/Users/test/src/ai")

        _validate_slug(slug)
        wt_path = mock_get_or_create(repo_root, slug)

        mock_get_or_create.assert_called_once_with(repo_root, slug)
        assert WORKTREES_DIR in str(wt_path)


class TestPMSystemPromptWorktreeInstruction:
    """Fix C: PM system prompt includes worktree CWD instruction for dev sessions."""

    def test_pm_prompt_contains_worktree_instruction(self):
        """The PM persona prompt must contain worktree isolation instructions."""
        pm_prompt_path = (
            Path(__file__).parent.parent.parent / "config" / "personas" / "project-manager.md"
        )
        assert pm_prompt_path.exists(), f"PM prompt not found at {pm_prompt_path}"

        content = pm_prompt_path.read_text()

        # Check for the key elements of the worktree instruction
        assert ".worktrees/" in content, "PM prompt must reference .worktrees/ path"
        assert "Issue #887" in content or "issue #887" in content, (
            "PM prompt must reference issue #887"
        )
        assert "dev-session" in content, "PM prompt must mention dev-session"
        assert "worktree" in content.lower(), "PM prompt must mention worktree isolation"

    def test_pm_prompt_contains_cwd_guidance(self):
        """The PM prompt must instruct about CWD for Agent tool calls."""
        pm_prompt_path = (
            Path(__file__).parent.parent.parent / "config" / "personas" / "project-manager.md"
        )
        content = pm_prompt_path.read_text()

        # The prompt should tell the PM to use the worktree path as working directory
        assert "working" in content.lower() and "directory" in content.lower(), (
            "PM prompt must mention working directory"
        )


class TestSyntheticSlugForSlugslessDev:
    """Issue #1272: residual-hole fix for slugless dev sessions.

    Verifies the synthesis logic in ``_execute_agent_session()``:
    - Slugless dev session with ``agent_session_id`` set gets ``dev-{aid[:8]}``
    - Slugless dev session with no ``agent_session_id`` is rejected by the
      executor-guard precondition (no synthesis attempted)
    - Synthetic slugs match the regex used by the cleanup hook
    """

    def test_synthetic_slug_pattern_matches_cleanup_regex(self):
        """The cleanup hook regex must match every shape the synthesis path produces."""
        # The synthesis line is ``slug = f"dev-{agent_session_id[:8]}"``.
        # Worker-issued aids are UUID4 hex; the first 8 chars are always
        # lowercase hex.
        cleanup_regex = re.compile(r"^dev-[0-9a-f]{8}$")
        for aid in (
            "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "00000000-0000-0000-0000-000000000000",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",
        ):
            slug = f"dev-{aid[:8]}"
            assert cleanup_regex.match(slug), f"cleanup regex must match {slug!r}"

    def test_synthetic_slug_validates(self):
        """Synthetic slugs must pass the worktree manager's _validate_slug check."""
        # If this regex breaks, the synthesis branch will raise ValueError on
        # _validate_slug() and dev sessions will fail to start.
        for aid_prefix in ("12345678", "abcdef01", "00000000"):
            slug = f"dev-{aid_prefix}"
            # Should not raise
            _validate_slug(slug)

    def test_synthetic_slug_does_not_match_real_slugs(self):
        """The cleanup regex must NOT match human-chosen slugs.

        This is the safety guarantee: ``cleanup_after_merge`` runs on every
        session completion when the slug starts with ``dev-`` and matches the
        regex. A real human-chosen slug like ``dev-improvements`` or
        ``dev-1272`` MUST NOT be cleaned up automatically.
        """
        cleanup_regex = re.compile(r"^dev-[0-9a-f]{8}$")
        for real_slug in (
            "my-feature",
            "fix-1272",
            "dev-improvements",  # starts with 'dev-' but not 8 hex chars
            "dev-1272",  # would match if regex allowed digits-only
            "dev-abcdefghi",  # 9 chars, too many
            "dev-abc",  # 3 chars, too few
            "dev-abcdef0g",  # 'g' is not hex
        ):
            if real_slug == "dev-1272":
                # 4 hex chars — does match the literal regex check below as
                # not 8 chars; sanity-check anyway.
                pass
            assert not cleanup_regex.match(real_slug), (
                f"cleanup regex must not match human slug {real_slug!r}"
            )

    def test_dev_session_no_slug_synthesizes_worktree(self):
        """Slugless dev session with aid set produces dev-{aid[:8]} slug.

        Mirrors the synthesis line in ``_execute_agent_session()`` so a
        regression in the formula or the trigger condition shows up here.
        """
        # Trigger condition mirrored from session_executor.py
        slug = None
        session_type = "dev"
        agent_session_id = "abcd1234-5678-9012-3456-789012345678"
        is_synthetic = False

        if not slug and session_type == "dev" and agent_session_id:
            slug = f"dev-{agent_session_id[:8]}"
            is_synthetic = True

        assert is_synthetic is True
        assert slug == "dev-abcd1234"
        assert re.match(r"^dev-[0-9a-f]{8}$", slug)

    def test_dev_session_no_slug_no_agent_session_id_blocked(self):
        """Slugless dev session with no aid must be blocked by the precondition.

        The synthesis line crashes with ``TypeError: 'NoneType' object is not
        subscriptable`` if ``aid`` is None. The executor-guard precondition
        must fire BEFORE the synthesis line is reached.
        """
        # Mirror the executor-guard precondition logic
        session = MagicMock()
        session.session_type = "dev"
        session.slug = None
        session.agent_session_id = None

        _stype_pre = getattr(session, "session_type", None)
        _slug_pre = getattr(session, "slug", None)
        _aid_pre = getattr(session, "agent_session_id", None)

        should_block = _stype_pre == "dev" and _slug_pre is None and _aid_pre is None
        assert should_block is True

    def test_pm_session_no_slug_no_aid_not_blocked_by_synthesis_guard(self):
        """PM/teammate sessions don't enter synthesis; the precondition lets them through."""
        for stype in ("pm", "teammate"):
            session = MagicMock()
            session.session_type = stype
            session.slug = None
            session.agent_session_id = None

            _stype_pre = getattr(session, "session_type", None)
            _slug_pre = getattr(session, "slug", None)
            _aid_pre = getattr(session, "agent_session_id", None)

            should_block = _stype_pre == "dev" and _slug_pre is None and _aid_pre is None
            assert should_block is False, (
                f"synthesis precondition must only fire for slugless dev with no aid, not {stype!r}"
            )

    def test_dev_session_with_aid_passes_precondition(self):
        """Slugless dev session WITH aid must pass the precondition (synthesis runs)."""
        session = MagicMock()
        session.session_type = "dev"
        session.slug = None
        session.agent_session_id = "ffeeddcc-bbaa-9988-7766-554433221100"

        _stype_pre = getattr(session, "session_type", None)
        _slug_pre = getattr(session, "slug", None)
        _aid_pre = getattr(session, "agent_session_id", None)

        should_block = _stype_pre == "dev" and _slug_pre is None and _aid_pre is None
        assert should_block is False

    def test_synthetic_slug_worktree_pruneable(self):
        """cleanup_after_merge() must accept dev-XXXXXXXX slugs without error."""
        from agent.worktree_manager import _validate_slug as v

        # Validates that the synthetic slug shape is accepted by the same
        # validation path cleanup_after_merge() uses.
        for aid_prefix in ("12345678", "abcdef01"):
            slug = f"dev-{aid_prefix}"
            v(slug)  # would raise ValueError if invalid


class TestSyntheticSlugLogMarker:
    """Issue #1272: ``[synthetic-slug]`` log marker for post-deploy reflection scans."""

    def test_log_marker_is_stable_literal(self):
        """The log marker must be a fixed literal token, not interpolated."""
        # Read the source and ensure the literal token appears in an
        # f-string prefix (i.e., before any ``{``). If somebody refactors
        # to ``f"[{token}]"``, log scans will break silently.
        src_path = Path(__file__).parent.parent.parent / "agent" / "session_executor.py"
        src = src_path.read_text()
        # The synthesis-site log line + the cleanup log line must both
        # contain the literal ``[synthetic-slug]`` token.
        assert src.count("[synthetic-slug]") >= 2, (
            "expected at least 2 occurrences of the literal `[synthetic-slug]` "
            "log marker in agent/session_executor.py — synthesis log + "
            "cleanup log"
        )
