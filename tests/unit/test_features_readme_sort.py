"""Tests for the features README alphabetical sort validator.

Tests cover:
- Sorted table passes validation
- Unsorted table fails validation with correct error
- --fix mode sorts correctly
- Edge cases: empty table, single row, missing header, rows without links
- Hook stdin integration (pass-through for non-matching files)
"""

import subprocess
import sys
from pathlib import Path
from textwrap import dedent

# Import the validator module directly
sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent / ".claude" / "hooks" / "validators"),
)
from validate_features_readme_sort import (
    check_sort_order,
    extract_feature_name,
    parse_table_rows,
    sort_rows,
)

VALIDATOR_SCRIPT = str(
    Path(__file__).parent.parent.parent
    / ".claude"
    / "hooks"
    / "validators"
    / "validate_features_readme_sort.py"
)


class TestExtractFeatureName:
    """Test feature name extraction from table rows."""

    def test_standard_link(self):
        row = "| [Agent Session Model](agent-session-model.md) | Description | Shipped |"
        assert extract_feature_name(row) == "Agent Session Model"

    def test_no_link(self):
        row = "| Plain text | Description | Shipped |"
        assert extract_feature_name(row) is None

    def test_complex_name(self):
        row = "| [Scale Job Queue (Popoto + Worktrees)](scale-job-queue.md) | Desc | Shipped |"
        name = extract_feature_name(row)
        assert name == "Scale Job Queue (Popoto + Worktrees)"

    def test_link_with_path(self):
        row = "| [My Feature](path/to/feature.md) | Desc | Shipped |"
        assert extract_feature_name(row) == "My Feature"


