"""Tests for the committed interpreter pin guard (issue #2617).

`scripts/check-interpreter-pin.sh` is the single implementation of the
"is this .venv the interpreter the repo pins?" comparison; both
`scripts/pytest-clean.sh` and `.githooks/pre-commit` call it so they fail
loudly on a mismatch instead of misreporting it as a lint or argument error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check-interpreter-pin.sh"


def _checkout(tmp_path, pin: str | None, venv_version: str | None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    if pin is not None:
        (root / ".python-version").write_text(pin)
    if venv_version is not None:
        venv = root / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text(f"home = /opt/python/bin\nversion_info = {venv_version}\n")
    return root


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([str(SCRIPT), str(root)], capture_output=True, text=True)


@pytest.mark.parametrize(
    ("pin", "venv_version"),
    [
        ("3.14\n", "3.14.3"),  # pin MAJOR.MINOR vs venv MAJOR.MINOR.PATCH
        ("3.14.3\n", "3.14"),  # and the reverse granularity
        ("# managed by uv\n3.14\n", "3.14.3"),
        ("cpython@3.14\n", "3.14.3"),
    ],
)
def test_matching_interpreter_is_silent(tmp_path, pin, venv_version):
    result = _run(_checkout(tmp_path, pin, venv_version))
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_mismatch_fails_loudly(tmp_path):
    result = _run(_checkout(tmp_path, "3.14\n", "3.13.2"))
    assert result.returncode == 1
    assert "INTERPRETER MISMATCH" in result.stderr
    assert "3.13" in result.stderr and "3.14" in result.stderr
    assert "uv sync --all-extras" in result.stderr


@pytest.mark.parametrize(
    ("pin", "venv_version"),
    [
        (None, "3.13.2"),  # unpinned checkout: nothing to compare against
        ("3.14\n", None),  # no venv yet: bootstrap will build it on the pin
        ("pypy\n", "3.13.2"),  # unparseable pin is not a mismatch claim
    ],
)
def test_nothing_to_compare_passes_quietly(tmp_path, pin, venv_version):
    result = _run(_checkout(tmp_path, pin, venv_version))
    assert result.returncode == 0, result.stderr


def test_pytest_clean_and_pre_commit_both_call_the_guard():
    """Neither caller may reimplement the comparison (#2617)."""
    for caller in (
        REPO_ROOT / "scripts" / "pytest-clean.sh",
        REPO_ROOT / ".githooks" / "pre-commit",
    ):
        assert "check-interpreter-pin.sh" in caller.read_text(), caller
