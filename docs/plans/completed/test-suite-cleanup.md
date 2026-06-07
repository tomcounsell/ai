---
status: Ready
type: chore
appetite: Large
owner: Valor
created: 2026-06-06
tracking: https://github.com/tomcounsell/ai/issues/1578
last_comment_id:
revision_applied: true
---

# Test Suite Cleanup — Zero Failures on `main`

## Problem

This repo runs a full `pytest` suite of 8,300+ tests on every PR. `main` carries a
backlog of pre-existing failures that broke silently as features evolved. Until they are
resolved, the suite cannot serve as a regression gate: every PR must manually distinguish
its own regressions from the known-bad baseline, which is error-prone and slow.

**Current behavior:** Multiple test clusters fail on `main` across six root-cause
categories (feature drift, post-refactor test drift, env/install, flaky-under-parallelism, and
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
| ~~C~~ → **E** | `test_watchdog_recovery::TestWatchdogDetectsUnexpectedExit` | 4 | 4 FAIL | **RECLASSIFIED**: test-isolation bug, not a source bug (see below) |
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
- `agent/session_health.py` (Category C reprieve/OOM logic) — **logic moved, tests not updated**: the OOM-defer and reprieve-scoping logic the Category C tests assert was extracted from `_agent_session_health_check` (now `agent/session_health.py:1422`) into the shared helper `_apply_recovery_transition` (`agent/session_health.py:1096`) by refactor #1270. The failing tests still `inspect.getsource(_agent_session_health_check)`, which only *calls* the helper, so the pinned substrings are absent. **Category C is therefore test-only re-pointing, not a source change** (verified: every asserted string is present in `_apply_recovery_transition`). The fallback-finalization sub-tests that inspect `_execute_agent_session` / `_recover_interrupted_agent_sessions_startup` already pass and need no change.
- **`monitoring/worker_watchdog.py` (watchdog cluster) — CORRECTED ROOT CAUSE:** The issue diagnosed the 4 `TestWatchdogDetectsUnexpectedExit` failures as "heartbeat TTL too permissive (132s-old heartbeat still healthy)." This is **wrong**. Verified at plan time: `check()` (`worker_watchdog.py:145`) returns `down` **only** when `_get_worker_pid()` returns `None`; the `HEARTBEAT_THRESHOLD` (=600s) branch is never reached when a PID is found. `_get_worker_pid()` (`worker_watchdog.py:119`) runs a **global** `pgrep -if "python -m worker"`. The test (`_spawn_fake_worker`) fabricates a process matching that pattern, kills it, and expects `down` — but on any machine where a **real worker is already running** (confirmed: PID 94409 = `python -m worker` on this box), the global pgrep matches the real worker, so `check()` returns `ok` and all 4 tests fail. This is a **test-isolation defect, not a source bug**: tightening the heartbeat TTL would not flip any of these 4 tests. Reclassified to **Category E** (environment/isolation). See the Category E technical approach for the fix.
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
- **Interface changes**: None. Category C is test-only re-pointing — the OOM-backoff and
  reprieve-scoping behaviour already exists in `_apply_recovery_transition`; no worker recovery
  semantics change. There is **no** non-test edit — the Category C tests import
  `_apply_recovery_transition` directly from `agent.session_health` (no re-export added).
- **Coupling**: Unchanged. Edits are localized per category; categories deliberately do not share
  files, enabling parallel execution.
- **Data ownership**: Unchanged.
- **Reversibility**: High. Every change is a small, independently revertible test or source edit.

## Appetite

**Size:** Large

**Team:** Solo dev (lead) orchestrating parallel builders per category, plus a validator.

**Interactions:**
- PM check-ins: 1-2 (confirm Category F disposition — fix vs. justify thresholds)
- Review rounds: 1 (all categories are test-only or mechanical; a light review confirms no assertions were weakened)

The coding is small per category; the bottleneck is breadth (6 categories) and the judgement call
on Category C (re-point the `inspect.getsource` calls to where #1270 moved the logic, adapting the
asserted strings/ordering to match — never weaken an assertion to silence a failure).

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
- **Category C — post-refactor test drift (update tests, do NOT change source):** **Verified at plan
  time:** the OOM-defer ordering and reprieve-scoping logic the tests assert *already exists in source* —
  it was extracted from `_agent_session_health_check` into the shared helper
  `agent/session_health.py::_apply_recovery_transition` (lines 1096–1419) by refactor #1270. The 6 tests
  fail only because they `inspect.getsource(_agent_session_health_check)`, which now merely *calls* the
  helper. Fix = re-point the `getsource` calls and adapt the asserted strings/ordering to the current
  split (see Technical Approach). The watchdog cluster was separately reclassified to E. **No source
  change** — adding the pinned lines to `_agent_session_health_check` would duplicate logic that already
  lives in the helper (violates NO-LEGACY/no-duplication).
- **Category D — env/install:** `mcp` is resolved; fix the `audit_skills` import so
  `test_skills_audit` collects (resolve the conftest/import path).
- **Category E — environment/isolation (test-only fixes):** Isolate tests that fail because of shared
  external state (parallelism OR a coexisting real worker). Three sub-clusters: (1) the watchdog
  `TestWatchdogDetectsUnexpectedExit` (4) — make the test track its **own** spawned PID instead of a
  global pgrep; (2) `test_memory_ingestion` Redis collision under xdist; (3) `test_compose_system_prompt`
  fixture-ordering under xdist. All Category E fixes are **test-only** — no source change.
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
- `test_sdlc_skill_md_parity` (3): **Precise root cause (verified at plan time):** all 3 failures stem
  from the test's `_step4_section()` helper doing an **exact-string** match on the heading
  `## Step 4: Dispatch ONE Sub-Skill` (`tests/unit/test_sdlc_skill_md_parity.py:41`), while the live
  SKILL.md heading is `## Step 4: Dispatch ONE Sub-Skill (or a Parallel-Safe Pair)` — the parenthetical
  suffix makes the locator return `""`, so all three tests fail at the `assert section` precondition
  *before* they ever check their real assertions (`sdlc-tool next-skill` reference, no hand-authored
  table, `blocked` output contract). **Fix = make the section-locator prefix-tolerant**, e.g. change the
  line-41 comparison from `line.strip() == "## Step 4: Dispatch ONE Sub-Skill"` to
  `line.strip().startswith("## Step 4: Dispatch ONE Sub-Skill")`. This is the minimal correct change: the
  three behavioural assertions are already satisfied by the current SKILL.md (it references
  `sdlc-tool next-skill`, contains no `| N |` dispatch rows, and documents the `blocked` JSON key). Do
  NOT rewrite the assertions to chase the heading text — fix the locator so the existing assertions run.
- `test_reflection_scheduler` (3, not 4): tests expect `interval: 300`; `config/reflections.yaml`
  uses `every: 300s`. Update the tests to read the `every` key (parse `300s` → 300) — the yaml schema
  is intentional. Confirm the registry-integrity and pm-briefings tests align with the current schema.
- `test_model_relationships::test_enrichment_field_count` (1): `TelegramMessage._meta.field_names`
  is now 20; update the hardcoded `== 18` to `== 20` and document the two added enrichment fields.
- `test_long_task_checkpointing::test_progress_md_in_build_soft_check` (1): test reads
  `.claude/skills/do-build/SKILL.md`; canonical path is `.claude/skills-global/do-build/SKILL.md`.
  Update `REPO_ROOT / ".claude" / "skills" / "do-build"` → `"skills-global"`.

**Category C (test-only re-pointing — 2 clusters, 6 failures):**

**Ground truth (verified at plan time against `agent/session_health.py`):** the asserted logic lives in
the helper `_apply_recovery_transition` (1096–1419), NOT in `_agent_session_health_check` (1422–1846)
which the failing tests inspect. Confirmed present in the helper: `pre_bump_attempts = entry.recovery_attempts or 0`
(1291), `getattr(entry, "exit_returncode", None) == -9` (1363), `and pre_bump_attempts == 0` (1364),
`_is_memory_tight()`, `timedelta(seconds=120)`, `update_fields=["scheduled_at", "recovery_attempts"]`,
`if reason_kind == "no_progress":` (1185), `tier1_flagged_total` (1189, exactly once), the degraded-Tier-2
log `"Tier 2 reprieve will only see compaction state"` (1182), `_tier2_reprieve_signal`, `reprieve_count`,
and `MAX_RECOVERY_ATTEMPTS`. So these are **post-refactor test drift** (same class as Category A), not
source bugs. **Do NOT add any of these lines to `_agent_session_health_check`** — they would duplicate
the helper.

- OOM backoff (`test_harness_oom_backoff`, 2 — `test_pre_bump_capture_ordering` line 80,
  `test_oom_defer_condition_grep_present` line 100): both `inspect.getsource(session_health._agent_session_health_check)`.
  **Fix = re-point both to `session_health._apply_recovery_transition`.** Every asserted substring and the
  `pre_bump_attempts ... < ... and pre_bump_attempts == 0` ordering already hold in the helper. No source edit.
- `TestRecoveryAttempts::test_health_check_source_mentions_recovery_attempts_and_max` (line 805): asserts
  `recovery_attempts`, `MAX_RECOVERY_ATTEMPTS`, `reprieve_count` in `q._agent_session_health_check`. **Fix =
  re-point to `_apply_recovery_transition`** (all three present there). **Prefer importing it directly from
  `agent.session_health`** in the test (`from agent.session_health import _apply_recovery_transition`) — do
  NOT add a new re-export to `agent_session_queue.py` (only `_agent_session_health_check` is re-exported
  there today; a new re-export would be a non-test edit for no benefit).
- `TestReprieveScopedToNoProgress` (3 — lines 995, 1019, 1047): all three assert a structural invariant
  (no_progress gate precedes / contains the tier1 counter and the reprieve signal, ahead of the kill path)
  that now lives **entirely inside `_apply_recovery_transition`**. **Verified at plan time** — the helper
  contains the gate `reason_kind == "no_progress"` (1185), `tier1_flagged_total` (1189, exactly once),
  `_tier2_reprieve_signal` (1193), `DISABLE_PROGRESS_KILL` (1217), and the degraded-Tier-2 log (1182), in
  that order. So the **correct target is the helper ALONE** — *not* a caller+helper concatenation (that
  fails: the caller's own `DISABLE_PROGRESS_KILL` precedes the helper's gate, collapsing
  `gated_section = src[gate_idx:kill_idx]` to empty). Per-test fixes:
  - `test_tier2_reprieve_only_applies_to_no_progress` (995): `getsource(_apply_recovery_transition)`; change
    the asserted gate string `_reason_kind == "no_progress"` → `reason_kind == "no_progress"`. The
    "defined-before-gated" check (`idx_kind < idx_gate`) re-anchors on the `reason_kind` **parameter** in the
    helper signature (line 1100), which precedes the gate — `reason_kind` is now classified by the caller and
    passed in, so its definition site is the signature. Keep `idx_gate < idx_tier1_counter` and
    `idx_gate < idx_reprieve` unchanged (both hold in the helper).
  - `test_tier1_flagged_metric_only_increments_for_no_progress` (1019): `getsource(_apply_recovery_transition)`;
    `count("tier1_flagged_total") == 1`, `gate_idx = find('reason_kind == "no_progress"')`,
    `kill_idx = find("DISABLE_PROGRESS_KILL")`, `gated_section = src[gate_idx:kill_idx]` contains the counter —
    all hold in the helper unchanged except the gate-string underscore.
  - `test_no_progress_handle_none_debug_log_present` (1047): `getsource(_apply_recovery_transition)`; the
    `"Tier 2 reprieve will only see compaction state"` log is present — pure re-point.
  Builder MUST `inspect.getsource` and re-run after each edit; do NOT weaken any assertion — re-point to the
  helper and adjust only the gate-string underscore + the `idx_kind` anchor.

**Category D:**
- `test_skills_audit` collection error: `ModuleNotFoundError: No module named 'audit_skills'`. Resolve
  the import — add the audit-skills source dir to the test path (conftest `sys.path` insert or a proper
  package import), matching how the module is actually shipped. Do not delete the test.

**Category E (environment/isolation — test-only, 3 sub-clusters, 6 failures):**
- **Watchdog (`TestWatchdogDetectsUnexpectedExit`, 4):** The test fails whenever a real `python -m worker`
  is running because `check()` → `_get_worker_pid()` does a **global** `pgrep -if "python -m worker"` that
  matches the real worker, not just the test's fabricated one. Fix the **test** (not the source — the
  global pgrep is correct production behaviour): patch `monitoring.worker_watchdog._get_worker_pid` within
  each test to return the fake worker's actual PID while it lives and `None` after it's killed (track the
  `proc.pid` from `_spawn_fake_worker` and check `proc.poll()`), OR `@pytest.mark.skipif` when a real
  worker is detected on the host. **Preferred:** mock `_get_worker_pid` to the spawned PID so the test is
  deterministic on every machine (including the worker box) — skipping silently drops coverage. The 4 tests
  then exercise the real `check()` down/ok/stale branch logic against a controlled PID. Confirm the timing
  assertions (`elapsed < MAX_DETECTION_LATENCY` = 130s) still hold — they will, since `check()` is
  synchronous and sub-second once `_get_worker_pid` is deterministic.
