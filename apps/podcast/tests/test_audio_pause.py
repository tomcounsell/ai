"""Tests for step_audio_generation pausing instead of generating audio."""

from __future__ import annotations

from django.test import TestCase

from apps.podcast.models import Episode, EpisodeWorkflow, Podcast
from apps.podcast.tasks import step_audio_generation


class TestStepAudioGenerationPause(TestCase):
    """Verify step_audio_generation pauses the workflow for a local worker."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode",
        )
        self.workflow = EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Audio Generation",
            status="running",
            history=[
                {
                    "step": "Episode Planning",
                    "status": "completed",
                    "started_at": "2026-01-01T00:00:00",
                    "completed_at": "2026-01-01T01:00:00",
                    "error": None,
                },
                {
                    "step": "Audio Generation",
                    "status": "queued",
                    "started_at": "2026-01-01T01:00:00",
                    "completed_at": None,
                    "error": None,
                },
            ],
        )

    def test_pauses_workflow_for_audio_generation(self):
        """step_audio_generation sets status to paused_for_human."""
        step_audio_generation.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "paused_for_human"
        assert self.workflow.blocked_on == "audio_generation"

    def test_does_not_advance_workflow(self):
        """step_audio_generation does NOT advance to the next step."""
        step_audio_generation.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Audio Generation"

    def test_fails_on_wrong_step(self):
        """Raises ValueError if workflow is not at Audio Generation."""
        self.workflow.current_step = "Synthesis"
        self.workflow.save()

        with self.assertRaises(ValueError):
            step_audio_generation.call(self.episode.id)
