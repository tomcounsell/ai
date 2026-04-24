#!/usr/bin/env python3
"""Refresh ``data/main_test_baseline.json`` from N pytest runs.

Runs pytest N times against the current checkout (intended for a clean
``main`` checkout), aggregates per-test outcomes across runs via ``--junitxml``,
classifies each failing test into one of four buckets, and writes a schema-v2
baseline file consumed by ``/do-merge``'s Full Suite Gate.

Classification precedence (first match wins):

1. Any collection error                         -> ``import_error``
2. Any pytest-timeout failure (exact prefix)    -> ``hung``
3. 100% non-pass across N runs                  -> ``real``
4. 1-99% non-pass across N runs                 -> ``flaky``

Default arguments are tuned for local dev.  See
``docs/features/merge-gate-baseline.md`` for the full rationale.

Usage:

    python scripts/refresh_test_baseline.py                # 3 runs, 60s per test
    python scripts/refresh_test_baseline.py --runs 5
    python scripts/refresh_test_baseline.py --dry-run      # prints to stdout
    python scripts/refresh_test_baseline.py --merge        # preserve note fields
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

# When invoked as ``python scripts/refresh_test_baseline.py`` (rather than
# ``python -m scripts.refresh_test_baseline``), the repo root is not on
# sys.path so ``from scripts._baseline_common import ...`` fails. Inject the
# repo root (parent of the ``scripts`` dir) before the import so both
# invocation styles work.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts._baseline_common import (  # noqa: E402 -- sys.path must be set first
    CATEGORY_FLAKY,
    CATEGORY_HUNG,
    CATEGORY_IMPORT_ERROR,
    CATEGORY_REAL,
    SCHEMA_VERSION,
    JunitxmlParseError,
    parse_junitxml,
)

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE_PATH = PROJECT_DIR / "data" / "main_test_baseline.json"

# Cap the default global timeout at 2h regardless of estimated test count.
DEFAULT_GLOBAL_TIMEOUT_CAP = 7200

# Fallback test-count estimate when ``pytest --collect-only`` cannot be
# parsed (e.g. collection itself errors, or the summary line is missing).
# Matches the current suite size order-of-magnitude; only used to size the
# default ``--global-timeout``, so a rough number is sufficient.
_FALLBACK_TEST_COUNT_ESTIMATE = 1500


def classify(
    outcomes_per_run: list[str],
) -> tuple[str, float, int] | None:
    """Classify a single test given its per-run outcomes.

    ``outcomes_per_run`` is a list of strings from surviving runs; each string
    is one of ``"pass"``, ``"fail"``, ``"timeout"``, or ``"collection_error"``.

    Returns:
        ``None`` if the test passed in every run (caller should skip; passing
        tests are not recorded in the baseline).  Otherwise a tuple of
        ``(category, fail_rate, hung_count)``.

    Precedence is applied in order; the first match wins:

    1. Any ``collection_error`` -> ``import_error``.
    2. Any ``timeout`` -> ``hung`` (even if there are also fails in other runs,
       because a hang is a different failure mode with a different fix surface).
    3. 100% non-pass (``fail`` + ``collection_error`` + ``timeout`` == total) -> ``real``.
    4. Otherwise -> ``flaky``.
    """
    total = len(outcomes_per_run)
    if total == 0:
        return None

    collection_errors = outcomes_per_run.count("collection_error")
    timeouts = outcomes_per_run.count("timeout")
    fails = outcomes_per_run.count("fail")
    non_pass = collection_errors + timeouts + fails

    if non_pass == 0:
        return None  # Test passed every run.

    fail_rate = non_pass / total

    if collection_errors > 0:
        return CATEGORY_IMPORT_ERROR, fail_rate, timeouts
    if timeouts > 0:
        return CATEGORY_HUNG, fail_rate, timeouts
    if non_pass == total:
        return CATEGORY_REAL, fail_rate, 0
    return CATEGORY_FLAKY, fail_rate, 0


def aggregate_outcomes(
    per_run_outcomes: Iterable[dict[str, str]],
) -> dict[str, list[str]]:
    """Combine per-run ``{node_id: outcome}`` maps into ``{node_id: [outcome, ...]}``.

    Runs that discover a test for the first time may miss it in earlier runs.
    Runs where a test never appears (e.g. never collected, or run was discarded
    entirely) are treated as absent for that node -- only the runs that
    actually observed the node are recorded.  This matches the intuition
    "classify from the runs you have".
    """
    aggregated: dict[str, list[str]] = {}
    for run in per_run_outcomes:
        for node_id, outcome in run.items():
            aggregated.setdefault(node_id, []).append(outcome)
    return aggregated


def capture_commit(repo_root: Path) -> str:
    """Return ``git rev-parse --short HEAD`` with ``-dirty`` suffix if appropriate.

    The suffix is appended when either ``git diff --quiet`` (unstaged) or
    ``git diff --cached --quiet`` (staged-but-uncommitted) exits non-zero.
    A baseline captured against a dirty tree is irreproducible -- the suffix
    is a reader-visible flag that the staleness warning also keys off of.
    """
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"

    dirty = False
    for args in (["git", "diff", "--quiet"], ["git", "diff", "--cached", "--quiet"]):
        try:
            result = subprocess.run(args, cwd=repo_root, check=False)
        except FileNotFoundError:
            return sha  # git not available - bail before we lie about dirtiness
        if result.returncode != 0:
            dirty = True
            break

    return f"{sha}-dirty" if dirty else sha


def run_pytest_once(
    run_index: int,
    junitxml_path: Path,
    test_timeout: int,
    global_timeout: int,
    pytest_args: list[str] | None = None,
    verbose: bool = False,
) -> bool:
    """Invoke pytest one time, writing junitxml to ``junitxml_path``.

    Returns True on a completed run that produced a junitxml file
    (regardless of pytest's own exit code -- failures are expected and get
    classified).  Returns False in any of these cases:

    - the outer ``subprocess.run(timeout=...)`` safety net fired
    - pytest crashed before writing junitxml (plugin registration errors,
      option-parse errors, conftest import failures, etc.)

    Either way the whole run is UNCLASSIFIABLE and the caller should
    discard it.  Before BLOCKER fix: a startup crash returned True and the
    downstream ParseError path masked the root cause as "all N runs
    failed" without any signal that pytest never wrote a file.  Now the
    missing-junitxml case is surfaced with captured stderr so the user
    sees what actually went wrong.
    """
    # pytest-timeout registers itself via setuptools entry points as the
    # short name ``timeout``.  Passing ``-p pytest_timeout`` here attempts a
    # second registration under the fully-qualified module name and raises
    # ``ValueError: Plugin already registered under a different name``.  The
    # ``--timeout=...`` flag alone is enough to activate the plugin -- if
    # ``pytest-timeout`` is not installed, pytest exits with a clear
    # "unrecognised option" error, which is the correct failure mode.
    cmd = [
        "pytest",
        "-q",
        "--tb=no",
        f"--junitxml={junitxml_path}",
        f"--timeout={test_timeout}",
        "--timeout-method=thread",
    ]
    if pytest_args:
        cmd.extend(pytest_args)
    else:
        cmd.append("tests/")

    if verbose:
        logger.info("[refresh] run %d: %s", run_index, " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            cwd=PROJECT_DIR,
            check=False,
            timeout=global_timeout,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[refresh] run %d hit the outer --global-timeout (%ds); discarding",
            run_index,
            global_timeout,
        )
        return False

    if not junitxml_path.exists():
        # pytest completed but never produced junitxml -- a startup-time
        # failure (plugin registration error, option parsing, conftest
        # import).  Surface stderr so the cause is visible instead of
        # being masked by the downstream JunitxmlParseError path.
        stderr_tail = (result.stderr or "").strip().splitlines()[-10:]
        stderr_blob = "\n".join(stderr_tail) if stderr_tail else "<no stderr>"
        logger.warning(
            "[refresh] run %d: pytest exited with code %d but did not write "
            "junitxml (%s); discarding run. stderr tail:\n%s",
            run_index,
            result.returncode,
            junitxml_path,
            stderr_blob,
        )
        return False

    return True


def load_existing_notes(path: Path) -> dict[str, str]:
    """Return ``{node_id: note}`` from an existing schema-v2 file (for --merge)."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("[refresh] could not load existing notes from %s: %s", path, exc)
        return {}
    tests = data.get("tests")
    if not isinstance(tests, dict):
        return {}
    notes: dict[str, str] = {}
    for node_id, record in tests.items():
        if isinstance(record, dict) and isinstance(record.get("note"), str):
            notes[node_id] = record["note"]
    return notes


