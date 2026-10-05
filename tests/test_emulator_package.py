"""The emulator is a package under `tests/`, run as modules: every module
imports as `tests.emulator.*`, pytest collects none of them, and nothing of
it is left in `scripts/`.

Live spend: none.
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.spend(usd=0)

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("common", "workspace", "stand_in", "judge", "replay")


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports_from_the_package(name):
    importlib.import_module(f"tests.emulator.{name}")


@pytest.mark.parametrize("name", ["replay", "judge", "stand_in", "workspace"])
def test_each_entry_point_runs_as_a_module(name):
    out = subprocess.run(
        [sys.executable, "-m", f"tests.emulator.{name}", "--help"], cwd=ROOT, capture_output=True, text=True,
        check=False,
    )  # fmt: skip
    assert out.returncode == 0, out.stderr


def test_pytest_collects_none_of_it_and_scripts_holds_none_of_it():
    assert not list((ROOT / "tests" / "emulator").glob("test_*.py"))
    gone = ("replay.py", "replay_common.py", "replay_workspace.py", "role_play_tom.py", "judge_replay.py",
            "demo_workspace.sh")  # fmt: skip
    assert not [p for p in (ROOT / "scripts").rglob("*") if p.name in gone]
