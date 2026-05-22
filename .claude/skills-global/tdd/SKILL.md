---
name: tdd
description: "Use when building with test-driven development discipline. Triggered by 'tdd this', 'write tests first', 'red-green-refactor', 'test-driven', or any request to implement a feature using the TDD cycle."
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Skill: /tdd

## Purpose
Scaffold a test-driven development cycle: write a failing test first, implement the minimal code to pass it, then refactor — with an explicit gate that the test is red before any implementation is written.

## When to Use
- Implementing a new function, class, or module from scratch
- Fixing a bug where a regression test should prevent recurrence
- The user says "tdd this", "write tests first", or "red-green-refactor"
- Any feature work where correctness is more important than speed of first draft

## Steps

1. **Understand the behavior to implement.** Read any referenced plan, issue, or existing tests. Clarify the acceptance criterion in one sentence: "Given X, when Y, then Z."

2. **Write the failing test (RED).** Create the test file before writing any implementation:

   Example — adding a `parse_duration` function:
   ```python
   # tests/unit/test_parse_duration.py
   import pytest
   from tools.time_utils import parse_duration

   def test_parse_duration_minutes():
       assert parse_duration("5m") == 300

   def test_parse_duration_hours():
       assert parse_duration("2h") == 7200

   def test_parse_duration_invalid_raises():
       with pytest.raises(ValueError, match="invalid duration"):
           parse_duration("banana")
   ```

3. **Run the test and confirm it FAILS.** This is a hard gate — do not write implementation until you see a red failure:
   ```bash
   pytest tests/unit/test_parse_duration.py -v
   ```
   Expected: `FAILED` or `ImportError`. If the test passes without implementation, the test is wrong — rewrite it.

4. **Write the minimal implementation (GREEN).** Write the smallest code that makes the failing test pass. Nothing more:
   ```python
   # tools/time_utils.py
   import re

   def parse_duration(value: str) -> int:
       match = re.fullmatch(r"(\d+)(m|h)", value)
       if not match:
           raise ValueError(f"invalid duration: {value!r}")
       n, unit = int(match.group(1)), match.group(2)
       return n * 60 if unit == "m" else n * 3600
   ```

5. **Run tests again and confirm GREEN:**
   ```bash
   pytest tests/unit/test_parse_duration.py -v
   ```
   Expected: all tests pass. If not, fix the implementation (not the tests).

6. **Refactor (REFACTOR).** With tests green, improve design:
   - Rename unclear variables
   - Extract helper functions
   - Remove duplication
   - Run tests after every change to confirm nothing broke

7. **Check test hygiene.** Before marking done:
   - Consolidate any overlapping tests with `@pytest.mark.parametrize`
   - Delete any tests that test internal implementation rather than behavior
   - Confirm the test names describe behavior, not code paths

8. **Run linting and formatting:**
   ```bash
   python -m ruff check . && python -m ruff format --check .
   ```

## Output
A passing test suite with minimal implementation and clean, refactored code. Test names describe behavior. No red tests remain.

## Anti-Patterns
- Never write implementation before confirming the test is red — that is the cardinal rule of TDD.
- Do not write tests that test implementation details (internal variables, private methods) — test behavior.
- Do not skip the REFACTOR phase — green tests without refactoring accumulate debt.
- Do not write 20 tests for one function upfront — write one test, make it pass, then add the next.
- Do not use /tdd for documentation-only changes, config files, or prompt files — TDD applies to logic code.
- Do not mark a task complete if any test is red or linting fails.
