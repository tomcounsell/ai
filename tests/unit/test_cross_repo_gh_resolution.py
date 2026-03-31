"""Behavioral tests for cross-repo GH_REPO env var injection (issue #375).

These tests verify that the GH_REPO environment variable is correctly set
in the subprocess environment for cross-repo SDLC requests, replacing the
unreliable approach of relying on LLM markdown instructions.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# AI_REPO_ROOT as defined in sdk_client.py
AI_REPO_ROOT = str(Path(__file__).parent.parent.parent)


class TestValorAgentGhRepo:
    """Test GH_REPO env var injection in ValorAgent._create_options()."""

    def _make_agent(self, gh_repo=None):
        """Create a ValorAgent with minimal config for testing."""
        with patch("agent.sdk_client.load_system_prompt", return_value="test prompt"):
            from agent.sdk_client import ValorAgent

            return ValorAgent(
                working_dir=AI_REPO_ROOT,
                gh_repo=gh_repo,
            )

    def test_gh_repo_set_in_env_when_provided(self):
        """GH_REPO should appear in env dict when gh_repo is a valid org/repo string."""
        agent = self._make_agent(gh_repo="tomcounsell/popoto")
        options = agent._create_options()
        assert options.env.get("GH_REPO") == "tomcounsell/popoto"

    def test_gh_repo_not_set_when_none(self):
        """GH_REPO should NOT appear in env dict when gh_repo is None (default)."""
        agent = self._make_agent(gh_repo=None)
        options = agent._create_options()
        assert "GH_REPO" not in options.env

    def test_gh_repo_not_set_when_empty_string(self):
        """GH_REPO should NOT appear in env dict when gh_repo is an empty string."""
        agent = self._make_agent(gh_repo="")
        options = agent._create_options()
        assert "GH_REPO" not in options.env


class TestGetAgentResponseSdkGhRepo:
    """Test that get_agent_response_sdk() passes gh_repo to ValorAgent for cross-repo SDLC."""

    POPOTO_PROJECT = {
        "name": "Popoto",
        "working_directory": str(Path.home() / "src/popoto"),
        "github": {"org": "tomcounsell", "repo": "popoto"},
    }

    AI_PROJECT = {
        "name": "Valor AI",
        "working_directory": AI_REPO_ROOT,
        "github": {"org": "tomcounsell", "repo": "ai"},
    }

    NO_GITHUB_PROJECT = {
        "name": "NoGithub",
        "working_directory": str(Path.home() / "src/nogithub"),
    }

    @pytest.fixture
    def mock_agent_class(self):
        """Patch ValorAgent to capture constructor args."""
        with patch("agent.sdk_client.ValorAgent") as mock_cls:
            mock_instance = MagicMock()
            mock_instance.query = AsyncMock(return_value="test response")
            mock_cls.return_value = mock_instance
            yield mock_cls

    @pytest.fixture
    def mock_dependencies(self):
        """Patch all dependencies of get_agent_response_sdk.

        Since classification is now read from the session (not re-classified),
        we mock AgentSession.query.filter to return a session with the
        desired classification_type.
        """
        # Create a mock session that returns "sdlc" classification
        mock_session = MagicMock()
        mock_session.classification_type = "sdlc"
        mock_session.status = "running"
        mock_session.created_at = 1000.0

        patches = {
            "classify": patch(
                "models.agent_session.AgentSession.query",
                **{"filter.return_value": [mock_session]},
            ),
            "context": patch(
                "bridge.context.build_context_prefix",
                return_value="CONTEXT",
            ),
            "pm_prompt": patch(
                "agent.sdk_client.load_pm_system_prompt",
                return_value=None,
            ),
        }
        started = {k: p.start() for k, p in patches.items()}
        # Store mock_session for tests that need to change classification
        started["_mock_session"] = mock_session
        yield started
        for p in patches.values():
            p.stop()

    @pytest.mark.asyncio
    async def test_cross_repo_sdlc_sets_gh_repo(self, mock_agent_class, mock_dependencies):
        """When classification=sdlc and project is cross-repo, gh_repo should be set."""
        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="SDLC issue 193",
            session_id="test-session-1",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            chat_id="123",
        )

        call_kwargs = mock_agent_class.call_args[1]
        assert call_kwargs["gh_repo"] == "tomcounsell/popoto"

    @pytest.mark.asyncio
    async def test_ai_repo_sdlc_does_not_set_gh_repo(self, mock_agent_class, mock_dependencies):
        """When project is the ai repo itself, gh_repo should NOT be set."""
        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="SDLC issue 42",
            session_id="test-session-2",
            sender_name="Valor",
            chat_title="Dev: Valor",
            project=self.AI_PROJECT,
            chat_id="456",
        )

        call_kwargs = mock_agent_class.call_args[1]
        assert call_kwargs["gh_repo"] is None

    @pytest.mark.asyncio
    async def test_non_sdlc_classification_does_not_set_gh_repo(
        self, mock_agent_class, mock_dependencies
    ):
        """When classification is not sdlc (e.g., question), gh_repo should NOT be set."""
        mock_dependencies["_mock_session"].classification_type = "question"

        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="What is popoto?",
            session_id="test-session-3",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            chat_id="123",
        )

        call_kwargs = mock_agent_class.call_args[1]
        assert call_kwargs["gh_repo"] is None

    @pytest.mark.asyncio
    async def test_pm_mode_does_not_set_gh_repo(self, mock_agent_class, mock_dependencies):
        """PM mode projects should never set gh_repo regardless of classification."""
        pm_project = {
            **self.POPOTO_PROJECT,
            "mode": "pm",
        }

        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="SDLC issue 193",
            session_id="test-session-4",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=pm_project,
            chat_id="123",
        )

        call_kwargs = mock_agent_class.call_args[1]
        assert call_kwargs["gh_repo"] is None

    @pytest.mark.asyncio
    async def test_project_without_github_config_does_not_crash(
        self, mock_agent_class, mock_dependencies
    ):
        """Projects without a github config key should not crash and should not set gh_repo."""
        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="SDLC issue 1",
            session_id="test-session-5",
            sender_name="Tom",
            chat_title="Dev: NoGithub",
            project=self.NO_GITHUB_PROJECT,
            chat_id="789",
        )

        call_kwargs = mock_agent_class.call_args[1]
        assert call_kwargs["gh_repo"] is None

    @pytest.mark.asyncio
    async def test_enriched_message_still_contains_github_line(
        self, mock_agent_class, mock_dependencies
    ):
        """The enriched message text should still contain the GITHUB: line as a safety net."""
        from agent.sdk_client import get_agent_response_sdk

        await get_agent_response_sdk(
            message="SDLC issue 193",
            session_id="test-session-6",
            sender_name="Tom",
            chat_title="Dev: Popoto",
            project=self.POPOTO_PROJECT,
            chat_id="123",
        )

        # The first positional arg to agent.query() is the enriched message
        query_call = mock_agent_class.return_value.query
        enriched_msg = query_call.call_args[0][0]
        assert "GITHUB: tomcounsell/popoto" in enriched_msg


class TestExecuteJobPassesFullProjectConfig:
    """Test that _execute_agent_session passes the full project config (including github key)
    to get_agent_response_sdk, not a minimal dict.

    Regression test: _execute_agent_session previously constructed a minimal project_config
    with only _key/working_directory/name, missing the github config needed for
    GH_REPO env var injection.
    """

    def test_execute_agent_session_uses_registered_project_config(self):
        """get_project_config should return full config including github key."""
        from agent.agent_session_queue import get_project_config, register_project_config

        full_config = {
            "name": "Popoto",
            "working_directory": str(Path.home() / "src/popoto"),
            "_key": "popoto",
            "github": {"org": "tomcounsell", "repo": "popoto"},
            "telegram": {"groups": ["Dev: Popoto"]},
        }
        register_project_config("popoto", full_config)

        retrieved = get_project_config("popoto")
        assert retrieved.get("github") == {"org": "tomcounsell", "repo": "popoto"}
        assert retrieved.get("working_directory") == str(Path.home() / "src/popoto")

    def test_unregistered_project_returns_empty(self):
        """Unregistered project key returns empty dict (fallback to minimal config)."""
        from agent.agent_session_queue import get_project_config

        assert get_project_config("nonexistent-project-xyz") == {}
