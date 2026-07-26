"""Regression-guard enforcement test for SDLC fork skills (issue #1915).

A `context: fork` skill runs in a forked Claude Code context that gets exactly
ONE turn. The Agent/Task tool defaults to BACKGROUND execution: it returns
immediately and notifies later. A fork has no "later turn" to receive that
notification, so any background dispatch is unrecoverable -- the fork reports
"running in the background, I'll continue when it completes" and then never
does, leaving unpushed branches and no PR (issue #1915). Commit 8542ffb19
fixed every dispatching fork skill to spawn with `run_in_background: false`.

This test locks in that fix so a future edit cannot silently regress it. Each
assertion guards a specific invariant:

1. Discovery (`test_all_fork_skills_discovered`): every `context: fork` SKILL.md
   under `.claude/skills-global/` and `.claude/skills/`, plus do-build's
   multi-file dispatch/PR sub-files (WORKFLOW.md, PR_AND_CLEANUP.md), is found
   dynamically. A refactor that stops discovering the anchor skills fails loudly.

2. Exists & non-empty (`test_every_fork_skill_exists_and_nonempty`): a missing
   or empty fork skill file is a TEST FAILURE, never a silent skip.

3. No un-joined background dispatch (`test_no_background_dispatch`): no fork skill
   file contains a real `run_in_background: true` dispatch (or the old buggy
   `run_in_background: [true if Parallel` template). Negated/backtick prose
   mentions (e.g. do-plan-critique's "never `run_in_background: true`") are
   correctly excluded -- the matcher strips inline-code spans and skips negated
   lines. `test_matcher_excludes_negated_prose` and
   `test_matcher_catches_real_violation` prove the matcher has teeth.

4. Presence of explicit `false` (`test_named_skills_have_explicit_false`): the
   named dispatching skills (do-build/WORKFLOW.md, do-sdlc/SKILL.md) MUST carry
   the literal `run_in_background: false`. Omitting the flag and relying on the
   tool default is a FAILURE -- this is the core regression guard for 8542ffb19.

5. Other positive anchors:
   - do-build/WORKFLOW.md no longer carries the old `true if Parallel: true`
     template (`test_workflow_no_old_parallel_template`, Defect-1 guard).
   - do-build/PR_AND_CLEANUP.md's reuse guard (`gh pr list --head`) precedes PR
     creation (`gh pr create`) (`test_pr_guard_precedes_create`).
   - do-sdlc/SKILL.md carries Hard Rule 6 phrasing
     (`test_do_sdlc_has_hard_rule_6`).
   - sdlc/SKILL.md carries the router live-ref cross-check
     (`test_router_has_live_ref_crosscheck`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


# --------------------------------------------------------------------------- #
# Repo-root resolution (robust in worktree and after merge to main)
# --------------------------------------------------------------------------- #
def _find_repo_root() -> Path:
    """Walk up from this test file until we find the dir containing `.claude/`."""
    here = Path(__file__).resolve()
    for candidate in (here, *here.parents):
        if (candidate / ".claude").is_dir():
            return candidate
    raise AssertionError(f"Could not locate repo root (dir containing .claude/) from {here}")


REPO_ROOT = _find_repo_root()

# do-build is a multi-file skill: its dispatch logic lives in WORKFLOW.md and
# its PR logic in PR_AND_CLEANUP.md -- both must be scanned alongside SKILL.md.
DO_BUILD_EXTRA_FILES = (
    ".claude/skills-global/do-build/WORKFLOW.md",
    ".claude/skills-global/do-build/PR_AND_CLEANUP.md",
)

# Anchors that MUST be discovered. A refactor that stops discovering these
# should fail loudly rather than silently shrink the guarded set.
REQUIRED_ANCHORS = (
    ".claude/skills-global/do-build/SKILL.md",
    ".claude/skills-global/do-sdlc/SKILL.md",
    ".claude/skills/sdlc/SKILL.md",
    ".claude/skills-global/do-plan-critique/SKILL.md",
    ".claude/skills-global/do-pr-review/SKILL.md",
    ".claude/skills-global/do-build/WORKFLOW.md",
)


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def _has_fork_frontmatter(path: Path) -> bool:
    """True iff the YAML frontmatter block contains a `context: fork` line.

    The frontmatter is the block between the first two `---` fences. We only
    inspect that block so a stray `context: fork` in prose does not count, and
    a doc/reference file without frontmatter is ignored.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "---":
            break  # end of frontmatter
        if stripped == "context: fork":
            return True
    return False


