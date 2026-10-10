"""The governance paragraph is the same, byte for byte, in every tracked
file that holds it: `CLAUDE.md`'s paragraph and the persona's copy in
`persona/governance.md`, which renders into every turn, and each copy
elsewhere must read the same words. The "Tests are not governance."
paragraph is the same in `CLAUDE.md` and the persona.

No model call. Live spend: none.
"""

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
OPENING = "Governance is restrained by structure, not sentiment."


def paragraph() -> str:
    text = (ROOT / "CLAUDE.md").read_text()
    start = text.index("**" + OPENING)
    return text[start : text.index("\n", start)]


SKIPPED_DIRS = {".git", ".venv", "node_modules", "__pycache__"}


def tracked_files() -> list[str]:
    """The tracked files by path from the root: `git ls-files`, or, where git
    cannot list them (a copy of the tree with no repository), every file
    under the root outside the directories no tree tracks."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    except subprocess.CalledProcessError, FileNotFoundError:
        found = []
        for here, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS]
            found += [str((Path(here) / f).relative_to(ROOT)) for f in files]
        return sorted(found)
    return [n.decode() for n in listed.stdout.split(b"\0") if n]


def test_the_tree_is_listed_without_git(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    names = tracked_files()
    assert "CLAUDE.md" in names and not any(n.startswith(".git/") for n in names)


def test_every_tracked_copy_of_the_governance_paragraph_is_byte_identical():
    held = paragraph()
    holders = []
    for name in tracked_files():
        path = ROOT / name
        try:
            text = path.read_text()
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        if "**" + OPENING in text:
            holders.append(name)
            assert held in text, f"{name} holds the governance paragraph in other words"
    assert "CLAUDE.md" in holders and "persona/governance.md" in holders


def test_the_persona_carries_the_tests_paragraph_in_claude_mds_words():
    tests = next(
        line
        for line in (ROOT / "CLAUDE.md").read_text().splitlines()
        if line.startswith("**Tests are not governance.")
    )
    assert tests in (ROOT / "persona" / "governance.md").read_text().splitlines()
