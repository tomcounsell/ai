#!/usr/bin/env python3
"""Validate plan prerequisites before build execution.

Reads a plan markdown file, parses the ## Prerequisites section for a
markdown table, extracts check commands, and runs each one. Reports
pass/fail for each requirement.

Usage:
    python scripts/check_prerequisites.py docs/plans/my-feature.md

Exit codes:
    0 - All checks passed (or no Prerequisites section found)
    1 - One or more checks failed
"""

import re
import subprocess
import sys
from pathlib import Path


def extract_prerequisites(plan_text: str) -> list[dict[str, str]]:
    """Extract prerequisite rows from the ## Prerequisites markdown table.

    Returns a list of dicts with keys: requirement, check_command, purpose.
    Returns empty list if no Prerequisites section or table found.
    """
    # Find the ## Prerequisites section
    section_match = re.search(
        r"^## Prerequisites\s*\n(.*?)(?=^## |\Z)",
        plan_text,
        re.MULTILINE | re.DOTALL,
    )
    if not section_match:
        return []

    section = section_match.group(1)

    # Find the table whose header contains "Check Command" (not status/info tables)
    rows = []
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # Look for a table header row containing "Check Command"
        if stripped.startswith("|") and "check command" in stripped.lower():
            # Skip the separator row
            i += 1
            if i < len(lines) and re.match(r"^\s*\|[\s\-:]+\|", lines[i]):
                i += 1
            # Parse data rows
            while i < len(lines):
                row = lines[i].strip()
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip("|").split("|")]
                if len(cells) >= 2:
                    cmd_cell = cells[1]
                    cmd_match = re.search(r"`(.+?)`", cmd_cell)
                    command = cmd_match.group(1) if cmd_match else cmd_cell
                    rows.append(
                        {
                            "requirement": cells[0].strip("`").strip(),
                            "check_command": command,
                            "purpose": cells[2].strip() if len(cells) >= 3 else "",
                        }
                    )
                i += 1
            break
        i += 1

    return rows


def run_checks(prerequisites: list[dict[str, str]]) -> tuple[bool, list[str]]:
    """Run each prerequisite check command.

    Returns (all_passed, report_lines).
    """
    all_passed = True
    report = []

    for prereq in prerequisites:
        req = prereq["requirement"]
        cmd = prereq["check_command"]

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                report.append(f"  PASS: {req}")
            else:
                all_passed = False
                error = (
                    result.stderr.strip() or result.stdout.strip() or "non-zero exit"
                )
                report.append(f"  FAIL: {req} -- {error}")
        except subprocess.TimeoutExpired:
            all_passed = False
            report.append(f"  FAIL: {req} -- timed out after 30s")
        except Exception as e:
            all_passed = False
            report.append(f"  FAIL: {req} -- {e}")

    return all_passed, report


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python scripts/check_prerequisites.py <plan-path>")
        print("Validates prerequisites defined in a plan's ## Prerequisites table.")
        return 1

    plan_path = Path(sys.argv[1])
    if not plan_path.exists():
        print(f"Error: Plan file not found: {plan_path}")
        return 1

    plan_text = plan_path.read_text()
    prerequisites = extract_prerequisites(plan_text)

    if not prerequisites:
        print(f"No Prerequisites section found in {plan_path}. Skipping checks.")
        return 0

    print(f"Checking {len(prerequisites)} prerequisite(s) from {plan_path}:")
    all_passed, report = run_checks(prerequisites)

    for line in report:
        print(line)

    if all_passed:
        print(f"\nAll {len(prerequisites)} prerequisite(s) passed.")
        return 0
    else:
        print("\nSome prerequisites failed. Fix issues before building.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
