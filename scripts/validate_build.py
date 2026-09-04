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


_VALUE_FLAGS = ("--repo", "--issue", "--pr", "--timeout")
_BARE_FLAGS = ("--record-outcomes",)


def _parse_argv(argv: list[str]) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """Split argv into ``(options, positionals, rejected_flags, warnings)``.

    A value flag whose next token is missing or is itself a flag is
    **rejected**, not silently satisfied. The documented production invocation
    interpolates unquoted shell variables (``--issue $ISSUE_NUMBER``), so an
    empty variable collapses the argument list and the naive reading takes the
    following flag as the value. That produced three real failures: a
    ``ValueError`` that escaped after the report and changed the exit code, a
    ledger row written under a repo literally named ``--issue``, and a plan
    path silently read from a flag so the run exited 0 having checked nothing.
    A value that merely *looks* like a flag -- any token starting with ``-``,
    not only ``--`` -- is rejected the same way, so ``--issue -5`` is refused
    rather than silently accepted as a negative issue number.

    Positionals are tokens that are neither a flag nor a flag's value, so
    ``--record-outcomes plan.md`` finds the plan rather than mistaking the flag
    for it. Only the first positional is used (the plan path); ``warnings``
    carries a ready-to-print ``ARGS: ...`` line for any positional beyond the
    first, and for a repeated flag (last occurrence wins), so both conditions
    are reported instead of being swallowed silently.
    """
    opts: dict[str, str] = {}
    positionals: list[str] = []
    rejected: list[str] = []
    warnings: list[str] = []

    i = 0
    while i < len(argv):
        token = argv[i]
        if token in _BARE_FLAGS:
            if token in opts:
                warnings.append(f"ARGS: {token} repeated -- using the last occurrence")
            opts[token] = ""
            i += 1
        elif token in _VALUE_FLAGS:
            value = argv[i + 1] if i + 1 < len(argv) else None
            if value is None or value.startswith("-"):
                rejected.append(token)
                i += 1
            else:
                if token in opts:
                    warnings.append(f"ARGS: {token} repeated -- using the last occurrence")
                opts[token] = value
                i += 2
        elif token.startswith("--"):
            rejected.append(token)
            i += 1
        else:
            positionals.append(token)
            i += 1

    if len(positionals) > 1:
        extras = ", ".join(positionals[1:])
        warnings.append(f"ARGS: ignoring extra positional argument(s): {extras}")

    return opts, positionals, rejected, warnings


def _print_usage() -> None:
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
    print("  --timeout N         Per-check bound in seconds (env: VERIFICATION_TIMEOUT_S).")
    print("                      A timeout is UNEVALUATED, which blocks; raise this when a")
    print("                      legitimate suite brushes the ceiling.")
    print()
    print("Exit codes:")
    print("  0 - All checks pass or skip")
    print("  1 - One or more checks failed")


def main() -> int:
    if len(sys.argv) < 2:
        _print_usage()
        return 0

    # --help/-h is honored anywhere in argv, not only as the first token: a
    # documented invocation like `--record-outcomes --help` must still show
    # help rather than being parsed as a (rejected) unknown-flag run.
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        _print_usage()
        return 0

    opts, positionals, bad_flags, arg_warnings = _parse_argv(sys.argv[1:])
    record_outcomes = "--record-outcomes" in opts
    opt_repo = opts.get("--repo")
    opt_issue = opts.get("--issue")
    opt_pr = opts.get("--pr")
    opt_timeout = opts.get("--timeout")

    for flag in bad_flags:
        print(f"ARGS: ignoring {flag} -- unknown flag, or its value was missing or another flag")
    for warning in arg_warnings:
        print(warning)

    if not positionals:
        # Never return 0 having run nothing: a green exit with zero checks is
        # indistinguishable from a clean plan, which is the whole failure this
        # module exists to make impossible.
        print("No plan path given. Usage: python scripts/validate_build.py <plan-path> [options]")
        return 1

    timeout = DEFAULT_TIMEOUT_S
    if opt_timeout:
        try:
            parsed_timeout = int(opt_timeout)
        except ValueError:
            print(f"ARGS: ignoring --timeout {opt_timeout!r} -- not an integer")
        else:
            if parsed_timeout <= 0:
                # A non-positive bound times out every row immediately, and a
                # timeout grades UNEVALUATED -- which now blocks merge
                # permanently once recorded. Fall back rather than let a
                # misconfigured `--timeout 0` (or a negative value) silently
                # turn every check into a durable refusal.
                print(
                    f"ARGS: ignoring --timeout {opt_timeout!r} -- must be positive, "
                    f"using default {DEFAULT_TIMEOUT_S}"
                )
            else:
                timeout = parsed_timeout

    plan_path = Path(positionals[0])
    if not plan_path.is_file():
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
    # Whether the plan declares a check table at all -- a table with rows, a
    # malformed row, or a non-check table it named and stood down on. Shared
    # by the run site below and the record site further down so the two
    # can never drift apart: a plan with none of these has declared no gate,
    # and recording a 0-row UNEVALUATED aggregate for it would block the lane
    # forever on a contract it never made (Risk 8,
    # docs/plans/sdlc-control-plane-asserted-facts.md).
    table_declared = bool(
        verification_table.checks or verification_table.malformed or verification_table.skipped
    )
    if table_declared:
        all_results.extend(
            check_verification_table(verification_table, timeout=timeout, check_results=graded)
        )

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
    if record_outcomes and not table_declared:
        # Absence of a contract is not a failed contract: a plan that names no
        # `## Verification` table has declared no gate, so there is nothing
        # for the merge predicate to enforce. Recording anyway would write a
        # 0-row aggregate that `aggregate_outcomes([], table)` grades
        # UNEVALUATED, and UNEVALUATED blocks merge permanently with no
        # self-heal -- re-reviewing re-records the identical aggregate. This
        # is the one case the writer must never be called for.
        print(
            "RECORD: skipped -- plan declares no verification table, so there is no gate to record"
        )
    elif record_outcomes:
        try:
            issue_int = int(opt_issue) if opt_issue else None
            pr_int = int(opt_pr) if opt_pr else None
        except ValueError:
            # Reported, never raised: an unparseable argument must not escape
            # after the summary has printed and rewrite the exit code.
            issue_int = pr_int = None
            print(
                f"RECORD: skipped -- --issue/--pr must be integers (got {opt_issue!r}/{opt_pr!r})"
            )
        else:
            if not opt_repo or issue_int is None:
                print("RECORD: skipped -- --record-outcomes requires --repo and --issue")
                issue_int = None

        if issue_int is not None and opt_repo:
            wrote = record_verification_outcomes(
                opt_repo,
                issue_int,
                graded,
                table=verification_table,
                pr_number=pr_int,
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
