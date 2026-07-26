# Test Reliability: Flaky Filter + Deterministic Baseline Parsing

## Overview

Three improvements to the `/do-test` pipeline that eliminate false regression reports and make baseline classification truly deterministic.

## Flaky Filter (Step 0.5)

When tests fail on a feature branch, the pipeline now retries only the failing tests once more before dispatching to baseline verification.

**Flow:**
1. Tests fail on feature branch
2. Re-run only the failing test IDs: `python -m pytest <failing_ids> -v --tb=short`
3. Tests that pass on retry → classified as `FLAKY` (reported but don't block)
4. Tests that still fail → sent to baseline verification as normal
5. If all failures are flaky → baseline verification skipped entirely

**Why:** Flaky tests (timing-dependent, LLM non-determinism, resource contention) that fail on the branch but pass on main were being misclassified as regressions. A single retry catches the most common intermittent failures without requiring additional dependencies like `pytest-rerunfailures`.

**Reporting:** Flaky tests appear in a dedicated results table with `FLAKY` verdict and a note that they should be investigated but don't block the pipeline.

## Deterministic Baseline Parsing (junitxml)

The baseline-verifier agent previously relied on LLM interpretation of raw pytest console output to classify each test. This was non-deterministic and vulnerable to:
- Output truncation filling the context window
- Test ID format mismatches
- Status keywords appearing in test names
- Traceback output interleaving with status lines

**Now:** The baseline-verifier runs pytest with `--junitxml=/tmp/baseline-results.xml` and parses the XML deterministically using Python's `xml.etree.ElementTree`:

```python
import xml.etree.ElementTree as ET
tree = ET.parse('/tmp/baseline-results.xml')
for tc in tree.findall('.//testcase'):
    # Extract classname, name, and status from structured XML
    # No LLM interpretation needed
```

The classification rules table (regression, pre_existing, inconclusive) is applied to the parsed output without any LLM judgment.

## Completeness Validation (Step 5.5)

After classification, a completeness check ensures every input test ID appears in exactly one bucket:

- **Missing IDs** (in input but not in any bucket) → added to `inconclusive` with note "not found in baseline results"
- **Duplicate IDs** (in multiple buckets) → kept in highest-severity bucket (regression > pre_existing > inconclusive)

This prevents silent drops where a test ID is lost during classification and never reported.

## Pipeline Integration

```
Test failures detected
    ↓
Step 0.5: Flaky Filter (retry on branch)
    ↓ (only consistent failures proceed)
Step 1-3: Baseline Verifier (worktree at main)
    ↓
Step 4: Run with --junitxml
    ↓
Step 5: Parse XML deterministically
    ↓
Step 5.5: Completeness validation
    ↓
Step 6-7: Cleanup + return JSON
```

## Related

- Issue: [#476](https://github.com/tomcounsell/ai/issues/476)
- Plan: `docs/plans/476_test_reliability.md`
- Prior art: Issue #363, PR #369 (original baseline verification)
- Spec files: `.claude/skills/do-test/SKILL.md`, `.claude/agents/baseline-verifier.md`
