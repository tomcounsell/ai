"""A skill that dispatches subagents must be allowed to (#2649).

`allowed-tools` in a SKILL.md frontmatter is a restriction: per
`.claude/skills-global/new-skill/SKILL.md`, "Restricts which tools the skill
can use. Omit to allow all." So a skill whose body instructs the driver to
dispatch subagents, while its own `allowed-tools` omits `Agent`, asks for
something it has forbidden itself.

`do-pr-review` was in exactly that state: `context: fork`, an `allowed-tools`
list without `Agent`, and a body mandating judge subagents — with this repo
opting into multi-judge consensus by default
(`SDLC_REVIEW_JUDGES=code-quality,risk`, `docs/sdlc/do-pr-review.md`). The
failure is silent: the driver inlines the judges instead, so reviews still post
and no gate trips, they just lose the independent-context property that makes a
second judge worth having.

This is a consistency check between two declarations in the same file, not a
claim about harness behavior. Whether a `context: fork` context also strips
`Agent` is a separate question this test does not speak to.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOTS = (REPO_ROOT / ".claude" / "skills-global", REPO_ROOT / ".claude" / "skills")

# Body phrasings that mean "spawn a subagent". Kept to unambiguous forms: a
# skill merely mentioning the word "agent" is not dispatching one.
DISPATCH_PATTERNS = (
    re.compile(r"\bAgent tool\b"),
    re.compile(r"\b(?:Agent|Task)\(\{"),
    re.compile(r"\bdispatch(?:es|ing)? (?:the )?(?:critics|judges|subagents?)\b", re.I),
    re.compile(r"\bjudge subagents?\b", re.I),
    re.compile(r"\bspawn(?:s|ing)? .{0,20}subagents?\b", re.I),
)


def _skill_files() -> list[Path]:
    files: list[Path] = []
    for root in SKILL_ROOTS:
        if root.exists():
            files.extend(sorted(root.glob("*/SKILL.md")))
    return files


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter, body). Empty frontmatter when there is none."""
    if not text.startswith("---"):
        return "", text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return "", text
    return parts[1], parts[2]


def _allowed_tools(frontmatter: str) -> list[str] | None:
    """The declared tool list, or None when the key is absent (= allow all)."""
    match = re.search(r"^allowed-tools:(.*)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    return [t.strip() for t in match.group(1).split(",") if t.strip()]


def test_skill_inventory_is_non_empty():
    """Guard the guard: a glob that stops finding skills must fail loudly
    rather than silently vacuously pass."""
    assert len(_skill_files()) > 20, _skill_files()


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_that_dispatches_subagents_is_allowed_the_agent_tool(skill_path: Path):
    text = skill_path.read_text()
    frontmatter, body = _split_frontmatter(text)

    allowed = _allowed_tools(frontmatter)
    if allowed is None:
        return  # no restriction declared: the full tool set, including Agent

    dispatch_hits = [p.pattern for p in DISPATCH_PATTERNS if p.search(body)]
    if not dispatch_hits:
        return

    assert any(t in ("Agent", "Task") for t in allowed), (
        f"{skill_path.relative_to(REPO_ROOT)} instructs subagent dispatch "
        f"(matched {dispatch_hits}) but its allowed-tools omits Agent, so the "
        f"dispatch it mandates is forbidden to it: {allowed}"
    )
