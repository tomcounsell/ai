---
status: Done
type: bug
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/476
last_comment_id:
---

# Test Reliability: Flaky Filter + Deterministic Baseline Parsing

## Problem

The test pipeline produces false positive regressions due to two issues: flaky tests that fail intermittently are sent directly to baseline verification (which may also see them flake), and the baseline verifier uses LLM text parsing of raw pytest output instead of structured data, making classification non-deterministic.

## Prior Art

- **Issue #363**: Established the baseline verification concept. Closed by PR #369.
- **PR #369**: Implemented the current baseline-verifier agent and do-test integration.

## Solution

### Fix 1: Flaky Filter (Step 0.5 in do-test)
Before dispatching to baseline-verifier, retry only the failing test IDs once on the current branch. Tests that pass on retry are classified as FLAKY and reported but don't block. Tests that still fail proceed to baseline verification.

### Fix 2: Deterministic Baseline Parsing (junitxml)
Replace LLM text parsing of pytest console output with `--junitxml` flag and `xml.etree.ElementTree` parsing. This removes all LLM interpretation from the classification step.

### Fix 3: Completeness Validation (Step 5.5)
After classification, verify every input test ID appears in exactly one bucket. Missing IDs are added to inconclusive. Duplicates across buckets are resolved by keeping the highest-severity classification.

## Prerequisites

None -- all capabilities (pytest junitxml, xml.etree.ElementTree) are standard toolchain.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers -- all changes are to markdown agent/skill specs

### Empty/Invalid Input Handling
- [ ] Flaky filter: if all failing tests pass on retry, baseline verification is skipped
- [ ] Baseline verifier: if junitxml file is missing or malformed, classify all tests as inconclusive

## Test Impact

No existing tests affected -- all changes are to markdown skill/agent specification files consumed by the LLM agent, not by pytest.

## Rabbit Holes

- Installing pytest-rerunfailures plugin -- unnecessary; a single manual retry is simpler
- Adding historical flake tracking -- over-engineering for the current need

## Risks

### Risk 1: Retry masks real regressions
**Mitigation:** Only one retry. True regressions fail consistently.

## No-Gos

- Not adding pytest-rerunfailures or any new pip dependencies
- Not changing the subagent dispatch mechanism
- Not adding historical flake tracking

## Update System

No update system changes required -- this modifies only markdown files propagated via standard git pull.

## Agent Integration

No agent integration required -- the baseline-verifier is already a registered subagent dispatched by do-test.

## Success Criteria

- [x] do-test SKILL.md includes flaky filter step (Step 0.5) that retries failing tests once before baseline dispatch
- [x] baseline-verifier.md uses `--junitxml` and Python XML parsing instead of LLM text parsing
- [x] baseline-verifier.md includes completeness validation (Step 5.5) ensuring all input test IDs are classified
- [x] Flaky test results appear in the results table with FLAKY classification
- [x] Documentation created and feature index updated

## Documentation

- [x] Create `docs/features/test-reliability-flaky-filter.md`
- [x] Add entry to `docs/features/README.md` index table
