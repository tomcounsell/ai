"""A skill that dispatches subagents must be allowed to (#2649).

`allowed-tools` in a SKILL.md frontmatter is a restriction: per
`.claude/skills-global/new-skill/SKILL.md`, "Restricts which tools the skill
can use. Omit to allow all." So a skill whose body instructs the driver to
dispatch subagents, while its own `allowed-tools` omits `Agent`, asks for
something it has forbidden itself.

`do-pr-review` was in exactly that state: `context: fork`, an `allowed-tools`
list without `Agent`, and a body mandating judge subagents — with this repo
running multi-judge consensus by default with the `code-quality` and `risk`
judges declared in `docs/sdlc/do-pr-review.md`. The
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

# Body phrasings that mean "spawn a subagent". A skill merely mentioning the
# word "agent" is not dispatching one, so a dispatch VERB is always required.
#
# The first version of this set was derived from `do-pr-review`'s own wording
# and therefore found `do-pr-review` and nothing else, while two skills sat in
# the identical broken state and passed green. Three near-misses caused it: the
# literal token "subagent" (so "Spawn an Explore agent" slipped past), no
# hyphenated "sub-agents", and three hardcoded nouns (so "Dispatch spikes …
# parallel Agent sub-agents" slipped past). Hence verb + agent-noun proximity
# rather than an enumeration of phrasings.
_AGENT_NOUN = r"(?:sub-?agents?|agents?|critics|judges)"
# "launch" is deliberately absent. This repo also uses "agent" for a Claude
# Managed Agent -- a hosted product you launch, not a subagent you spawn -- and
# `build-agent` ("launch the agent -> run a graded outcome -> schedule it on
# cron") is a false positive under it. No skill in either root writes
# "launch ... subagent", so excluding the verb costs no coverage.
_DISPATCH_VERB = r"(?:spawn|dispatch|fan[-\s]?out)"

DISPATCH_PATTERNS = (
    re.compile(r"\bAgent tool\b"),
    re.compile(r"\b(?:Agent|Task)\(\{"),
    re.compile(r"\bExplore agents?\b", re.I),
    re.compile(r"\bAgent sub-?agents?\b", re.I),
    # A dispatch verb and an agent noun in the same clause. Bounded to 60
    # characters and stopped at a sentence end so the two words have to be
    # talking about each other.
    re.compile(rf"\b{_DISPATCH_VERB}\w*\b[^.\n]{{0,60}}?\b{_AGENT_NOUN}\b", re.I),
)

# A dispatch phrase inside a prohibition is the opposite of an instruction to
# dispatch. `do-design-system` says "do NOT delegate to a subagent" and
# `authenticity-pass` returns a draft *to* a drafter subagent rather than
# spawning one; both must stay green while the patterns widen.
_NEGATION = re.compile(r"(?:do\s+not|don't|never|avoid|without|rather than|instead of)\s*$", re.I)


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
    """The declared tool list, or None when the key is absent (= allow all).

    Both YAML forms count. An inline `allowed-tools: A, B` and a block
    sequence (`allowed-tools:` followed by `  - A`) mean the same thing, but a
    regex reading only the inline form parses the block form as `[]` — which is
    indistinguishable from "declared, restricted to nothing" and is *not* the
    `None` that means unrestricted. `calendar-sync` uses the block form today,
    so the next list-form skill to legitimately declare `Agent` would get a
    failure whose message contradicts the file it describes.
    """
    try:
        import yaml

        data = yaml.safe_load(frontmatter)
    except Exception:
        data = None

    if isinstance(data, dict) and "allowed-tools" in data:
        value = data["allowed-tools"]
        if value is None:
            return []
        if isinstance(value, str):
            return [t.strip() for t in value.split(",") if t.strip()]
        if isinstance(value, list):
            return [str(t).strip() for t in value if str(t).strip()]
        return []

    if isinstance(data, dict):
        return None  # parsed cleanly, key genuinely absent

    # Frontmatter that YAML cannot parse: fall back to the inline form rather
    # than silently reporting "unrestricted".
    match = re.search(r"^allowed-tools:(.*)$", frontmatter, re.MULTILINE)
    if not match:
        return None
    return [t.strip() for t in match.group(1).split(",") if t.strip()]


def test_skill_inventory_is_non_empty():
    """Guard the guard: a glob that stops finding skills must fail loudly
    rather than silently vacuously pass."""
    assert len(_skill_files()) > 20, _skill_files()


def _dispatch_hits(body: str) -> list[str]:
    """Patterns whose match is a genuine instruction to dispatch.

    A match sitting inside a prohibition is skipped: a skill saying "do NOT
    delegate to a subagent" is asserting the opposite of a dispatch.
    """
    hits = []
    for pattern in DISPATCH_PATTERNS:
        for match in pattern.finditer(body):
            line_start = body.rfind("\n", 0, match.start()) + 1
            if _NEGATION.search(body[line_start : match.start()]):
                continue
            hits.append(pattern.pattern)
            break
    return hits


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_that_dispatches_subagents_is_allowed_the_agent_tool(skill_path: Path):
    text = skill_path.read_text()
    frontmatter, body = _split_frontmatter(text)

    allowed = _allowed_tools(frontmatter)
    if allowed is None:
        return  # no restriction declared: the full tool set, including Agent

    dispatch_hits = _dispatch_hits(body)
    if not dispatch_hits:
        return

    assert any(t in ("Agent", "Task") for t in allowed), (
        f"{skill_path.relative_to(REPO_ROOT)} instructs subagent dispatch "
        f"(matched {dispatch_hits}) but its allowed-tools omits Agent, so the "
        f"dispatch it mandates is forbidden to it: {allowed}"
    )