- **`test_memory_ingestion::test_human_message_creates_memory` (1, Redis collision):** Give the test a
  unique per-worker Redis key prefix derived from `PYTEST_XDIST_WORKER` (or a Popoto-scoped unique project
  key) so two xdist workers never touch the same key. Fixes the real collision rather than serializing.
- **`test_compose_system_prompt::test_pm_cell_byte_stable_against_local_fixture` (1, fixture ordering):**
  Pin to one worker via `@pytest.mark.xdist_group(name="compose_system_prompt")`, or make the fixture read
  deterministic (sorted iteration / isolated copy). Prefer the deterministic-read fix if it's a small change;
  fall back to `xdist_group` if the non-determinism is in shared global state another worker mutates.

**Category F:**
- Re-run all three clusters at build start. For `test_memory_prefetch` and `test_benchmarks`: profile;
  if the slow path is genuine, fix it; otherwise recalibrate the threshold with an inline comment citing
  the measured value and the headroom rationale. For `test_doc_impact_finder_sdk::TestLiveHaikuReranking`:
  add a `skipif` guard when the live key/CI signal is absent; if the response format changed, update the
  assertion to the current contract — never weaken to a tautology.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Category C is test-only; no recovery/finalization branch is modified. The tests still assert the
      existing observable signals (`tier1_flagged_total` increment, degraded-Tier-2 debug log) — confirm
      the re-pointed `getsource` targets the function that actually contains those signals.