def build_baseline(
    aggregated: dict[str, list[str]],
    runs: int,
    repo_root: Path,
    argv: list[str],
    preserved_notes: dict[str, str] | None = None,
) -> dict:
    """Build the schema-v2 baseline dict from aggregated outcomes."""
    preserved_notes = preserved_notes or {}
    tests: dict[str, dict] = {}
    for node_id, outcomes in aggregated.items():
        classified = classify(outcomes)
        if classified is None:
            continue
        category, fail_rate, hung_count = classified
        entry: dict[str, int | float | str] = {
            "category": category,
            "fail_rate": round(fail_rate, 4),
            "hung_count": hung_count,
        }
        if node_id in preserved_notes:
            entry["note"] = preserved_notes[node_id]
        tests[node_id] = entry

    generated_by = "python " + " ".join(argv)
    baseline = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "generated_by": generated_by,
        "runs": runs,
        "commit": capture_commit(repo_root),
        "tests": tests,
    }
    return baseline


def format_summary(baseline: dict) -> str:
    """Return a human-readable summary of a baseline dict."""
    tests = baseline.get("tests", {})
    counts: dict[str, int] = {}
    for record in tests.values():
        if isinstance(record, dict):
            cat = record.get("category", "unknown")
            counts[cat] = counts.get(cat, 0) + 1

    lines = [
        f"schema_version : {baseline.get('schema_version')}",
        f"generated_at   : {baseline.get('generated_at')}",
        f"commit         : {baseline.get('commit')}",
        f"runs           : {baseline.get('runs')}",
        f"total failing  : {len(tests)}",
    ]
    for category in (
        CATEGORY_REAL,
        CATEGORY_FLAKY,
        CATEGORY_HUNG,
        CATEGORY_IMPORT_ERROR,
    ):
        lines.append(f"  {category:<14}: {counts.get(category, 0)}")
    return "\n".join(lines)


