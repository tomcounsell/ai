"""CLAUDE.md rule 8: the suite is at most 15 test files and 5,000 lines, and
never longer than the production code it tests."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRODUCTION = ("core", "harnesses", "bridges", "routines", "tools", "ui", "api", "memory", "scripts")


def lines(files) -> int:
    return sum(len(f.read_text().splitlines()) for f in files)


def test_the_suite_stays_within_its_budget():
    tests = list((ROOT / "tests").rglob("*.py"))
    files = [f for f in tests if f.name.startswith("test_")]
    assert len(files) <= 15, f"{len(files)} test files, budget 15"
    assert lines(tests) <= 5000, f"{lines(tests)} test lines, budget 5000"


def test_the_tests_are_shorter_than_the_code():
    production = [f for d in PRODUCTION for f in (ROOT / d).rglob("*.py")]
    tests = list((ROOT / "tests").rglob("*.py"))
    assert lines(tests) <= lines(production), f"{lines(tests)} test lines, {lines(production)} production"
