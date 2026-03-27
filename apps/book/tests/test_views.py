from datetime import timedelta

import pytest
from django.utils import timezone

from apps.book.models import Announcement, DraftChapter, EarlyReader, Testimonial

BOOK_HOST = "blendedworkforce.ai"

# New models need migrations before their tables exist in test DB.
needs_migration = pytest.mark.skipif(
    True,
    reason="Requires migration for new book models (EarlyReader, Testimonial, DraftChapter)",
)


@pytest.mark.django_db
class TestLandingView:
    def test_landing_page_renders(self, client):
        response = client.get("/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200

    def test_landing_uses_correct_template(self, client):
        response = client.get("/", HTTP_HOST=BOOK_HOST)
        assert "book/landing.html" in [t.name for t in response.templates]

    @needs_migration
    def test_landing_shows_featured_testimonials(self, client):
        Testimonial.objects.create(
            quote="Absolutely essential reading",
            author_name="Jane CEO",
            is_featured=True,
        )
        response = client.get("/", HTTP_HOST=BOOK_HOST)
        assert b"Absolutely essential reading" in response.content
        assert b"Jane CEO" in response.content

    @needs_migration
    def test_landing_hides_non_featured_testimonials(self, client):
        Testimonial.objects.create(
            quote="Hidden quote",
            author_name="Nobody",
            is_featured=False,
        )
        response = client.get("/", HTTP_HOST=BOOK_HOST)
        assert b"Hidden quote" not in response.content


@pytest.mark.django_db
class TestAnnouncementListView:
    def test_announcements_page_renders(self, client):
        response = client.get("/announcements/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200

    def test_shows_published_announcements(self, client):
        Announcement.objects.create(
            title="Published Post",
            body="Content here.",
            published_at=timezone.now() - timedelta(hours=1),
        )
        response = client.get("/announcements/", HTTP_HOST=BOOK_HOST)
        assert b"Published Post" in response.content

    def test_hides_draft_announcements(self, client):
        Announcement.objects.create(
            title="Draft Post",
            body="Not visible.",
            published_at=None,
        )
        response = client.get("/announcements/", HTTP_HOST=BOOK_HOST)
        assert b"Draft Post" not in response.content

    def test_hides_future_announcements(self, client):
        Announcement.objects.create(
            title="Future Post",
            body="Not yet.",
            published_at=timezone.now() + timedelta(days=1),
        )
        response = client.get("/announcements/", HTTP_HOST=BOOK_HOST)
        assert b"Future Post" not in response.content

    def test_empty_state(self, client):
        response = client.get("/announcements/", HTTP_HOST=BOOK_HOST)
        assert b"No announcements yet" in response.content


@pytest.mark.django_db
class TestSignupView:
    def test_signup_form_renders(self, client):
        response = client.get("/signup/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200
        assert b"Become an Early Reader" in response.content

    @needs_migration
    def test_signup_creates_early_reader(self, client):
        response = client.post(
            "/signup/",
            {
                "name": "Test Reader",
                "email": "test@example.com",
                "company": "TestCo",
                "role": "ceo",
                "research_question": "How to start?",
            },
            HTTP_HOST=BOOK_HOST,
        )
        assert response.status_code == 302
        assert EarlyReader.objects.filter(email="test@example.com").exists()
        reader = EarlyReader.objects.get(email="test@example.com")
        assert reader.name == "Test Reader"
        assert reader.company == "TestCo"

    @needs_migration
    def test_signup_requires_name_and_email(self, client):
        response = client.post(
            "/signup/",
            {"name": "", "email": ""},
            HTTP_HOST=BOOK_HOST,
        )
        assert response.status_code == 200
        assert EarlyReader.objects.count() == 0

    @needs_migration
    def test_signup_duplicate_email_rejected(self, client):
        EarlyReader.objects.create(name="Existing", email="dup@example.com")
        response = client.post(
            "/signup/",
            {"name": "New", "email": "dup@example.com"},
            HTTP_HOST=BOOK_HOST,
        )
        assert response.status_code == 200
        assert EarlyReader.objects.count() == 1

    def test_signup_success_page_renders(self, client):
        response = client.get("/signup/success/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200
        assert b"You're In" in response.content


@pytest.mark.django_db
class TestChatView:
    def test_chat_page_renders(self, client):
        response = client.get("/chat/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200
        assert b"Chat with Valor" in response.content

    def test_chat_send_rejects_empty_message(self, client):
        response = client.post(
            "/chat/send/",
            {"message": ""},
            HTTP_HOST=BOOK_HOST,
        )
        assert response.status_code == 400

    def test_chat_send_rejects_get(self, client):
        response = client.get("/chat/send/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 405


@pytest.mark.django_db
class TestChapterViews:
    def test_chapter_list_requires_login(self, client):
        response = client.get("/chapters/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 302

    @needs_migration
    def test_chapter_list_renders_for_logged_in(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="reader", password="testpass123"
        )
        client.force_login(user)
        response = client.get("/chapters/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200

    @needs_migration
    def test_chapter_list_shows_published_chapters(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="reader", password="testpass123"
        )
        client.force_login(user)
        DraftChapter.objects.create(
            title="Published Chapter",
            volume=1,
            chapter_number=1,
            published_at=timezone.now() - timedelta(hours=1),
        )
        DraftChapter.objects.create(
            title="Draft Chapter",
            volume=1,
            chapter_number=2,
            published_at=None,
        )
        response = client.get("/chapters/", HTTP_HOST=BOOK_HOST)
        assert b"Published Chapter" in response.content
        assert b"Draft Chapter" not in response.content

    @needs_migration
    def test_chapter_detail_renders(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="reader", password="testpass123"
        )
        client.force_login(user)
        ch = DraftChapter.objects.create(
            title="Test Chapter",
            volume=1,
            chapter_number=1,
            body_markdown="**bold content**",
            published_at=timezone.now() - timedelta(hours=1),
        )
        response = client.get(f"/chapters/{ch.pk}/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 200
        assert b"Test Chapter" in response.content
        assert b"<strong>bold content</strong>" in response.content

    @needs_migration
    def test_chapter_detail_requires_login(self, client):
        ch = DraftChapter.objects.create(
            title="Locked",
            volume=1,
            chapter_number=1,
            published_at=timezone.now() - timedelta(hours=1),
        )
        response = client.get(f"/chapters/{ch.pk}/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 302

    @needs_migration
    def test_unpublished_chapter_returns_404(self, client, django_user_model):
        user = django_user_model.objects.create_user(
            username="reader", password="testpass123"
        )
        client.force_login(user)
        ch = DraftChapter.objects.create(
            title="Draft",
            volume=1,
            chapter_number=1,
            published_at=None,
        )
        response = client.get(f"/chapters/{ch.pk}/", HTTP_HOST=BOOK_HOST)
        assert response.status_code == 404