- [ ] No new `except Exception: pass` blocks introduced.

### Empty/Invalid Input Handling
- [ ] Category C OOM path: the `pre_bump_attempts = entry.recovery_attempts or 0` capture already handles
      the `recovery_attempts is None` case in `_apply_recovery_transition` — no change, just verify the
      re-pointed test still asserts the `or 0` form.
- [ ] No agent-output processing changes; no silent-loop surface touched.

### Error State Rendering
- [ ] Category A `test_session_modal_liveness_render` covers the ghost/unknown/alive PID render
      branches — update tests to keep asserting the error/ghost rendering paths, not just the alive case.

## Test Impact

- [ ] `tests/unit/test_session_modal_liveness_render.py` (6 cases) — UPDATE: assert current template CSS/conditional output.
- [ ] `tests/unit/test_bridge_relay.py::TestSendQueuedMessage` (5 cases) — UPDATE: assert current file-send API contract.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` (3 cases) — UPDATE: make `_step4_section()` locator (line 41) prefix-tolerant (`startswith` not `==`) so it finds the parenthetical-suffixed Step 4 heading; the 3 behavioural assertions already pass once the section is located.
- [ ] `tests/unit/test_reflection_scheduler.py` (3 cases) — UPDATE: read `every` key (parse `Ns`) instead of `interval`.
- [ ] `tests/unit/test_model_relationships.py::TestTelegramMessageEnrichmentFields::test_enrichment_field_count` — UPDATE: `== 18` → `== 20`.
- [ ] `tests/unit/test_long_task_checkpointing.py::test_progress_md_in_build_soft_check` — UPDATE: path `skills` → `skills-global`.
- [ ] `tests/integration/test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit` (4 cases) — UPDATE (Category E): mock `_get_worker_pid` to the fake worker's PID so the test is isolated from a coexisting real worker. No source change.
- [ ] `tests/unit/test_harness_oom_backoff.py` (2 cases) — UPDATE (Category C): re-point both `inspect.getsource` calls from `_agent_session_health_check` to `_apply_recovery_transition`. No source change.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestRecoveryAttempts::test_health_check_source_mentions_recovery_attempts_and_max` (1 case) — UPDATE (Category C): re-point `getsource` to `_apply_recovery_transition`.
- [ ] `tests/unit/test_health_check_recovery_finalization.py::TestReprieveScopedToNoProgress` (3 cases) — UPDATE (Category C): re-point each `getsource` to `_apply_recovery_transition` (helper-ALONE, not combined); change gate string `_reason_kind == "no_progress"` → `reason_kind == "no_progress"`; re-anchor `idx_kind` on the `reason_kind` param. No source change.
- [ ] `tests/unit/test_skills_audit.py` — KEEP: fix `audit_skills` import so the module collects.
- [ ] `tests/unit/test_memory_ingestion.py::test_human_message_creates_memory` — UPDATE: add unique per-worker Redis key prefix.
- [ ] `tests/unit/test_compose_system_prompt.py::test_pm_cell_byte_stable_against_local_fixture` — UPDATE: add `xdist_group` marker.
- [ ] `tests/integration/test_memory_prefetch.py::test_prefetch_completes_under_budget` — UPDATE: fix slow path or recalibrate threshold with comment.
- [ ] `tests/performance/test_benchmarks.py::TestEndurance::test_garbage_collection` — UPDATE: fix or recalibrate with comment.
- [ ] `tests/integration/test_doc_impact_finder_sdk.py::TestLiveHaikuReranking` (4 cases) — UPDATE: add `skipif` guard / align to current response contract.
- [ ] `tests/unit/test_do_merge_review_filter.py` (15 cases) — NO CHANGE: already passing (Category B resolved); verify-only.
- [ ] `tests/integration/test_markitdown_ingestion.py` (3 cases) — NO CHANGE: already passing (Category B resolved); verify-only.

