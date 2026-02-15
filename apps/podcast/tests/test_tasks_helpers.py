"""Tests for task helper functions in apps.podcast.tasks."""

from __future__ import annotations

import pytest
from django.test import TestCase

from apps.podcast.models import (
    Episode,
    EpisodeArtifact,
    Podcast,
)
from apps.podcast.tasks import _get_crafted_prompt


class TestGetCraftedPrompt(TestCase):
    """Tests for _get_crafted_prompt artifact-based prompt reading."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
        )

    def test_reads_prompt_from_artifact(self):
        """Returns content from the named prompt artifact."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="prompt-gpt",
            content="Research the industry adoption of...",
        )
        result = _get_crafted_prompt(self.episode.id, "prompt-gpt")
        assert result == "Research the industry adoption of..."

    def test_reads_gemini_prompt(self):
        """Works for different prompt artifact titles."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="prompt-gemini",
            content="Analyze policy frameworks for...",
        )
        result = _get_crafted_prompt(self.episode.id, "prompt-gemini")
        assert result == "Analyze policy frameworks for..."

    def test_raises_on_missing_artifact(self):
        """Raises DoesNotExist when no matching artifact exists."""
        with pytest.raises(EpisodeArtifact.DoesNotExist):
            _get_crafted_prompt(self.episode.id, "prompt-gpt")

    def test_raises_on_empty_content(self):
        """Raises ValueError when artifact exists but has no content."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="prompt-gpt",
            content="",
        )
        with pytest.raises(ValueError, match="has no content"):
            _get_crafted_prompt(self.episode.id, "prompt-gpt")