def discover_fork_skill_files() -> list[Path]:
    """Discover every fork skill file to scan.

    Globs SKILL.md under both skill roots, keeps only `context: fork` ones,
    then adds do-build's dispatch + PR sub-files.
    """
    found: set[Path] = set()
    for root in (".claude/skills-global", ".claude/skills"):
        for skill_md in (REPO_ROOT / root).glob("**/SKILL.md"):
            if _has_fork_frontmatter(skill_md):
                found.add(skill_md.resolve())
    for extra in DO_BUILD_EXTRA_FILES:
        found.add((REPO_ROOT / extra).resolve())
    return sorted(found)


FORK_SKILL_FILES = discover_fork_skill_files()


def _rel(path: Path) -> str:
    """Repo-relative path string for readable failure messages."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# Background-dispatch matcher (with backtick + negation exclusion)
# --------------------------------------------------------------------------- #
_FORBIDDEN_TOKENS = (
    "run_in_background: true",
    "run_in_background: [true if Parallel",
)
_NEGATION_KEYWORDS = (
    "never",
    "not",
    "don't",
    "no ",
    "avoid",
    "forbidden",
    "must not",
    "instead of",
)


def _strip_inline_code(line: str) -> str:
    """Remove text between backticks (inline-code spans) from a line.

    A negated prose mention that wraps the token in backticks (e.g. the line
    'never `run_in_background: true`') loses the bare token after stripping the
    span, so the line cannot register as a violation.
    """
    out: list[str] = []
    in_code = False
    for ch in line:
        if ch == "`":
            in_code = not in_code
            continue
        if not in_code:
            out.append(ch)
    return "".join(out)


def line_is_violation(line: str) -> bool:
    """True iff `line` is a real un-joined background dispatch.

    A line VIOLATES only if, after stripping backtick spans AND excluding
    negated prose, it still contains a bare forbidden token.
    """
    lowered = line.lower()
    if any(kw in lowered for kw in _NEGATION_KEYWORDS):
        return False
    stripped = _strip_inline_code(line)
    return any(tok in stripped for tok in _FORBIDDEN_TOKENS)


def find_violations(text: str) -> list[str]:
    """Return offending line contents (stripped) from a file's text."""
    return [line.strip() for line in text.splitlines() if line_is_violation(line)]


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_all_fork_skills_discovered():
    """Discovery is non-empty and contains every required anchor."""
    assert FORK_SKILL_FILES, "No fork skill files discovered -- discovery is broken."
    discovered_rel = {_rel(p) for p in FORK_SKILL_FILES}
    for anchor in REQUIRED_ANCHORS:
        assert anchor in discovered_rel, (
            f"Required fork-skill anchor not discovered: {anchor}. "
            f"Discovered set: {sorted(discovered_rel)}"
        )


def test_every_fork_skill_exists_and_nonempty():
    """Every discovered fork skill file exists and has non-empty content."""
    for path in FORK_SKILL_FILES:
        assert path.exists(), f"Fork skill file missing: {_rel(path)}"
        content = path.read_text(encoding="utf-8").strip()
        assert content, f"Fork skill file is empty: {_rel(path)}"


def test_no_background_dispatch():
    """No fork skill file contains a real background-dispatch pattern."""
    offenders: list[str] = []
    for path in FORK_SKILL_FILES:
        text = path.read_text(encoding="utf-8")
        for bad_line in find_violations(text):
            offenders.append(f"{_rel(path)}: {bad_line}")
    assert not offenders, (
        "Found forbidden background-dispatch pattern(s) in fork skill(s):\n" + "\n".join(offenders)
    )


