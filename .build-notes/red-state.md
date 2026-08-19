# Red-state evidence (T1, T3, T9)

Captured before any production code changed, against `main` @ `b901b87e6^`
(the commit immediately preceding this lane's first code commit). Read back
by T19 when writing the PR body.

## T1 — pre-fix parser and evaluator behavior

`parse_verification_table` (`agent/verification_parser.py`, pre-fix) on the
`## Verification` section from `61717ccb2^:docs/plans/docs-auditor-rename-detection.md`
(committed verbatim as `tests/fixtures/verification/2741_pre_fix_verification.md`):

```
OLD PARSER on #2741 fixture: checks=27 malformed=0
```

27 checks: 16 real + 11 junk rows from the section's second table (a
`| Row | Pre-change | Meaning |` summary table whose header, separator, and 9
data rows all parsed as guaranteed-fail checks). Confirms #2836's core claim.

`scripts/validate_build.py::check_verification_table` (pre-fix, its own local
four-branch evaluator):

```
output > 0 / echo 1        -> FAIL   (should PASS -- #2843)
match count == 0 / clean   -> FAIL   (should PASS -- #2843, grep -c prints "0", exits 1)
match count == 0 / violated -> PASS  (should FAIL -- #2783 Severity-1, grep -c prints "2", exits 0,
                                       falls through to `actual_exit == 0`)
```

`agent/verification_parser.py::evaluate_expectation` (pre-fix, unaffected --
#2843/#2783 are `validate_build.py`'s own separate, weaker evaluator, not this
one):

```
evaluate_expectation("output > 0", exit_code=0, output="1") -> True   (already correct)
evaluate_expectation("match count == 0", exit_code=1, output="0") -> True  (already correct)
```

## T3 — pre-fix `tests/unit/test_verification_parser.py` failures

Before `ParsedTable.skipped` and the `table`-argument `format_results`
signature existed:

```
FAILED tests/unit/test_verification_parser.py::TestMalformedRowReporting::test_format_results_names_authoring_errors_separately
FAILED tests/unit/test_verification_parser.py::TestMalformedRowReporting::test_a_malformed_row_fails_the_run
```

Both raised `AttributeError: 'list' object has no attribute 'malformed'` --
the test file's updated calls pass a `ParsedTable`, and the pre-fix
`format_results(results, malformed=None)` signature could not accept one.

## T9 — pre-fix `tests/unit/test_validate_build.py` failures

Before `scripts/validate_build.py` delegated to `agent.verification_parser`:

```
FAILED tests/unit/test_validate_build.py::TestParseVerificationTable::test_parses_standard_table
FAILED tests/unit/test_validate_build.py::TestParseVerificationTable::test_no_verification_section
FAILED tests/unit/test_validate_build.py::TestParseVerificationTable::test_verification_without_table
FAILED tests/unit/test_validate_build.py::TestCheckVerificationTable::test_passing_command
FAILED tests/unit/test_validate_build.py::TestCheckVerificationTable::test_failing_command
FAILED tests/unit/test_validate_build.py::TestCheckVerificationTable::test_output_check
FAILED tests/unit/test_validate_build.py::TestCheckVerificationTable::test_timeout_skips
FAILED tests/unit/test_validate_build.py::TestMainEdgeCases::test_pipe_bearing_command_runs_intact
10 failed, 72 passed in 12.94s (combined with the two test_verification_parser.py failures above)
```

All ten failures are `TypeError` / `AttributeError` from the updated test
bodies calling the new `ParsedTable`-based API against the still-unconverged
implementation -- the expected shape of demonstrated red before a delegation
change, not an unrelated regression.

After the fix (T1-T13 committed): all 101 tests across both files pass, and
`scripts/validate_build.py` reports the corrected PASS/FAIL for all three rows
above (verified directly against the fixed evaluator).
