"""Tests for targeted research resilience: per-source error capture, threshold
fan-in, fail_research_source, SubStep status, and retry endpoint.

Covers the fixes for issue #176: targeted research tasks that fail silently,
causing the workflow to get stuck.
"""

from __future__ import annotations

from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast
from apps.podcast.services.workflow import fail_research_source, fail_step
from apps.podcast.services.workflow_progress import (
    SubStep,
    _resolve_substep_status,
    compute_workflow_progress,
)
from apps.podcast.signals import _check_targeted_research_complete


class TestFailResearchSource(TestCase):
    """Tests for the fail_research_source() helper."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-frs",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode-frs",
        )
        self.wf = EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Targeted Research",
            status="running",
            history=[{"step": "Targeted Research", "status": "started"}],
        )
        # Create placeholder artifact
        self.artifact = EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-chatgpt",
            content="",
        )

    def test_writes_failed_content_to_artifact(self):
        """fail_research_source writes [FAILED: ...] to artifact content."""
        fail_research_source(self.episode.pk, "p2-chatgpt", "API timeout")
        self.artifact.refresh_from_db()
        self.assertEqual(self.artifact.content, "[FAILED: API timeout]")

    def test_stores_error_in_metadata(self):
        """fail_research_source stores error and failed_at in metadata."""
        fail_research_source(self.episode.pk, "p2-chatgpt", "Connection error")
        self.artifact.refresh_from_db()
        self.assertEqual(self.artifact.metadata["error"], "Connection error")
        self.assertIn("failed_at", self.artifact.metadata)

    def test_does_not_change_workflow_status(self):
        """fail_research_source does NOT set workflow status to 'failed'."""
        fail_research_source(self.episode.pk, "p2-chatgpt", "Some error")
        self.wf.refresh_from_db()
        self.assertEqual(self.wf.status, "running")

    def test_triggers_post_save_signal(self):
        """fail_research_source uses .save() which triggers post_save."""
        # Verify the artifact is actually saved (content changed from "")
        fail_research_source(self.episode.pk, "p2-chatgpt", "Error")
        fresh = EpisodeArtifact.objects.get(pk=self.artifact.pk)
        self.assertTrue(fresh.content.startswith("[FAILED:"))


class TestFailStepAccumulation(TestCase):
    """Tests for fail_step() error accumulation."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-fsa",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode-fsa",
        )
        self.wf = EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Targeted Research",
            status="running",
            history=[{"step": "Targeted Research", "status": "started"}],
        )

    def test_first_error_written(self):
        """First fail_step call writes the error."""
        fail_step(self.episode.pk, "Targeted Research", "Error 1")
        self.wf.refresh_from_db()
        entry = self.wf.history[-1]
        self.assertEqual(entry["error"], "Error 1")

    def test_second_error_appended(self):
        """Second fail_step call appends to existing error, not overwrites."""
        fail_step(self.episode.pk, "Targeted Research", "Error 1")
        fail_step(self.episode.pk, "Targeted Research", "Error 2")
        self.wf.refresh_from_db()
        entry = self.wf.history[-1]
        self.assertIn("Error 1", entry["error"])
        self.assertIn("Error 2", entry["error"])
        self.assertIn("---", entry["error"])


class TestThresholdFanIn(TestCase):
    """Tests for threshold-based fan-in in _check_targeted_research_complete."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-tfi",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode-tfi",
        )

    def test_advances_with_one_success_and_failures(self):
        """Fan-in returns True when 1 succeeds and others fail."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="Real research content"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-gemini",
            content="[FAILED: API timeout]",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-claude",
            content="[FAILED: Connection error]",
        )
        self.assertTrue(_check_targeted_research_complete(self.episode.pk))

    def test_does_not_advance_when_all_failed(self):
        """Fan-in returns False when all sources failed (no real content)."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-chatgpt",
            content="[FAILED: Error 1]",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-gemini",
            content="[FAILED: Error 2]",
        )
        self.assertFalse(_check_targeted_research_complete(self.episode.pk))

    def test_does_not_advance_with_pending_tasks(self):
        """Fan-in returns False when some tasks still running (empty content)."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="Real content"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-gemini", content=""
        )
        self.assertFalse(_check_targeted_research_complete(self.episode.pk))

    def test_mixed_success_skipped_failed(self):
        """Fan-in returns True with mix of success, skipped, and failed."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="p2-chatgpt", content="Real content"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-gemini",
            content="[SKIPPED: No API key]",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-claude",
            content="[FAILED: Timeout]",
        )
        self.assertTrue(_check_targeted_research_complete(self.episode.pk))

    def test_all_skipped_no_real_content(self):
        """Fan-in returns False when all sources skipped (no real content)."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-chatgpt",
            content="[SKIPPED: No key]",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p2-gemini",
            content="[SKIPPED: No key]",
        )
        self.assertFalse(_check_targeted_research_complete(self.episode.pk))


