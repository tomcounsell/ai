"""Tests for the skills audit validation rules and sync script."""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parents[2]
        / ".claude"
        / "skills-global"
        / "do-skills-audit"
        / "scripts"
    ),
)

from audit_skills import (  # noqa: E402
    AuditReport,
    Finding,
    apply_fixes,
    audit_skill,
    discover_skills,
    parse_frontmatter,
    prune_husk_directories,
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
    rule_13_coupling_signals,
    rule_19_husk_directories,
    rule_21_bucket_c_coupling,
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
    skill_md.write_text("""---
name: test-skill
description: "Use when testing the audit system. Handles test validation."
---

# Test Skill

This is a test skill for validation purposes.
""")
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
    skill_md.write_text("""---
name: multi-skill
description: "Use when testing sub-file links."
---

# Multi Skill

See [reference](REFERENCE.md) and [examples](EXAMPLES.md) for details.
Also see [broken](NONEXISTENT.md) for more.
""")
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
        f = rule_04_description_trigger("test", {"description": "Use when testing things."})
        assert f.severity == "PASS"

    def test_with_triggered_by(self):
        f = rule_04_description_trigger("test", {"description": "Triggered by test requests."})
        assert f.severity == "PASS"

    def test_missing_trigger(self):
        f = rule_04_description_trigger("test", {"description": "A skill for doing things."})
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
    # NOTE: `setup`/`prime` moved to project-only (issue #1783) and are no longer
    # INFRA_SKILLS; `update` remains a representative infra skill.
    def test_infra_with_flag(self):
        f = rule_06_infra_classification("update", {"disable-model-invocation": True})
        assert f.severity == "PASS"

    def test_infra_without_flag(self):
        f = rule_06_infra_classification("update", {})
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
        skill_md.write_text("---\nname: test-skill\ndescription: 'has trailing  '\n---\n\nBody.")

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


# ---------------------------------------------------------------------------
# Rule 13: coupling-signal guard (issue #1783)
# ---------------------------------------------------------------------------

import audit_skills as audit_mod  # noqa: E402

LEANED_BODY = (
    "## Repo context\n"
    "If `.claude/skill-context/do-docs.md` exists, read it and honor its "
    "declarations; otherwise use the generic defaults described below.\n\n"
    "Run `sdlc-tool stage-marker` and `python -m tools.doc_impact_finder` "
    "only when the context file declares them.\n"
)

COUPLED_BODY_NO_PROBE = (
    "## Steps\n"
    "1. Run `sdlc-tool stage-query` to read pipeline state.\n"
    "2. Run `python -m tools.doc_impact_finder` over the diff.\n"
)


class TestRule13CouplingSignals:
    def test_green_leaned_body_passes(self):
        """Coupling signals present but with the probe step -> PASS, no FAIL."""
        f = rule_13_coupling_signals("do-docs", LEANED_BODY)
        assert f.severity == "PASS"
        assert f.rule == 13

    def test_red_coupled_without_probe_fails(self):
        """Coupling signals without the probe step -> FAIL severity."""
        f = rule_13_coupling_signals("do-docs", COUPLED_BODY_NO_PROBE)
        assert f.severity == "FAIL"
        assert f.rule == 13

    def test_clean_body_passes(self):
        f = rule_13_coupling_signals("generic-skill", "Just a normal generic body.")
        assert f.severity == "PASS"

    def test_doc_path_only_is_not_coupling(self):
        """Doc-path/branch-name mentions are NOT coupling (executable-only set).

        A bare see-also link to docs/features/ (or docs/plans/, session/{slug})
        does not break execution in a foreign repo, so it must PASS even without
        a probe — per plan Risk 2 the guard must not fire on Bucket A skills.
        """
        body = (
            "See [`docs/features/byob-browser-control.md`](../../../docs/features/byob.md).\n"
            "Plans live in `docs/plans/*.md`; branches use `session/{slug}`.\n"
        )
        f = rule_13_coupling_signals("mermaid-render", body)
        assert f.severity == "PASS"

    @pytest.mark.parametrize("body", ["", "no coupling here at all", "sdlc-tool"])
    def test_edge_cases_return_finding_no_exception(self, body):
        """Empty, coupling-only, and neither -> deterministic Finding, never raises."""
        f = rule_13_coupling_signals("x", body)
        assert isinstance(f, Finding)
        assert f.rule == 13

    def test_none_body_does_not_raise(self):
        f = rule_13_coupling_signals("x", None)  # type: ignore[arg-type]
        assert f.severity == "PASS"

    def test_main_exits_nonzero_on_coupling_violation(self, tmp_path, monkeypatch):
        """main() keys off summary['fail'] -> a FAIL coupling body yields exit code 1."""
        skills_dir = tmp_path / "skills-global"
        coupled = skills_dir / "coupled"
        coupled.mkdir(parents=True)
        (coupled / "SKILL.md").write_text(
            '---\nname: coupled\ndescription: "Use when testing coupling."\n---\n'
            + COUPLED_BODY_NO_PROBE
        )

        monkeypatch.setattr(audit_mod, "SKILLS_DIR", skills_dir)
        monkeypatch.setattr(
            sys,
            "argv",
            ["audit_skills.py", "--json", "--no-sync", "--skill", "coupled"],
        )
        exit_code = audit_mod.main()
        assert exit_code == 1

    def test_main_exits_zero_on_leaned_body(self, tmp_path, monkeypatch):
        """A leaned (probed) body does not trip the red-state exit."""
        skills_dir = tmp_path / "skills-global"
        leaned = skills_dir / "leaned"
        leaned.mkdir(parents=True)
        (leaned / "SKILL.md").write_text(
            '---\nname: leaned\ndescription: "Use when testing leaned bodies."\n---\n'
            + LEANED_BODY.replace("do-docs.md", "leaned.md")
        )

        monkeypatch.setattr(audit_mod, "SKILLS_DIR", skills_dir)
        monkeypatch.setattr(
            sys,
            "argv",
            ["audit_skills.py", "--json", "--no-sync", "--skill", "leaned"],
        )
        exit_code = audit_mod.main()
        assert exit_code == 0


# ---------------------------------------------------------------------------
# Rule 21: Bucket-C coupling (issue #2079)
# ---------------------------------------------------------------------------

# Project-only skill names a global body must not invoke as a slash command.
# In real runs these are derived live from .claude/skills/; tests pass them
# explicitly so the fixtures are self-contained.
BUCKET_C_PROJECT_ONLY = {"sdlc", "setup", "do-deploy", "prime", "update"}

# The five 61b55ce7 leaks modeled as literal fixtures (leaked -> corrected).
# These do NOT read git history — they reconstruct the pre-fix (leaked) prose
# and the same-line conditional-framed corrected prose.
LEAK_FIXTURES = {
    "audit-models": (
        "If the report surfaces substantive issues, escalate to the human or to `/sdlc`.",
        "If the report surfaces substantive issues, escalate to the human or route "
        "them into the repo's standard development workflow (in this repo: the SDLC pipeline).",
    ),
    "claude-standards": (
        "If the report surfaces substantive issues, escalate to the human or to `/sdlc`.",
        "If the report surfaces substantive issues, escalate to the human or route "
        "them into the repo's standard development workflow (in this repo: the SDLC pipeline).",
    ),
    "mermaid-render": (
        'If red, run `/setup` and answer "yes" to the computer-use opt-in.',
        "If red, re-run the machine's BYOB install/opt-in flow (in this repo, that "
        "is the setup skill; otherwise reinstall under `~/.byob`).",
    ),
    "do-issue": (
        "This skill is invoked by `/sdlc` at **Step 1: Ensure a GitHub Issue Exists**.",
        "This skill is invoked by the repo's SDLC router (in this repo: `/sdlc`) at "
        "**Step 1: Ensure a GitHub Issue Exists**.",
    ),
    "do-deploy-example": (
        "The `GH_REPO` environment variable is automatically set by `sdk_client.py`. "
        "When `SDLC_TARGET_REPO` is set, use it for all local git operations.",
        "Check whether `GH_REPO` is set (in this repo the harness exports it). Use a "
        "template-local DEPLOY_TARGET_REPO for local git operations, defaulting to the cwd.",
    ),
}


class TestRule21BucketCCoupling:
    @pytest.mark.parametrize("skill", sorted(LEAK_FIXTURES))
    def test_reverted_leak_fails(self, skill):
        """All 5 reverted 61b55ce7 fixtures FAIL rule_21 (AC1)."""
        leaked, _ = LEAK_FIXTURES[skill]
        f = rule_21_bucket_c_coupling(skill, leaked, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "FAIL"
        assert f.rule == 21

    @pytest.mark.parametrize("skill", sorted(LEAK_FIXTURES))
    def test_corrected_form_passes(self, skill):
        """Same-line conditional/probe-framed corrected forms PASS (AC1)."""
        _, corrected = LEAK_FIXTURES[skill]
        f = rule_21_bucket_c_coupling(skill, corrected, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "PASS"

    def test_global_skill_self_token_not_false_matched(self):
        """`/do-deploy-example` (a global skill) must not match `/do-deploy` (B1)."""
        # do-deploy-example is NOT in the project-only set; do-deploy IS.
        body = "Copy this template and invoke `/do-deploy-example` to try it out."
        f = rule_21_bucket_c_coupling("do-deploy-example", body, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "PASS"

    def test_hyphen_boundary_both_edges(self):
        """Trailing/leading hyphen edges are safe: `/setup` != `/setups`, `/sdlc` in `/do-sdlc`."""
        body = "Run `/setups-helper` and `/do-sdlc` — neither is a bare project-only invocation."
        f = rule_21_bucket_c_coupling("x", body, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "PASS"

    def test_fenced_signal_a_not_flagged(self):
        """A slash-token inside a code fence is a demo, not a coupling claim."""
        body = "Example usage:\n```\n/sdlc build\n```\nThat runs the pipeline."
        f = rule_21_bucket_c_coupling("x", body, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "PASS"

    def test_fenced_signal_b_not_flagged(self):
        body = '```bash\nREPO="${SDLC_TARGET_REPO:-.}"\n```'
        f = rule_21_bucket_c_coupling("x", body, BUCKET_C_PROJECT_ONLY)
        assert f.severity == "PASS"

    def test_signal_b_infra_token_fails(self):
        f = rule_21_bucket_c_coupling(
            "x", "The GH_REPO var is set by `sdk_client.py`.", BUCKET_C_PROJECT_ONLY
        )
        assert f.severity == "FAIL"

    def test_sub_file_signal_fails(self):
        """A bare `/sdlc` in a sub-file (not SKILL.md) FAILs — the Gap-2 scan surface."""
        f = rule_21_bucket_c_coupling(
            "x", "clean body", BUCKET_C_PROJECT_ONLY, sub_file_text="Escalate to `/sdlc`."
        )
        assert f.severity == "FAIL"

    @pytest.mark.parametrize("body", ["", "   \n\t  ", "no coupling here at all"])
    def test_empty_and_whitespace_pass(self, body):
        f = rule_21_bucket_c_coupling("x", body, BUCKET_C_PROJECT_ONLY)
        assert isinstance(f, Finding)
        assert f.severity == "PASS"

    def test_none_body_and_none_set_do_not_raise(self):
        f = rule_21_bucket_c_coupling("x", None, None)  # type: ignore[arg-type]
        assert f.severity == "PASS"

    def test_empty_project_only_never_fires(self):
        """A foreign repo with no project-only skills: Signal A cannot fire."""
        f = rule_21_bucket_c_coupling("x", "Run `/sdlc` now.", set())
        assert f.severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 13 + 21 sub-file scan and self-exemption (issue #2079, Gap 2)
# ---------------------------------------------------------------------------


class TestSubFileScan:
    def _make_skill(self, tmp_path, name, body, sub_files):
        skill_dir = tmp_path / "skills-global" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f'---\nname: {name}\ndescription: "Use when testing sub-file scans."\n---\n{body}'
        )
        for fname, content in sub_files.items():
            (skill_dir / fname).write_text(content)
        return skill_dir

    def _findings(self, tmp_path, skill_dir, rule):
        report = AuditReport()
        audit_skill(skill_dir / "SKILL.md", report, dir_label="global")
        return [f for f in report.results if f.rule == rule]

    def test_planted_coupling_in_subfile_fails_without_probe(self, tmp_path):
        """`sdlc-tool` in CHECKS.md FAILs rule_13 when SKILL.md lacks the probe (AC2)."""
        skill_dir = self._make_skill(
            tmp_path,
            "planted",
            "A generic body with no probe.\n",
            {"CHECKS.md": "Run `sdlc-tool stage-query` to read state."},
        )
        r13 = self._findings(tmp_path, skill_dir, 13)
        assert r13 and r13[0].severity == "FAIL"

    def test_planted_coupling_in_subfile_passes_with_probe(self, tmp_path):
        """With the SKILL.md probe, the same sub-file token is covered (AC2)."""
        body = (
            "If `.claude/skill-context/planted.md` exists, read it and honor its "
            "declarations; otherwise use the generic defaults described below.\n"
        )
        skill_dir = self._make_skill(
            tmp_path,
            "planted",
            body,
            {"CHECKS.md": "Run `sdlc-tool stage-query` to read state."},
        )
        r13 = self._findings(tmp_path, skill_dir, 13)
        assert r13 and r13[0].severity == "PASS"

    def test_do_skills_audit_self_exempt(self, tmp_path, monkeypatch):
        """do-skills-audit's own docs (which name /sdlc, sdk_client.py) stay PASS."""
        # Point the project-only derivation at a set that includes 'sdlc'.
        skills_dir = tmp_path / "skills-global"
        proj_dir = tmp_path / "skills"
        (proj_dir / "sdlc").mkdir(parents=True)
        (proj_dir / "sdlc" / "SKILL.md").write_text(
            '---\nname: sdlc\ndescription: "Use when routing."\n---\nrouter\n'
        )
        monkeypatch.setattr(audit_mod, "SKILLS_DIR", skills_dir)
        monkeypatch.setattr(audit_mod, "PROJECT_SKILLS_DIR", proj_dir)
        skill_dir = self._make_skill(
            tmp_path,
            "do-skills-audit",
            "The rule inventory documents `/sdlc`, `sdk_client.py`, `SDLC_TARGET_REPO`.\n",
            {"CHECKS.md": "It also mentions `sdlc-tool` and `/setup`."},
        )
        r13 = self._findings(tmp_path, skill_dir, 13)
        r21 = self._findings(tmp_path, skill_dir, 21)
        assert r13 and r13[0].severity == "PASS"
        assert r21 and r21[0].severity == "PASS"


# ---------------------------------------------------------------------------
# Rule 19: husk directories (issue #1902)
# ---------------------------------------------------------------------------


class TestRule19HuskDirectories:
    def test_empty_husk_fails_with_empty_message(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "empty-husk"
        husk.mkdir(parents=True)
        pycache = husk / "__pycache__"
        pycache.mkdir()
        (pycache / "mod.cpython-311.pyc").write_text("compiled")
        (husk / ".DS_Store").write_text("mac metadata")

        findings = rule_19_husk_directories(skills_dir, "global")

        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "FAIL"
        assert f.rule == 19
        assert "(empty)" in f.message

    def test_real_content_husk_fails_with_contains_message(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "orphan-husk"
        husk.mkdir(parents=True)
        (husk / "notes.md").write_text("orphaned content")

        findings = rule_19_husk_directories(skills_dir, "global")

        assert len(findings) == 1
        f = findings[0]
        assert f.severity == "FAIL"
        assert "(contains:" in f.message
        assert "notes.md" in f.message

    def test_dir_with_skill_md_is_not_a_husk(self, tmp_path):
        skills_dir = tmp_path / "skills"
        valid = skills_dir / "real-skill"
        valid.mkdir(parents=True)
        (valid / "SKILL.md").write_text("---\nname: real-skill\n---\n")

        findings = rule_19_husk_directories(skills_dir, "global")

        assert findings == []

    def test_underscore_prefixed_dir_is_exempt(self, tmp_path):
        skills_dir = tmp_path / "skills"
        shared = skills_dir / "_shared"
        shared.mkdir(parents=True)
        (shared / "helper.py").write_text("# shared helper, no SKILL.md here")

        findings = rule_19_husk_directories(skills_dir, "global")

        assert findings == []

    def test_nonexistent_skills_dir_returns_empty_list(self, tmp_path):
        missing = tmp_path / "does-not-exist"

        findings = rule_19_husk_directories(missing, "global")

        assert findings == []


# ---------------------------------------------------------------------------
# prune_husk_directories: rule 19 auto-fix companion (issue #1902)
# ---------------------------------------------------------------------------


class TestPruneHuskDirectories:
    def test_empty_husk_is_removed_and_reported(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "empty-husk"
        husk.mkdir(parents=True)
        (husk / "__pycache__").mkdir()
        (husk / "__pycache__" / "mod.cpython-311.pyc").write_text("compiled")

        removed = prune_husk_directories(skills_dir, "global")

        assert not husk.exists()
        assert len(removed) == 1
        assert "empty-husk" in removed[0]

    def test_husk_containing_orphaned_file_is_preserved_and_not_reported(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "orphan-husk"
        husk.mkdir(parents=True)
        (husk / "notes.md").write_text("orphaned content")

        removed = prune_husk_directories(skills_dir, "global")

        assert husk.exists()
        assert (husk / "notes.md").exists()
        assert removed == []

    def test_dir_with_skill_md_is_left_untouched(self, tmp_path):
        skills_dir = tmp_path / "skills"
        valid = skills_dir / "real-skill"
        valid.mkdir(parents=True)
        (valid / "SKILL.md").write_text("---\nname: real-skill\n---\n")

        removed = prune_husk_directories(skills_dir, "global")

        assert valid.exists()
        assert (valid / "SKILL.md").exists()
        assert removed == []

    def test_underscore_prefixed_dir_is_left_untouched(self, tmp_path):
        skills_dir = tmp_path / "skills"
        shared = skills_dir / "_shared"
        shared.mkdir(parents=True)

        removed = prune_husk_directories(skills_dir, "global")

        assert shared.exists()
        assert removed == []

    def test_oserror_on_one_husk_does_not_abort_sweep(self, tmp_path, monkeypatch):
        skills_dir = tmp_path / "skills"
        bad_husk = skills_dir / "bad-husk"
        good_husk = skills_dir / "good-husk"
        bad_husk.mkdir(parents=True)
        good_husk.mkdir(parents=True)
        (bad_husk / "__pycache__").mkdir()
        (good_husk / "__pycache__").mkdir()

        real_rmtree = shutil.rmtree

        def flaky_rmtree(path, *args, **kwargs):
            if Path(path).name == "bad-husk":
                raise OSError("simulated permission failure")
            return real_rmtree(path, *args, **kwargs)

        monkeypatch.setattr(shutil, "rmtree", flaky_rmtree)

        removed = prune_husk_directories(skills_dir, "global")

        assert bad_husk.exists()  # failed delete leaves the dir behind
        assert not good_husk.exists()  # sweep continues past the failure
        assert len(removed) == 1
        assert "good-husk" in removed[0]

    def test_warns_with_resolved_absolute_path_before_delete(self, tmp_path, caplog):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "log-husk"
        husk.mkdir(parents=True)
        (husk / "__pycache__").mkdir()
        resolved_path = str(husk.resolve())

        with caplog.at_level(logging.WARNING, logger="audit_skills"):
            removed = prune_husk_directories(skills_dir, "global")

        assert len(removed) == 1
        assert not husk.exists()
        messages = [record.getMessage() for record in caplog.records]
        assert any(resolved_path in message for message in messages)

    def test_toctou_guard_skips_dir_that_gained_a_file(self, tmp_path, monkeypatch):
        """A husk that looked empty at the initial scan but gained a real file
        before the immediate pre-delete re-check must NOT be removed."""
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "toctou-husk"
        husk.mkdir(parents=True)

        real_is_empty_husk = audit_mod._is_empty_husk
        call_count = {"n": 0}

        def fake_is_empty_husk(d):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Simulate a file landing in the directory between the
                # initial scan and the TOCTOU re-check immediately before
                # the delete.
                (d / "late_arrival.txt").write_text("real content, not a husk")
                return True
            return real_is_empty_husk(d)

        monkeypatch.setattr(audit_mod, "_is_empty_husk", fake_is_empty_husk)

        removed = prune_husk_directories(skills_dir, "global")

        assert call_count["n"] == 2
        assert removed == []
        assert husk.exists()
        assert (husk / "late_arrival.txt").exists()


# ---------------------------------------------------------------------------
# Prune-then-detect gating: the safety guarantee named in the plan
# (issue #1902) — a --fix run must never silently delete real orphans.
# ---------------------------------------------------------------------------


class TestPruneDetectGating:
    def test_prune_junk_only_husk_removed_no_fail(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "junk-only-husk"
        husk.mkdir(parents=True)
        (husk / "__pycache__").mkdir()
        (husk / "__pycache__" / "mod.cpython-311.pyc").write_text("compiled")
        (husk / ".DS_Store").write_text("mac metadata")

        # Simulate the --fix prune-then-detect order: prune first, then
        # re-run rule 19 detection on whatever remains.
        removed = prune_husk_directories(skills_dir, "global")
        findings = rule_19_husk_directories(skills_dir, "global")

        assert len(removed) == 1
        assert not husk.exists()
        assert findings == []  # nothing left to FAIL on

    def test_prune_real_content_husk_preserved_and_fails(self, tmp_path):
        skills_dir = tmp_path / "skills"
        husk = skills_dir / "real-content-husk"
        husk.mkdir(parents=True)
        (husk / "orphaned_notes.md").write_text("real orphaned content")

        # Same prune-then-detect sequence as a --fix run.
        removed = prune_husk_directories(skills_dir, "global")
        findings = rule_19_husk_directories(skills_dir, "global")

        assert removed == []
        assert husk.exists()
        assert (husk / "orphaned_notes.md").exists()
        assert len(findings) == 1
        assert findings[0].severity == "FAIL"
        assert "real-content-husk" in findings[0].skill
