"""Tests for scripts/migrate_completed_plan.py.

Covers Bug 1 fix: README-based display name extraction replacing .title() mangling.
"""

import contextlib
import os
import sys
import textwrap
from pathlib import Path

import pytest

# Import the functions under test directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.migrate_completed_plan import (  # noqa: E402
    extract_feature_doc_path,
    extract_feature_name_from_index,
    validate_feature_doc,
    validate_feature_index,
)

# --- Fixtures ---

SAMPLE_README = textwrap.dedent("""\
    # Feature Documentation Index

    | Feature | Description | Status |
    |---------|-------------|--------|
    | [PM/Dev Session Architecture](pm-dev-session-architecture.md) | PM/Dev split | Shipped |
    | [SDLC Critique Stage](sdlc-critique-stage.md) | Automated plan validation | Shipped |
    | [AI Evaluator](ai-evaluator.md) | Semantic build evaluation | Shipped |
    | [Bridge Self-Healing](bridge-self-healing.md) | Crash recovery | Shipped |
    | [Do-Build AI Evaluator](do-build-ai-evaluator.md) | AI evaluator step | Shipped |
""")


# --- Tests for extract_feature_name_from_index ---


class TestExtractFeatureNameFromIndex:
    """Test README-based display name extraction using the real function."""

    @pytest.fixture(autouse=True)
    def _setup_readme(self, tmp_path):
        """Create a docs/features/README.md and chdir so the real function finds it."""
        readme_path = tmp_path / "docs" / "features" / "README.md"
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(SAMPLE_README)
        self._tmp = tmp_path

    def _extract(self, filename: str) -> str | None:
        with _chdir(self._tmp):
            return extract_feature_name_from_index(filename)

    def test_acronym_heavy_filename_pm(self):
        """PM in filename should resolve to PM/Dev Session Architecture, not Pm/Dev..."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"

    def test_acronym_heavy_filename_sdlc(self):
        """SDLC in filename should resolve correctly."""
        result = self._extract("sdlc-critique-stage.md")
        assert result == "SDLC Critique Stage"

    def test_acronym_heavy_filename_ai(self):
        """AI in filename should resolve correctly."""
        result = self._extract("ai-evaluator.md")
        assert result == "AI Evaluator"

    def test_display_text_differs_from_filename(self):
        """Display text can contain characters not in filename (e.g., slashes)."""
        result = self._extract("pm-dev-session-architecture.md")
        assert result == "PM/Dev Session Architecture"
        # Verify .title() would have mangled this
        mangled = "pm-dev-session-architecture".replace("-", " ").title()
        assert mangled == "Pm Dev Session Architecture"  # Wrong!
        assert result != mangled

    def test_hyphenated_compound_name(self):
        """Compound names with hyphens (do-build) should resolve correctly."""
        result = self._extract("do-build-ai-evaluator.md")
        assert result == "Do-Build AI Evaluator"

    def test_missing_readme_entry(self):
        """Filename with no matching README row returns None."""
        result = self._extract("nonexistent-feature.md")
        assert result is None

    def test_simple_filename(self):
        """Simple filename without acronyms works fine."""
        result = self._extract("bridge-self-healing.md")
        assert result == "Bridge Self-Healing"


class TestValidateFeatureIndex:
    """Test feature index validation."""

    def test_feature_found_case_insensitive(self, tmp_path):
        """validate_feature_index finds features case-insensitively."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("PM/Dev Session Architecture")
            assert valid is True
            assert error == ""

    def test_feature_not_found(self, tmp_path):
        """validate_feature_index returns error for missing feature."""
        readme = tmp_path / "docs" / "features" / "README.md"
        readme.parent.mkdir(parents=True, exist_ok=True)
        readme.write_text(SAMPLE_README)

        with _chdir(tmp_path):
            valid, error = validate_feature_index("Nonexistent Feature XYZ")
            assert valid is False
            assert "Nonexistent Feature XYZ" in error

    def test_no_readme_file(self, tmp_path):
        """validate_feature_index handles missing README gracefully."""
        with _chdir(tmp_path):
            valid, error = validate_feature_index("Any Feature")
            assert valid is False
            assert "not found" in error


