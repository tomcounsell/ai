"""Tests for the generate_chapters PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.generate_chapters import (
    Chapter,
    ChapterList,
    generate_chapters,
)


class TestGenerateChapters:
    """Tests for generate_chapters service function."""

    def _make_mock_result(self, chapters: ChapterList) -> MagicMock:
        """Build a mock AgentRunResult with the given chapters."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 200

        mock_result = MagicMock()
        mock_result.output = chapters
        mock_result.usage.return_value = mock_usage
        return mock_result

    def test_returns_chapter_list(self):
        mock_chapters = ChapterList(
            chapters=[
                Chapter(
                    title="Introduction",
                    start_time="00:00",
                    summary="Episode opening",
                ),
                Chapter(
                    title="Main Topic",
                    start_time="05:30",
                    summary="Deep dive into the topic",
                ),
            ]
        )
        mock_result = self._make_mock_result(mock_chapters)

        with patch("apps.podcast.services.generate_chapters.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = generate_chapters("Some transcript", "Test Episode")

        assert isinstance(result, ChapterList)
        assert len(result.chapters) == 2
        assert result.chapters[0].title == "Introduction"
        assert result.chapters[1].start_time == "05:30"

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(ChapterList(chapters=[]))

        with patch("apps.podcast.services.generate_chapters.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            generate_chapters("My transcript text", "My Episode Title")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My Episode Title" in call_args
            assert "My transcript text" in call_args

    def test_logs_usage(self, caplog):
        mock_chapters = ChapterList(
            chapters=[
                Chapter(
                    title="Intro",
                    start_time="00:00",
                    summary="Opening",
                ),
            ]
        )
        mock_result = self._make_mock_result(mock_chapters)

        with patch("apps.podcast.services.generate_chapters.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"

            with caplog.at_level(logging.INFO):
                generate_chapters("transcript", "Episode")

        assert "generate_chapters" in caplog.text
        assert "input_tokens=1000" in caplog.text
        assert "output_tokens=200" in caplog.text

    def test_empty_chapters(self):
        mock_result = self._make_mock_result(ChapterList(chapters=[]))

        with patch("apps.podcast.services.generate_chapters.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = generate_chapters("Short transcript", "Short Episode")

        assert isinstance(result, ChapterList)
        assert len(result.chapters) == 0
