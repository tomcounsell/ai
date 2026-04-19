---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-19
tracking: https://github.com/tomcounsell/ai/issues/1041
last_comment_id: 4275143961
---

# Test-suite debt: restore green test suite on main

## Problem

`pytest` on main at `ba51c088` reports ~177 test failures across three suites. The damage is concentrated in a handful of shared root causes, not 177 independent bugs:

- Refactor **#1023** (agent_session_queue split) merged on 2026-04-18 with incomplete shim re-exports. Tests that `patch("agent.agent_session_queue.X")` silently fail because `X` moved to a split module and was never re-exported.
- Popoto/Redis index corruption in fixture setup causes `Model.query.filter(...)` to return `[]` after records are written. Affects ~47 integration + tools tests.
- Tool-level input validation tests run against an API-key gate that fires before input checks, so empty-input assertions see the wrong error (`ANTHROPIC_API_KEY required` instead of `"empty"`).
- Several tests assert on symbols (`HEARTBEAT_FRESHNESS_WINDOW`, `REPLY_THREAD_CONTEXT_HEADER`, `com.valor.update` plist label) that have been removed or relocated but never updated.
- Pre-existing clusters A-H from the original recon (knowledge_search namespace pollution, reflection async drift, PersonaType count, plist hang, live-API gate, MagicMock sentinel) remain on top of these new post-#1023 regressions.

**Current behavior:**
- Tools: 41 failed / 83 passed / 38 skipped (7.8s)
- Integration: 68 failed / 439 passed / 1 collection error (177s, from 513 collected)
- Unit: run in progress at plan time; prior recon reported ~27 failures tied to Cluster A + downstream clusters
- CI signal is drowned; regressions from further refactors cannot be distinguished from this standing debt.

**Desired outcome:**
- `pytest tests/unit tests/integration tests/tools` passes on main with 0 regressions and 0 collection errors.
- Pre-existing clusters A-H resolved; post-#1023 clusters I-M resolved.
- Each cluster lands on its own small PR so bisecting a future regression stays easy.
- Tools-tests that depend on a live API key are gated with `pytest.mark.skipif` so missing keys do not masquerade as bugs.

## Freshness Check

**Baseline commit:** `ba51c088`
**Issue filed at:** 2026-04-18T07:11:20Z
**Disposition:** Minor drift — issue still valid, but #1023 split landed after filing and expanded the scope.

