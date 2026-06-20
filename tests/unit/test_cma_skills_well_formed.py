"""Structural tests for the /imagine-agent and /build-agent CMA global skills.

Validates that:
- Both skill directories exist under .claude/skills-global/
- Each SKILL.md is present and carries the required YAML frontmatter keys
  (name:, description:, allowed-tools:)
- The build-agent reference files (cma-primitives.md, build-sheet.md) exist
- The persona tools.md segment references imagine-agent (integration point)

These are structural / presence tests only — they do not execute the skills or
call the Anthropic CMA API.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_GLOBAL_DIR = REPO_ROOT / ".claude" / "skills-global"

CMA_SKILLS = ["imagine-agent", "build-agent"]

# NOTE: CMA skills use "## What this skill does" / "## Anti-patterns", not the
# "Purpose"/"When to Use"/"Steps"/"Output"/"Anti-Patterns" headings of
# test_skills_exist.py — do NOT add REQUIRED_SECTIONS-style body checks here.


@pytest.mark.parametrize("skill_name", CMA_SKILLS)
def test_cma_skill_directory_exists(skill_name: str) -> None:
    """Each CMA skill must have a directory under .claude/skills-global/."""
    skill_dir = SKILLS_GLOBAL_DIR / skill_name
    assert skill_dir.is_dir(), f"Missing CMA skill directory: {skill_dir}"


@pytest.mark.parametrize("skill_name", CMA_SKILLS)
def test_cma_skill_md_exists(skill_name: str) -> None:
    """Each CMA skill must have a SKILL.md file."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    assert skill_md.is_file(), f"Missing SKILL.md for CMA skill '{skill_name}': {skill_md}"


@pytest.mark.parametrize("skill_name", CMA_SKILLS)
def test_cma_skill_md_has_name_frontmatter(skill_name: str) -> None:
    """Each CMA SKILL.md must declare a 'name:' key in frontmatter."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    # name: must appear in the YAML frontmatter block (before the first ---)
    assert "name:" in content, f"SKILL.md for '{skill_name}' is missing 'name:' frontmatter"


@pytest.mark.parametrize("skill_name", CMA_SKILLS)
def test_cma_skill_md_has_description_frontmatter(skill_name: str) -> None:
    """Each CMA SKILL.md must declare a 'description:' key in frontmatter."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    assert "description:" in content, (
        f"SKILL.md for '{skill_name}' is missing 'description:' frontmatter"
    )


@pytest.mark.parametrize("skill_name", CMA_SKILLS)
def test_cma_skill_md_has_allowed_tools_frontmatter(skill_name: str) -> None:
    """Each CMA SKILL.md must declare an 'allowed-tools:' key in frontmatter."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    assert "allowed-tools:" in content, (
        f"SKILL.md for '{skill_name}' is missing 'allowed-tools:' frontmatter"
    )


def test_build_agent_reference_files_exist() -> None:
    """The build-agent references directory must contain both required reference files."""
    refs_dir = SKILLS_GLOBAL_DIR / "build-agent" / "references"

    cma_primitives = refs_dir / "cma-primitives.md"
    assert cma_primitives.is_file(), f"Missing build-agent reference file: {cma_primitives}"

    build_sheet = refs_dir / "build-sheet.md"
    assert build_sheet.is_file(), f"Missing build-agent reference file: {build_sheet}"


def test_tools_md_mentions_imagine_agent() -> None:
    """The persona tools.md segment must reference imagine-agent (CMA subsection check)."""
    tools_md = REPO_ROOT / "config" / "personas" / "segments" / "tools.md"
    assert tools_md.is_file(), f"Persona tools.md not found: {tools_md}"
    content = tools_md.read_text()
    assert "imagine-agent" in content, (
        "config/personas/segments/tools.md must mention 'imagine-agent' "
        "(the Managed Agent Creation subsection is missing or incomplete)"
    )
