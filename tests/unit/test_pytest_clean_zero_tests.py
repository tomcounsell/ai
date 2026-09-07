"""scripts/pytest-clean.sh fails closed when a green run executed nothing (#3195).

A run in which every selected test is *skipped* executes nothing and exits 0
-- indistinguishable, by exit code alone, from a real pass. The realistic
trigger is on this machine already: `tests/conftest.py`'s `scratch_test_db`
fixture calls `pytest.skip()` when the 15-slot machine-global test-DB pool is
exhausted, which happens routinely past ~5 concurrent agents. Any mutation
check whose selected tests all depend on that fixture reports a confident
green having run nothing.

`tests/unit/test_worktree_venv_absent_guard.py`'s `--version` model cannot
exercise this guard: `--version` runs no pytest session at all, so the
plugin never loads and every case here would land on "file absent -> pass
through". The harness below instead builds a real sandbox pytest rootdir
(own `pyproject.toml`, `.git` as a directory, a symlink to the repo's real
`.venv`, no `.python-version`, a copy of the plugin) and drives a genuine
pytest session through the wrapper. See spike-5/spike-6 in
docs/plans/pytest-clean-zero-tests-fail-closed.md.

Two rules this file follows, both round-1 false-pass channels:
  1. The negative control (TestNegativeControl) runs before any guard
     assertion is trusted -- it proves the sandbox's OWN plugin copy is what
     loads, not a decoy or the repo's.
  2. Every subprocess env is built from a copy of `os.environ` with
     `PYTHONPATH`, `PYTEST_CLEAN_COUNT_FILE`, and `PYTEST_ALLOW_ZERO_TESTS`
     removed (`_base_env`), so an ambient value inherited from the process
     running these tests (itself ordinarily run under
     scripts/pytest-clean.sh) cannot pass a test for the wrong reason.

The script and plugin under test are resolved from env vars
(`PYTEST_CLEAN_SCRIPT`, `PYTEST_EXECUTED_COUNT_SOURCE`), defaulting to the
real repo files. This is the mutation seam: `/do-build`'s validator points
these at mutated copies under a `mktemp -d` rather than editing the shared
checkout, which peer lanes are running out of concurrently.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_UNDER_TEST = Path(
    os.environ.get("PYTEST_CLEAN_SCRIPT", str(REPO_ROOT / "scripts" / "pytest-clean.sh"))
)
PLUGIN_SOURCE = Path(
    os.environ.get("PYTEST_EXECUTED_COUNT_SOURCE", str(REPO_ROOT / "pytest_executed_count.py"))
)

ZERO_TESTS_HEADLINE = "ZERO TESTS EXECUTED (#3195)"

# The three existing wrapper refusals this diagnostic must never be confused
# with, verbatim from scripts/pytest-clean.sh.
OTHER_GUARD_HEADLINES = (
    "worktree has no usable .venv of its own",
    "refusing to run against an off-pin interpreter",
    "WEDGED",
)

FIXTURE_SKIP = """
import pytest


@pytest.fixture
def pool_exhausted():
    pytest.skip("pool exhausted")


def test_one(pool_exhausted):
    assert True


def test_two(pool_exhausted):
    assert True
"""

BODY_SKIP = """
import pytest


def test_one():
    pytest.skip("gone")


def test_two():
    pytest.skip("gone")
"""

MARKER_SKIP = """
import pytest


@pytest.mark.skip
def test_one():
    assert True


@pytest.mark.skip
def test_two():
    assert True
"""

PASSING = """
def test_one():
    assert True
"""

FAILING = """
def test_one():
    assert False
