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
    0 - All checks pass (or are non-blocking skips)
    1 - One or more checks failed, or could not be evaluated
"""

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.verification_parser import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    CheckOutcome,
    CheckResult,
    ParsedTable,
    parse_verification_table,
    record_verification_outcomes,
    run_checks,
)


def extract_section(plan_text: str, heading: str) -> str:
    """Extract content of a markdown section by heading name.

    Returns the text between the heading and the next heading of level 1-3,
    or end of document. Does not distinguish heading levels -- any heading
    at level 1, 2, or 3 terminates the section.
    """
    # Match ## heading or ### heading
    pattern = r"^(#{1,3}) " + re.escape(heading) + r"\s*\n(.*?)(?=^#{1,3} |\Z)"
    match = re.search(pattern, plan_text, re.MULTILINE | re.DOTALL)
    if match:
        return match.group(2)
    return ""


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


def check_verification_table(
    table: ParsedTable,
    *,
    timeout: int = DEFAULT_TIMEOUT_S,
    check_results: list[CheckResult] | None = None,
) -> list[dict]:
    """Run verification table commands and compare output.

    Delegates table definition, expectation grammar, **execution**, bound, and
    timeout disposition to ``agent.verification_parser`` (#2843/#3065) rather
    than carrying its own. This runner keeps only its report shape.

    Execution goes through ``run_checks``, so the two runners cannot drift on
    what a check *did*, only on how it is printed. ``check_results``, when
    given, is extended with the graded :class:`CheckResult` objects so a caller
    can persist the aggregate without running every command a second time.

    The bound is ``DEFAULT_TIMEOUT_S`` and a timeout is ``UNEVALUATED``, both
    shared with ``run_checks``. This module previously carried a private 30s
    ceiling and called a timeout ``SKIP``, so the two runners graded the same
    event two different ways -- and ``SKIP`` did not even block the exit code
    (#2901). ``UNEVALUATED`` blocks: it is not a pass.
    """
    results = []

    for m in table.malformed:
        # Plan-authoring error, not evidence about the code (#2570/#2836).
        # Fails the run -- a row nobody can execute is not a passing check --
        # but says plainly that the plan is what needs fixing.
        results.append(
            {
                "status": "FAIL",
                "message": (
                    f"MALFORMED VERIFICATION ROW (fix the plan, not the code): "
                    f"{m.reason} Row: {m.line}"
                ),
            }
        )

    for s in table.skipped:
        # Non-check table: named, but never counted as PASS/FAIL/SKIP so it
        # cannot change the exit code (#2836).
        results.append(
            {
                "status": "INFO",
                "message": (
                    f"NON-CHECK TABLE SKIPPED: {s.header} ({s.row_count} row(s)) -- {s.reason}"
                ),
            }
        )

    graded = run_checks(table.checks, timeout=timeout)
    if check_results is not None:
        check_results.extend(graded)

    for r in graded:
        name = r.check.name
        if r.outcome is CheckOutcome.PASS:
            results.append({"status": "PASS", "message": name})
        elif r.outcome is CheckOutcome.UNEVALUATED:
            results.append({"status": "UNEVALUATED", "message": f"{name} -- {r.reason}"})
        else:
            results.append(
                {
                    "status": "FAIL",
                    "message": (
                        f"{name} -- expected: {r.check.expected},"
                        f" got exit={r.exit_code}"
                        f" output={r.output[:100]}"
                    ),
                }
            )

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
        print("Usage: python scripts/validate_build.py <plan-path> [options]")
        print()
        print("Validates a build against the plan specification.")
        print("Checks file path assertions, verification table commands,")
        print("and success criteria commands.")
        print()
        print("Options:")
        print("  --record-outcomes   Persist the graded aggregate to the lane's ledger,")
        print("                      where the merge predicate reads it. Requires")
        print("                      --repo and --issue; pass --pr so the record is")
        print("                      stamped with the head SHA it was graded against.")
        print("                      An unstamped record is refused at merge, so record")
        print("                      at REVIEW/DOCS time, once the lane has a PR.")
        print("  --repo OWNER/NAME   Target repo for the ledger key.")
        print("  --issue N           Issue number for the ledger key.")
        print("  --pr N              PR whose head SHA anchors the record.")
        print()
        print("Exit codes:")
        print("  0 - All checks pass or skip")
        print("  1 - One or more checks failed")
        return 0

    argv = sys.argv[1:]

    def _opt(flag: str) -> str | None:
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                return argv[i + 1]
        return None

    record_outcomes = "--record-outcomes" in argv
    opt_repo = _opt("--repo")
    opt_issue = _opt("--issue")
    opt_pr = _opt("--pr")

    plan_path = Path(argv[0])
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
    verification_table = parse_verification_table(plan_text)
    graded: list[CheckResult] = []
    if verification_table.checks or verification_table.malformed or verification_table.skipped:
        all_results.extend(check_verification_table(verification_table, check_results=graded))

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
    unevaluated_count = sum(1 for r in all_results if r["status"] == "UNEVALUATED")

    print(
        f"\nResult: {pass_count} PASS, {fail_count} FAIL, "
        f"{unevaluated_count} UNEVALUATED, {skip_count} SKIP"
    )

    # Persist last, and never let a ledger failure change what the human is
    # told: the write reports its own success or failure on its own line and
    # does not touch the exit code, which belongs to the checks.
    if record_outcomes:
        if not opt_repo or not opt_issue:
            print("RECORD: skipped -- --record-outcomes requires --repo and --issue")
        else:
            wrote = record_verification_outcomes(
                opt_repo,
                int(opt_issue),
                graded,
                table=verification_table,
                pr_number=int(opt_pr) if opt_pr else None,
            )
            if wrote:
                anchor = f"anchored to PR #{opt_pr} head" if opt_pr else "UNANCHORED"
                print(
                    f"RECORD: verification outcomes written for {opt_repo}#{opt_issue} ({anchor})"
                )
                if not opt_pr:
                    print(
                        "RECORD: no --pr given, so no head SHA was stamped. "
                        "The merge predicate refuses an unanchored record."
                    )
            else:
                print(f"RECORD: FAILED to write verification outcomes for {opt_repo}#{opt_issue}")

    # UNEVALUATED blocks. It is not a pass, and it is not a FAIL either: the
    # exit code says "stop", the report says the grader could not answer.
    return 1 if (fail_count or unevaluated_count) else 0


if __name__ == "__main__":
    sys.exit(main())
