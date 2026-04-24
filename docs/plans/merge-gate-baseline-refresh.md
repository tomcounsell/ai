---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-24
tracking: https://github.com/tomcounsell/ai/issues/1084
last_comment_id:
---

# Merge-Gate Baseline: Categorise Failures + Refresh Tool

## Problem

The `/do-merge` full-suite gate compares a sorted list of failing pytest node IDs from the PR branch against a flat list in `data/main_test_baseline.json` via `comm -23`. This treats every pre-existing failure as equivalent — a genuinely broken test, a non-deterministic LLM-as-judge test, a module-level `ImportError`, and a test that hangs indefinitely all occupy the same bucket. The file itself is maintained by hand and drifts silently between merges.

**Current behavior:**
- When a PR introduces a regression that happens to show the same failure *count* as a flaky test on main, `comm -23` reports zero new failures and the gate waves the regression through. This was observed on PRs #1054 (MERGED 2026-04-20) and #1070 (MERGED 2026-04-20) — both showed 112 failures on PR and main, but the failing set was different each run.
- The baseline file is git-ignored (`.gitignore:181: data/`), so each machine has its own stale copy. After the #1041 cleanup, the file on at least one machine was ~62 entries out of date.
- No tool exists to regenerate the file. A human runs pytest and hand-edits JSON, occasionally.

**Desired outcome:**
- The merge gate compares *identity* (test node IDs) per category, not just total failure counts. A new deterministic regression blocks merge even if a flaky pre-existing failure happens to pass on this run.
- Pre-existing failures are classified as `real`, `flaky`, `hung`, or `import_error`. New failures in the `flaky` bucket are reported but don't block. New failures in `real`/`hung`/`import_error` block.
- A `scripts/refresh_test_baseline.py` tool regenerates `data/main_test_baseline.json` from N pytest runs, auto-classifying by fail rate.
- Backwards compatibility: both the merge gate and the refresh tool accept the legacy flat `{"failing_tests": [...]}` shape and migrate it on first refresh.

## Freshness Check

