"""Tests for the programmatic ``create_session`` core extracted from ``cmd_create`` (#2696).

The reflection's create rung calls this core directly rather than shelling out
to the CLI, so the core has to carry the guards the CLI used to own alone:

* the #1633 child-session stopgap, which must fire **before** any slug or
  project_key resolution, worktree provisioning, or enqueue — otherwise the new
  core is an ungated bypass of an active refusal;
* failures as return values (``CreateResult(success=False, error=...)``) rather
  than prints and exit codes, so a caller can tell "refused" from "crashed"
  without parsing stderr.

Persistence is fenced at ``_push_agent_session`` — no real Redis, no real
worktree.
"""

from __future__ import annotations

import pytest

from models.child_session_gate import BYPASS_ENV_VAR, CHILD_SESSIONS_DISABLED_MESSAGE
from tools import valor_session


@pytest.fixture
def spy(monkeypatch, tmp_path):
    """Fence every side effect ``create_session`` can have, and record it."""
    import agent.agent_session_queue as queue

    calls: dict[str, list] = {"push": [], "worktree": [], "resolve": []}

    async def fake_push(**kwargs):
        calls["push"].append(kwargs)

    def fake_worktree(repo_root, slug):
        calls["worktree"].append((repo_root, slug))
        return tmp_path / ".worktrees" / slug

    def fake_resolve(key):
        calls["resolve"].append(key)
        return (
            tmp_path,
            {
                "working_directory": str(tmp_path),
                "github": {"org": "tomcounsell", "repo": "ai"},
            },
        )

    monkeypatch.setattr(queue, "_push_agent_session", fake_push)
    monkeypatch.setattr(valor_session, "_check_worker_health", lambda: (True, 1.0))
    monkeypatch.setattr(valor_session, "_resolve_project_working_directory", fake_resolve)
    monkeypatch.setattr("agent.worktree_manager.get_or_create_worktree", fake_worktree)
    monkeypatch.setattr("agent.worktree_manager._validate_slug", lambda *_: None)
    return calls


class TestChildSessionStopgap:
    """A truthy ``parent_id`` must be refused before anything happens."""

    def test_parent_id_refused_with_the_canonical_message(self, spy, monkeypatch):
        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)

        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
            parent_id="parent-abc",
        )

        assert result.success is False
        assert result.error == CHILD_SESSIONS_DISABLED_MESSAGE

    def test_parent_id_refusal_has_zero_side_effects(self, spy, monkeypatch):
        """No project resolution, no worktree, nothing enqueued."""
        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)

        valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
            parent_id="parent-abc",
        )

        assert spy["resolve"] == []
        assert spy["worktree"] == []
        assert spy["push"] == []

    def test_parentless_creation_is_untouched_by_the_gate(self, spy, monkeypatch):
        monkeypatch.delenv(BYPASS_ENV_VAR, raising=False)

        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
        )

        assert result.success is True
        assert len(spy["push"]) == 1

    def test_bypass_env_lets_a_parent_attached_create_through(self, spy, monkeypatch):
        monkeypatch.setenv(BYPASS_ENV_VAR, "1")

        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
            parent_id="parent-abc",
        )

        assert result.success is True
        assert spy["push"][0]["parent_agent_session_id"] == "parent-abc"


class TestFailuresAreReturnValues:
    def test_worktree_provisioning_failure_enqueues_nothing(self, spy, monkeypatch):
        def boom(repo_root, slug):
            raise RuntimeError("worktree add failed: branch is checked out elsewhere")

        monkeypatch.setattr("agent.worktree_manager.get_or_create_worktree", boom)

        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
        )

        assert result.success is False
        assert "worktree add failed" in (result.error or "")
        assert spy["push"] == [], "a failed worktree must not leave a half-created session"

    def test_slugless_eng_session_is_refused(self, spy):
        """#1109 / #1272: an eng session without a slug would inherit the worker's branch."""
        result = valor_session.create_session(
            message="do some work with no issue reference",
            role="eng",
            slug=None,
            project_key="test-2696",
        )

        assert result.success is False
        assert "--slug" in (result.error or "")
        assert spy["push"] == []

    def test_role_and_session_type_must_agree(self, spy):
        """``role`` gates the slug requirement; ``session_type`` picks the SessionType.

        Left independent, ``role="teammate", session_type="eng"`` with no slug
        skips the slug requirement and lands an ENG session in the main
        checkout — the hole #1109/#1272 closed.
        """
        result = valor_session.create_session(
            message="do some work with no issue reference",
            role="teammate",
            session_type="eng",
            slug=None,
            project_key="test-2696",
        )

        assert result.success is False
        assert "disagree" in (result.error or "")
        assert spy["resolve"] == []
        assert spy["worktree"] == []
        assert spy["push"] == [], "a mismatch must refuse with zero side effects"

    def test_unknown_session_type_is_refused(self, spy):
        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
            session_type="wizard",
        )

        assert result.success is False
        assert "wizard" in (result.error or "")
        assert spy["push"] == []


class TestPayloadThreading:
    """The full ``_push_agent_session`` payload survives the extraction."""

    def test_sdlc_metadata_is_derived_from_the_message(self, spy):
        """The reflection's steer text names ``issue #N``, which is what makes this work."""
        result = valor_session.create_session(
            message=(
                "SDLC lane sdlc-2696 is stalled: PR #2700 on issue #2696, last commit 9h ago. "
                "Resume the pipeline: invoke /sdlc for issue #2696."
            ),
            slug="sdlc-2696",
            project_key="test-2696",
        )

        assert result.success is True
        pushed = spy["push"][0]
        assert pushed["classification_type"] == "sdlc"
        assert pushed["issue_url"] == "https://github.com/tomcounsell/ai/issues/2696"

    def test_slug_model_and_chrome_flag_are_threaded(self, spy):
        valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
            model="opus",
            requires_real_chrome=True,
            chat_id="0",
        )

        pushed = spy["push"][0]
        assert pushed["slug"] == "sdlc-2696"
        assert pushed["model"] == "opus"
        assert pushed["requires_real_chrome"] is True
        assert pushed["chat_id"] == "0"
        assert pushed["project_config"]["working_directory"]

    def test_auto_derives_the_slug_from_an_issue_reference(self, spy):
        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug=None,
            project_key="test-2696",
        )

        assert result.success is True
        assert spy["push"][0]["slug"] == "sdlc-2696"

    def test_progress_notes_are_returned_not_printed(self, spy, capsys):
        """Presentation belongs to ``cmd_create``; the core carries the text."""
        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug=None,
            project_key="test-2696",
        )

        assert capsys.readouterr().err == "", "the core must not write to stderr"
        assert any("Auto-derived slug: sdlc-2696" in n for n in result.notes)
        assert any("Worktree:" in n for n in result.notes)

    def test_sender_name_follows_session_type(self, spy):
        valor_session.create_session(
            message="Run /sdlc for issue #2696",
            role="teammate",
            session_type="teammate",
            slug="sdlc-2696",
            project_key="test-2696",
        )

        assert spy["push"][0]["sender_name"] == "valor-session (teammate)"

    def test_success_reports_worker_health_instead_of_printing_it(self, spy):
        result = valor_session.create_session(
            message="Run /sdlc for issue #2696",
            slug="sdlc-2696",
            project_key="test-2696",
        )

        assert result.worker_healthy is True
        assert result.worker_heartbeat_age_s == 1.0
        assert result.project_key == "test-2696"
        assert result.session_id
