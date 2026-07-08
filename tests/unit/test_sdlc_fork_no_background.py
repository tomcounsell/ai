"""Regression guard: every ``context: fork`` skill must keep the no-live-background
-child-at-turn-end invariant that commit ``8542ffb19`` established (issue #1915).

Background: a ``context: fork`` skill is a single, non-resumable subagent turn.
Before ``8542ffb19``, do-build spawned builders with
``run_in_background: [true if Parallel: true]`` and then tried to poll/resume
for up to 15 minutes; do-sdlc omitted the flag entirely (inheriting the Agent
tool's background default). A fork that ends its turn with a live background
child can never be re-entered when that child finishes — the fork reports
"running in the background, I'll continue when it completes" and then never
does, leaving uncommitted/unpushed work (the failure mode behind #1904, #1901,
#1902, #1898). ``8542ffb19`` fixed the two skills that had actually burned a
pipeline (do-build, do-sdlc) by switching their dispatch to
``run_in_background: false``. This file guards that fix mechanically, and
generalizes it to the *entire* ``context: fork`` family so a future regression
in any fork skill — not just the two batch-implicated ones — is caught before
it ships.

This module asserts four distinct invariant classes; each test class below
maps to one:

1. **Discovery coverage** (``TestForkSkillDiscovery``): every skill file whose
   YAML frontmatter declares ``context: fork`` is actually found by the glob
   scan used elsewhere in this file — including do-build's real dispatch file
   (``WORKFLOW.md``), which carries the forbidden-pattern risk even though the
   ``context: fork`` marker itself lives in ``do-build/SKILL.md``. A canary
   assertion (``pthread`` must appear) proves the glob actually ran rather
   than silently matching nothing.

2. **Turn-boundary invariant** (``TestNoUnjoinedBackgroundDispatch``): no
   discovered fork skill file contains an un-joined background dispatch — the
   literal ``run_in_background: true`` (in a dispatch position, not a negated
   prose warning against the pattern) or the old broken template
   ``run_in_background: [true if Parallel: ...]``. This is the core
   regression guard: if this invariant is ever violated again, this test
   fails loudly and names the offending file.

3. **do-build PR guard** (``TestDoBuildPositiveAssertions``): do-build's real
   dispatch file states ``run_in_background: false`` explicitly, and its
   PR-creation step checks for an existing open PR (``gh pr list --head``)
   before calling ``gh pr create`` — a live-ref dedup guard from the sibling
   Defect-3 fix in this same plan (#1915).

4. **do-sdlc foreground flag** (``TestDoSdlcPositiveAssertions``): do-sdlc's
   SKILL.md explicitly forces ``run_in_background: false`` on every stage
   dispatch and documents it as "Hard Rule 6" — the exact prose landed by
   ``8542ffb19``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# The old, broken template pattern that predates 8542ffb19. A fork spawning a
# subagent with this literal string would resolve to background dispatch
# whenever `Parallel: true`, reintroducing the phantom-wait bug.
FORBIDDEN_TEMPLATE_PATTERN = "run_in_background: [true if Parallel:"

# The literal key:value that must never appear in a dispatch position inside
# a context: fork skill.
FORBIDDEN_LITERAL = "run_in_background: true"

# Cues that mark a line as a *negated* or *prose* mention of the forbidden
# literal (e.g. "never `run_in_background: true`") rather than an actual
# dispatch template. Calibrated against do-plan-critique/SKILL.md's real
# line: "**foreground** (never `run_in_background: true` — a background
# re-dispatch re-introduces exactly the fire-and-forget assumption ...)".
NEGATION_CUES = (
    "never",
    "n't",
    "not ",
    "must not",
    "forbidden",
    "do not",
)


# ---------------------------------------------------------------------------
# Discovery: find every context: fork skill file
# ---------------------------------------------------------------------------


def _frontmatter(text: str) -> str:
    """Return the YAML frontmatter block (between the leading '---' markers)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i])
    return ""


