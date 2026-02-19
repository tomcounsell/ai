"""Tests for apps.podcast.services.analysis module."""

import logging
from unittest.mock import Mock, patch

import pytest
from pydantic_ai.exceptions import ModelHTTPError, UsageLimitExceeded

from apps.podcast.models import Episode, EpisodeArtifact, Podcast
from apps.podcast.services import analysis


@pytest.fixture
def podcast():
    """Create a test podcast."""
    return Podcast.objects.create(
        title="Test Podcast Quota",
        slug="test-podcast-quota",
        description="Test podcast description",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode(podcast):
    """Create a test episode."""
    return Episode.objects.create(
        podcast=podcast,
        title="Test Episode",
        slug="test-episode",
        description="Test description",
    )


@pytest.fixture
def research_artifact(episode):
    """Create a research artifact."""
    return EpisodeArtifact.objects.create(
        episode=episode,
        title="p2-perplexity",
        content="This is research content about sleep science.",
        description="Perplexity research output.",
        workflow_context="Research Gathering",
    )


@pytest.mark.django_db
class TestCreateResearchDigestQuotaHandling:
    """Test that create_research_digest handles quota errors gracefully."""

    def test_successful_digest_creation(self, research_artifact, episode):
        """Test successful digest creation when AI call works."""
        from apps.podcast.services.digest_research import ResearchDigest

        mock_digest = ResearchDigest(
            table_of_contents=["Introduction"],
            key_findings=[],
            statistics=[],
            sources=[],
            topics=["sleep"],
            questions_answered=[],
            questions_unanswered=[],
            contradictions=[],
        )

        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.return_value = mock_digest
            artifact = analysis.create_research_digest(episode.id, "p2-perplexity")

        # Verify digest was created successfully
        assert artifact.title == "digest-perplexity"
        assert artifact.content is not None
        assert "[SKIPPED:" not in artifact.content
        assert artifact.metadata is not None
        mock_digest_fn.assert_called_once()

    def test_handles_usage_limit_exceeded(self, research_artifact, episode, caplog):
        """Test graceful handling of UsageLimitExceeded exception."""
        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.side_effect = UsageLimitExceeded(
                "You do not have enough quota to make this request"
            )

            with caplog.at_level(logging.WARNING):
                artifact = analysis.create_research_digest(episode.id, "p2-perplexity")

        # Verify skipped artifact was created
        assert artifact.title == "digest-perplexity"
        assert "[SKIPPED: AI quota exceeded]" in artifact.content
        assert "p2-perplexity" in artifact.content
        assert artifact.metadata["skipped"] is True
        assert artifact.metadata["reason"] == "quota_exceeded"

        # Verify warning was logged
        assert "AI quota exceeded" in caplog.text
        assert "p2-perplexity" in caplog.text

    def test_handles_http_429_error(self, research_artifact, episode, caplog):
        """Test graceful handling of HTTP 429 (rate limit) error."""
        # Create ModelHTTPError with 429 status
        error = ModelHTTPError(status_code=429, model_name="test-model")
        # Manually set response attribute with status_code
        error.response = Mock(status_code=429)

        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.side_effect = error

            with caplog.at_level(logging.WARNING):
                artifact = analysis.create_research_digest(episode.id, "p2-perplexity")

        # Verify skipped artifact was created
        assert artifact.title == "digest-perplexity"
        assert "[SKIPPED: AI quota exceeded]" in artifact.content
        assert artifact.metadata["skipped"] is True
        assert artifact.metadata["reason"] == "quota_exceeded"

        # Verify warning was logged
        assert "AI quota exceeded" in caplog.text

    def test_reraises_non_quota_http_errors(self, research_artifact, episode):
        """Test that non-quota HTTP errors are re-raised."""
        # Create ModelHTTPError with 500 status (not a quota error)
        error = ModelHTTPError(status_code=500, model_name="test-model")
        error.response = Mock(status_code=500)

        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.side_effect = error

            # Should re-raise the exception
            with pytest.raises(ModelHTTPError):
                analysis.create_research_digest(episode.id, "p2-perplexity")

    def test_reraises_other_exceptions(self, research_artifact, episode):
        """Test that non-quota exceptions are re-raised."""
        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.side_effect = ValueError("Invalid input")

            # Should re-raise the exception
            with pytest.raises(ValueError):
                analysis.create_research_digest(episode.id, "p2-perplexity")

    def test_skipped_digest_title_derivation(self, research_artifact, episode):
        """Test that digest title is correctly derived for skipped digests."""
        with patch(
            "apps.podcast.services.digest_research.digest_research"
        ) as mock_digest_fn:
            mock_digest_fn.side_effect = UsageLimitExceeded("Quota exceeded")

            artifact = analysis.create_research_digest(episode.id, "p2-perplexity")

        # Verify title is correctly derived from p2-perplexity -> digest-perplexity
        assert artifact.title == "digest-perplexity"
        assert "p2-perplexity" in artifact.content


@pytest.fixture
def podcast_briefing():
    """Create a test podcast for briefing tests."""
    return Podcast.objects.create(
        title="Test Podcast Briefing",
        slug="test-podcast-briefing",
        description="Test podcast description",
        author_name="Test Author",
        author_email="test@example.com",
    )


@pytest.fixture
def episode_briefing(podcast_briefing):
    """Create a test episode for briefing tests."""
    return Episode.objects.create(
        podcast=podcast_briefing,
        title="Test Episode Briefing",
        slug="test-episode-briefing",
        description="Test description",
    )


@pytest.fixture
def briefing_artifacts(episode_briefing):
    """Create artifacts needed for briefing tests."""
    # Create cross-validation artifact (required)
    cv = EpisodeArtifact.objects.create(
        episode=episode_briefing,
        title="cross-validation",
        content="Cross-validation content",
        description="Cross-validation report.",
        workflow_context="Cross-Validation",
    )
    # Create raw research artifact
    research = EpisodeArtifact.objects.create(
        episode=episode_briefing,
        title="p2-perplexity",
        content="Raw perplexity research content",
        description="Perplexity research output.",
        workflow_context="Research Gathering",
    )
    return {"cv": cv, "research": research}


@pytest.mark.django_db
class TestWriteBriefingFallback:
    """Test that write_briefing handles skipped digests correctly."""

    def test_uses_raw_research_for_skipped_digests(
        self, briefing_artifacts, episode_briefing, caplog
    ):
        """Test that briefing uses raw research when digest is skipped."""
        # Create a skipped digest
        EpisodeArtifact.objects.create(
            episode=episode_briefing,
            title="digest-perplexity",
            content="[SKIPPED: AI quota exceeded]\n\nRaw research available in p2-perplexity.",
            description="Digest of p2-perplexity (skipped - quota exceeded).",
            workflow_context="Research Gathering",
            metadata={"skipped": True, "reason": "quota_exceeded"},
        )

        from apps.podcast.services.write_briefing import (
            DepthEntry,
            MasterBriefing,
            SourceInventory,
        )

        mock_briefing = MasterBriefing(
            verified_findings=[],
            depth_distribution=[
                DepthEntry(
                    topic="test",
                    depth_rating="light",
                    recommendation="Test recommendation",
                )
            ],
            practical_audit=[],
            story_bank=[],
            counterpoints=[],
            research_gaps=[],
            source_inventory=SourceInventory(tier1=[], tier2=[], tier3=[]),
            synthesis_notes="Test notes",
        )

        with patch(
            "apps.podcast.services.write_briefing.write_briefing"
        ) as mock_write_briefing:
            mock_write_briefing.return_value = mock_briefing

            with caplog.at_level(logging.INFO):
                analysis.write_briefing(episode_briefing.id)

            # Verify the function was called with raw research instead of skipped digest
            call_args = mock_write_briefing.call_args
            research_digests = call_args[1]["research_digests"]

            # Should contain raw research content, not the skipped digest
            assert "perplexity" in research_digests
            assert "Raw perplexity research content" in research_digests["perplexity"]
            assert "[SKIPPED:" not in research_digests["perplexity"]

        # Verify log message about using raw research
        assert "Using raw research for skipped digests" in caplog.text
        assert "perplexity" in caplog.text

    def test_uses_successful_digests_normally(
        self, briefing_artifacts, episode_briefing
    ):
        """Test that successful digests are used normally."""
        # Create a successful digest
        EpisodeArtifact.objects.create(
            episode=episode_briefing,
            title="digest-perplexity",
            content="# Research Digest\n\n## Key Findings\n\n- Finding 1",
            description="Digest of p2-perplexity.",
            workflow_context="Research Gathering",
            metadata={"skipped": False},
        )

        from apps.podcast.services.write_briefing import (
            DepthEntry,
            MasterBriefing,
            SourceInventory,
        )

        mock_briefing = MasterBriefing(
            verified_findings=[],
            depth_distribution=[
                DepthEntry(
                    topic="test",
                    depth_rating="light",
                    recommendation="Test recommendation",
                )
            ],
            practical_audit=[],
            story_bank=[],
            counterpoints=[],
            research_gaps=[],
            source_inventory=SourceInventory(tier1=[], tier2=[], tier3=[]),
            synthesis_notes="Test notes",
        )

        with patch(
            "apps.podcast.services.write_briefing.write_briefing"
        ) as mock_write_briefing:
            mock_write_briefing.return_value = mock_briefing

            analysis.write_briefing(episode_briefing.id)

            # Verify the function was called with digest content
            call_args = mock_write_briefing.call_args
            research_digests = call_args[1]["research_digests"]

            # Should use digest content, not raw research
            assert "perplexity" in research_digests
            assert "## Key Findings" in research_digests["perplexity"]
            assert (
                "Raw perplexity research content" not in research_digests["perplexity"]
            )
