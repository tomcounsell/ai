"""Tests for .claude/hooks/post_compact.py.

Issue #1139. Covers the re-grounding nudge builder, AgentSession lookup
success/failure/no-session paths, stdout output format, and bail-out guarantee.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add .claude/hooks to sys.path so we can import post_compact directly
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent.parent / ".claude" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from post_compact import (
    _build_regrounding_nudge,
    _extract_issue_number,
    _lookup_session,
)
from models.agent_session import AgentSession


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session(claude_uuid: str, plan_url=None, issue_url=None, stage_states=None) -> AgentSession:
    """Create a minimal AgentSession for testing."""
    session = AgentSession(
        session_id=f"test-{claude_uuid[:8]}",
        session_type="dev",
        project_key="test-postcompact",
        claude_session_uuid=claude_uuid,
    )
    if plan_url is not None:
        session.plan_url = plan_url
    if issue_url is not None:
        session.issue_url = issue_url
    session.save()
    return session


# ---------------------------------------------------------------------------
# _extract_issue_number
# ---------------------------------------------------------------------------


class TestExtractIssueNumber:
    def test_standard_github_url(self):
        url = "https://github.com/tomcounsell/ai/issues/1139"
        assert _extract_issue_number(url) == 1139

    def test_url_with_trailing_slash(self):
        url = "https://github.com/org/repo/issues/42/"
        assert _extract_issue_number(url) == 42

    def test_none_returns_none(self):
        assert _extract_issue_number(None) is None

    def test_empty_string_returns_none(self):
        assert _extract_issue_number("") is None

    def test_no_trailing_number_returns_none(self):
        assert _extract_issue_number("https://github.com/org/repo/issues/") is None

    def test_plan_url_has_no_issue_number(self):
        """Plan URLs don't end with issue numbers -- returns None."""
        url = "https://github.com/org/repo/blob/main/docs/plans/foo.md"
        assert _extract_issue_number(url) is None


# ---------------------------------------------------------------------------
# _build_regrounding_nudge
# ---------------------------------------------------------------------------


class TestBuildRegroundingNudge:
    def test_full_nudge_with_all_context(self, tmp_path):
        """All 4 items present when plan, stage_states, PROGRESS.md, and cwd all exist."""
        progress_md = tmp_path / "PROGRESS.md"
        progress_md.write_text("## Working State")

        nudge = _build_regrounding_nudge(
            plan_url="https://github.com/org/repo/blob/main/docs/plans/foo.md",
            issue_url="https://github.com/org/repo/issues/1139",
            stage_states_json='{"build": "complete"}',
            cwd=str(tmp_path),
        )

        assert "Context was just compacted" in nudge
        assert "Re-read the plan" in nudge
        assert "SDLC stage progress" in nudge
        assert "1139" in nudge
        assert "PROGRESS.md" in nudge
        assert "TodoWrite" in nudge

    def test_minimal_nudge_no_session(self):
        """No session context -- minimal nudge: header + TodoWrite item only."""
        nudge = _build_regrounding_nudge(
            plan_url=None,
            issue_url=None,
            stage_states_json=None,
            cwd="",
        )

        assert "Context was just compacted" in nudge
        assert "TodoWrite" in nudge
        # Plan and stage items absent
        assert "Re-read the plan" not in nudge
        assert "SDLC stage progress" not in nudge
        assert "PROGRESS.md" not in nudge

    def test_partial_nudge_no_plan(self):
        """AgentSession found but plan_url is None -- plan item absent."""
        nudge = _build_regrounding_nudge(
            plan_url=None,
            issue_url="https://github.com/org/repo/issues/42",
            stage_states_json='{"build": "complete"}',
            cwd="",
        )

        assert "Re-read the plan" not in nudge
        assert "SDLC stage progress" in nudge
        assert "TodoWrite" in nudge

    def test_partial_nudge_no_progress_md(self, tmp_path):
        """Plan and stage_states present but no PROGRESS.md -- PROGRESS.md item absent."""
        # tmp_path exists but has no PROGRESS.md
        nudge = _build_regrounding_nudge(
            plan_url="https://github.com/org/repo/blob/main/docs/plans/foo.md",
            issue_url="https://github.com/org/repo/issues/99",
            stage_states_json='{"build": "complete"}',
            cwd=str(tmp_path),
        )

        assert "Re-read the plan" in nudge
        assert "SDLC stage progress" in nudge
        assert "PROGRESS.md" not in nudge
        assert "TodoWrite" in nudge

    def test_nudge_under_token_budget(self, tmp_path):
        """Full 4-item nudge word count must be < 250 (proxy for < 300 tokens)."""
        progress_md = tmp_path / "PROGRESS.md"
        progress_md.write_text("## Working State")

        nudge = _build_regrounding_nudge(
            plan_url="https://github.com/org/repo/blob/main/docs/plans/foo.md",
            issue_url="https://github.com/org/repo/issues/1139",
            stage_states_json='{"build": "complete"}',
            cwd=str(tmp_path),
        )

        word_count = len(nudge.split())
        assert word_count < 250, f"Nudge too long: {word_count} words"

    def test_empty_cwd_no_progress_md_check(self):
        """Empty cwd string -- PROGRESS.md item never included."""
        nudge = _build_regrounding_nudge(
            plan_url=None,
            issue_url=None,
            stage_states_json=None,
            cwd="",
        )
        assert "PROGRESS.md" not in nudge

    def test_stage_states_no_issue_url(self):
        """stage_states set but no issue_url -- stage item included but without issue number."""
        nudge = _build_regrounding_nudge(
            plan_url=None,
            issue_url=None,
            stage_states_json='{"build": "complete"}',
            cwd="",
        )
        assert "SDLC stage progress" in nudge
        # No issue number in the nudge since issue_url is None
        assert "--issue-number" not in nudge