def discover_fork_skill_files() -> list[Path]:
    """Glob every SKILL.md under skills-global/ and skills/, keep the ones
    whose frontmatter declares ``context: fork``, and always include
    do-build's real dispatch file (its own SKILL.md carries the ``context:
    fork`` marker, but the dispatch logic — and thus the regression risk —
    lives in WORKFLOW.md).
    """
    candidates: list[Path] = []
    for pattern_root in (
        REPO_ROOT / ".claude" / "skills-global",
        REPO_ROOT / ".claude" / "skills",
    ):
        if not pattern_root.is_dir():
            continue
        candidates.extend(pattern_root.glob("**/SKILL.md"))

    fork_skills: list[Path] = []
    for path in sorted(candidates):
        text = path.read_text(encoding="utf-8")
        fm = _frontmatter(text)
        if re.search(r"^context:\s*fork\s*$", fm, re.MULTILINE):
            fork_skills.append(path)

    # do-build's actual dispatch logic lives in WORKFLOW.md, not SKILL.md,
    # even though the context: fork marker is carried on SKILL.md.
    workflow_md = REPO_ROOT / ".claude" / "skills-global" / "do-build" / "WORKFLOW.md"
    fork_skills.append(workflow_md)

    return fork_skills


FORK_SKILL_FILES = discover_fork_skill_files()


class TestForkSkillDiscovery:
    """Guards discovery coverage: the glob must actually find fork skills."""

    def test_discovery_found_at_least_one_skill(self):
        assert FORK_SKILL_FILES, (
            "discover_fork_skill_files() found zero files — the glob patterns "
            "or frontmatter regex are broken; this test would silently pass "
            "on everything else."
        )

    def test_discovery_canary_pthread(self):
        """pthread must be discovered — proves the glob+frontmatter scan works
        end to end, not just against a hardcoded list."""
        names = {p.parent.name for p in FORK_SKILL_FILES}
        assert "pthread" in names, (
            f"Expected 'pthread' among discovered context: fork skills, got: {sorted(names)}. "
            "If pthread's frontmatter changed (e.g. context: fork removed), "
            "investigate before assuming this test is stale."
        )

    def test_discovery_includes_expected_skills(self):
        """Canary set of skills that must always be covered by discovery
        (per plan docs/plans/sdlc-fork-sync-workers-worktree-isolation.md,
        Resolved Question 3)."""
        names = {p.parent.name for p in FORK_SKILL_FILES}
        expected = {"do-build", "do-sdlc", "pthread", "do-pr-review", "do-plan-critique"}
        missing = expected - names
        assert not missing, (
            f"Expected context: fork skills missing from discovery: {sorted(missing)}. "
            f"Discovered: {sorted(names)}"
        )

    def test_discovery_includes_do_build_workflow_dispatch_file(self):
        assert any(p.name == "WORKFLOW.md" for p in FORK_SKILL_FILES), (
            "do-build/WORKFLOW.md (the real dispatch file) must always be "
            "included in the scanned set, even though context: fork lives in "
            "do-build/SKILL.md's frontmatter."
        )


# ---------------------------------------------------------------------------
# Turn-boundary invariant: no un-joined background dispatch
# ---------------------------------------------------------------------------


def _is_negated_prose(line: str) -> bool:
    lowered = line.lower()
    return any(cue in lowered for cue in NEGATION_CUES)


