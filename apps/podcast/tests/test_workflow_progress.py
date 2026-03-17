from django.test import TestCase
from django.utils import timezone

from apps.podcast.models import Episode, EpisodeArtifact, Podcast
from apps.podcast.services.workflow_progress import (
    Phase,
    SubStep,
    compute_workflow_progress,
)


class PhaseDataclassTestCase(TestCase):
    """Tests for the Phase and SubStep dataclass behavior."""

    def test_phase_status_pending_when_no_steps_complete(self):
        """All sub_steps incomplete returns 'pending'."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=False),
                SubStep(label="B", complete=False),
            ],
        )
        self.assertEqual(phase.status, "pending")

    def test_phase_status_in_progress_when_some_complete(self):
        """Mixed complete/incomplete returns 'in_progress'."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=True),
                SubStep(label="B", complete=False),
            ],
        )
        self.assertEqual(phase.status, "in_progress")

    def test_phase_status_complete_when_all_complete(self):
        """All sub_steps complete returns 'complete'."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=True),
                SubStep(label="B", complete=True),
            ],
        )
        self.assertEqual(phase.status, "complete")

    def test_phase_progress_fraction_zero(self):
        """No sub_steps complete returns 0.0."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=False),
                SubStep(label="B", complete=False),
            ],
        )
        self.assertEqual(phase.progress_fraction, 0.0)

    def test_phase_progress_fraction_half(self):
        """1 of 2 complete returns 0.5."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=True),
                SubStep(label="B", complete=False),
            ],
        )
        self.assertEqual(phase.progress_fraction, 0.5)

    def test_phase_progress_fraction_one(self):
        """All complete returns 1.0."""
        phase = Phase(
            number=1,
            name="Test",
            description="desc",
            sub_steps=[
                SubStep(label="A", complete=True),
                SubStep(label="B", complete=True),
            ],
        )
        self.assertEqual(phase.progress_fraction, 1.0)

    def test_phase_status_pending_when_empty_substeps(self):
        """Empty sub_steps list returns 'pending'."""
        phase = Phase(number=1, name="Test", description="desc", sub_steps=[])
        self.assertEqual(phase.status, "pending")

    def test_phase_progress_fraction_zero_when_empty(self):
        """Empty sub_steps list returns 0.0."""
        phase = Phase(number=1, name="Test", description="desc", sub_steps=[])
        self.assertEqual(phase.progress_fraction, 0.0)


class ComputeWorkflowProgressTestCase(TestCase):
    """Tests for compute_workflow_progress using real DB objects."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast",
            description="A test podcast.",
            author_name="Tester",
            author_email="test@example.com",
        )

    def _create_episode(self, **kwargs):
        """Helper to create an Episode with sensible defaults."""
        defaults = {
            "podcast": self.podcast,
            "title": "Test Episode",
            "slug": "test-episode",
            "status": "draft",
        }
        defaults.update(kwargs)
        return Episode.objects.create(**defaults)

    def _artifact_titles(self, episode):
        return list(episode.artifacts.values_list("title", flat=True))

    def test_draft_episode_all_pending(self):
        """Fresh draft Episode with no artifacts: phase 1 is in_progress, rest pending."""
        episode = self._create_episode()
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        # Phase 1 should be in_progress (episode exists=True, status not draft=False)
        self.assertEqual(phases[0].status, "in_progress")
        self.assertEqual(phases[0].progress_fraction, 0.5)

        # All other phases should be pending
        for phase in phases[1:]:
            self.assertEqual(
                phase.status,
                "pending",
                f"Phase {phase.number} ({phase.name}) should be pending",
            )

    def test_phase_1_complete_when_not_draft(self):
        """Episode with status='in_progress' makes phase 1 complete."""
        episode = self._create_episode(status="in_progress")
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[0].number, 1)
        self.assertEqual(phases[0].status, "complete")
        self.assertEqual(phases[0].progress_fraction, 1.0)

    def test_phase_2_perplexity_artifact(self):
        """Adding a 'research/p2-perplexity.md' artifact completes phase 2."""
        episode = self._create_episode()
        EpisodeArtifact.objects.create(
            episode=episode,
            title="research/p2-perplexity.md",
            content="Perplexity research content.",
        )
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[1].number, 2)
        self.assertEqual(phases[1].status, "complete")
        self.assertEqual(phases[1].progress_fraction, 1.0)

    def test_phase_4_multiple_targeted_research(self):
        """Adding p2-grok and p2-gemini artifacts makes phase 4 in_progress (2 of 7)."""
        episode = self._create_episode()
        EpisodeArtifact.objects.create(
            episode=episode,
            title="research/p2-grok.md",
            content="Grok research.",
        )
        EpisodeArtifact.objects.create(
            episode=episode,
            title="research/p2-gemini.md",
            content="Gemini research.",
        )
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[3].number, 4)
        self.assertEqual(phases[3].status, "in_progress")
        # 7 sub-steps: chatgpt, gemini, claude, together, mirofish, grok, manual
        self.assertAlmostEqual(phases[3].progress_fraction, 2 / 7)

    def test_phase_7_report_text_with_word_count(self):
        """Episode with report_text completes phase 7 and detail has word count."""
        report = "This is a sample report with exactly ten words here now"
        episode = self._create_episode(report_text=report)
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[6].number, 7)
        self.assertEqual(phases[6].status, "complete")
        self.assertEqual(phases[6].progress_fraction, 1.0)

        # Verify the detail contains the word count
        word_count = len(report.split())
        detail = phases[6].sub_steps[0].detail
        self.assertIn(str(word_count), detail)
        self.assertIn("words", detail)

    def test_phase_9_audio_generation(self):
        """Episode with audio_url and audio_file_size_bytes completes phase 9."""
        episode = self._create_episode(
            audio_url="https://example.com/audio.mp3",
            audio_file_size_bytes=52_428_800,  # 50 MB
        )
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[8].number, 9)
        self.assertEqual(phases[8].status, "complete")
        self.assertEqual(phases[8].progress_fraction, 1.0)

        # Verify the size detail
        size_detail = phases[8].sub_steps[1].detail
        self.assertIn("50.0 MB", size_detail)

    def test_phase_12_published(self):
        """Episode with published_at set completes phase 12."""
        episode = self._create_episode()
        episode.published_at = timezone.now()
        episode.save()
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[11].number, 12)
        self.assertEqual(phases[11].status, "complete")
        self.assertEqual(phases[11].progress_fraction, 1.0)

    def test_fully_complete_episode(self):
        """Episode with ALL fields and artifacts makes all 12 phases complete."""
        episode = self._create_episode(
            status="complete",
            report_text="A full report text with many words for the synthesis phase.",
            audio_url="https://example.com/audio.mp3",
            audio_file_size_bytes=10_000_000,
            transcript="Full transcript of the episode audio content.",
            chapters="00:00 Introduction\n05:00 Main Topic\n20:00 Conclusion",
            cover_image_url="https://example.com/cover.jpg",
            description="A full episode description for publishing.",
        )
        episode.published_at = timezone.now()
        episode.save()

        # Create all required artifacts (including p2-mirofish for Phase 4)
        artifact_titles = [
            "research/p2-perplexity.md",
            "research/question-discovery.md",
            "research/p2-grok.md",
            "research/p2-chatgpt.md",
            "research/p2-gemini.md",
            "research/p2-claude.md",
            "research/p2-together.md",
            "research/p2-mirofish.md",
            "research/p2-manual.md",
            "research/cross-validation.md",
            "research/p3-briefing.md",
            "plans/content-plan.md",
        ]
        for title in artifact_titles:
            EpisodeArtifact.objects.create(
                episode=episode,
                title=title,
                content=f"Content for {title}.",
            )

        titles = self._artifact_titles(episode)
        phases = compute_workflow_progress(episode, titles)

        for phase in phases:
            self.assertEqual(
                phase.status,
                "complete",
                f"Phase {phase.number} ({phase.name}) should be complete, "
                f"got '{phase.status}'",
            )
            self.assertEqual(
                phase.progress_fraction,
                1.0,
                f"Phase {phase.number} ({phase.name}) should have progress 1.0",
            )

    def test_artifact_matching_case_insensitive(self):
        """Artifact 'Research/P2-PERPLEXITY.md' still matches phase 2."""
        episode = self._create_episode()
        EpisodeArtifact.objects.create(
            episode=episode,
            title="Research/P2-PERPLEXITY.md",
            content="Perplexity research.",
        )
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))

        self.assertEqual(phases[1].number, 2)
        self.assertEqual(phases[1].status, "complete")

    def test_returns_twelve_phases(self):
        """Always returns exactly 12 phases."""
        episode = self._create_episode()
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))
        self.assertEqual(len(phases), 12)

    def test_phases_ordered_by_number(self):
        """Phases are returned in order 1 through 12."""
        episode = self._create_episode()
        phases = compute_workflow_progress(episode, self._artifact_titles(episode))
        numbers = [p.number for p in phases]
        self.assertEqual(numbers, list(range(1, 13)))