# ---------------------------------------------------------------------------
# _lookup_session
# ---------------------------------------------------------------------------


class TestLookupSession:
    def test_session_found_returns_fields(self):
        """Session exists -- returns (plan_url, issue_url, stage_states_json)."""
        claude_uuid = str(uuid.uuid4())
        _make_session(
            claude_uuid,
            plan_url="https://github.com/org/repo/blob/main/docs/plans/foo.md",
            issue_url="https://github.com/org/repo/issues/1139",
        )

        plan_url, issue_url, stage_states_json = _lookup_session(claude_uuid)

        assert plan_url == "https://github.com/org/repo/blob/main/docs/plans/foo.md"
        assert issue_url == "https://github.com/org/repo/issues/1139"
        # No stage_states set -- should be None
        assert stage_states_json is None

    def test_no_session_returns_nones(self):
        """No matching session -- returns (None, None, None)."""
        plan_url, issue_url, stage_states_json = _lookup_session(str(uuid.uuid4()))
        assert plan_url is None
        assert issue_url is None
        assert stage_states_json is None

    def test_lookup_exception_returns_nones(self):
        """AgentSession.query raises -- returns (None, None, None) without raising."""
        with patch("models.agent_session.AgentSession.query") as mock_query:
            mock_query.filter.side_effect = ConnectionError("redis down")
            plan_url, issue_url, stage_states_json = _lookup_session(str(uuid.uuid4()))

        assert plan_url is None
        assert issue_url is None
        assert stage_states_json is None


# ---------------------------------------------------------------------------
# main() / subprocess contract
# ---------------------------------------------------------------------------


class TestMainFunction:
    def test_no_session_id_in_input(self, capsys):
        """Input missing session_id -- nothing written to stdout."""
        from post_compact import main

        hook_input = {"hook_event_name": "PostCompact", "trigger": "auto"}
        # Patch at the module level where it was imported (not the source module)
        with patch("post_compact.read_hook_input", return_value=hook_input):
            main()

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_no_session_row_emits_minimal_nudge(self, capsys):
        """No AgentSession row -- still emits minimal nudge (header + TodoWrite)."""
        from post_compact import main

        claude_uuid = str(uuid.uuid4())
        hook_input = {
            "hook_event_name": "PostCompact",
            "session_id": claude_uuid,
            "trigger": "auto",
            "cwd": "/tmp",
        }
        # Patch at the module level where it was imported (not the source module)
        with patch("post_compact.read_hook_input", return_value=hook_input):
            main()

        captured = capsys.readouterr()
        assert "Context was just compacted" in captured.out
        assert "TodoWrite" in captured.out
        # No plan or stage items
        assert "Re-read the plan" not in captured.out


# ---------------------------------------------------------------------------
# Standalone import guard
# ---------------------------------------------------------------------------


class TestNoAsyncio:
    def test_does_not_import_asyncio(self):
        """Hook must not import asyncio -- it is fully synchronous."""
        import importlib
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_post_compact_check",
            HOOKS_DIR / "post_compact.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Do NOT exec the module to avoid side effects -- just check source
        src = (HOOKS_DIR / "post_compact.py").read_text()
        assert "import asyncio" not in src, "Hook must not import asyncio"
        assert "asyncio." not in src, "Hook must not use asyncio"


# ---------------------------------------------------------------------------
# Autouse fixture: clean up test sessions
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_agent_sessions():
    """Drop all AgentSession rows with test prefix after each test."""
    yield
    try:
        for s in AgentSession.query.all():
            try:
                if getattr(s, "project_key", "").startswith("test-postcompact"):
                    s.delete()
            except Exception:
                pass
    except Exception:
        pass
