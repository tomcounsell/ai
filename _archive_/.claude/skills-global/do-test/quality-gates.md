# Quality Checks and Exception Swallow Gate

Loaded after tests pass, before OUTCOME emission. The quality scans are
advisory; the Exception Swallow Gate is mandatory and can block the TEST stage.
The examples below are Python-flavored — apply the same patterns to the
project's language (swap `*.py` globs and `except Exception` for the
language's equivalents).

## Quality Checks (Post-Test)

After tests pass, run these additional quality scans and include results in the report:

### Exception Swallow Scan

Scan for silently-swallowed exceptions (e.g. Python's `except Exception: pass`)
that lack test coverage. Target the repo's primary source directories (the
context file may name them; the generic default is every tracked source
directory, e.g. `git ls-files '*.py'`):

```bash
grep -rn "except.*Exception.*:" --include="*.py" <source-dirs> | grep -v "logger\|log\.\|warning\|error\|raise\|# .*tested" | head -20
```

Report any bare exception handlers found. Each should either:
1. Have a corresponding test asserting observable behavior (logger.warning, state change)
2. Be documented with a comment explaining why bare `pass` is acceptable (e.g., cleanup during shutdown)

### Empty Input Check

If the test suite covers agent output processing code, verify that empty/None/whitespace inputs are tested:

```bash
grep -rn "def test.*empty\|def test.*none\|def test.*whitespace" tests/ --include="*.py" | wc -l
```

Flag if the changed files include output processing code but the test suite has zero empty input tests.

### Closure Coverage Flag

If any changed files contain inner functions or closures (functions defined inside other functions), flag whether those closures have dedicated test coverage:

```bash
grep -rn "def .*(" --include="*.py" <source-dirs> | grep "^.*:.*def .*:$" | head -10
```

Closures that replicate logic already tested elsewhere (e.g., inline routing logic that should call a shared function) are a test smell. Note them in the report.

### Stale xfail Hygiene Scan

After tests pass, scan for expected-failure markers whose underlying bug is now
fixed (in pytest: xfail-marked tests that now pass, "xpass"). When a bug fix
lands, the corresponding expected-failure marker should be removed and converted
to a hard assertion. Stale markers indicate the fix landed but the test wasn't updated.

**Two forms of xfail exist and require different detection:**

1. **Decorator form** (`@pytest.mark.xfail`): Pytest reports these as `XPASS` in test output when the test unexpectedly passes. Check the pytest output for `XPASS` entries.

2. **Runtime form** (`pytest.xfail("reason")` called inside the test body): These are **invisible to XPASS detection** because the call short-circuits the test before it reaches the assertion. A test with a runtime `pytest.xfail()` will show as `xfail` even when the underlying bug is fixed — it never gets a chance to pass. **This is the more dangerous form** because it silently hides regressions.

```bash
# Find ALL xfail markers (both decorator and runtime forms)
grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py" | head -20
```

**For decorator xfails:** Check if pytest reports `XPASS` in the test output.

**For runtime xfails:** These ALWAYS require manual review. Flag every `pytest.xfail(` call found in test bodies:
1. If the call is guarded by a condition (e.g., `if broken: pytest.xfail(...)`), check whether the condition is still true
2. If the call is unconditional, flag it as "runtime xfail — cannot detect if bug is fixed, must be reviewed"

For each stale xfail detected (either form):
1. Flag it prominently in the quality report: "STALE XFAIL: tests/foo/test_bar.py:LINE — [decorator|runtime] form"
2. Include the file and line number for easy removal
3. Suggest: "This test should have its xfail marker removed and converted to a hard assertion"

**Important:** Runtime `pytest.xfail()` is a stronger smell than decorator `@pytest.mark.xfail`. If `--changed` mode is active and the changed files include a bug fix, runtime xfails in related test files should be flagged as **blockers**, not just warnings.

**Skip if:** No xfail markers found in the test suite.

## Exception Swallow Gate

Before emitting the OUTCOME, run a mandatory Exception Swallow Gate on the diff. This gate blocks the TEST stage if new unguarded `except Exception` blocks are introduced.

**When to run:** Always — after tests pass, before OUTCOME emission. Scan the diff (not the full codebase) for new `except Exception` blocks only.

**Gate logic:**

```bash
# Get the diff of new/changed Python lines that add except Exception blocks
DIFF_BASE=$(git rev-parse --abbrev-ref HEAD | grep -q "^main$" && echo "HEAD~1" || echo "main")
DIFF_CONTENT=$(git diff "$DIFF_BASE"...HEAD -- '*.py' | grep '^+' | grep -v '^+++')

# Find line numbers (within the diff output) of new except Exception clauses
EXCEPT_LINE_NUMS=$(echo "$DIFF_CONTENT" | grep -n 'except.*Exception' | cut -d: -f1)

if [ -z "$EXCEPT_LINE_NUMS" ]; then
    echo "EXCEPTION_SWALLOW_GATE: PASS (no new except Exception blocks)"
else
    # For each new except Exception line, check the clause line AND the next 3 handler-body lines
    FAILURES=""
    TOTAL_LINES=$(echo "$DIFF_CONTENT" | wc -l)
    while IFS= read -r lineno; do
        # Extract the except clause line itself
        clause_line=$(echo "$DIFF_CONTENT" | sed -n "${lineno}p")
        # Extract up to 3 handler-body lines following the except clause
        end_line=$((lineno + 3))
        [ $end_line -gt $TOTAL_LINES ] && end_line=$TOTAL_LINES
        body_lines=$(echo "$DIFF_CONTENT" | sed -n "$((lineno+1)),${end_line}p")
        # Pass if the except clause has a valid swallow-ok comment (reason must be 10+ non-whitespace chars)
        if echo "$clause_line" | grep -qE "# swallow-ok: .{10,}"; then
            continue
        fi
        # Pass if the handler body contains logger, log., warning, error, or raise
        if echo "$body_lines" | grep -qE "logger|log\.|warning|error|raise"; then
            continue
        fi
        FAILURES="$FAILURES
$clause_line"
    done <<< "$EXCEPT_LINE_NUMS"

    if [ -z "$FAILURES" ]; then
        echo "EXCEPTION_SWALLOW_GATE: PASS"
    else
        echo "EXCEPTION_SWALLOW_GATE: FAIL — new unguarded except Exception block(s):"
        echo -e "$FAILURES"
        echo ""
        echo "Each new except Exception block must either:"
        echo "  1. Contain logger, log., warning, error, or raise in the handler body (next 3 lines)"
        echo "  2. Have an inline comment on the except line: # swallow-ok: {reason with 10+ chars}"
        echo "     Example: # swallow-ok: safe during shutdown, task already cancelled"
        echo "     Invalid: # swallow-ok: x   (reason too short)"
        echo "     Invalid: # swallow-ok:      (empty reason)"
        echo "GATE_FAILED"
    fi
fi
```

**If gate fails:** Emit `<!-- OUTCOME {"status":"fail","stage":"TEST","artifacts":{"swallow_gate":"failed","new_swallows":[...]}} -->` and stop. Do NOT emit a success OUTCOME.

**Carve-out convention:** To exempt a legitimate exception swallow, add an inline comment on the same line as the `except` clause:
```python
except Exception:  # swallow-ok: safe during shutdown, task already cancelled
    pass
```
The reason must be at least 10 non-whitespace characters. Bare `# swallow-ok:` or whitespace-only reasons do NOT pass.
