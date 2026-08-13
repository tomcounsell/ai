"""The fence-census anti-criterion and its red-state proof (#2518, Task 9).

``tools/check_fence_census.py`` is the mechanical answer to the pattern that let
PR #2516 ship six unguarded fenced-pid consumers: a correct new primitive
applied at the sites the author was looking at, with the remaining sites still
compiling and still passing. Nothing enumerated "every consumer of a fenced
pid", so review had to do it by eye, twice, and missed it twice.

**A checker that cannot fail is worthless**, which is why the red-state proof is
the load-bearing test here, not the green one. `tests/fixtures/fence_census_violator/`
holds a deliberately-violating input; the checker must exit 1 against it and
name both violating functions. `tests/` is excluded from the checker's own scan
dirs on purpose, so the fixture can live in the repo without failing the real
run.

The green assertion matters too, but for a different reason: it is the
Verification row, so it must be true at HEAD and must be run the way the row
runs it — as a subprocess, from an arbitrary cwd, exit code checked directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_claim import subprocess_env
from tools.check_fence_census import EXEMPTION_MARKER, check_file

#: tests/unit/test_fence_census.py -> repo root. Resolved from this file rather
#: than from cwd so the subprocess invocations below are cwd-independent.
REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKER = REPO_ROOT / "tools" / "check_fence_census.py"
VIOLATOR_ROOT = REPO_ROOT / "tests" / "fixtures" / "fence_census_violator"


def _run_checker(root: Path, *extra: str, cwd: Path | None = None):
    """Invoke the checker as a subprocess, exactly as the Verification row does."""
    return subprocess.run(
        [sys.executable, str(CHECKER), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        cwd=str(cwd) if cwd is not None else str(Path(__file__).parent),
        env=subprocess_env(),
        timeout=120,
    )


class TestGreenState:
    """The real repo is clean — this is the Verification row."""

    def test_repo_root_exits_zero(self):
        result = _run_checker(REPO_ROOT)
        assert result.returncode == 0, (
            "The fence census must be clean at HEAD. Unguarded consumers:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        assert "clean" in result.stdout

    def test_is_cwd_independent(self, tmp_path):
        """Run from an unrelated directory — the checker resolves its own paths."""
        result = _run_checker(REPO_ROOT, cwd=tmp_path)
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"

    def test_reports_a_nonzero_number_of_guarded_consumers(self):
        """A checker that finds NOTHING also exits 0.

        Without this, deleting the detection logic entirely would leave the
        Verification row green. The count is asserted as ">0", never as an exact
        threshold — a threshold is the very failure mode this tool replaces.
        """
        result = _run_checker(REPO_ROOT, "--list")
        assert result.returncode == 0
        assert "Guarded fenced-pid consumers (0):" not in result.stdout, (
            "The census found zero consumers — it is not looking at anything, "
            "so its clean verdict is vacuous"
        )
        assert "agent/session_health.py" in result.stdout, (
            "session_health.py holds most of the fenced-pid decision sites; if "
            "the census does not see them, its pattern has drifted from the code"
        )


class TestRedState:
    """The proof the checker can fail. Without this, green means nothing."""

    def test_violator_fixture_exits_one(self):
        result = _run_checker(VIOLATOR_ROOT)
        assert result.returncode == 1, (
            "The census must FAIL against a deliberately-violating input.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FENCE CENSUS FAILED" in result.stdout

    def test_violator_output_names_both_violating_functions(self):
        result = _run_checker(VIOLATOR_ROOT)
        assert "_reprieve_like_consumer" in result.stdout, (
            "the exact shape of the six #2516 defects (pid rebound, create_time "
            "dropped) must be reported"
        )
        assert "_inline_consumer" in result.stdout, (
            "the inline variant must be reported too — reporting only the "
            "assigned-to-a-local form would miss half the defect class"
        )

    def test_violator_output_carries_file_and_line(self):
        """``file:line`` is what makes the failure actionable, not a bare count."""
        result = _run_checker(VIOLATOR_ROOT)
        assert "agent/violator.py:" in result.stdout
        # The offending source line is echoed so a reviewer sees the shape.
        assert 'get("pid")' in result.stdout

    def test_guarded_and_log_only_functions_are_not_reported(self):
        """False positives would train people to ignore the checker."""
        result = _run_checker(VIOLATOR_ROOT)
        assert "_properly_guarded" not in result.stdout, (
            "a function that calls fence_is_live is guarded and must not be flagged"
        )
        assert "_log_only" not in result.stdout, (
            "the exemption marker must suppress a log-only read"
        )

    def test_exactly_two_violations_are_reported(self):
        result = _run_checker(VIOLATOR_ROOT)
        assert "2 unguarded fenced-pid consumer(s)" in result.stdout

    def test_failure_output_tells_the_reader_how_to_exempt(self):
        result = _run_checker(VIOLATOR_ROOT)
        assert EXEMPTION_MARKER in result.stdout, (
            "the failure message must name the exemption mechanism, or the "
            "next person hits it and weakens the checker instead"
        )


class TestExemptionMarkerPrecision:
    """The marker covers its own line and the one immediately below — no further.

    A marker with loose scope is a silently-widening exemption: one annotation
    near the top of a function would excuse every read beneath it. That is the
    rising-threshold failure this tool exists to replace, reintroduced through
    the back door.
    """

    def _write(self, tmp_path: Path, body: str) -> Path:
        pkg = tmp_path / "agent"
        pkg.mkdir(parents=True, exist_ok=True)
        path = pkg / "sample.py"
        path.write_text(body, encoding="utf-8")
        return path

    def test_marker_on_the_line_above_exempts(self, tmp_path):
        path = self._write(
            tmp_path,
            "def consumer(entry):\n"
            f"    # {EXEMPTION_MARKER}\n"
            '    return (getattr(entry, "live_fence", None) or {}).get("pid")\n',
        )
        violations, _ = check_file(path)
        assert violations == []

    def test_marker_on_the_reads_own_line_exempts(self, tmp_path):
        path = self._write(
            tmp_path,
            "def consumer(entry):\n"
            '    return (getattr(entry, "live_fence", None) or {}).get("pid")'
            f"  # {EXEMPTION_MARKER}\n",
        )
        violations, _ = check_file(path)
        assert violations == []

    def test_marker_two_lines_above_does_not_exempt(self, tmp_path):
        path = self._write(
            tmp_path,
            "def consumer(entry):\n"
            f"    # {EXEMPTION_MARKER}\n"
            "    x = 1  # an intervening line\n"
            '    return (getattr(entry, "live_fence", None) or {}).get("pid")\n',
        )
        violations, _ = check_file(path)
        assert len(violations) == 1, (
            "an exemption must not reach past the line immediately below it, or "
            "one annotation silently excuses a whole function body"
        )
        assert violations[0][1] == "consumer"

    def test_marker_below_the_read_does_not_exempt(self, tmp_path):
        path = self._write(
            tmp_path,
            "def consumer(entry):\n"
            '    pid = (getattr(entry, "live_fence", None) or {}).get("pid")\n'
            f"    # {EXEMPTION_MARKER}\n"
            "    return pid\n",
        )
        violations, _ = check_file(path)
        assert len(violations) == 1

    def test_a_near_miss_marker_string_does_not_exempt(self, tmp_path):
        """The marker is matched exactly — an approximation is not an exemption."""
        path = self._write(
            tmp_path,
            "def consumer(entry):\n"
            "    # fence-census: log only\n"
            '    return (getattr(entry, "live_fence", None) or {}).get("pid")\n',
        )
        violations, _ = check_file(path)
        assert len(violations) == 1


class TestGuardRecognition:
    """What the checker accepts as a guard, pinned so it cannot silently widen."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        pkg = tmp_path / "agent"
        pkg.mkdir(parents=True, exist_ok=True)
        path = pkg / "sample.py"
        path.write_text(body, encoding="utf-8")
        return path

    @pytest.mark.parametrize("guard", ["fence_is_live", "create_times_match"])
    def test_either_guard_predicate_counts(self, tmp_path, guard):
        path = self._write(
            tmp_path,
            "def consumer(entry, observed):\n"
            '    fence = getattr(entry, "live_fence", None)\n'
            '    pid = fence.get("pid")\n'
            f"    return {guard}(pid, observed)\n",
        )
        violations, guarded = check_file(path)
        assert violations == []
        assert len(guarded) == 1

    def test_forwarding_both_halves_counts_as_guarded(self, tmp_path):
        """Plumbing is not the defect class.

        The six #2516 defects DISCARDED the ``create_time`` sitting in the same
        dict. A scope that keeps both halves and hands them on is by
        construction not in that class, and the function it hands them to is
        checked independently by this same rule.
        """
        path = self._write(
            tmp_path,
            "def plumbing(entry):\n"
            '    fence = getattr(entry, "live_fence", None)\n'
            '    pid = fence.get("pid")\n'
            '    ct = fence.get("create_time")\n'
            "    return decide(pid, ct)\n",
        )
        violations, guarded = check_file(path)
        assert violations == []
        assert len(guarded) == 1

    def test_a_guard_in_a_nested_function_does_not_vouch_for_the_enclosing_one(self, tmp_path):
        """Adjacency is function-scoped. A closure's guard is not the caller's."""
        path = self._write(
            tmp_path,
            "def outer(entry):\n"
            '    fence = getattr(entry, "live_fence", None)\n'
            '    pid = fence.get("pid")\n'
            "\n"
            "    def inner(p, ct):\n"
            "        return fence_is_live(p, ct)\n"
            "\n"
            "    return pid\n",
        )
        violations, _ = check_file(path)
        assert len(violations) == 1
        assert violations[0][1] == "outer"

    def test_a_file_with_no_fence_reads_is_not_flagged(self, tmp_path):
        path = self._write(tmp_path, "def unrelated(x):\n    return x.get('pid')\n")
        violations, guarded = check_file(path)
        assert violations == []
        assert guarded == []

    def test_an_unparseable_file_is_skipped_not_fatal(self, tmp_path):
        """A syntax error elsewhere in the tree must not take the census down."""
        path = self._write(tmp_path, "live_fence\ndef broken(:\n    pass\n")
        violations, guarded = check_file(path)
        assert violations == []
        assert guarded == []


class TestFixtureIsOutOfScanScope:
    def test_tests_dir_is_excluded_from_the_default_scan(self):
        """Otherwise the violating fixture would fail the real run forever."""
        from tools.check_fence_census import SCAN_DIRS

        assert "tests" not in SCAN_DIRS

    def test_the_fixture_still_exists_where_the_red_state_proof_expects_it(self):
        assert (VIOLATOR_ROOT / "agent" / "violator.py").is_file(), (
            "the red-state proof is only as durable as its fixture; deleting "
            "the fixture must fail loudly rather than silently skip the proof"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
