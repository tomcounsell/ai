from datetime import timedelta

import pytest
from django.utils import timezone

from apps.book.models import Announcement


@pytest.mark.django_db
class TestAnnouncement:
    def test_create_announcement(self):
        announcement = Announcement.objects.create(
            title="Test Announcement",
            body="This is a test.",
            published_at=timezone.now(),
        )
        assert announcement.pk is not None
        assert str(announcement) == "Test Announcement"

    def test_is_published_when_published(self):
        announcement = Announcement(
            title="Published",
            body="Body",
            published_at=timezone.now() - timedelta(hours=1),
        )
        assert announcement.is_published is True

    def test_is_not_published_when_future(self):
        announcement = Announcement(
            title="Future",
            body="Body",
            published_at=timezone.now() + timedelta(hours=1),
        )
        assert announcement.is_published is False

    def test_is_not_published_when_none(self):
        announcement = Announcement(
            title="Draft",
            body="Body",
            published_at=None,
        )
        assert announcement.is_published is False

    def test_ordering(self):
        """Announcements are ordered by -published_at."""
        now = timezone.now()
        a1 = Announcement.objects.create(
            title="Older",
            body="Body",
            published_at=now - timedelta(days=2),
        )
        a2 = Announcement.objects.create(
            title="Newer",
            body="Body",
            published_at=now - timedelta(days=1),
        )
        announcements = list(Announcement.objects.all())
        assert announcements == [a2, a1]
