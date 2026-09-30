"""Tests that verify surviving new global skills exist on disk with required sections.

`deepen`, `observability`, and `tdd` were pruned (issue #2337) as never-dispatched
skills from the original 6-skill batch (#1319); they are intentionally absent from
this list.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
SKILLS_GLOBAL_DIR = REPO_ROOT / ".claude" / "skills-global"

NEW_SKILLS = [
    "ontologies",
    "grill-me",
    "zoom-out",
]

REQUIRED_SECTIONS = [
    "Purpose",
    "When to Use",
    "Steps",
    "Output",
    "Anti-Patterns",
]


@pytest.mark.parametrize("skill_name", NEW_SKILLS)
def test_skill_directory_exists(skill_name: str) -> None:
    """Each new skill must have a directory under .claude/skills-global/."""
    skill_dir = SKILLS_GLOBAL_DIR / skill_name
    assert skill_dir.is_dir(), f"Missing skill directory: {skill_dir}"


@pytest.mark.parametrize("skill_name", NEW_SKILLS)
def test_skill_md_exists(skill_name: str) -> None:
    """Each new skill must have a SKILL.md file."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    assert skill_md.is_file(), f"Missing SKILL.md: {skill_md}"


@pytest.mark.parametrize("skill_name", NEW_SKILLS)
def test_skill_md_has_required_sections(skill_name: str) -> None:
    """Each SKILL.md must contain all 5 required section headings."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    for section in REQUIRED_SECTIONS:
        assert re.search(rf"^##\s+{re.escape(section)}", content, re.MULTILINE), (
            f"SKILL.md for '{skill_name}' is missing section: ## {section}"
        )


@pytest.mark.parametrize("skill_name", NEW_SKILLS)
def test_skill_md_has_name_frontmatter(skill_name: str) -> None:
    """Each SKILL.md must declare a name: in frontmatter."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    assert re.search(r"^name:\s+\S+", content, re.MULTILINE), (
        f"SKILL.md for '{skill_name}' is missing 'name:' frontmatter"
    )


@pytest.mark.parametrize("skill_name", NEW_SKILLS)
def test_skill_md_has_nonempty_sections(skill_name: str) -> None:
    """Each required section in a SKILL.md must have non-empty content."""
    skill_md = SKILLS_GLOBAL_DIR / skill_name / "SKILL.md"
    content = skill_md.read_text()
    lines = content.splitlines()

    for section in REQUIRED_SECTIONS:
        # Find the section header
        section_start = None
        for i, line in enumerate(lines):
            if re.match(rf"^##\s+{re.escape(section)}\s*$", line):
                section_start = i
                break

        assert section_start is not None, (
            f"SKILL.md for '{skill_name}' missing section: ## {section}"
        )

        # Check that content follows the header (before the next ## section)
        section_content = []
        for line in lines[section_start + 1 :]:
            if line.startswith("## "):
                break
            section_content.append(line)

        non_empty = [line for line in section_content if line.strip()]
        assert non_empty, f"SKILL.md for '{skill_name}' section '## {section}' has no content"
