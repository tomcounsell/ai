from django.conf import settings
from django.test import TestCase

from apps.ai.apps import AIConfig


class AIConfigTest(TestCase):
    """
    Test case for the AI app configuration.
    """

    def test_app_config(self):
        """Test the AI app is correctly configured."""
        self.assertEqual(AIConfig.name, "apps.ai")
        self.assertEqual(AIConfig.label, "ai")
        self.assertEqual(AIConfig.default_auto_field, "django.db.models.BigAutoField")

    def test_app_installed(self):
        """Test the AI app is installed in the project."""
        # This test may fail during initial runs before server restart
        # as Django might not have fully loaded the app
        self.assertTrue(
            "apps.ai" in settings.INSTALLED_APPS or "apps.ai" in settings.PROJECT_APPS
        )
