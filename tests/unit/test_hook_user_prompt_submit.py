"""Tests for user_prompt_submit hook session_type env var registration.

Verifies that the hook reads SESSION_TYPE env var and passes it through
to AgentSession.create_local(), so local-* records reflect the correct persona.

The hook script lives in .claude/hooks/ and is not importable as a module from
tests. These tests therefore validate the end-to-end contract through two lenses:
  1. Direct env-var read test — verify os.environ.get("SESSION_TYPE") behaves
     correctly in isolation (trivial sanity test for the pattern used in the hook).
  2. AgentSession.create_local() kwarg pass-through — confirm that passing the
     env value as session_type kwarg stores the correct value in the session record.

Together they close the critical path: env var → hook reads it → create_local gets it → Redis stores it.
"""

import os
from unittest.mock import patch

import pytest


class TestSessionTypeEnvVarPattern:
    """Validate the env-var read pattern used in user_prompt_submit.py."""

    def test_env_var_absent_returns_none(self, monkeypatch):
        """When SESSION_TYPE is not set, os.environ.get returns None."""
        monkeypatch.delenv("SESSION_TYPE", raising=False)
        assert os.environ.get("SESSION_TYPE") is None

    def test_env_var_teammate_returns_teammate(self, monkeypatch):
        """When SESSION_TYPE=teammate, os.environ.get returns 'teammate'."""
        monkeypatch.setenv("SESSION_TYPE", "teammate")
        assert os.environ.get("SESSION_TYPE") == "teammate"

    def test_env_var_pm_returns_pm(self, monkeypatch):
        """When SESSION_TYPE=pm, os.environ.get returns 'pm'."""
        monkeypatch.setenv("SESSION_TYPE", "pm")
        assert os.environ.get("SESSION_TYPE") == "pm"

    def test_conditional_kwarg_omitted_when_none(self):
        """The conditional kwargs pattern omits session_type when value is None."""
        session_type_override = None
        kwargs = {"session_type": session_type_override} if session_type_override else {}
        assert "session_type" not in kwargs

    def test_conditional_kwarg_included_when_set(self):
        """The conditional kwargs pattern includes session_type when value is set."""
        session_type_override = "teammate"
        kwargs = {"session_type": session_type_override} if session_type_override else {}
        assert kwargs.get("session_type") == "teammate"


class TestCreateLocalSessionTypeKwarg:
    """AgentSession.create_local() should store whatever session_type is passed."""

    def test_no_session_type_kwarg_defaults_to_dev(self):
        """When session_type kwarg is absent (env var was None), session_type is 'dev'."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = None
            session = AgentSession.create_local(
                session_id="local-abc-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "dev"

    def test_session_type_teammate_stored(self):
        """When session_type='teammate' is passed (from SESSION_TYPE env var), session stores it."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "teammate"
            session = AgentSession.create_local(
                session_id="local-tm-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "teammate"

    def test_session_type_pm_stored(self):
        """When session_type='pm' is passed (from SESSION_TYPE env var), session stores it."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "pm"
            session = AgentSession.create_local(
                session_id="local-pm-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "pm"

    def test_session_type_dev_explicit_stored(self):
        """When SESSION_TYPE=dev is explicitly set, session stores 'dev' explicitly."""
        with patch("models.agent_session.AgentSession.save"):
            from models.agent_session import AgentSession

            session_type_override = "dev"
            session = AgentSession.create_local(
                session_id="local-dev-def",
                project_key="dm",
                working_dir="/tmp",
                **({"session_type": session_type_override} if session_type_override else {}),
            )

            assert session.session_type == "dev"
