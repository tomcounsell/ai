"""
Tests to verify template paths work correctly.
"""

import os

from django.conf import settings
from django.test import TestCase


class TemplatePathsTestCase(TestCase):
    """Test that template paths resolve correctly."""

    def test_template_files_exist(self):
        """
        Test that required template files exist in the app templates directory.
        """
        app_template_dir = os.path.join(settings.BASE_DIR, "apps/public/templates")

        required_templates = [
            "base.html",
            "pages/home.html",
            "account/login.html",
            "layout/footer.html",
            "layout/nav/navbar.html",
            "layout/nav/account_menu.html",
            "layout/messages/toast.html",
        ]

        for template_path in required_templates:
            app_file_path = os.path.join(app_template_dir, template_path)

            self.assertTrue(
                os.path.isfile(app_file_path),
                f"Template {template_path} should exist in app template directory",
            )
