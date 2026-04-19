---
status: Ready
type: bug
appetite: Medium
owner: Valor
created: 2026-04-19
tracking: https://github.com/tomcounsell/ai/issues/1041
last_comment_id: 4275201174
---

# Test-suite debt: restore 100% green test suite on main

## Problem

`pytest tests/unit tests/integration tests/tools` on main at `ba51c088` reports **160 failures + 1 collection error + 1 hang** across 19 root-cause clusters. Most clusters share a small number of origins:

- Refactor **#1023** (agent_session_queue split) merged 2026-04-18 with incomplete shim re-exports, new function signatures, and source-code string assertions that tests still grep for. Multiple clusters are direct fallout.
- Popoto/Redis index corruption in fixture setup causes `Model.query.filter(...)` to return `[]` after records are written. Affects ~47 integration + tools tests with a single likely root cause.
- Tool input-validation tests run against an API-key gate that fires before input checks, so empty-input assertions see the wrong error.
- `ANTHROPIC_API_KEY` absence: 21 classifier tests + 16 LLM-dependent unit tests that require the key but aren't gated.
- Several tests assert on symbols, strings, and plist labels that have been removed or relocated but never updated.

**Current behavior:**

| Suite | Failed | Passed | Notes |
|-------|--------|--------|-------|
| Unit | 51 | 4327 | + 1 hang (`test_worker_persistent.py`) |
| Integration | 68 | 439 | + 1 collection error (`test_session_heartbeat_progress.py`) |
| Tools | 41 | 83 | |
| **Total** | **160** | **4849** | |

**Desired outcome:**
- `pytest tests/unit tests/integration tests/tools` passes with exit code 0 on main.
- **100% of failures fixed**, including pre-existing ones. No baseline-close-as-stale path; this issue exists specifically to drive all failures to zero.
- 0 collection errors. 0 hangs.
- Single PR that closes `#1041`; within the PR, commits are organized per cluster so bisect stays useful, and the PR can be landed incrementally (per-cluster pushes) if individual clusters are ready before others.

## Freshness Check

**Baseline commit:** `ba51c088`
**Issue filed at:** 2026-04-18T07:11:20Z
**Disposition:** Minor drift — issue still valid, but #1023 split landed after filing and expanded the scope. All new clusters (I–S) added to issue comments `4275143961` and `4275201174`.

**File:line references re-verified:**
- `tools/knowledge_search._compute_embedding` — still referenced by `tools/emoji_embedding.py:215, 221, 270, 273, 460, 466`. Cluster A premise holds.
- `config/enums.PersonaType` — has 4 members today (DEVELOPER, PROJECT_MANAGER, TEAMMATE, CUSTOMER_SERVICE). Cluster H + Cluster Unit-9 premise holds.
- `agent/agent_session_queue.HEARTBEAT_FRESHNESS_WINDOW` / `.get_branch_state` / `.REPLY_THREAD_CONTEXT_HEADER` — not in the post-#1023 shim. Clusters I + S premise holds.
- `scripts/update/newsyslog.py` — present; action-message wording drift plausible.

**Cited sibling issues/PRs re-checked:**
- **#1036** (300s no-progress guard) — CLOSED; PR #1039 merged 2026-04-18. Unrelated to this plan's scope.
- **#761** (fix `_pop_agent_session` tests) — CLOSED; plan `fix-pop-agent-session-tests.md` has `status: docs_complete`. Cluster E = fixing any NEW `_pop_agent_session` breakage from #1023, not re-doing the earlier work.

