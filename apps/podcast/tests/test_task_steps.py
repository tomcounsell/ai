"""Tests for task-per-step pipeline functions in Phases 6-12.

Each test class sets up an Episode + EpisodeWorkflow at the correct step,
mocks the underlying service call and downstream .enqueue(), then verifies
workflow state transitions, artifact creation, and error handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.test import TestCase

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast


def _make_workflow(episode, step, *, prev_step=None):
    """Create an EpisodeWorkflow at the given step with minimal history."""
    history = []
    if prev_step:
        history.append(
            {
                "step": prev_step,
                "status": "completed",
                "started_at": "2026-01-01T00:00:00",
                "completed_at": "2026-01-01T01:00:00",
                "error": None,
            }
        )
    history.append(
        {
            "step": step,
            "status": "started",
            "started_at": "2026-01-01T01:00:00",
            "completed_at": None,
            "error": None,
        }
    )
    return EpisodeWorkflow.objects.create(
        episode=episode,
        current_step=step,
        status="running",
        history=history,
    )


class _BaseTestCase(TestCase):
    """Common fixture setup for all task step tests."""

    def setUp(self):
        self.podcast = Podcast.objects.create(
            title="Test Podcast",
            slug="test-podcast-steps",
            description="desc",
            author_name="Author",
            author_email="a@b.com",
        )
        self.episode = Episode.objects.create(
            podcast=self.podcast,
            title="Test Episode",
            slug="test-episode-steps",
        )


# ---------------------------------------------------------------------------
# Phase 6: Cross-Validation
# ---------------------------------------------------------------------------


class TestStepCrossValidation(_BaseTestCase):
    """Tests for step_cross_validation task."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Cross-Validation", prev_step="Targeted Research"
        )

    @patch("apps.podcast.tasks.step_master_briefing")
    @patch("apps.podcast.services.analysis.cross_validate")
    def test_happy_path(self, mock_cv, mock_next_step):
        """Calls cross_validate service and advances to Master Briefing."""
        mock_cv.return_value = MagicMock()
        mock_next_step.enqueue = MagicMock()

        from apps.podcast.tasks import step_cross_validation

        step_cross_validation.call(self.episode.id)

        mock_cv.assert_called_once_with(self.episode.id)
        mock_next_step.enqueue.assert_called_once_with(episode_id=self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Master Briefing"
        assert self.workflow.status == "running"

    @patch("apps.podcast.services.analysis.cross_validate")
    def test_error_fails_step(self, mock_cv):
        """On exception, workflow status becomes 'failed'."""
        mock_cv.side_effect = ValueError("No research artifacts")

        from apps.podcast.tasks import step_cross_validation

        with pytest.raises(ValueError):
            step_cross_validation.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"

    def test_wrong_step_raises(self):
        """Raises ValueError if workflow is not at Cross-Validation."""
        self.workflow.current_step = "Setup"
        self.workflow.save()

        from apps.podcast.tasks import step_cross_validation

        with pytest.raises(ValueError, match="not 'Cross-Validation'"):
            step_cross_validation.call(self.episode.id)


# ---------------------------------------------------------------------------
# Phase 7: Master Briefing + Quality Gate Wave 1
# ---------------------------------------------------------------------------


class TestStepMasterBriefing(_BaseTestCase):
    """Tests for step_master_briefing task with quality gate wave_1."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Master Briefing", prev_step="Cross-Validation"
        )

    @patch("apps.podcast.tasks.step_synthesis")
    @patch("apps.podcast.services.workflow.check_quality_gate")
    @patch("apps.podcast.services.analysis.write_briefing")
    def test_happy_path_gate_passes(self, mock_briefing, mock_gate, mock_next_step):
        """When wave_1 passes, advances to Synthesis."""
        mock_briefing.return_value = MagicMock()
        mock_gate.return_value = {"passed": True, "details": "OK"}
        mock_next_step.enqueue = MagicMock()

        from apps.podcast.tasks import step_master_briefing

        step_master_briefing.call(self.episode.id)

        mock_briefing.assert_called_once_with(self.episode.id)
        mock_gate.assert_called_once_with(self.episode.id, "wave_1")
        mock_next_step.enqueue.assert_called_once_with(episode_id=self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Synthesis"
        assert self.workflow.status == "running"

    @patch("apps.podcast.services.workflow.pause_for_human")
    @patch("apps.podcast.services.workflow.check_quality_gate")
    @patch("apps.podcast.services.analysis.write_briefing")
    def test_gate_fails_pauses_workflow(self, mock_briefing, mock_gate, mock_pause):
        """When wave_1 fails, pauses workflow for human review."""
        mock_briefing.return_value = MagicMock()
        mock_gate.return_value = {
            "passed": False,
            "details": "p3-briefing has only 50 words",
        }

        from apps.podcast.tasks import step_master_briefing

        step_master_briefing.call(self.episode.id)

        mock_pause.assert_called_once()
        call_args = mock_pause.call_args
        assert call_args[0][0] == self.episode.id
        assert "Wave 1 failed" in call_args[0][1]

    @patch("apps.podcast.services.analysis.write_briefing")
    def test_error_fails_step(self, mock_briefing):
        """On exception, workflow status becomes 'failed'."""
        mock_briefing.side_effect = ValueError("No cross-validation artifact")

        from apps.podcast.tasks import step_master_briefing

        with pytest.raises(ValueError):
            step_master_briefing.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"


# ---------------------------------------------------------------------------
# Phase 8: Synthesis
# ---------------------------------------------------------------------------


class TestStepSynthesis(_BaseTestCase):
    """Tests for step_synthesis task."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Synthesis", prev_step="Master Briefing"
        )

    @patch("apps.podcast.tasks.step_episode_planning")
    @patch("apps.podcast.services.synthesis.synthesize_report")
    def test_happy_path(self, mock_synth, mock_next_step):
        """Calls synthesize_report and advances to Episode Planning."""
        mock_synth.return_value = "Full report text"
        mock_next_step.enqueue = MagicMock()

        from apps.podcast.tasks import step_synthesis

        step_synthesis.call(self.episode.id)

        mock_synth.assert_called_once_with(self.episode.id)
        mock_next_step.enqueue.assert_called_once_with(episode_id=self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Episode Planning"
        assert self.workflow.status == "running"

    @patch("apps.podcast.services.synthesis.synthesize_report")
    def test_error_fails_step(self, mock_synth):
        """On exception, workflow status becomes 'failed'."""
        mock_synth.side_effect = RuntimeError("AI model error")

        from apps.podcast.tasks import step_synthesis

        with pytest.raises(RuntimeError, match="AI model error"):
            step_synthesis.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"


# ---------------------------------------------------------------------------
# Phase 9: Episode Planning + Quality Gate Wave 2
# ---------------------------------------------------------------------------


class TestStepEpisodePlanning(_BaseTestCase):
    """Tests for step_episode_planning task with quality gate wave_2."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Episode Planning", prev_step="Synthesis"
        )

    @patch("apps.podcast.tasks.step_audio_generation")
    @patch("apps.podcast.services.workflow.check_quality_gate")
    @patch("apps.podcast.services.synthesis.plan_episode_content")
    def test_happy_path_gate_passes(self, mock_plan, mock_gate, mock_next_step):
        """When wave_2 passes, advances to Audio Generation."""
        mock_plan.return_value = MagicMock()
        mock_gate.return_value = {"passed": True, "details": "Content plan found."}
        mock_next_step.enqueue = MagicMock()

        from apps.podcast.tasks import step_episode_planning

        step_episode_planning.call(self.episode.id)

        mock_plan.assert_called_once_with(self.episode.id)
        mock_gate.assert_called_once_with(self.episode.id, "wave_2")
        mock_next_step.enqueue.assert_called_once_with(episode_id=self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Audio Generation"
        assert self.workflow.status == "running"

    @patch("apps.podcast.services.workflow.pause_for_human")
    @patch("apps.podcast.services.workflow.check_quality_gate")
    @patch("apps.podcast.services.synthesis.plan_episode_content")
    def test_gate_fails_pauses_workflow(self, mock_plan, mock_gate, mock_pause):
        """When wave_2 fails, pauses workflow for human review."""
        mock_plan.return_value = MagicMock()
        mock_gate.return_value = {
            "passed": False,
            "details": "No content_plan artifact found.",
        }

        from apps.podcast.tasks import step_episode_planning

        step_episode_planning.call(self.episode.id)

        mock_pause.assert_called_once()
        call_args = mock_pause.call_args
        assert call_args[0][0] == self.episode.id
        assert "Wave 2 failed" in call_args[0][1]

    @patch("apps.podcast.services.synthesis.plan_episode_content")
    def test_error_fails_step(self, mock_plan):
        """On exception, workflow status becomes 'failed'."""
        mock_plan.side_effect = ValueError("No report_text")

        from apps.podcast.tasks import step_episode_planning

        with pytest.raises(ValueError):
            step_episode_planning.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"


# ---------------------------------------------------------------------------
# Phase 10: Audio Processing - Transcription
# ---------------------------------------------------------------------------


class TestStepTranscribeAudio(_BaseTestCase):
    """Tests for step_transcribe_audio task."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Audio Processing", prev_step="Audio Generation"
        )

    @patch("apps.podcast.tasks.step_generate_chapters")
    @patch("apps.podcast.services.audio.transcribe_audio")
    def test_happy_path(self, mock_transcribe, mock_next_step):
        """Calls transcribe_audio and enqueues chapter generation."""
        mock_transcribe.return_value = "Full transcript text"
        mock_next_step.enqueue = MagicMock()

        from apps.podcast.tasks import step_transcribe_audio

        step_transcribe_audio.call(self.episode.id)

        mock_transcribe.assert_called_once_with(self.episode.id)
        mock_next_step.enqueue.assert_called_once_with(episode_id=self.episode.id)

        # Workflow stays at Audio Processing (chapters are next sub-step)
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Audio Processing"

    @patch("apps.podcast.services.audio.transcribe_audio")
    def test_error_fails_step(self, mock_transcribe):
        """On exception, workflow status becomes 'failed'."""
        mock_transcribe.side_effect = ValueError("No audio_url")

        from apps.podcast.tasks import step_transcribe_audio

        with pytest.raises(ValueError):
            step_transcribe_audio.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"


