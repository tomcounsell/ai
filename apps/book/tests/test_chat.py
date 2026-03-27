"""Tests for the book chat agent configuration."""

from apps.book.chat import VALOR_SYSTEM_PROMPT, BookChatDeps, book_chat_agent


class TestBookChatAgent:
    def test_agent_exists(self):
        assert book_chat_agent is not None

    def test_agent_uses_anthropic_model(self):
        # The agent is configured with an Anthropic model
        assert "anthropic" in str(book_chat_agent.model)

    def test_system_prompt_contains_valor_identity(self):
        assert "Valor Engels" in VALOR_SYSTEM_PROMPT
        assert "Blended Workforce 2026" in VALOR_SYSTEM_PROMPT
        assert "Tom Counsell" in VALOR_SYSTEM_PROMPT

    def test_system_prompt_sets_boundaries(self):
        assert "NOT a general-purpose assistant" in VALOR_SYSTEM_PROMPT

    def test_deps_defaults(self):
        deps = BookChatDeps()
        assert deps.session_messages == []

    def test_deps_with_history(self):
        history = [{"role": "user", "content": "hi"}]
        deps = BookChatDeps(session_messages=history)
        assert len(deps.session_messages) == 1


class TestLoopsIntegration:
    """Test that the Loops welcome email shortcut exists and is callable."""

    def test_send_early_reader_welcome_email_importable(self):
        from apps.integration.loops.shortcuts import send_early_reader_welcome_email

        assert callable(send_early_reader_welcome_email)
