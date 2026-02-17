"""Tests for the local audio worker API endpoints."""

from __future__ import annotations

import json

from django.test import TestCase, override_settings

from apps.podcast.models import Episode, EpisodeArtifact, EpisodeWorkflow, Podcast


@override_settings(LOCAL_WORKER_API_KEY="test-key")
class TestPendingAudioView(TestCase):
    """Tests for GET /api/podcast/pending-audio/."""

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
        self.url = "/api/podcast/pending-audio/"
        self.auth_headers = {"HTTP_AUTHORIZATION": "Bearer test-key"}

    def test_returns_401_without_auth(self):
        """Request without Authorization header returns 401."""
        response = self.client.get(self.url)
        assert response.status_code == 401
        data = json.loads(response.content)
        assert data["error"] == "Unauthorized"

    def test_returns_401_with_wrong_key(self):
        """Request with wrong API key returns 401."""
        response = self.client.get(
            self.url, headers={"authorization": "Bearer wrong-key"}
        )
        assert response.status_code == 401

    def test_returns_empty_list_when_no_pending(self):
        """Returns empty episodes list when nothing is paused for audio."""
        response = self.client.get(self.url, **self.auth_headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data == {"episodes": []}

    def test_returns_episodes_paused_for_audio(self):
        """Returns correct episode data when paused for audio_generation."""
        EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Audio Generation",
            status="paused_for_human",
            blocked_on="audio_generation",
            history=[],
        )
        self.episode.report_text = "Report content"
        self.episode.sources_text = "Sources content"
        self.episode.save()

        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p3-briefing",
            content="Briefing content",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="content-plan",
            content="Plan content",
        )
        EpisodeArtifact.objects.create(
            episode=self.episode,
            title="p1-brief",
            content="Brief content",
        )

        response = self.client.get(self.url, **self.auth_headers)
        assert response.status_code == 200
        data = json.loads(response.content)

        assert len(data["episodes"]) == 1
        ep = data["episodes"][0]
        assert ep["id"] == self.episode.id
        assert ep["title"] == "Test Episode"
        assert ep["slug"] == "test-episode"
        assert ep["podcast_slug"] == "test-podcast"
        assert ep["sources"]["report.md"] == "Report content"
        assert ep["sources"]["sources.md"] == "Sources content"
        assert ep["sources"]["briefing.md"] == "Briefing content"
        assert ep["sources"]["content_plan.md"] == "Plan content"
        assert ep["sources"]["brief.md"] == "Brief content"

    def test_excludes_non_audio_paused_workflows(self):
        """Workflows paused for other reasons are not included."""
        EpisodeWorkflow.objects.create(
            episode=self.episode,
            current_step="Master Briefing",
            status="paused_for_human",
            blocked_on="Quality Gate Wave 1 failed",
            history=[],
        )
        response = self.client.get(self.url, **self.auth_headers)
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["episodes"] == []

    @override_settings(LOCAL_WORKER_API_KEY="")
    def test_returns_503_when_api_key_not_configured(self):
        """Returns 503 when LOCAL_WORKER_API_KEY is empty."""
        response = self.client.get(self.url, **self.auth_headers)
        assert response.status_code == 503
        data = json.loads(response.content)
        assert data["error"] == "Worker API not configured"

    def test_post_not_allowed(self):
        """POST method returns 405."""
        response = self.client.post(self.url, **self.auth_headers)
        assert response.status_code == 405


@override_settings(LOCAL_WORKER_API_KEY="test-key")
class TestAudioCallbackView(TestCase):
    """Tests for POST /api/podcast/episodes/<id>/audio-callback/."""

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
            status="paused_for_human",
            blocked_on="audio_generation",
            history=[
                {
                    "step": "Audio Generation",
                    "status": "paused_for_human",
                    "started_at": "2026-01-01T00:00:00",
                    "completed_at": None,
                    "error": None,
                }
            ],
        )
        self.url = f"/api/podcast/episodes/{self.episode.id}/audio-callback/"
        self.auth_headers = {
            "HTTP_AUTHORIZATION": "Bearer test-key",
        }
        self.valid_payload = {
            "audio_url": "https://storage.example.com/audio.mp3",
            "audio_file_size_bytes": 12345678,
        }

    def test_returns_401_without_auth(self):
        """Request without Authorization header returns 401."""
        response = self.client.post(
            self.url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
        )
        assert response.status_code == 401

    def test_updates_episode_and_resumes_workflow(self):
        """Valid callback updates episode audio fields and advances workflow."""
        response = self.client.post(
            self.url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            **self.auth_headers,
        )
        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["status"] == "ok"
        assert data["message"] == "Audio received, transcription enqueued"

        # Verify episode was updated
        self.episode.refresh_from_db()
        assert self.episode.audio_url == "https://storage.example.com/audio.mp3"
        assert self.episode.audio_file_size_bytes == 12345678

        # Verify workflow was advanced past Audio Generation
        self.workflow.refresh_from_db()
        assert self.workflow.current_step == "Audio Processing"
        assert self.workflow.status == "running"
        assert self.workflow.blocked_on == ""

    def test_returns_400_for_missing_audio_url(self):
        """Returns 400 when audio_url is missing from body."""
        response = self.client.post(
            self.url,
            data=json.dumps({"audio_file_size_bytes": 100}),
            content_type="application/json",
            **self.auth_headers,
        )
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "audio_url" in data["error"]

    def test_returns_400_for_invalid_json(self):
        """Returns 400 when request body is not valid JSON."""
        response = self.client.post(
            self.url,
            data="not json",
            content_type="application/json",
            **self.auth_headers,
        )
        assert response.status_code == 400
        data = json.loads(response.content)
        assert "Invalid JSON" in data["error"]

    def test_returns_404_for_nonexistent_episode(self):
        """Returns 404 for an episode that does not exist."""
        url = "/api/podcast/episodes/99999/audio-callback/"
        response = self.client.post(
            url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            **self.auth_headers,
        )
        assert response.status_code == 404

    def test_returns_409_when_not_paused_for_audio(self):
        """Returns 409 when workflow is not paused for audio_generation."""
        self.workflow.status = "running"
        self.workflow.blocked_on = ""
        self.workflow.save()

        response = self.client.post(
            self.url,
            data=json.dumps(self.valid_payload),
            content_type="application/json",
            **self.auth_headers,
        )
        assert response.status_code == 409
        data = json.loads(response.content)
        assert "not waiting for audio" in data["error"]

    def test_get_not_allowed(self):
        """GET method returns 405."""
        response = self.client.get(self.url, **self.auth_headers)
        assert response.status_code == 405