def find_forbidden_literal_lines(text: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every line containing the forbidden
    `run_in_background: true` literal that is NOT a negated/prose mention."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FORBIDDEN_LITERAL in line and not _is_negated_prose(line):
            hits.append((lineno, line))
    return hits


def find_forbidden_template_lines(text: str) -> list[tuple[int, str]]:
    """Return (line_number, line_text) for every line containing the old
    broken `run_in_background: [true if Parallel: ...]` template."""
    hits: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FORBIDDEN_TEMPLATE_PATTERN in line:
            hits.append((lineno, line))
    return hits


@pytest.mark.parametrize(
    "skill_path", FORK_SKILL_FILES, ids=[str(p.relative_to(REPO_ROOT)) for p in FORK_SKILL_FILES]
)
class TestNoUnjoinedBackgroundDispatch:
    def test_file_exists_and_nonempty(self, skill_path: Path):
        assert skill_path.exists(), (
            f"{skill_path}: discovered context: fork skill file is missing on disk. "
            "A fork skill file must exist and be readable — this is a hard failure, "
            "not a skip."
        )
        assert skill_path.stat().st_size > 0, (
            f"{skill_path}: discovered context: fork skill file is empty (0 bytes). "
            "An empty skill file cannot carry the required turn-boundary invariant "
            "prose — this is a hard failure, not a skip."
        )

    def test_no_forbidden_background_literal(self, skill_path: Path):
        text = skill_path.read_text(encoding="utf-8")
        hits = find_forbidden_literal_lines(text)
        assert not hits, (
            f"{skill_path}: found forbidden pattern {FORBIDDEN_LITERAL!r} in a "
            "dispatch position (not excluded as negated/prose) at line(s): "
            + ", ".join(f"{n}: {line.strip()!r}" for n, line in hits)
            + ". A context: fork skill must never dispatch a subagent with "
            "run_in_background: true — the fork cannot be resumed when a "
            "background child finishes (issue #1915)."
        )

    def test_no_forbidden_background_template(self, skill_path: Path):
        text = skill_path.read_text(encoding="utf-8")
        hits = find_forbidden_template_lines(text)
        assert not hits, (
            f"{skill_path}: found forbidden pattern {FORBIDDEN_TEMPLATE_PATTERN!r} "
            "(the pre-8542ffb19 broken template) at line(s): "
            + ", ".join(f"{n}: {line.strip()!r}" for n, line in hits)
            + ". This template resolves to background dispatch whenever "
            "Parallel: true, reintroducing the phantom-wait bug (issue #1915)."
        )


# ---------------------------------------------------------------------------
# Positive assertions: do-build's PR dedup guard + foreground dispatch
# ---------------------------------------------------------------------------


DO_BUILD_WORKFLOW = REPO_ROOT / ".claude" / "skills-global" / "do-build" / "WORKFLOW.md"
DO_BUILD_PR_AND_CLEANUP = REPO_ROOT / ".claude" / "skills-global" / "do-build" / "PR_AND_CLEANUP.md"


class TestDoBuildPositiveAssertions:
    def test_workflow_declares_foreground_dispatch(self):
        text = DO_BUILD_WORKFLOW.read_text(encoding="utf-8")
        assert "run_in_background: false" in text, (
            f"{DO_BUILD_WORKFLOW}: expected literal 'run_in_background: false' "
            "(the 8542ffb19 fix for do-build's builder dispatch) not found. "
            "This is the Defect-1 fix this test locks in — a revert here "
            "reintroduces the phantom-wait bug (issue #1915)."
        )

    def test_pr_guard_checks_existing_pr_before_create(self):
        """gh pr list --head must appear BEFORE gh pr create (live-ref dedup
        guard, Defect 3) — line-order check, not just substring presence."""
        text = DO_BUILD_PR_AND_CLEANUP.read_text(encoding="utf-8")
        list_idx = text.find("gh pr list --head")
        create_idx = text.find("gh pr create")
        assert list_idx != -1, (
            f"{DO_BUILD_PR_AND_CLEANUP}: expected 'gh pr list --head' "
            "(the live-ref PR dedup guard) not found."
        )
        assert create_idx != -1, f"{DO_BUILD_PR_AND_CLEANUP}: expected 'gh pr create' not found."
        assert list_idx < create_idx, (
            f"{DO_BUILD_PR_AND_CLEANUP}: 'gh pr list --head' (index {list_idx}) must "
            f"appear BEFORE 'gh pr create' (index {create_idx}) — the dedup guard must "
            "run before PR creation, or it cannot prevent a duplicate PR (issue #1915)."
        )


# ---------------------------------------------------------------------------
# Positive assertions: do-sdlc's explicit foreground flag + Hard Rule 6
# ---------------------------------------------------------------------------


DO_SDLC_SKILL = REPO_ROOT / ".claude" / "skills-global" / "do-sdlc" / "SKILL.md"


class TestDoSdlcPositiveAssertions:
    def test_declares_foreground_dispatch(self):
        text = DO_SDLC_SKILL.read_text(encoding="utf-8")
        assert "run_in_background: false" in text, (
            f"{DO_SDLC_SKILL}: expected literal 'run_in_background: false' "
            "(the 8542ffb19 fix for do-sdlc's stage dispatch) not found. "
            "This is the Defect-1 fix this test locks in — a revert here "
            "reintroduces the phantom-wait bug (issue #1915)."
        )

    def test_documents_hard_rule_6(self):
        text = DO_SDLC_SKILL.read_text(encoding="utf-8")
        assert "Hard Rule 6" in text, (
            f"{DO_SDLC_SKILL}: expected 'Hard Rule 6' text (documenting the "
            "run_in_background: false invariant, landed by 8542ffb19) not found."
        )
