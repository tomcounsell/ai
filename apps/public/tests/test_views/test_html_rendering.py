"""
Integration tests for HTML rendering in views.

These tests verify that views:
- Render HTML correctly with templates
- Include context variables in rendered output
- Apply correct template blocks
- Properly handle form rendering
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.common.tests.factories import UserFactory

User = get_user_model()


class HTMLRenderingTestCase(TestCase):
    """Integration tests for rendered HTML from views."""

    def setUp(self):
        """Set up test data."""
        self.user = UserFactory.create(
            username="htmluser",
            email="htmluser@example.com",
            password="testpassword123",
        )

    def test_login_page_renders_correctly(self):
        """Test that the login page renders correctly."""
        url = reverse("public:account-login")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/login.html")

        # Check that the HTML contains expected elements
        html = response.content.decode("utf-8")
        self.assertIn("<form", html)
        self.assertIn('name="username"', html)
        self.assertIn('name="password"', html)
        self.assertIn('type="submit"', html)

    def test_settings_page_logged_in(self):
        """Test that the settings page renders correctly when logged in."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("public:account-settings")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "account/settings.html")

        # Check that the HTML contains expected elements
        html = response.content.decode("utf-8")
        self.assertIn('value="htmluser@example.com"', html)  # Email value
        self.assertIn('name="first_name"', html)
        self.assertIn('name="last_name"', html)
        self.assertIn('name="old_password"', html)  # Password form

    def test_dashboard_page_logged_in(self):
        """Test that the dashboard page renders correctly when logged in."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("public:dashboard")
        response = self.client.get(url)

        # Check status code and template
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "pages/home.html")

        # Check base template is extended
        self.assertTemplateUsed(response, "base.html")

    def test_form_error_rendering(self):
        """Test that form errors are properly rendered."""
        url = reverse("public:account-login")
        response = self.client.post(
            url, {"username": "nonexistent", "password": "wrongpassword"}
        )

        # Check form errors are rendered
        html = response.content.decode("utf-8")
        self.assertIn("error", html.lower())

    def test_message_rendering(self):
        """Test that messages are properly rendered."""
        self.client.login(username="htmluser", password="testpassword123")
        url = reverse("public:account-settings")

        # Make a valid form submission that should generate success message
        response = self.client.post(
            url,
            {
                "first_name": "Updated",
                "last_name": "User",
                "email": "htmluser@example.com",
            },
            follow=True,
        )

        # Check that success message is rendered
        messages = list(response.context["messages"])
        self.assertTrue(len(messages) > 0)
        self.assertEqual(str(messages[0]), "User settings updated.")


class DashboardBrandCSSTestCase(TestCase):
    """Tests that the dashboard template uses brand.css classes instead of inline styles."""

    def setUp(self):
        """Set up test data and fetch the dashboard HTML."""
        self.user = UserFactory.create(
            username="branduser",
            email="branduser@example.com",
            password="testpassword123",
        )
        self.client.login(username="branduser", password="testpassword123")
        url = reverse("public:dashboard")
        response = self.client.get(url)
        self.html = response.content.decode("utf-8")

    def test_uses_section_hero_class(self):
        """Test that the outer container uses section-hero class."""
        self.assertIn('class="section-hero"', self.html)

    def test_uses_card_technical_class(self):
        """Test that the MCP overview section uses card-technical class."""
        self.assertIn('class="card-technical"', self.html)

    def test_uses_status_indicator_classes(self):
        """Test that status dots use status-indicator and status-operational classes."""
        self.assertIn("status-indicator", self.html)
        self.assertIn("status-operational", self.html)

    def test_no_hardcoded_green_color(self):
        """Test that hardcoded #4CAF50 is not used in inline styles."""
        self.assertNotIn("#4CAF50", self.html)

    def test_no_underscore_case_labels(self):
        """Test that UNDERSCORE_CASE labels are removed."""
        underscore_labels = [
            "PLATFORM_SPECIFICATIONS",
            "PROTOCOL_OVERVIEW",
            "BENEFIT_01",
            "BENEFIT_02",
            "BENEFIT_03",
            "AVAILABLE_SERVERS",
            "MCP_SERVER_01",
            "MCP_SERVER_02",
            "IMPLEMENTATION_DETAILS",
        ]
        for label in underscore_labels:
            self.assertNotIn(label, self.html, f"Found UNDERSCORE_CASE label: {label}")

    def test_no_onmouseover_handlers(self):
        """Test that onmouseover/onmouseout inline handlers are removed."""
        self.assertNotIn("onmouseover", self.html)
        self.assertNotIn("onmouseout", self.html)

    def test_uses_footer_link_class(self):
        """Test that the repository link uses footer-link class."""
        self.assertIn("footer-link", self.html)

    def test_uses_text_technical_label_class(self):
        """Test that implementation detail labels use text-technical-label class."""
        self.assertIn("text-technical-label", self.html)

    def test_spec_table_no_redundant_inline_styles(self):
        """Test that spec-table-inline td elements don't have redundant inline styles."""
        # The spec-table-inline class handles font-family, font-size, padding, and
        # first-child color. So td elements inside it should not need those inline.
        import re

        # Find all td elements inside spec-table-inline context
        # After migration, td elements should be plain <td> without class="text-mono"
        # and without redundant inline font-size/padding styles
        spec_table_match = re.search(
            r'class="spec-table-inline".*?</table>', self.html, re.DOTALL
        )
        if spec_table_match:
            table_html = spec_table_match.group()
            self.assertNotIn('class="text-mono"', table_html)

    def test_h2_no_inline_mono_overrides(self):
        """Test that h2 elements don't have inline mono font overrides."""
        import re

        h2_matches = re.findall(r"<h2[^>]*>", self.html)
        for h2 in h2_matches:
            self.assertNotIn("text-mono", h2)
            self.assertNotIn("font-family", h2)

    def test_h3_no_inline_mono_overrides(self):
        """Test that h3 elements don't have inline mono font overrides."""
        import re

        h3_matches = re.findall(r"<h3[^>]*>", self.html)
        for h3 in h3_matches:
            self.assertNotIn("text-mono", h3)
            self.assertNotIn("font-family", h3)
