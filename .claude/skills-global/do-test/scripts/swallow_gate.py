#!/usr/bin/env python3
"""Exception Swallow Gate: fail when the diff adds an unguarded `except Exception`.

Scans lines added to `*.py` files between the diff base and HEAD (base: `main`,
or `HEAD~1` when on `main`). Each added line matching `except ... Exception`
passes only if either:

  1. one of the next 3 added lines contains logger, log., warning, error, or raise; or
  2. the except line carries `# swallow-ok: <reason>` with a reason of 10+
     non-whitespace characters.

Prints `EXCEPTION_SWALLOW_GATE: PASS` (exit 0) or `EXCEPTION_SWALLOW_GATE: FAIL`
followed by the offending lines (exit 1). Exit 2 means the diff could not be read.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

EXCEPT_RE = re.compile(r"except.*Exception")
HANDLED_RE = re.compile(r"logger|log\.|warning|error|raise")
SWALLOW_OK_RE = re.compile(r"#\s*swallow-ok:(.*)$")
BODY_WINDOW = 3


def default_base() -> str:
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True
    ).stdout.strip()
    return "HEAD~1" if branch == "main" else "main"


def swallow_ok(line: str) -> bool:
    m = SWALLOW_OK_RE.search(line)
    return bool(m) and len("".join(m.group(1).split())) >= 10


def find_violations(added: list[str]) -> list[str]:
    violations = []
    for i, line in enumerate(added):
        if not EXCEPT_RE.search(line) or swallow_ok(line):
            continue
        if any(HANDLED_RE.search(body) for body in added[i + 1 : i + 1 + BODY_WINDOW]):
            continue
        violations.append(line)
    return violations


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--base", help="diff base (default: main, or HEAD~1 on main)")
    args = ap.parse_args()
    base = args.base or default_base()

    diff = subprocess.run(
        ["git", "diff", f"{base}...HEAD", "--", "*.py"], capture_output=True, text=True
    )
    if diff.returncode != 0:
        err = diff.stderr.strip()
        print(f"EXCEPTION_SWALLOW_GATE: ERROR (git diff {base}...HEAD failed: {err})")
        return 2
    added = [
        ln[1:] for ln in diff.stdout.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    ]
    violations = find_violations(added)
    if not violations:
        print("EXCEPTION_SWALLOW_GATE: PASS")
        return 0
    print("EXCEPTION_SWALLOW_GATE: FAIL (new unguarded except Exception block(s)):")
    for line in violations:
        print(f"  {line.strip()}")
    print(
        "Each must log or re-raise within its next 3 lines, or carry "
        "`# swallow-ok: <reason of 10+ chars>` on the except line."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
