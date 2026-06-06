---
status: Planning
type: chore
appetite: Large
owner: Valor
created: 2026-06-06
tracking: https://github.com/tomcounsell/ai/issues/1578
last_comment_id:
---

# Test Suite Cleanup — Zero Failures on `main`

## Problem

This repo runs a full `pytest` suite of 8,300+ tests on every PR. `main` carries a
backlog of pre-existing failures that broke silently as features evolved. Until they are
resolved, the suite cannot serve as a regression gate: every PR must manually distinguish
its own regressions from the known-bad baseline, which is error-prone and slow.

**Current behavior:** Multiple test clusters fail on `main` across six root-cause
categories (feature drift, real source bugs, env/install, flaky-under-parallelism, and
performance thresholds). Each PR must verify its failures are "pre-existing."

**Desired outcome:** `scripts/pytest-clean.sh tests/ -q` on `main` exits 0 with 0 failures
and 0 collection errors. Every failure on a PR is then a genuine regression introduced by
that branch.

## Freshness Check

**Baseline commit:** `8a6db88d`
**Issue filed at:** 2026-06-05T10:39:54Z
**Disposition:** Minor drift — the issue's core premise holds, but ~12 of the original 54
failures are **already resolved on current `main`**. The remaining ~37 failures + 1
collection error were re-verified as still failing. Categories and counts were re-baselined
by running each named cluster with `-n0` (serial) at plan time.

**Re-verified inventory (ground truth at `8a6db88d`):**

| Cat | Cluster | Issue count | Verified now | Disposition |
|-----|---------|-------------|--------------|-------------|
| A | `test_session_modal_liveness_render` | 6 | 6 FAIL | unchanged |
| A | `test_bridge_relay::TestSendQueuedMessage` | 5 | 5 FAIL | unchanged |
| A | `test_sdlc_skill_md_parity` | 3 | 3 FAIL | unchanged |
| A | `test_reflection_scheduler` | 4 | **3 FAIL** | drift: 3 not 4 |
| A | `test_model_relationships::test_enrichment_field_count` | 1 | 1 FAIL (20 vs 18) | unchanged |
| A | `test_long_task_checkpointing::test_progress_md_in_build_soft_check` | 1 | 1 FAIL | unchanged |
| B | `test_do_merge_review_filter` | 10 | **0 FAIL (15 PASS)** | RESOLVED |
| B | `test_markitdown_ingestion` | 1 | **0 FAIL (3 PASS)** | RESOLVED |
| C | `test_watchdog_recovery::TestWatchdogDetectsUnexpectedExit` | 4 | 4 FAIL | unchanged |
| C | `test_harness_oom_backoff` | 2 | 2 FAIL | unchanged |
| C | `test_health_check_recovery_finalization` | 4 | 4 FAIL | unchanged |
| D | `test_memory_mcp_server::test_fresh_shell_import_resolution` | 1 | **0 FAIL (PASS)** | RESOLVED (`mcp` installed) |
| D | `test_skills_audit` (collection error) | 1 | 1 collection ERROR | unchanged |
| E | `test_memory_ingestion::test_human_message_creates_memory` | 1 | PASS `-n0` | unchanged (flaky parallel-only) |
| E | `test_compose_system_prompt::test_pm_cell_byte_stable_against_local_fixture` | 1 | PASS `-n0` | unchanged (flaky parallel-only) |
| F | `test_memory_prefetch::test_prefetch_completes_under_budget` | 1 | not re-run (latency-sensitive) | verify at build |
| F | `test_benchmarks::TestEndurance::test_garbage_collection` | 1 | not re-run | verify at build |
| F | `test_doc_impact_finder_sdk::TestLiveHaikuReranking` | 4 | not re-run (live Haiku API) | verify at build |

