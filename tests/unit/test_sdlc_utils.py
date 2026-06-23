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


class TestDeterministicIdVsIssueUrl:
    """C2 (#1671): issue_url ownership must beat a stale sdlc-local-{N}."""

    def test_live_bridge_session_wins_over_stale_local(self):
        """When both a stale sdlc-local-N record and a live bridge eng session
        owning the issue via issue_url exist, the bridge session wins."""
        from tools._sdlc_utils import find_session_by_issue

        stale_local = MagicMock(name="stale_local")
        stale_local.session_id = "sdlc-local-1147"
        stale_local.session_type = "eng"
        stale_local.issue_url = None
        stale_local.message_text = None

        live_bridge = MagicMock(name="live_bridge")
        live_bridge.session_id = "tg_valor_-100_42"
        live_bridge.session_type = "eng"
        live_bridge.issue_url = "https://github.com/tomcounsell/ai/issues/1147"
        live_bridge.message_text = None

        def _filter(**kwargs):
            # session_type="eng" pass returns both; session_id pass returns the local.
            if kwargs.get("session_type") == "eng":
                return [stale_local, live_bridge]
            if kwargs.get("session_id") == "sdlc-local-1147":
                return [stale_local]
            return []

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = _filter

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1147)

        # The issue_url pass runs FIRST, so the bridge session that owns the
        # issue wins over the stale deterministic-id record.
        assert result is live_bridge

    def test_deterministic_local_used_when_no_issue_url_owner(self):
        """When no eng session owns the issue via issue_url, the deterministic
        sdlc-local-N record is the fallback (preserves #1558)."""
        from tools._sdlc_utils import find_session_by_issue

        local = MagicMock(name="local")
        local.session_id = "sdlc-local-1148"
        local.session_type = "eng"
        local.issue_url = None
        local.message_text = None

        def _filter(**kwargs):
            if kwargs.get("session_type") == "eng":
                return [local]
            if kwargs.get("session_id") == "sdlc-local-1148":
                return [local]
            return []

        mock_as = MagicMock()
        mock_as.query.filter.side_effect = _filter

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            result = find_session_by_issue(1148)

        assert result is local


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

    def test_tracking_field_wins_over_incidental_mention(self, tmp_path, monkeypatch):
        """A plan that *tracks* the issue beats one that only mentions it.

        Regression for the find_plan_path mis-resolution that broke G5: an
        out-of-scope `#{issue}` cross-reference in another plan's No-Gos must
        never win over the plan whose `tracking:` frontmatter owns the issue,
        regardless of directory iteration order.
        """
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        # `aaa_other.md` sorts first and merely mentions #1712 as out-of-scope.
        self._write_plan(
            plans_dir,
            "aaa_other.md",
            "tracking: https://github.com/org/repo/issues/1721\n\n"
            "## No-Gos\n- [SEPARATE-SLUG #1712] separate concern, not in scope.\n",
        )
        # The real owner sorts later but carries the authoritative tracking field.
        owner = self._write_plan(
            plans_dir,
            "zzz_bridge.md",
            "tracking: https://github.com/org/repo/issues/1712\n\nbody\n",
        )

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(1712)

        assert result == owner

    def test_falls_back_to_mention_when_no_tracking_owner(self, tmp_path, monkeypatch):
        """When no plan's tracking field claims the issue, any textual ref still resolves."""
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(plans_dir, "feature.md", "relates to #4242\n")

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(4242)

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

    # ------------------------------------------------------------------
    # Tests for the _is_ai_repo_fallback bare-#N suppression (CONCERN 3)
    # ------------------------------------------------------------------

    def test_file_fallback_bare_mention_returns_none(self, tmp_path, monkeypatch):
        """CONCERN 3: bare-#N match from __file__ fallback path is suppressed.

        When SDLC_TARGET_REPO is unset and git resolution fails (i.e. we
        fall back to the __file__-relative ai-repo plans dir), a bare textual
        mention of the issue number must return None — not a foreign plan.
        """
        import tools._sdlc_utils as _utils
        from tools._sdlc_utils import find_plan_path

        # Point the __file__ fallback at our tmp plans dir so we can plant a
        # "foreign" plan that merely mentions the issue.
        plans_dir = tmp_path / "docs" / "plans"
        self._write_plan(plans_dir, "ai-plan.md", "No-Gos: see #9999 from other repo\n")

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        # Make git resolution fail so the __file__ fallback is used.
        with patch("tools._sdlc_utils._git_toplevel", return_value=None):
            # Redirect the __file__ fallback to tmp_path so the test is self-contained.
            monkeypatch.setattr(
                _utils,
                "__file__",
                str(tmp_path / "tools" / "_sdlc_utils.py"),
            )
            # Create the expected fallback path structure: __file__/../.. / docs/plans
            (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
            self._write_plan(
                tmp_path / "docs" / "plans",
                "ai-cross-ref.md",
                "No-Gos: see #9999 from another repo\n",
            )
            result = find_plan_path(9999)

        # The bare-#N fallback must be suppressed when using the __file__ path.
        assert result is None

    def test_file_fallback_tracking_match_still_returned(self, tmp_path, monkeypatch):
        """CONCERN 3: tracking: match is always authoritative, even on __file__ fallback.

        If somehow a plan in the ai-repo has a proper tracking: frontmatter
        for this issue, it IS the right plan and must be returned.
        """
        import tools._sdlc_utils as _utils
        from tools._sdlc_utils import find_plan_path

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        # Create a plan with a tracking: line in the fallback-path location.
        monkeypatch.setattr(
            _utils,
            "__file__",
            str(tmp_path / "tools" / "_sdlc_utils.py"),
        )
        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(
            plans_dir,
            "owned.md",
            "tracking: https://github.com/org/ai/issues/9998\n",
        )

        with patch("tools._sdlc_utils._git_toplevel", return_value=None):
            result = find_plan_path(9998)

        # tracking: match is authoritative regardless of resolution path.
        assert result == plan

    def test_sdlc_target_repo_bare_fallback_not_suppressed(self, tmp_path, monkeypatch):
        """CONCERN 3: when SDLC_TARGET_REPO is set, bare-#N fallback is kept.

        The suppression is ONLY for the __file__ ai-repo fallback.  When
        SDLC_TARGET_REPO points at the real target repo, a bare mention in
        that repo's plans is a legitimate textual reference.
        """
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(plans_dir, "feature.md", "relates to #7777\n")

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        result = find_plan_path(7777)

        assert result == plan

    def test_sdlc_target_repo_nonexistent_path_returns_none(self, tmp_path, monkeypatch):
        """When SDLC_TARGET_REPO points at a non-existent directory, return None."""
        from tools._sdlc_utils import find_plan_path

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path / "does-not-exist"))
        result = find_plan_path(1234)

        assert result is None

    def test_git_toplevel_bare_fallback_not_suppressed(self, tmp_path, monkeypatch):
        """CONCERN 3: when git-toplevel resolves (path 2), bare-#N fallback is kept.

        Only the __file__ fallback (path 3) suppresses the bare-#N match.
        Path 2 (git toplevel) is the correct repo and the fallback is legitimate.
        """
        from tools._sdlc_utils import find_plan_path

        plans_dir = tmp_path / "docs" / "plans"
        plan = self._write_plan(plans_dir, "feature.md", "see #5555 for context\n")

        monkeypatch.delenv("SDLC_TARGET_REPO", raising=False)
        with patch("tools._sdlc_utils._git_toplevel", return_value=tmp_path):
            result = find_plan_path(5555)

        assert result == plan