def test_matcher_excludes_negated_prose():
    """do-plan-critique's backtick-wrapped negated mention must NOT flag.

    Guards the matcher against regressing into a false-positive. The file must
    be in the scanned set AND must register zero violations.
    """
    target = (REPO_ROOT / ".claude/skills-global/do-plan-critique/SKILL.md").resolve()
    assert target in FORK_SKILL_FILES, (
        "do-plan-critique/SKILL.md is not in the scanned fork-skill set; "
        "the false-positive exclusion check cannot run."
    )
    text = target.read_text(encoding="utf-8")
    assert "run_in_background: true" in text, (
        "Expected do-plan-critique to contain a negated backtick mention of "
        "the token (test premise); file content changed."
    )
    violations = find_violations(text)
    assert not violations, (
        f"Matcher false-positived on negated prose in do-plan-critique: {violations}"
    )


def test_matcher_catches_real_violation():
    """The matcher must flag a genuine dispatch line (has teeth)."""
    assert line_is_violation("  run_in_background: true")
    assert line_is_violation("  run_in_background: [true if Parallel: true]")
    # Sanity: a foreground line and a negated prose line do NOT flag.
    assert not line_is_violation("  run_in_background: false")
    assert not line_is_violation("Always foreground -- never `run_in_background: true` in a fork.")


def test_named_skills_have_explicit_false():
    """Named dispatching skills carry the literal `run_in_background: false`.

    Locks in commit 8542ffb19. Omitting the flag (relying on tool default) is
    a FAILURE -- we assert PRESENCE of the explicit `false` token.
    """
    named = (
        ".claude/skills-global/do-build/WORKFLOW.md",
        ".claude/skills-global/do-sdlc/SKILL.md",
    )
    for rel in named:
        path = REPO_ROOT / rel
        text = path.read_text(encoding="utf-8")
        assert "run_in_background: false" in text, (
            f"{rel} is missing the explicit foreground flag "
            "`run_in_background: false`. A dispatching fork skill must set the "
            "flag explicitly, not rely on the tool default (issue #1915, "
            "commit 8542ffb19)."
        )


def test_workflow_no_old_parallel_template():
    """do-build/WORKFLOW.md no longer carries the old buggy template (Defect-1)."""
    text = (REPO_ROOT / ".claude/skills-global/do-build/WORKFLOW.md").read_text(encoding="utf-8")
    assert "true if Parallel: true" not in text, (
        "do-build/WORKFLOW.md still contains the old buggy "
        "`true if Parallel: true` background template (Defect-1 regression)."
    )


def test_pr_guard_precedes_create():
    """The reuse guard (`gh pr list --head`) precedes PR creation.

    In PR_AND_CLEANUP.md the first `gh pr list --head` must appear before the
    first `gh pr create`, so an existing PR is reused before a new one is made.
    """
    text = (REPO_ROOT / ".claude/skills-global/do-build/PR_AND_CLEANUP.md").read_text(
        encoding="utf-8"
    )
    list_idx = text.find("gh pr list --head")
    create_idx = text.find("gh pr create")
    assert list_idx != -1, "PR_AND_CLEANUP.md missing `gh pr list --head` reuse guard."
    assert create_idx != -1, "PR_AND_CLEANUP.md missing `gh pr create`."
    assert list_idx < create_idx, (
        "In PR_AND_CLEANUP.md the `gh pr list --head` reuse guard "
        f"(index {list_idx}) must precede `gh pr create` (index {create_idx})."
    )


def test_do_sdlc_has_hard_rule_6():
    """do-sdlc/SKILL.md carries Hard Rule 6 anchor phrasing."""
    text = (REPO_ROOT / ".claude/skills-global/do-sdlc/SKILL.md").read_text(encoding="utf-8")
    assert "never end the turn waiting on a background child" in text, (
        "do-sdlc/SKILL.md is missing Hard Rule 6 phrasing "
        "('never end the turn waiting on a background child')."
    )


def test_router_has_live_ref_crosscheck():
    """sdlc/SKILL.md router carries the `gh pr list --head` live-ref cross-check."""
    text = (REPO_ROOT / ".claude/skills/sdlc/SKILL.md").read_text(encoding="utf-8")
    assert "gh pr list --head" in text, (
        "sdlc/SKILL.md (router) is missing the `gh pr list --head` live-ref cross-check note."
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