# ---------------------------------------------------------------------------
# Phase 10: Audio Processing - Chapter Generation
# ---------------------------------------------------------------------------


class TestStepGenerateChapters(_BaseTestCase):
    """Tests for step_generate_chapters task with fan-out to publishing."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Audio Processing", prev_step="Audio Generation"
        )

    @patch("apps.podcast.tasks.step_companions")
    @patch("apps.podcast.tasks.step_metadata")
    @patch("apps.podcast.tasks.step_cover_art")
    @patch("apps.podcast.services.audio.generate_episode_chapters")
    def test_happy_path_fans_out(
        self, mock_chapters, mock_cover, mock_meta, mock_companions
    ):
        """Generates chapters and fans out to all three publishing tasks."""
        mock_chapters.return_value = '{"chapters": []}'
        mock_cover.enqueue = MagicMock()
        mock_meta.enqueue = MagicMock()
        mock_companions.enqueue = MagicMock()

        from apps.podcast.tasks import step_generate_chapters

        step_generate_chapters.call(self.episode.id)

        mock_chapters.assert_called_once_with(self.episode.id)
        mock_cover.enqueue.assert_called_once_with(episode_id=self.episode.id)
        mock_meta.enqueue.assert_called_once_with(episode_id=self.episode.id)
        mock_companions.enqueue.assert_called_once_with(episode_id=self.episode.id)

        # Workflow advances to Publishing Assets
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Publishing Assets"
        assert self.workflow.status == "running"

    @patch("apps.podcast.services.audio.generate_episode_chapters")
    def test_error_fails_step(self, mock_chapters):
        """On exception, workflow status becomes 'failed'."""
        mock_chapters.side_effect = ValueError("No transcript")

        from apps.podcast.tasks import step_generate_chapters

        with pytest.raises(ValueError):
            step_generate_chapters.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"

    def test_wrong_step_raises(self):
        """Raises ValueError if workflow is not at Audio Processing."""
        self.workflow.current_step = "Synthesis"
        self.workflow.save()

        from apps.podcast.tasks import step_generate_chapters

        with pytest.raises(ValueError, match="not 'Audio Processing'"):
            step_generate_chapters.call(self.episode.id)


# ---------------------------------------------------------------------------
# Phase 11: Publishing Assets - Cover Art
# ---------------------------------------------------------------------------


class TestStepCoverArt(_BaseTestCase):
    """Tests for step_cover_art parallel publishing sub-step."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Publishing Assets", prev_step="Audio Processing"
        )

    @patch("apps.podcast.services.publishing.generate_cover_art")
    def test_happy_path(self, mock_cover):
        """Calls generate_cover_art service. Does NOT enqueue next step."""
        mock_cover.return_value = "https://example.com/cover.png"

        from apps.podcast.tasks import step_cover_art

        step_cover_art.call(self.episode.id)

        mock_cover.assert_called_once_with(self.episode.id)

        # Workflow stays at Publishing Assets (signal handles fan-in)
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Publishing Assets"

    @patch("apps.podcast.services.publishing.generate_cover_art")
    def test_error_fails_step(self, mock_cover):
        """On exception, workflow status becomes 'failed'."""
        mock_cover.side_effect = RuntimeError("Image generation failed")

        from apps.podcast.tasks import step_cover_art

        with pytest.raises(RuntimeError, match="Image generation failed"):
            step_cover_art.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"

    def test_wrong_step_raises(self):
        """Raises ValueError if workflow is not at Publishing Assets."""
        self.workflow.current_step = "Synthesis"
        self.workflow.save()

        from apps.podcast.tasks import step_cover_art

        with pytest.raises(ValueError, match="not 'Publishing Assets'"):
            step_cover_art.call(self.episode.id)


