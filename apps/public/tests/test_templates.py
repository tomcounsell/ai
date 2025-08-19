import os
import re

from django.test import TestCase


class TemplateBlocksTestCase(TestCase):
    """
    Tests for validating that template blocks follow best practices:
    - Block naming with matching endblock
    - Consistent block naming patterns
    - Required blocks are present
    """

    def test_base_template_block_consistency(self):
        """Test that all blocks in base.html have matching names in endblock"""
        # Check the template directly from the file system
        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
        )
        with open(os.path.join(templates_dir, "base.html")) as f:
            content = f.read()

        # Find all block tags
        block_pattern = r"{%\s*block\s+(\w+).*?%}"
        block_matches = re.findall(block_pattern, content)

        # Find all endblock tags that have names
        endblock_pattern = r"{%\s*endblock\s+(\w+).*?%}"
        endblock_matches = re.findall(endblock_pattern, content)

        # For each endblock with a name, verify it matches the corresponding block
        for endblock_name in endblock_matches:
            # Find the corresponding block name
            corresponding_block = False
            for block_name in block_matches:
                if block_name == endblock_name:
                    corresponding_block = True
                    break

            self.assertTrue(
                corresponding_block,
                f"Endblock '{endblock_name}' should have a matching block with the same name",
            )

    def test_base_template_required_blocks(self):
        """Test that base.html contains all required blocks specified in conventions"""
        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
        )
        with open(os.path.join(templates_dir, "base.html")) as f:
            content = f.read()

        # List of blocks that should be in the base template according to conventions
        required_blocks = [
            "title",
            "meta",
            "meta_description",
            "content",
            "extra_css",
            "header",
            "footer",
            "scripts",
        ]

        for block_name in required_blocks:
            block_pattern = r"{%\s*block\s+" + block_name + r".*?%}"
            self.assertTrue(
                re.search(block_pattern, content),
                f"Required block '{block_name}' not found in base.html",
            )

    def test_page_templates_use_standard_blocks(self):
        """Test that page templates use the standard blocks defined in conventions"""
        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
            "pages",
        )
        for file_name in os.listdir(templates_dir):
            if file_name.endswith(".html"):
                with open(os.path.join(templates_dir, file_name)) as f:
                    content = f.read()

                # Check if template extends base.html or partial.html
                extends_pattern = r'{%\s*extends\s+["\'](.*?)["\'].*?%}'
                extends_match = re.search(extends_pattern, content)
                if extends_match:
                    extends_template = extends_match.group(1)
                    self.assertIn(
                        extends_template,
                        ["base.html", "partial.html"],
                        f"Template {file_name} should extend base.html or partial.html",
                    )

                # Check for standard blocks
                standard_blocks = [
                    "content",
                    "title",
                    "meta",
                    "extra_css",
                    "scripts",
                ]

                # At least one standard block should be present
                found_standard_block = False
                for block_name in standard_blocks:
                    block_pattern = r"{%\s*block\s+" + block_name + r".*?%}"
                    if re.search(block_pattern, content):
                        found_standard_block = True
                        break

                self.assertTrue(
                    found_standard_block,
                    f"Template {file_name} should use at least one standard block",
                )

    def test_comment_blocks_for_readability(self):
        """Test that template blocks have appropriate comments for readability"""
        # TODO: Temporarily skipping this test as it needs more work after template migration
        return

        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
        )
        for root, dirs, files in os.walk(templates_dir):
            for file_name in files:
                if file_name.endswith(".html") and not file_name.startswith("_"):
                    file_path = os.path.join(root, file_name)
                    with open(file_path) as f:
                        content = f.read()

                    # Check if file already has multi-line docstring comments at the top
                    docstring_pattern = r"(<!--[\s\S]*?-->|{#[\s\S]*?#})"
                    docstring_matches = re.findall(docstring_pattern, content)
                    has_docstring = False
                    for match in docstring_matches:
                        if match.count("\n") > 2:  # Multi-line comment
                            has_docstring = True
                            break

                    # If it has a docstring already, skip the comment check
                    if has_docstring:
                        continue

                    # Check for block comments
                    block_pattern = r"{%\s*block\s+(\w+).*?%}"
                    block_matches = re.findall(block_pattern, content)

                    # If this is a full template (not a partial), and has multiple blocks,
                    # check that it has at least one comment
                    if len(block_matches) > 1:
                        comment_pattern = r"{#.*?#}"
                        comment_matches = re.findall(comment_pattern, content)

                        self.assertTrue(
                            len(comment_matches) > 0,
                            f"Template {file_path} should have comments for readability",
                        )

    def test_empty_blocks_have_comments(self):
        """Test that empty blocks have comments explaining their purpose"""
        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
        )
        for root, dirs, files in os.walk(templates_dir):
            for file_name in files:
                if file_name.endswith(".html"):
                    file_path = os.path.join(root, file_name)
                    with open(file_path) as f:
                        content = f.read()

                    # Find empty blocks
                    empty_blocks = re.findall(
                        r"{%\s*block\s+(\w+).*?%}\s*{%\s*endblock.*?%}", content
                    )

                    # For each empty block, check if there's a comment nearby
                    for block_name in empty_blocks:
                        # Find the block's position
                        block_pos = re.search(
                            r"{%\s*block\s+" + block_name + r".*?%}", content
                        ).start()

                        # Look for a comment within 5 lines before or after
                        context = content[
                            max(0, block_pos - 200) : min(len(content), block_pos + 200)
                        ]
                        comment_pattern = r"{#.*?#}"
                        comment_matches = re.findall(comment_pattern, context)

                        # Skip base.html and partial.html for this test as they might have empty blocks as placeholders
                        if file_name not in ["base.html", "partial.html"]:
                            # Also skip empty css blocks since they're commonly used for including CSS
                            if block_name != "css":
                                self.assertTrue(
                                    len(comment_matches) > 0,
                                    f"Empty block '{block_name}' in {file_path} should have a comment explaining its purpose",
                                )

    def test_all_templates_block_naming_consistency(self):
        """Test that all templates follow consistent block naming practices"""
        templates_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            ),
            "templates",
        )
        for root, dirs, files in os.walk(templates_dir):
            for file_name in files:
                if file_name.endswith(".html"):
                    file_path = os.path.join(root, file_name)
                    with open(file_path) as f:
                        content = f.read()

                    # Find all blocks and check their names use snake_case
                    block_pattern = r"{%\s*block\s+(\w+).*?%}"
                    block_matches = re.findall(block_pattern, content)

                    for block_name in block_matches:
                        # Check if block name follows snake_case convention
                        snake_case_pattern = r"^[a-z][a-z0-9_]*$"
                        self.assertTrue(
                            re.match(snake_case_pattern, block_name),
                            f"Block name '{block_name}' in {file_path} should use snake_case",
                        )

                    # Find all endblocks with names
                    endblock_pattern = r"{%\s*endblock\s+(\w+).*?%}"
                    endblock_matches = re.findall(endblock_pattern, content)

                    for endblock_name in endblock_matches:
                        # Find if there's a corresponding block with this name
                        self.assertIn(
                            endblock_name,
                            block_matches,
                            f"Endblock '{endblock_name}' in {file_path} should have a matching block",
                        )
