# Test Suite

Organized by test level and tagged by feature. Run `pytest --collect-only -q` for current counts.

## Running Tests

```bash
# By level (parallel by default — `-n auto --dist=loadfile` from pyproject.toml)
pytest tests/unit/               # Unit tests (~20 min parallel; --timeout=420 is pytest-timeout's per-test budget, not a run bound)
pytest tests/integration/        # Integration tests (~125s parallel, ~330s serial; needs Redis)
pytest -m e2e                    # E2E tests
pytest -m slow                   # Performance benchmarks

# Force serial (for debugging xdist races or running with breakpoints)
pytest tests/unit/ -n0
pytest tests/integration/ -n0

# By feature (works across all levels)
pytest -m sdlc                   # All SDLC pipeline tests (516)
pytest -m messaging              # All messaging tests (327)
pytest -m sessions               # All session tests (293)
pytest -m "sdlc or sessions"     # Combine features

# Targeted
pytest tests/unit/test_observer.py           # Single file
pytest tests/unit/test_observer.py::TestX    # Single class
```

### Parallel Execution Notes

`pytest-xdist` runs tests across N worker subprocesses (one per CPU). Two patterns matter when authoring tests:

1. **Per-process Redis db (claimed), and ownership is enforced.** Each pytest **process** gets a *unique* test db, claimed atomically from the pool `[1..15]` via a held `fcntl.flock` (#2060) in `pytest_configure`, before collection and therefore before any fixture. This is stronger than the old per-*worker* `gw{N}→db{N+1}` mapping: it prevents two concurrent pytest **processes** (a single-test run plus a background full-suite run) from both landing on db1 and `flushdb()`-ing each other's data mid-test. **Never construct a `redis.Redis(db=N)` from a number you derived yourself** — not from `PYTEST_XDIST_WORKER`, not from a literal, not by reading it back out of `POPOTO_REDIS_DB.connection_pool.connection_kwargs`. `tests.db_claim.claim_test_db()` and the `redis_test_url` fixture are the only sources; a test that genuinely needs a *second* db requests the `scratch_test_db` fixture, which claims another owned pool slot. A `flushdb()` against a db this process has not claimed now raises at its own line (#2628). The process environment is correct by construction: `pytest_configure` exports the claimed db as `REDIS_URL` process-wide, so a subprocess inherits the claim without any special handling; `tests.db_claim.subprocess_env` survives as an opt-in `PYTHONPATH` pinner, not the inheritance channel (see "Subprocess Test-DB Inheritance" below). See [`docs/features/test-db-ownership.md`](../docs/features/test-db-ownership.md).

2. **File-level grouping (`--dist=loadfile`).** All tests in one file land on the same worker. Files whose tests share global resources (npm/npx caches, host-level lockfiles, a single GitHub issue, an in-process module variable) rely on this — they otherwise collide under inter-test parallelism.
3. **Host-coupled liveness checks must mock their probe.** Tests that assert process-liveness behaviour (e.g. `test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit`) must not rely on a global `pgrep`/process scan, because a real `python -m worker` running on the dev box masks the test's fabricated process. Mock the probe (`monitoring.worker_watchdog._get_worker_pid`) to the test's own spawned PID so the assertion is deterministic with or without a coexisting real worker (issue #1578, Category E).
4. **Notify pub/sub is isolated by channel NAME, not by db.** Redis pub/sub is server-global — `PUBLISH`/`SUBSCRIBE` ignore the db `SELECT`, so the per-process db claim (pattern 1) does **not** isolate notifies. A fixture enqueue on db=`N` would otherwise publish to the one global `valor:sessions:new` channel that the launchd **live worker** (db=0) subscribes to, spinning up production queue loops for fixture sessions (issue #2147). The fix: `agent.agent_session_queue.notify_channel_for(client)` derives the channel from the client's db — db=0 keeps the canonical `valor:sessions:new`, any test db (db>=1) gets `valor:sessions:new:db{N}`. So fixture notifies land only on the db-scoped channel; the live worker never sees them. **Any test-spawned worker must connect on the claimed test db** (inherit `POPOTO_REDIS_DB` / `redis_test_url`, never hardcode db=0) so it derives the *same* `valor:sessions:new:db{N}` channel and joins the intra-test notify flow — a worker on db=0 would rejoin the production channel and defeat the isolation. The CI gate is the deterministic dual-channel probe in `tests/integration/test_notify_isolation.py`; the shared `tests/_worker_guard.py::assert_not_live_worker(pid)` guard (unit-covered in `tests/unit/test_worker_guard.py`) backstops the worker-lifecycle kill tests. See [`docs/features/test-isolation-hardening.md`](../docs/features/test-isolation-hardening.md) (root cause 4).

### Test isolation under xdist

Two cross-file phantom-failure mechanisms were root-caused and fixed in `tests/conftest.py` (umbrella issue #1897). Both are single-run, single-worker-sequence bugs — a test passes in isolation but fails only under a specific xdist worker composition, then passes again on re-run. If you hit a new instance of this class, read the fixture docstrings in `tests/conftest.py` first; they are the source of truth for the exact mechanism.

1. **Popoto db-cache split-brain.** `_popoto_modules_with_redis_db()` (consumed by the autouse `redis_test_db` fixture) memoizes which `popoto.*` submodules hold a `POPOTO_REDIS_DB` symbol so it doesn't walk all of `sys.modules` every test. The cache invalidates on a **compound trigger**: `len(sys.modules)` change (catches a brand-new, never-cached db-holder) **OR** per-entry object-identity divergence (catches an equal-count eviction-then-reimport, where a module is replaced under the same dotted name — e.g. by `mock_claude_sdk_cleanup` evicting `agent.*`). Count/len may gate additions but must **never** be the sole invalidation key — a sole count/name-set signal false-greens an equal-count module replacement (the stale object keeps its pre-swap `POPOTO_REDIS_DB` binding), and identity alone misses never-cached new holders (`any()` over an empty or partial cache is vacuously false). A stale cache leaves some popoto submodule's `POPOTO_REDIS_DB` pointed at the wrong test db, so an in-process write and a subprocess (or `Model.query.filter`) read can silently land on different Redis databases. This fix also subsumes issue #2037 (create-then-`filter` split-brain — same stale-cache mechanism, read path instead of write path).
2. **agent-hooks hooks-less-parent corruption.** The autouse `agent_hooks_consistency_guard` fixture detects and repairs a state where `sys.modules["agent"]` is cached but lacks a `hooks` attribute even though `sys.modules["agent.hooks"]` is still cached — CPython only rebinds a submodule onto its parent package at fresh-import time, so a partial `sys.modules` mutation (SDK swap, `importlib.reload`, `patch.dict`) can leave the parent "hooks-less" while the submodule cache survives. Any dotted-string `monkeypatch.setattr("agent.hooks...", ...)` then raises `AttributeError` during test setup, before the test body runs. The guard repairs by rebinding every cached `agent.*` submodule onto its parent package, in place. It deliberately does **not** evict: eviction also rebuilds the chain, but it strands any module-level `from agent import X` binding, so the test body calls a stale object while `patch("agent.X.seam")` patches the fresh one and the seam under test is never patched (#2551).

3. **A reload splits a shared exception class in two (#2603).** `importlib.reload(models.session_lifecycle)` keeps the module object and rebinds every class in it, so every module that imported `StatusConflictError` by name — including every test module, at collection time — keeps the old class and its `except`/`pytest.raises` silently stops matching. The autouse `shared_module_identity_guard` restores the original binding at teardown and warns, naming the test that reloaded (a teardown failure under `-W error::RuntimeWarning`). Exception classes are not the only casualties: a module-level **registry** is orphaned the same way, so the guard also covers `agent.index_drift`, `monitoring.bridge_watchdog`, and `monitoring.worker_watchdog` (#2628). **Do not reload a shared module in-process, and if you must, restore what it owns.** A test whose restore fixture holds a collection-time `from module import REGISTRY` binding will silently clean the orphan while the test writes into the live one — go through the module object. When you need to observe a genuinely first import, shell out to a fresh interpreter; an in-process reload cannot see one anyway, since everything is already cached.

A **cross-process** family (#2060, #2605) is not xdist-ordering at all: two separate pytest processes sharing a test db and `flushdb()`-ing each other, fixed by the per-process db claim described in pattern 1 above. #2605 was the subprocess corollary; a process-wide `REDIS_URL` export now makes the inheritance correct by construction for any subprocess, so `tests/db_claim.py::subprocess_env` survives only as an opt-in `PYTHONPATH` pinner (see "Subprocess Test-DB Inheritance" below). Re-deriving the db from `PYTEST_XDIST_WORKER` sends the child to a db this process does not own.

New instances of this class get filed under the umbrella issue [#1897](https://github.com/tomcounsell/ai/issues/1897) as they're observed and root-caused. `tests/unit/test_conftest_isolation_guards.py` is the deterministic regression suite locking in the fixes (Test A: agent-hooks guard repair; Test B: falsifiable len-vs-identity binding gate for the popoto cache; Test C: #2037 create-then-`filter` round trip; `TestPerProcessDbClaim`: #2060/#2605 per-process db claim, its consumers, and the process-wide `REDIS_URL` export's behavioral assertions; `TestExportedRedisUrlSurvivesSyntheticHookCalls`: the leak-detection probe proving nothing earlier in the file's own synthetic hook calls polluted the live session's `REDIS_URL`; `TestSharedExceptionIdentityGuard`: #2603 reload repair; `TestFlushOwnershipGuard` / `TestSessionClaimHook` / `TestReloadedRegistryIdentity`: #2628 db ownership, the popoto plugin repoint, and the registry reload leak) — start there when investigating a new phantom failure. See [`docs/features/test-isolation-hardening.md`](../docs/features/test-isolation-hardening.md) for a write-up of this single-run isolation work, and [`docs/features/test-concurrency-coordination.md`](../docs/features/test-concurrency-coordination.md) for the cross-run sentinel-ID namespacing.

### Un-awaited-coroutine leak guardrail (issue #2120)

A `pytest_runtest_teardown` hook in `tests/conftest.py` runs one `gc.collect()` at each
test's teardown inside a warning recorder and **re-emits** any captured `coroutine '...'
was never awaited` RuntimeWarning as a loud, test-attributed warning. This targets the
class of full-suite teardown wedge (#2118/#2120): a test hands an eagerly-created coroutine
to a seam that drops it (never awaited, never closed); when that coroutine is held alive in
an event-loop / task reference cycle, its finalization is deferred to a session-level
`gc.collect()` where the whole batch finalizes at once and — on a contended machine —
hangs the run before junitxml is written.

- **Normal runs:** the leak surfaces as a per-test warning in the summary (non-fatal) —
  `un-awaited coroutine leak surfaced at teardown of <nodeid>: coroutine '...' was never awaited`.
- **Fail-fast:** under `python -W error::RuntimeWarning -m pytest ...` the re-emitted warning
  becomes a per-test teardown **error**, converting a silent session-teardown wedge into an
  attributable failure at the offending test.
- **Attribution is best-effort:** a coroutine created in test A but not collected until B's
  teardown is attributed to B — the goal is to make the class loud and locatable, not
  forensically perfect.
- **Escape hatch:** set `COROUTINE_LEAK_GUARD=0` to disable the hook (e.g. to isolate its own
  cost). Regression suite: `tests/unit/test_coroutine_leak_guardrail.py`.

Fix at source, not by suppression: the three #2118 leaks (`run_email_bridge`,
`download_media`, `_ingest_attachments`) and the two #2120 residuals (`_evaluate_promise_async`
via `bridge/promise_gate.py::_run_async_safely`, `_worker_loop` in
`test_slow_redis_no_loop_freeze.py`) were each closed where the coroutine was dropped.

### Operator control-state isolation (issue #2552)

`bridge/catchup.py` anchors `CATCHUP_DISABLED_FLAG` to its own source tree (not to a
fixture-controlled path), which is correct for production but means the suite reads
live operator control state by default. On a host where an operator has paused message
recovery via `data/catchup-disabled`, 17 unit tests with no relation to catchup went red
purely because the file existed on that machine and not on another — a false red with no
explanation anywhere in the diff.

The autouse `isolate_catchup_kill_switch` fixture (`tests/conftest.py`) repoints
`bridge.catchup.CATCHUP_DISABLED_FLAG` at a per-test tmp path for the duration of every
test, covering all three production readers (`bridge/catchup.py`, `bridge/reconciler.py`,
`bridge/agent_catchup.py`) since they all import the symbol from `bridge.catchup` rather
than re-deriving the path. The flag is *repointed*, not stubbed — `catchup_disabled()`
still runs a real `Path.exists()` against a real filesystem path, so a test that wants
genuine disabled behavior can `touch` the redirected path.

- **Escape hatch:** set `CATCHUP_FLAG_ISOLATION=0` to skip the redirect. This exists so
  the negative control for #2552 stays permanently reproducible — running the suite with
  it set must reproduce the same 17 flag-caused failures on a host where the real
  `data/catchup-disabled` exists.
- **The general lesson:** a test reading a gitignored production flag file is a
  contamination *class*, not a one-off. Any future operator-toggled flag under `data/`
  needs the same treatment before it can silently flip unrelated tests red depending on
  which machine or checkout ran them.

### Known-failing clusters resolved on `main` (issue #1578)

The previously known-bad clusters on `main` were driven to green in #1578. The fixes were **test-only** — assertions were re-pointed to current source/templates, never weakened, and no test was deleted:

- Feature/refactor drift (Category A/C): `test_session_modal_liveness_render`, `test_bridge_relay`, `test_sdlc_skill_md_parity`, `test_reflection_scheduler` (`every:` not `interval:`), `test_model_relationships`, `test_long_task_checkpointing` (`skills-global`), `test_harness_oom_backoff` and `test_health_check_recovery_finalization` (`inspect.getsource` re-pointed from `_agent_session_health_check` to `_apply_recovery_transition`, where #1270 moved the OOM/reprieve logic).
- Env/install (Category D): `test_skills_audit` (`audit_skills` import path).
- Isolation (Category E): `test_watchdog_recovery` (mock `_get_worker_pid`), `test_memory_ingestion` (per-worker Redis key prefix), `test_compose_system_prompt` (deterministic read).
- Performance/timing (Category F): `test_memory_prefetch` and `test_benchmarks` thresholds recalibrated with inline measurement comments; `test_doc_impact_finder_sdk::TestLiveHaikuReranking` re-pointed to `impact_finder_core._rerank_single_candidate` with its prompt-builder contract.

## Feature Markers

Every test is auto-tagged by filename via `tests/conftest.py`. When a feature changes, run its marker to find tests that may need updating.

| Marker | What it covers | Example command |
|--------|----------------|-----------------|
| `sdlc` | Pipeline stages, observer, steering, hooks, state machine | `pytest -m sdlc` |
| `messaging` | Telegram routing, delivery, dedup, markdown, media | `pytest -m messaging` |
| `sessions` | Lifecycle, watchdog, stall detection, recovery, goals | `pytest -m sessions` |
| `summarizer` | Response summarization, nudge feedback, message formatting | `pytest -m summarizer` |
| `classifiers` | Intake, work requests, message quality, auto-continue | `pytest -m classifiers` |
| `validation` | Commit messages, plan sections, build checks, docs audit | `pytest -m validation` |
| `reflections` | Learning system, bug detection, scheduling, reports | `pytest -m reflections` |
| `tools` | Search, code execution, link analysis, image analysis | `pytest -m tools` |
| `jobs` | Job scheduling, queue priority, health monitoring | `pytest -m jobs` |
| `git` | Branch management, worktrees, workspace safety | `pytest -m git` |
| `models` | Redis/Popoto model relationships and persistence | `pytest -m models` |
| `monitoring` | Health checks, telemetry, watchdog, benchmarks | `pytest -m monitoring` |
| `impact` | Code and documentation impact analysis | `pytest -m impact` |
| `context` | Context modes, session tags, enrichment | `pytest -m context` |
| `config` | Configuration loading, settings, remote updates | `pytest -m config` |
| `sdk` | Claude SDK client, permissions, SDLC enforcement | `pytest -m sdk` |

Check counts with: `pytest -m <marker> --collect-only -q`

## Patch-Target Convention

When a test patches a symbol, patch the **canonical module that owns the symbol**, not the shim that re-exports it. After PR #1023 split `agent/agent_session_queue.py` into purpose-specific modules (`session_health`, `session_completion`, `session_executor`, `branch_manager`, etc.), tests that still patched `agent.agent_session_queue.<X>` silently no-op'd because the new modules import helpers via direct paths (`from agent.session_executor import steer_session as _steer_session`). The shim keeps re-exports for type checkers and editor navigation, but patch targets must hit the runtime import site. See #1041 and the post-mortem in its plan for details.

Attribute access through a module alias — `_queue.<X>`, where `_queue` is the hub module object — is the same mistake in a second syntax, and a write through it (`_queue.X = fake`) rebinds only the hub's copy, so the owning module never sees it. `tests/unit/test_hub_alias_references.py` guards both forms. It derives the hazard set from the hub's own AST (a name the hub imports and never references in its own body) rather than pinning a list, so it stays honest as the hub changes.

## Test-Reliability Layers

- **PR-branch flaky filter** (`/do-test`, PR #484, issue #476) — when a test fails on the PR branch, pytest retries the failure once; tests that pass on retry are dropped from the failure report. This layer addresses flakiness *on the PR branch*.
- **Baseline verification** (`/do-test`'s `baseline-verifier` subagent) — consistent failures are re-run against `main` to classify them as PR-introduced regressions (blocking) vs pre-existing (reported). See `docs/features/test-baseline-verification.md`.

The merge gate runs no tests (#2376) — the TEST stage owns the final full-suite run and the nightly regression run is the post-merge backstop.

## Directory Structure

```
tests/
├── conftest.py              # Root fixtures + feature auto-tagging
├── unit/                    # Pure logic, no external deps
├── integration/             # Requires Redis and/or network
├── tools/                   # Tool-specific tests (may need API keys)
├── e2e/                     # Full-stack synthetic flows
├── performance/             # Benchmarks and endurance
├── ai_judge/                # AI judge validation
```

## Test Index by Feature

### `messaging` — Telegram message handling

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_bridge_logic.py` | 40 | Group-to-project mapping, routing |
| unit | `test_bridge_shutdown.py` | 5 | Graceful shutdown task cancellation |
| unit | `valor_telegram/test_valor_telegram_parsing.py` | 23 | `parse_since`, `resolve_chat`, timestamp/relative-age formatting, CLI arg parsing |
| unit | `valor_telegram/test_valor_telegram_cli_send.py` | 16 | `cmd_send` |
| unit | `valor_telegram/test_valor_telegram_cli_read.py` | 30 | `cmd_read`: ambiguity handling, flags, did-you-mean, project scoping |
| unit | `valor_telegram/test_valor_telegram_cli_chats.py` | 10 | `cmd_chats` search and project scoping |
| unit | `valor_telegram/test_valor_telegram_rtr.py` | 18 | Read-the-room secondary consumer, `cmd_send` RTR path, promise gate |
| unit | `valor_telegram/test_valor_telegram_await.py` | 10 | Await/settle timing for send |
| unit | `valor_telegram/test_valor_telegram_chat_log.py` | 5 | Chat-log recording |
| unit | `valor_telegram/test_valor_telegram_voice_flag.py` | 3 | Voice-note payload flags |
| unit | `test_media_handling.py` | 17 | Media attachment handling |
| unit | `test_transcript_liveness.py` | 12 | Transcript state management |
| unit | `test_messenger.py` | 11 | Message formatting and delivery |
| unit | `test_duplicate_delivery.py` | 7 | Duplicate message prevention |
| unit | `test_file_extraction.py` | 20 | File extraction from messages |
| integration | `test_message_routing.py` | 21 | Message routing end-to-end |
| integration | `test_reply_delivery.py` | — | Reply delivery flow |
| integration | `test_unthreaded_routing.py` | 7 | Unthreaded message routing |
| e2e | `test_message_pipeline.py` | 37 | Full routing → context → response flow |

### `messaging` — Email bridge

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_email_bridge.py` | 31 | Parsing, SMTP output, batch cap, env loading |
| integration | `test_email_bridge.py` | 5 | Inbound routing, thread continuation, health timestamp |

### `sdlc` — Pipeline stages and observer

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_observer.py` | 81 | Stage detection, routing, progression |
| unit | `test_skills_audit.py` | 77 | Skills directory structure validation, rule-19 husk detection, `--fix` auto-prune |
| unit | `test_pipeline_integrity.py` | 31 | Pipeline state preservation |
| unit | `test_post_tool_use_sdlc.py` | 31 | Post-tool SDLC hook execution |
| unit | `test_pipeline_graph.py` | 29 | Pipeline graph visualization |
| unit | `test_observer_early_return.py` | 18 | Early return optimization |
| unit | `test_pipeline_state.py` | 15 | Pipeline state transitions |
| unit | `test_pipeline_state_machine.py` | 137 | `PipelineStateMachine` transitions, outcome classification, opt-in predecessor backfill (`_backfill_predecessors`, `_reaches_issue`) |
| unit | `test_sdlc_stage_marker.py` | 25 | Stage marker writes via CLI (session resolution, issue-number fallback, opt-in predecessor backfill on `in_progress`/`completed`) |
| unit | `test_sdlc_lease_helper_binding.py` | 10 | Lease helpers stay unsnapshotted in `sdlc_dispatch`/`sdlc_meta_set`/`sdlc_stage_marker`: per-module globals check, repo-wide AST sweep, and a behavioral late-patch assertion (#2469, #2637) |
| unit | `test_sdlc_stage_query.py` | 17 | Stage query CLI (session-id and issue-number resolution) |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_core.py` | 15 | `ensure_session` create/reuse, CLI output, local message text |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_short_circuit.py` | 14 | Env-var short-circuit for bridge-initiated sessions, identifier mismatch |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_adoption.py` | 27 | Ownerless adoption, lane-slug minting at lane start, `--kill-orphans` |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_issue_lock.py` | 16 | Issue-level ownership lock wiring at all return points (#1954) |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_run_identity.py` | 23 | Verified run-id reuse, supervised-run signal/module, owned-run self-recognition |
| unit | `sdlc_session_ensure/test_sdlc_session_ensure_lease_identity.py` | 19 | Lease-heartbeat spawn identity, durable run identity, anchor write on lease confirmation |
| unit | `sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py` | 33 | `DISPATCH_RULES` wiring and rows 1–10, verdict normalization, plan-existence gate |
| unit | `sdlc_router_decision/test_sdlc_router_decision_verdict_staleness.py` | 14 | Review and critique verdict staleness |
| unit | `sdlc_router_decision/test_sdlc_router_decision_convergence.py` | 26 | Convergence latch, dead-end recovery, marker desync, G5 loop bound |
| unit | `sdlc_router_decision/test_sdlc_router_decision_post_patch.py` | 18 | Row 8b ownership of stale verdicts and its disjointness/failure paths |
| unit | `sdlc_router_decision/test_sdlc_router_decision_with_concerns.py` | 16 | READY-WITH-CONCERNS scoping, rule ordering, termination |
| unit | `test_sdlc_utils.py` | 6 | Shared `find_session_by_issue()` helper |
| unit | `test_observer_message_for_user.py` | 11 | Observer user messaging |
| unit | `test_sdlc_env_vars.py` | 10 | SDLC environment variable injection |
| unit | `test_stop_reason_observer.py` | 7 | Stop reason classification |
| unit | `test_sdlc_mode.py` | 6 | SDLC mode enforcement |
| unit | `test_pre_tool_use_hook.py` | 6 | Pre-tool hook validation |
| unit | `test_stop_hook.py` | 12 | Stop hook enforcement |
| unit | `test_sdlc_reminder.py` | — | SDLC reminder messaging |
| integration | `test_steering.py` | 32 | Steering queue push/pop/clear |
| integration | `test_cross_repo_build.py` | 8 | Cross-repo build flow |
| integration | `test_artifact_inference.py` | 15 | Artifact-based pipeline stage inference (real gh CLI + filesystem) |
| unit | `test_continuation_pm.py` | 8 | Continuation PM creation, depth cap, dedup, steer failure fallback |
| unit | `test_do_plan_critique_barrier.py` | — | Roster membership gate: terminal-fence detection, missing-critic gap surfacing, incomplete-roster STOP verdict (#1690) |
| integration | `test_parent_child_round_trip.py` | 11 | Parent-child linkage, dev session completion steering, continuation PM round-trip |
| unit | `test_pm_progress_updates.py` | 12 | Locks the PM role doc's evidence-bearing progress-update guidance (prompt-text anchors) and characterizes which taught phrasings clear `bridge/promise_gate.py::_evaluate_promise_heuristic` on the deterministic fallback branch (#2664) |

### `sessions` — Session lifecycle and health

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_stall_detection.py` | 49 | Stall detection, backoff, retry |
| unit | `test_goal_gates.py` | 37 | Goal gate evaluation |
| unit | `test_session_watchdog.py` | 35 | Health assessment, error cascades |
| unit | `test_open_question_gate.py` | 32 | Open question detection |
| unit | `test_pending_recovery.py` | 21 | Pending stall recovery (consolidated) |
| unit | `test_escape_hatch.py` | 18 | Escape hatch for stuck sessions |
| unit | `test_session_status.py` | 15 | Session status tracking |
| unit | `test_worker_entry.py` | 24 | Worker entry point startup, config loading, argument parsing |
| integration | `test_agent_session_lifecycle.py` | 58 | Session lifecycle, history, summarizer |
| integration | `test_lifecycle_transition.py` | 16 | Session state transitions |
| integration | `test_session_heartbeat_progress.py` | 12 | Two-tier no-progress detector: dual heartbeat freshness, Tier 2 reprieve gates, recovery_attempts/reprieve_count fields, DISABLE_PROGRESS_KILL kill-switch |
| unit | `test_session_health_tool_timeout.py` | 4 | Wedge-signal reset on tool_timeout requeue: regression for issue #1762 double-count loop, genuine post-recovery exhaustion, save-error resilience, degraded notice on terminal failure |
| unit | `test_pid_fence.py` | 50 | `(pid, create_time)` execution fence: `create_times_match` tolerance rules, the `CREATE_TIME_TOLERANCE_S` pin and its fail-safe error direction, NaN/uncoercible input, `stamp_execution_spawn` (including the observable WARNING on save failure and the unbounded-`spawn_history` design pin), and `find_live_session_by_pid` match resolution — fenced-beats-pid-only, legacy fallback, multi-match WARNING, blinded-cohort continuation |
| unit | `test_session_health_fence_guards.py` | 16 | The two ENFORCING, unshadowed fence guards: `_has_progress` (a recycled pid probing as hung must not prematurely release a progressing session; a fence-verified hang still does) and `_owned_task_hang_check` |
| unit | `test_session_health_reprieve_fence.py` | 15 | `_tier2_reprieve_signal`'s enforcing fence (#2518): a recycled or unreadable fence withdraws the reprieve at both reprieve-granting return points, an unfenced legacy row keeps it, the compaction gate short-circuits first, and the live module carries no fence config flag |
| unit | `test_session_health_orphan_reap.py` | 19 | In-process orphan reap and the fenced staged-SIGKILL drain: `(pid, create_time)` staging, drain-time re-verification, and the legacy-row policy (SIGTERM on a liveness probe, no SIGKILL escalation) |
| unit | `test_session_health_orphan_process_reap.py` | 48 | Cross-process orphan reaper gates, with `find_live_session_by_pid` mocked to isolate them; the scan itself is covered unmocked in `test_orphan_reap_forward_scan.py` |
| unit | `test_session_health_subprocess_kill.py` | 33 | Recovery SIGTERM→SIGKILL escalation and the fenced pre-cancel snapshot: a legacy row yields `pid_snapshot=None` instead of failing open into a real kill |
| unit | `test_worker_session_sweep.py` | 19 | Dead-worker startup sweep, including all three fence branches (dead / recycled / matching), status-index scoping, and sweep-exactly-once |
| integration | `test_orphan_reap_forward_scan.py` | 17 | Ownership resolution against REAL Redis rows with `find_live_session_by_pid` unmocked: a live fenced session is not reaped (the canary assertion), orphans still are, duplicate fence pids resolve by identity rather than `frozenset` order, and a blinded status cohort fails toward protected |
| unit | `test_fence_census.py` | 22 | The `tools/check_fence_census.py` anti-criterion: green state at HEAD (the Verification row), the RED-state proof against `tests/fixtures/fence_census_violator/` naming both violating functions, exemption-marker line precision, and guard recognition (predicate call or forwarding both fence halves) |
| unit | `test_update_stale_session_fence.py` | 17 | `/update`'s stale-session cleanup: fence-live rows skipped at any age and counted separately, fence-dead rows still deferring to the recency and age gates, the two reason strings, and the caller's three-value unpack |
| unit | `test_dashboard_liveness_probe.py` | 24 | The fenced two-argument dashboard probe: matching fence → live, recycled fence → not-live, legacy row with a live PID → unknown, plus the unreadable-identity branches |
| integration | `test_dashboard_liveness_endpoint.py` | 6 | `/dashboard.json` carries both fence halves and renders matching / recycled / legacy rows as live / not-live / unknown |
| e2e | `test_session_continuity.py` | 11 | Session creation, resume, transcript |

### `summarizer` — Response processing

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_summarizer.py` | 158 | Response summarization, classification |

### `classifiers` — Message classification

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_message_quality.py` | 30 | Message quality scoring |
| unit | `test_intake_classifier.py` | 23 | Message classification |
| unit | `test_auto_continue.py` | 22 | Auto-continue logic |
| unit | `test_work_request_classifier.py` | 16 | Work request classification |
| integration | `test_stage_aware_auto_continue.py` | 39 | Stage-aware auto-continue |
| tools | `test_classifier.py` | 17 | Classifier tool tests |

### `validation` — Quality checks and parsing

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_docs_auditor_substrate.py` | 130 | Documentation reference validation |
| unit | `test_hook_target.py` | 128 | Shared hook-payload target resolution and scope filtering (`hook_target.py`) |
| unit | `test_validate_no_gos_justification.py` | 77 | No-Gos section justification validation |
| unit | `test_validate_file_contains.py` | 49 | Required-content file validation, payload-targeted |
| unit | `test_validate_test_impact.py` | 42 | Test impact section validation |
| unit | `test_validate_documentation_section.py` | 41 | Documentation section validation, payload-targeted |
| unit | `test_validate_verification_section.py` | 31 | Verification validation |
| unit | `test_features_readme_sort.py` | 27 | README table sorting |
| unit | `test_verification_parser.py` | 58 | Verification section parsing (per-block table scoping, `SkippedTable`) |
| unit | `test_validate_build.py` | 43 | `scripts/validate_build.py` execution loop (30s-timeout SKIP, cross-runner agreement with `agent.verification_parser` on parse-only fixtures) |
| unit | `test_validate_commit_message.py` | 16 | Commit message format |
| unit | `test_validate_sdlc_on_stop.py` | 12 | SDLC stop validation |
| unit | `test_build_validation.py` | 6 | Build process validation |
| unit | `test_hub_alias_references.py` | 5 | Module-alias reference guard (#2876): no tracked Python reaches an `agent.agent_session_queue` pure re-export through a module alias. Hazard set derived from the hub's AST, not pinned; discovery via `git ls-files`, no exemption set |
| unit | `test_stale_reference_sweep.py` | 3 | Stale-prose sweep (#2853, #2839): the unregistered reflection name, the two deleted granite package paths, and a scoped cadence-wording anti-criterion. Enumerates via `git ls-files` (untracked scratch Markdown is not repo content) and assembles every compared token by concatenation so the file cannot trip its own sweeps |
| unit | `test_site_graph_consistency.py` | 2 | Public-site knowledge-graph staleness (#2531): every `data-files` chip reference resolves to a `graph.js` node; frameworks named by the graph are still declared dependencies |

### `reflections` — Learning system

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_reflections.py` | 39 | LLM reflection, bug detection |
| unit | `test_reflection_scheduler.py` | 35 | Cron-based scheduling |
| unit | `test_reflections_multi_repo.py` | 21 | Multi-repo reflections |
| unit | `test_reflections_report.py` | 20 | Reflection reports |
| unit | `test_reflections_scheduling.py` | 19 | Launchd infrastructure |
| unit | `test_reflection_model.py` | 12 | Reflection model: mark_completed(), run_history append |
| integration | `test_reflections_redis.py` | 20 | Reflection persistence |

### `tools` — Individual tool tests

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| tools | `test_telegram_history.py` | 47 | Message storage, search, links |
| tools | `test_link_analysis.py` | 25 | URL extraction, metadata |
| tools | `test_code_execution.py` | 24 | Code execution, safety checks |
| tools | `test_test_judge.py` | 16 | AI judge validation |
| tools | `test_doc_summary.py` | 14 | Document summarization |
| tools | `test_image_analysis.py` | 12 | Image analysis |
| tools | `test_search.py` | 11 | Web search |
| ai_judge | `test_ai_judge.py` | 24 | AI judge prompts, evaluation |

### `jobs` — Job scheduling and queue

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_job_hierarchy.py` | 22 | Job priority and hierarchy |
| unit | `test_agent_session_queue_revival_helper.py` | 7 | Queue revival prompt helper, cooldown tracking |
| integration | `test_enqueue_continuation.py` | 29 | Continuation job enqueuing |
| integration | `test_job_scheduler.py` | 21 | 4-tier priority, FIFO |
| integration | `test_job_health_monitor.py` | 20 | Job health monitoring |
| integration | `test_job_queue_race.py` | 13 | Race condition prevention |

### `git` — Version control operations

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `worktree_manager/test_worktree_manager_cleanup.py` | 25 | Slug validation, cleanup after merge, stale-worktree recovery |
| unit | `worktree_manager/test_worktree_manager_creation.py` | 8 | `create_worktree` stale recovery, `get_or_create_worktree` |
| unit | `worktree_manager/test_worktree_manager_busy_guards.py` | 30 | Busy check/probe, session scan, live-process and removal guards |
| unit | `worktree_manager/test_worktree_manager_venv_provisioning.py` | 39 | Branch verification, interpreter-pin resolution, venv provisioning wiring |
| unit | `worktree_manager/test_worktree_manager_uncommitted.py` | 5 | Preserving uncommitted changes |
| unit | `test_git_state_guard.py` | 21 | Git state validation |
| unit | `test_workspace_safety.py` | 18 | Workspace safety checks |
| unit | `test_branch_manager.py` | 11 | Branch creation/deletion |
| unit | `test_symlinks.py` | 6 | Symlink handling |

### `models` — Data persistence

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_model_relationships.py` | 30 | Redis model relationships |
| unit | `test_document_chunk.py` | 7 | DocumentChunk model, import, search behavior |
| unit | `test_chunking.py` | 15 | Chunking engine: heading-aware, token-count, overlap |
| unit | `test_memory_model.py` | 151 | Memory model (decay, confidence, write filter, bloom) |
| unit | `test_memory_hook.py` | 135 | PostToolUse thought injection, sliding window |
| unit | `memory_extraction/test_memory_extraction_post_session.py` | 20 | Post-session Haiku extraction driver, session cap |
| unit | `memory_extraction/test_memory_extraction_outcome_detection.py` | 37 | Outcome detection, bigrams, act rate, history, LLM judge |
| unit | `memory_extraction/test_memory_extraction_parsing.py` | 48 | Categorized-observation parsing, JSON payload extraction, post-merge learning |
| unit | `memory_extraction/test_memory_extraction_refusal_filters.py` | 36 | Refusal detector and scoping-boilerplate filters, narrowness guards |
| unit | `memory_extraction/test_memory_extraction_event_loop_safety.py` | 7 | No nested `asyncio.run()` on the hook subprocess path |
| unit | `test_memory_ingestion.py` | 89 | Telegram message memory ingestion |
| integration | `test_redis_models.py` | 30 | Popoto model CRUD |

### `monitoring` — Observability

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_telemetry.py` | 27 | Telemetry data collection |
| unit | `test_health_check.py` | 12 | Health monitoring |
| unit | `test_bridge_watchdog.py` | — | Bridge watchdog |
| unit | `test_watchdog_log_isolation.py` | 17 | Import-time logging isolation for `monitoring/bridge_watchdog.py`, `monitoring/worker_watchdog.py`, `scripts/log_rotate.py` — no root handler, no file opened at import, config only from `__main__` (#2643) |
| unit | `test_doctor_console_scripts.py` | 48 | Doctor's `[project.scripts]` health check, both halves: PATH resolution into a repo venv bin dir with three-state remedy attribution (#2566/#2665), and interpreter identity for the winning script's shebang (`ok`/`missing`/`off-pin`/`outside`/`unverified`, #2748) |
| integration | `test_connectivity_gaps.py` | 12 | Connectivity failure handling |
| integration | `test_silent_failures.py` | 7 | Silent failure detection |
| performance | `test_benchmarks.py` | 16 | Latency, throughput, memory |

### `impact` — Code/doc change analysis

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_doc_impact_finder.py` | 21 | Documentation impact analysis |
| unit | `test_code_impact_finder.py` | 24 | Code file impact analysis, empty-chunk filtering, degraded-run CLI exit (#2499) |
| unit | `test_cross_repo_gh_resolution.py` | 11 | Cross-repo GitHub resolution |
| unit | `test_cross_wire_fixes.py` | 7 | Cross-wire fix application |
| integration | `test_doc_impact_finder_sdk.py` | 13 | Doc impact with SDK |

### `context` — Context and tagging

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_session_tags.py` | 33 | Session tagging |
| unit | `test_context_modes.py` | 27 | Context mode selection |
| unit | `test_pm_channels.py` | 19 | PM channel routing |

### `config` — Configuration and deployment

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| integration | `test_remote_update.py` | 29 | Remote update execution |
| unit | `test_hook_interpreter.py` | 16 | Hook interpreter contract (#2503): bare-`python` ban on generated commands, AST floor check that global-scope scripts stay `MIN_GLOBAL_PYTHON`-clean, real `env -i` execution of the shim and of every global script, worktree venv precedence, double-`/update` idempotence |
| unit | `test_migrate_strip_pid_fields.py` | 24 | The pid-field strip migration (#2518 D1, refactored onto the shared engine by #2524): the zero-record fork (#2543) — a blinded scan exits 2, distinct from the per-record-error 1, while a SCAN-confirmed empty keyspace exits 0, with a truncated or raising SCAN failing closed — per-record error isolation, logs landing on stdout as observed from a real subprocess, and the wrapper's captured output asserted NON-EMPTY with the `Stats: {'total_records` marker — a `grep` for a `result.stdout` token passes against the stderr bug this fixes. Sets `REDIS_URL` to the claimed test db: the helper shells out with `--apply` and would otherwise rewrite production rows |
| unit | `test_strip_migration_shared.py` | 71 | The shared `scripts/_strip_migration.py` engine (#2524): engine-level tests exercise the scan, the zero-record fork in both directions with the raw hash SCAN faked (#2543), and the `clean_indexes()` sweep once; cross-script invariants are parametrized over all three delegates (`migrate_strip_pid_fields.py`, `migrate_strip_pty_fields.py`, `migrate_schema_diet_fields.py`) to catch any future clone-instead-of-import drift, and pin both fork directions per script end-to-end through `main` (blinded → exit 2, empty keyspace → exit 0) since the engine-level exit tests feed a literal stats dict. `TestTheDiscriminatorSeamItself` is the one class that does NOT fake the SCAN — it runs `agent_session_hash_count()` against real ORM-created rows, because a wrong answer there fails the guard open in exactly the #1720 case it exists to catch while every faked test stays green |
| unit | `test_migrations.py` | 10 | Pipeline-ledger backfill migration, plus `strip_pid_fields_v2` registration and its ordering (after v1, before the phantom purge) |
| unit | `test_migration_index_repair.py` | 30 | The shared guarded index reconstruction for the five rename migrations (#2544): every fail-closed branch of `reconstruct_agent_session_indexes` (skipped `(0, 0)` repair, raising repair, error accumulation) with a positive control, plus cross-script invariants parametrized over all five call sites so a future hand-copy of the guard fails, and end-to-end fail-closed coverage for the two live registry entries |
| e2e | `test_config_bootstrap.py` | 13 | Config loading, health checks |

### `sdk` — Claude SDK integration

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_sdk_client_sdlc.py` | 32 | SDK SDLC enforcement |
| unit | `test_sdk_client.py` | 7 | SDK client basics |
| unit | `test_sdk_permissions.py` | 7 | SDK permissions |
| unit | `test_workflow_sdk_integration.py` | 6 | Workflow SDK integration |

### `session_runner` — Headless session runner (post-#1924 substrate)

The PTY substrate (`agent/granite_container/`, `tests/unit/granite_container/`,
`tests/granite_faults/`) was deleted by the granite-pty-teardown cutover
(#1924). The replacement execution leg — `agent/session_runner/` — is covered
here.

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `session_runner/test_runner_turns.py` | 19 | Single-session PM loop: simplified `[/user]`/`[/complete]` route table, wrapup guard, bounded nudges, boundary steering, compliance-miss accounting, `session_events` entry cap |
| unit | `session_runner/test_runner_dev_subagent.py` | 7 | Dev agent definition contract (continuation/steering/rails baked in), PM prime spawn-once contract, ResumeContext four-scalar seam |
| unit | `session_runner/test_runner_preempt.py` | 19 | Steer-preempt (D4): generation-token guard, kill-at-boundary race (pending steers re-pushed on loop exit), SIGTERM→SIGKILL escalation, timeout-as-preempt; plus the runner-path fenced-execution-record stamping (full record incl. non-None `pid_create_time`, per-turn re-stamp, scoped 5-field save) |
| unit | `session_runner/test_runner_resume.py` | 19 | Four-scalar resume consumption, cwd-scoped resume (Race 3), stale-UUID fallback, skip-prime, capture-at-init (Race 5) + off-loop version probe, `dev_agent_id` sidechain capture, turn-history mirror (bounded, never read on resume) |
| unit | `session_runner/test_runner_liveness.py` | 23 | Role-aware turn timeout table, subprocess-death/hang/missing-binary classification (wedge-coverage replacement), `on_stdout_event`/`on_init`/`on_spawn` adapter wiring |
| unit | `session_runner/test_headless_role_driver.py` | 18 | `HeadlessRoleDriver` turn dispatch, prime injection, hook-edge turn-end reconciliation, nonzero-exit-no-result classification |
| unit | `session_runner/test_router_classification.py` | 6 | PM-prefix classifier: strict-token payloads, fallback token stripping (no raw `[/user]` ever delivered) |
| unit | `session_runner/test_hook_edge_notifications.py` | 21 | Hook settings generation, NDJSON edge consumer, Notification envelopes |
| unit | `session_runner/headless_hook_probe.py` | — | Support module (no tests): real-CLI turn-end + prime-resolution probe harness, salvaged from the deleted granite-faults tree |
| integration | `test_runner_dispatch_e2e.py` | 2 | Executor → `SessionRunner` → `HeadlessRoleDriver` → fake harness → delivery callback; the anti-"built-but-never-wired" gate |
| integration | `test_headless_probe_e2e.py` | 4 | Subscription-auth env contract (always-on) + real `claude -p` turn-end/prime-resolution probes, gated on `HEADLESS_PROBE_SMOKE=1`; the canary for new `claude` releases |

### Other

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_nightly_regression_tests.py` | 107 | Nightly regression runner: widened-collection argv, process-group ownership, run-integrity guard (coverage floor, fixture-error ceiling, signal-death), serial re-confirmation trust, collection-aware re-baseline/seed escalation, `_fatal`/`load_env_or_die` refusal paths, JSON report parsing, Telegram alerting |
| unit | `test_update_nightly_tests_staleness.py` | 11 | Three-way install-outcome classification (`installed`/`skipped`/`failed`) and the `/update` staleness warning clock (`max(plist_mtime, run_at)`) |
| unit | `test_template_filter_registry.py` | 4 | Dashboard Jinja filter guards (#2719): every template's filter demand resolves against `ui.app.register_template_filters`; filter-key equality against a `Jinja2Templates`-shaped env; registrar-completeness; no test file hand-copies a `.filters[...]` registration |
| unit | `test_analytics_stats_render.py` | 3 | Render coverage for `_partials/analytics_stats.html`'s two `\| usd` cost cards, including the sub-cent-renders-as-$0.01 case |
| integration | `test_install_nightly_tests.py` | 13 | `install_nightly_tests.sh`: worktree refusal precedes the role gate, `has_worker_role()` gate text, the shipped success-marker `scripts/update/service.py` depends on |
| e2e | `test_telegram_flow.py` | — | Live Telegram flow stubs |

Dashboard render-test fixtures (`test_session_modal_liveness_render.py`,
`test_per_project_modal.py`, `test_analytics_stats_render.py`) build a bare
`jinja2.Environment` and must obtain filters by calling
`ui.app.register_template_filters(env)` — never by hand-copying individual
`env.filters["name"] = ...` assignments. Three independent hand-rolled filter
lists (production, plus two divergent test stand-ins) is exactly what
shipped `TemplateRuntimeError: No filter named 'usd' found` past all three
call sites (#2719); `test_template_filter_registry.py`'s no-hand-copy guard
enforces this in CI, not just in this note.

## Fixtures

| Fixture | Scope | Source | Purpose |
|---------|-------|--------|---------|
| `mock_claude_sdk_cleanup` | autouse | `conftest.py` | SDK mock cleanup between tests |
| `redis_test_db` | autouse | `conftest.py` | Per-worker Redis db isolation |
| `isolate_catchup_kill_switch` | autouse | `conftest.py` | Repoints the operator catchup kill-switch flag at a per-test tmp path (issue #2552) |
| `sample_config` | function | `conftest.py` | 3-project sample configuration |
| `valor_project` | function | `conftest.py` | Single project config |
| `cross_lane_repo` | function | `tests/unit/conftest.py` | Factory building the #2689 cross-lane reproducer repo (tracked anchor + another lane's untracked plan) shared by the hook-validator target-resolution tests |
| `mock_telegram_client` | function | `tests/e2e/conftest.py` | AsyncMock Telethon client |
| `make_telegram_event` | function | `tests/e2e/conftest.py` | Telegram event factory |
| `mock_agent_response` | function | `tests/e2e/conftest.py` | Canned agent response |
| `e2e_config` | function | `tests/e2e/conftest.py` | Config with test overrides |
| `perplexity_api_key` | function | `tools/conftest.py` | Perplexity API key (skip if missing) |
| `anthropic_api_key` | function | `tools/conftest.py` | Anthropic API key (skip if missing) |

## Adding Tests for New Features

1. **Pick the right level**: Unit for pure logic, integration for Redis/network, e2e for multi-component flows
2. **Name the file** with a keyword from `FEATURE_MAP` in `tests/conftest.py` so it auto-tags
3. **Or add a new entry** to `FEATURE_MAP` if creating a new feature area
4. **Add to this index** under the appropriate feature section

### Naming Convention

```
test_{feature_keyword}[_detail].py
```

The `{feature_keyword}` must match a key in `FEATURE_MAP` (in `tests/conftest.py`) for auto-tagging. Examples:
- `test_pipeline_new_stage.py` → auto-tagged `sdlc`
- `test_session_timeout.py` → auto-tagged `sessions` (matches "session_")
- `test_bridge_rate_limit.py` → auto-tagged `messaging` (matches "bridge")

### Splitting or Renaming a Test File

Markers are derived from the **basename only** — `pytest_collection_modifyitems` in
`tests/conftest.py` strips `test_` and `.py` from the nodeid's last path segment and
substring-matches the remainder against `FEATURE_MAP`, taking the first hit. The
directory a file sits in contributes nothing, so moving a file into a subpackage is
marker-neutral while renaming it is not.

That makes a split of a large module a silent marker hazard in both directions:

- **Losing a marker.** `test_stop_hook.py` (marker `sdlc`) split into
  `tests/unit/stop_hook/test_exit_codes.py` yields basename `exit_codes`, which matches
  nothing — those tests vanish from `pytest -m sdlc` while still running in a full sweep.
- **Gaining one.** A new basename can pick up an unrelated pattern by accident;
  `..._transport_aware_routing` matches `routing` and would be tagged `messaging`.

A `--collect-only` total-count check cannot catch either: the total is unchanged, only
the tagging moves. So when splitting a file:

1. Keep the original basename as a **prefix** of every new file
   (`test_output_handler.py` → `test_output_handler_drafter.py`), which preserves any
   matching substring by construction.
2. Check each new basename against `FEATURE_MAP` for an accidental new match.
3. Verify by comparing the per-marker collected counts before and after, not just the
   total — e.g. `pytest -m <marker> --collect-only -q | tail -1` for every marker the
   file touches.

**Step 1 is necessary but not sufficient, because "first hit" means insertion order
decides.** A correctly-prefixed name can still lose its marker when an *earlier* key in
`FEATURE_MAP` happens to appear in the suffix you chose. `worktree_manager` sits near the
end of the dict, well after `config` and `lifecycle`:

```
test_worktree_manager_config.py     -> "config" is found first    -> tagged `config`,   not `git`
test_worktree_manager_lifecycle.py  -> "lifecycle" is found first -> tagged `sessions`, not `git`
test_worktree_manager_cleanup.py    -> no earlier key matches     -> tagged `git`       (correct)
```

All three follow the prefix rule. Two of them are silently wrong. The same trap applies to
`test_sdlc_session_ensure_*`: a file named `..._bridge_short_circuit.py` matches `bridge` —
the very first key — and would be tagged `messaging` instead of `sdlc`.

So the check in step 2 must run a candidate basename through the **real first-hit
algorithm**, not just scan for an obviously-unrelated word. Iterate `FEATURE_MAP` in order
and take the first `pattern in basename` hit, exactly as `pytest_collection_modifyitems`
does. Do this *before* writing the files; renaming afterwards is cheap, but only if you
notice, and step 3's count check is what catches you if you didn't.

This procedure is currently manual. Automating it as a standing regression guard — so a
mistagged basename fails a test instead of relying on whoever does the split remembering
to check — is tracked in [#3010](https://github.com/tomcounsell/ai/issues/3010).

### Feature Marker Registration

New markers must be added in two places:
1. `pyproject.toml` → `[tool.pytest.ini_options]` markers list
2. `tests/conftest.py` → `FEATURE_MAP` dictionary

## Known Blind Spots

Source modules with no test coverage. Priority targets for new tests.

| Priority | Module | Lines | Risk |
|----------|--------|------:|------|
| Critical | `bridge/telegram_bridge.py` | 1,655 | Main entry point |
| Critical | `agent/hooks/` | ~150 | Dev session lifecycle hooks |
| Critical | `bridge/context.py` | 557 | Context building |
| Critical | `bridge/response.py` | 579 | Response formatting |
| Critical | `config/loader.py` | 432 | Config initialization |
| Critical | `scripts/update/` (6 files) | 2,140 | Deployment system |
| High | `monitoring/` (5 of 6 modules) | 1,200+ | Reliability |
| High | `agent/completion.py` | 316 | Auto-continue |
| High | `tools/job_scheduler.py` | 705 | Async work execution |
| Medium | Hook validators (29 files) | ~1,500 | Config enforcement |

**Partially covered** (operational layer added in #936):
- `bridge/email_bridge.py` — parsing, SMTP output, routing, and thread continuation have full coverage. Operational layer (`main()`, `_poll_imap()` batch cap, `_email_inbox_loop()` health timestamp) now covered via unit and integration tests.

## Subprocess Test-DB Inheritance (issue #2805)

The pytest process's environment is correct by construction. `tests/conftest.py::pytest_configure`
claims a private db from the pool `[1..15]` (`tests/db_claim.py`'s
`fcntl.flock`) and exports it as both `POPOTO_TEST_DB` and `REDIS_URL`
immediately after the claim. A plain `subprocess.run([...])` with **no
`env=`** inherits `os.environ` and therefore inherits the claimed
`REDIS_URL` — a child launched without any special handling lands on the
claimed test db, not production db0. This holds for every process in the
tree: a nested pytest child spawned without `env=` inherits the claimed
`REDIS_URL`, then overwrites it with its own claim via its own
`pytest_configure` (the #2628 invariant), and each xdist worker exports
its own claim independently since every worker runs `pytest_configure`
itself.

**`subprocess_env` survives as the `PYTHONPATH` pinner it also always
was**, not as an isolation gate. `subprocess_env(*, project_root=None,
**extra)` from `tests.db_claim` re-pins `REDIS_URL` to the same claimed
db (redundant with the process-wide export, but states the intent at the
call site) and, when `project_root=` is passed, prepends it to the
child's `PYTHONPATH` so the child resolves repo modules from this
checkout. Thread extra variables as keyword arguments
(`subprocess_env(AI_REPO_ROOT=...)`) rather than hand-building a dict. If
a site also needs keys removed, the accepted shape is `env =
subprocess_env(); env.pop("NAME", None)` — assign, then mutate with
`.pop(<literal>, None)` / `.update(...)`, never rebind.

`project_root=` is **opt-in, not a default**. Pass it when the child must
resolve repo modules from this checkout; omit it when the test asserts
something about import order or module resolution.
`tests/unit/test_sdlc_tool_wrapper.py::test_dispatch_from_foreign_cwd_with_own_tools_succeeds`
is the worked example of a deliberate omission — it pins the wrapper's own
module-resolution order against a decoy `tools/` package, so prepending
`REPO_ROOT` to `PYTHONPATH` would resolve the import for reasons other than
the wrapper's doing.

Never re-derive a db number by hand. Reading
`POPOTO_REDIS_DB.connection_pool.connection_kwargs` to rebuild a `REDIS_URL`
is the anti-pattern the #2628/#2763 line of fixes removed; `claim_test_db()`
(directly, or via `subprocess_env`/the process-wide export) is the only
source.

**A test that genuinely needs db0**, to prove a production guard fires,
states that intent explicitly at the call site:
`env={**subprocess_env(), "REDIS_URL": "redis://localhost:6379/0"}`.

**One documented coverage gap remains by design**: a child spawned with a
non-splatting `env=` (e.g. `env={"PATH": os.environ["PATH"]}`) drops
`REDIS_URL` entirely — the child never inherits `os.environ` at all, so
the process-wide export cannot rescue it. This shape is rarer than
omitting `env=` altogether, and the runtime backstops
(`tools/redis_flush_guard.py` on a db0 flush; the conftest claimed-db
flush guard) still fail closed underneath it.
`tests/unit/test_conftest_isolation_guards.py::TestPerProcessDbClaim::test_non_splatting_env_drops_redis_url`
documents this gap in code rather than only here.

The enforcement layer this replaced — a 688-line AST scanner over
`tests/**/*.py` with a `path:line`-keyed `ALLOWLIST` — was deleted in
full. A static scanner cannot see a child spawned any other way, cannot
see in-process code that builds its own client from `REDIS_URL`, and its
allowlist's line-number keys were unstable under any merge that shifted
a line. The permanent regression detector is now behavioral, split across
two classes in `tests/unit/test_conftest_isolation_guards.py` by the
property each checks: `TestPerProcessDbClaim` asserts that the live
process's `REDIS_URL` names its own claim under per-worker `--dist=each`,
that an unguarded child's resolved `REDIS_URL` is byte-identical to the
parent's, that a nested pytest child claims its own db rather than
leaking the parent's, and documents the non-splatting-`env=` coverage
gap; `TestExportedRedisUrlSurvivesSyntheticHookCalls`, placed at the END
of the file so it collects last, asserts nothing in the file's own
synthetic `pytest_configure()` calls polluted the live session's
`REDIS_URL` by the time collection finishes.
