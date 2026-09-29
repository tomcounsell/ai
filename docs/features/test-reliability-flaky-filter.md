# Test Reliability: Flaky Filter + Deterministic Baseline Parsing

## Overview

Three parts of the `/do-test` pipeline that eliminate false regression reports and make baseline classification deterministic.

## Flaky Filter (Step 0.5)

When tests fail on a feature branch, the pipeline retries only the failing tests once more before dispatching to baseline verification.

**Flow:**
1. Tests fail on feature branch
2. Re-run only the failing test IDs: `python -m pytest <failing_ids> -v --tb=short`
3. Tests that pass on retry → classified as `FLAKY` (reported but don't block)
4. Tests that still fail → sent to baseline verification as normal
5. If all failures are flaky → baseline verification skipped entirely

**Why:** Flaky tests (timing-dependent, LLM non-determinism, resource contention) that fail on the branch but pass on main are otherwise misclassified as regressions. A single retry catches the most common intermittent failures without requiring additional dependencies like `pytest-rerunfailures`.

**Reporting:** Flaky tests appear in a dedicated results table with `FLAKY` verdict and a note that they should be investigated but don't block the pipeline.

## Deterministic Baseline Parsing (junitxml)

do-test's `scripts/baseline_verify.py` runs pytest with `--junitxml` into its temp dir and parses the XML deterministically using Python's `xml.etree.ElementTree`. This avoids the non-determinism of LLM interpretation of raw console output, which is vulnerable to:
- Output truncation filling the context window
- Test ID format mismatches
- Status keywords appearing in test names
- Traceback output interleaving with status lines

The structured XML parse:

```python
import xml.etree.ElementTree as ET
tree = ET.parse(junit_path)
for tc in tree.findall('.//testcase'):
    # Extract classname, name, and status from structured XML
    # No LLM interpretation needed
```

The classification rules table (regression, pre_existing, inconclusive) is applied to the parsed output without any LLM judgment.

## Completeness

The script buckets each input test ID exactly once: an ID that errors, skips, or is missing from the baseline results goes to `inconclusive`, and a setup failure marks every ID inconclusive. No test ID is silently dropped.

## Pipeline Integration

```
Test failures detected
    ↓
Step 0.5: Flaky Filter (retry on branch)
    ↓ (only consistent failures proceed)
baseline_verify.py: throwaway worktree at main
    ↓
Run only the failing IDs with --junitxml
    ↓
Parse XML deterministically; bucket every ID once
    ↓
Remove worktree + print JSON
```

## Related

- Plan: `docs/plans/476_test_reliability.md`
- Spec files: `.claude/skills-global/do-test/baseline-verification.md`, `.claude/skills-global/do-test/scripts/baseline_verify.py`
