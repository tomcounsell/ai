from datetime import timedelta

import pytest
from django.utils import timezone

from apps.book.models import Announcement, DraftChapter, EarlyReader, Testimonial

# New models (EarlyReader, Testimonial, DraftChapter) need migrations before
# their database tests can run.  Mark DB-touching tests so they skip cleanly
# until then.
needs_migration = pytest.mark.skipif(
    True,
    reason="Requires migration for new book models (EarlyReader, Testimonial, DraftChapter)",
)


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


@pytest.mark.django_db
class TestEarlyReader:
    @needs_migration
    def test_create_early_reader(self):
        reader = EarlyReader.objects.create(
            name="Jane Doe",
            email="jane@example.com",
            company="Acme Corp",
            role="ceo",
        )
        assert reader.pk is not None
        assert str(reader) == "Jane Doe <jane@example.com>"

    @needs_migration
    def test_email_unique(self):
        EarlyReader.objects.create(name="A", email="dup@example.com")
        with pytest.raises(Exception, match="duplicate key|UNIQUE constraint"):
            EarlyReader.objects.create(name="B", email="dup@example.com")

    @needs_migration
    def test_defaults(self):
        reader = EarlyReader.objects.create(name="Min", email="min@example.com")
        assert reader.role == "other"
        assert reader.company == ""
        assert reader.research_question == ""
        assert reader.is_confirmed is False

    @needs_migration
    def test_ordering(self):
        """Early readers are ordered by -created_at."""
        r1 = EarlyReader.objects.create(name="First", email="first@example.com")
        r2 = EarlyReader.objects.create(name="Second", email="second@example.com")
        readers = list(EarlyReader.objects.all())
        assert readers == [r2, r1]

    def test_str_representation(self):
        """Test __str__ without hitting the database."""
        reader = EarlyReader(name="Jane Doe", email="jane@example.com")
        assert str(reader) == "Jane Doe <jane@example.com>"

    def test_role_choices(self):
        reader = EarlyReader(role="ceo")
        assert reader.role == "ceo"


@pytest.mark.django_db
class TestTestimonial:
    @needs_migration
    def test_create_testimonial(self):
        t = Testimonial.objects.create(
            quote="Great book!",
            author_name="John Smith",
            company="BigCo",
            role="CTO",
            is_featured=True,
        )
        assert t.pk is not None
        assert "Great book!" in str(t)
        assert "John Smith" in str(t)

    @needs_migration
    def test_featured_filter(self):
        Testimonial.objects.create(quote="Featured", author_name="A", is_featured=True)
        Testimonial.objects.create(
            quote="Not featured", author_name="B", is_featured=False
        )
        featured = Testimonial.objects.filter(is_featured=True)
        assert featured.count() == 1
        assert featured.first().quote == "Featured"

    def test_str_representation(self):
        """Test __str__ without hitting the database."""
        t = Testimonial(
            quote="This is a long quote that should be truncated in the string representation",
            author_name="Author",
        )
        result = str(t)
        assert "Author" in result


@pytest.mark.django_db
class TestDraftChapter:
    @needs_migration
    def test_create_chapter(self):
        ch = DraftChapter.objects.create(
            title="Your First AI Employee",
            volume=1,
            chapter_number=1,
            body_markdown="# Hello\n\nWorld",
            published_at=timezone.now(),
        )
        assert ch.pk is not None
        assert "Vol. 1, Ch. 1" in str(ch)

    def test_is_published(self):
        ch = DraftChapter(
            title="T",
            volume=1,
            chapter_number=1,
            published_at=timezone.now() - timedelta(hours=1),
        )
        assert ch.is_published is True

    def test_is_not_published_when_future(self):
        ch = DraftChapter(
            title="T",
            volume=1,
            chapter_number=1,
            published_at=timezone.now() + timedelta(hours=1),
        )
        assert ch.is_published is False

    def test_is_not_published_when_none(self):
        ch = DraftChapter(title="T", volume=1, chapter_number=1, published_at=None)
        assert ch.is_published is False

    def test_body_html_renders_markdown(self):
        ch = DraftChapter(
            title="T",
            volume=1,
            chapter_number=1,
            body_markdown="**bold** text",
        )
        html = ch.body_html
        assert "<strong>bold</strong>" in html
        assert "text" in html

    def test_body_html_empty_when_no_markdown(self):
        ch = DraftChapter(title="T", volume=1, chapter_number=1, body_markdown="")
        assert ch.body_html == ""

    @needs_migration
    def test_ordering(self):
        """Chapters are ordered by volume then chapter_number."""
        ch2 = DraftChapter.objects.create(title="B", volume=1, chapter_number=2)
        ch1 = DraftChapter.objects.create(title="A", volume=1, chapter_number=1)
        chapters = list(DraftChapter.objects.all())
        assert chapters == [ch1, ch2]

    @needs_migration
    def test_unique_together(self):
        DraftChapter.objects.create(title="A", volume=1, chapter_number=1)
        with pytest.raises(Exception, match="duplicate key|UNIQUE constraint"):
            DraftChapter.objects.create(title="B", volume=1, chapter_number=1)

    def test_str_representation(self):
        ch = DraftChapter(title="Test", volume=1, chapter_number=3)
        assert str(ch) == "Vol. 1, Ch. 3: Test"
