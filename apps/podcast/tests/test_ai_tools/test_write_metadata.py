"""Tests for the write_metadata PydanticAI tool."""

import logging
from unittest.mock import MagicMock, patch

from apps.podcast.services.write_metadata import (
    EpisodeMetadata,
    Resource,
    Timestamp,
    write_metadata,
)


class TestWriteMetadata:
    """Tests for write_metadata service function."""

    def _make_mock_result(self, metadata: EpisodeMetadata) -> MagicMock:
        """Build a mock AgentRunResult with the given metadata."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 1000
        mock_usage.output_tokens = 200

        mock_result = MagicMock()
        mock_result.output = metadata
        mock_result.usage.return_value = mock_usage
        return mock_result

    def _make_sample_metadata(self) -> EpisodeMetadata:
        return EpisodeMetadata(
            description="A deep dive into sleep science and its impact on health.",
            what_youll_learn=[
                "Discover the stages of sleep",
                "Learn how sleep affects memory",
                "Understand circadian rhythm optimization",
            ],
            key_timestamps=[
                Timestamp(time="00:00", description="Introduction"),
                Timestamp(time="05:30", description="Sleep stages explained"),
                Timestamp(time="15:00", description="Memory consolidation"),
            ],
            keywords=["sleep", "memory", "circadian", "health", "neuroscience"],
            resources=[
                Resource(
                    title="Why We Sleep",
                    url="https://example.com/book",
                    category="reading",
                    description="Comprehensive book on sleep science",
                ),
            ],
            primary_cta="Subscribe for weekly episodes on health science.",
            voiced_cta="If you enjoyed this episode, share it with a friend.",
        )

    def test_returns_episode_metadata(self):
        mock_metadata = self._make_sample_metadata()
        mock_result = self._make_mock_result(mock_metadata)

        with patch("apps.podcast.services.write_metadata.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = write_metadata(
                "Episode report", "Transcript text", "[]", "Sleep Science"
            )

        assert isinstance(result, EpisodeMetadata)
        assert "sleep science" in result.description.lower()
        assert len(result.what_youll_learn) == 3
        assert len(result.key_timestamps) == 3
        assert result.key_timestamps[0].time == "00:00"
        assert len(result.resources) == 1
        assert result.resources[0].category == "reading"

    def test_passes_correct_prompt(self):
        mock_result = self._make_mock_result(self._make_sample_metadata())

        with patch("apps.podcast.services.write_metadata.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            write_metadata("My report", "My transcript", '{"chapters": []}', "My Title")

            call_args = mock_agent.run_sync.call_args[0][0]
            assert "My report" in call_args
            assert "My transcript" in call_args
            assert '{"chapters": []}' in call_args
            assert "My Title" in call_args

    def test_logs_usage(self, caplog):
        mock_result = self._make_mock_result(self._make_sample_metadata())

        with patch("apps.podcast.services.write_metadata.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"

            with caplog.at_level(logging.INFO):
                write_metadata("report", "transcript", "[]", "Episode")

        assert "write_metadata" in caplog.text
        assert "input_tokens=1000" in caplog.text
        assert "output_tokens=200" in caplog.text

    def test_empty_metadata(self):
        empty_metadata = EpisodeMetadata(
            description="",
            what_youll_learn=[],
            key_timestamps=[],
            keywords=[],
            resources=[],
            primary_cta="",
            voiced_cta="",
        )
        mock_result = self._make_mock_result(empty_metadata)

        with patch("apps.podcast.services.write_metadata.agent") as mock_agent:
            mock_agent.run_sync.return_value = mock_result
            mock_agent.model = "anthropic:claude-sonnet-4-6"
            result = write_metadata("report", "transcript", "[]", "Episode")

        assert isinstance(result, EpisodeMetadata)
        assert len(result.what_youll_learn) == 0
        assert len(result.key_timestamps) == 0
