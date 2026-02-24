"""Tests for the skills audit validation rules and sync script."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / ".claude"
        / "skills"
        / "do-skills-audit"
        / "scripts"
    ),
)

from audit_skills import (  # noqa: E402
    AuditReport,
    apply_fixes,
    audit_skill,
    discover_skills,
    parse_frontmatter,
    rule_01_line_count,
    rule_02_frontmatter_exists,
    rule_03_name_field,
    rule_04_description_trigger,
    rule_05_description_length,
    rule_06_infra_classification,
    rule_07_background_classification,
    rule_08_fork_classification,
    rule_09_sub_file_links,
    rule_10_duplicate_descriptions,
    rule_11_known_fields,
    rule_12_argument_hint,
)
from sync_best_practices import (  # noqa: E402
    compare_fields,
    extract_fields_from_docs,
    extract_line_limit,
    generate_report,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_skill(tmp_path):
    """Create a minimal valid skill for testing."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: test-skill
description: "Use when testing the audit system. Handles test validation."
---

# Test Skill

This is a test skill for validation purposes.
"""
    )
    return skill_dir


@pytest.fixture
def tmp_skill_with_subfiles(tmp_path):
    """Create a skill with sub-file references."""
    skill_dir = tmp_path / "multi-skill"
    skill_dir.mkdir()
    # Create sub-files
    (skill_dir / "REFERENCE.md").write_text("# Reference\nDetails here.")
    (skill_dir / "EXAMPLES.md").write_text("# Examples\nExamples here.")
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        """---
name: multi-skill
description: "Use when testing sub-file links."
---

# Multi Skill

