"""Unit tests for tools._sdlc_utils shared session lookup.

Tests cover:
- find_session_by_issue matching PM sessions by issue URL suffix
- Returns None when no match
- Handles invalid input (0, negative, None)
- Handles Redis errors gracefully
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


class TestFindSessionByIssue:
    """Tests for the shared find_session_by_issue function."""

    def test_finds_matching_pm_session(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/941"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result == mock_session

    def test_returns_none_when_no_match(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = "https://github.com/tomcounsell/ai/issues/999"

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None

    def test_returns_none_for_zero(self):
        from tools._sdlc_utils import find_session_by_issue

        result = find_session_by_issue(0)
        assert result is None

    def test_returns_none_for_negative(self):
        from tools._sdlc_utils import find_session_by_issue

        result = find_session_by_issue(-1)
        assert result is None

    def test_handles_redis_error_gracefully(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = ConnectionError("Redis down")

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None

    def test_handles_session_without_issue_url(self):
        from tools._sdlc_utils import find_session_by_issue

        mock_session = MagicMock()
        mock_session.issue_url = None
        # Also ensure message_text does not accidentally match.
        mock_session.message_text = ""

        mock_as = MagicMock()
        mock_as.query.filter.return_value = [mock_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(941)

        assert result is None


class TestMessageTextFallback:
    """Tests for the message_text fallback pass in find_session_by_issue."""

    def _session(self, *, issue_url=None, message_text=None):
        s = MagicMock()
        s.issue_url = issue_url
        s.message_text = message_text
        return s

    def test_matches_sdlc_issue_phrase(self):
        from tools._sdlc_utils import find_session_by_issue

        bridge = self._session(message_text="SDLC issue 1147")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is bridge

    def test_matches_issue_hash(self):
        from tools._sdlc_utils import find_session_by_issue

        bridge = self._session(message_text="please work on issue #1147 today")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is bridge

    def test_case_insensitive(self):
        from tools._sdlc_utils import find_session_by_issue

        bridge = self._session(message_text="ISSUE 1147 is urgent")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [bridge]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is bridge

    def test_word_boundary_rejects_tissue(self):
        """'tissue 1147' must NOT match — word boundary protection."""
        from tools._sdlc_utils import find_session_by_issue

        decoy = self._session(message_text="tissue 1147 sample count")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [decoy]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is None

    def test_does_not_match_different_number(self):
        from tools._sdlc_utils import find_session_by_issue

        other = self._session(message_text="SDLC issue 1140")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [other]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is None

    def test_none_message_text_does_not_match(self):
        from tools._sdlc_utils import find_session_by_issue

        s = self._session(message_text=None)
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is None

    def test_empty_message_text_does_not_match(self):
        from tools._sdlc_utils import find_session_by_issue

        s = self._session(message_text="")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [s]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        assert result is None

    def test_issue_url_priority_over_message_text(self):
        """If both could match, the issue_url match wins (preserves priority)."""
        from tools._sdlc_utils import find_session_by_issue

        # In query order: first has message_text match only, second has issue_url.
        text_match = self._session(message_text="SDLC issue 1147")
        url_match = self._session(issue_url="https://github.com/tomcounsell/ai/issues/1147")
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [text_match, url_match]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        # The url_match must win because the issue_url pass runs first across
        # the whole list before the message_text fallback pass begins.
        assert result is url_match


class TestFindPlanPath:
    """Tests for find_plan_path portability + tracking-URL matching (D1, D2)."""

    @staticmethod
    def _write_plan(plans_dir, name, body):
        plans_dir.mkdir(parents=True, exist_ok=True)
        p = plans_dir / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_resolves_from_cwd_git_root_no_env(self, tmp_path, monkeypatch):
        """D1: with no SDLC_TARGET_REPO, the plans dir comes from the cwd git root."""
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(plans_dir, "feature.md", "tracking: #4242\n")

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(4242)

        assert result == plan

    def test_env_var_overrides_git_root(self, tmp_path, monkeypatch):
        """D1: SDLC_TARGET_REPO wins over the cwd git root (override semantics)."""
        from tools._sdlc_utils import find_plan_path

        env_repo = tmp_path / "envrepo"
        git_repo = tmp_path / "gitrepo"
        env_plan = self._write_plan(env_repo / "docs" / "plans", "e.md", "#4242\n")
        self._write_plan(git_repo / "docs" / "plans", "g.md", "#4242\n")

        monkeypatch.setenv("SDLC_TARGET_REPO", str(env_repo))
        with patch("tools._sdlc_utils._git_toplevel", return_value=git_repo):
            result = find_plan_path(4242)

        assert result == env_plan

    def test_git_failure_falls_through_to_file_fallback(self, tmp_path, monkeypatch):
        """D1: when git resolution fails and no env var, fall to __file__ fallback (no crash)."""
        from tools._sdlc_utils import find_plan_path

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        # _git_toplevel returns None (not a repo / git missing); the __file__
        # fallback dir is the real repo, which won't contain issue 999999999.
        with patch("tools._sdlc_utils._git_toplevel", return_value=None):
            result = find_plan_path(999999999)

        assert result is None  # no crash, clean miss

    def test_matches_tracking_url_form(self, tmp_path, monkeypatch):
        """D2: a plan referencing the issue only by tracking URL is found."""
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(
            plans_dir,
            "url.md",
            "tracking: https://github.com/org/repo/issues/145\n",
        )

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(145)

        assert result == plan

    def test_boundary_1455_does_not_match_145(self, tmp_path, monkeypatch):
        """D2: #1455 must not satisfy a lookup for issue 145."""
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        self._write_plan(plans_dir, "other.md", "see #1455 and issues/1455\n")

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(145)

        assert result is None

    def test_git_toplevel_handles_non_repo(self, tmp_path):
        """_git_toplevel returns None outside a git repo rather than raising."""
        from tools._sdlc_utils import _git_toplevel

        # tmp_path is not inside a git repo (pytest tmp dirs are not under VCS).
        result = _git_toplevel(cwd=tmp_path)
        assert result is None or isinstance(result, os.PathLike)