class TestParseTableRows:
    """Test markdown table parsing."""

    def test_standard_table(self):
        content = dedent("""\
            # Feature Documentation Index

            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Alpha](alpha.md) | First | Shipped |
            | [Beta](beta.md) | Second | Shipped |

            ## Adding New Entries

            Instructions here.
        """)
        rows, start, end = parse_table_rows(content)
        assert len(rows) == 2
        assert "Alpha" in rows[0]
        assert "Beta" in rows[1]
        assert start > 0
        assert end > start

    def test_empty_table(self):
        content = dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|

            ## Adding New Entries
        """)
        rows, start, end = parse_table_rows(content)
        assert len(rows) == 0

    def test_single_row(self):
        content = dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Only One](one.md) | Sole entry | Shipped |

            ## Adding New Entries
        """)
        rows, start, end = parse_table_rows(content)
        assert len(rows) == 1
        assert "Only One" in rows[0]

    def test_missing_features_header(self):
        content = dedent("""\
            # Some Other Document

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Alpha](alpha.md) | First | Shipped |
        """)
        rows, start, end = parse_table_rows(content)
        assert len(rows) == 0

    def test_no_adding_entries_section(self):
        """Table extends to end of file with no next header."""
        content = dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Alpha](alpha.md) | First | Shipped |
            | [Beta](beta.md) | Second | Shipped |
        """)
        rows, start, end = parse_table_rows(content)
        assert len(rows) == 2


class TestCheckSortOrder:
    """Test alphabetical sort order validation."""

    def test_sorted_entries(self):
        rows = [
            "| [Alpha](alpha.md) | First | Shipped |",
            "| [Beta](beta.md) | Second | Shipped |",
            "| [Gamma](gamma.md) | Third | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert is_sorted
        assert len(violations) == 0

    def test_unsorted_entries(self):
        rows = [
            "| [Beta](beta.md) | Second | Shipped |",
            "| [Alpha](alpha.md) | First | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert not is_sorted
        assert len(violations) == 1
        assert violations[0][1] == "Alpha"
        assert violations[0][2] == "Beta"

    def test_case_insensitive_sorting(self):
        """'do-patch' and 'Do Test' compare as lowercase."""
        rows = [
            "| [Do Test](do-test.md) | Desc | Shipped |",
            "| [do-patch Skill](do-patch.md) | Desc | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert is_sorted

    def test_case_insensitive_violation(self):
        """Reversed case-insensitive order detected."""
        rows = [
            "| [do-patch Skill](do-patch.md) | Desc | Shipped |",
            "| [Do Test](do-test.md) | Desc | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert not is_sorted

    def test_empty_list(self):
        is_sorted, violations = check_sort_order([])
        assert is_sorted
        assert len(violations) == 0

    def test_single_entry(self):
        rows = ["| [Alpha](alpha.md) | First | Shipped |"]
        is_sorted, violations = check_sort_order(rows)
        assert is_sorted

    def test_sdk_sdlc_ordering(self):
        """SDK should sort before SDLC (case-insensitive)."""
        rows = [
            "| [SDK Modernization](sdk.md) | Desc | Shipped |",
            "| [SDLC Enforcement](sdlc.md) | Desc | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert is_sorted

    def test_rows_without_links_skipped(self):
        """Rows without link syntax should not cause errors."""
        rows = [
            "| [Alpha](alpha.md) | First | Shipped |",
            "| Plain text row | No link | Draft |",
            "| [Beta](beta.md) | Second | Shipped |",
        ]
        is_sorted, violations = check_sort_order(rows)
        assert is_sorted


class TestSortRows:
    """Test the auto-sort functionality."""

    def test_sorts_unsorted_rows(self):
        rows = [
            "| [Gamma](gamma.md) | Third | Shipped |",
            "| [Alpha](alpha.md) | First | Shipped |",
            "| [Beta](beta.md) | Second | Shipped |",
        ]
        sorted_result = sort_rows(rows)
        assert "Alpha" in sorted_result[0]
        assert "Beta" in sorted_result[1]
        assert "Gamma" in sorted_result[2]

    def test_already_sorted(self):
        rows = [
            "| [Alpha](alpha.md) | First | Shipped |",
            "| [Beta](beta.md) | Second | Shipped |",
        ]
        sorted_result = sort_rows(rows)
        assert sorted_result == rows

    def test_case_insensitive_sort(self):
        rows = [
            "| [do-patch Skill](do-patch.md) | Desc | Shipped |",
            "| [Do Test](do-test.md) | Desc | Shipped |",
        ]
        sorted_result = sort_rows(rows)
        assert "Do Test" in sorted_result[0]
        assert "do-patch" in sorted_result[1]


class TestCheckModeIntegration:
    """Integration tests using the CLI interface."""

    def test_sorted_file_passes(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Alpha](alpha.md) | First | Shipped |
            | [Beta](beta.md) | Second | Shipped |

            ## Adding New Entries
        """)
        )
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, "--check", str(readme)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_unsorted_file_fails(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Beta](beta.md) | Second | Shipped |
            | [Alpha](alpha.md) | First | Shipped |

            ## Adding New Entries
        """)
        )
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, "--check", str(readme)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
        assert "not alphabetically sorted" in result.stderr
        assert '"Alpha" should come before "Beta"' in result.stderr

    def test_fix_mode_sorts_file(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Gamma](gamma.md) | Third | Shipped |
            | [Alpha](alpha.md) | First | Shipped |
            | [Beta](beta.md) | Second | Shipped |

            ## Adding New Entries

            Instructions here.
        """)
        )
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, "--fix", str(readme)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "Sorted 3 entries" in result.stdout

        # Verify file is now sorted
        content = readme.read_text()
        lines = content.split("\n")
        data_rows = [line for line in lines if line.strip().startswith("| [")]
        names = [extract_feature_name(r) for r in data_rows]
        assert names == ["Alpha", "Beta", "Gamma"]

    def test_fix_already_sorted(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text(
            dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Alpha](alpha.md) | First | Shipped |
            | [Beta](beta.md) | Second | Shipped |

            ## Adding New Entries
        """)
        )
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, "--fix", str(readme)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "already sorted" in result.stdout

    def test_missing_features_header_passes(self, tmp_path):
        readme = tmp_path / "README.md"
        readme.write_text("# Just a normal markdown file\n\nSome text.\n")
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, "--check", str(readme)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


class TestHookStdinIntegration:
    """Test the hook stdin protocol for Claude Code integration."""

    def test_non_matching_file_passes_through(self):
        """Non-README file in stdin should pass through."""
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT],
            input='{"tool_input":{"file_path":"some/other/file.md"}}',
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_matching_file_validates(self, tmp_path):
        """README.md in stdin triggers validation."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True)
        readme.write_text(
            dedent("""\
            ## Features

            | Feature | Description | Status |
            |---------|-------------|--------|
            | [Beta](beta.md) | Second | Shipped |
            | [Alpha](alpha.md) | First | Shipped |

            ## Adding New Entries
        """)
        )
        stdin_json = f'{{"tool_input":{{"file_path":"{readme}"}}}}'
        result = subprocess.run(
            [sys.executable, VALIDATOR_SCRIPT, str(readme)],
            input=stdin_json,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 2