See [reference](REFERENCE.md) and [examples](EXAMPLES.md) for details.
Also see [broken](NONEXISTENT.md) for more.
"""
    )
    return skill_dir


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


class TestFrontmatterParsing:
    def test_valid_frontmatter(self):
        text = "---\nname: foo\ndescription: bar\n---\n\nBody here."
        fm, body = parse_frontmatter(text)
        assert fm["name"] == "foo"
        assert fm["description"] == "bar"
        assert body == "Body here."

    def test_no_frontmatter(self):
        text = "Just some text without frontmatter."
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n---\n\nBody."
        fm, body = parse_frontmatter(text)
        assert fm == {} or fm is None  # yaml.safe_load returns None for empty
        # Our code handles None -> {}

    def test_multiline_description(self):
        text = '---\nname: foo\ndescription: "A long description\\nthat spans lines"\n---\n\nBody.'
        fm, body = parse_frontmatter(text)
        assert fm["name"] == "foo"


# ---------------------------------------------------------------------------
# Rule 1: Line count
# ---------------------------------------------------------------------------


class TestRule01LineCount:
    def test_pass(self):
        lines = ["line"] * 100
        f = rule_01_line_count("test", lines)
        assert f.severity == "PASS"

    def test_exactly_500(self):
        lines = ["line"] * 500
        f = rule_01_line_count("test", lines)
        assert f.severity == "PASS"

    def test_fail_501(self):
        lines = ["line"] * 501
        f = rule_01_line_count("test", lines)
        assert f.severity == "FAIL"


# ---------------------------------------------------------------------------
# Rule 2: Frontmatter exists
# ---------------------------------------------------------------------------


class TestRule02FrontmatterExists:
    def test_pass(self):
        f = rule_02_frontmatter_exists("test", {"name": "foo"})
        assert f.severity == "PASS"

    def test_fail(self):
        f = rule_02_frontmatter_exists("test", {})
        assert f.severity == "FAIL"


# ---------------------------------------------------------------------------
# Rule 3: Name field
# ---------------------------------------------------------------------------


class TestRule03NameField:
    def test_valid_name(self):
        f = rule_03_name_field("test-skill", {"name": "test-skill"}, "test-skill")
        assert f.severity == "PASS"

    def test_missing_name(self):
        f = rule_03_name_field("test", {}, "test")
        assert f.severity == "FAIL"

    def test_uppercase_name(self):
        f = rule_03_name_field("test", {"name": "Test-Skill"}, "test")
        assert f.severity == "FAIL"

    def test_name_mismatch(self):
        f = rule_03_name_field("test", {"name": "different"}, "test")
        assert f.severity == "FAIL"

    def test_too_long(self):
        long_name = "a" * 65
        f = rule_03_name_field(long_name, {"name": long_name}, long_name)
        assert f.severity == "FAIL"


# ---------------------------------------------------------------------------
# Rule 4: Description trigger
# ---------------------------------------------------------------------------


class TestRule04DescriptionTrigger:
    def test_with_use_when(self):
        f = rule_04_description_trigger(
            "test", {"description": "Use when testing things."}
        )
        assert f.severity == "PASS"

    def test_with_triggered_by(self):
        f = rule_04_description_trigger(
            "test", {"description": "Triggered by test requests."}
        )
        assert f.severity == "PASS"

    def test_missing_trigger(self):
        f = rule_04_description_trigger(
            "test", {"description": "A skill for doing things."}
        )
        assert f.severity == "WARN"

    def test_missing_description(self):
        f = rule_04_description_trigger("test", {})
        assert f.severity == "FAIL"


# ---------------------------------------------------------------------------
# Rule 5: Description length
# ---------------------------------------------------------------------------


class TestRule05DescriptionLength:
    def test_short(self):
        f = rule_05_description_length("test", {"description": "Short."})
        assert f.severity == "PASS"

    def test_too_long(self):
        f = rule_05_description_length("test", {"description": "x" * 1025})
        assert f.severity == "WARN"


# ---------------------------------------------------------------------------
# Rule 6: Infrastructure classification
# ---------------------------------------------------------------------------


class TestRule06InfraClassification:
    def test_infra_with_flag(self):
        f = rule_06_infra_classification("setup", {"disable-model-invocation": True})
        assert f.severity == "PASS"

    def test_infra_without_flag(self):
        f = rule_06_infra_classification("setup", {})
        assert f.severity == "WARN"

    def test_non_infra(self):
        f = rule_06_infra_classification("do-build", {})
        assert f.severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 7: Background classification
# ---------------------------------------------------------------------------


class TestRule07BackgroundClassification:
    def test_bg_with_flag(self):
        f = rule_07_background_classification("telegram", {"user-invocable": False})
        assert f.severity == "PASS"

    def test_bg_without_flag(self):
        f = rule_07_background_classification("telegram", {})
        assert f.severity == "WARN"

    def test_non_bg(self):
        f = rule_07_background_classification("do-build", {})
        assert f.severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 8: Fork classification
# ---------------------------------------------------------------------------


class TestRule08ForkClassification:
    def test_fork_with_flag(self):
        f = rule_08_fork_classification("do-build", {"context": "fork"})
        assert f.severity == "PASS"

    def test_fork_without_flag(self):
        f = rule_08_fork_classification("do-build", {})
        assert f.severity == "WARN"

    def test_non_fork(self):
        f = rule_08_fork_classification("telegram", {})
        assert f.severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 9: Sub-file links
# ---------------------------------------------------------------------------


class TestRule09SubFileLinks:
    def test_valid_links(self, tmp_skill_with_subfiles):
        body = "See [ref](REFERENCE.md) and [ex](EXAMPLES.md)."
        f = rule_09_sub_file_links("test", body, tmp_skill_with_subfiles)
        assert f.severity == "PASS"

    def test_broken_link(self, tmp_skill_with_subfiles):
        body = "See [broken](NONEXISTENT.md)."
        f = rule_09_sub_file_links("test", body, tmp_skill_with_subfiles)
        assert f.severity == "FAIL"
        assert "NONEXISTENT.md" in f.message

    def test_url_skipped(self, tmp_skill_with_subfiles):
        body = "See [docs](https://example.com)."
        f = rule_09_sub_file_links("test", body, tmp_skill_with_subfiles)
        assert f.severity == "PASS"

    def test_template_variable_skipped(self, tmp_skill_with_subfiles):
        body = "See [review]({review_url})."
        f = rule_09_sub_file_links("test", body, tmp_skill_with_subfiles)
        assert f.severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 10: Duplicate descriptions
# ---------------------------------------------------------------------------


class TestRule10DuplicateDescriptions:
    def test_no_duplicates(self):
        descs = {"skill-a": "Description A", "skill-b": "Description B"}
        findings = rule_10_duplicate_descriptions(descs)
        assert len(findings) == 0

    def test_duplicates(self):
        descs = {"skill-a": "Same description", "skill-b": "Same description"}
        findings = rule_10_duplicate_descriptions(descs)
        assert len(findings) == 1
        assert findings[0].severity == "WARN"


# ---------------------------------------------------------------------------
# Rule 11: Known fields
# ---------------------------------------------------------------------------


class TestRule11KnownFields:
    def test_all_known(self):
        f = rule_11_known_fields("test", {"name": "foo", "description": "bar"})
        assert f.severity == "PASS"

    def test_unknown_field(self):
        f = rule_11_known_fields("test", {"name": "foo", "custom-field": "value"})
        assert f.severity == "WARN"
        assert "custom-field" in f.message


# ---------------------------------------------------------------------------
# Rule 12: Argument hint
# ---------------------------------------------------------------------------


class TestRule12ArgumentHint:
    def test_no_arguments(self):
        f = rule_12_argument_hint("test", {}, "No arguments used here.")
        assert f.severity == "PASS"

    def test_arguments_with_hint(self):
        f = rule_12_argument_hint(
            "test",
            {"argument-hint": "[file]"},
            "Process $ARGUMENTS here.",
        )
        assert f.severity == "PASS"

    def test_arguments_without_hint(self):
        f = rule_12_argument_hint("test", {}, "Process $ARGUMENTS here.")
        assert f.severity == "WARN"

    def test_positional_arguments(self):
        f = rule_12_argument_hint("test", {}, "Use $0 and $1.")
        assert f.severity == "WARN"


# ---------------------------------------------------------------------------
# Fix helpers
# ---------------------------------------------------------------------------


class TestApplyFixes:
    def test_add_missing_name(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\ndescription: test\n---\n\nBody.")

        fm = {"description": "test"}
        text = skill_md.read_text()
        fixes = apply_fixes(skill_md, fm, text, "my-skill")

        assert len(fixes) == 1
        assert "Added name: my-skill" in fixes[0]
        # Verify file was updated
        new_text = skill_md.read_text()
        assert "my-skill" in new_text

    def test_trim_whitespace(self, tmp_path):
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: test-skill\ndescription: 'has trailing  '\n---\n\nBody."
        )

        fm = {"name": "test-skill", "description": "has trailing  "}
        text = skill_md.read_text()
        fixes = apply_fixes(skill_md, fm, text, "test-skill")

        assert any("Trimmed" in f for f in fixes)


# ---------------------------------------------------------------------------
# Full skill audit
# ---------------------------------------------------------------------------


class TestAuditSkill:
    def test_valid_skill(self, tmp_skill):
        report = AuditReport()
        audit_skill(tmp_skill / "SKILL.md", report)
        assert report.summary["fail"] == 0


# ---------------------------------------------------------------------------
# Sync best practices (deterministic parts)
# ---------------------------------------------------------------------------


class TestSyncBestPractices:
    def test_extract_fields(self):
        text = (
            "The `name` field is required. Use `description` for triggers."
            " The `model` field is optional."
        )
        fields = extract_fields_from_docs(text)
        assert "name" in fields
        assert "description" in fields
        assert "model" in fields

    def test_extract_line_limit(self):
        text = "Keep SKILL.md under 500 lines for optimal loading."
        limit = extract_line_limit(text)
        assert limit == 500

    def test_extract_line_limit_different(self):
        text = "Files should be less than 300 lines."
        limit = extract_line_limit(text)
        assert limit == 300

    def test_compare_fields_aligned(self):
        result = compare_fields({"name", "description"}, {"name", "description"})
        assert result["aligned"] == ["description", "name"]
        assert result["in_anthropic_not_ours"] == []
        assert result["in_ours_not_anthropic"] == []

    def test_compare_fields_drift(self):
        result = compare_fields(
            {"name", "description", "model"},
            {"name", "description", "custom"},
        )
        assert "model" in result["in_anthropic_not_ours"]
        assert "custom" in result["in_ours_not_anthropic"]

    def test_generate_report(self):
        docs = {
            "sources": {"skills_docs": "The `name` field is required."},
            "_cache_status": "FRESH",
            "_cache_age_days": 1,
        }
        our_state = {"fields": {"name", "description"}, "rules": {}}
        report = generate_report(docs, our_state)
        assert "alignments" in report
        assert "drifts" in report
        assert isinstance(report["alignments"], list)


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


class TestDiscovery:
    def test_discover_all(self, tmp_path):
        skills_dir = tmp_path / "skills"
        for name in ["alpha", "beta", "gamma"]:
            d = skills_dir / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
        paths = discover_skills(skills_dir)
        assert len(paths) == 3

    def test_discover_single(self, tmp_path):
        skills_dir = tmp_path / "skills"
        d = skills_dir / "target"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: target\n---\n")
        paths = discover_skills(skills_dir, "target")
        assert len(paths) == 1

    def test_discover_missing(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        paths = discover_skills(skills_dir, "nonexistent")
        assert len(paths) == 0