**Commits on main since issue was filed (touching referenced files):**
- `b7e1a1db` refactor: split `agent_session_queue.py` (#1023) — root cause for Clusters I, L, N, O, S; partial contributor to D, E, J.
- `d76232f4` feat(health-check): promote last_stdout_at to Tier-1 kill signal (#1046) — tangential
- `b847ae4a` fix orphan detection crash — unrelated
- Plans `#1025`, `#1026`, `#1030` — plan commits only, implementation not landed.

**Active plans in `docs/plans/` overlapping this area:**
- `fix-pop-agent-session-tests.md` — `docs_complete`, prior fix for issue #761; current Cluster E is a new regression, not a duplicate.
- `test-reliability-flaky-filter.md` — `Planning`, depends on a green baseline (i.e., this plan is a prerequisite).
- `test_coverage_gaps_471.md` — `Building`, unrelated clusters.

## Prior Art

- **Issue #761 / plan `fix-pop-agent-session-tests.md`**: Previously fixed `_pop_agent_session` delete-and-recreate → in-place-mutation test drift. Cluster E + Cluster N are new regressions from #1023's further signature changes.
- **Issue #1042**: "SDLC skill audit: close the five blind spots that let bugs through" — closed 2026-04-18. Context on how test regressions slip through CI.
- **PR #1051**: refactor: split `agent_session_queue.py` — root cause for Clusters I, L, N, O, S. Merged 2026-04-18.
- **PR #1029**: Collapse session concurrency: single `MAX_CONCURRENT_SESSIONS=8` cap — explains Cluster M (test was pinned to the old value).

## Research

Skipped — all root causes are internal. No external libraries involved beyond pytest.

## Spike Results

### spike-1: Popoto index corruption root cause (Cluster J)

- **Assumption**: "The ~47 `[]`-returning `query.filter(...)` failures share a single root cause in test fixture setup/teardown, not individual test bugs."
- **Method**: code-read + targeted reproduction in an isolated worktree.
- **Time cap**: 15 min.
- **Agent Type**: `debugging-specialist`.
- **Dispatch**: before committing any fix to Cluster J.
- **Exit criteria**: confirmed single root cause AND proposed single-site fix. If no single root cause, escalate to PM before continuing.

### spike-2: Shim re-export completeness audit (Cluster I)

- **Assumption**: "Only three symbols (`HEARTBEAT_FRESHNESS_WINDOW`, `get_branch_state`, `REPLY_THREAD_CONTEXT_HEADER`) need re-export; no other test patches silently fail."
- **Method**: grep every `patch("agent.agent_session_queue.<symbol>")` across `tests/`, then verify each resolves on the current shim.
- **Time cap**: 5 min.
- **Agent Type**: `Explore`.
- **Dispatch**: before writing the Cluster I fix.
- **Exit criteria**: exhaustive list of missing re-exports.

### spike-3: `test_worker_persistent.py` hang source (Cluster Q)

- **Assumption**: "The hang is a single blocking call (subprocess.wait, recv on a socket, or similar) with no timeout."
- **Method**: run the file with `-x --timeout=30` and inspect the traceback from timeout kill; read the test for blocking I/O.
- **Time cap**: 10 min.
- **Agent Type**: `debugging-specialist`.
- **Dispatch**: before touching Cluster Q.
- **Exit criteria**: blocking call identified + fix plan (timeout, mock, or delete).

### spike-4: Redis re-read feature decision (Cluster P)

- **Assumption**: "The re-read tests in `test_complete_agent_session_redis_reread.py` assert behavior the implementation doesn't have. Either the feature was planned-not-shipped, or the tests are speculative."
- **Method**: git-blame the test file and the `_complete_agent_session` production function; check if a related feature branch exists or is pending.
- **Time cap**: 5 min.
- **Agent Type**: `Explore`.
- **Dispatch**: before deciding fix vs remove.
- **Exit criteria**: PM-facing recommendation: "implement the missing re-read" vs "delete the speculative tests".

## Data Flow

N/A — this plan is test-suite debt cleanup. No production data paths change except the guard-order reorder in `tools/doc_summary` and `tools/test_judge` (Cluster K).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| Plan `fix-pop-agent-session-tests.md` (#761) | Rewrote `_pop_agent_session` tests for in-place mutation | Did not prevent downstream drift: #1023's split changed `_pop_agent_session`'s signature + removed surrounding constants; `test_remote_update.py` and `test_silent_failures.py` broke at a distance |
| PR #1029 concurrency collapse | Changed `MAX_CONCURRENT_SESSIONS` semantics to a single 8-cap | `test_worker_concurrency.py::test_global_ceiling_across_multiple_chat_ids` stayed pinned to the old value of 2; never updated |

**Root cause pattern:** Tests reach into the `agent/agent_session_queue` namespace via `patch()`, symbol imports, and **source-code string grep** (Cluster O). Any refactor that moves, renames, or reorganizes that module breaks tests at a distance. This plan fixes the immediate breakages; a follow-up (out of scope) should audit whether tests should patch canonical modules instead of the shim.

## Architectural Impact

- **Coupling:** No change.
- **Interface changes:** Re-export missing symbols on `agent/agent_session_queue.py`. Small guard-order reorder in `tools/doc_summary` and `tools/test_judge` (moves empty-input validation before API-key check). Preserves public contracts.
- **Data ownership:** No change.
- **Reversibility:** Every change is in `tests/` or narrowly-scoped test-accessibility code. Fully reversible.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM check-ins

**Interactions:**
- PM check-ins: 1 (spike-4 decision on Cluster P: implement vs delete)
- Review rounds: 1 (one review on the final PR)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Popoto ORM tests require Redis |
| Anthropic API key available | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | LLM-backed unit tests (Clusters F, Unit-1) must run with a key; do not merge if skipped in CI |

Run all checks: `python scripts/check_prerequisites.py docs/plans/test_suite_debt_1041.md`

## Solution

### Key Elements

- **Shim re-export fix** (Cluster I): add missing symbols to `agent/agent_session_queue.py`; update 3 test patch targets.
- **Popoto fixture isolation fix** (Cluster J): after spike-1, apply a single-site conftest/fixture fix.
- **Tool API-key gate — hybrid** (Cluster K, per Cluster K research): REORDER guards in `tools/doc_summary/__init__.py` and `tools/test_judge/__init__.py` (moves empty-input validation above the API-key check), and SKIPIF-gate `tests/tools/test_classifier.py` (21 tests that require a real API path — no input-validation branch exists to reorder).
- **LLM-dependent unit tests** (Clusters F, Unit-1): gate `TestRealHaikuClassification`, `TestLlmClassification`, and `TestLiveHaikuReranking` with `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"))`. CI that has the key will still run them.
- **Symbol drift updates** (Clusters D, H): update mock signatures, assertion wordings, and plist labels.
- **Cluster E**: verify-then-fix. `_pop_agent_session` in `test_worker_drain.py` + `test_worker_concurrency.py` — update for current signature.
- **Cluster M**: rewrite `test_global_ceiling_across_multiple_chat_ids` against `MAX_CONCURRENT_SESSIONS=8`.
- **Cluster A reproduction** (`knowledge_search` namespace): reproduce isolation-vs-full-suite split; fix at import-order root cause, not downstream.
- **Cluster C** (reflection async drift): remove `asyncio.run(...)` wrappers since callables return `dict` synchronously.
- **Cluster G** (MagicMock sentinel): set `session_id="child-001"` on the `MagicMock` in `test_steer_child.py`.
- **Cluster L** (silent log regressions): restore `logger.warning` calls in cooldown load/save if they were removed accidentally; if intentional, update tests to assert new observable.
- **Cluster N** (`_poll_imap` new arg): pass `known_senders` in the 2 affected `test_email_bridge.py` tests.
- **Cluster O** (source-code string assertions): replace string-grep assertions with behavioral assertions, or update the grep target to the new split module. Each of the 4 tests gets its own decision.
- **Cluster P** (Redis re-read): decide per spike-4 whether to implement `_complete_agent_session` re-read logic or delete the 2 speculative tests.
- **Cluster Q** (hanging test file): per spike-3, add timeout / mock / or delete `test_worker_persistent.py` tests that hang.
- **Cluster R** (`|| true` install script): investigate whether the install script genuinely swallows errors; fix script OR loosen the test with justification.
- **Cluster S** (startup recovery — 5 additional tests): update patch targets and assertions to match the post-#1023 startup recovery code path.

### Flow

Baseline → spikes (1, 2, 3, 4) in parallel → PM decides Cluster P (spike-4 outcome) → cluster builds (parallel where independent) → per-cluster commits pushed to single feature branch → validator re-runs full suite after each cluster → final run green → merge PR → close #1041.

### Technical Approach

- **Single PR.** Branch `fix/test-suite-debt-1041`. All clusters commit to this branch with commit-message prefix `test(cluster-X): ...`. Incremental pushes allowed as clusters complete. Final commit has `Closes #1041`.
- **Spike first for ambiguous clusters (I, J, P, Q).** Do not touch code in those clusters until the spike returns.
- **Fix all failures.** No "close as pre-existing" path. The issue was filed specifically to drive all failures to zero; baseline-comparison is only used to confirm no NEW regressions get introduced during the fix.
- **Don't rewrite production code to satisfy tests** unless the production code is actually wrong. Only Cluster K's guard reorder and possibly Cluster L's missing `logger.warning` fall in the "production is wrong" bucket.
- **Leave runtime `pytest.xfail()` alone.** No xfail markers in the current suite — verified.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new `except Exception: pass` blocks introduced — plan is test updates plus narrow re-exports and guard reorders.
- [ ] Cluster L fix must restore or replace the `logger.warning` path — every `except Exception:` block touched must log something observable.

### Empty/Invalid Input Handling
- [ ] After Cluster K reorder, `tools/doc_summary.summarize_content("")` returns an error whose message contains `"empty"`.
- [ ] After Cluster K reorder, `tools/test_judge.*` returns an error whose message contains `"empty"` for blank test output or blank criteria.
- [ ] `test_empty_content_returns_error`, `test_whitespace_content_returns_error`, `test_empty_test_output_returns_error`, `test_empty_criteria_returns_error` all pass.

### Error State Rendering
- [ ] Not applicable — no user-visible output paths change.

## Test Impact

**Cluster I — Shim re-exports**
- [ ] `tests/integration/test_session_heartbeat_progress.py` — UPDATE: collect-clean once `HEARTBEAT_FRESHNESS_WINDOW` is re-exported
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestCheckRevivalTerminalFilter::test_revival_passes_non_terminal_branches` — UPDATE: patch target → `agent.session_revival.get_branch_state`
- [ ] `tests/integration/test_steering.py::TestResolveRootSessionId::test_no_double_hydration_when_handler_prehydrates` — UPDATE: assert against current location of `REPLY_THREAD_CONTEXT_HEADER`

**Cluster J — Popoto fixture isolation (~47 failures)**
- [ ] `tests/integration/test_agent_session_scheduler.py` — UPDATE conftest only
- [ ] `tests/integration/test_agent_session_queue_race.py` — UPDATE conftest only
- [ ] `tests/integration/test_bridge_routing.py` — UPDATE conftest only
- [ ] `tests/integration/test_connectivity_gaps.py` — UPDATE conftest only
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE conftest only
- [ ] `tests/integration/test_parent_child_round_trip.py` — UPDATE conftest only
- [ ] `tests/tools/test_telegram_history.py` — UPDATE conftest only

**Cluster K — Tool API-key gate (hybrid approach, per Cluster K research subagent)**
- [ ] `tools/doc_summary/__init__.py` — UPDATE: move empty-content check above API-key guard (lines ~63–73 move before line 55)
- [ ] `tools/test_judge/__init__.py` — UPDATE: move empty-check for `test_output` and `expected_criteria` above API-key guard (lines 72–76 move before lines 64–70)
- [ ] `tests/tools/test_classifier.py` — UPDATE: add `anthropic_api_key` fixture / `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"))` to all 21 tests (no input-validation path in `tools/classifier.py` to reorder)

**Cluster D — `_pop_agent_session` signature + plist**
- [ ] `tests/integration/test_remote_update.py::TestWorkerRestartCheck::test_worker_checks_flag_after_job_completion` — UPDATE: `pop_side_effect()` accepts new arg count
- [ ] `tests/integration/test_remote_update.py::TestServiceManager::test_update_plist_defined` — REPLACE: `com.valor.update` is gone; assert current labels

**Cluster E — StatusConflictError + concurrency semantics**
- [ ] `tests/integration/test_worker_drain.py::TestWorkerLoopDrain::test_worker_picks_up_second_job_via_event` — UPDATE for current signatures
- [ ] `tests/integration/test_worker_drain.py::TestWorkerLoopDrain::test_worker_drain_fallback_finds_job` — UPDATE
- [ ] `tests/integration/test_worker_drain.py::TestExitTimeDiagnostic::test_exit_diagnostic_logs_warning` — UPDATE

**Cluster M — Concurrency semaphore**
- [ ] `tests/integration/test_worker_concurrency.py::TestPerChatSerialization::test_global_ceiling_across_multiple_chat_ids` — REPLACE: rewrite against `MAX_CONCURRENT_SESSIONS=8` (post-#1029 value)

**Cluster A — knowledge_search namespace**
- [ ] `tools/knowledge_search/__init__.py` — UPDATE: fix import-order root cause surfaced in isolation-vs-full-suite reproduction
- [ ] `tests/unit/test_ui_app.py`, `test_knowledge_indexer.py`, `test_emoji_embedding.py`, `test_custom_emoji_index.py` — UPDATE or REPLACE depending on reproduction findings

**Cluster C — Reflection async drift**
- [ ] `tests/unit/test_reflections_package.py::TestMaintenanceCallables::test_run_legacy_code_scan_returns_valid` — UPDATE: drop `asyncio.run()`
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_skills_audit_no_script` — UPDATE: drop `asyncio.run()`
- [ ] `tests/unit/test_reflections_package.py::TestAuditingCallables::test_run_pr_review_audit_no_projects` — UPDATE
- [ ] `tests/unit/test_reflections_package.py::TestTaskManagementCallables::test_run_task_management_no_projects` — UPDATE
- [ ] `tests/unit/test_reflections_package.py::TestSessionIntelligenceCallable::test_run_no_sessions` — UPDATE
- [ ] `tests/unit/test_reflections_package.py::TestSessionIntelligenceCallable::test_run_with_mocked_sessions` — UPDATE

**Cluster F — Live API gate (plus 16 more Unit-1 tests)**
- [ ] `tests/*/TestLiveHaikuReranking::*` — UPDATE: `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"))`
- [ ] `tests/unit/test_intake_classifier.py::TestRealHaikuClassification::*` (9 tests) — UPDATE: same skipif
- [ ] `tests/unit/test_work_request_classifier.py::TestLlmClassification::*` (7 tests) — UPDATE: same skipif

**Cluster G — MagicMock sentinel**
- [ ] `tests/unit/test_steer_child.py::TestSteerChild::test_valid_steering` — UPDATE: configure `MagicMock(..., session_id="child-001")`
- [ ] `tests/unit/test_steer_child.py::TestSteerChild::test_parent_id_from_env` — UPDATE: same

**Cluster H — PersonaType + newsyslog**
- [ ] `tests/unit/test_enums.py::TestPersonaType::test_all_members` — UPDATE: 3 → 4
- [ ] `tests/unit/test_update_newsyslog.py::TestCheckNewsyslog::test_surfaces_action_when_sudo_needs_password` — UPDATE: match current action-message wording

**Cluster L — Silent log regressions**
- [ ] `tests/integration/test_silent_failures.py::TestLoadCooldownsLogging::test_file_read_failure_logs_warning` — UPDATE; if production log was removed accidentally, restore it
- [ ] `tests/integration/test_silent_failures.py::TestSaveCooldownsLogging::test_file_write_failure_logs_warning` — UPDATE; same

**Cluster N — `_poll_imap` signature**
- [ ] `tests/unit/test_email_bridge.py::TestPollImapBatchCap::test_batch_cap_limits_fetched_messages` — UPDATE: pass `known_senders`
- [ ] `tests/unit/test_email_bridge.py::TestPollImapBatchCap::test_batch_cap_exact_boundary` — UPDATE: same

**Cluster O — Source-code string assertions (4 tests)**
- [ ] `tests/unit/test_duplicate_delivery.py::TestCompletedSessionGuard::test_completed_session_skips_auto_continue` — REPLACE: behavioral assertion for completed-session guard instead of source-string grep
- [ ] `tests/unit/test_duplicate_delivery.py::TestCompletedSessionGuard::test_guard_is_before_nudge_routing` — REPLACE: behavioral assertion for ordering
- [ ] `tests/unit/test_worker_entry.py::TestWorkerStartupSequence::test_cleanup_orphaned_in_agent_queue_not_bridge` — UPDATE: grep target → `agent/session_pickup.py` or wherever `_cleanup_orphaned_claude_processes` now lives
- [ ] `tests/unit/test_agent_session_scheduler_kill.py::TestRecoveryExcludesKilled::test_recover_interrupted_agent_sessions_startup_filters_running` — UPDATE: assert on actual `.filter()` call args, not source strings

**Cluster P — Redis re-read (per spike-4 outcome)**
- [ ] `tests/unit/test_complete_agent_session_redis_reread.py::TestCompleteAgentSessionRedisReread::test_fresh_record_used_when_found` — DECISION pending spike-4: implement the re-read in `_complete_agent_session` OR DELETE
- [ ] `tests/unit/test_complete_agent_session_redis_reread.py::TestCompleteAgentSessionRedisReread::test_most_recent_record_chosen_when_multiple_found` — same

**Cluster Q — Hanging test file (per spike-3)**
- [ ] `tests/unit/test_worker_persistent.py` — DECISION pending spike-3: add per-test timeouts, mock the blocking call, OR delete the file if tests are redundant with integration coverage

**Cluster R — `|| true` swallow**
- [ ] `tests/unit/test_reflections_scheduling.py::TestInstallMechanism::test_remote_update_no_silent_failures` — UPDATE after investigating; if script is wrong, fix script; if test is too strict, loosen with justified comment

**Cluster S — Startup recovery (5 additional tests)**
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoverySkipsTerminal::test_startup_recovery_only_queries_running` — UPDATE: patch target / assertion for new code path
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoveryLocalSessionGuard::test_startup_recovery_abandons_local_sessions` — UPDATE
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoveryLocalSessionGuard::test_startup_recovery_recovers_bridge_sessions` — UPDATE
- [ ] `tests/unit/test_recovery_respawn_safety.py::TestStartupRecoveryLocalSessionGuard::test_startup_recovery_mixed_local_and_bridge` — UPDATE
- [ ] `tests/unit/test_zombie_session_resurrection.py::TestStartupRecoverySkipsTerminalSessions::test_legitimate_running_session_still_recovered` — UPDATE
- [ ] `tests/unit/test_zombie_session_resurrection.py::TestStartupRecoverySkipsTerminalSessions::test_mixed_terminal_and_running_only_recovers_running` — UPDATE

**Cluster Unit-8 — classifier heuristic fallback**
- [ ] `tests/unit/test_cross_wire_fixes.py::TestClassifierInformationalCompletion::test_qa_answer_classified_as_completion` — UPDATE: add skipif (no-key path drops to heuristic fallback that returns QUESTION)

## Rabbit Holes

- **Do NOT rewrite the shim pattern.** Auditing every test's patch target to address canonical modules is a ~30-file change. Save for follow-up.
- **Do NOT generalize Popoto fixture setup.** Fix the single root cause from spike-1 only.
- **Do NOT chase `black --check` output.** The 434-file report is a `pyproject.toml` config omission — separate chore.
- **Do NOT re-ship the old `com.valor.update` plist.** It was deliberately removed.
- **Do NOT expand `test_worker_persistent.py`** — if spike-3 shows the tests are low-value or duplicated, delete them.

## Risks

### Risk 1: Popoto spike finds no single root cause
**Impact:** Cluster J expands from 1 conftest fix to ~7 test-file rewrites. Schedule slips ~1 day.
**Mitigation:** Spike has an explicit exit criterion. Escalate to PM for appetite reset if no single cause.

### Risk 2: Cluster K reorder changes downstream error-text consumers
**Impact:** Callers that branch on the exact string `"ANTHROPIC_API_KEY or OPENROUTER_API_KEY required"` break.
**Mitigation:** Cluster K research already grepped for callers — none found. Add a test that asserts the new error-ordering contract.

### Risk 3: Cluster P decision is "implement the feature"
**Impact:** Plan expands to include non-trivial production change (Redis re-read in `_complete_agent_session`). Schedule slips 2–4 hours.
**Mitigation:** Spike-4 surfaces the decision before any code is written. If "implement," this plan still covers it; no re-scoping needed beyond a longer build-complete task.

### Risk 4: Shim fix masks deeper patch-target drift
**Impact:** Re-exports restore green but the next refactor breaks the same class of tests.
**Mitigation:** File a follow-up issue "audit test patch targets to hit canonical modules"; out of scope here.

### Risk 5: LLM-gated tests always skip in CI
**Impact:** CI runs green because 16+21 tests always skip — giving false confidence.
**Mitigation:** Add a Verification step that fails if `ANTHROPIC_API_KEY` is missing and any LLM test was skipped. CI environments where keys are present run the tests for real; dev machines gracefully skip.

## Race Conditions

No race conditions identified — changes are test-only or narrow re-exports/guards. All operations synchronous.

## No-Gos (Out of Scope)

- Refactoring `agent/agent_session_queue` shim pattern.
- Adding `[tool.black]` config to `pyproject.toml`.
- Per-cluster PRs — single PR per your call.
- Flaky-filter work from `test-reliability-flaky-filter.md`.
- E2E or performance suite cleanup (not run; no failures surfaced).
- Enabling `-n auto` parallelization (separate CI config change).

## Update System

No update system changes required — only test updates plus narrow re-exports and guard reorders. No scripts, binaries, or config files propagate.

## Agent Integration

No agent integration required — changes confined to `tests/`, `tools/doc_summary/`, `tools/test_judge/`, and `agent/agent_session_queue.py` re-exports. No MCP server, `.mcp.json`, or bridge surface area changes.

## Documentation

- [ ] Add a 3–5 line "Patch-target convention" callout to `tests/README.md` noting canonical-vs-shim guidance (prevents the next test-at-shim surprise).
- [ ] Update `docs/features/bridge-worker-architecture.md` only if Cluster I reveals the shim contract needs explicit documentation (likely skip — shim is meant to be transparent).
- [ ] No other documentation affected.

## Success Criteria

- [ ] `pytest tests/unit tests/integration tests/tools --tb=short` returns exit code **0** on main.
- [ ] **160 failures → 0**. 1 collection error → 0. 1 hang → resolved.
- [ ] No new `pytest.mark.skipif` added except the documented LLM-API-key cases (Clusters F + K-classifier + Unit-1 + Unit-8) and a single `(not REDIS)` guard if spike-1 demands it.
- [ ] Single PR `Closes #1041` on merge.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`) — `tests/README.md` patch-target note.
- [ ] No regressions vs `ba51c088` baseline (validator baseline-verifies after each cluster commit).
- [ ] `test_worker_persistent.py` does not hang in CI.

## Team Orchestration

Lead agent orchestrates; all work delegated to sub-agents. Single PR, commits organized per cluster.

### Team Members

- **Spike (popoto)**
  - Name: `popoto-spike`
  - Role: Root-cause Cluster J.
  - Agent Type: `debugging-specialist`
  - Resume: false

- **Spike (shim audit)**
  - Name: `shim-audit`
  - Role: Enumerate shim re-export gaps.
  - Agent Type: `Explore`
  - Resume: false

- **Spike (hang)**
  - Name: `hang-spike`
  - Role: Identify blocking call in `test_worker_persistent.py`.
  - Agent Type: `debugging-specialist`
  - Resume: false

- **Spike (redis re-read)**
  - Name: `redis-reread-spike`
  - Role: Decide implement-vs-delete for Cluster P.
  - Agent Type: `Explore`
  - Resume: false

- **Builder (shim + tool gates)**
  - Name: `shim-tool-builder`
  - Role: Cluster I re-exports; Cluster K reorder + skipif; Cluster L log restore.
  - Agent Type: builder
  - Resume: true

- **Builder (popoto)**
  - Name: `popoto-builder`
  - Role: Apply Cluster J fix.
  - Agent Type: builder
  - Resume: true

- **Builder (symbol drift)**
  - Name: `drift-builder`
  - Role: Clusters D, E, G, H, M, N, O.
  - Agent Type: builder
  - Resume: true

- **Builder (unit llm-gated + heuristic)**
  - Name: `llm-gate-builder`
  - Role: Clusters F + Unit-1 + Unit-8 skipif guards.
  - Agent Type: builder
  - Resume: true

- **Builder (unit import + async)**
  - Name: `unit-builder`
  - Role: Clusters A (knowledge_search), C (reflection async), S (startup recovery), R (install script).
  - Agent Type: builder
  - Resume: true

- **Builder (hang + redis-reread)**
  - Name: `tricky-builder`
  - Role: Clusters Q (after spike-3) and P (after spike-4, per PM decision).
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `test-validator`
  - Role: Re-run `pytest` after each cluster commit; baseline-verify no collateral damage.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Spike — Popoto root cause
- **Task ID**: spike-popoto
- **Depends On**: none
- **Assigned To**: popoto-spike
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Reproduce one failing test in isolation (e.g. `tests/tools/test_telegram_history.py::TestSearchLinks::test_search_by_query`).
- Identify corruption source (fixture teardown, Popoto config, Redis namespace collision).
- Propose single-site fix. Time cap: 15 min.

### 2. Spike — Shim re-export audit
- **Task ID**: spike-shim
- **Depends On**: none
- **Assigned To**: shim-audit
- **Agent Type**: Explore
- **Parallel**: true
- Grep every `patch\("agent\.agent_session_queue\.([A-Za-z_]+)"` across `tests/`.
- For each captured symbol, verify `agent/agent_session_queue.py` exports it today.
- Return missing-symbol list. Time cap: 5 min.

### 3. Spike — `test_worker_persistent.py` hang
- **Task ID**: spike-hang
- **Depends On**: none
- **Assigned To**: hang-spike
- **Agent Type**: debugging-specialist
- **Parallel**: true
- Run `pytest tests/unit/test_worker_persistent.py --timeout=30`; capture timeout traceback.
- Identify blocking call + propose timeout / mock / delete. Time cap: 10 min.

### 4. Spike — Redis re-read feature decision
- **Task ID**: spike-redis-reread
- **Depends On**: none
- **Assigned To**: redis-reread-spike
- **Agent Type**: Explore
- **Parallel**: true
- Read `_complete_agent_session` and blame the re-read test file; check for a related pending branch.
- Recommend: implement re-read vs delete speculative tests.
- PM makes final call. Time cap: 5 min.

### 5. Build — Shim re-exports + tool API-key hybrid + cooldown logs (Clusters I, K, L)
- **Task ID**: build-shim-tool
- **Depends On**: spike-shim
- **Validates**: `test_session_heartbeat_progress.py`, `test_recovery_respawn_safety.py::TestCheckRevivalTerminalFilter`, `test_steering.py::TestResolveRootSessionId`, `test_classifier.py`, `test_doc_summary.py::TestSummarizeValidation`, `test_test_judge.py::TestJudgeValidation`, `test_silent_failures.py`
- **Informed By**: spike-shim, Cluster K research
- **Assigned To**: shim-tool-builder
- **Agent Type**: builder
- **Parallel**: true (after spike-shim)
- Add missing re-exports in `agent/agent_session_queue.py` per spike-shim findings.
- Update 3 test patch targets (Cluster I).
- Reorder guards in `tools/doc_summary/__init__.py` and `tools/test_judge/__init__.py` (empty-check before API-key check).
- Add skipif-or-fixture to `tests/tools/test_classifier.py` (21 tests).
- Investigate cooldown logger.warning: if production regression, restore; else update tests (Cluster L).
- Commit message: `test(cluster-I+K+L): restore shim exports, reorder tool guards, gate classifier`.

### 6. Build — Popoto fixture fix (Cluster J)
- **Task ID**: build-popoto
- **Depends On**: spike-popoto
- **Validates**: 7 affected test files
- **Informed By**: spike-popoto
- **Assigned To**: popoto-builder
- **Agent Type**: builder
- **Parallel**: false
- Apply single-site fix from spike.
- Re-run 7 affected files; confirm ~47 tests flip FAIL→PASS.
- Commit: `test(cluster-J): restore popoto query results via conftest fix`.

### 7. Build — Symbol drift (Clusters D, E, G, H, M, N, O)
- **Task ID**: build-drift
- **Depends On**: none
- **Validates**: cluster-specific test files per Test Impact
- **Assigned To**: drift-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `test_remote_update.py` mock signature; remove `com.valor.update` assertion.
- Fix `test_steer_child.py` MagicMock sentinel (Cluster G).
- Update `PersonaType` count 3→4 and newsyslog action wording (Cluster H).
- Rewrite `test_global_ceiling_across_multiple_chat_ids` against 8 (Cluster M).
- Update `_pop_agent_session` callers in `test_worker_drain.py` (Cluster E).
- Pass `known_senders` in 2 `test_email_bridge.py` tests (Cluster N).
- Replace source-string greps with behavioral assertions in 4 Cluster O tests.
- Commit: `test(cluster-D+E+G+H+M+N+O): update symbol drift and signatures`.

### 8. Build — LLM API-key skipif guards (Clusters F, Unit-1, Unit-8)
- **Task ID**: build-llm-gates
- **Depends On**: none
- **Validates**: `TestLiveHaikuReranking`, `test_intake_classifier.py::TestRealHaikuClassification`, `test_work_request_classifier.py::TestLlmClassification`, `test_cross_wire_fixes.py::TestClassifierInformationalCompletion`
- **Assigned To**: llm-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="...")` to affected test classes.
- Commit: `test(cluster-F+Unit-1+Unit-8): gate LLM tests behind ANTHROPIC_API_KEY`.

### 9. Build — Import + async + startup + install clusters (A, C, S, R)
- **Task ID**: build-unit
- **Depends On**: none
- **Validates**: `test_custom_emoji_index.py`, `test_emoji_embedding.py`, `test_knowledge_indexer.py`, `test_ui_app.py`, `test_reflections_package.py`, `test_recovery_respawn_safety.py`, `test_zombie_session_resurrection.py`, `test_reflections_scheduling.py`
- **Assigned To**: unit-builder
- **Agent Type**: builder
- **Parallel**: true
- Reproduce Cluster A isolation/full-suite split; fix at `tools/knowledge_search/__init__.py` import-order root cause.
- Remove `asyncio.run(...)` wrappers in 6 Cluster C reflection tests.
- Update 5 Cluster S startup-recovery test patch targets / assertions.
- Investigate `|| true` in install script (Cluster R); fix script or justify test loosening.
- Commit: `test(cluster-A+C+S+R): fix import, async, startup-recovery, install drift`.

### 10. Build — Hang + Redis re-read (Clusters Q, P)
- **Task ID**: build-tricky
- **Depends On**: spike-hang, spike-redis-reread, PM decision on P
- **Validates**: `test_worker_persistent.py`, `test_complete_agent_session_redis_reread.py`
- **Informed By**: spike-hang, spike-redis-reread
- **Assigned To**: tricky-builder
- **Agent Type**: builder
- **Parallel**: false
- Apply spike-3 fix (timeout/mock/delete) to Cluster Q.
- Apply PM decision on Cluster P: implement re-read in `_complete_agent_session` or delete the 2 tests.
- Commit: `test(cluster-Q+P): unblock hanging test + resolve redis-reread decision`.

### 11. Validate — Full suite re-run
- **Task ID**: validate-suite
- **Depends On**: build-shim-tool, build-popoto, build-drift, build-llm-gates, build-unit, build-tricky
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit tests/integration tests/tools --tb=short`.
- Confirm exit code 0, 0 collection errors, 0 hangs.
- Baseline-verify no new regressions vs `ba51c088`.
- Report per-cluster before/after counts.

### 12. Document — Patch-target convention note
- **Task ID**: document-note
- **Depends On**: validate-suite
- **Assigned To**: shim-tool-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add 3–5 line callout to `tests/README.md` about preferring canonical-module patch targets.

### 13. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-suite, document-note
- **Assigned To**: test-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table below end-to-end.
- Confirm all Success Criteria met. Generate final report for #1041.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit -q --tb=no` | exit code 0 |
| Integration tests pass | `pytest tests/integration -q --tb=no` | exit code 0 |
| Tools tests pass | `pytest tests/tools -q --tb=no` | exit code 0 |
| No collection errors | `pytest tests/ --collect-only 2>&1 \| grep -c "ERROR"` | output == 0 |
| No hangs | `timeout 600 pytest tests/unit tests/integration tests/tools -q` | exit code 0 |
| Shim symbols exported | `python -c "from agent.agent_session_queue import HEARTBEAT_FRESHNESS_WINDOW, get_branch_state, REPLY_THREAD_CONTEXT_HEADER"` | exit code 0 |
| Empty-input returns "empty" (doc_summary) | `python -c "from tools.doc_summary import summarize_content; import os; os.environ.pop('ANTHROPIC_API_KEY', None); os.environ.pop('OPENROUTER_API_KEY', None); r=summarize_content(''); assert 'empty' in r['error'].lower()"` | exit code 0 |
| Empty-input returns "empty" (test_judge) | `python -c "from tools.test_judge import judge_tests; import os; os.environ.pop('ANTHROPIC_API_KEY', None); os.environ.pop('OPENROUTER_API_KEY', None); r=judge_tests('','criteria'); assert 'empty' in r['error'].lower()"` | exit code 0 |
| No stale xfails | `grep -rn 'pytest.xfail(' tests/ --include='*.py'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
