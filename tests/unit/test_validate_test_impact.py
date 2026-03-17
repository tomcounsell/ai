"""Unit tests for validate_test_impact_section.py hook validator."""

import sys
from pathlib import Path

# Hook scripts live in .claude/hooks/validators/
VALIDATORS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import the validator module."""
    import validate_test_impact_section

    return validate_test_impact_section


class TestExtractTestImpactSection:
    """Tests for extracting the ## Test Impact section."""

    def test_extracts_section(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Test Impact

- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: new assertion

## Rabbit Holes

None.
"""
        section = mod.extract_test_impact_section(content)
        assert section is not None
        assert "UPDATE" in section

    def test_returns_none_when_missing(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Rabbit Holes

None.
"""
        section = mod.extract_test_impact_section(content)
        assert section is None

    def test_extracts_section_at_end_of_file(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Test Impact

No existing tests affected — this is greenfield with no prior coverage.
"""
        section = mod.extract_test_impact_section(content)
        assert section is not None
        assert "greenfield" in section


class TestIsSectionComplete:
    """Tests for checking section completeness."""

    def test_accepts_checklist_with_dispositions(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: new return value\n"
            "- [ ] `tests/unit/test_baz.py::test_qux` — DELETE: removed feature"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "disposition" in reason.lower()

    def test_accepts_single_disposition(self):
        mod = import_validator()
        content = "- [ ] `tests/unit/test_foo.py::test_bar` — REPLACE: rewrite for new API contract"
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete

    def test_accepts_no_tests_affected_with_justification(self):
        mod = import_validator()
        content = (
            "No existing tests affected — this is a greenfield feature with no prior test coverage."
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "no existing tests affected" in reason.lower()

    def test_rejects_no_tests_affected_without_justification(self):
        mod = import_validator()
        content = "No existing tests affected"
        is_complete, reason = mod.is_section_complete(content)
        assert not is_complete
        assert "too brief" in reason.lower()

    def test_rejects_empty_section(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("")
        assert not is_complete
        assert "empty" in reason.lower()

    def test_rejects_placeholder_tbd(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("TBD")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_dots(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("...")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_bracket(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("[Fill in later]")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_too_brief_content(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("Some short text.")
        assert not is_complete
        assert "too brief" in reason.lower()

    def test_accepts_checklists_without_dispositions_if_substantive(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py` needs to be updated for the new interface changes\n"
            "- [ ] `tests/integration/test_bar.py` should be modified to use new endpoint"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "checklist" in reason.lower() or "items" in reason.lower()

    def test_case_insensitive_dispositions(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py::test_bar`"
            " — update: new assertion value for changed output"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete

    def test_checked_items_accepted(self):
        mod = import_validator()
        content = (
            "- [x] `tests/unit/test_foo.py::test_bar`"
            " — UPDATE: new assertion value for changed output"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete


class TestValidateTestImpactSection:
    """Tests for the full validation function."""

    def test_passes_valid_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: assert new return value

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert success

    def test_fails_missing_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert not success
        assert "missing" in message.lower()

    def test_fails_empty_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert not success

    def test_fails_nonexistent_file(self):
        mod = import_validator()
        success, message = mod.validate_test_impact_section("/nonexistent/path.md")
        assert not success
        assert "failed to read" in message.lower()

    def test_passes_exemption_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage.

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert success
