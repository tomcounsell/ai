"""
Tests for the briefing landing page (/briefing/).

Verifies:
- Anonymous GET returns 200 (no redirect)
- Authenticated GET returns 200
- Anonymous response contains anonymous CTA text
- Authenticated response contains authenticated CTA text
- URL resolves by name (public:briefing)
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

User = get_user_model()


class BriefingLandingViewTestCase(TestCase):
    """Tests for BriefingLandingView at /briefing/."""

    def setUp(self):
        self.client = Client()
        self.url = reverse("public:briefing")

    def test_route_resolves(self):
        """URL named public:briefing resolves without error."""
        self.assertEqual(self.url, "/briefing/")

    def test_anonymous_get_returns_200(self):
        """Anonymous GET /briefing/ returns HTTP 200, not a redirect."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_anonymous_does_not_redirect(self):
        """Anonymous users are not redirected away from /briefing/."""
        response = self.client.get(self.url)
        self.assertNotEqual(response.status_code, 302)

    def test_anonymous_sees_signup_cta(self):
        """Anonymous response contains 'Get Your First Briefing' CTA."""
        response = self.client.get(self.url)
        self.assertContains(response, "Get Your First Briefing")

    def test_anonymous_cta_links_to_signup(self):
        """Anonymous CTA points to /accounts/signup/."""
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")
        self.assertIn("/accounts/signup/", content)

    def test_anonymous_does_not_see_authenticated_cta(self):
        """Anonymous users do not see the 'Start a Briefing' CTA."""
        response = self.client.get(self.url)
        self.assertNotContains(response, "Start a Briefing")

    def test_authenticated_get_returns_200(self):
        """Authenticated GET /briefing/ returns HTTP 200."""
        user = User.objects.create_user(
            username="test_briefing_user",
            email="briefing@test.com",
            password="testpass123",
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)

    def test_authenticated_sees_start_briefing_cta(self):
        """Authenticated response contains 'Start a Briefing' CTA."""
        user = User.objects.create_user(
            username="test_briefing_user2",
            email="briefing2@test.com",
            password="testpass123",
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertContains(response, "Start a Briefing")

    def test_authenticated_does_not_see_anonymous_cta(self):
        """Authenticated users do not see the 'Get Your First Briefing' CTA."""
        user = User.objects.create_user(
            username="test_briefing_user3",
            email="briefing3@test.com",
            password="testpass123",
        )
        self.client.force_login(user)
        response = self.client.get(self.url)
        self.assertNotContains(response, "Get Your First Briefing")

    def test_page_contains_hero_headline(self):
        """Page contains the emotional hook headline content."""
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")
        # Headline text is split across lines in the template; check a key phrase
        self.assertIn("Walk into any meeting", content)

    def test_page_contains_how_it_works_section(self):
        """Page contains the three-step workflow section."""
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")
        self.assertIn("STEP_01", content)
        self.assertIn("STEP_02", content)
        self.assertIn("STEP_03", content)

    def test_page_contains_what_you_get_section(self):
        """Page contains the what-you-get section."""
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")
        self.assertIn("WHAT_YOU_GET", content)

    def test_template_used(self):
        """briefing.html template is used for the view."""
        response = self.client.get(self.url)
        self.assertTemplateUsed(response, "briefing.html")

    def test_uses_brand_css_classes(self):
        """Template uses documented brand.css classes (section-hero, product-card, etc.)."""
        response = self.client.get(self.url)
        content = response.content.decode("utf-8")
        self.assertIn("section-hero", content)
        self.assertIn("product-card", content)
        self.assertIn("divider-technical", content)
        self.assertIn("text-technical-label", content)
        self.assertIn("btn-brand", content)