"""

# A deliberate syntax error -- pytest cannot even collect this file.
COLLECTION_ERROR = "def test_bad(:\n    pass\n"

# Two genuine xfails (both fail as expected: call/skipped/wasxfail=True,
# spike-4). Deliberately NOT mixed with an xpass: an xpass reports
# call/passed/wasxfail=True, and "passed" already satisfies the counting
# rule's `outcome != "skipped"` branch on its own -- a mixed rootdir would
# still count the xpass with the wasxfail clause deleted and the mutation
# would not bite. Only an all-xfail (no xpass) rootdir isolates the clause:
# every report is call/skipped, so counting depends entirely on `wasxfail`
# being checked.
ALL_XFAIL = """
import pytest


@pytest.mark.xfail(reason="known failure")
def test_expected_fail_one():
    assert False


@pytest.mark.xfail(reason="known failure")
def test_expected_fail_two():
    assert False
"""


def _base_env() -> dict:
    """A copy of the ambient environment with PYTHONPATH,
    PYTEST_CLEAN_COUNT_FILE, and PYTEST_ALLOW_ZERO_TESTS removed. The process
    running this test file is itself ordinarily launched under
    scripts/pytest-clean.sh, which exports the first two -- an unstripped env
    would let a subprocess pass these tests for inheriting the outer run's
    state rather than producing its own. PYTEST_ALLOW_ZERO_TESTS is popped
    for the same reason: it is load-bearing for the skip-shape assertions and
    an ambient value would silently suppress the exit they check for.
    """
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTEST_CLEAN_COUNT_FILE", None)
    env.pop("PYTEST_ALLOW_ZERO_TESTS", None)
    # #3222 review tech debt: the #2574 stall watcher's subshell leaves a
    # `sleep 30` grandchild holding stdout/stderr pipe write-ends open past
    # the pytest controller exiting, so subprocess.run(..., capture_output=
    # True) blocks ~30s per invocation even for a trivial run like
    # `--version`. Measured: 30.1s with the watcher on vs 0.6s with
    # PYTEST_STALL_LIMIT_S=0. This file makes 15+ such calls; nothing here
    # exercises the wedge detector itself, and the guard still fires
    # correctly with the watcher disabled.
    env["PYTEST_STALL_LIMIT_S"] = "0"
    return env


def _sandbox(tmp_path: Path) -> Path:
    """A pytest rootdir shaped exactly as the guard's harness requires
    (spike-5): own pyproject.toml, .git as a directory (not a worktree
    gitdir-pointer file, which would trip the #3033 guard), a symlink to the
    repo's real .venv (so PYTEST_BIN resolves to a real pytest -- a fake
    `.venv/bin/pytest` cannot run a session at all), no .python-version (so
    check-interpreter-pin.sh returns 0 at its "no pin file" early exit), and
    a COPY of the plugin under test (never a shared reference -- the subject
    under test must be the sandbox's own copy).
    """
    root = tmp_path / "sandbox"
    root.mkdir()
    (root / ".git").mkdir()
    (root / ".venv").symlink_to(REPO_ROOT / ".venv")
    (root / "pyproject.toml").write_text('[tool.pytest.ini_options]\naddopts = ""\n')
    (root / "pytest_executed_count.py").write_text(PLUGIN_SOURCE.read_text())
    return root


def _write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body)


def _append_marker(root: Path) -> None:
    """Append a stderr marker to the sandbox's OWN plugin copy, fired at
    import time regardless of how the module gets resolved (`python -c` or
    `pytest -p`). Used only to prove which copy actually loaded -- the same
    technique the #3222 review used to demonstrate that TestNegativeControl's
    `python -c` check does not exercise `-p` resolution: `python -c` puts cwd
    on `sys.path`, `pytest -p` does not, so a test can pass via the former
    while the wrapper (which always uses `-p`) loads a different copy
    entirely.
    """
    plugin = root / "pytest_executed_count.py"
    with open(plugin, "a") as f:
        f.write(
            "\n"
            "import sys as _marker_sys\n"
            'print(f"SANDBOX_PLUGIN_LOADED:{__file__}", file=_marker_sys.stderr)\n'
        )


def _marker_lines(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("SANDBOX_PLUGIN_LOADED:")]


def _run(
    root: Path, args: list[str], env: dict | None = None, script: Path | None = None
) -> subprocess.CompletedProcess:
    cmd = [str(script or SCRIPT_UNDER_TEST), *args]
    return subprocess.run(
        cmd,
        cwd=str(root),
        env=env if env is not None else _base_env(),
        capture_output=True,
        text=True,
        timeout=90,
    )


class TestNegativeControl:
    """Must pass before any other test in this file is trusted. With a decoy
    module of the same name on PYTHONPATH behind the sandbox, `-p` resolved
    to the sandbox copy; with the sandbox absent from PYTHONPATH, it
    resolved to the decoy (spike-5). Without this control, a test can pass
    while exercising a different copy of the plugin than the one under
    test, so mutating that copy would produce no observable change.
    """

    def test_sandbox_plugin_resolves_from_the_sandbox(self, tmp_path):
        root = _sandbox(tmp_path)
        venv_python = root / ".venv" / "bin" / "python"
        result = subprocess.run(
            [str(venv_python), "-c", "import pytest_executed_count as m; print(m.__file__)"],
            cwd=str(root),
            env=_base_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        resolved = Path(result.stdout.strip()).resolve()
        assert resolved.is_relative_to(tmp_path.resolve()), (
            f"pytest_executed_count resolved to {resolved}, expected under {tmp_path} "
            "-- the sandbox's own copy did not load"
        )

    def test_sandbox_plugin_resolves_from_the_sandbox_via_dash_p(self, tmp_path):
        """#3222 review: the check above drives `python -c`, which is not the
        resolution path the wrapper (or any other test in this file) uses.
        `pytest -p` does not put cwd on sys.path, so it needs PYTHONPATH set
        explicitly -- exactly what the wrapper does at
        scripts/pytest-clean.sh:173 and what Blocker 2's fix restores for
        TestPluginFailsOpenOnAnUnwritableCountFile below. Proven with the
        marker technique: the sandbox's own copy prints its __file__ to
        stderr at import time.
        """
        root = _sandbox(tmp_path)
        _append_marker(root)
        _write(root, "test_x.py", PASSING)
        venv_pytest = root / ".venv" / "bin" / "pytest"
        env = _base_env()
        env["PYTHONPATH"] = str(root)
        result = subprocess.run(
            [str(venv_pytest), "-p", "pytest_executed_count", "test_x.py", "-q"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        markers = _marker_lines(result.stderr)
        assert len(markers) == 1, result.stderr
        resolved = Path(markers[0].split(":", 1)[1]).resolve()
        assert resolved.is_relative_to(tmp_path.resolve()), (
            f"pytest_executed_count resolved to {resolved} via -p with PYTHONPATH set, "
            f"expected under {tmp_path}"
        )

    def test_without_pythonpath_dash_p_does_not_provably_load_the_sandbox_copy(self, tmp_path):
        """The negative control for the check above, and the exact defect
        Blocker 2 named: without PYTHONPATH pointed at the sandbox, `-p`
        resolution falls through to whatever copy of the module the venv's
        own import machinery finds first (e.g. an editable-install `.pth`
        entry for a different checkout), never the sandbox's marked copy --
        so the marker must NOT appear.

        #3222 review nit: this control used to assert only "no marker",
        which passes identically whether -p resolved to a *different* copy
        (what it documents -- exit 0, 1 passed, silently the wrong module)
        or died with ImportError (round 1's linked-worktree symptom, a
        completely different failure). Measured directly on this machine:
        the real repo-root pytest_executed_count.py is what an editable
        install's `.pth` entry surfaces here, so the run succeeds with
        `1 passed` -- pinning returncode == 0 is what makes this test able
        to tell the two states apart.
        """
        root = _sandbox(tmp_path)
        _append_marker(root)
        _write(root, "test_x.py", PASSING)
        venv_pytest = root / ".venv" / "bin" / "pytest"
        env = _base_env()  # PYTHONPATH deliberately left unset
        result = subprocess.run(
            [str(venv_pytest), "-p", "pytest_executed_count", "test_x.py", "-q"],
            cwd=str(root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "1 passed" in result.stdout, result.stdout
        assert not _marker_lines(result.stderr), (
            "the sandbox's marked plugin copy loaded via -p without PYTHONPATH set -- "
            "this control is supposed to prove that does NOT happen"
        )


class TestSkipShapesAreRefused:
    """The three ways a test can execute nothing while still reporting
    'skipped', each measured in spike-4. Named so `-k skip_shape` selects
    exactly these three (Verification table)."""

    def test_fixture_skip_shape_is_refused(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", FIXTURE_SKIP)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode != 0, result.stdout
        assert ZERO_TESTS_HEADLINE in result.stderr

    def test_body_skip_shape_is_refused(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", BODY_SKIP)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode != 0, result.stdout
        assert ZERO_TESTS_HEADLINE in result.stderr

    def test_marker_skip_shape_is_refused(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", MARKER_SKIP)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode != 0, result.stdout
        assert ZERO_TESTS_HEADLINE in result.stderr


class TestAlreadyRedRunsKeepTheirOwnStatus:
    """The guard may only ever convert a green into a red (spike-8). Named so
    `-k pytest_status_preserved` selects exactly these two."""

    def test_collection_error_pytest_status_preserved(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", COLLECTION_ERROR)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode == 2, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_zero_collected_pytest_status_preserved(self, tmp_path):
        root = _sandbox(tmp_path)
        (root / "empty").mkdir()
        result = _run(root, ["empty/", "-q"])
        assert result.returncode == 5, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr


class TestPassThroughPathsAreUnaffected:
    def test_all_passing_exits_zero_no_diagnostic(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_failing_test_keeps_pytests_own_status(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", FAILING)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode != 0
        assert ZERO_TESTS_HEADLINE not in result.stderr
        assert "1 failed" in result.stdout

    def test_collect_only_exits_zero(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", FIXTURE_SKIP)
        result = _run(root, ["test_x.py", "--collect-only", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_setup_plan_exits_zero(self, tmp_path):
        """#3222 review Blocker 1: --setup-plan runs a real session, produces
        zero `call` reports, and exits 0 through bare pytest. Before the fix
        this reverted to exit 1 with a false pool-exhaustion diagnostic."""
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "--setup-plan", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_setup_only_exits_zero(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "--setup-only", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_fixtures_exits_zero(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "--fixtures", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_fixtures_per_test_exits_zero(self, tmp_path):
        """#3222 review round 2 blocker: --fixtures-per-test (dest
        show_fixtures_per_test) is a fourth call site (_pytest/fixtures.py's
        _show_fixtures_per_test) that wrap_session runs while executing zero
        `call` reports, same shape as --fixtures. Before this fix it reverted
        to exit 1 with a false pool-exhaustion diagnostic."""
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "--fixtures-per-test", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_cache_show_exits_zero(self, tmp_path):
        """#3222 review round 2 blocker: --cache-show (dest cacheshow) is the
        fourth wrap_session call site (_pytest/cacheprovider.py's cacheshow),
        distinct from the test-execution flags entirely -- it just prints
        cache contents. Before this fix it reverted to exit 1 with a false
        pool-exhaustion diagnostic."""
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "--cache-show", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_version_exits_zero(self, tmp_path):
        root = _sandbox(tmp_path)
        result = _run(root, ["--version"])
        assert result.returncode == 0, result.stdout


class TestXfailCountsAsExecuted:
    """#3222 review tech debt: pytest_executed_count.py:110's `wasxfail`
    clause shipped with zero behavioral cover -- no rootdir in this file
    produced an xfail report, so deleting the clause left the full file
    green (mutation-measured: `27 passed`). An xfail reports
    call/skipped/wasxfail=True (spike-4): `outcome != "skipped"` is False,
    so without the `wasxfail` check every xfail is miscounted as
    not-executed. An all-xfail selection (this rootdir) would undercount to
    `count 0` and trip a false ZERO TESTS EXECUTED on a run that was
    actually informative.
    """

    def test_all_xfail_exits_zero_no_diagnostic(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", ALL_XFAIL)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr
        assert "2 xfailed" in result.stdout


class TestEscapeHatchSuppressesExitNotMessage:
    def test_allow_zero_tests_suppresses_exit_but_not_message(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", FIXTURE_SKIP)
        env = _base_env()
        env["PYTEST_ALLOW_ZERO_TESTS"] = "1"
        result = _run(root, ["test_x.py", "-q"], env=env)
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE in result.stderr


class TestCallerFlagsSurviveTheInjection:
    """The `-p pytest_executed_count` injection uses `set --`, which
    prepends rather than replaces, so a caller's own `-p` flag must still
    take effect (Risk 2)."""

    def test_default_run_creates_the_pytest_cache_dir(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode == 0
        assert (root / ".pytest_cache").exists()

    def test_callers_own_p_flag_still_applies(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "-p", "no:cacheprovider", "-q"])
        assert result.returncode == 0, result.stdout
        assert not (root / ".pytest_cache").exists()


class TestCountFileIsNeverInherited:
    """Race 4: this plan's own tests run scripts/pytest-clean.sh under
    scripts/pytest-clean.sh, so the inner wrapper inherits the outer run's
    exported PYTEST_CLEAN_COUNT_FILE unless the mint is unconditional."""

    def test_nested_invocation_leaves_the_outer_count_file_untouched(self, tmp_path):
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", PASSING)
        outer_file = tmp_path / "outer-count-file"
        outer_file.write_text("count 7")
        env = _base_env()
        env["PYTEST_CLEAN_COUNT_FILE"] = str(outer_file)
        result = _run(root, ["test_x.py", "-q"], env=env)
        assert result.returncode == 0, result.stdout
        assert outer_file.read_text() == "count 7"

    def test_early_abort_before_the_mint_leaves_an_inherited_count_file_untouched(self, tmp_path):
        """#3222 review tech debt: scripts/pytest-clean.sh:112's
        COUNT_FILE_MINTED tracker shipped with zero cover for the path it was
        added to fix. The count file is minted well AFTER the #3033
        worktree-venv guard, so a linked worktree with no usable `.venv`
        aborts before COUNT_FILE_MINTED is ever assigned -- if cleanup()
        reverted to `rm -f "$PYTEST_CLEAN_COUNT_FILE"` it would delete
        whatever an ENCLOSING wrapper invocation already exported there,
        since that env var is inherited from the caller's environment before
        the mint site runs. Same shape as the nested-invocation case above,
        but for the abort-before-mint path rather than the success path.
        """
        root = tmp_path / "no_venv_worktree"
        root.mkdir()
        (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts = ''\n")
        (root / ".git").write_text("gitdir: /somewhere/.git/worktrees/x\n")
        outer_file = tmp_path / "outer-count-file"
        outer_file.write_text("count 7")
        env = _base_env()
        env["PYTEST_CLEAN_COUNT_FILE"] = str(outer_file)
        result = _run(root, ["--version"], env=env)
        assert result.returncode != 0, result.stdout
        assert "worktree has no usable .venv of its own" in result.stderr
        assert outer_file.read_text() == "count 7"


class TestPluginFailsOpenOnAnUnwritableCountFile:
    """The plugin's write helper swallows OSError -- a deliberate fail-OPEN
    on an unwritable temp dir, so a broken path degrades to today's
    behavior instead of taking down an otherwise-passing run. Exercises the
    plugin directly (not through the wrapper): the wrapper's own mint is
    unconditional `mktemp`, so a test cannot steer where the wrapper itself
    writes; the plugin's OSError handling is the thing under test here."""

    def test_unwritable_count_file_path_does_not_break_the_run(self, tmp_path):
        root = _sandbox(tmp_path)
        _append_marker(root)
        _write(root, "test_x.py", PASSING)
        unwritable_dir = tmp_path / "unwritable"
        unwritable_dir.mkdir(mode=0o000)
        try:
            env = _base_env()
            # #3222 review Blocker 2: this is the one test in the file that
            # calls pytest directly instead of through the wrapper, so it
            # loses the wrapper's `export PYTHONPATH="$REPO_ROOT"`
            # (scripts/pytest-clean.sh:173) and _base_env() strips it too.
            # Without this, -p resolution falls through to whatever copy the
            # venv's own import machinery finds first -- in a linked worktree
            # that dies with ImportError, and where it happens to succeed it
            # silently exercises the WRONG copy of the module under test.
            # Set it exactly as the wrapper does for every other case.
            env["PYTHONPATH"] = str(root)
            env["PYTEST_CLEAN_COUNT_FILE"] = str(unwritable_dir / "count-file")
            venv_pytest = root / ".venv" / "bin" / "pytest"
            result = subprocess.run(
                [str(venv_pytest), "-p", "pytest_executed_count", "test_x.py", "-q"],
                cwd=str(root),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            assert "1 passed" in result.stdout
            # Prove the sandbox's own copy is what actually ran -- the fix
            # above is inert if this doesn't hold (Blocker 2's whole point).
            markers = _marker_lines(result.stderr)
            assert len(markers) == 1, result.stderr
            resolved = Path(markers[0].split(":", 1)[1]).resolve()
            assert resolved.is_relative_to(tmp_path.resolve()), (
                f"pytest_executed_count resolved to {resolved}, expected under {tmp_path}"
            )
        finally:
            unwritable_dir.chmod(0o755)


class TestDiagnosticIsDistinctFromTheOtherGuards:
    def test_diagnostic_headline_names_cause_remedy_and_hatch(self, tmp_path):
        # #3222 review nit, deliberately not fixed: `_base_env()` sets
        # PYTEST_STALL_LIMIT_S=0 for every subprocess in this file, so the
        # #2574 wedge watcher subshell never starts and "WEDGED" is
        # unreachable in the assertion loop below by construction, not
        # because this diagnostic is distinct from it. Restoring the
        # watcher's production default here would reintroduce the ~30s
        # per-call tax _base_env() exists to remove, for a headline this
        # file's other assertions already prove is unique text. Left as-is;
        # the WEDGED headline itself has no dedicated regression test
        # anywhere in this suite.
        root = _sandbox(tmp_path)
        _write(root, "test_x.py", FIXTURE_SKIP)
        result = _run(root, ["test_x.py", "-q"])
        assert ZERO_TESTS_HEADLINE in result.stderr
        assert "test-DB pool" in result.stderr
        assert "reap-xdist.sh --apply" in result.stderr
        assert "PYTEST_ALLOW_ZERO_TESTS" in result.stderr
        for headline in OTHER_GUARD_HEADLINES:
            assert headline not in result.stderr


class TestInjectionGateFalseBranch:
    """The injection gate `if [ -f "$REPO_ROOT/pytest_executed_count.py" ]`
    (scripts/pytest-clean.sh:198) had no automated cover for its false
    branch: `_sandbox()` always copies the plugin in, so deleting the `if`
    killed no existing test even though the branch is what protects six
    concurrent lanes from an ImportError on every run if the module is ever
    absent from a checkout (#3222 review nit)."""

    def test_run_without_the_plugin_present_is_unaffected(self, tmp_path):
        root = _sandbox(tmp_path)
        (root / "pytest_executed_count.py").unlink()
        _write(root, "test_x.py", PASSING)
        result = _run(root, ["test_x.py", "-q"])
        assert result.returncode == 0, result.stdout
        assert ZERO_TESTS_HEADLINE not in result.stderr

    def test_help_passes_through_untouched(self, tmp_path):
        """--help is named in the plan's Success Criteria but had no case
        (#3222 review nit)."""
        root = _sandbox(tmp_path)
        result = _run(root, ["--help"])
        assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# Shell-level check of the pass-through predicate. The count file is minted
# by the wrapper and written only by the plugin, so no test can seed it with
# garbage through the wrapper's public surface -- driving pytest to produce a
# truncated file is not reproducible. This drives the wrapper's own
# `verdict_passes_through` body, sliced out of the script under test at run
# time, never a retyped copy of its `case` patterns: a loop that repeats the
# patterns in this file would pass identically with the wrapper's own `case`
# deleted (spike-9).
# ---------------------------------------------------------------------------

_PREDICATE_DRIVER = r"""
set -u
SCRIPT="$1"
SLICE="$(mktemp)"
sed -n '/^verdict_passes_through()/,/^}/p' "$SCRIPT" > "$SLICE"

if [ ! -s "$SLICE" ]; then
    echo "EMPTY_SLICE: verdict_passes_through not found in $SCRIPT" >&2
    rm -f "$SLICE"
    exit 3
fi

if grep -q '^exit ' "$SLICE"; then
    echo "SLICE_OVERRUN: verdict_passes_through is not in the sliceable multi-line form" >&2
    rm -f "$SLICE"
    exit 4
fi

. "$SLICE"

if ! declare -f verdict_passes_through >/dev/null 2>&1; then
    echo "UNDEFINED: verdict_passes_through is not defined after sourcing" >&2
    rm -f "$SLICE"
    exit 5
fi

rm -f "$SLICE"

check() {
    local input="$1" expect="$2" got
    if verdict_passes_through "$input"; then got=pass; else got=fail; fi
    if [ "$got" != "$expect" ]; then
        echo "MISMATCH: input=[$input] expected=$expect got=$got" >&2
        exit 6
    fi
}

check ""             pass
check "collectonly"  pass
check "count 0"      fail
check "count 1"      pass
check "count 10"     pass
check "started"      fail
check "coun"         fail
check "  "           fail
check "count -1"     fail

echo "ALL_OK"
"""


def _run_predicate_driver(script_path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", _PREDICATE_DRIVER, "predicate-driver", str(script_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestPredicateSlice:
    def test_predicate_allowlist_matches_the_wrapper_exactly(self):
        result = _run_predicate_driver(SCRIPT_UNDER_TEST)
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "ALL_OK" in result.stdout

    def test_slice_refuses_when_the_function_is_absent(self, tmp_path):
        scratch = tmp_path / "no_predicate.sh"
        scratch.write_text("#!/usr/bin/env bash\necho hi\n")
        result = _run_predicate_driver(scratch)
        assert result.returncode == 3
        assert "EMPTY_SLICE" in result.stderr

    def test_slice_refuses_on_a_collapsed_one_line_definition(self, tmp_path):
        # Mirrors what task 2 forbids: `verdict_passes_through` collapsed to
        # one line ending `esac; }` makes the sed range run to EOF and
        # swallow whatever follows -- here a synthetic `exit 0`, in the real
        # wrapper the script's own `exit "$PYTEST_EXIT"`.
        scratch = tmp_path / "collapsed.sh"
        scratch.write_text(
            "#!/usr/bin/env bash\n"
            'verdict_passes_through() { case "$1" in "") return 0 ;; *) return 1 ;; esac; }\n'
            "exit 0\n"
        )
        result = _run_predicate_driver(scratch)
        assert result.returncode == 4
        assert "SLICE_OVERRUN" in result.stderr

    def test_driver_does_not_use_the_vacuous_process_substitution_form(self):
        # Anti-criterion (spike-9): under /bin/bash 3.2.57 on macOS,
        # process-substitution sourcing returns 0 and defines nothing, so
        # every verdict would report a vacuous pass. Pinned here as a
        # negative assertion on the driver itself.
        assert "<(" not in _PREDICATE_DRIVER