**File:line references re-verified:**
- `tools/knowledge/indexer.py` `full_scan` — issue claimed "cannot import name 'full_scan'" — **gone**: `full_scan` is defined at `indexer.py:449` and imports cleanly. `test_markitdown_ingestion` passes.
- `.claude/commands/do-merge.md` — issue claimed tests read a worktree-only file — **drifted**: `test_do_merge_review_filter` now reads `docs/sdlc/do-merge.md` (REPO_ROOT/docs/sdlc/do-merge.md), which exists on `main`; all 15 tests pass. The "land `do-merge.md` or delete tests" product decision is **moot**.
- `agent/session_executor.py` (Category C reprieve/OOM source) — **drifted**: `_agent_session_health_check` now lives in `agent/session_health.py:1422`. The OOM-backoff test (`test_harness_oom_backoff`) and the reprieve-scoping tests (`test_health_check_recovery_finalization::TestReprieveScopedToNoProgress`) `inspect.getsource` of `agent/session_health.py`. The "Fallback finalization" sub-test still inspects `agent/session_executor.py`. Category C fixes target **both** modules.
- `config/reflections.yaml` — issue claimed `every: 300s` vs test expecting `interval: 300` — **confirmed**: yaml uses `every: 300s`; tests `KeyError: 'interval'`.

**Cited sibling issues/PRs re-checked:** Issue body cites no blocking sibling issues. Prior-art
search (below) surfaced PR #1082 and #1154 as the relevant precedents.

