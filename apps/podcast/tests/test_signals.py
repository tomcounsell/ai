"""Tests for podcast signal-based fan-in logic."""

from __future__ import annotations

from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, Podcast
from apps.podcast.signals import _check_targeted_research_complete


class TestCheckTargetedResearchComplete(TestCase):
    """Tests for _check_targeted_research_complete fan-in logic."""

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

    def test_false_when_no_targeted_artifacts(self):
        """No p2-* artifacts (except perplexity) means not complete."""
        # Only p2-perplexity exists — should be excluded
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-perplexity",
            content="Perplexity research results",
        )
        assert _check_targeted_research_complete(self.episode.id) is False

    def test_false_when_targeted_artifacts_empty(self):
        """Placeholder artifacts with empty content are not complete."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content=""
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content=""
        )
        assert _check_targeted_research_complete(self.episode.id) is False

    def test_false_when_only_one_has_content(self):
        """One complete, one empty — not complete."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="GPT results"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content=""
        )
        assert _check_targeted_research_complete(self.episode.id) is False

    def test_true_when_all_have_content(self):
        """All targeted artifacts populated — complete."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="GPT results"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content="Gemini results"
        )
        assert _check_targeted_research_complete(self.episode.id) is True

    def test_perplexity_excluded(self):
        """p2-perplexity is excluded from the targeted check."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-perplexity", content="Perplexity"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="GPT"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content="Gemini"
        )
        assert _check_targeted_research_complete(self.episode.id) is True

    def test_dynamic_with_extra_tool(self):
        """Works with any number of p2-* artifacts, not just chatgpt/gemini."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="GPT"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content="Gemini"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-custom-tool", content=""
        )
        # Third artifact is empty, so not complete
        assert _check_targeted_research_complete(self.episode.id) is False
