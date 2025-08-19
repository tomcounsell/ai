"""End-to-end tests for AI chat functionality."""

import json
import os
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.ai.agent.chat import ChatSession as AgentChatSession


class AITestChatE2ETestCase(TestCase):
    """E2E tests for the AI test chat endpoint."""

    def setUp(self):
        """Set up test fixtures."""
        self.test_chat_url = reverse("ai:test-chat")
        self.test_page_url = reverse("ai:test-page")

    def test_chat_page_loads(self):
        """Test that the chat page loads successfully."""
        response = self.client.get(self.test_page_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AI Chat Test")
        self.assertContains(response, "Send a message to start chatting")

    def test_chat_page_shows_api_key_warning_when_not_configured(self):
        """Test that the page shows a warning when API key is not configured."""
        with patch.dict(os.environ, {}, clear=True):
            response = self.client.get(self.test_page_url)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "Set OPENAI_API_KEY in your .env.local")

    @override_settings(OPENAI_API_KEY="test-key-123")
    def test_chat_page_hides_api_key_warning_when_configured(self):
        """Test that the page doesn't show warning when API key is configured."""
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key-123"}):
            response = self.client.get(self.test_page_url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, "Set OPENAI_API_KEY in your .env.local")

    def test_chat_endpoint_requires_message(self):
        """Test that the chat endpoint requires a message."""
        # Test with JSON
        response = self.client.post(
            self.test_chat_url, data=json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(response.content, {"error": "Message is required"})

        # Test with form data
        response = self.client.post(self.test_chat_url, data={})
        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(response.content, {"error": "Message is required"})

    def test_chat_endpoint_accepts_json(self):
        """Test that the chat endpoint accepts JSON data."""
        with patch("apps.ai.views.test_chat.process_chat_message_sync") as mock_process:
            mock_process.return_value = "Test AI response"

            response = self.client.post(
                self.test_chat_url,
                data=json.dumps({"message": "Hello AI"}),
                content_type="application/json",
            )

            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertEqual(data["message"], "Hello AI")
            self.assertEqual(data["response"], "Test AI response")

    def test_chat_endpoint_accepts_form_data(self):
        """Test that the chat endpoint accepts form data (HTMX)."""
        with patch("apps.ai.views.test_chat.process_chat_message_sync") as mock_process:
            mock_process.return_value = "Test AI response"

            response = self.client.post(
                self.test_chat_url, data={"message": "Hello from HTMX"}
            )

            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertEqual(data["message"], "Hello from HTMX")
            self.assertEqual(data["response"], "Test AI response")

    def test_chat_handles_missing_api_key_gracefully(self):
        """Test that the chat handles missing API key gracefully."""
        with patch("apps.ai.views.test_chat.get_openai_model") as mock_model:
            mock_model.side_effect = ValueError("OpenAI API key not found")

            response = self.client.post(self.test_chat_url, data={"message": "Hello"})

            self.assertEqual(response.status_code, 200)
            data = json.loads(response.content)
            self.assertIn("AI service not configured", data["response"])
            self.assertIn("OPENAI_API_KEY", data["response"])

    @patch("apps.ai.views.test_chat.get_openai_model")
    @patch("apps.ai.views.test_chat.process_chat_message_sync")
    def test_chat_integration_with_pydantic_ai(self, mock_process, mock_model):
        """Test the integration with PydanticAI agent."""
        # Mock the model and process function
        mock_model_instance = Mock()
        mock_model.return_value = mock_model_instance
        mock_process.return_value = "2 + 2 equals 4"

        response = self.client.post(
            self.test_chat_url, data={"message": "What is 2 + 2?"}
        )

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["message"], "What is 2 + 2?")
        self.assertEqual(data["response"], "2 + 2 equals 4")

        # Verify the agent was called correctly
        mock_process.assert_called_once()
        call_args = mock_process.call_args
        self.assertEqual(call_args[0][0], "What is 2 + 2?")  # message
        self.assertIsInstance(call_args[0][1], AgentChatSession)  # session

    def test_chat_maintains_session_context(self):
        """Test that the chat maintains context within a session."""
        with patch("apps.ai.views.test_chat.process_chat_message_sync") as mock_process:
            # First message
            mock_process.return_value = "Hello! How can I help you?"
            response1 = self.client.post(self.test_chat_url, data={"message": "Hello"})
            self.assertEqual(response1.status_code, 200)

            # Second message - should have same session
            mock_process.return_value = "Paris is the capital of France."
            response2 = self.client.post(
                self.test_chat_url, data={"message": "What is the capital of France?"}
            )
            self.assertEqual(response2.status_code, 200)

            # Verify session was reused (same session_id)
            calls = mock_process.call_args_list
            session1 = calls[0][0][1]
            session2 = calls[1][0][1]
            self.assertEqual(session1.session_id, session2.session_id)
            self.assertEqual(session1.session_id, "test-session")


class AIChagentUnitTestCase(TestCase):
    """Unit tests for the AI chat agent."""

    def test_process_chat_message_sync(self):
        """Test the sync process_chat_message function."""
        from apps.ai.agent.chat import ChatDependencies, process_chat_message_sync

        with patch("apps.ai.agent.chat.chat_agent.run") as mock_run:
            # Mock the agent response
            mock_result = Mock()
            mock_result.text = "Test response"

            # Make mock_run return a coroutine
            async def mock_async_run(*args, **kwargs):
                return mock_result

            mock_run.return_value = mock_async_run()

            # Create test session
            session = AgentChatSession(session_id="test-123", user_id=1, messages=[])

            # Process message synchronously
            response = process_chat_message_sync(
                "Test message",
                session,
                ChatDependencies(user_id=1, session_id="test-123"),
            )

            self.assertEqual(response, "Test response")
            self.assertEqual(len(session.messages), 2)  # user + assistant
            self.assertEqual(session.messages[0].content, "Test message")
            self.assertEqual(session.messages[1].content, "Test response")

    def test_chat_session_model(self):
        """Test the ChatSession model functionality."""
        session = AgentChatSession(session_id="test-session", user_id=1, messages=[])

        # Add messages
        session.add_message("user", "Hello")
        session.add_message("assistant", "Hi there!")

        self.assertEqual(len(session.messages), 2)
        self.assertEqual(session.messages[0].role, "user")
        self.assertEqual(session.messages[0].content, "Hello")
        self.assertEqual(session.messages[1].role, "assistant")
        self.assertEqual(session.messages[1].content, "Hi there!")

        # Test conversation history
        history = session.get_conversation_history()
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "Hello")


class AIModelProviderTestCase(TestCase):
    """Tests for the LLM provider configuration."""

    def test_get_openai_model_requires_api_key(self):
        """Test that get_openai_model requires an API key."""
        from apps.ai.llm.providers import get_openai_model

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError) as context:
                get_openai_model()

            self.assertIn("OpenAI API key not found", str(context.exception))

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    def test_get_openai_model_with_env_key(self):
        """Test getting OpenAI model with environment key."""
        from apps.ai.llm.providers import get_openai_model

        model = get_openai_model("gpt-4o-mini")
        self.assertIsNotNone(model)

    def test_get_openai_model_with_explicit_key(self):
        """Test getting OpenAI model with explicit key."""
        from apps.ai.llm.providers import get_openai_model

        with patch.dict(os.environ, {}, clear=True):
            model = get_openai_model("gpt-4o", api_key="explicit-key")
            self.assertIsNotNone(model)
            self.assertEqual(os.environ.get("OPENAI_API_KEY"), "explicit-key")