# ---------------------------------------------------------------------------
# Phase 11: Publishing Assets - Metadata
# ---------------------------------------------------------------------------


class TestStepMetadata(_BaseTestCase):
    """Tests for step_metadata parallel publishing sub-step."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Publishing Assets", prev_step="Audio Processing"
        )

    @patch("apps.podcast.services.publishing.write_episode_metadata")
    def test_happy_path(self, mock_meta):
        """Calls write_episode_metadata service. Does NOT enqueue next step."""
        mock_meta.return_value = MagicMock()

        from apps.podcast.tasks import step_metadata

        step_metadata.call(self.episode.id)

        mock_meta.assert_called_once_with(self.episode.id)

        # Workflow stays at Publishing Assets
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Publishing Assets"

    @patch("apps.podcast.services.publishing.write_episode_metadata")
    def test_error_fails_step(self, mock_meta):
        """On exception, workflow status becomes 'failed'."""
        mock_meta.side_effect = ValueError("No report_text")

        from apps.podcast.tasks import step_metadata

        with pytest.raises(ValueError):
            step_metadata.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"

    def test_wrong_step_raises(self):
        """Raises ValueError if workflow is not at Publishing Assets."""
        self.workflow.current_step = "Audio Processing"
        self.workflow.save()

        from apps.podcast.tasks import step_metadata

        with pytest.raises(ValueError, match="not 'Publishing Assets'"):
            step_metadata.call(self.episode.id)


# ---------------------------------------------------------------------------
# Phase 11: Publishing Assets - Companions
# ---------------------------------------------------------------------------


class TestStepCompanions(_BaseTestCase):
    """Tests for step_companions parallel publishing sub-step."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Publishing Assets", prev_step="Audio Processing"
        )

    @patch("apps.podcast.services.publishing.generate_companions")
    def test_happy_path(self, mock_companions):
        """Calls generate_companions service. Does NOT enqueue next step."""
        mock_companions.return_value = []

        from apps.podcast.tasks import step_companions

        step_companions.call(self.episode.id)

        mock_companions.assert_called_once_with(self.episode.id)

        # Workflow stays at Publishing Assets
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Publishing Assets"

    @patch("apps.podcast.services.publishing.generate_companions")
    def test_error_fails_step(self, mock_companions):
        """On exception, workflow status becomes 'failed'."""
        mock_companions.side_effect = ValueError("No report_text")

        from apps.podcast.tasks import step_companions

        with pytest.raises(ValueError):
            step_companions.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"