**Baseline commit:** `bc23403c` (main) / `a8c3843f` (session worktree)
**Issue filed at:** 2026-04-20T16:40:32Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/commands/do-merge.md:224-292` — Full Suite Gate block, `comm -23` comparison, bootstrap path, post-merge reset — **still holds verbatim**. No commits touched `do-merge.md` since the issue was filed.
- `.claude/commands/do-merge.md:250-259` — Bootstrap path that writes current failures as the baseline when file is missing — **unchanged**.
- `.claude/commands/do-merge.md:265` — `{"failing_tests": []}` reset after clean merge — **unchanged**.
- `.gitignore:181` — `data/` ignored — **confirmed**.
- `tests/unit/test_intake_classifier.py::TestRealHaikuClassification` and `tests/unit/test_work_request_classifier.py::TestLlmClassification` — **both files still exist**. These are the motivating LLM-as-judge flakes.
- `data/main_test_baseline.json` on the session worktree: **does not exist** (git-ignored). This is expected — baseline is per-machine.

**Cited sibling issues/PRs re-checked:**
- #476 / PR #484 — closed 2026-03-23 / merged 2026-03-23. PR #484 introduced the `--junitxml` parser and branch-side retry in `do-test`. Does NOT touch `data/main_test_baseline.json` — confirmed complementary, not duplicative.
- #1041 — closed 2026-04-20T16:36:13Z (4 min before #1084 was filed). Test-suite debt cleanup landed. Issue #1084 was filed immediately afterward as the follow-up lesson from that cleanup.
- #1054 — merged 2026-04-20T12:45:06Z. The session_type refactor. Cited as count-coincidence example.
- #1070 — merged 2026-04-20T12:28:31Z. Reply-chain hydration fix. Cited as count-coincidence example.

**Commits on main since issue was filed (touching referenced files):** None — `git log --since="2026-04-20T16:40:32Z" -- .claude/commands/do-merge.md data/main_test_baseline.json` returns empty.

**Active plans in `docs/plans/` overlapping this area:** None. The closest plan (`test-reliability-flaky-filter.md`) is the completed plan for #476/PR #484, covering the PR-branch flaky filter at `do-test` time — a different layer. No overlap.

**Notes:** The issue's reconnaissance was done immediately after the #1041 cleanup. All file:line references are exact and current.

## Prior Art

- **Issue #363 / PR #369** (closed 2026-03-11) — "Verify pre-existing test failures against main instead of hand-waving". Introduced the baseline-verifier subagent pattern. Established that baseline comparison must be structural (not LLM improvised). Succeeded for its target (`/do-test` on PR branches); did not touch the merge-gate baseline.
- **Issue #476 / PR #484** (closed 2026-03-23) — "Test reliability: flaky filter + baseline verifier". Added branch-side retry for flaky filtering and `--junitxml` parsing (`xml.etree.ElementTree`) in the baseline verifier. This PR's retry-based flaky detection is the oracle the refresh tool will reuse for classification.
- **Issue #1041** (closed 2026-04-20) — "Test-suite debt: 60 failures + 11 errors on main". The cleanup that exposed how stale the merge-gate baseline had become. This issue (#1084) is its direct follow-up.

## Research

No relevant external findings — proceeding with codebase context and training data. The technical ground (pytest `--junitxml`, `xml.etree.ElementTree`, shell `comm`) is all stdlib/well-documented behavior already exercised in #476's implementation.

## Data Flow

**Baseline refresh path (new):**
1. **Entry point**: Developer runs `python scripts/refresh_test_baseline.py --runs 3` on a clean `main` checkout.
2. **Run orchestrator**: Script invokes `pytest tests/ --junitxml=/tmp/baseline-run-{i}.xml -q --tb=no` N times (default 3). Each run gets its own XML file so runs are independent.
3. **Timeout handling**: Each test has a per-test timeout (default 120s, configurable via `--test-timeout`). Tests that exceed this are tagged `hung`. Collection errors (reported in junitxml as `<error>` at the suite level) are tagged `import_error`.
4. **Parser**: `xml.etree.ElementTree` reads each XML file, extracts test identity + outcome per run. Aggregates per-node-ID: pass/fail counts, timeout counts, collection-error counts.
5. **Classifier**: For each test that failed at least once:
   - 100% fail rate, no timeout, no collection error → `real`
   - 1-99% fail rate → `flaky`
   - Any timeout → `hung` (even if some runs passed — a hang is a hang)
   - Any collection error → `import_error`
6. **Writer**: Emits categorised JSON to `data/main_test_baseline.json`. Preserves any existing `note` fields from a previous file when `--merge` is passed.
7. **Output**: JSON written, human-readable diff summary printed to stdout.

**Merge-gate comparison path (modified):**
1. **Entry point**: `/do-merge` reaches the Full Suite Gate step.
2. **PR run**: `pytest tests/ --junitxml=/tmp/pr_run.xml -q --tb=no` — single run on the PR branch (merge gate is not the place to multi-run for flake detection; that happens in `do-test`).
3. **Parser**: Extract failing node IDs from the junitxml output.
4. **Baseline load**: Read `data/main_test_baseline.json`. If the file is legacy-shape (`{"failing_tests": [...]}`), promote every entry to `{"category": "real"}` in memory.
5. **Set comparison**: For each failing node ID on the PR:
   - Key not in baseline → **new regression**. Block unless it is empirically flaky (see Risk R3 below).
   - Key in baseline with `category == "flaky"` → report, allow.
   - Key in baseline with `category == "real"` | `"hung"` | `"import_error"` → report as pre-existing, allow.
6. **Also check the reverse**: Any key in baseline with `category == "real"` that is NOT in the PR's failing set is noted as "pre-existing no longer failing" — informational, not blocking. Suggests the baseline should be refreshed.
7. **Decision**: If any **new regression** remains after classification, gate fails.
8. **Post-merge update**: On clean merge, write `{"schema_version": 2, "tests": {}}` to reset.

## Architectural Impact

- **New dependencies**: None. `xml.etree.ElementTree` is stdlib. Pytest `--junitxml` is built in. Reuses the parsing approach established by PR #484.
- **Interface changes**: `data/main_test_baseline.json` gains a new shape (`schema_version: 2` with a `tests` map). Both shapes must be readable by the merge gate during the migration window.
- **Coupling**: Minor. Adds one new script (`scripts/refresh_test_baseline.py`) and touches the Full Suite Gate section of `.claude/commands/do-merge.md`. No Python source changes outside `scripts/` and `tests/`.
- **Data ownership**: `data/main_test_baseline.json` continues to be git-ignored and per-machine. No change. (Centralising the file is deliberately out of scope — see No-Gos.)
- **Reversibility**: Fully reversible. The new schema is additive; the merge gate still accepts the legacy flat list. Ripping out the refresh tool leaves a working (if hand-maintained) baseline in place.

## Appetite

**Size:** Medium

**Team:** Solo dev + 1 review round

**Interactions:**
- PM check-ins: 1 (mid-build, to confirm the Schema v2 shape still feels right after the first run)
- Review rounds: 1

Coding time is small (script + markdown edits + one integration test). The bottleneck is correctness of the regression-detection test — the thing that motivated the issue — and getting the categorisation thresholds right.

## Prerequisites

No prerequisites — uses only stdlib plus pytest, which is already a test-suite dependency.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| pytest installed | `python -c "import pytest"` | Test runner |
| ElementTree available | `python -c "import xml.etree.ElementTree"` | XML parsing |

## Solution

### Key Elements

- **`scripts/refresh_test_baseline.py`**: Runs pytest N times on the current checkout (intended for `main`), collects per-test outcomes across runs via `--junitxml`, classifies each failing test, writes the categorised baseline.
- **Schema v2 (`data/main_test_baseline.json`)**: A dictionary keyed by test node ID, with each value carrying `category` plus optional metadata (`fail_rate`, `first_seen_commit`, `note`).
- **Backwards-compat loader**: The merge gate and the refresh tool both detect legacy shape (`failing_tests` list) and promote it to schema v2 in memory. No data loss.
- **Categorisation-aware comparison** in `/do-merge`'s Full Suite Gate: compares PR failures against baseline keys, bucketed by category. Blocks only on new `real`/`hung`/`import_error` regressions.
- **Regression test**: A test that simulates the exact PR #1054/#1070 scenario (count-coincident but set-different) and asserts the gate blocks.

### Flow

**Developer on main** → `python scripts/refresh_test_baseline.py --runs 3` → pytest runs 3x → junitxml parsed → classifier buckets each failure → categorised `data/main_test_baseline.json` written → diff summary printed.

**`/do-merge` on PR branch** → Pytest runs 1x → PR failing set loaded → baseline loaded (legacy-promoted if needed) → each PR failure classified against baseline → new `real`/`hung`/`import_error` failures → block; new `flaky` → report, allow; pre-existing → report, allow → decision.

### Technical Approach

1. **Schema shape** (Map keyed by node ID, Option A from the issue):

    ```json
    {
      "schema_version": 2,
      "generated_at": "2026-04-24T12:00:00Z",
      "runs": 3,
      "commit": "bc23403c",
      "tests": {
        "tests/unit/test_intake_classifier.py::TestRealHaikuClassification::test_foo": {
          "category": "flaky",
          "fail_rate": 0.33,
          "note": "LLM-as-judge — see issue #1084"
        },
        "tests/unit/test_reflection.py::test_async_return": {
          "category": "real",
          "fail_rate": 1.0
        }
      }
    }
    ```

    Rationale: a keyed map makes `key in baseline` and `baseline[key].category` both O(1) in Python. It diffs cleanly in git (one line per test). Alternative parallel-list-per-category (`{"flaky": [...], "real": [...]}`) was rejected because moving a test between categories would show as a delete+add diff rather than a value change, and because the classifier already produces a natural per-test record.

2. **Classification source**: Run pytest N times (default `--runs 3`, configurable). Classify by fail-rate thresholds:
    - 100% fail across all N runs → `real`
    - 1-99% fail → `flaky`
    - Any timeout (pytest marker `call/error` with timeout exit) → `hung`
    - Any collection error (pytest `<error>` at session or module level) → `import_error`

    Rationale: reuses the retry-as-oracle pattern from PR #484. N=3 is the minimum needed to distinguish 1-of-3 flakiness from 3-of-3 determinism while keeping runtime tractable (the full suite takes ~5-15 min, so 3 runs ≈ 15-45 min — acceptable for a tool that runs rarely).

3. **Refresh tool surface**: `scripts/refresh_test_baseline.py` (matches the pattern of `scripts/nightly_regression_tests.py` and `scripts/sdlc_reflection.py`). Arguments:
    - `--runs N` (default 3) — number of pytest invocations
    - `--output PATH` (default `data/main_test_baseline.json`) — where to write
    - `--test-timeout SECONDS` (default 120) — per-test timeout for classifying `hung`
    - `--merge` — preserve `note` fields from existing file if present (otherwise overwrite)
    - `--dry-run` — run pytest, print the classification summary, don't write the file
    - `--verbose` — log each pytest invocation

    Rationale: put it in `scripts/` because it's a developer/automation tool, not imported by the bridge or worker. `tools/` is reserved for code called by the agent via MCP. Args mirror the refresh workflow: usually `--runs 3 --merge`, occasionally `--dry-run` to preview.

4. **Merge-gate comparison logic** (see Data Flow step 5):
    - The gate uses a Python block (not shell `comm`) because once the schema is a dict, shell set-ops become unwieldy. This simplifies the script in `.claude/commands/do-merge.md`.
    - The comparison produces three structured outputs: `new_blocking_regressions`, `new_flaky_occurrences`, `preexisting_failures_present`, plus `baseline_keys_no_longer_failing` (advisory).
    - The gate blocks if `len(new_blocking_regressions) > 0`.

5. **Migration**:
    - Merge gate: if loaded JSON has `failing_tests` key (and no `tests` key), treat as legacy. In-memory promote each entry to `{"category": "real"}`. No file write from the merge gate — only the refresh tool upgrades the on-disk format.
    - Refresh tool: on first run against a legacy file, write the v2 shape.
    - No version sunset — legacy load stays in place as a thin compatibility path. If a dev never runs the refresh tool, the gate still works (degraded — everything is `real`).

### Migration Detection Rule

- A file with `"schema_version": 2` AND a `"tests"` object → v2
- A file with a `"failing_tests"` array and no `"schema_version"` → v1 (legacy)
- An empty file or malformed JSON → treat as no baseline (existing bootstrap path)
- Any other shape → log a warning, treat as no baseline, write a fresh v2 on next refresh

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The parser for `junitxml` must not silently swallow missing attributes. Add a test that feeds a malformed junitxml (e.g., missing `classname`) and asserts the tool reports the error with a node ID hint, not a cryptic `KeyError`.
- [ ] The merge-gate Python block must not silently pass when `data/main_test_baseline.json` is empty/malformed. Ensure the existing bootstrap path is preserved and add a test for "malformed JSON, no schema_version, no failing_tests" → treat as no baseline.

### Empty/Invalid Input Handling
- [ ] Zero failures across all runs → write empty `tests: {}` map, not a missing key.
- [ ] Empty PR failing set on merge gate → short-circuit success path (no comparison needed).
- [ ] None/empty strings for `--output` → argparse rejects with non-zero exit.

### Error State Rendering
- [ ] When the gate blocks, the output must list every blocking regression by full node ID, not just a count. The existing `/do-merge` surface already does this; the new block must preserve that.

## Test Impact

- [ ] `tests/unit/test_do_merge_baseline.py` (create) — NEW file for the categorised baseline logic. Covers: legacy-shape load, v2 load, new-regression detection, flaky-occurrence pass-through, count-coincident regression test (simulated PR #1054/#1070 scenario).
- [ ] `tests/unit/test_refresh_test_baseline.py` (create) — NEW file for the refresh tool. Covers: N-run aggregation, fail-rate classifier boundaries (0%, 1-of-3, 2-of-3, 3-of-3), timeout → `hung`, collection error → `import_error`, `--merge` preserves notes, `--dry-run` doesn't write.
- [ ] No existing tests affected — `/do-merge`'s baseline handling has no current unit tests (it's embedded in the markdown skill), and PR #484's test infrastructure (`tests/unit/test_baseline_verifier*.py` if present) is a different layer. No UPDATE/DELETE/REPLACE actions on existing tests. The regression test is additive.

## Rabbit Holes

- **Centralising the baseline in git**: Tempting ("then it's not per-machine!") but #1084's `Dropped` bucket explicitly ruled it out. `data/` is ignored for good reasons (transient artifacts, privacy). A shared baseline is a separate feature with different tradeoffs — leave it.
- **Running the refresh tool automatically as a nightly cron**: Sounds efficient but pytest's full suite takes 5-15 min × 3 runs = 15-45 min of machine time. Not worth automating until we measure how often the baseline actually drifts. Ship the tool first, measure, automate later if warranted.
- **Integrating with PR #484's branch-side retry**: Tempting to share code, but the two live at different stages (PR branch at `/do-test` vs. `main` at `/do-merge`). The refresh tool runs on `main` without regard to a PR; sharing would couple them prematurely. Keep the implementations cleanly separate even if some parser code is visually similar.
- **LLM-based categorisation**: The tool could ask Claude to classify "is this failure real or flaky?" but deterministic fail-rate thresholds are better: they are reproducible, cheap, and have no hidden failure modes. Use LLMs only where judgment is needed; fail rate is not judgment.
- **Per-category `hung` timeouts**: Pytest has `pytest-timeout`, which we don't currently use repo-wide. Adding it is a tangent. Implement a simpler per-test timeout with `subprocess.run(timeout=...)` wrapping pytest invocations, with a single global per-run wall clock.

## Risks

### Risk 1: N=3 runs may misclassify low-rate flakes as `real`

**Impact:** A test that fails 5% of the time would pass all 3 runs ~86% of the time (would classify as not-failing, won't appear in baseline); but if it happens to fail once in 3, it classifies as `flaky` correctly. The real risk is the opposite: a test that fails 30% of the time could fail 3/3 (probability ~2.7%) and classify as `real`. A PR that causes this test to pass would go unblocked.

**Mitigation:** Ship with `--runs 3` default but expose `--runs` as configurable. Document in `tests/README.md` that N=5 is safer when you suspect the baseline is noisy. The classifier also records `fail_rate` in each entry, so post-hoc tuning is possible. Low-rate flakes misclassified as `real` fail **closed** (block merges that shouldn't block) rather than open (allow regressions) — the safer direction.

### Risk 2: Schema v2 migration introduces a silent regression during the transition

**Impact:** If the migration logic in `/do-merge` is wrong, a running system might pass a regression through during the migration window.

**Mitigation:** Migration is **read-only** in the merge gate (no file writes from the gate itself). The refresh tool is the only upgrader. This means rollback is trivial: revert the `/do-merge` markdown change and the legacy flat file (if present) continues working as before. The regression test covers both shapes explicitly.

### Risk 3: A test is genuinely flaky AND a PR genuinely regresses it

**Impact:** The gate would see the test failing on the PR, find it in the `flaky` bucket of the baseline, and allow the merge. A real regression slips through because the test was already known-flaky.

**Mitigation:** This is a known limitation of any fail-rate-based classifier. Document it in `tests/README.md`. The alternative — always blocking on flaky tests — would force PRs to retry indefinitely. Recommended practice: a test that becomes a recurring source of confusion should be moved to `@pytest.mark.flaky` with a retry plugin, or quarantined entirely, rather than kept in the baseline. This is out of scope for the plan but worth noting.

### Risk 4: Refresh-tool runtime is prohibitive on CI

**Impact:** 15-45 min to refresh the baseline means developers won't run it, and staleness returns.

**Mitigation:** The tool is designed for manual periodic runs, not CI. `--runs 1` exists for "quick snapshot" usage (no flaky classification, everything ≥1 failure gets `real`). The canonical trigger is "after a large green-main cleanup." If this proves insufficient in practice, nightly automation is a straightforward follow-up (explicitly out of scope per No-Gos).

## Race Conditions

No race conditions identified. The refresh tool runs sequentially (N pytest invocations, one at a time). The merge gate reads the baseline file once before comparison and does not race with concurrent processes (each dev's worktree has its own file). Pytest-internal parallelism (`-n auto`) does not introduce concurrency at the baseline layer — each junitxml is produced by a single pytest process.

## No-Gos (Out of Scope)

- **Centralising the baseline file in git**: Explicitly called out in the issue's Dropped bucket. Defer to a separate future feature if a shared-baseline case emerges.
- **Automating refresh tool as a cron/launchd job**: Not worth it until we measure drift frequency. Ship manual, observe, automate later if warranted.
- **Replacing the flaky filter in `/do-test`**: That is PR #484's system and operates on a different layer. Merge-gate baseline and PR-branch flaky filter stay independent.
- **`pytest-timeout` integration repo-wide**: Adding a repo-wide timeout plugin is a much larger change. Implement `--test-timeout` locally in the refresh tool only.
- **Categorising flaky tests by *cause* (LLM-judge vs. network vs. state leak)**: The four categories (real, flaky, hung, import_error) are sufficient. Finer-grained causes are a separate concern.
- **Auto-quarantining flaky tests**: Marking tests as skip when they enter the baseline's `flaky` bucket would silently reduce coverage. Explicit, manual quarantine (via `@pytest.mark.flaky` or `skip`) is the correct path.

## Update System

No update system changes required — the refresh tool is a developer convenience with no runtime dependencies and no deployment artifacts. The updated `/do-merge` markdown is already part of `.claude/commands/` which is synced with the repo checkout on every `/update`.

## Agent Integration

No agent integration required — this is a developer/CI tooling change. The refresh tool lives in `scripts/`, not `tools/`. `/do-merge` is a Claude Code skill executed by the builder/merge agent, so the markdown-level changes are picked up via normal skill loading. No MCP changes, no `.mcp.json` edits, no bridge imports.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/merge-gate-baseline.md` describing: what the merge-gate baseline is, how it differs from the PR-branch flaky filter (#476), the schema v2 shape, when to refresh, how to interpret the four categories.
- [ ] Add entry to `docs/features/README.md` index table under the Testing section.
- [ ] Update `tests/README.md` with a one-paragraph explainer distinguishing the merge-gate baseline from the #476 flaky filter (required by issue acceptance criteria).
- [ ] Update `.claude/commands/do-merge.md` — replace the Full Suite Gate section with the new Python-based comparison, document the four categories, document the migration rule.
- [ ] Update the "Quick Commands" table in `CLAUDE.md` with a row for `python scripts/refresh_test_baseline.py`.

### Inline Documentation
- [ ] Module-level docstring in `scripts/refresh_test_baseline.py` explaining purpose, default arguments, and intended invocation pattern.
- [ ] Inline comment block at the top of the modified Full Suite Gate in `.claude/commands/do-merge.md` pointing to `docs/features/merge-gate-baseline.md`.

## Success Criteria

- [ ] `data/main_test_baseline.json` supports schema v2 (map keyed by node ID, categories: `real`, `flaky`, `hung`, `import_error`).
- [ ] `scripts/refresh_test_baseline.py` exists and regenerates the file from N pytest runs, classifying automatically.
- [ ] `/do-merge` full-suite gate treats new `flaky`-category failures as non-blocking and new `real`/`hung`/`import_error` failures as blocking.
- [ ] A regression test demonstrates that a PR introducing a genuine regression masked by a count-coincident flaky failure is blocked (simulates the PR #1054/#1070 scenario).
- [ ] Both the refresh tool and `/do-merge` accept the legacy `{"failing_tests": [...]}` flat schema (promoted to `real` in memory).
- [ ] `.claude/commands/do-merge.md` documents the new comparison logic.
- [ ] `tests/README.md` includes a paragraph distinguishing the merge-gate baseline from #476's flaky filter.
- [ ] `docs/features/merge-gate-baseline.md` created and linked from `docs/features/README.md`.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] `python -m ruff check .` and `python -m ruff format --check .` pass.

## Team Orchestration

The builder handles this plan end-to-end: it's a single-module script + one markdown edit + tests + docs. No parallel builder streams are warranted.

### Team Members

- **Builder (baseline-tooling)**
  - Name: `baseline-builder`
  - Role: Implement `scripts/refresh_test_baseline.py`, update `.claude/commands/do-merge.md`'s Full Suite Gate, write unit tests, write docs.
  - Agent Type: `builder`
  - Resume: true

- **Validator (baseline-tooling)**
  - Name: `baseline-validator`
  - Role: Verify all Success Criteria are met; run the regression test end-to-end; confirm schema v1 and v2 files both load correctly; confirm the regression-test scenario blocks.
  - Agent Type: `validator`
  - Resume: true

## Step by Step Tasks

### 1. Implement refresh tool core
- **Task ID**: build-refresh-tool
- **Depends On**: none
- **Validates**: `tests/unit/test_refresh_test_baseline.py` (create)
- **Informed By**: Prior Art #476/#484 (junitxml parsing pattern)
- **Assigned To**: baseline-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/refresh_test_baseline.py` with argparse for `--runs`, `--output`, `--test-timeout`, `--merge`, `--dry-run`, `--verbose`.
- Implement the N-run orchestrator: for i in range(N), invoke pytest with `--junitxml=/tmp/baseline-run-{i}.xml`, capture exit code, respect `--test-timeout` as per-process timeout via `subprocess.run(timeout=...)` on a wall-clock basis.
- Implement the junitxml aggregator: parse each XML with `xml.etree.ElementTree`, collect per-node-ID outcomes across runs.
- Implement the classifier: fail_rate thresholds (100% → `real`, 1-99% → `flaky`, any timeout → `hung`, any collection-error → `import_error`).
- Implement the writer: emit schema v2 JSON. Support `--merge` to preserve `note` fields from the existing file.

### 2. Update /do-merge comparison logic
- **Task ID**: update-merge-gate
- **Depends On**: none (can run in parallel with build-refresh-tool)
- **Validates**: `tests/unit/test_do_merge_baseline.py` (create)
- **Assigned To**: baseline-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the shell-based `comm -23` block in `.claude/commands/do-merge.md` (lines 226-284) with a Python block that: loads the baseline (auto-promoting legacy shape), parses the PR's junitxml or falls back to text parsing, computes the three output buckets, blocks on any new `real`/`hung`/`import_error`.
- Preserve the existing bootstrap path (missing file → write current failures as baseline). Update the bootstrap writer to emit schema v2 with everything categorised as `real` (a single run can't distinguish real from flaky).
- Preserve the post-merge reset path; update it to write `{"schema_version": 2, "tests": {}}`.
- Add an inline comment pointing to `docs/features/merge-gate-baseline.md`.

### 3. Regression test for count-coincidence scenario
- **Task ID**: build-regression-test
- **Depends On**: update-merge-gate, build-refresh-tool
- **Validates**: `tests/unit/test_do_merge_baseline.py::test_count_coincident_regression_is_blocked`
- **Assigned To**: baseline-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Construct a baseline fixture with: `{flaky_test_A: "flaky", real_test_B: "real"}`.
- Construct a PR-failing-set fixture with: `{real_test_B: "still failing", real_test_C: "new deterministic regression"}` — count is 2, matches baseline count of 2, but `real_test_C` is genuinely new.
- Assert the comparison classifies `real_test_C` as `new_blocking_regression` and that the gate decision is BLOCK.
- Add a second assertion: the PR failing set `{flaky_test_A: "now failing"}` is reported as non-blocking (flaky re-occurrence).

### 4. Write feature documentation
- **Task ID**: document-feature
- **Depends On**: build-refresh-tool, update-merge-gate, build-regression-test
- **Assigned To**: baseline-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/merge-gate-baseline.md` describing the system end to end.
- Add an entry to `docs/features/README.md` index table.
- Update `tests/README.md` with the one-paragraph distinction between merge-gate baseline and #476 flaky filter.
- Add a row in `CLAUDE.md` "Quick Commands" for `python scripts/refresh_test_baseline.py`.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-refresh-tool, update-merge-gate, build-regression-test, document-feature
- **Assigned To**: baseline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_refresh_test_baseline.py tests/unit/test_do_merge_baseline.py -v` → all pass.
- Run `python -m ruff check scripts/refresh_test_baseline.py tests/unit/test_refresh_test_baseline.py tests/unit/test_do_merge_baseline.py` → exit 0.
- Run `python -m ruff format --check scripts/refresh_test_baseline.py tests/unit/test_refresh_test_baseline.py tests/unit/test_do_merge_baseline.py` → exit 0.
- Load a legacy `{"failing_tests": [...]}` JSON in Python using the new loader → confirm it promotes to `real`.
- Run the refresh tool with `--dry-run --runs 1` on a small test subset (e.g., `pytest tests/unit/test_sdlc_skill_md_parity.py --junitxml=/tmp/smoke.xml`) → confirm the tool emits valid JSON to stdout without writing.
- Verify all Success Criteria checkboxes are checkable from artifacts.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Refresh-tool tests pass | `pytest tests/unit/test_refresh_test_baseline.py -q` | exit code 0 |
| Merge-gate tests pass | `pytest tests/unit/test_do_merge_baseline.py -q` | exit code 0 |
| Regression scenario blocks | `pytest tests/unit/test_do_merge_baseline.py::test_count_coincident_regression_is_blocked -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/refresh_test_baseline.py tests/unit/` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/refresh_test_baseline.py tests/unit/` | exit code 0 |
| Refresh tool dry-run works | `python scripts/refresh_test_baseline.py --dry-run --runs 1 --output /tmp/out.json` | exit code 0 |
| Feature doc exists | `test -f docs/features/merge-gate-baseline.md` | exit code 0 |
| tests/README.md updated | `grep -q 'merge-gate baseline' tests/README.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None. The five open questions from the issue's Solution Sketch are resolved in the Technical Approach section:

1. **Schema shape** — Map keyed by node ID with per-test category + metadata. Resolved in Technical Approach #1.
2. **Classification source** — N-run pytest with fail-rate thresholds; defaults to N=3, configurable. Resolved in Technical Approach #2.
3. **Refresh tool surface** — `scripts/refresh_test_baseline.py` with `--runs`, `--output`, `--test-timeout`, `--merge`, `--dry-run`, `--verbose`. Resolved in Technical Approach #3.
4. **Merge-gate comparison logic** — Python block in `.claude/commands/do-merge.md` that buckets PR failures by baseline category; blocks on new `real`/`hung`/`import_error`. Resolved in Technical Approach #4 and Data Flow step 5.
5. **Migration** — Read-only legacy promotion at load time; refresh tool is the only upgrader. Resolved in Technical Approach #5.

If critique surfaces anything that warrants a human call, this section will be repopulated before build.
