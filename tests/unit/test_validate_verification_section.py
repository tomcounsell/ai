"""Unit tests for validate_verification_section.py hook validator."""

import sys
from pathlib import Path

# Hook scripts live in .claude/hooks/validators/
VALIDATORS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import the validator module."""
    import validate_verification_section

    return validate_verification_section


class TestExtractVerificationSection:
    """Tests for extracting the ## Verification section."""

    def test_extracts_section(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |

## Open Questions

None.
"""
        section = mod.extract_verification_section(content)
        assert section is not None
        assert "echo hi" in section

    def test_returns_none_when_missing(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Success Criteria

- [ ] Done
"""
        section = mod.extract_verification_section(content)
        assert section is None


class TestIsSectionComplete:
    """Tests for checking section completeness."""

    def test_valid_table(self):
        mod = import_validator()
        section = """\
| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |
"""
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is True

    def test_empty_section(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("")
        assert is_complete is False

    def test_no_data_rows(self):
        mod = import_validator()
        section = """\
| Check | Command | Expected |
|-------|---------|----------|
"""
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is False

    def test_placeholder_text(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("[TODO]")
        assert is_complete is False


class TestValidateVerificationSection:
    """Integration tests for the full validation function."""

    def test_valid_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is True

    def test_missing_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is False
        assert "missing" in msg.lower() or "VALIDATION FAILED" in msg

    def test_incomplete_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Verification

TBD

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is False