# ---------------------------------------------------------------------------
# Phase 12: Publish
# ---------------------------------------------------------------------------


class TestStepPublish(_BaseTestCase):
    """Tests for step_publish task - final step in the pipeline."""

    def setUp(self):
        super().setUp()
        self.workflow = _make_workflow(
            self.episode, "Publish", prev_step="Publishing Assets"
        )

    @patch("apps.podcast.services.publishing.publish_episode")
    def test_happy_path_completes_workflow(self, mock_publish):
        """Calls publish_episode and marks workflow as complete."""
        mock_publish.return_value = self.episode

        from apps.podcast.tasks import step_publish

        step_publish.call(self.episode.id)

        mock_publish.assert_called_once_with(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "complete"
        assert self.workflow.current_step == "Publish"

    @patch("apps.podcast.services.publishing.publish_episode")
    def test_error_fails_step(self, mock_publish):
        """On exception, workflow status becomes 'failed'."""
        mock_publish.side_effect = RuntimeError("Publish failed")

        from apps.podcast.tasks import step_publish

        with pytest.raises(RuntimeError, match="Publish failed"):
            step_publish.call(self.episode.id)

        self.workflow.refresh_from_db()
        assert self.workflow.status == "failed"

    def test_wrong_step_raises(self):
        """Raises ValueError if workflow is not at Publish."""
        self.workflow.current_step = "Audio Processing"
        self.workflow.save()

        from apps.podcast.tasks import step_publish

        with pytest.raises(ValueError, match="not 'Publish'"):
            step_publish.call(self.episode.id)


# ---------------------------------------------------------------------------
# Quality Gate Tests (wave_1 and wave_2)
# ---------------------------------------------------------------------------


class TestQualityGateWave1(_BaseTestCase):
    """Tests for check_quality_gate('wave_1') used in Master Briefing."""

    def test_passes_with_substantial_briefing(self):
        """wave_1 passes when p3-briefing has 200+ words."""
        content = " ".join(["word"] * 250)
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p3-briefing",
            content=content,
        )

        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_1")
        assert result["passed"] is True
        assert "250 words" in result["details"]

    def test_fails_with_insufficient_briefing(self):
        """wave_1 fails when p3-briefing has fewer than 200 words."""
        content = " ".join(["word"] * 50)
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p3-briefing",
            content=content,
        )

        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_1")
        assert result["passed"] is False
        assert "50 words" in result["details"]

    def test_fails_with_no_briefing(self):
        """wave_1 fails when no p3-briefing artifact exists."""
        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_1")
        assert result["passed"] is False
        assert "No p3-briefing" in result["details"]

    def test_fails_with_empty_briefing(self):
        """wave_1 fails when p3-briefing exists but has no content."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p3-briefing",
            content="",
        )

        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_1")
        assert result["passed"] is False


class TestQualityGateWave2(_BaseTestCase):
    """Tests for check_quality_gate('wave_2') used in Episode Planning."""

    def test_passes_with_content_plan(self):
        """wave_2 passes when content_plan artifact exists."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="content_plan",
            content='{"sections": []}',
        )

        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_2")
        assert result["passed"] is True

    def test_passes_with_content_plan_hyphenated(self):
        """wave_2 passes with content-plan (hyphenated variant)."""
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="content-plan",
            content='{"sections": []}',
        )

        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_2")
        assert result["passed"] is True

    def test_fails_with_no_plan(self):
        """wave_2 fails when no content plan artifact exists."""
        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_2")
        assert result["passed"] is False
        assert "No content_plan" in result["details"]

    def test_unknown_gate_fails(self):
        """Unknown gate name returns failure."""
        from apps.podcast.services.workflow import check_quality_gate

        result = check_quality_gate(self.episode.id, "wave_99")
        assert result["passed"] is False
        assert "Unknown quality gate" in result["details"]