**File:line references re-verified:**
- `tools/knowledge_search._compute_embedding` — still referenced by `tools/emoji_embedding.py:215, 221, 270, 273, 460, 466`. Cluster A premise holds.
- `config/enums.PersonaType` — now has 4 members (DEVELOPER, PROJECT_MANAGER, TEAMMATE, CUSTOMER_SERVICE). Cluster H premise holds.
- `scripts/update/newsyslog.py` — module exists; action-message wording drift from Cluster H still plausible.
- `agent/agent_session_queue.HEARTBEAT_FRESHNESS_WINDOW` — no longer defined in `agent_session_queue.py` (moved during #1023). New; not in original recon.
- `agent/agent_session_queue.get_branch_state` — moved to `agent/session_revival.py`; patch targets in `tests/unit/test_recovery_respawn_safety.py` are stale. New.
- `agent/agent_session_queue.REPLY_THREAD_CONTEXT_HEADER` — removed during split. New.

**Cited sibling issues/PRs re-checked:**
- #1036 (300s no-progress guard) — CLOSED; PR #1039 merged 2026-04-18. Resolution unrelated to this issue's clusters.
- #761 (fix `_pop_agent_session` tests) — CLOSED; plan `docs/plans/fix-pop-agent-session-tests.md` has `status: docs_complete`. Cluster E in this issue likely overlaps with that prior work — verify tests actually green before re-attempting.

**Commits on main since issue was filed (touching referenced files):**
- `b7e1a1db` refactor: split `agent_session_queue.py` (#1023) — **changed root cause for multiple clusters**. Added clusters I/L; plausibly expanded J.
- `d76232f4` feat(health-check): promote last_stdout_at to Tier-1 kill signal (#1046) — tangential
- `b847ae4a` fix orphan detection crash — unrelated
- `27321311` fix: circuit-break docs auditor (#1034) — unrelated to test failures
- Plans `#1025`, `#1026`, `#1030` — plan commits only, implementation not landed.

**Active plans in `docs/plans/` overlapping this area:**
- `fix-pop-agent-session-tests.md` — `status: docs_complete`, already landed per issue #761 closure. May overlap Cluster E; re-check before duplicating.
- `test-reliability-flaky-filter.md` — `status: Planning`, targets flaky-vs-regression classification. Complementary, not overlapping; a green baseline is a prerequisite for that plan's completeness.
- `test_coverage_gaps_471.md` — `status: Building`, targets nudge/revival/routing test coverage. Unrelated clusters.

**Notes:** Post-#1023 regressions (Clusters I, J, L, M) were discovered during this plan's freshness re-run and posted as issue comment `4275143961`. They are incorporated below.

## Prior Art

- **Issue #761 / plan `fix-pop-agent-session-tests.md`**: "Fix `_pop_agent_session` tests and extraction helper docstrings" — closed 2026-04-06. Previously fixed the delete-and-recreate → in-place-mutation test drift. Cluster E in this plan may be fallout (either the refactor #1023 regressed it, or tests never landed to main).
- **Issue #1042**: "SDLC skill audit: close the five blind spots that let bugs through" — closed 2026-04-18. Context on how test regressions slip through CI.
- **PR #1051**: refactor: split `agent_session_queue.py` (5545 LOC) by responsibility — merged 2026-04-18. The source of Clusters I, L, and the `_pop_agent_session` signature drift in Cluster D.
- **PR #1029**: Collapse session concurrency: single `MAX_CONCURRENT_SESSIONS=8` cap — may explain Cluster M (global semaphore drift).

## Research

Skipped — all root causes are internal; no external libraries involved beyond pytest.

## Spike Results

### spike-1: Popoto index corruption root cause (Cluster J)

- **Assumption**: "The ~47 `[]`-returning `query.filter(...)` failures share a single root cause in test fixture setup/teardown, not individual test bugs."
- **Method**: code-read + targeted reproduction
- **Time cap**: 10 min
- **Agent Type**: `debugging-specialist` in a worktree
- **Dispatch**: before committing any fix to Cluster J
- **Exit criteria**: confirmed root cause (fixture teardown leaks, Popoto key-schema change, Redis namespace collision with running bridge, or per-test isolation gap) AND proposed single-site fix.
- **Impact on plan**: determines whether Cluster J is a 1-line conftest fix or a 20-file test rewrite. Build effort delta: ~2 hours.

### spike-2: Shim re-export completeness audit (Cluster I)

- **Assumption**: "Only three symbols (`HEARTBEAT_FRESHNESS_WINDOW`, `get_branch_state`, `REPLY_THREAD_CONTEXT_HEADER`) need re-export; no other test patches silently fail."
- **Method**: grep for every `patch("agent.agent_session_queue.<symbol>"`, then verify each `<symbol>` resolves on the current shim.
- **Time cap**: 5 min
- **Agent Type**: `Explore`
- **Dispatch**: before writing the Cluster I fix
- **Exit criteria**: exhaustive list of missing re-exports.
- **Impact on plan**: if count > 3, the shim fix pattern changes from "add three imports" to "add a `__getattr__` catch-all or re-export module".

## Data Flow

N/A — this plan is test-suite debt cleanup. No production data paths change.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Plan `fix-pop-agent-session-tests.md` (#761) | Rewrote `_pop_agent_session` tests for in-place mutation | Did not prevent downstream drift: #1023's signature change to `_pop_agent_session` broke `test_remote_update.py` mocks; tests pinned to specific symbols (`HEARTBEAT_FRESHNESS_WINDOW`) broke when the queue module was split |

**Root cause pattern:** Tests in this repo reach into the `agent/agent_session_queue` namespace via `patch()`, symbol imports, and grep-based assertions. Any refactor that moves, renames, or removes a symbol in that module breaks tests at a distance. The shim pattern used in #1023 was meant to solve this but is incomplete. This plan fixes the immediate breakages; a follow-up (out of scope here) should consider whether tests should patch canonical modules (`agent.session_revival`) instead of the shim.

## Architectural Impact

- **Coupling:** No change. This is test-suite cleanup.
- **Interface changes:** Re-export three missing symbols on `agent/agent_session_queue.py`. This preserves the shim contract for existing tests.
- **Data ownership:** No change.
- **Reversibility:** Every change is in `tests/` or narrowly-scoped test-accessibility code. Fully reversible.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM check-ins

**Interactions:**
- PM check-ins: 1-2 (scope confirmation before starting; decision on single-PR-vs-per-cluster-PR)
- Review rounds: 1 (one review before merge; each cluster PR small enough to review in <10 min)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Popoto ORM tests require Redis |
| No live Anthropic API key required | (none) | Tests gated with `pytest.mark.skipif` run without it |

Run all checks: `python scripts/check_prerequisites.py docs/plans/test_suite_debt_1041.md`

## Solution

### Key Elements

- **Shim re-export fix** (Cluster I, L): add the missing symbols to `agent/agent_session_queue.py` so existing test patch targets resolve. Immediate win, ~3 lines.
- **Popoto fixture isolation fix** (Cluster J): after spike-1 root-causes it, apply a single-site fix — most likely in `tests/conftest.py` or a shared fixture that flushes Popoto indexes between tests.
- **API-key gate reorder or skipif** (Cluster K): reorder guards so input validation runs before API-key checks, OR gate the affected tests with `pytest.mark.skipif(not ANTHROPIC_API_KEY)`. Per-test-file decision.
- **Symbol drift updates** (Clusters D, H): update tests and strings to match current source (`_pop_agent_session` signature, `com.valor.update` plist removal, `PersonaType` 4-member count, newsyslog action wording).
- **Cluster E sanity check**: verify the `StatusConflictError` failures are actually still failing after #761's landed fix; close-without-change if stale.
- **Cluster A reproduction**: reproduce the isolation-vs-full-suite split; fix at the `tools/knowledge_search` import-order root cause, not by rewriting downstream tests.
- **Cluster C (reflections async)**: replace `asyncio.run(callable())` with direct call in the affected reflection tests, since callables return `dict` synchronously.
- **Cluster F (live API gate)**: add `pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"))` to `TestLiveHaikuReranking`.
- **Cluster G (MagicMock sentinel)**: set `session_id="child-001"` on the `MagicMock` in the affected test.
- **Cluster M (concurrency semaphore)**: investigate whether `MAX_CONCURRENT_SESSIONS=8` truly fails to limit to 2 in this test's context, or whether the test is mis-scoped. Bug-or-test decision.

### Flow

Baseline → run suites → post-cluster patches → re-run suites → confirm green → per-cluster PRs → merge → close #1041.

### Technical Approach

- **One cluster per PR.** Each cluster gets its own branch `fix/test-debt-clusterX-shortname`. Small PRs unblock review fast and keep bisect clean.
- **Spike first.** Clusters I and J are spiked before touching code. Clusters with known fixes (D, E, F, G, H, L) proceed directly.
- **Don't rewrite production code to satisfy tests** unless the production code is actually wrong. Cluster K (API-key-gate reorder) is the one place where a production change is arguably correct (validate inputs first); everything else fixes tests to match source.
- **Leave runtime `pytest.xfail()` alone.** No xfail markers exist in the current suite (verified), so the stale-xfail trap does not apply.
- **Unit-suite data TBD.** The unit run was still in progress at plan-writing time. If the finished report adds new clusters, update this plan before starting Build.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced — this plan only updates tests and a few re-exports; no new handlers.

### Empty/Invalid Input Handling
- [ ] Cluster K fix (API-key gate reorder) must keep `result["error"]` non-empty for empty-string input and containing "empty" in the lowercased message.
- [ ] `test_doc_summary.py::TestSummarizeValidation::test_empty_content_returns_error` passes after the fix.

### Error State Rendering
- [ ] Not applicable — no user-visible output paths change.

## Test Impact

Per-cluster. Every fix either updates or deletes specific tests; disposition is UPDATE unless noted.

**Cluster I — Shim re-exports**
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: un-skip once `HEARTBEAT_FRESHNESS_WINDOW` is re-exported (or patch target corrected)
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestCheckRevivalTerminalFilter::test_revival_passes_non_terminal_branches` — UPDATE: change patch target to `agent.session_revival.get_branch_state`
- [ ] `tests/integration/test_steering.py::TestResolveRootSessionId::test_no_double_hydration_when_handler_prehydrates` — UPDATE: assert against `agent.session_executor.REPLY_THREAD_CONTEXT_HEADER` (or wherever it now lives) or remove the string-level guard

**Cluster J — Popoto isolation (~47 failures across these files)**
- [ ] `tests/integration/test_agent_session_scheduler.py` — UPDATE conftest only, not individual tests
- [ ] `tests/integration/test_agent_session_queue_race.py` — UPDATE conftest only
- [ ] `tests/integration/test_bridge_routing.py` — UPDATE conftest only
- [ ] `tests/integration/test_connectivity_gaps.py` — UPDATE conftest only
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE conftest only
- [ ] `tests/integration/test_parent_child_round_trip.py` — UPDATE conftest only
- [ ] `tests/tools/test_telegram_history.py` — UPDATE conftest only

**Cluster K — Tool API-key gate**
- [ ] `tools/classifier.py` — UPDATE: move input-validation guard above API-key guard (production code, not test)
- [ ] `tools/doc_summary.py` — UPDATE: same
- [ ] `tools/test_judge/*.py` — UPDATE: same
- [ ] Or, alternatively, gate `tests/tools/test_classifier.py`, `test_doc_summary.py`, `test_test_judge.py` with `pytest.mark.skipif(not ANTHROPIC_API_KEY)` — requires product-owner decision

**Cluster D — `_pop_agent_session` signature + plist**
- [ ] `tests/integration/test_remote_update.py::TestWorkerRestartCheck::test_worker_checks_flag_after_job_completion` — UPDATE: fix `pop_side_effect()` to accept new arg count
- [ ] `tests/integration/test_remote_update.py::TestServiceManager::test_update_plist_defined` — DELETE or REPLACE: `com.valor.update` is gone; assert the current labels instead

**Cluster E — StatusConflictError**
- [ ] `tests/integration/test_worker_drain.py::*` (3 tests) — UPDATE if still failing after plan `fix-pop-agent-session-tests.md` landed; close as stale if baseline-verified green
- [ ] `tests/integration/test_worker_concurrency.py::TestPerChatSerialization::test_global_ceiling_across_multiple_chat_ids` — REPLACE: rewrite test against `MAX_CONCURRENT_SESSIONS=8` (not 2)

**Cluster A — knowledge_search namespace**
- [ ] `tests/unit/test_ui_app.py`, `test_knowledge_indexer.py`, `test_emoji_embedding.py` — UPDATE or REPLACE depending on spike findings; fix is at import-order root cause in `tools/knowledge_search/__init__.py`, not per-test

**Cluster C — Reflection async drift**
- [ ] `tests/unit/test_reflections*.py` — UPDATE: remove `asyncio.run(...)` wrapper around sync-returning callables

**Cluster F — Live API gate**
- [ ] `tests/*/TestLiveHaikuReranking::*` — UPDATE: add `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"))`

**Cluster G — MagicMock sentinel**
- [ ] Locate the failing test (was named in recon but file not cited); UPDATE: add `session_id="child-001"` to the `MagicMock(...)` call

**Cluster H — PersonaType + newsyslog**
- [ ] `tests/unit/test_personas*.py` — UPDATE: assert 4 members not 3
- [ ] `tests/unit/test_newsyslog*.py` — UPDATE: assert current action message wording

**Cluster L — Silent log regressions**
- [ ] `tests/integration/test_silent_failures.py::TestLoadCooldownsLogging::test_file_read_failure_logs_warning` — UPDATE if log was intentionally removed; REPLACE with new observable check. Otherwise restore the `logger.warning` call in production code.
- [ ] `tests/integration/test_silent_failures.py::TestSaveCooldownsLogging::test_file_write_failure_logs_warning` — same

**Cluster M — Semaphore**
- [ ] `tests/integration/test_worker_concurrency.py::TestPerChatSerialization::test_global_ceiling_across_multiple_chat_ids` — see Cluster E above (merged disposition)

## Rabbit Holes

- **Do NOT rewrite the shim pattern in this plan.** Auditing every test's patch target so they address canonical modules (`agent.session_revival`) instead of the shim is a ~30-file change. Save it for a follow-up refactor plan.
- **Do NOT generalize Popoto fixture setup.** Fix the one root cause found by spike-1; resist the urge to standardize all integration fixture teardown in this PR.
- **Do NOT chase black-formatting output.** The 434-file report is a `pyproject.toml` config omission (no `[tool.black]` section). A separate <10-line fix adds `[tool.black] line-length = 100` to unify with ruff. Out of scope here; file a separate chore if desired.
- **Do NOT re-ship the old `com.valor.update` plist.** It was deliberately removed; update the test, do not restore the script.
- **Do NOT promote Cluster K skipif over production reorder without asking.** The reorder (validate inputs before API key) is arguably a correctness improvement, not just a test accommodation. Requires owner decision.

## Risks

### Risk 1: Unit-suite data missing at plan time
**Impact:** New clusters surface post-plan and force a mid-build scope expansion.
**Mitigation:** Phase 0 of Build re-runs `pytest tests/unit -q` first. If new clusters appear, update plan and re-run critique before coding.

### Risk 2: Popoto spike finds no single root cause
**Impact:** Cluster J expands from 1 conftest fix to ~7 test-file rewrites. Schedule slips ~1 day.
**Mitigation:** Spike has an explicit exit criterion. If no single root cause emerges, escalate to PM for appetite reset.

### Risk 3: API-key reorder changes tool-error contract
**Impact:** Downstream consumers of `classify_request()` (bridge, MCP) that branch on `"No Anthropic API key"` text break.
**Mitigation:** Grep for the exact error string before touching; if referenced, keep text stable or update all call sites in same PR.

### Risk 4: #1023 shim fix masks deeper patch-target drift
**Impact:** Re-exporting three symbols restores green but does not prevent the next refactor from breaking the same class of tests.
**Mitigation:** Out of scope for this plan, but file a follow-up issue on "audit test patch targets to hit canonical modules" as part of the post-merge cleanup.

## Race Conditions

No race conditions identified — changes are test-only or single-line re-exports. All operations are synchronous.

## No-Gos (Out of Scope)

- New test infrastructure (no change to `conftest.py` beyond fixing Cluster J's root cause).
- Refactoring `agent/agent_session_queue` shim pattern.
- Adding `[tool.black]` config to `pyproject.toml` (separate chore).
- Enabling flaky-test filter work from plan `test-reliability-flaky-filter.md` (that plan remains Planning; this is a prerequisite, not a merge).
- E2E or performance suite cleanup (unchanged in this plan; run not attempted).
- Re-enabling full-suite run with `-n auto` parallelization (that is a CI config change).

## Update System

No update system changes required — this plan only fixes tests and one minor shim addition. No scripts, binaries, or config files change.

## Agent Integration

No agent integration required — changes are confined to `tests/` and (for Cluster K option A) `tools/`. No MCP server, `.mcp.json`, or bridge surface area changes.

## Documentation

- [ ] Update `docs/features/bridge-worker-architecture.md` **only if** Cluster I reveals the shim contract needs documenting (likely no change — shim is meant to be transparent).
- [ ] Add a "Patch-target convention" callout to `tests/README.md` noting the canonical-vs-shim decision from Risk 4. 3–5 lines; prevents the next test-at-shim surprise.
- [ ] No other documentation affected.

## Success Criteria

- [ ] `pytest tests/unit tests/integration tests/tools --tb=short` returns exit code 0 on main.
- [ ] 0 collection errors (i.e., `tests/integration/test_session_heartbeat_progress.py` collects cleanly).
- [ ] No new `pytest.mark.skipif` added except for the documented live-API cases (Cluster F + optionally K).
- [ ] Each cluster's fix lands in its own PR with `Closes #1041` on the final PR only.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) for the `tests/README.md` note.
- [ ] No regressions from the original 8 clusters A-H (baseline-verified against `ba51c088`).

## Team Orchestration

Lead agent orchestrates; all work delegated to sub-agents.

### Team Members

- **Spike (popoto)**
  - Name: `popoto-spike`
  - Role: Root-cause Cluster J via worktree reproduction.
  - Agent Type: `debugging-specialist`
  - Resume: false (one-shot investigation)

- **Spike (shim audit)**
  - Name: `shim-audit`
  - Role: Enumerate every `patch("agent.agent_session_queue.<X>"` and check each resolves.
  - Agent Type: `Explore`
  - Resume: false

- **Builder (shim re-exports)**
  - Name: `shim-builder`
  - Role: Add missing re-exports on `agent/agent_session_queue.py` and update 3 test patch targets.
  - Agent Type: builder
  - Resume: true

- **Builder (popoto fixtures)**
  - Name: `popoto-builder`
  - Role: Apply the single-site fix identified by `popoto-spike`.
  - Agent Type: builder
  - Resume: true

- **Builder (tool gates)**
  - Name: `tool-gates-builder`
  - Role: Cluster K reorder or skipif (final choice pending PM input on Risk 3).
  - Agent Type: builder
  - Resume: true

- **Builder (symbol drift)**
  - Name: `drift-builder`
  - Role: Update tests for Clusters D, E, F, G, H, M.
  - Agent Type: builder
  - Resume: true

- **Builder (unit clusters)**
  - Name: `unit-builder`
  - Role: Update tests for Cluster A, C, and any late-surfaced clusters from the unit run.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `test-validator`
  - Role: Re-run `pytest` after each builder finishes; confirm only target tests moved from FAIL to PASS with no collateral regressions.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Spike — Popoto root cause
- **Task ID**: spike-popoto
- **Depends On**: none
- **Validates**: proposed single-site fix documented in plan before build starts
- **Assigned To**: popoto-spike
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Reproduce one failing test in isolation (e.g. `tests/tools/test_telegram_history.py::TestSearchLinks::test_search_by_query`).
- Inspect Popoto index keys before and after test setup; identify the corruption source.
- Propose the single-site fix (conftest, fixture, Popoto config).
- Time cap: 15 minutes.

### 2. Spike — Shim re-export audit
- **Task ID**: spike-shim
- **Depends On**: none
- **Validates**: exhaustive symbol list before Cluster I build
- **Assigned To**: shim-audit
- **Agent Type**: Explore
- **Parallel**: true
- Grep every `patch\("agent\.agent_session_queue\.([A-Za-z_]+)"` across `tests/`.
- For each captured symbol, check `agent/agent_session_queue.py` currently exports it.
- Return the missing-symbol list.
- Time cap: 5 minutes.

### 3. Build — Shim re-exports (Cluster I + L if log-re-add needed)
- **Task ID**: build-shim
- **Depends On**: spike-shim
- **Validates**: `tests/integration/test_session_heartbeat_progress.py`, `tests/unit/test_recovery_respawn_safety.py::TestCheckRevivalTerminalFilter`, `tests/integration/test_steering.py::TestResolveRootSessionId`
- **Informed By**: spike-shim
- **Assigned To**: shim-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `from agent.session_X import <symbol>` for each missing re-export in `agent/agent_session_queue.py`.
- Fix `tests/unit/test_recovery_respawn_safety.py` patch target.
- Re-run the three target test files; confirm green.

### 4. Build — Popoto fixture fix (Cluster J)
- **Task ID**: build-popoto
- **Depends On**: spike-popoto
- **Validates**: 7 affected test files (see Test Impact)
- **Informed By**: spike-popoto
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: false
- Apply the single-site fix from spike-popoto.
- Re-run all 7 affected test files; confirm ~47 tests flip FAIL→PASS.

### 5. Build — Symbol drift cluster (D, E, F, G, H, M)
- **Task ID**: build-drift
- **Depends On**: none (independent of spikes)
- **Validates**: cluster-specific test files listed in Test Impact
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `test_remote_update.py` mock signature and remove `com.valor.update` assertion.
- Fix MagicMock sentinel in Cluster G test.
- Update `PersonaType` assertion (3 → 4) and newsyslog action wording.
- Add skipif to `TestLiveHaikuReranking`.
- Verify Cluster E — if baseline-green, close without change.
- Re-scope Cluster M test against `MAX_CONCURRENT_SESSIONS=8`.

### 6. Build — Tool API-key gate (Cluster K)
- **Task ID**: build-tool-gates
- **Depends On**: PM decision on Risk 3 (reorder vs skipif)
- **Validates**: `tests/tools/test_classifier.py`, `test_doc_summary.py`, `test_test_judge.py`
- **Assigned To**: tool-gates-builder
- **Agent Type**: builder
- **Parallel**: true (after decision)
- Apply chosen approach: either reorder guards in `tools/classifier.py`, `tools/doc_summary.py`, `tools/test_judge/__init__.py`, OR add `pytest.mark.skipif` to the 24 affected tests.
- Re-run tool tests; confirm 24 flip FAIL→PASS (reorder) or FAIL→SKIP (skipif).

### 7. Build — Unit suite clusters (A, C, + late surprises)
- **Task ID**: build-unit
- **Depends On**: final unit-run results
- **Validates**: `tests/unit/test_ui_app.py`, `test_knowledge_indexer.py`, `test_emoji_embedding.py`, reflections tests
- **Assigned To**: unit-builder
- **Agent Type**: builder
- **Parallel**: true
- Reproduce Cluster A isolation-vs-full-suite split; fix at `tools/knowledge_search/__init__.py` import-order root cause.
- Remove `asyncio.run(...)` wrappers in Cluster C reflection tests.
- Address any unit-suite clusters surfaced after plan approval.

### 8. Validate — Full suite re-run
- **Task ID**: validate-suite
- **Depends On**: build-shim, build-popoto, build-drift, build-tool-gates, build-unit
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit tests/integration tests/tools --tb=short`.
- Confirm exit code 0, 0 collection errors.
- Baseline-verify no regressions vs `ba51c088`.
- Report per-cluster before/after counts.

### 9. Document — Patch-target convention note
- **Task ID**: document-note
- **Depends On**: validate-suite
- **Assigned To**: shim-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add a 3-5 line callout to `tests/README.md` about patch targets preferring canonical modules.

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-suite, document-note
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table below end-to-end.
- Confirm all Success Criteria met.
- Generate final report for #1041 close.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit -q --tb=no` | exit code 0 |
| Integration tests pass | `pytest tests/integration -q --tb=no` | exit code 0 |
| Tools tests pass | `pytest tests/tools -q --tb=no` | exit code 0 |
| No collection errors | `pytest tests/ --collect-only 2>&1 \| grep -c "ERROR"` | output == 0 |
| Shim symbols exported | `python -c "from agent.agent_session_queue import HEARTBEAT_FRESHNESS_WINDOW, get_branch_state, REPLY_THREAD_CONTEXT_HEADER"` | exit code 0 |
| Format clean | `black --check --line-length 100 .` | exit code 0 |
| No stale xfails | `grep -rn 'pytest.xfail(' tests/ --include="*.py"` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Single PR or one PR per cluster?** Plan assumes per-cluster (easier review/bisect). If PM prefers one bundle, merge tasks 3–7 into a single build.
2. **Cluster K — reorder guards vs skipif?** Reorder is correct-per-design but may change tool error text that downstream code depends on. Skipif is test-only. Which does the owner prefer?
3. **Cluster E — attempt fix or baseline-close?** If `test_worker_drain.py` and `test_worker_concurrency.py` `StatusConflictError`s are stable pre-existing failures (per original recon Cluster E), should we close them as pre-existing (out of scope) or fix them in this plan?
4. **Unit-suite data.** Unit run still in progress at plan time. Hold Build until unit results land, or proceed speculatively and patch mid-flight if new clusters surface?
