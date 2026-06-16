"""Unit tests for tools/critique_resume.py — the critique-resume-probe CLI.

Coverage matrix
---------------
- Match case: newest dir with matching plan_hash + incomplete gate → exit 0, stdout = dir
- Mismatch case: plan_hash doesn't match → exit 1, empty stdout, stale dir on stderr
- Missing case: no .critique-runs dir → exit 1, empty stdout
- Complete gate case: matching plan_hash but gate is complete → exit 1 (don't resume)
- Garbage .plan_hash file → skip candidate, exit 1
- compute_plan_hash returns None (unreadable plan) → exit 1, no crash
- Multiple dirs: picks newest matching non-complete one
- CLI subprocess test: critique-resume-probe --help exits 0
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.critique_resume import find_reusable_run, main

# ---------------------------------------------------------------------------
# Fence constants (mirror critique_roster_check)
# ---------------------------------------------------------------------------
FENCE_DELIMITER = "<<<CRITIQUE-RESULT-COMPLETE>>>"
FENCE_STATUS = "STATUS: COMPLETED"
COMPLETE_SUFFIX = f"\n{FENCE_DELIMITER}\n{FENCE_STATUS}\n"


# ---------------------------------------------------------------------------
# Helpers for building synthetic run directories
# ---------------------------------------------------------------------------


def _make_roster(run_dir: Path, names: list[str]) -> None:
    """Write a valid _roster.json to run_dir."""
    (run_dir / "_roster.json").write_text(
        json.dumps({"roster": names, "count": len(names)}), encoding="utf-8"
    )


def _write_result(run_dir: Path, name: str, *, complete: bool = False) -> None:
    """Write a {name}.result.md file, optionally with the completion fence."""
    body = f"# {name} findings\n\nSome critique text here.\n"
    if complete:
        body += COMPLETE_SUFFIX
    (run_dir / f"{name}.result.md").write_text(body, encoding="utf-8")


def _make_run_dir(
    base: Path,
    prefix: str,
    timestamp: str,
    *,
    plan_hash: str | None,
    roster: list[str] | None = None,
    completed_members: list[str] | None = None,
) -> Path:
    """Create a synthetic run directory.

    Args:
        base: The base .critique-runs directory.
        prefix: issue number or slug string.
        timestamp: Timestamp suffix (e.g. "20260101T120000").
        plan_hash: Content for .plan_hash (None → don't write the file).
        roster: Roster member names (None → don't write _roster.json).
        completed_members: Subset of roster that get complete result files.
    """
    run_dir = base / f"{prefix}-{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    if plan_hash is not None:
        (run_dir / ".plan_hash").write_text(plan_hash, encoding="utf-8")

    if roster is not None:
        _make_roster(run_dir, roster)
        completed_members = completed_members or []
        for name in roster:
            _write_result(run_dir, name, complete=(name in completed_members))

    return run_dir


# ---------------------------------------------------------------------------
# Fixture: a real plan file with a known hash
# ---------------------------------------------------------------------------


@pytest.fixture()
def plan_file(tmp_path: Path) -> Path:
    """A minimal plan file whose hash we can compute."""
    p = tmp_path / "my-plan.md"
    p.write_text("# My Plan\n\nThis is the plan content.\n", encoding="utf-8")
    return p


@pytest.fixture()
def plan_hash(plan_file: Path) -> str:
    """The sha256 hash of plan_file as returned by compute_plan_hash."""
    from tools.sdlc_verdict import compute_plan_hash

    h = compute_plan_hash(str(plan_file))
    assert h is not None, "compute_plan_hash must return a value for a real file"
    return h


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMatchCase:
    """Newest dir with matching plan_hash + incomplete gate → exit 0, stdout = dir."""

    def test_returns_dir_path(self, tmp_path: Path, plan_file: Path, plan_hash: str) -> None:
        base = tmp_path / ".critique-runs"
        run_dir = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic", "operator"],
            completed_members=[],  # none complete → gate is incomplete
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result == str(run_dir)

    def test_main_exits_0_and_prints_path(
        self, tmp_path: Path, plan_file: Path, plan_hash: str, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        run_dir = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )

        exit_code = main(["--plan", str(plan_file), "--issue", "42", "--base-dir", str(base)])
        captured = capsys.readouterr()
        assert exit_code == 0
        assert captured.out.strip() == str(run_dir)


class TestMismatchCase:
    """plan_hash doesn't match → exit 1, empty stdout, stale dir printed to stderr."""

    def test_exit_1_on_hash_mismatch(
        self, tmp_path: Path, plan_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        stale_dir = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash="sha256:deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
            roster=["skeptic"],
            completed_members=[],
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        captured = capsys.readouterr()

        assert result is None
        # Stale dir must appear on stderr
        assert str(stale_dir) in captured.err

    def test_main_exits_1_empty_stdout(
        self, tmp_path: Path, plan_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        _make_run_dir(
            base,
            prefix="slug-foo",
            timestamp="20260101T120000",
            plan_hash="sha256:0000000000000000000000000000000000000000000000000000000000000000",
            roster=["skeptic"],
        )

        exit_code = main(["--plan", str(plan_file), "--slug", "slug-foo", "--base-dir", str(base)])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert captured.out.strip() == ""


class TestMissingBaseDir:
    """No .critique-runs dir → exit 1, empty stdout."""

    def test_nonexistent_base_dir(
        self, tmp_path: Path, plan_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        nonexistent = tmp_path / "no-such-dir"

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(nonexistent))
        assert result is None

    def test_main_exits_1(
        self, tmp_path: Path, plan_file: Path, capsys: pytest.CaptureFixture
    ) -> None:
        nonexistent = str(tmp_path / "no-such-dir")
        exit_code = main(["--plan", str(plan_file), "--issue", "99", "--base-dir", nonexistent])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert captured.out.strip() == ""


class TestCompleteGate:
    """Matching plan_hash but gate is complete → exit 1 (don't resume finished runs)."""

    def test_complete_run_not_returned(
        self, tmp_path: Path, plan_file: Path, plan_hash: str
    ) -> None:
        base = tmp_path / ".critique-runs"
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic", "operator"],
            completed_members=["skeptic", "operator"],  # both complete → gate complete
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result is None

    def test_main_exits_1_for_complete_run(
        self, tmp_path: Path, plan_file: Path, plan_hash: str, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=["skeptic"],
        )

        exit_code = main(["--plan", str(plan_file), "--issue", "42", "--base-dir", str(base)])
        captured = capsys.readouterr()
        assert exit_code == 1
        assert captured.out.strip() == ""


class TestGarbagePlanHash:
    """Garbage .plan_hash file → skip candidate, exit 1."""

    def test_skips_dir_with_garbage_hash(self, tmp_path: Path, plan_file: Path) -> None:
        base = tmp_path / ".critique-runs"
        # Write a dir with a non-matching (garbage) hash
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash="not-a-real-hash",
            roster=["skeptic"],
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result is None

    def test_missing_plan_hash_file_skipped(self, tmp_path: Path, plan_file: Path) -> None:
        base = tmp_path / ".critique-runs"
        # Create dir without .plan_hash at all
        run_dir = base / "42-20260101T120000"
        run_dir.mkdir(parents=True)
        _make_roster(run_dir, ["skeptic"])

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result is None


class TestNoneHash:
    """compute_plan_hash returns None → exit 1, no crash."""

    def test_none_hash_returns_none(self, tmp_path: Path, plan_hash: str) -> None:
        base = tmp_path / ".critique-runs"
        # The run dir has a valid hash, but the plan file is unreadable
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
        )

        # Patch compute_plan_hash to return None (plan file unreadable)
        with patch("tools.critique_resume.compute_plan_hash", return_value=None):
            result = find_reusable_run("nonexistent-plan.md", prefix="42", base_dir=str(base))

        assert result is None

    def test_main_exits_1_when_hash_is_none(
        self, tmp_path: Path, plan_hash: str, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
        )

        with patch("tools.critique_resume.compute_plan_hash", return_value=None):
            exit_code = main(["--plan", "nonexistent.md", "--issue", "42", "--base-dir", str(base)])

        captured = capsys.readouterr()
        assert exit_code == 1
        assert captured.out.strip() == ""


class TestMultipleDirs:
    """Multiple dirs: picks newest matching non-complete one."""

    def test_picks_newest(self, tmp_path: Path, plan_file: Path, plan_hash: str) -> None:
        base = tmp_path / ".critique-runs"
        # Older dir — matching + incomplete
        older = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T100000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )
        # Newer dir — matching + incomplete
        newer = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result == str(newer)
        assert result != str(older)

    def test_skips_complete_picks_next_newest(
        self, tmp_path: Path, plan_file: Path, plan_hash: str
    ) -> None:
        base = tmp_path / ".critique-runs"
        # Newest dir is complete → should be skipped
        _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=["skeptic"],  # complete
        )
        # Second dir — matching + incomplete → should be returned
        second = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T110000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result == str(second)

    def test_skips_stale_picks_matching(
        self, tmp_path: Path, plan_file: Path, plan_hash: str, capsys: pytest.CaptureFixture
    ) -> None:
        base = tmp_path / ".critique-runs"
        # Newest dir has stale hash
        stale = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T130000",
            plan_hash="sha256:stalehashstalehashhashstalehashhashstalehashhashstalehashhashstale",
            roster=["skeptic"],
        )
        # Older dir has matching hash + incomplete
        matching = _make_run_dir(
            base,
            prefix="42",
            timestamp="20260101T110000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        captured = capsys.readouterr()

        assert result == str(matching)
        # Stale dir must appear on stderr
        assert str(stale) in captured.err

    def test_prefix_does_not_match_other_issues(
        self, tmp_path: Path, plan_file: Path, plan_hash: str
    ) -> None:
        base = tmp_path / ".critique-runs"
        # A dir for a different issue
        _make_run_dir(
            base,
            prefix="99",
            timestamp="20260101T120000",
            plan_hash=plan_hash,
            roster=["skeptic"],
            completed_members=[],
        )
        # The target issue has no dirs
        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result is None


class TestMalformedRoster:
    """Malformed _roster.json → evaluate returns error → treat as not reusable."""

    def test_malformed_roster_skipped(
        self, tmp_path: Path, plan_file: Path, plan_hash: str
    ) -> None:
        base = tmp_path / ".critique-runs"
        run_dir = base / "42-20260101T120000"
        run_dir.mkdir(parents=True)
        # Write matching .plan_hash
        (run_dir / ".plan_hash").write_text(plan_hash, encoding="utf-8")
        # Write malformed (non-JSON) _roster.json
        (run_dir / "_roster.json").write_text("this is not json {{", encoding="utf-8")

        result = find_reusable_run(str(plan_file), prefix="42", base_dir=str(base))
        assert result is None


class TestCLIHelp:
    """CLI invocation tests for critique-resume-probe."""

    def test_help_exits_0(self) -> None:
        """--help should exit 0 and mention the program name."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_missing_required_args_exits_nonzero(self) -> None:
        """No arguments should cause argparse to exit non-zero."""
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code != 0

    def test_mutually_exclusive_issue_slug(self, tmp_path: Path) -> None:
        """--issue and --slug are mutually exclusive."""
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan", encoding="utf-8")
        with pytest.raises(SystemExit) as exc_info:
            main(
                [
                    "--plan",
                    str(plan),
                    "--issue",
                    "42",
                    "--slug",
                    "my-slug",
                    "--base-dir",
                    str(tmp_path / ".critique-runs"),
                ]
            )
        assert exc_info.value.code != 0

    def test_subprocess_help_exits_0(self) -> None:
        """Sanity-check: subprocess invocation of --help exits 0."""
        result = subprocess.run(
            [sys.executable, "-m", "tools.critique_resume", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "critique-resume-probe" in result.stdout