class TestValidateFeatureDoc:
    """Test feature doc validation."""

    def test_valid_doc(self, tmp_path):
        """Valid doc with title and content passes."""
        doc = tmp_path / "feature.md"
        doc.write_text("# My Feature\n\nThis is a substantial description of the feature.")
        valid, error = validate_feature_doc(doc)
        assert valid is True

    def test_missing_doc(self, tmp_path):
        """Missing doc fails gracefully."""
        doc = tmp_path / "nonexistent.md"
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "not found" in error

    def test_doc_without_title(self, tmp_path):
        """Doc without title heading fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("Just some text without a heading.")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "missing title" in error

    def test_doc_too_short(self, tmp_path):
        """Doc with only title and no content fails."""
        doc = tmp_path / "feature.md"
        doc.write_text("# Title\n\nShort")
        valid, error = validate_feature_doc(doc)
        assert valid is False
        assert "too short" in error


class TestExtractFeatureDocPath:
    """Test feature doc path extraction from plan text."""

    def test_extracts_create_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Create `docs/features/my-feature.md` describing the feature
            - [ ] Update README index
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/my-feature.md"

    def test_extracts_update_path(self):
        plan = textwrap.dedent("""\
            ## Documentation
            - [ ] Update `docs/features/existing.md` with new section
        """)
        result = extract_feature_doc_path(plan)
        assert result == "docs/features/existing.md"

    def test_no_documentation_section(self):
        plan = "## Other Section\nSome content"
        result = extract_feature_doc_path(plan)
        assert result is None


class TestEndToEndMigrationChain:
    """Integration test: full migration validation chain.

    Exercises the specific scenario that triggered the original bug:
    a feature named pm-dev-session-architecture with a README entry that
    says PM/Dev Session Architecture (not Pm Dev Session Architecture).
    """

    def test_full_chain_with_acronym_feature(self, tmp_path):
        """The migration chain works end-to-end with acronym-heavy filenames."""
        # Set up docs/features/ directory
        features_dir = tmp_path / "docs" / "features"
        features_dir.mkdir(parents=True)

        # Create the README index
        readme = features_dir / "README.md"
        readme.write_text(SAMPLE_README)

        # Create the feature doc
        feature_doc = features_dir / "pm-dev-session-architecture.md"
        feature_doc.write_text(
            "# PM/Teammate/Dev Session Architecture\n\n"
            "Session type discriminator splitting orchestration from execution.\n\n"
            "## Overview\nDetailed description of the architecture."
        )

        with _chdir(tmp_path):
            # Step 1: validate feature doc exists
            valid, error = validate_feature_doc(feature_doc)
            assert valid is True, f"Feature doc validation failed: {error}"

            # Step 2: extract name from index (the new way - Bug 1 fix)
            feature_name = extract_feature_name_from_index("pm-dev-session-architecture.md")
            assert feature_name is not None, "Failed to extract feature name"
            assert feature_name == "PM/Dev Session Architecture"

            # Step 3: validate the extracted name is in the index
            valid, error = validate_feature_index(feature_name)
            assert valid is True, f"Feature index validation failed: {error}"

            # Step 4: verify the OLD way (.title()) would have failed
            mangled_name = "pm-dev-session-architecture".replace("-", " ").title()
            assert mangled_name == "Pm Dev Session Architecture"
            # This would have failed because "Pm" != "PM"
            valid_old, _ = validate_feature_index(mangled_name)
            # The old approach fails: "Pm Dev Session Architecture"
            # won't match "PM/Dev Session Architecture" (missing "/")
            assert valid_old is False, "Old .title() approach should fail due to missing /"


# --- Helpers ---


@contextlib.contextmanager
def _chdir(path):
    """Context manager to temporarily change directory."""
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)