# ---------------------------------------------------------------------------
# Publishing Assets Fan-In Signal Tests
# ---------------------------------------------------------------------------


class TestPublishingAssetsFanIn(_BaseTestCase):
    """Tests for _check_publishing_assets_complete signal logic."""

    def test_false_when_no_publishing_artifacts(self):
        """No publishing artifacts means not complete."""
        from apps.podcast.signals import _check_publishing_assets_complete

        assert _check_publishing_assets_complete(self.episode.id) is False

    def test_false_when_only_some_artifacts(self):
        """Some publishing artifacts present but not all -- not complete."""
        EpisodeArtifact.objects.create(
            episode=self.episode, title="metadata", content="metadata content"
        )
        EpisodeArtifact.objects.create(
            episode=self.episode, title="cover-art", content="cover art content"
        )

        from apps.podcast.signals import _check_publishing_assets_complete

        assert _check_publishing_assets_complete(self.episode.id) is False

    def test_false_when_artifact_has_empty_content(self):
        """All titles present but one has empty content -- not complete."""
        for title in [
            "metadata",
            "companion-summary",
            "companion-checklist",
            "companion-frameworks",
        ]:
            EpisodeArtifact.objects.create(
                episode=self.episode, title=title, content=f"content for {title}"
            )
        # cover-art exists but empty
        EpisodeArtifact.objects.create(
            episode=self.episode, title="cover-art", content=""
        )

        from apps.podcast.signals import _check_publishing_assets_complete

        assert _check_publishing_assets_complete(self.episode.id) is False

    def test_true_when_all_artifacts_have_content(self):
        """All required publishing artifacts with content -- complete."""
        for title in [
            "metadata",
            "companion-summary",
            "companion-checklist",
            "companion-frameworks",
            "cover-art",
        ]:
            EpisodeArtifact.objects.create(
                episode=self.episode, title=title, content=f"content for {title}"
            )

        from apps.podcast.signals import _check_publishing_assets_complete

        assert _check_publishing_assets_complete(self.episode.id) is True

    def test_skipped_cover_art_counts_as_complete(self):
        """Cover-art with [SKIPPED: ...] content counts as complete."""
        for title in [
            "metadata",
            "companion-summary",
            "companion-checklist",
            "companion-frameworks",
        ]:
            EpisodeArtifact.objects.create(
                episode=self.episode, title=title, content=f"content for {title}"
            )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="cover-art",
            content="[SKIPPED: OPENROUTER_API_KEY not configured]",
        )

        from apps.podcast.signals import _check_publishing_assets_complete

        assert _check_publishing_assets_complete(self.episode.id) is True


