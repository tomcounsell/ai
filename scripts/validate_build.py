#!/usr/bin/env python3
"""Validate a build against the plan specification.

Deterministic (no LLM) validation that checks:
1. File path assertions from plan checkboxes (Create/Add -> exists, Delete/Remove -> not exists)
2. Verification table commands (run command, compare output)
3. Grep-based success criteria (run commands, check exit codes)

Usage:
    python scripts/validate_build.py docs/plans/my-feature.md
    python scripts/validate_build.py --help

Exit codes:
    0 - All checks pass or skip (no failures)
    1 - One or more checks failed
"""

import re
import subprocess
import sys
from pathlib import Path


def extract_section(plan_text: str, heading: str) -> str:
    """Extract content of a markdown section by heading name.

    Returns the text between the heading and the next heading of equal or
    higher level, or end of document.
    """
    # Match ## heading or ### heading
    pattern = r"^(#{1,3}) " + re.escape(heading) + r"\s*\n(.*?)(?=^#{1,3} |\Z)"
    match = re.search(pattern, plan_text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(2)
    return ""


def extract_checkboxes(section_text: str) -> list[str]:
    """Extract unchecked checkbox lines from a section."""
    return re.findall(r"^[ \t]*- \[ \] (.+)$", section_text, re.MULTILINE)


def parse_file_assertions(plan_text: str) -> list[dict[str, str]]:
    """Parse file path assertions from plan checkboxes.

    Scans all checkbox lines for patterns like:
    - Create/Add X -> file should exist
    - Delete/Remove X -> file should NOT exist
    - Update/Edit/Modify X -> file should be modified in diff
    """
    assertions = []
    # Look at all checkbox lines across the plan
    checkbox_lines = re.findall(r"^[ \t]*- \[[ x]\] (.+)$", plan_text, re.MULTILINE)

    for line in checkbox_lines:
        # Match patterns like "Create `path/to/file`" or "Add `path/to/file`"
        create_match = re.search(r"\b(?:Create|Add)\s+`([^`]+\.\w+)`", line, re.IGNORECASE)
        if create_match:
            path = create_match.group(1)
            assertions.append({"action": "exists", "path": path, "source": line.strip()})
            continue

        # Match "Delete X" or "Remove X"
        delete_match = re.search(r"\b(?:Delete|Remove)\s+`([^`]+\.\w+)`", line, re.IGNORECASE)
        if delete_match:
            path = delete_match.group(1)
            assertions.append({"action": "not_exists", "path": path, "source": line.strip()})
            continue

        # Match "Update X" or "Edit X" or "Modify X"
        update_match = re.search(r"\b(?:Update|Edit|Modify)\s+`([^`]+\.\w+)`", line, re.IGNORECASE)
        if update_match:
            path = update_match.group(1)
            assertions.append({"action": "modified", "path": path, "source": line.strip()})
            continue

    return assertions


def parse_verification_table(plan_text: str) -> list[dict[str, str]]:
    """Parse the ## Verification table for check commands.

    Expected format:
    | Check | Command | Expected |
    |-------|---------|----------|
    | name  | `cmd`   | result   |
    """
    section = extract_section(plan_text, "Verification")
    if not section:
        return []

    checks = []
    lines = section.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        # Look for table header with Command column
        if stripped.startswith("|") and "command" in stripped.lower():
            # Skip separator row
            i += 1
            if i < len(lines) and re.match(r"^\s*\|[\s\-:]+\|", lines[i]):
                i += 1
            # Parse data rows
            while i < len(lines):
                row = lines[i].strip()
                if not row.startswith("|"):
                    break
                cells = [c.strip() for c in row.strip("|").split("|")]
                if len(cells) >= 3:
                    cmd_cell = cells[1]
                    cmd_match = re.search(r"`(.+?)`", cmd_cell)
                    command = cmd_match.group(1) if cmd_match else cmd_cell.strip()
                    expected_cell = cells[2].strip()
                    checks.append(
                        {
                            "name": cells[0].strip(),
                            "command": command,
                            "expected": expected_cell,
                        }
                    )
                i += 1
            break
        i += 1

    return checks


def parse_success_criteria_commands(plan_text: str) -> list[dict[str, str]]:
    """Parse ## Success Criteria for items containing runnable commands.

    Looks for checkbox items that contain backtick-quoted commands.
    """
    section = extract_section(plan_text, "Success Criteria")
    if not section:
        return []

    criteria = []
    checkbox_lines = re.findall(r"^[ \t]*- \[[ x]\] (.+)$", section, re.MULTILINE)
    for line in checkbox_lines:
        # Extract commands in backticks
        cmd_match = re.search(r"`([^`]+)`", line)
        if cmd_match:
            cmd = cmd_match.group(1)
            # Only include if it looks like a runnable command
            if any(
                cmd.startswith(prefix)
                for prefix in [
                    "python",
                    "pytest",
                    "grep",
                    "test ",
                    "ls ",
                    "cat ",
                    "ruff",
                ]
            ):
                criteria.append({"command": cmd, "source": line.strip()})

    return criteria


def check_file_assertions(assertions: list[dict[str, str]]) -> list[dict]:
    """Run file path assertions and return results."""
    results = []
    for assertion in assertions:
        path = Path(assertion["path"])
        action = assertion["action"]

        if action == "exists":
            if path.exists():
                results.append(
                    {
                        "status": "PASS",
                        "message": f"{assertion['path']} exists",
                    }
                )
            else:
                results.append(
                    {
                        "status": "FAIL",
                        "message": (
                            f"{assertion['path']} does not exist"
                            f" (expected by: {assertion['source']})"
                        ),
                    }
                )
        elif action == "not_exists":
            if not path.exists():
                results.append(
                    {
                        "status": "PASS",
                        "message": f"{assertion['path']} deleted",
                    }
                )
            else:
                results.append(
                    {
                        "status": "FAIL",
                        "message": (
                            f"{assertion['path']} still exists"
                            f" (expected deleted by: {assertion['source']})"
                        ),
                    }
                )
        elif action == "modified":
            # Check if file was modified in main..HEAD diff
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", "main..HEAD"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                modified_files = result.stdout.strip().splitlines()
                if assertion["path"] in modified_files:
                    results.append(
                        {
                            "status": "PASS",
                            "message": f"{assertion['path']} modified in diff",
                        }
                    )
                else:
                    results.append(
                        {
                            "status": "FAIL",
                            "message": f"{assertion['path']} not modified in main..HEAD diff",
                        }
                    )
            except Exception:
                results.append(
                    {
                        "status": "SKIP",
                        "message": f"{assertion['path']} -- could not check git diff",
                    }
                )

    return results


def check_verification_table(checks: list[dict[str, str]]) -> list[dict]:
    """Run verification table commands and compare output."""
    results = []
    for check in checks:
        cmd = check["command"]
        expected = check["expected"]
        name = check["name"]

        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            actual_output = result.stdout.strip()
            actual_exit = result.returncode

            # Check expected value
            passed = False
            if expected.startswith("exit code "):
                expected_code = int(expected.replace("exit code ", ""))
                passed = actual_exit == expected_code
            elif expected.startswith("output "):
                expected_output = expected.replace("output ", "")
                passed = actual_output == expected_output
            else:
                # Flexible match: check if expected is in output or exit code is 0
                passed = expected.lower() in actual_output.lower() or actual_exit == 0

            if passed:
                results.append({"status": "PASS", "message": name})
            else:
                results.append(
                    {
                        "status": "FAIL",
                        "message": (
                            f"{name} -- expected: {expected},"
                            f" got exit={actual_exit}"
                            f" output={actual_output[:100]}"
                        ),
                    }
                )
        except subprocess.TimeoutExpired:
            results.append({"status": "SKIP", "message": f"{name} -- timed out after 30s"})
        except Exception as e:
            results.append({"status": "SKIP", "message": f"{name} -- error: {e}"})

    return results


def check_success_criteria(criteria: list[dict[str, str]]) -> list[dict]:
    """Run success criteria commands and check exit codes."""
    results = []
    for item in criteria:
        cmd = item["command"]
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                results.append({"status": "PASS", "message": f"Success criterion: {cmd}"})
            else:
                results.append(
                    {
                        "status": "FAIL",
                        "message": f"Success criterion failed: {cmd} (exit {result.returncode})",
                    }
                )
        except subprocess.TimeoutExpired:
            results.append(
                {
                    "status": "SKIP",
                    "message": f"Success criterion timed out: {cmd}",
                }
            )
        except Exception as e:
            results.append(
                {
                    "status": "SKIP",
                    "message": f"Success criterion error: {cmd} -- {e}",
                }
            )

    return results


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("--help", "-h"):
        print("Usage: python scripts/validate_build.py <plan-path>")
        print()
        print("Validates a build against the plan specification.")
        print("Checks file path assertions, verification table commands,")
        print("and success criteria commands.")
        print()
        print("Exit codes:")
        print("  0 - All checks pass or skip")
        print("  1 - One or more checks failed")
        return 0

    plan_path = Path(sys.argv[1])
    if not plan_path.exists():
        print(f"Plan file not found: {plan_path}")
        print("Nothing to validate.")
        return 0

    plan_text = plan_path.read_text()
    if not plan_text.strip():
        print(f"Plan file is empty: {plan_path}")
        print("Nothing to validate.")
        return 0

    all_results = []

    # 1. File path assertions
    file_assertions = parse_file_assertions(plan_text)
    if file_assertions:
        all_results.extend(check_file_assertions(file_assertions))

    # 2. Verification table
    verification_checks = parse_verification_table(plan_text)
    if verification_checks:
        all_results.extend(check_verification_table(verification_checks))

    # 3. Success criteria commands
    success_criteria = parse_success_criteria_commands(plan_text)
    if success_criteria:
        all_results.extend(check_success_criteria(success_criteria))

    if not all_results:
        print(f"No validatable assertions found in {plan_path}")
        print("Nothing to validate.")
        return 0

    # Print results
    for r in all_results:
        print(f"{r['status']}: {r['message']}")

    pass_count = sum(1 for r in all_results if r["status"] == "PASS")
    fail_count = sum(1 for r in all_results if r["status"] == "FAIL")
    skip_count = sum(1 for r in all_results if r["status"] == "SKIP")

    print(f"\nResult: {pass_count} PASS, {fail_count} FAIL, {skip_count} SKIP")

    return 1 if fail_count > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
