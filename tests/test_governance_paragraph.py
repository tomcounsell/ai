"""The governance paragraph is the same, byte for byte, in every tracked
file that holds it: `CLAUDE.md`'s paragraph is the one the persona renders
into every turn, and each copy elsewhere must read the same words.

No model call. Live spend: none.
"""

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


def test_every_tracked_copy_of_the_governance_paragraph_is_byte_identical():
    held = paragraph()
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
    ).stdout.split(b"\0")
    holders = []
    for name in filter(None, tracked):
        path = ROOT / name.decode()
        try:
            text = path.read_text()
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        if "**" + OPENING in text:
            holders.append(name.decode())
            assert held in text, f"{name.decode()} holds the governance paragraph in other words"
    assert "CLAUDE.md" in holders and len(holders) > 1