## Rabbit Holes

- **Re-architecting the watchdog heartbeat system.** The watchdog cluster is a Category E test-isolation
  fix (mock `_get_worker_pid`), not a heartbeat redesign. Do not touch the heartbeat protocol.
- **Adding the pinned lines to `_agent_session_health_check`.** The OOM/reprieve logic already exists in
  `_apply_recovery_transition` (#1270). Re-creating it in the caller to satisfy the stale `getsource`
  targets would duplicate logic and violate NO-LEGACY. Re-point the tests instead.
- **Chasing Category F into a profiling project.** If a threshold is genuinely close, recalibrate with a
  documented measurement. Only fix the slow path if it is clearly pathological.
- **Deleting tests to reach zero.** The issue constraint forbids weakening assertions; deleting a failing
  test to "pass" is the same sin. `test_skills_audit` and Category C tests must be made to pass, not removed.
- **Re-litigating Category B.** It is resolved; do not reopen the "land do-merge.md vs delete tests" debate.

## Risks

### Risk 1: Category C test re-pointing masks a genuine regression
**Impact:** Category C is test-only (the OOM/reprieve logic already lives in `_apply_recovery_transition`).
The risk is the opposite of a source change: if a builder re-points `getsource` to a function that does
*not* actually contain the asserted invariant, the test would pass vacuously. (The watchdog cluster is a
Category E test-isolation fix; no production behaviour changes anywhere in this plan.)
**Mitigation:** For every re-pointed assertion, confirm the substring is genuinely present in the new
target function (grep the line number), and run the full `test_health_check_recovery_finalization` +
`test_harness_oom_backoff` suites (not just the failing subset) to confirm no sibling assertion breaks.
Route Category C through a code-review round to confirm no assertion was weakened or mis-anchored.

### Risk 1b: Watchdog test mock diverges from production `check()` behaviour
**Impact:** Mocking `_get_worker_pid` in the watchdog test could mask a future real regression in the
global-pgrep liveness logic, since the mock bypasses the actual PID lookup.
**Mitigation:** Mock ONLY `_get_worker_pid` (the environment-coupled boundary) and let the real `check()`
exercise its down/ok/stale branch logic against the mocked PID. Do NOT mock `check()` itself. The
production pgrep path is separately covered by `test_check_reports_healthy_while_worker_runs` against a
genuinely-spawned process. Document in a test comment why the mock exists (coexisting-real-worker hazard).

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
import anything new. Category C is test-only re-pointing (the worker recovery code is unchanged); no new
agent-reachable surface is added.

## Documentation

### Feature Documentation
- [ ] No new feature doc — this is cleanup. Instead, update `tests/README.md` if any blind-spot/marker
      notes reference the now-fixed clusters (e.g. remove any "known-failing on main" callouts).

### External Documentation Site
- [ ] N/A — repo has no external docs site for the test suite.

### Inline Documentation
- [ ] Category F: inline comment on any recalibrated threshold citing the measured value and headroom.
- [ ] Category C: brief comment in each re-pointed test noting the `getsource` target moved to
      `_apply_recovery_transition` per refactor #1270 (reference issue #1578), so future readers don't
      "restore" the assertion to the caller.

## Success Criteria

- [ ] `scripts/pytest-clean.sh tests/ -q` on `main` exits 0 with 0 failures and 0 collection errors
- [ ] All Category A test files updated to match current source behaviour (no source changes for A)
- [ ] Category C tests re-pointed (test-only): the 6 OOM/reprieve tests now `inspect.getsource`
      `_apply_recovery_transition` (where #1270 moved the logic) and pass with NO change to
      `agent/session_health.py` source behaviour (no `monitoring/worker_watchdog.py` change either)
- [ ] `test_skills_audit` collects and passes (`audit_skills` import resolved)
- [ ] Category E (test-only) green: watchdog cluster passes with a real worker running (mock `_get_worker_pid`),
      and the two xdist clusters pass under `-n auto` (unique Redis key prefixes / `xdist_group`)
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

- **Builder (category-C-drift)**
  - Name: builder-c-drift
  - Role: Re-point the 6 OOM/reprieve tests' `inspect.getsource` calls to `_apply_recovery_transition` and adapt asserted strings/ordering to the post-#1270 split (test-only; no source change)
  - Agent Type: builder
  - Resume: true

- **Builder (category-D-env)**
  - Name: builder-d-env
  - Role: Resolve `audit_skills` import so `test_skills_audit` collects
  - Agent Type: builder
  - Resume: true

- **Builder (category-E-env)**
  - Name: builder-e-env
  - Role: Isolate the three environment/isolation clusters — watchdog (mock `_get_worker_pid`),
    `test_memory_ingestion` (unique Redis prefix), `test_compose_system_prompt` (deterministic read / xdist_group)
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

### 2. Category C — post-refactor test drift (2 clusters, test-only)
- **Task ID**: build-c-drift
- **Depends On**: none
- **Validates**: tests/unit/test_harness_oom_backoff.py, tests/unit/test_health_check_recovery_finalization.py
- **Assigned To**: builder-c-drift
- **Agent Type**: builder
- **Parallel**: true
- The OOM-defer ordering and reprieve gating already exist in `agent/session_health.py::_apply_recovery_transition` (lines 1096–1419, per refactor #1270). **Do NOT add or change any source line** — re-point the tests.
- `test_harness_oom_backoff` (2): change `inspect.getsource(session_health._agent_session_health_check)` → `_apply_recovery_transition` at both call sites (lines ~80, ~100). Verify each asserted substring (`pre_bump_attempts = entry.recovery_attempts or 0`, `and pre_bump_attempts == 0`, `exit_returncode", None) == -9`, `_is_memory_tight()`, `timedelta(seconds=120)`, `update_fields=["scheduled_at", "recovery_attempts"]`) is present in the new target.
- `TestRecoveryAttempts::test_health_check_source_mentions_recovery_attempts_and_max` (1): re-point to `_apply_recovery_transition` (has `recovery_attempts`, `MAX_RECOVERY_ATTEMPTS`, `reprieve_count`). Import it directly via `from agent.session_health import _apply_recovery_transition` — do NOT add a re-export to `agent_session_queue.py`.
- `TestReprieveScopedToNoProgress` (3): re-point each to `_apply_recovery_transition` (helper-ALONE — the whole gate→tier1→reprieve→kill chain lives there; a caller+helper concat fails because the caller's `DISABLE_PROGRESS_KILL` precedes the helper gate and empties the `src[gate_idx:kill_idx]` slice). Change gate string `_reason_kind == "no_progress"` → `reason_kind == "no_progress"`; re-anchor `idx_kind` on the `reason_kind` parameter (signature line 1100). Never weaken an assertion.
- Do NOT touch `monitoring/worker_watchdog.py` — the watchdog cluster is Category E (test-only).
- Run the FULL `test_health_check_recovery_finalization` + `test_harness_oom_backoff` suites with `-n0` to confirm no neighbouring regressions.

### 3. Category D — env/install
- **Task ID**: build-d-env
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py
- **Assigned To**: builder-d-env
- **Agent Type**: builder
- **Parallel**: true
- Resolve `ModuleNotFoundError: No module named 'audit_skills'` (conftest `sys.path` insert or proper package import).
- Confirm the file collects and all its cases pass with `-n0`.

### 4. Category E — environment/isolation (test-only, 3 clusters)
- **Task ID**: build-e-env
- **Depends On**: none
- **Validates**: tests/integration/test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit, tests/unit/test_memory_ingestion.py::test_human_message_creates_memory, tests/unit/test_compose_system_prompt.py::test_pm_cell_byte_stable_against_local_fixture
- **Assigned To**: builder-e-env
- **Agent Type**: async-specialist
- **Parallel**: true
- Watchdog (4): patch `monitoring.worker_watchdog._get_worker_pid` in each `TestWatchdogDetectsUnexpectedExit` test to return the fake worker's `proc.pid` while alive and `None` after kill (track `proc.poll()`), so a coexisting real worker no longer masks the kill. Do NOT mock `check()` itself. Add a comment explaining the coexisting-real-worker hazard. **Verify on this machine** (a real worker is running, PID `python -m worker`) — the cluster must go green with a live worker present.
- Give `test_memory_ingestion` a unique per-worker Redis key prefix derived from `PYTEST_XDIST_WORKER` (fix the real collision).
- Pin `test_compose_system_prompt` via `xdist_group` or make the fixture read deterministic.
- Confirm all three pass under `-n auto` across repeated runs AND with a real worker running.

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
- **Depends On**: build-a-drift, build-c-drift, build-d-env, build-e-env, build-f-perf
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
| Category C green | `.venv/bin/python -m pytest -n0 tests/unit/test_harness_oom_backoff.py tests/unit/test_health_check_recovery_finalization.py -q` | exit code 0 |
| Category C not vacuous (substrings present in re-pointed target) | `.venv/bin/python -c "import inspect; from agent.session_health import _apply_recovery_transition as f; s=inspect.getsource(f); assert all(x in s for x in ['pre_bump_attempts = entry.recovery_attempts or 0','reason_kind == \"no_progress\"','tier1_flagged_total','Tier 2 reprieve will only see compaction state']); print('ok')"` | prints `ok` |
| Category D collects | `.venv/bin/python -m pytest -n0 --collect-only tests/unit/test_skills_audit.py -q` | exit code 0 |
| Category E green (with real worker running) | `.venv/bin/python -m pytest -n0 tests/integration/test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit -q` then `-n auto tests/unit/test_memory_ingestion.py tests/unit/test_compose_system_prompt.py -q` | exit code 0 |
| Category B still green | `.venv/bin/python -m pytest -n0 tests/unit/test_do_merge_review_filter.py tests/integration/test_markitdown_ingestion.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Re-critique of revision pass 3 (2026-06-06). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Skeptic, Adversary | `TestReprieveScopedToNoProgress` (3) cannot pass via "inspect combined caller+helper source" as the plan prescribes. The 2 source-audit sub-tests have conflicting `getsource` needs that no single concatenation satisfies. | TBD (revision) | `test_tier1_flagged_metric_only_increments_for_no_progress` does `src[gate_idx:kill_idx]` where `kill_idx=src.find("DISABLE_PROGRESS_KILL")`. In `caller+helper`, the caller's `DISABLE_PROGRESS_KILL` (idx 14688) precedes the helper's gate (idx 23344) → `gate_idx<kill_idx` is **False**, slice empty, assert fails. Use **helper-alone** source for this test (gate/tier1/reprieve/kill all live in helper, ordering holds). But `test_tier2_reprieve_only_applies_to_no_progress` needs the assignment `_reason_kind = "no_progress"` which exists **only in the caller** (line 1617) — not in helper. The two tests need DIFFERENT getsource targets; rework each assertion explicitly rather than one combined-source prescription. |
| CONCERN | Skeptic | Plan's literal claim "the ordering checks hold in caller-then-helper concatenation" is factually wrong (verified). | TBD (revision) | In `caller+helper`: `gate < kill` is False because `DISABLE_PROGRESS_KILL` appears 3× (1 in caller, 2 in helper) and `.find` returns the caller's first. Correct the plan text in §Technical Approach Category C and Task 2 to specify per-test getsource targets. |
| CONCERN | Operator | Verification command for Category C (`pytest -n0 test_harness_oom_backoff test_health_check_recovery_finalization`) will not catch the vacuous-pass risk Risk 1 names — passing tests don't prove the substring is in the *intended* function. | TBD (revision) | Builder must `inspect.getsource` and grep the target function for each pinned substring as a separate step (already in Task 2 prose); promote it to a Verification-table row: `python -c "import inspect; from agent.session_health import _apply_recovery_transition as f; assert 'pre_bump_attempts == 0' in inspect.getsource(f)"`. |
| NIT | Simplifier | `_apply_recovery_transition` re-export from `agent_session_queue.py` is needed (only `_agent_session_health_check` is re-exported at line 77); tests could import from `agent.session_health` directly and skip the re-export. | TBD (revision) | Prefer the direct import `from agent.session_health import _apply_recovery_transition` in the test — avoids adding a new public-ish surface to `agent_session_queue.py` for a test-only need. |

---

## Decisions (resolved from prior Open Questions)

These were Open Questions at first draft; resolved to explicit defaults during the critique-revision pass so
build is not blocked on round-trips. Each is reversible if the PM/human overrides during review.

1. **Category F disposition — DECIDED: recalibrate-with-comment.** For `test_memory_prefetch` (8.25s vs 5s
   budget) and the GC benchmark, the builder recalibrates the threshold and writes an inline comment citing
   the measured value + headroom rationale, UNLESS profiling shows a pathological slow path (a single obvious
   regression), in which case fix the slow path instead. Builder reports which choice was made per cluster.
2. **Live-Haiku tests — DECIDED: `skipif`-guard.** `TestLiveHaikuReranking` gets a `skipif` guard on absent
   `ANTHROPIC_API_KEY` (and a known-CI signal). When the key IS present, the assertions must run against the
   current response contract — never weakened to a tautology. This keeps coverage where the key exists and
   avoids non-deterministic CI failures where it does not.
3. **Scope — DECIDED: verify-only for B and D-`mcp`.** Category B (now green) and the resolved D-`mcp` item are
   verify-only line-items; no additional hardening is added (avoids scope creep on already-passing clusters).

## Critique-Revision Changelog

This pass corrected one material misclassification surfaced by re-verifying source at plan time:

- **Watchdog cluster (`TestWatchdogDetectsUnexpectedExit`, 4) reclassified C → E.** The issue's "heartbeat
  TTL too permissive" diagnosis was wrong: `check()` returns `down` only on `_get_worker_pid() is None`, and
  `_get_worker_pid()` does a **global** `pgrep -if "python -m worker"` that matches any real worker on the
  host (confirmed: a real worker runs on this machine). The 4 tests fail because they cannot isolate their
  fabricated worker from a real one — a **test-isolation defect**, not a source bug. The "tighten the TTL"
  approach would have flipped 0 of the 4 tests. Fix is now test-only (mock `_get_worker_pid`), with no
  production-behaviour risk. Category C is correspondingly reduced to 2 clusters (OOM + reprieve) — later
  reclassified to test-only re-pointing in revision pass 3 below.
- **OOM/reprieve fixes now cite the exact source strings** the tests `inspect.getsource`-pin (e.g.
  `pre_bump_attempts = entry.recovery_attempts or 0` must precede `and pre_bump_attempts == 0`), removing the
  prior hand-wavy "match the spec" framing.

### Revision pass 3 (post-critique) — Category C reclassified to test drift

Re-verifying source at plan time confirmed the critique's load-bearing blocker: **Category C is NOT a source
bug.** The OOM-defer ordering and reprieve-scoping logic the 6 tests assert already exists in
`agent/session_health.py::_apply_recovery_transition` (1096–1419) — it was extracted from
`_agent_session_health_check` by refactor #1270. Each asserted substring was located in the helper:
`pre_bump_attempts = entry.recovery_attempts or 0` (1291), `getattr(entry, "exit_returncode", None) == -9`
(1363), `and pre_bump_attempts == 0` (1364), `if reason_kind == "no_progress":` (1185), `tier1_flagged_total`
(1189, once), the degraded-Tier-2 log (1182), `reprieve_count`/`MAX_RECOVERY_ATTEMPTS`. The tests fail only
because they still `inspect.getsource(_agent_session_health_check)`, which now merely *calls* the helper.

**Fix is test-only re-pointing** (same class as Category A): point the `getsource` calls at
`_apply_recovery_transition`, and for `TestReprieveScopedToNoProgress` (whose invariant #1270 split across two
functions) inspect the combined caller+helper source and update the gate string from
`_reason_kind == "no_progress"` to the helper's `reason_kind == "no_progress"`. Adding the pinned lines to
`_agent_session_health_check` (the prior plan's instruction) would **duplicate** existing logic and violate
NO-LEGACY. The builder/agent-type for Category C changed from `debugging-specialist` (source fix) to `builder`
(test edit), and Risk 1 was inverted (the risk is now a vacuous-pass from mis-anchored re-pointing, not a
production behaviour change). With this, **no production code changes anywhere in the plan** — every category
is test-only or mechanical.
- **Open Questions resolved to explicit defaults** (above) so build proceeds without a human round-trip.

### Revision pass 2 (post-critique)

- **Category A `test_sdlc_skill_md_parity` — corrected the build instruction to the verified root cause.**
  The prior text said "update the parity test's expected strings," which risked a wrong fix. Verified at
  plan time: all 3 failures are the `_step4_section()` helper's **exact-match** on the Step 4 heading
  (`tests/unit/test_sdlc_skill_md_parity.py:41`) failing to locate the now-parenthetical-suffixed heading
  `## Step 4: Dispatch ONE Sub-Skill (or a Parallel-Safe Pair)`, so all three short-circuit at the
  `assert section` precondition. The minimal correct fix is a **prefix-tolerant locator** (`startswith`),
  after which the three behavioural assertions already pass against the current SKILL.md. Updated both the
  Category A technical approach and the Test Impact line to name the exact file:line and the intended edit.