# ---------------------------------------------------------------------------
# _acquire_step_lock Tests
# ---------------------------------------------------------------------------


class TestAcquireStepLock(_BaseTestCase):
    """Tests for the _acquire_step_lock helper."""

    def test_allows_normal_execution(self):
        """Lock allows execution when step matches and status is 'started'."""
        _make_workflow(self.episode, "Cross-Validation")

        from apps.podcast.tasks import _acquire_step_lock

        # Should not raise
        _acquire_step_lock(self.episode.id, "Cross-Validation")

    def test_rejects_wrong_step(self):
        """Raises ValueError when current step doesn't match expected."""
        _make_workflow(self.episode, "Synthesis")

        from apps.podcast.tasks import _acquire_step_lock

        with pytest.raises(ValueError, match="not 'Cross-Validation'"):
            _acquire_step_lock(self.episode.id, "Cross-Validation")

    def test_rejects_completed_step(self):
        """Raises ValueError when trying to re-run a completed step."""
        wf = _make_workflow(self.episode, "Cross-Validation")
        # Manually mark step as completed in history
        for entry in wf.history:
            if entry["step"] == "Cross-Validation":
                entry["status"] = "completed"
        wf.save()

        from apps.podcast.tasks import _acquire_step_lock

        with pytest.raises(ValueError, match="already completed"):
            _acquire_step_lock(self.episode.id, "Cross-Validation")

    def test_rejects_failed_step(self):
        """Raises ValueError when trying to re-run a failed step."""
        wf = _make_workflow(self.episode, "Cross-Validation")
        for entry in wf.history:
            if entry["step"] == "Cross-Validation":
                entry["status"] = "failed"
        wf.save()

        from apps.podcast.tasks import _acquire_step_lock

        with pytest.raises(ValueError, match="already failed"):
            _acquire_step_lock(self.episode.id, "Cross-Validation")