def estimate_test_count() -> int:
    """Return the approximate number of collected tests, or a safe fallback.

    Used only to size the default ``--global-timeout``; a rough answer is fine.
    Logs a warning when the fallback fires so a user debugging a stalled
    refresh can tell that collection itself failed (e.g. a broken
    ``conftest.py`` import) rather than assuming the ``1500`` value was
    measured.
    """
    try:
        result = subprocess.run(
            ["pytest", "tests/", "--collect-only", "-q"],
            cwd=PROJECT_DIR,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning(
            "[refresh] pytest --collect-only could not run (%s); using "
            "fallback estimate of %d tests for --global-timeout sizing.",
            exc,
            _FALLBACK_TEST_COUNT_ESTIMATE,
        )
        return _FALLBACK_TEST_COUNT_ESTIMATE

    # Pytest's `-q --collect-only` prints lines like "<module>::<test>" and
    # ends with "<N> tests collected".
    for line in reversed(result.stdout.splitlines()):
        line = line.strip()
        if line.endswith(" tests collected") or line.endswith(" test collected"):
            try:
                return int(line.split()[0])
            except (ValueError, IndexError):
                break
    logger.warning(
        "[refresh] pytest --collect-only did not print an 'N tests "
        "collected' summary line (exit=%d); using fallback estimate of %d "
        "tests for --global-timeout sizing. Collection may have errored -- "
        "check the tree manually if this refresh stalls.",
        result.returncode,
        _FALLBACK_TEST_COUNT_ESTIMATE,
    )
    return _FALLBACK_TEST_COUNT_ESTIMATE


def compute_default_global_timeout(test_timeout: int) -> int:
    """Default ``--global-timeout`` = ``test_timeout * 3 * est_test_count``, capped."""
    estimated = estimate_test_count()
    raw = test_timeout * 3 * max(1, estimated)
    return min(raw, DEFAULT_GLOBAL_TIMEOUT_CAP)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh the merge-gate test baseline from N pytest runs.",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of pytest invocations to aggregate across (default: 3).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Path to write the baseline JSON.  In --dry-run mode the default is '-' "
            "(stdout) so dropping --dry-run does not silently overwrite the live "
            "baseline.  Otherwise defaults to data/main_test_baseline.json."
        ),
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=60,
        help="Per-test timeout in seconds, passed to pytest-timeout (default: 60).",
    )
    parser.add_argument(
        "--global-timeout",
        type=int,
        default=0,
        help=(
            "Per-run wall-clock cap in seconds (safety net for C-extension "
            "wedges).  Default computes from test-timeout * 3 * test_count, "
            f"capped at {DEFAULT_GLOBAL_TIMEOUT_CAP}s."
        ),
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Preserve `note` fields from the existing baseline file when writing.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse junitxml and print classification; do not write the baseline file.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log each pytest invocation's command line.",
    )
    parser.add_argument(
        "pytest_args",
        nargs="*",
        help="Additional arguments forwarded to each pytest invocation.",
    )
    return parser.parse_args(argv)


