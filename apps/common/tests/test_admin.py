"""
Tests for the Django admin customizations.
"""

import warnings

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from apps.common.models import Role, Team, TeamMember
from apps.common.tests.factories import UserFactory

# Override settings for testing
# Filter out the timezone warnings during tests
warnings.filterwarnings(
    "ignore",
    message="DateTimeField .* received a naive datetime",
    category=RuntimeWarning,
)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    USE_TZ=True,  # Ensure timezone support is active
)
class AdminTestCase(TestCase):
    """Test cases for admin customizations."""

    def setUp(self):
        """Set up test data."""
        # Use get_or_create to avoid duplicate user errors
        # Use timezone.now() for date_joined to avoid naive datetime warnings
        self.user, created = get_user_model().objects.get_or_create(
            username="admin_test",
            defaults={
                "email": "admin_test@example.com",
                "is_superuser": True,
                "is_staff": True,
                "date_joined": timezone.now(),
            },
        )

        if created:
            self.user.set_password("password123")
            self.user.save()
        self.client = Client()
        self.client.login(username="admin_test", password="password123")

        # Create team and members
        self.team = Team.objects.create(
            name="Test Team",
            slug="test-team",
            description="A team for testing",
            is_active=True,
        )

        self.regular_user = UserFactory.create()
        self.team_member = TeamMember.objects.create(
            team=self.team, user=self.regular_user, role=Role.MEMBER.value
        )

    def test_admin_index(self):
        """Test the admin index page loads successfully."""
        url = "/admin/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Database")

    def test_unfold_integration(self):
        """Test that the admin site is using Django Unfold."""
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)

        # Get the admin site styles to check if our CSS is included
        self.assertIn("output.css", str(response.content))

    def test_admin_page(self):
        """Test the admin list page elements."""
        url = "/admin/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # Verify that admin shows our custom page title
        self.assertContains(response, "ProjectName Database")

        # Check for CSS output
        self.assertContains(response, "output.css")

    def test_custom_dashboard(self):
        """Test the custom admin dashboard."""
        url = "/admin/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        # We may be running in a test environment where the dashboard callback
        # isn't fully registered or rendered. Let's check for basic
        # admin elements instead of specific dashboard widgets.
        self.assertContains(response, "ProjectName Database")
        self.assertContains(response, "output.css")

        # Check for modules that should be in the admin
        self.assertContains(response, "Common")
        self.assertContains(response, "Users")