**Commits on main since issue was filed (touching referenced files):** 14 commits landed
(`8a6db88d` back to `3cfee3d8`). None directly target the failing test clusters in A/C/E/F;
the B and D-`mcp` resolutions predate the issue's recon snapshot or landed via unrelated dep
bumps (`mcp` became importable; `claude-agent-sdk`/`anthropic` bumps).

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/` was scanned; no
active plan touches the test suite cleanup area.

**Notes:** The net effect of the drift is **scope reduction** — Category B is entirely done
and one of two Category D items is done. The plan removes those from the work list and keeps
them only as verification line-items (assert they still pass after the rest lands). No
premise of the issue is invalidated; the suite still does not pass on `main`.

## Prior Art

- **PR #1082**: `test(#1041): clean up post-#1023/#1026/#1035 test drift (health/lifecycle/reflections/emoji)` —
  Direct precedent: the same class of work (test drift cleanup in health/lifecycle/reflections).
  Establishes the pattern of updating test assertions to match intended current source behaviour
  rather than weakening them. Reuse its approach for Category A.
- **PR #1154**: `feat(#1084): categorise merge-gate baseline + refresh tool` — Added
  `scripts/refresh_test_baseline.py` and merge-gate baseline categorisation. Relevant because once
  this plan lands, the test baseline should be refreshed so the merge gate stops counting these as
  known-bad (see Update System).
- **PR #1535 / #1545**: `SDLC Pipeline Portability` — Moved `/do-merge` to a portable skill that
  defers to `docs/sdlc/do-merge.md`. This is the change that resolved Category B by relocating the
  merge-filter content to a path the test already reads.

No prior attempt tried to drive the entire suite to zero failures; prior work was scoped to
individual drift clusters.

## Data Flow

Not applicable — this is a test-and-source-cleanup chore with no end-to-end runtime data flow.
Each category is an independent edit to either a test file (A, E) or a source module (C, D) plus
threshold/justification edits (F). The only cross-cutting "flow" is the merge-gate baseline, which
reads the post-fix passing suite (see Update System).

## Architectural Impact

- **New dependencies**: None. `mcp` is already importable (Category D-`mcp` is resolved); no new
  packages are added.
- **Interface changes**: Category C touches `agent/session_health.py` behaviour
  (watchdog heartbeat TTL, OOM-backoff capture ordering, reprieve scoping). These are
  internal-to-worker recovery semantics, not public APIs.
- **Coupling**: Unchanged. Edits are localized per category; categories deliberately do not share
  files, enabling parallel execution.
- **Data ownership**: Unchanged.
- **Reversibility**: High. Every change is a small, independently revertible test or source edit.

## Appetite

**Size:** Large

**Team:** Solo dev (lead) orchestrating parallel builders per category, plus a validator.

**Interactions:**
- PM check-ins: 1-2 (confirm Category F disposition — fix vs. justify thresholds)
- Review rounds: 1 (Category C source changes warrant code review; A/D/E/F are mechanical)

The coding is small per category; the bottleneck is breadth (6 categories) and the judgement call
on Category C (real source bugs must be fixed to match documented spec, not patched to silence).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `mcp` importable | `.venv/bin/python -c "import mcp"` | Category D-`mcp` is already resolved; guard against regression |
| Redis up | `.venv/bin/python -c "import redis; redis.Redis().ping()"` | Memory/session tests (C, E) require Redis |
| `ANTHROPIC_API_KEY` (Category F live tests) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | `test_doc_impact_finder_sdk::TestLiveHaikuReranking` calls live Haiku |

Run all checks: `python scripts/check_prerequisites.py docs/plans/test-suite-cleanup.md`

## Solution

### Key Elements

- **Category A — drift (update tests, do NOT change source):** Read current source/templates,
  update test assertions to the *intended* current behaviour. Six independent clusters.
- **Category B — RESOLVED:** No work. Add verification line-items asserting the clusters still pass.
- **Category C — real bugs (fix source to match documented spec):** Three source defects in the
  worker recovery path. Fix `agent/session_health.py` (and `agent/session_executor.py` for the
  fallback-finalization sub-check) so behaviour matches the spec the tests encode.
- **Category D — env/install:** `mcp` is resolved; fix the `audit_skills` import so
  `test_skills_audit` collects (resolve the conftest/import path).
- **Category E — flaky under xdist:** Isolate the two parallel-only failures via `xdist_group`
  marker or unique per-worker Redis key prefixes so they pass under `-n auto`.
- **Category F — performance/timing:** Re-run at build time; for each, either fix the slow path or
  justify a recalibrated threshold with an inline comment. Live-Haiku tests get a `skipif` guard
  when the API key/CI signal is absent rather than a weakened assertion.

### Flow

`pytest-clean.sh tests/` on main → today: N failures + 1 collection error → apply per-category
fixes (parallel) → re-run full suite → 0 failures, 0 errors → refresh merge-gate baseline.

### Technical Approach

**Category A (tests only):**
- `test_session_modal_liveness_render` (6): read the current dashboard session-modal template;
  update asserted CSS classes / conditional render output to match.
- `test_bridge_relay::TestSendQueuedMessage` (5): read `bridge/telegram_relay.py` current file-send
  API; update the 5 tests to the new contract (album send, file-only, backward-compat string path).
- `test_sdlc_skill_md_parity` (3): the SDLC SKILL.md Step 4 gained a parenthetical and now references
  the `next-skill` tool / blocked-output contract; update the parity test's expected strings to match
  the current `agent/sdlc_router.py` + SKILL.md.
- `test_reflection_scheduler` (3, not 4): tests expect `interval: 300`; `config/reflections.yaml`
  uses `every: 300s`. Update the tests to read the `every` key (parse `300s` → 300) — the yaml schema
  is intentional. Confirm the registry-integrity and pm-briefings tests align with the current schema.
- `test_model_relationships::test_enrichment_field_count` (1): `TelegramMessage._meta.field_names`
  is now 20; update the hardcoded `== 18` to `== 20` and document the two added enrichment fields.
- `test_long_task_checkpointing::test_progress_md_in_build_soft_check` (1): test reads
  `.claude/skills/do-build/SKILL.md`; canonical path is `.claude/skills-global/do-build/SKILL.md`.
  Update `REPO_ROOT / ".claude" / "skills" / "do-build"` → `"skills-global"`.

**Category C (source fixes):**
- Watchdog TTL (`test_watchdog_recovery::TestWatchdogDetectsUnexpectedExit`, 4): the watchdog reports
  `ok` after the worker is killed because the heartbeat TTL is too permissive (a stale heartbeat still
  reads healthy). Tighten the down-detection so a killed worker reports `down` immediately, matching
  the acceptance-criterion timing the tests assert.
- OOM backoff (`test_harness_oom_backoff`, 2): `_agent_session_health_check` in
  `agent/session_health.py` must capture `pre_bump_attempts = entry.recovery_attempts or 0` BEFORE the
  OOM-defer condition so the defer triggers only on OS kills (`pre_bump_attempts == 0`). Add the capture
  line and ordering; the test asserts bytecode references `pre_bump_attempts` before the defer condition.
- Reprieve scoping (`test_health_check_recovery_finalization::TestReprieveScopedToNoProgress` +
  `TestRecoveryAttempts`, 4): the Tier 1/Tier 2 reprieve block must be gated on
  `_reason_kind == "no_progress"` so a reprieve is not granted for all recovery types. Add the gating
  condition, the `tier1_flagged_total` increment under that gate, and the degraded-Tier-2 debug log
  ("Tier 2 reprieve will only see compaction state") when the handle is None. Source: `agent/session_health.py`.

**Category D:**
- `test_skills_audit` collection error: `ModuleNotFoundError: No module named 'audit_skills'`. Resolve
  the import — add the audit-skills source dir to the test path (conftest `sys.path` insert or a proper
  package import), matching how the module is actually shipped. Do not delete the test.

**Category E:**
- Add `@pytest.mark.xdist_group(name="...")` to co-locate the colliding tests on one worker, or give each
  test a unique per-worker Redis key prefix (e.g. derive from `PYTEST_XDIST_WORKER`). Prefer unique key
  prefixes for `test_memory_ingestion` (Redis collision) and an `xdist_group` for
  `test_compose_system_prompt` (shared fixture / ordering).

**Category F:**
- Re-run all three clusters at build start. For `test_memory_prefetch` and `test_benchmarks`: profile;
  if the slow path is genuine, fix it; otherwise recalibrate the threshold with an inline comment citing
  the measured value and the headroom rationale. For `test_doc_impact_finder_sdk::TestLiveHaikuReranking`:
  add a `skipif` guard when the live key/CI signal is absent; if the response format changed, update the
  assertion to the current contract — never weaken to a tautology.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Category C touches recovery/finalization branches that swallow or log exceptions; each modified
      branch must keep its observable signal (logger.warning / metric / state change) and the tests
      assert those signals (e.g. `tier1_flagged_total` increment, degraded-Tier-2 debug log).
- [ ] No new `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] Category C OOM path: confirm `pre_bump_attempts == 0` handles the `recovery_attempts is None`
      case via `or 0` (the capture line being added).
- [ ] No agent-output processing changes; no silent-loop surface touched.

### Error State Rendering
- [ ] Category A `test_session_modal_liveness_render` covers the ghost/unknown/alive PID render
      branches — update tests to keep asserting the error/ghost rendering paths, not just the alive case.

## Test Impact

- [ ] `tests/unit/test_session_modal_liveness_render.py` (6 cases) — UPDATE: assert current template CSS/conditional output.
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage` (5 cases) — UPDATE: assert current file-send API contract.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` (3 cases) — UPDATE: expected strings to match current SKILL.md Step 4 + router.
- [ ] `tests/unit/test_reflection_scheduler.py` (3 cases) — UPDATE: read `every` key (parse `Ns`) instead of `interval`.
- [ ] `tests/unit/test_model_relationships.py::TestTelegramMessageEnrichmentFields::test_enrichment_field_count` — UPDATE: `== 18` → `== 20`.
- [ ] `tests/unit/test_long_task_checkpointing.py::test_progress_md_in_build_soft_check` — UPDATE: path `skills` → `skills-global`.
- [ ] `tests/integration/test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit` (4 cases) — KEEP (source fix makes them pass).
- [ ] `tests/unit/test_harness_oom_backoff.py` (2 cases) — KEEP (source fix makes them pass).
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestReprieveScopedToNoProgress` + `::TestRecoveryAttempts` (4 cases) — KEEP (source fix makes them pass).
- [ ] `tests/unit/test_skills_audit.py` — KEEP: fix `audit_skills` import so the module collects.
- [ ] `tests/unit/test_memory_ingestion.py::test_human_message_creates_memory` — UPDATE: add unique per-worker Redis key prefix.
- [ ] `tests/unit/test_compose_system_prompt.py::test_pm_cell_byte_stable_against_local_fixture` — UPDATE: add `xdist_group` marker.
- [ ] `tests/integration/test_memory_prefetch.py::test_prefetch_completes_under_budget` — UPDATE: fix slow path or recalibrate threshold with comment.
- [ ] `tests/performance/test_benchmarks.py::TestEndurance::test_garbage_collection` — UPDATE: fix or recalibrate with comment.
- [ ] `tests/integration/test_doc_impact_finder_sdk.py::TestLiveHaikuReranking` (4 cases) — UPDATE: add `skipif` guard / align to current response contract.
- [ ] `tests/unit/test_do_merge_review_filter.py` (15 cases) — NO CHANGE: already passing (Category B resolved); verify-only.
- [ ] `tests/integration/test_markitdown_ingestion.py` (3 cases) — NO CHANGE: already passing (Category B resolved); verify-only.

## Rabbit Holes

- **Re-architecting the watchdog heartbeat system.** Category C asks only to tighten the down-detection
  TTL so a killed worker reports `down`. Do not redesign the heartbeat protocol.
- **Refactoring `_agent_session_health_check`.** It is a large function; the OOM and reprieve fixes are
  surgical line additions/orderings. Resist a broader cleanup — it would explode review surface.
- **Chasing Category F into a profiling project.** If a threshold is genuinely close, recalibrate with a
  documented measurement. Only fix the slow path if it is clearly pathological.
- **Deleting tests to reach zero.** The issue constraint forbids weakening assertions; deleting a failing
  test to "pass" is the same sin. `test_skills_audit` and Category C tests must be made to pass, not removed.
- **Re-litigating Category B.** It is resolved; do not reopen the "land do-merge.md vs delete tests" debate.

## Risks

### Risk 1: Category C source fixes change recovery behaviour in production
**Impact:** Tightening watchdog TTL or reprieve scoping could make the worker more aggressive about
declaring sessions down / less forgiving on recovery, affecting live session reliability.
**Mitigation:** Make changes match the documented spec the tests already encode (these are
under-implementations of agreed behaviour, not new policy). Route Category C through a code-review round.
Run the full `test_watchdog_recovery` and `test_health_check_recovery_finalization` suites, not just the
failing subset, to confirm no neighbouring behaviour regresses.

### Risk 2: Fixing flaky tests (E) masks a real concurrency bug
**Impact:** Forcing `xdist_group` could paper over a genuine shared-state defect.
**Mitigation:** For `test_memory_ingestion`, prefer unique per-worker Redis key prefixes (fixes the actual
collision) over serializing; only use `xdist_group` where the shared resource is a test fixture, not product state.

### Risk 3: Category F live-Haiku tests are non-deterministic in CI
**Impact:** `skipif` guards could silently skip coverage; recalibrated thresholds could drift again.
**Mitigation:** Guard on an explicit signal (API key present AND not a known-CI marker), and document the
measured latency in the comment so the next drift is obvious. Surface the F disposition to the PM before finalizing.

## Race Conditions

### Race 1: Parallel Redis key collision in `test_memory_ingestion`
**Location:** `tests/unit/test_memory_ingestion.py::test_human_message_creates_memory`
**Trigger:** Two xdist workers write/read the same Redis key for the "human message creates memory" assertion.
**Data prerequisite:** Each worker's memory record must be keyed uniquely before the assertion reads it.
**State prerequisite:** No cross-worker key sharing.
**Mitigation:** Derive a per-worker key prefix from `PYTEST_XDIST_WORKER` (or use Popoto-scoped unique
project keys) so workers never touch the same key.

### Race 2: Shared fixture ordering in `test_compose_system_prompt`
**Location:** `tests/unit/test_compose_system_prompt.py::test_pm_cell_byte_stable_against_local_fixture`
**Trigger:** Non-deterministic ordering / shared fixture state under parallelism produces a byte-unstable prompt.
**Data prerequisite:** The local fixture must be read in a stable order.
**State prerequisite:** Test must not depend on global mutable state another worker mutates.
**Mitigation:** Pin the test to a single worker via `@pytest.mark.xdist_group`, or make the fixture read
deterministic (sorted iteration / isolated copy).

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. Category B and the Category D-`mcp`
  item are already resolved on `main` and are retained only as verify-only line-items, not deferred work.

## Update System

- **Merge-gate baseline refresh — [ORDERED] (must run after this PR merges):** The bootstrap test
  baseline (`scripts/refresh_test_baseline.py`, per PR #1154) is computed from `main`'s passing state,
  so it can only be refreshed AFTER this PR squash-merges (a human-gated merge event). Run
  `python scripts/refresh_test_baseline.py` as the first step once the PR has merged. Until refreshed,
  the stale baseline may false-positive "regressions" (see the known `data/merge_authorized_{N}` bypass).
- **No update script / `/update` skill changes** — no new dependencies, config files, or machine-level
  propagation. `mcp` is already present in the environment; no requirements change is introduced by this plan.

## Agent Integration

No agent integration required — this is a test-suite-and-source-cleanup chore. No new CLI entry point in
`pyproject.toml [project.scripts]`, no new MCP server or `.mcp.json` change, and the bridge does not need to
import anything new. Category C edits internal worker recovery code already invoked by the worker; no new
agent-reachable surface is added.

## Documentation

### Feature Documentation
- [ ] No new feature doc — this is cleanup. Instead, update `tests/README.md` if any blind-spot/marker
      notes reference the now-fixed clusters (e.g. remove any "known-failing on main" callouts).

### External Documentation Site
- [ ] N/A — repo has no external docs site for the test suite.

### Inline Documentation
- [ ] Category F: inline comment on any recalibrated threshold citing the measured value and headroom.
- [ ] Category C: brief comment on the watchdog TTL tightening and the `pre_bump_attempts` capture-ordering
      rationale (reference issue #1578 / the OOM-defer semantics).

## Success Criteria

- [ ] `scripts/pytest-clean.sh tests/ -q` on `main` exits 0 with 0 failures and 0 collection errors
- [ ] All Category A test files updated to match current source behaviour (no source changes for A)
- [ ] Category C source bugs fixed: watchdog down-detection TTL, OOM `pre_bump_attempts` capture ordering
      (`agent/session_health.py`), reprieve scoping gated on `_reason_kind == "no_progress"`
- [ ] `test_skills_audit` collects and passes (`audit_skills` import resolved)
- [ ] Flaky xdist tests pass under `-n auto` (unique Redis key prefixes / `xdist_group`)
- [ ] Category F: each threshold either fixed or justified with an inline comment; live-Haiku tests guarded
- [ ] Verify-only: `test_do_merge_review_filter` (15) and `test_markitdown_ingestion` (3) still pass
- [ ] No test deleted and no assertion weakened to a tautology (issue constraint)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] [ORDERED] Merge-gate baseline refreshed once this PR merges (`scripts/refresh_test_baseline.py`)

## Team Orchestration

The lead orchestrates one builder per category (A, C, D, E, F run in parallel — they touch disjoint files),
then a validator runs the full suite. Category B has no builder (verify-only).

### Team Members

- **Builder (category-A-drift)**
  - Name: builder-a-drift
  - Role: Update Category A test assertions to current source behaviour (6 clusters, tests only)
  - Agent Type: builder
  - Resume: true

- **Builder (category-C-bugs)**
  - Name: builder-c-bugs
  - Role: Fix the three source defects (watchdog TTL, OOM capture ordering, reprieve scoping)
  - Agent Type: debugging-specialist
  - Resume: true

- **Builder (category-D-env)**
  - Name: builder-d-env
  - Role: Resolve `audit_skills` import so `test_skills_audit` collects
  - Agent Type: builder
  - Resume: true

- **Builder (category-E-flaky)**
  - Name: builder-e-flaky
  - Role: Isolate the two parallel-only flaky tests (unique Redis prefixes / xdist_group)
  - Agent Type: async-specialist
  - Resume: true

- **Builder (category-F-perf)**
  - Name: builder-f-perf
  - Role: Re-run perf/live clusters; fix slow paths or justify thresholds; guard live-Haiku tests
  - Agent Type: performance-optimizer
  - Resume: true

- **Validator (full-suite)**
  - Name: validator-suite
  - Role: Run the full suite to confirm 0 failures / 0 errors and verify B clusters still pass
  - Agent Type: validator
  - Resume: true

### Step by Step Tasks

### 1. Category A — feature drift (tests only)
- **Task ID**: build-a-drift
- **Depends On**: none
- **Validates**: tests/unit/test_session_modal_liveness_render.py, tests/unit/test_bridge_relay.py, tests/unit/test_sdlc_skill_md_parity.py, tests/unit/test_reflection_scheduler.py, tests/unit/test_model_relationships.py, tests/unit/test_long_task_checkpointing.py
- **Assigned To**: builder-a-drift
- **Agent Type**: builder
- **Parallel**: true
- Read current source/templates for each of the 6 clusters; update assertions to intended behaviour.
- Do NOT modify any source file — Category A is test-only.
- Run each file with `-n0` to confirm green.

### 2. Category C — real source bugs
- **Task ID**: build-c-bugs
- **Depends On**: none
- **Validates**: tests/integration/test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit, tests/unit/test_harness_oom_backoff.py, tests/unit/test_health_check_recovery_finalization.py
- **Assigned To**: builder-c-bugs
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Tighten watchdog down-detection TTL so a killed worker reports `down` immediately.
- Add `pre_bump_attempts = entry.recovery_attempts or 0` capture BEFORE the OOM-defer condition in `agent/session_health.py`.
- Gate Tier 1/Tier 2 reprieve on `_reason_kind == "no_progress"`; add `tier1_flagged_total` increment and degraded-Tier-2 debug log.
- Run the FULL `test_watchdog_recovery` and `test_health_check_recovery_finalization` suites (not just the failing subset) to confirm no neighbouring regressions.

### 3. Category D — env/install
- **Task ID**: build-d-env
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py
- **Assigned To**: builder-d-env
- **Agent Type**: builder
- **Parallel**: true
- Resolve `ModuleNotFoundError: No module named 'audit_skills'` (conftest `sys.path` insert or proper package import).
- Confirm the file collects and all its cases pass with `-n0`.

### 4. Category E — flaky under xdist
- **Task ID**: build-e-flaky
- **Depends On**: none
- **Validates**: tests/unit/test_memory_ingestion.py::test_human_message_creates_memory, tests/unit/test_compose_system_prompt.py::test_pm_cell_byte_stable_against_local_fixture
- **Assigned To**: builder-e-flaky
- **Agent Type**: async-specialist
- **Parallel**: true
- Give `test_memory_ingestion` a unique per-worker Redis key prefix (fix the real collision).
- Pin `test_compose_system_prompt` via `xdist_group` or make the fixture read deterministic.
- Confirm both pass under `-n auto` across repeated runs.

### 5. Category F — performance/timing
- **Task ID**: build-f-perf
- **Depends On**: none
- **Validates**: tests/integration/test_memory_prefetch.py::test_prefetch_completes_under_budget, tests/performance/test_benchmarks.py::TestEndurance::test_garbage_collection, tests/integration/test_doc_impact_finder_sdk.py::TestLiveHaikuReranking
- **Assigned To**: builder-f-perf
- **Agent Type**: performance-optimizer
- **Parallel**: true
- Re-run each cluster; for prefetch/GC, fix the slow path or recalibrate the threshold with an inline comment citing the measured value.
- For live-Haiku tests, add a `skipif` guard on absent key/CI and align assertions to the current response contract — no weakening.

### 6. Full-suite validation
- **Task ID**: validate-suite
- **Depends On**: build-a-drift, build-c-bugs, build-d-env, build-e-flaky, build-f-perf
- **Assigned To**: validator-suite
- **Agent Type**: validator
- **Parallel**: false
- Run `scripts/pytest-clean.sh tests/ -q`; confirm 0 failures and 0 collection errors.
- Verify Category B clusters (`test_do_merge_review_filter`, `test_markitdown_ingestion`) still pass.
- Confirm no source assertion was weakened to a tautology and no test was deleted.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Full suite green | `scripts/pytest-clean.sh tests/ -q` | exit code 0 |
| Category A green | `.venv/bin/python -m pytest -n0 tests/unit/test_session_modal_liveness_render.py tests/unit/test_bridge_relay.py tests/unit/test_sdlc_skill_md_parity.py tests/unit/test_reflection_scheduler.py tests/unit/test_model_relationships.py tests/unit/test_long_task_checkpointing.py -q` | exit code 0 |
| Category C green | `.venv/bin/python -m pytest -n0 tests/integration/test_watchdog_recovery.py tests/unit/test_harness_oom_backoff.py tests/unit/test_health_check_recovery_finalization.py -q` | exit code 0 |
| Category D collects | `.venv/bin/python -m pytest -n0 --collect-only tests/unit/test_skills_audit.py -q` | exit code 0 |
| Category B still green | `.venv/bin/python -m pytest -n0 tests/unit/test_do_merge_review_filter.py tests/integration/test_markitdown_ingestion.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Category F disposition:** For `test_memory_prefetch` (8.25s vs 5s budget) and the GC benchmark — do you
   want the slow path profiled and fixed, or is recalibrating the threshold with a documented measurement
   acceptable? Default assumption: recalibrate-with-comment unless the slow path is pathological.
2. **Live-Haiku tests:** OK to `skipif`-guard `TestLiveHaikuReranking` when no API key / in CI, rather than
   making them hard-required? Default assumption: yes, guard them.
3. **Scope confirmation:** Category B (11) and the Category D-`mcp` item (1) are already green on `main`. The
   plan treats them as verify-only. Confirm you don't want any additional hardening there.
