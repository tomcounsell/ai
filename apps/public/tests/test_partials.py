import os

from django.conf import settings
from django.test import TestCase


def get_templates_dir():
    """Return the path to the app templates directory."""
    return os.path.join(settings.BASE_DIR, "apps", "public", "templates")


class PartialsDirectoryTestCase(TestCase):
    """
    Tests for validating the components directory structure and naming conventions
    """

    def test_components_directory_exists(self):
        """Test that the components directory exists in the templates directory"""
        templates_dir = get_templates_dir()
        components_dir = os.path.join(templates_dir, "components")
        self.assertTrue(
            os.path.exists(components_dir), "Components directory does not exist"
        )
        self.assertTrue(
            os.path.isdir(components_dir), "Components path is not a directory"
        )

    def test_components_directory_structure(self):
        """Test that the components directory has appropriate subdirectories"""
        templates_dir = get_templates_dir()
        components_dir = os.path.join(templates_dir, "components")

        # Define expected subdirectories for components
        expected_subdirs = ["forms", "lists", "cards", "modals", "common"]

        for subdir in expected_subdirs:
            subdir_path = os.path.join(components_dir, subdir)
            self.assertTrue(
                os.path.exists(subdir_path),
                f"Components subdirectory '{subdir}' does not exist",
            )
            self.assertTrue(
                os.path.isdir(subdir_path),
                f"Components path '{subdir}' is not a directory",
            )

    def test_component_base_template_exists(self):
        """Test that a base template for components exists"""
        templates_dir = get_templates_dir()
        component_base_path = os.path.join(
            templates_dir, "components", "_component_base.html"
        )

        self.assertTrue(
            os.path.exists(component_base_path),
            "Component base template does not exist",
        )

        # Check content of the base component template
        with open(component_base_path) as f:
            content = f.read()

        # Verify that it has the required block(s)
        self.assertIn(
            "{% block content %}",
            content,
            "Component base template is missing content block",
        )
        self.assertIn(
            "{% endblock content %}",
            content,
            "Component base template is missing endblock with name",
        )

    def test_component_naming_conventions(self):
        """Test that component templates in typed subdirectories follow naming conventions.

        Only checks templates that use {% extends %} (full components), not
        include-only snippets which may use simpler names.
        """
        import re

        templates_dir = get_templates_dir()
        components_dir = os.path.join(templates_dir, "components")

        # Skip test if directory doesn't exist yet (will be caught by earlier test)
        if not os.path.exists(components_dir):
            return

        # Directories to skip: forms has different conventions, oob/layout are utility dirs,
        # common has standalone snippets, and the root components dir contains utility templates
        skip_dirs = {"forms", "oob", "layout", "components", "common"}

        # Walk through typed subdirectories (cards, lists, modals)
        for root, dirs, files in os.walk(components_dir):
            subdir_name = os.path.basename(root)
            if subdir_name in skip_dirs:
                continue

            for file in files:
                # Skip examples.html and modal_base.html
                if file == "examples.html" or file == "modal_base.html":
                    continue
                if file.endswith(".html") and not file.startswith("_"):
                    # Only check naming for templates that use extends (full components)
                    file_path = os.path.join(root, file)
                    with open(file_path) as f:
                        content = f.read()
                    if not re.search(r"{%\s*extends\s+", content):
                        continue

                    # Check if the file name follows the convention of type_name.html
                    # e.g., "card_team.html", "list_items.html", etc.
                    parts = file.replace(".html", "").split("_")
                    self.assertTrue(
                        len(parts) >= 2,
                        f"Component template '{file}' should follow naming convention 'type_name.html'",
                    )

                    # Check that the file is in the correct subdirectory
                    if subdir_name != "modals":
                        expected_prefix = (
                            subdir_name[:-1]
                            if subdir_name.endswith("s")
                            else subdir_name
                        )
                        self.assertEqual(
                            parts[0],
                            expected_prefix,
                            f"Component in '{subdir_name}' should have prefix '{expected_prefix}_'",
                        )

    def test_component_templates_extend_base(self):
        """Test that component templates with extends use the correct base template.

        Only validates templates that use {% extends %} (full components).
        Include-only snippets (no extends) are valid and skipped.
        """
        import re

        templates_dir = get_templates_dir()
        components_dir = os.path.join(templates_dir, "components")

        # Skip test if directory doesn't exist yet (will be caught by earlier test)
        if not os.path.exists(components_dir):
            return

        # Directories to skip: forms may not extend base, oob/layout are utility dirs,
        # and the root components dir contains utility templates
        skip_dirs = {"forms", "oob", "layout", "components"}

        # Walk through all subdirectories
        for root, dirs, files in os.walk(components_dir):
            subdir_name = os.path.basename(root)
            if subdir_name in skip_dirs:
                continue

            for file in files:
                # Skip base templates that don't need to extend anything
                if file == "modal_base.html":
                    continue

                if file.endswith(".html") and not file.startswith("_"):
                    file_path = os.path.join(root, file)
                    with open(file_path) as f:
                        content = f.read()

                    # Check if the file has an extends statement
                    extends_pattern = r'{%\s*extends\s+"([^"]+)"\s*%}'
                    extends_match = re.search(extends_pattern, content)

                    # Skip include-only snippets (no extends statement)
                    if extends_match is None:
                        continue

                    # Get the template that it extends
                    extends_template = extends_match.group(1)

                    # Also skip examples.html which has special inheritance
                    if file == "examples.html":
                        continue

                    # Check if it's in the modals directory
                    if os.path.basename(root) == "modals":
                        # Modals that are not examples.html should extend a modal base template
                        acceptable_bases = [
                            "modals/modal_base.html",
                            "components/modals/modal_base.html",
                        ]
                        self.assertTrue(
                            any(base in extends_template for base in acceptable_bases),
                            f"Modal template '{file}' should extend a modal base template",
                        )
                    else:
                        # Other components should extend component_base or base templates
                        acceptable_bases = [
                            "components/_component_base.html",
                            "partial.html",
                        ]
                        self.assertTrue(
                            any(base in extends_template for base in acceptable_bases),
                            f"Component template '{file}' should extend one of: {acceptable_bases}",
                        )
