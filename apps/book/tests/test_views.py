from datetime import timedelta

import pytest
from django.utils import timezone

from apps.book.models import Announcement


@pytest.mark.django_db
class TestLandingView:
    def test_landing_page_renders(self, client):
        response = client.get("/", HTTP_HOST="blendedworkforce.ai")
        assert response.status_code == 200

    def test_landing_uses_correct_template(self, client):
        response = client.get("/", HTTP_HOST="blendedworkforce.ai")
        assert "book/landing.html" in [t.name for t in response.templates]


@pytest.mark.django_db
class TestAnnouncementListView:
    def test_announcements_page_renders(self, client):
        response = client.get("/announcements/", HTTP_HOST="blendedworkforce.ai")
        assert response.status_code == 200

    def test_shows_published_announcements(self, client):
        Announcement.objects.create(
            title="Published Post",
            body="Content here.",
            published_at=timezone.now() - timedelta(hours=1),
        )
        response = client.get("/announcements/", HTTP_HOST="blendedworkforce.ai")
        assert b"Published Post" in response.content

    def test_hides_draft_announcements(self, client):
        Announcement.objects.create(
            title="Draft Post",
            body="Not visible.",
            published_at=None,
        )
        response = client.get("/announcements/", HTTP_HOST="blendedworkforce.ai")
        assert b"Draft Post" not in response.content

    def test_hides_future_announcements(self, client):
        Announcement.objects.create(
            title="Future Post",
            body="Not yet.",
            published_at=timezone.now() + timedelta(days=1),
        )
        response = client.get("/announcements/", HTTP_HOST="blendedworkforce.ai")
        assert b"Future Post" not in response.content

    def test_empty_state(self, client):
        response = client.get("/announcements/", HTTP_HOST="blendedworkforce.ai")
        assert b"No announcements yet" in response.content