def resolve_output_path(args: argparse.Namespace) -> str:
    """Pick the output path honouring the dry-run-defaults-to-stdout rule."""
    if args.output is not None:
        return args.output
    if args.dry_run:
        return "-"
    return str(DEFAULT_BASELINE_PATH)


def write_baseline(path_or_dash: str, baseline: dict) -> None:
    """Write the baseline to a file, or to stdout if ``path_or_dash`` is ``-``."""
    serialised = json.dumps(baseline, indent=2, sort_keys=True) + "\n"
    if path_or_dash == "-":
        sys.stdout.write(serialised)
        return
    out_path = Path(path_or_dash)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(serialised)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(message)s",
    )

    global_timeout = args.global_timeout or compute_default_global_timeout(
        args.test_timeout
    )
    output_path = resolve_output_path(args)
    invocation_argv = sys.argv[1:] if argv is None else argv

    successful_runs: list[dict[str, str]] = []
    failed_runs = 0
    with tempfile.TemporaryDirectory(prefix="baseline-runs-") as parent_tmp_str:
        parent_tmp = Path(parent_tmp_str)
        for run_index in range(args.runs):
            per_run_tmp = parent_tmp / f"run-{run_index}"
            per_run_tmp.mkdir()
            xml_path = per_run_tmp / "junit.xml"

            completed = run_pytest_once(
                run_index=run_index,
                junitxml_path=xml_path,
                test_timeout=args.test_timeout,
                global_timeout=global_timeout,
                pytest_args=args.pytest_args,
                verbose=args.verbose,
            )
            if not completed:
                failed_runs += 1
                continue

            try:
                outcomes = parse_junitxml(xml_path)
            except JunitxmlParseError as exc:
                logger.warning("[refresh] run %d: %s; discarding run", run_index, exc)
                failed_runs += 1
                continue
            successful_runs.append(outcomes)

    if not successful_runs:
        logger.error(
            "[refresh] all %d runs failed (outer timeout or junitxml ParseError). "
            "Not writing baseline.",
            args.runs,
        )
        return 1

    aggregated = aggregate_outcomes(successful_runs)
    preserved_notes = load_existing_notes(DEFAULT_BASELINE_PATH) if args.merge else {}

    baseline = build_baseline(
        aggregated=aggregated,
        runs=len(successful_runs),
        repo_root=PROJECT_DIR,
        argv=invocation_argv,
        preserved_notes=preserved_notes,
    )

    if args.dry_run:
        write_baseline("-", baseline)
        sys.stderr.write("\n--- summary ---\n")
        sys.stderr.write(format_summary(baseline) + "\n")
        if failed_runs:
            sys.stderr.write(f"discarded runs : {failed_runs}\n")
        return 0

    write_baseline(output_path, baseline)
    sys.stdout.write(f"Wrote {output_path}\n")
    sys.stdout.write(format_summary(baseline) + "\n")
    if failed_runs:
        sys.stdout.write(f"discarded runs : {failed_runs}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
