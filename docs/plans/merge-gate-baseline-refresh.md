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
2. **Run orchestrator**: Script invokes `pytest tests/ --junitxml=/tmp/baseline-run-{i}.xml -q --tb=no -p pytest_timeout --timeout={test_timeout} --timeout-method=thread` N times (default 3). Each run gets its own XML file so runs are independent. The `pytest-timeout` plugin produces a per-test `<failure message="Timeout ..."/>` entry in junitxml — a real, classifiable `hung` verdict per node ID — instead of killing the whole pytest process as `subprocess.run(timeout=...)` would do. This is the design pick that resolves the critique's BLOCKER: `hung` is now reachable.
3. **Wall-clock safety net**: The entire pytest invocation is still wrapped in `subprocess.run(timeout=global_wall_clock)` where `global_wall_clock = test_timeout * expected_test_count * safety_factor` (default: 3× the sum of per-test timeouts). If `pytest-timeout` fails to terminate a wedged test (rare, e.g., a C extension ignoring the signal), the outer subprocess timeout catches it. When the outer timeout fires, the whole run is treated as UNCLASSIFIABLE for that attempt — the refresh tool logs a warning, discards the junitxml, and continues with the remaining runs. If all N runs hit the outer timeout, the tool exits non-zero without writing.
4. **Parser**: `xml.etree.ElementTree` reads each XML file. For each `<testcase>`, read `classname + name` for node identity and inspect children: `<failure>` with `message` containing `"Timeout"` (pytest-timeout's signature) → timeout outcome; `<failure>` without that message → fail outcome; `<error>` at session or module level with no per-testcase entry → collection error. Aggregates per-node-ID: pass/fail/timeout/collection-error counts across the N runs.
5. **Classifier**: For each test that failed at least once across N runs (applied in this order — first match wins):
   - Any collection error → `import_error` (the test couldn't even load; takes precedence because it is structural).
   - Any timeout outcome (pytest-timeout `<failure message="Timeout ...">`) → `hung`. Takes precedence over fail/flaky — a test that hangs once in 3 runs is `hung`, not `flaky`, because a hang is a different failure mode that warrants a different fix.
   - 100% fail rate across all N runs, zero timeouts, zero collection errors → `real`.
   - 1-99% fail rate across N runs, zero timeouts, zero collection errors → `flaky`.

    This precedence order is explicit because a test can hit multiple buckets (e.g., fail 2× and timeout 1×) and the classifier must pick one. `hung` and `import_error` are structural; `real` and `flaky` are count-based.
6. **Writer**: Emits categorised JSON to the `--output` path (default `data/main_test_baseline.json`). Preserves any existing `note` fields from a previous file when `--merge` is passed.
7. **Output**: JSON written, human-readable diff summary printed to stdout (counts per category, list of tests moved between categories since last refresh if `--merge`).

**Merge-gate comparison path (modified):**
1. **Entry point**: `/do-merge` reaches the Full Suite Gate step.
2. **PR run**: `pytest tests/ --junitxml=/tmp/pr_run.xml -q --tb=no` — single run on the PR branch. The merge gate does NOT invoke pytest-timeout per-test; PR-branch hang detection is delegated to `do-test`'s existing infrastructure and, ultimately, to the outer `/do-merge` wall-clock. The merge gate is a pass/fail oracle for the baseline diff, not a flake-detection layer.
3. **Parser**: Extract failing node IDs from `/tmp/pr_run.xml` (same junitxml-based parser as refresh; reuses the parsing function from `scripts/refresh_test_baseline.py`, imported by the embedded Python block in `do-merge.md`). Treat all non-pass outcomes (fail, error, timeout) as "failing" for set-comparison purposes.
4. **Baseline load**: Read `data/main_test_baseline.json`. If the file is legacy-shape (`{"failing_tests": [...]}` with no `schema_version`), promote every entry to `{"category": "real"}` in memory. If the file is missing or malformed, bootstrap path runs (see Technical Approach §5).
5. **Set comparison**: For each failing node ID on the PR:
   - Key not in baseline → **new regression**. Block. (The merge gate does not retry flaky tests itself; `do-test` already did that on the PR branch before this stage.)
   - Key in baseline with `category == "flaky"` → report as flaky re-occurrence, allow.
   - Key in baseline with `category == "real"` | `"hung"` | `"import_error"` → report as pre-existing, allow.
6. **Also check the reverse**: Any key in baseline with `category == "real"` that is NOT in the PR's failing set is noted as "pre-existing no longer failing" — informational, suggests the baseline should be refreshed. Not blocking.
7. **Decision**: If any **new regression** remains after classification, gate fails and emits a structured block message listing every blocking node ID.
8. **Post-merge update**: On clean merge, write `{"schema_version": 2, "generated_at": "<iso-utc>", "runs": 0, "commit": "<sha>", "tests": {}}` to reset.

## Architectural Impact

- **New dependencies**: One dev dep — `pytest-timeout>=2.3` — added to `pyproject.toml` and locked in `uv.lock`. `xml.etree.ElementTree` is stdlib; `pytest --junitxml` is built-in. Reuses the parsing approach established by PR #484. The pytest-timeout plugin is NOT registered in pytest's default addopts and does NOT affect non-refresh-tool pytest invocations (see Risk R6).
- **Interface changes**: `data/main_test_baseline.json` gains a new shape (`schema_version: 2` with a `tests` map). Both shapes must be readable by the merge gate during the migration window.
- **Coupling**: Minor. Adds one new script (`scripts/refresh_test_baseline.py`), touches the Full Suite Gate section of `.claude/commands/do-merge.md`, and updates `pyproject.toml` + `uv.lock`. No Python source changes outside `scripts/` and `tests/`.
- **Data ownership**: `data/main_test_baseline.json` continues to be git-ignored and per-machine. No change. (Centralising the file is deliberately out of scope — see No-Gos.)
- **Reversibility**: Fully reversible. The new schema is additive; the merge gate still accepts the legacy flat list. Removing `pytest-timeout` and ripping out the refresh tool leaves the merge gate's legacy-only behavior (flat list, shell `comm`) as a working fallback — the markdown-level gate logic would need to be reverted in a single edit to `.claude/commands/do-merge.md`.

## Appetite

**Size:** Medium

**Team:** Solo dev + 1 review round

**Interactions:**
- PM check-ins: 1 (mid-build, to confirm the Schema v2 shape still feels right after the first run)
- Review rounds: 1

Coding time is small (script + markdown edits + one integration test). The bottleneck is correctness of the regression-detection test — the thing that motivated the issue — and getting the categorisation thresholds right.

## Prerequisites

One new dev dependency: `pytest-timeout`. This is the resolution to the critique's BLOCKER — it gives us real per-test timeout outcomes in junitxml that the classifier can bucket as `hung`. It is added to the `[tool.uv]` / `[project.optional-dependencies]` group alongside the other pytest extras (`pytest-asyncio`, `pytest-xdist`, `pytest-json-report`) in `pyproject.toml`, and locked via `uv lock`.

The plugin is NOT registered in `pyproject.toml`'s `[tool.pytest.ini_options]` and NOT added to the default pytest addopts — it only activates when the refresh tool invokes pytest with `-p pytest_timeout --timeout=N`. The merge-gate's PR-branch pytest run does NOT use it. This keeps the plugin scoped to the refresh tool's invocation, preserving the No-Go against "repo-wide pytest-timeout integration" while unblocking the `hung` category. (The No-Go as written in the original plan meant "don't change the default pytest behavior everywhere" — that remains true.)

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| pytest installed | `python -c "import pytest"` | Test runner |
| pytest-timeout installed | `python -c "import pytest_timeout"` | Per-test timeout classification for `hung` |
| ElementTree available | `python -c "import xml.etree.ElementTree"` | XML parsing |
| uv.lock synced | `uv lock --check` | Ensures the new dep is locked before merge |

## Solution

### Key Elements

- **`scripts/refresh_test_baseline.py`**: Runs pytest N times on the current checkout (intended for `main`) with `pytest-timeout` enabled per-run, collects per-test outcomes across runs via `--junitxml`, classifies each failing test, writes the categorised baseline.
- **`pytest-timeout` dev dep**: Added to `pyproject.toml` and locked in `uv.lock`, but NOT registered in pytest's default addopts. Only activated when the refresh tool invokes pytest with `-p pytest_timeout --timeout=N`. Delivers the per-test `hung` classification that the critique's BLOCKER identified as missing.
- **Schema v2 (`data/main_test_baseline.json`)**: A dictionary keyed by test node ID, with each value carrying `category` plus optional metadata (`fail_rate`, `hung_count`, `note`). Top-level metadata (`schema_version`, `generated_at`, `generated_by`, `runs`, `commit`, optional `bootstrap`) each has a concrete consumer.
- **Backwards-compat loader**: The merge gate and the refresh tool both detect legacy shape (`failing_tests` list) and promote it to schema v2 in memory. No data loss.
- **Categorisation-aware comparison** in `/do-merge`'s Full Suite Gate: compares PR failures against baseline keys, bucketed by category. Blocks only on new `real`/`hung`/`import_error` regressions. Includes a staleness warning when `generated_at` is more than 14 days old.
- **Regression test**: A test that simulates the exact PR #1054/#1070 scenario (count-coincident but set-different) and asserts the gate blocks. A second regression test asserts that a test recorded as `hung` in the baseline is NOT blocking when the PR still has it failing.

### Flow

**Developer on main** → `python scripts/refresh_test_baseline.py --runs 3` → pytest runs 3× with `-p pytest_timeout --timeout=60` → junitxml parsed, per-test outcomes (pass/fail/timeout/collection-error) aggregated → classifier buckets each failure (precedence: `import_error` > `hung` > `real` > `flaky`) → categorised `data/main_test_baseline.json` written → diff summary printed.

**`/do-merge` on PR branch** → Pytest runs 1× (no pytest-timeout — that's a refresh-tool-only concern) → PR failing set loaded from junitxml → baseline loaded (legacy-promoted if needed, bootstrap path if missing) → each PR failure classified against baseline → new `real`/`hung`/`import_error` failures → block; new `flaky` → report, allow; pre-existing → report, allow → staleness warning if baseline is 14+ days old → decision.

### Technical Approach

1. **Schema shape** (Map keyed by node ID, Option A from the issue):

    ```json
    {
      "schema_version": 2,
      "generated_at": "2026-04-24T12:00:00+00:00",
      "generated_by": "scripts/refresh_test_baseline.py --runs 3",
      "runs": 3,
      "commit": "bc23403c",
      "tests": {
        "tests/unit/test_intake_classifier.py::TestRealHaikuClassification::test_foo": {
          "category": "flaky",
          "fail_rate": 0.33,
          "hung_count": 0,
          "note": "LLM-as-judge — see issue #1084"
        },
        "tests/unit/test_reflection.py::test_async_return": {
          "category": "real",
          "fail_rate": 1.0,
          "hung_count": 0
        },
        "tests/integration/test_wedge.py::test_deadlock": {
          "category": "hung",
          "fail_rate": 0.33,
          "hung_count": 1,
          "note": "Times out in thread-mode; investigate"
        }
      }
    }
    ```

    **Field semantics — each one earns its place:**
    - `schema_version` (int, required): used by the loader for format detection.
    - `generated_at` (ISO-8601 UTC timestamp with `+00:00` suffix — `datetime.now(timezone.utc).isoformat()`): used by the merge gate's staleness warning (see §4 below).
    - `generated_by` (string): exact invocation, for human debugging of drift.
    - `runs` (int): N pytest invocations that produced this file; needed by the classifier's `fail_rate` validation and by future refreshes that compare counts.
    - `commit` (string): `git rev-parse --short HEAD` at refresh time. Lets a reader confirm which baseline sha their file reflects.
    - `tests` (object): per-node-ID records.
    - `tests.<node_id>.category` (string, one of `real`|`flaky`|`hung`|`import_error`): used by the gate.
    - `tests.<node_id>.fail_rate` (float, `(fail_count + hung_count) / runs`): used by humans to tune thresholds.
    - `tests.<node_id>.hung_count` (int): needed because `category=hung` could come from 1 timeout in N; the count lets a reader see severity.
    - `tests.<node_id>.note` (string, optional): free-text annotation preserved across `--merge` refreshes.

    Rationale: a keyed map makes `key in baseline` and `baseline[key]["category"]` both O(1) in Python. It diffs cleanly in git (one line per test). Alternative parallel-list-per-category (`{"flaky": [...], "real": [...]}`) was rejected because moving a test between categories would show as a delete+add diff rather than a value change, and because the classifier already produces a natural per-test record.

2. **Classification source**: Run pytest N times (default `--runs 3`, configurable). The pytest invocation is:

    ```
    pytest tests/ \
      -q --tb=no \
      --junitxml=/tmp/baseline-run-{i}.xml \
      -p pytest_timeout --timeout={per_test_timeout} --timeout-method=thread
    ```

    Per-test timeouts use `pytest-timeout`'s **thread** method (default), not `signal`. `thread` works cross-platform and does not conflict with pytest-xdist workers. The plugin emits a `<failure message="Timeout (>...s) from pytest-timeout">` entry per timed-out test in junitxml — a real, classifiable per-node outcome.

    Classify by outcome across N runs, applying rules in this precedence order (first match wins):
    - Any collection error for this node → `import_error`
    - Any pytest-timeout failure for this node → `hung`
    - 100% non-pass (fail or skip-marked-as-fail) across all N runs, zero timeouts, zero collection errors → `real`
    - 1-99% non-pass across N runs, zero timeouts, zero collection errors → `flaky`

    **Precedence rationale**: A test that times out once and fails twice is `hung` (not `flaky`), because the fix surface is different — a hang is about a wedge/deadlock, a flaky is about determinism. This precedence is documented inline in the classifier and asserted by unit tests at each boundary.

    N=3 is the minimum needed to distinguish 1-of-3 flakiness from 3-of-3 determinism while keeping runtime tractable. Runtime budget: the full suite takes ~5-15 min of test execution on main (not counting timeouts); adding per-test timeouts can only shorten, not lengthen, the run. So 3 runs ≈ 15-45 min — acceptable for a tool that runs rarely.

3. **Refresh tool surface**: `scripts/refresh_test_baseline.py` (matches the pattern of `scripts/nightly_regression_tests.py` and `scripts/sdlc_reflection.py`). Arguments:
    - `--runs N` (default 3) — number of pytest invocations
    - `--output PATH` (default `data/main_test_baseline.json`; in `--dry-run` mode the default is `-` meaning stdout, so an accidental drop of `--dry-run` does not silently overwrite the live baseline)
    - `--test-timeout SECONDS` (default 60) — per-test timeout for classifying `hung`, passed through to pytest-timeout
    - `--global-timeout SECONDS` (default: `test_timeout × 3 × estimated_test_count`, capped at 7200s / 2 hours) — wall-clock cap on each pytest invocation as a safety net
    - `--merge` — preserve `note` fields from existing file if present (otherwise overwrite)
    - `--dry-run` — run pytest, print the classification summary, don't write the on-disk baseline
    - `--verbose` — log each pytest invocation

    Rationale: put it in `scripts/` because it's a developer/automation tool, not imported by the bridge or worker. `tools/` is reserved for code called by the agent via MCP. `--test-timeout 60` (not 120) is chosen because tests/unit's slowest typical test runs in ~8s (pytest-xdist ceiling on local dev) and any single test taking >60s is almost certainly wedged — a lower default catches real hangs faster while still being forgiving. Devs can raise it with `--test-timeout 120` for slow integration suites. `--global-timeout` exists to catch the edge case where pytest-timeout's thread method fails (e.g., C-extension ignoring the signal).

4. **Merge-gate comparison logic** (see Data Flow step 5):
    - The gate uses an inline `python3 - <<'PY'` heredoc block in `.claude/commands/do-merge.md` (not a standalone module) for a first-cut implementation. This keeps the diff to `do-merge.md` self-contained and readable; no new importable module is introduced that only `do-merge.md` uses. A later refactor can extract the block into `scripts/refresh_test_baseline.py` as a reusable function — see §4a below.
    - The block produces four structured outputs (JSON to stdout, consumed by surrounding shell): `new_blocking_regressions` (list of node IDs), `new_flaky_occurrences` (list), `preexisting_failures_present` (count), `baseline_keys_no_longer_failing` (advisory list).
    - The gate blocks if `len(new_blocking_regressions) > 0`.
    - **Staleness warning**: if `generated_at` is more than 14 days old at merge time, the gate prints a non-blocking WARNING line: "Baseline is N days old — consider `python scripts/refresh_test_baseline.py`". This is the primary consumer of the `generated_at` field.

5. **Bootstrap path (no baseline file present)**:
    - Current `/do-merge` already has this path: if `data/main_test_baseline.json` is missing and tests fail, write the current failure list as the new baseline and allow the merge.
    - Updated bootstrap writes schema v2 with every failing test categorised as `real`, plus a top-level flag `"bootstrap": true` and a prominent inline comment in the output explaining that a real refresh should follow. This field is read by the staleness warning in §4: a `bootstrap: true` baseline always warns.
    - A bootstrap baseline is strictly a stop-gap; the first `python scripts/refresh_test_baseline.py` run overwrites it (dropping the `bootstrap` flag) and properly categorises. This is documented in `docs/features/merge-gate-baseline.md`.

6. **Merge-gate runtime imports from refresh tool**:
    - The merge gate's Python block implements the minimum logic (load baseline, diff sets, emit JSON) inline to avoid the markdown skill depending on an importable module. If future work shows duplication pain (the Python block in `do-merge.md` and the classifier in `refresh_test_baseline.py` drift), extract a shared module to `scripts/baseline_lib.py` and have both import from it. Out of scope for this plan.

7. **Migration**:
    - Merge gate: if loaded JSON has `failing_tests` key (and no `tests` key), treat as legacy. In-memory promote each entry to `{"category": "real", "fail_rate": 1.0, "hung_count": 0}`. No file write from the merge gate — only the refresh tool upgrades the on-disk format.
    - Refresh tool: on first run against a legacy file, write the v2 shape.
    - No version sunset — legacy load stays in place as a thin compatibility path. If a dev never runs the refresh tool, the gate still works (degraded — everything is `real`, no `flaky` pass-through available).

### Migration Detection Rule

- A file with `"schema_version": 2` AND a `"tests"` object → v2
- A file with a `"failing_tests"` array and no `"schema_version"` → v1 (legacy)
- An empty file, `{}`, or malformed JSON → treat as no baseline (existing bootstrap path)
- Any other shape (e.g., `schema_version: 3`, unrecognised) → log a warning to stderr, treat as no baseline, bootstrap writes a fresh v2 on next refresh

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

- [ ] `tests/unit/test_do_merge_baseline.py` (create) — NEW file for the categorised baseline logic. Covers: legacy-shape load, v2 load, new-regression detection, flaky-occurrence pass-through, `hung` passthrough, staleness warning, count-coincident regression test (simulated PR #1054/#1070 scenario).
- [ ] `tests/unit/test_refresh_test_baseline.py` (create) — NEW file for the refresh tool. Covers: N-run aggregation, fail-rate classifier boundaries (0%, 1-of-3, 2-of-3, 3-of-3), classifier precedence (2 fails + 1 timeout → `hung`, not `flaky`), timeout → `hung`, collection error → `import_error`, `--merge` preserves notes, `--dry-run` defaults output to stdout, outer `--global-timeout` discards the stuck run.
- [ ] No existing tests affected — `/do-merge`'s baseline handling has no current unit tests (it's embedded in the markdown skill), and PR #484's test infrastructure (`tests/unit/test_baseline_verifier*.py` if present) is a different layer. No UPDATE/DELETE/REPLACE actions on existing tests. The regression tests are additive.
- [ ] No existing test is expected to regress from adding `pytest-timeout` as a dep (validated in Step 5: `pytest tests/unit/ -q` runtime unchanged within 5%). If regressions appear, they are blockers — the critique's BLOCKER fix must not break the existing suite.

## Rabbit Holes

- **Centralising the baseline in git**: Tempting ("then it's not per-machine!") but #1084's `Dropped` bucket explicitly ruled it out. `data/` is ignored for good reasons (transient artifacts, privacy). A shared baseline is a separate feature with different tradeoffs — leave it.
- **Running the refresh tool automatically as a nightly cron**: Sounds efficient but pytest's full suite takes 5-15 min × 3 runs = 15-45 min of machine time. Not worth automating until we measure how often the baseline actually drifts. Ship the tool first, measure, automate later if warranted.
- **Integrating with PR #484's branch-side retry**: Tempting to share code, but the two live at different stages (PR branch at `/do-test` vs. `main` at `/do-merge`). The refresh tool runs on `main` without regard to a PR; sharing would couple them prematurely. Keep the implementations cleanly separate even if some parser code is visually similar.
- **LLM-based categorisation**: The tool could ask Claude to classify "is this failure real or flaky?" but deterministic fail-rate thresholds are better: they are reproducible, cheap, and have no hidden failure modes. Use LLMs only where judgment is needed; fail rate is not judgment.
- **Repo-wide `pytest-timeout` integration**: The plugin IS added as a dev dep (required for per-test `hung` classification — see Technical Approach §2), but it is NOT registered in `[tool.pytest.ini_options]` and NOT added to the default pytest addopts. It only activates when invoked by the refresh tool via `-p pytest_timeout --timeout=N`. This keeps default pytest behavior unchanged everywhere else (`/do-test`, local dev, CI). Going any further — e.g., adding a default timeout to every test across the repo — is a separate change that would require auditing every slow test and is out of scope.
- **Using `subprocess.run(timeout=...)` as the per-test timeout mechanism**: This was the original plan, flagged as a BLOCKER by the critique: `subprocess.run(timeout=...)` raises `TimeoutExpired` and kills the entire pytest process, so the outer collector sees an exception and cannot classify per-node. No path to a `hung` category exists with that mechanism without looping pytest per-test (rejected for runtime reasons — ~1500 tests × 3 runs × ~1s startup = 75 min overhead). Replaced with `pytest-timeout` plus an outer wall-clock safety net.

## Risks

### Risk 1: N=3 runs may misclassify low-rate flakes as `real`

**Impact:** A test that fails 5% of the time would pass all 3 runs ~86% of the time (would classify as not-failing, won't appear in baseline); but if it happens to fail once in 3, it classifies as `flaky` correctly. The real risk is the opposite: a test that fails 30% of the time could fail 3/3 (probability ~2.7%) and classify as `real`. A PR that causes this test to pass would go unblocked.

**Mitigation:** Ship with `--runs 3` default but expose `--runs` as configurable. Document in `tests/README.md` that `--runs 5` is safer when you suspect the baseline is noisy. The classifier also records `fail_rate` in each entry, so post-hoc tuning is possible. Low-rate flakes misclassified as `real` fail **closed** (block merges that shouldn't block) rather than open (allow regressions) — the safer direction.

### Risk 2: Schema v2 migration introduces a silent regression during the transition

**Impact:** If the migration logic in `/do-merge` is wrong, a running system might pass a regression through during the migration window.

**Mitigation:** Migration is **read-only** in the merge gate (no file writes from the gate itself). The refresh tool is the only upgrader. This means rollback is trivial: revert the `/do-merge` markdown change and the legacy flat file (if present) continues working as before. The regression test covers both shapes explicitly.

### Risk 3: A test is genuinely flaky AND a PR genuinely regresses it

**Impact:** The gate would see the test failing on the PR, find it in the `flaky` bucket of the baseline, and allow the merge. A real regression slips through because the test was already known-flaky.

**Mitigation:** This is a known limitation of any fail-rate-based classifier. Document it in `tests/README.md`. The alternative — always blocking on flaky tests — would force PRs to retry indefinitely. Recommended practice: a test that becomes a recurring source of confusion should be moved to `@pytest.mark.flaky` with a retry plugin, or quarantined entirely, rather than kept in the baseline. This is out of scope for the plan but worth noting.

### Risk 4: Refresh-tool runtime is prohibitive on CI

**Impact:** 15-45 min to refresh the baseline means developers won't run it, and staleness returns.

**Mitigation:** The tool is designed for manual periodic runs, not CI. `--runs 1` exists for "quick snapshot" usage (no flaky classification, everything ≥1 failure gets `real`). The canonical trigger is "after a large green-main cleanup." If this proves insufficient in practice, nightly automation is a straightforward follow-up (explicitly out of scope per No-Gos).

### Risk 5: `pytest-timeout` thread method fails on tests that ignore the signal

**Impact:** The thread-based timeout in pytest-timeout works by raising an async exception in the test thread. Tests that are blocked in a C extension (e.g., `time.sleep()` in a native call, a blocking socket read without a selectors-aware wrapper) can ignore the thread exception and keep running. In the worst case, the pytest process wedges indefinitely, and no `<failure>` entry is emitted — the test appears as "did not run" rather than `hung`.

**Mitigation:** The outer `--global-timeout` wall-clock wrapper catches the entire pytest invocation. When the outer timeout fires, the refresh tool discards that run's junitxml and logs a warning with the run index. If all N runs hit the outer timeout, exit non-zero without writing. This means a truly wedging test degrades the refresh to "inconclusive" (safe) rather than silent drop (unsafe). Documented in `docs/features/merge-gate-baseline.md`.

### Risk 6: Adding `pytest-timeout` as a dep affects CI runtime unexpectedly

**Impact:** Even though the plugin is only activated when explicitly invoked (`-p pytest_timeout --timeout=N`), import-time side effects could theoretically slow down regular pytest runs.

**Mitigation:** `pytest-timeout` has no default import-time registration; it hooks in only via `-p` or `[tool.pytest.ini_options].addopts`. Verified by running the existing `pytest tests/unit/` before and after adding the dep; both baseline measurements recorded in the build PR's description. If runtime regresses by more than 5%, roll back the dep and pick Option (c) per-test subprocess loop (documented but rejected for runtime reasons).

## Race Conditions

No race conditions identified. The refresh tool runs sequentially (N pytest invocations, one at a time). The merge gate reads the baseline file once before comparison and does not race with concurrent processes (each dev's worktree has its own file). Pytest-internal parallelism (`-n auto`) does not introduce concurrency at the baseline layer — each junitxml is produced by a single pytest process.

## No-Gos (Out of Scope)

- **Centralising the baseline file in git**: Explicitly called out in the issue's Dropped bucket. Defer to a separate future feature if a shared-baseline case emerges.
- **Automating refresh tool as a cron/launchd job**: Not worth it until we measure drift frequency. Ship manual, observe, automate later if warranted.
- **Replacing the flaky filter in `/do-test`**: That is PR #484's system and operates on a different layer. Merge-gate baseline and PR-branch flaky filter stay independent.
- **`pytest-timeout` registered in default pytest addopts**: The plugin IS added as a dev dep (required for per-test `hung` classification), but it is NOT registered in `[tool.pytest.ini_options].addopts`. Non-refresh-tool pytest invocations run exactly as before. Changing the default behavior — e.g., adding a default per-test timeout to every pytest run everywhere — is a separate, much larger change that would require auditing every slow test and is out of scope.
- **Categorising flaky tests by *cause* (LLM-judge vs. network vs. state leak)**: The four categories (real, flaky, hung, import_error) are sufficient. Finer-grained causes are a separate concern.
- **Auto-quarantining flaky tests**: Marking tests as skip when they enter the baseline's `flaky` bucket would silently reduce coverage. Explicit, manual quarantine (via `@pytest.mark.flaky` or `skip`) is the correct path.

## Update System

Minimal update-system impact:
- **New dev dep (`pytest-timeout`)**: propagated automatically by `scripts/update/env_sync.py` (or its equivalent in the `/update` skill) via `uv sync` after `git pull` lands the updated `pyproject.toml` / `uv.lock`. No changes to `scripts/remote-update.sh` or the `/update` skill itself are required — the dep-sync step already handles new `uv.lock` entries.
- **No runtime impact**: `pytest-timeout` is a test-time-only dep. The bridge, worker, and email bridge do not import it. No service restart is needed after the update.
- **No migration**: `data/main_test_baseline.json` is git-ignored and per-machine. Existing baselines (legacy flat shape) continue to work; the first `python scripts/refresh_test_baseline.py` run on each machine upgrades that machine's baseline to schema v2.

The updated `/do-merge` markdown is part of `.claude/commands/`, which is synced with the repo checkout on every `/update`.

## Agent Integration

No agent integration required — this is a developer/CI tooling change. The refresh tool lives in `scripts/`, not `tools/`. `/do-merge` is a Claude Code skill executed by the builder/merge agent, so the markdown-level changes are picked up via normal skill loading. No MCP changes, no `.mcp.json` edits, no bridge imports.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/merge-gate-baseline.md` describing: what the merge-gate baseline is, how it differs from the PR-branch flaky filter (#476), the schema v2 shape (every top-level field and its consumer), when to refresh, how to interpret the four categories, the classifier precedence order, the pytest-timeout scoping rule (dev dep, never registered in pytest addopts), the outer `--global-timeout` safety net, and Risk R5 (C-extension wedge).
- [ ] Add entry to `docs/features/README.md` index table under the Testing section.
- [ ] Update `tests/README.md` with a one-paragraph explainer distinguishing the merge-gate baseline from the #476 flaky filter (required by issue acceptance criteria), plus a sentence noting that pytest-timeout is available as a dev dep but only activates when the refresh tool invokes it.
- [ ] Update `.claude/commands/do-merge.md` — replace the Full Suite Gate section with the new inline Python-heredoc-based comparison, document the four categories, document the migration rule, document the staleness warning.
- [ ] Update the "Quick Commands" table in `CLAUDE.md` with a row for `python scripts/refresh_test_baseline.py`.

### Inline Documentation
- [ ] Module-level docstring in `scripts/refresh_test_baseline.py` explaining purpose, default arguments, and intended invocation pattern.
- [ ] Inline comment block at the top of the modified Full Suite Gate in `.claude/commands/do-merge.md` pointing to `docs/features/merge-gate-baseline.md`.

## Success Criteria

- [ ] `data/main_test_baseline.json` supports schema v2 (map keyed by node ID, categories: `real`, `flaky`, `hung`, `import_error`) with all top-level metadata fields documented and consumed.
- [ ] `scripts/refresh_test_baseline.py` exists and regenerates the file from N pytest runs, using `pytest-timeout` for per-test hang detection, classifying automatically with explicit precedence (`import_error` > `hung` > `real` > `flaky`).
- [ ] `pytest-timeout` is added as a dev dep in `pyproject.toml` and locked in `uv.lock`, but NOT registered in default pytest addopts.
- [ ] Running `pytest tests/unit/` (without the refresh tool's flags) does NOT apply per-test timeouts — verified in final validation.
- [ ] `/do-merge` full-suite gate treats new `flaky`-category failures as non-blocking and new `real`/`hung`/`import_error` failures as blocking.
- [ ] A regression test demonstrates that a PR introducing a genuine regression masked by a count-coincident flaky failure is blocked (simulates the PR #1054/#1070 scenario).
- [ ] A second regression test demonstrates the `hung` category is reachable and classified correctly end-to-end (resolves the critique BLOCKER).
- [ ] Both the refresh tool and `/do-merge` accept the legacy `{"failing_tests": [...]}` flat schema (promoted to `real` in memory).
- [ ] `/do-merge` emits a staleness WARNING when the baseline's `generated_at` is more than 14 days old or `bootstrap: true`.
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

### 0. Add pytest-timeout dev dependency
- **Task ID**: add-pytest-timeout-dep
- **Depends On**: none
- **Validates**: `python -c "import pytest_timeout"` exits 0; `uv lock --check` exits 0.
- **Assigned To**: baseline-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `"pytest-timeout>=2.3"` to the pytest extras in `pyproject.toml` (alongside `pytest-asyncio`, `pytest-xdist`, `pytest-json-report`).
- Do NOT add pytest-timeout to `[tool.pytest.ini_options].addopts` — it must not activate on regular pytest runs.
- Run `uv lock` to update `uv.lock`.
- Commit both files in a single commit titled `chore: add pytest-timeout dev dep for merge-gate baseline`.

### 1. Implement refresh tool core
- **Task ID**: build-refresh-tool
- **Depends On**: add-pytest-timeout-dep
- **Validates**: `tests/unit/test_refresh_test_baseline.py` (create)
- **Informed By**: Prior Art #476/#484 (junitxml parsing pattern)
- **Assigned To**: baseline-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/refresh_test_baseline.py` with argparse for `--runs`, `--output`, `--test-timeout`, `--global-timeout`, `--merge`, `--dry-run`, `--verbose`. Default `--output`: `data/main_test_baseline.json` in normal mode, `-` (stdout) in `--dry-run` mode.
- Implement the N-run orchestrator: for i in range(N), invoke `pytest tests/ -q --tb=no --junitxml=/tmp/baseline-run-{i}.xml -p pytest_timeout --timeout={test_timeout} --timeout-method=thread`. Wrap each pytest invocation in `subprocess.run(timeout=global_timeout)` as a safety net; on `TimeoutExpired` discard that run's junitxml and continue with the remaining runs.
- Implement the junitxml aggregator: parse each XML with `xml.etree.ElementTree`, for each `<testcase>` read `classname::name` as node ID and inspect children. Distinguish `<failure message="Timeout ..."/>` (from pytest-timeout) from regular `<failure>` and `<error>`. Aggregate per-node-ID: pass/fail/timeout/collection-error counts across the surviving runs.
- Implement the classifier with explicit precedence order (first match wins): `import_error` (any collection error) → `hung` (any pytest-timeout failure) → `real` (100% non-pass, zero timeouts, zero collection errors) → `flaky` (1-99% non-pass, zero timeouts, zero collection errors). Include assert-based unit tests at each boundary.
- Implement the writer: emit schema v2 JSON with all required top-level fields (`schema_version: 2`, `generated_at` via `datetime.now(timezone.utc).isoformat()`, `generated_by` recording the full argv, `runs`, `commit` via `git rev-parse --short HEAD`, `tests` map). Support `--merge` to preserve `note` fields from the existing file.
- If all N runs hit the outer `--global-timeout`, log an error and exit non-zero without writing.

### 2. Update /do-merge comparison logic
- **Task ID**: update-merge-gate
- **Depends On**: add-pytest-timeout-dep (can run in parallel with build-refresh-tool after dep task lands)
- **Validates**: `tests/unit/test_do_merge_baseline.py` (create)
- **Assigned To**: baseline-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace the shell-based `comm -23` block in `.claude/commands/do-merge.md` (lines ~226-284) with an inline `python3 - <<'PY'` heredoc block that: loads the baseline (auto-promoting legacy shape; bootstrap if missing), parses `/tmp/pr_run.xml` for PR failures, computes four output buckets (`new_blocking_regressions`, `new_flaky_occurrences`, `preexisting_failures_present`, `baseline_keys_no_longer_failing`), emits the verdict. Blocks on any non-empty `new_blocking_regressions`.
- Update the PR-branch pytest invocation to `pytest tests/ -q --tb=no --junitxml=/tmp/pr_run.xml` (no pytest-timeout — merge-gate does not classify hangs).
- Add the staleness warning: if the loaded baseline's `generated_at` is more than 14 days old (or the file has `"bootstrap": true`), print a non-blocking WARNING.
- Preserve the existing bootstrap path (missing file → write current failures as baseline). Update the bootstrap writer to emit schema v2 with everything categorised as `real` (a single run can't distinguish real from flaky), plus `"bootstrap": true` at the top level.
- Preserve the post-merge reset path; update it to write `{"schema_version": 2, "generated_at": "<iso-utc>", "runs": 0, "commit": "<sha>", "tests": {}}`.
- Add an inline comment block at the top of the modified gate pointing to `docs/features/merge-gate-baseline.md`.

### 3. Regression tests for count-coincidence scenario
- **Task ID**: build-regression-test
- **Depends On**: update-merge-gate, build-refresh-tool
- **Validates**: `tests/unit/test_do_merge_baseline.py::test_count_coincident_regression_is_blocked` and `::test_hung_baseline_entry_is_preexisting`
- **Assigned To**: baseline-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- **Test 1 (count-coincidence)**: Construct a baseline fixture with `{flaky_test_A: {"category": "flaky"}, real_test_B: {"category": "real"}}`. Construct a PR-failing-set fixture: `{real_test_B, real_test_C}` — count is 2, matches baseline count of 2, but `real_test_C` is genuinely new. Assert the comparison classifies `real_test_C` as `new_blocking_regression` and that the gate decision is BLOCK. Second assertion: the PR failing set `{flaky_test_A}` alone is reported as non-blocking (flaky re-occurrence).
- **Test 2 (hung passthrough)**: Construct a baseline with `{hung_test_X: {"category": "hung"}}`. PR failing set `{hung_test_X}`. Assert the gate emits "preexisting" and does not block — proves the `hung` category is treated as pre-existing (critique's BLOCKER resolution path verified end-to-end).
- **Test 3 (classifier precedence)**: Give the classifier a test with 2 failures + 1 timeout across 3 runs. Assert it classifies as `hung`, not `flaky` — proves the precedence order documented in Technical Approach §2.

### 4. Write feature documentation
- **Task ID**: document-feature
- **Depends On**: build-refresh-tool, update-merge-gate, build-regression-test
- **Assigned To**: baseline-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/merge-gate-baseline.md` describing the system end to end: schema v2, four categories with examples, pytest-timeout scoping rule, refresh tool CLI, merge-gate comparison flow, staleness warning, bootstrap path, legacy migration, risks (R5 C-extension wedge, R1 low-rate flakes).
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
- Run the refresh tool with `--dry-run --runs 1 --test-timeout 30` on a small subset (e.g., `--tests tests/unit/test_sdlc_skill_md_parity.py`) → confirm the tool emits valid schema-v2 JSON to stdout without writing `data/main_test_baseline.json`.
- Run `uv lock --check` → exit 0 (confirms pytest-timeout is locked).
- Confirm that running `pytest tests/unit/ -q` (without the refresh tool's `-p pytest_timeout` flag) does NOT apply timeouts — `import pytest_timeout; pytest_timeout.is_debugging()` returns falsy in the absence of `--timeout` (verifies the scoping guarantee in Risk R6).
- Verify all Success Criteria checkboxes are checkable from artifacts.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Refresh-tool tests pass | `pytest tests/unit/test_refresh_test_baseline.py -q` | exit code 0 |
| Merge-gate tests pass | `pytest tests/unit/test_do_merge_baseline.py -q` | exit code 0 |
| Regression scenario blocks | `pytest tests/unit/test_do_merge_baseline.py::test_count_coincident_regression_is_blocked -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/refresh_test_baseline.py tests/unit/` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/refresh_test_baseline.py tests/unit/` | exit code 0 |
| Refresh tool dry-run to stdout | `python scripts/refresh_test_baseline.py --dry-run --runs 1` | exit code 0; valid JSON on stdout; `data/main_test_baseline.json` unchanged |
| pytest-timeout not default | `pytest tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0; no timeout applied |
| pytest-timeout via refresh tool | `python scripts/refresh_test_baseline.py --dry-run --runs 1 --test-timeout 30` | exit code 0; junit shows `<timeout>` for any >30s test |
| uv.lock synced | `uv lock --check` | exit code 0 |
| Feature doc exists | `test -f docs/features/merge-gate-baseline.md` | exit code 0 |
| tests/README.md updated | `grep -q 'merge-gate baseline' tests/README.md` | exit code 0 |

## Critique Results

First critique cycle: **NEEDS REVISION** — 1 blocker, 6 concerns, 3 nits. Revision recorded here.

### BLOCKER: `hung` category unreachable with `subprocess.run(timeout=...)`

**Finding**: When pytest hits a subprocess-level timeout, `subprocess.run` raises `TimeoutExpired` and the outer collector sees an exception, not a classifiable per-node `hung` outcome. The whole pytest process is killed; no junitxml is written for that run; no single test is tagged as having hung. With only a process-level timeout, `hung` as a per-test category is literally unreachable — the original plan claimed the category existed but had no mechanism to produce it.

**Options considered**:
- (a) `pytest-timeout` plugin — per-test in-process timeouts; emits `<failure message="Timeout ...">` per node in junitxml.
- (b) Drop the `hung` category and fold into `fail` with a marker — simple but **violates issue acceptance criteria**, which explicitly require `hung` as one of four categories.
- (c) Loop pytest per-test wrapped in `subprocess.run(timeout=...)` — works, but ~1500 tests × 3 runs × ~1s startup ≈ 75 min of pure overhead, making the tool unusable.

**Chosen: (a)**. Add `pytest-timeout` as a dev dep, activate only in the refresh tool's invocation (`-p pytest_timeout --timeout=N`), leave default pytest behavior unchanged everywhere else. Keeps the four-category contract while making `hung` deterministically reachable. The "repo-wide pytest-timeout integration" No-Go is preserved — the plugin is dep-level only, never registered in pytest addopts. An outer `subprocess.run(timeout=global_timeout)` wall-clock wrapper provides a safety net for the rare C-extension-ignores-the-signal case (Risk R5).

All references to `subprocess.run(timeout=...)` as the per-test mechanism have been purged from the plan. Data Flow §2-§3, Technical Approach §2, Risks R5 and R6, and Step-by-Step Tasks §0-§1 all reflect the chosen design.

### CONCERNS (6)

1. **`hung` vs. `flaky` classifier precedence** — A test with 2 fails + 1 timeout across 3 runs could hit both buckets; the original classifier was ambiguous. **Addressed** in Technical Approach §2 with an explicit precedence order (`import_error` > `hung` > `real` > `flaky`, first match wins) and a regression test in Step §3 (`test_classifier_precedence`).
2. **Runtime claims vs. hang scenarios** — Original plan claimed 15-45 min for 3 runs, but a test that hangs at 120s timeout × N tests × 3 runs could blow that wildly. **Addressed**: lowered default `--test-timeout` to 60s (from 120s) and added `--global-timeout` (default: `test_timeout × 3 × estimated_test_count`, capped at 7200s) with documented semantics — outer timeout discards the run, doesn't fail-close.
3. **Bootstrap over-blocking** — When `/do-merge` bootstraps a baseline from a single PR run, everything becomes `real`, so legitimately flaky pre-existing failures become falsely-`real` and over-block future PRs. **Addressed**: bootstrap writer emits `"bootstrap": true` at the top level, and the gate's staleness warning always fires while this flag is set — prompting the dev to run the refresh tool with multiple runs for real categorisation.
4. **Merge-gate single run has no flaky retry** — A genuinely new test that is actually flaky will fail on its first PR run and get flagged as new regression. **Addressed** by acknowledging this is delegated to `do-test`'s existing retry infrastructure (per Issue #476 / PR #484). The merge gate is explicitly positioned as a set-diff oracle, not a flake detector, in Data Flow §2 (merge-gate path) and Risks R3.
5. **Python block location** — Original plan said "Python block (not shell `comm`)" but didn't specify inline heredoc vs. standalone module. **Addressed** in Technical Approach §4: inline `python3 - <<'PY'` heredoc in `.claude/commands/do-merge.md` is the chosen shape for v1. Extraction to a shared module is called out as future work in §6.
6. **Schema metadata as dead weight** — Original plan listed `generated_at`, `commit`, `runs` without showing a consumer. **Addressed** in Technical Approach §1: each field now has a documented consumer (`generated_at` → 14-day staleness warning; `commit` → reader confirmation; `runs` → future-refresh comparison; `generated_by` → human debugging). Added `hung_count` and optional `bootstrap` fields with explicit uses.

### NITS (3)

1. **`--test-timeout 120` default** — Lowered to 60s because typical slow tests in `tests/unit` run in ~8s. Devs with slow integration suites can pass `--test-timeout 120`.
2. **`--output` default in dry-run** — In `--dry-run` mode, `--output` now defaults to `-` (stdout), so accidentally dropping `--dry-run` does NOT silently overwrite the live baseline.
3. **ISO-8601 `Z` suffix** — Example JSON corrected from `...Z` to `+00:00` to match what `datetime.now(timezone.utc).isoformat()` actually produces.

---

## Open Questions

None. The five open questions from the issue's Solution Sketch are resolved in the Technical Approach section:

1. **Schema shape** — Map keyed by node ID with per-test category + metadata. Resolved in Technical Approach #1.
2. **Classification source** — N-run pytest with `pytest-timeout` per-test timeouts and fail-rate thresholds; defaults to N=3, configurable. Resolved in Technical Approach #2.
3. **Refresh tool surface** — `scripts/refresh_test_baseline.py` with `--runs`, `--output`, `--test-timeout`, `--global-timeout`, `--merge`, `--dry-run`, `--verbose`. Resolved in Technical Approach #3.
4. **Merge-gate comparison logic** — Inline `python3 - <<'PY'` heredoc in `.claude/commands/do-merge.md` that buckets PR failures by baseline category; blocks on new `real`/`hung`/`import_error`. Resolved in Technical Approach #4 and Data Flow step 5.
5. **Migration** — Read-only legacy promotion at load time; refresh tool is the only upgrader. Resolved in Technical Approach #7.

The critique's BLOCKER (hung category unreachable) is resolved via the `pytest-timeout`-scoped approach documented in Critique Results above.
