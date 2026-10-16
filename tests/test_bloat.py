"""CLAUDE.md rule 8: no production code that nothing in production uses.
Code only the tests reach is dead too, so vulture reads production alone."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_nothing_in_production_is_unused():
    found = subprocess.run(
        [sys.executable, "-m", "vulture"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    assert found.returncode == 0, found.stdout + found.stderr