class TestFindSessionEnsure:
    """Tests for the ensure=True auto-create branch of find_session (#1558).

    Covers the resolver-boundary auto-ensure: writes-only opt-in, the create
    guards, env-session short-circuit (no create), and failure-yields-None.
    The default ensure=False path must remain a pure lookup with zero creates.
    """

    def test_default_ensure_false_no_create_returns_none(self, monkeypatch):
        """ensure=False (default) with a valid issue and no session → None, no create."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        ensure_mock = MagicMock()
        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
                result = _sdlc_utils.find_session(None, 1558)

        assert result is None
        ensure_mock.assert_not_called()

    def test_ensure_true_valid_issue_creates_and_returns(self, monkeypatch):
        """ensure=True with a valid issue and no existing session → ensure_session is
        called and the re-resolved session is returned."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        created = MagicMock(name="created_session")

        # find_session_by_issue: first lookup misses; the re-resolve after ensure
        # goes through the session_id env path (mocked AgentSession query below).
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [created]
        type(created).session_type = "eng"

        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch(
                "tools.sdlc_session_ensure.ensure_session",
                return_value={"session_id": "sdlc-local-1558", "created": True},
            ) as ensure_mock:
                with patch("tools._sdlc_utils.AgentSession", mock_as):
                    result = _sdlc_utils.find_session(None, 1558, ensure=True)

        ensure_mock.assert_called_once_with(1558)
        assert result is created

    def test_ensure_true_none_issue_no_env_no_create(self, monkeypatch):
        """ensure=True with issue_number=None and no env → no create, returns None."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        ensure_mock = MagicMock()
        with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
            result = _sdlc_utils.find_session(None, None, ensure=True)

        assert result is None
        ensure_mock.assert_not_called()

    def test_ensure_true_zero_issue_no_create(self, monkeypatch):
        """ensure=True with issue_number=0 → guarded out, no create, None."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        ensure_mock = MagicMock()
        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
                result = _sdlc_utils.find_session(None, 0, ensure=True)

        assert result is None
        ensure_mock.assert_not_called()

    def test_ensure_true_negative_issue_no_create(self, monkeypatch):
        """ensure=True with issue_number=-1 → guarded out, no create, None."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        ensure_mock = MagicMock()
        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
                result = _sdlc_utils.find_session(None, -1, ensure=True)

        assert result is None
        ensure_mock.assert_not_called()

    def test_issue_number_beats_env_session_on_write_path(self, monkeypatch):
        """#1671/#1672: with an explicit issue_number, the issue-scoped session
        wins over a divergent env-var session — even on the ensure=True write
        path. ensure_session is never reached because the issue lookup hits."""
        from tools import _sdlc_utils

        # Env var points at a DIFFERENT session (e.g. a parent's inherited id).
        monkeypatch.setenv("VALOR_SESSION_ID", "parent-pm-divergent")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        issue_session = MagicMock(name="issue_session")
        issue_session.session_type = "eng"

        ensure_mock = MagicMock()
        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=issue_session):
            with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
                result = _sdlc_utils.find_session(None, 1558, ensure=True)

        # Issue-scoped session wins; the divergent env session is NOT returned.
        assert result is issue_session
        ensure_mock.assert_not_called()

    def test_env_session_resolves_when_no_issue_number(self, monkeypatch):
        """Bridge case preserved: a write WITHOUT an issue number resolves the
        env-var session exactly as before (env is the fallback, not gone)."""
        from tools import _sdlc_utils

        monkeypatch.setenv("VALOR_SESSION_ID", "bridge-pm-1")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock(name="env_session")
        env_session.session_type = "eng"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [env_session]

        ensure_mock = MagicMock()
        with patch("tools._sdlc_utils.AgentSession", mock_as):
            with patch("tools.sdlc_session_ensure.ensure_session", ensure_mock):
                # No issue_number → env fallback resolves the bridge session.
                result = _sdlc_utils.find_session(None, None, ensure=True)

        assert result is env_session
        ensure_mock.assert_not_called()

    def test_explicit_session_id_arg_beats_issue_number(self, monkeypatch):
        """The explicit session_id ARGUMENT still overrides issue-based
        resolution (step 1 before step 2). Only the env-var session loses to
        an issue number, not an explicitly-passed id."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        explicit_session = MagicMock(name="explicit_session")
        explicit_session.session_type = "eng"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [explicit_session]

        # find_session_by_issue would return a DIFFERENT session — must not win.
        issue_session = MagicMock(name="issue_session")
        with patch("tools._sdlc_utils.AgentSession", mock_as):
            with patch.object(_sdlc_utils, "find_session_by_issue", return_value=issue_session):
                result = _sdlc_utils.find_session("explicit-id-123", 1558)

        assert result is explicit_session

    def test_issue_number_zero_skips_issue_pass_uses_env(self, monkeypatch):
        """issue_number=0 must NOT trigger the issue-first pass (gated on >= 1);
        resolution falls straight to the env-var session."""
        from tools import _sdlc_utils

        monkeypatch.setenv("VALOR_SESSION_ID", "env-pm")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock(name="env_session")
        env_session.session_type = "eng"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [env_session]

        fsbi = MagicMock()
        with patch("tools._sdlc_utils.AgentSession", mock_as):
            with patch.object(_sdlc_utils, "find_session_by_issue", fsbi):
                result = _sdlc_utils.find_session(None, 0)

        assert result is env_session
        fsbi.assert_not_called()

    def test_issue_number_none_skips_issue_pass_uses_env(self, monkeypatch):
        """issue_number=None must NOT trigger the issue-first pass; falls to env."""
        from tools import _sdlc_utils

        monkeypatch.setenv("VALOR_SESSION_ID", "env-pm")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock(name="env_session")
        env_session.session_type = "eng"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [env_session]

        fsbi = MagicMock()
        with patch("tools._sdlc_utils.AgentSession", mock_as):
            with patch.object(_sdlc_utils, "find_session_by_issue", fsbi):
                result = _sdlc_utils.find_session(None, None)

        assert result is env_session
        fsbi.assert_not_called()

    def test_issue_first_pass_raises_falls_through_to_env(self, monkeypatch):
        """If find_session_by_issue raises, resolution falls through to the
        env-var pass (observable: env session returned, not an exception)."""
        from tools import _sdlc_utils

        monkeypatch.setenv("VALOR_SESSION_ID", "env-pm")
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        env_session = MagicMock(name="env_session")
        env_session.session_type = "eng"
        mock_as = MagicMock()
        mock_as.query.filter.return_value = [env_session]

        with patch("tools._sdlc_utils.AgentSession", mock_as):
            with patch.object(
                _sdlc_utils,
                "find_session_by_issue",
                side_effect=ConnectionError("Redis down"),
            ):
                result = _sdlc_utils.find_session(None, 1558)

        assert result is env_session

    def test_ensure_raises_yields_none(self, monkeypatch):
        """If ensure_session raises, find_session swallows it and returns None."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch(
                "tools.sdlc_session_ensure.ensure_session",
                side_effect=RuntimeError("boom"),
            ):
                result = _sdlc_utils.find_session(None, 1558, ensure=True)

        assert result is None

    def test_ensure_returns_empty_dict_yields_none(self, monkeypatch):
        """If ensure_session returns {} (failed resolve), find_session returns None."""
        from tools import _sdlc_utils

        monkeypatch.delenv("VALOR_SESSION_ID", raising=False)
        monkeypatch.delenv("AGENT_SESSION_ID", raising=False)

        with patch.object(_sdlc_utils, "find_session_by_issue", return_value=None):
            with patch("tools.sdlc_session_ensure.ensure_session", return_value={}):
                result = _sdlc_utils.find_session(None, 1558, ensure=True)

        assert result is None


class TestSessionOwnsIssue:
    """Unit tests for session_owns_issue — all three predicates, edge cases, robustness."""

    class _Session:
        """Minimal session object with the three attributes session_owns_issue reads."""

        def __init__(self, *, issue_url=None, session_id="", message_text=""):
            self.issue_url = issue_url
            self.session_id = session_id
            self.message_text = message_text

    def test_returns_false_for_none_session(self):
        from tools._sdlc_utils import session_owns_issue

        assert session_owns_issue(None, 42) is False

    def test_returns_false_for_none_issue_number(self):
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(issue_url="https://github.com/x/y/issues/42")
        assert session_owns_issue(session, None) is False

    def test_returns_false_for_zero_issue_number(self):
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(issue_url="https://github.com/x/y/issues/42")
        assert session_owns_issue(session, 0) is False

    def test_predicate1_issue_url_match(self):
        """Predicate 1: issue_url ends with /issues/{issue_number}."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(issue_url="https://github.com/x/y/issues/42")
        assert session_owns_issue(session, 42) is True

    def test_predicate1_issue_url_no_match(self):
        """Predicate 1: issue_url ends with a different issue number."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(issue_url="https://github.com/x/y/issues/99")
        assert session_owns_issue(session, 42) is False

    def test_predicate2_sdlc_local_match(self):
        """Predicate 2: session_id == 'sdlc-local-{issue_number}'."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(session_id="sdlc-local-42")
        assert session_owns_issue(session, 42) is True

    def test_predicate2_sdlc_local_no_match(self):
        """Predicate 2: session_id is a different sdlc-local record."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(session_id="sdlc-local-99")
        assert session_owns_issue(session, 42) is False

    def test_predicate3_message_text_match_basic(self):
        """Predicate 3: message_text contains 'issue #42'."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(message_text="SDLC issue #42: fix bug")
        assert session_owns_issue(session, 42) is True

    def test_predicate3_message_text_match_no_hash(self):
        """Predicate 3: message_text contains 'issue 42' (no hash)."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(message_text="issue 42 is the one")
        assert session_owns_issue(session, 42) is True

    def test_predicate3_message_text_match_case_insensitive(self):
        """Predicate 3: match is case-insensitive (uppercase 'Issue')."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(message_text="Issue #42")
        assert session_owns_issue(session, 42) is True

    def test_predicate3_word_boundary_tissue(self):
        """Predicate 3: 'tissue 42' must NOT match — word boundary on left."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(message_text="tissue 42")
        assert session_owns_issue(session, 42) is False

    def test_predicate3_word_boundary_issue_with_suffix(self):
        """Predicate 3: 'issue 427' must NOT match for issue 42 — word boundary on right."""
        from tools._sdlc_utils import session_owns_issue

        session = self._Session(message_text="issue 427 other")
        assert session_owns_issue(session, 42) is False

    def test_no_raise_on_malformed_session(self):
        """A session that raises on attribute access must return False, never propagate."""
        from unittest.mock import Mock

        from tools._sdlc_utils import session_owns_issue

        bad_session = Mock(spec=[])  # no attributes — any getattr raises AttributeError
        # Must not raise; graceful False return.
        result = session_owns_issue(bad_session, 42)
        assert result is False