class TestResolveSubstepStatus(TestCase):
    """Tests for _resolve_substep_status helper."""

    def test_empty_content_pending(self):
        status, error = _resolve_substep_status("")
        self.assertEqual(status, "pending")
        self.assertEqual(error, "")

    def test_empty_content_running_when_workflow_running(self):
        status, error = _resolve_substep_status("", workflow_is_running=True)
        self.assertEqual(status, "running")

    def test_failed_content(self):
        status, error = _resolve_substep_status("[FAILED: API timeout]")
        self.assertEqual(status, "failed")
        self.assertEqual(error, "API timeout")

    def test_skipped_content(self):
        status, error = _resolve_substep_status("[SKIPPED: No API key]")
        self.assertEqual(status, "skipped")
        self.assertEqual(error, "")

    def test_real_content_complete(self):
        status, error = _resolve_substep_status("Some real research content here")
        self.assertEqual(status, "complete")
        self.assertEqual(error, "")

    def test_failed_empty_error(self):
        """[FAILED: ] with empty error doesn't break."""
        status, error = _resolve_substep_status("[FAILED: ]")
        self.assertEqual(status, "failed")


class TestSubStepStatusField(TestCase):
    """Tests for SubStep dataclass status and error fields."""

    def test_default_status_is_pending(self):
        step = SubStep(label="Test", complete=False)
        self.assertEqual(step.status, "pending")
        self.assertEqual(step.error, "")

    def test_status_and_error_set(self):
        step = SubStep(
            label="ChatGPT research",
            complete=False,
            status="failed",
            error="API timeout",
        )
        self.assertEqual(step.status, "failed")
        self.assertEqual(step.error, "API timeout")


class TestPhase4WithArtifactContents(TestCase):
    """Tests for compute_workflow_progress Phase 4 with artifact_contents."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-p4",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode-p4",
        )

    def test_phase4_substeps_include_mirofish(self):
        """Phase 4 includes MiroFish as a sub-step."""
        phases = compute_workflow_progress(self.episode, [])
        phase4 = phases[3]
        labels = [s.label for s in phase4.sub_steps]
        self.assertIn("MiroFish research", labels)

    def test_phase4_status_from_content(self):
        """Phase 4 sub-steps derive status from artifact content."""
        artifact_titles = ["p2-chatgpt", "p2-gemini", "p2-claude"]
        artifact_contents = {
            "p2-chatgpt": "Real research",
            "p2-gemini": "[FAILED: Timeout]",
            "p2-claude": "",
        }
        phases = compute_workflow_progress(
            self.episode,
            artifact_titles,
            artifact_contents=artifact_contents,
            workflow_is_running=True,
        )
        phase4 = phases[3]

        # Find specific sub-steps
        chatgpt = next(s for s in phase4.sub_steps if s.artifact_key == "chatgpt")
        gemini = next(s for s in phase4.sub_steps if s.artifact_key == "gemini")
        claude = next(s for s in phase4.sub_steps if s.artifact_key == "claude")

        self.assertEqual(chatgpt.status, "complete")
        self.assertEqual(gemini.status, "failed")
        self.assertIn("Timeout", gemini.error)
        self.assertEqual(claude.status, "running")  # empty + workflow running

    def test_phase4_seven_substeps(self):
        """Phase 4 has 7 sub-steps (chatgpt, gemini, claude, together, mirofish, grok, manual)."""
        phases = compute_workflow_progress(self.episode, [])
        self.assertEqual(len(phases[3].sub_steps), 7)
