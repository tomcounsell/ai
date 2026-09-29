#!/usr/bin/env python3
"""Classify failing pytest node IDs by re-running them on a base ref.

Creates a throwaway detached worktree at the base ref (default ``main``) under a
unique temp dir, runs only the given test IDs there with ``--junitxml``, and
buckets each input ID deterministically:

    PASSED on base          -> regressions   (the branch broke it)
    FAILED on base          -> pre_existing  (already broken on base)
    ERROR / SKIPPED / absent -> inconclusive

Every input ID lands in exactly one bucket. The worktree is always removed.
Prints one JSON object to stdout and exits 0; setup failures still produce JSON
with every ID marked inconclusive and the reason in ``raw_output``.

Run it with the project's own interpreter (e.g. ``.venv/bin/python``): the
default runner is ``<this interpreter> -m pytest``, so the base worktree needs
no venv of its own.
"""

from __future__ import annotations

import argparse
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

RAW_OUTPUT_LIMIT = 20000


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def junit_key(node_id: str) -> tuple[str, str]:
    """Map a pytest node ID to junitxml's (classname, name).

    ``tests/unit/test_foo.py::TestBar::test_baz[x]`` ->
    ``("tests.unit.test_foo.TestBar", "test_baz[x]")``.
    """
    parts = node_id.split("::")
    module = parts[0].removesuffix(".py").replace("/", ".")
    classname = ".".join([module, *parts[1:-1]])
    return classname, parts[-1]


def parse_junit(path: Path) -> dict[tuple[str, str], str]:
    results: dict[tuple[str, str], str] = {}
    for tc in ET.parse(path).getroot().iter("testcase"):
        if tc.find("failure") is not None:
            status = "FAILED"
        elif tc.find("error") is not None:
            status = "ERROR"
        elif tc.find("skipped") is not None:
            status = "SKIPPED"
        else:
            status = "PASSED"
        results[(tc.get("classname", ""), tc.get("name", ""))] = status
    return results


def classify(ids: list[str], results: dict[tuple[str, str], str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {"regressions": [], "pre_existing": [], "inconclusive": []}
    for node_id in ids:
        status = results.get(junit_key(node_id))
        if status == "PASSED":
            buckets["regressions"].append(node_id)
        elif status == "FAILED":
            buckets["pre_existing"].append(node_id)
        else:
            buckets["inconclusive"].append(node_id)
    return buckets


def report(commit: str | None, ids: list[str], raw: str, buckets: dict | None = None) -> None:
    out = {"baseline_commit": commit}
    out.update(buckets or {"regressions": [], "pre_existing": [], "inconclusive": list(ids)})
    out["raw_output"] = raw[-RAW_OUTPUT_LIMIT:]
    print(json.dumps(out, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("test_ids", nargs="*", help="failing pytest node IDs")
    ap.add_argument("--base", default="main", help="ref to verify against (default: main)")
    ap.add_argument(
        "--runner",
        default=f"{shlex.quote(sys.executable)} -m pytest",
        help="test command; IDs and --junitxml are appended (default: this interpreter -m pytest)",
    )
    ap.add_argument("--timeout", type=int, default=300, help="seconds before the run is abandoned")
    ap.add_argument(
        "--copy",
        action="append",
        default=[],
        metavar="SRC[:DEST]",
        help=(
            "untracked file to copy into the base worktree "
            "(DEST relative to it; default basename)"
        ),
    )
    args = ap.parse_args()
    ids = list(dict.fromkeys(args.test_ids))

    if not ids:
        report(None, [], "No failing tests provided; skipping baseline verification.")
        return 0

    rev = _git("rev-parse", "--verify", f"{args.base}^{{commit}}")
    if rev.returncode != 0:
        report(None, ids, f"Cannot resolve base ref {args.base!r}: {rev.stderr.strip()}")
        return 0
    commit = rev.stdout.strip()

    _git("worktree", "prune")
    tmp = Path(tempfile.mkdtemp(prefix="baseline-verify-"))
    wt = tmp / "wt"
    junit = tmp / "results.xml"
    try:
        add = _git("worktree", "add", "--detach", str(wt), commit)
        if add.returncode != 0:
            report(None, ids, f"Failed to create baseline worktree: {add.stderr.strip()}")
            return 0

        for spec in args.copy:
            src, _, dest = spec.partition(":")
            src_path = Path(src).expanduser()
            if src_path.is_file():
                target = wt / (dest or src_path.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_path, target)

        runner = shlex.split(args.runner)
        # The run's cwd is the base worktree, so a relative program path such as
        # `.venv/bin/python` must resolve against the invoking directory instead.
        if "/" in runner[0] and not Path(runner[0]).is_absolute():
            runner[0] = str(Path(runner[0]).absolute())
        cmd = [*runner, *ids, "-v", "--tb=short", f"--junitxml={junit}"]
        try:
            run = subprocess.run(
                cmd, cwd=wt, capture_output=True, text=True, timeout=args.timeout
            )
        except subprocess.TimeoutExpired as exc:
            partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            report(commit, ids, f"TIMEOUT after {args.timeout}s\n{partial}")
            return 0
        except FileNotFoundError as exc:
            report(commit, ids, f"Runner not found: {exc}")
            return 0

        raw = f"$ {shlex.join(cmd)}\nexit={run.returncode}\n{run.stdout}{run.stderr}"
        try:
            results = parse_junit(junit)
        except (FileNotFoundError, ET.ParseError) as exc:
            report(commit, ids, f"junitxml parse failure: {exc}\n{raw}")
            return 0
        report(commit, ids, raw, classify(ids, results))
        return 0
    finally:
        _git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(tmp, ignore_errors=True)
        _git("worktree", "prune")


if __name__ == "__main__":
    sys.exit(main())
