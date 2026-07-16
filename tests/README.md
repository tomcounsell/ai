# Test Suite

Organized by test level and tagged by feature. Run `pytest --collect-only -q` for current counts.

## Running Tests

```bash
# By level (parallel by default — `-n auto --dist=loadfile` from pyproject.toml)
pytest tests/unit/               # Unit tests (~40s parallel, ~250s serial)
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

1. **Per-process Redis db (claimed).** The autouse `redis_test_db` fixture (`tests/conftest.py`) gives each pytest **process** a *unique* test db, claimed atomically from the pool `[1..15]` via a held `fcntl.flock` (issue #2060). This is stronger than the old per-*worker* `gw{N}→db{N+1}` mapping: it prevents two concurrent pytest **processes** (a single-test run + a background full-suite run) from both landing on db1 and `flushdb()`-ing each other's data mid-test. Tests that build a raw `redis.Redis(...)` client or set `REDIS_URL` for a subprocess must use the claimed db, not a hardcoded `db=1` — use the `redis_test_url` fixture (which reads the same claim). A subprocess that inherits `POPOTO_REDIS_DB` (e.g. deriving from `connection_pool.connection_kwargs['db']`) picks it up automatically. See [`docs/features/test-isolation-hardening.md`](../docs/features/test-isolation-hardening.md) (root cause 3).
2. **File-level grouping (`--dist=loadfile`).** All tests in one file land on the same worker. Files whose tests share global resources (npm/npx caches, host-level lockfiles, a single GitHub issue, an in-process module variable) rely on this — they otherwise collide under inter-test parallelism.
3. **Host-coupled liveness checks must mock their probe.** Tests that assert process-liveness behaviour (e.g. `test_watchdog_recovery.py::TestWatchdogDetectsUnexpectedExit`) must not rely on a global `pgrep`/process scan, because a real `python -m worker` running on the dev box masks the test's fabricated process. Mock the probe (`monitoring.worker_watchdog._get_worker_pid`) to the test's own spawned PID so the assertion is deterministic with or without a coexisting real worker (issue #1578, Category E).

### Test isolation under xdist

Two cross-file phantom-failure mechanisms were root-caused and fixed in `tests/conftest.py` (umbrella issue #1897). Both are single-run, single-worker-sequence bugs — a test passes in isolation but fails only under a specific xdist worker composition, then passes again on re-run. If you hit a new instance of this class, read the fixture docstrings in `tests/conftest.py` first; they are the source of truth for the exact mechanism.

1. **Popoto db-cache split-brain.** `_popoto_modules_with_redis_db()` (consumed by the autouse `redis_test_db` fixture) memoizes which `popoto.*` submodules hold a `POPOTO_REDIS_DB` symbol so it doesn't walk all of `sys.modules` every test. The cache invalidates on a **compound trigger**: `len(sys.modules)` change (catches a brand-new, never-cached db-holder) **OR** per-entry object-identity divergence (catches an equal-count eviction-then-reimport, where a module is replaced under the same dotted name — e.g. by `mock_claude_sdk_cleanup` evicting `agent.*`). Count/len may gate additions but must **never** be the sole invalidation key — a sole count/name-set signal false-greens an equal-count module replacement (the stale object keeps its pre-swap `POPOTO_REDIS_DB` binding), and identity alone misses never-cached new holders (`any()` over an empty or partial cache is vacuously false). A stale cache leaves some popoto submodule's `POPOTO_REDIS_DB` pointed at the wrong test db, so an in-process write and a subprocess (or `Model.query.filter`) read can silently land on different Redis databases. This fix also subsumes issue #2037 (create-then-`filter` split-brain — same stale-cache mechanism, read path instead of write path).
2. **agent-hooks hooks-less-parent corruption.** The autouse `agent_hooks_consistency_guard` fixture detects and repairs a state where `sys.modules["agent"]` is cached but lacks a `hooks` attribute even though `sys.modules["agent.hooks"]` is still cached — CPython only rebinds a submodule onto its parent package at fresh-import time, so a partial `sys.modules` mutation (SDK swap, `importlib.reload`, `patch.dict`) can leave the parent "hooks-less" while the submodule cache survives. Any dotted-string `monkeypatch.setattr("agent.hooks...", ...)` then raises `AttributeError` during test setup, before the test body runs. The guard repairs by evicting **every** `agent.*` key from `sys.modules`, not just the two implicated ones — a full eviction is required because the next import must rebuild the whole parent→child attribute chain together; a partial eviction just reproduces the same corruption on the next import.

A third, **cross-process** instance (#2060) is not xdist-ordering at all: two separate pytest processes sharing a test db and `flushdb()`-ing each other — fixed by the per-process db claim described in pattern 1 above.

New instances of this class get filed under the umbrella issue [#1897](https://github.com/tomcounsell/ai/issues/1897) as they're observed and root-caused. `tests/unit/test_conftest_isolation_guards.py` is the deterministic regression suite locking in the fixes (Test A: agent-hooks guard repair; Test B: falsifiable len-vs-identity binding gate for the popoto cache; Test C: #2037 create-then-`filter` round trip; `TestPerProcessDbClaim`: #2060 per-process db claim) — start there when investigating a new phantom failure. See [`docs/features/test-isolation-hardening.md`](../docs/features/test-isolation-hardening.md) for a write-up distinguishing this single-run isolation work from the separate cross-run concurrency coordination in [`docs/features/full-suite-pytest-lock.md`](../docs/features/full-suite-pytest-lock.md).

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

## Merge-Gate Baseline vs. PR-Branch Flaky Filter

Two independent test-reliability layers exist in this repo and are easy to confuse:

- **PR-branch flaky filter** (`/do-test`, PR #484, issue #476) — when a test fails on the PR branch, pytest retries the failure once; tests that pass on retry are dropped from the failure report before `/do-merge` sees them. This layer addresses flakiness *on the PR branch*.
- **Merge-gate baseline** (`/do-merge`, PR #484 of #1084) — a categorised per-test baseline (`data/main_test_baseline.json`, schema v2) records which tests are pre-existing failures on `main`. The Full Suite Gate compares PR failures against the baseline per category (`real`, `flaky`, `hung`, `import_error`). New regressions in the blocking categories fail the gate; pre-existing failures — including baseline-`flaky` re-occurrences — do not. This layer addresses staleness *on main*.

Regenerate the merge-gate baseline with `python scripts/refresh_test_baseline.py` on a clean `main` checkout. The tool uses `pytest-timeout` (added as a dev dep) for per-test `hung` classification, but the plugin is NOT registered in pytest's default addopts — it only activates when the refresh tool invokes pytest with `-p pytest_timeout --timeout=N`, so regular `pytest tests/unit/` runs and `/do-test` invocations are unaffected. See `docs/features/merge-gate-baseline.md` for the full contract.

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
| unit | `test_valor_telegram.py` | 17 | Telegram command handling |
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
| unit | `test_sdlc_stage_query.py` | 17 | Stage query CLI (session-id and issue-number resolution) |
| unit | `test_sdlc_session_ensure.py` | 8 | Local session creation/reuse for SDLC pipeline state |
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
| unit | `test_docs_auditor_substrate.py` | 62 | Documentation reference validation |
| unit | `test_features_readme_sort.py` | 27 | README table sorting |
| unit | `test_verification_parser.py` | 20 | Verification section parsing |
| unit | `test_validate_test_impact.py` | 20 | Test impact section validation |
| unit | `test_validate_commit_message.py` | 16 | Commit message format |
| unit | `test_validate_sdlc_on_stop.py` | 12 | SDLC stop validation |
| unit | `test_validate_verification_section.py` | 9 | Verification validation |
| unit | `test_build_validation.py` | 6 | Build process validation |

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
| unit | `test_worktree_manager.py` | 28 | Worktree management |
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
| unit | `test_memory_extraction.py` | 107 | Post-session Haiku extraction, outcome detection |
| unit | `test_memory_ingestion.py` | 89 | Telegram message memory ingestion |
| integration | `test_redis_models.py` | 30 | Popoto model CRUD |

### `monitoring` — Observability

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_telemetry.py` | 27 | Telemetry data collection |
| unit | `test_health_check.py` | 12 | Health monitoring |
| unit | `test_bridge_watchdog.py` | — | Bridge watchdog |
| integration | `test_connectivity_gaps.py` | 12 | Connectivity failure handling |
| integration | `test_silent_failures.py` | 7 | Silent failure detection |
| performance | `test_benchmarks.py` | 16 | Latency, throughput, memory |

### `impact` — Code/doc change analysis

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_doc_impact_finder.py` | 21 | Documentation impact analysis |
| unit | `test_code_impact_finder.py` | 19 | Code file impact analysis |
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
| unit | `session_runner/test_runner_preempt.py` | 8 | Steer-preempt (D4): generation-token guard, kill-at-boundary race (pending steers re-pushed on loop exit), SIGTERM→SIGKILL escalation, timeout-as-preempt |
| unit | `session_runner/test_runner_resume.py` | 19 | Four-scalar resume consumption, cwd-scoped resume (Race 3), stale-UUID fallback, skip-prime, capture-at-init (Race 5) + off-loop version probe, `dev_agent_id` sidechain capture, turn-history mirror (bounded, never read on resume) |
| unit | `session_runner/test_runner_liveness.py` | 14 | Role-aware turn timeout table, subprocess-death/hang/missing-binary classification (wedge-coverage replacement) |
| unit | `session_runner/test_headless_role_driver.py` | 18 | `HeadlessRoleDriver` turn dispatch, prime injection, hook-edge turn-end reconciliation, nonzero-exit-no-result classification |
| unit | `session_runner/test_router_classification.py` | 6 | PM-prefix classifier: strict-token payloads, fallback token stripping (no raw `[/user]` ever delivered) |
| unit | `session_runner/test_hook_edge_notifications.py` | 21 | Hook settings generation, NDJSON edge consumer, Notification envelopes |
| unit | `session_runner/headless_hook_probe.py` | — | Support module (no tests): real-CLI turn-end + prime-resolution probe harness, salvaged from the deleted granite-faults tree |
| integration | `test_runner_dispatch_e2e.py` | 2 | Executor → `SessionRunner` → `HeadlessRoleDriver` → fake harness → delivery callback; the anti-"built-but-never-wired" gate |
| integration | `test_headless_probe_e2e.py` | 4 | Subscription-auth env contract (always-on) + real `claude -p` turn-end/prime-resolution probes, gated on `HEADLESS_PROBE_SMOKE=1`; the canary for new `claude` releases |

### Other

| Level | File | Tests | Description |
|-------|------|------:|-------------|
| unit | `test_nightly_regression_tests.py` | 28 | Nightly regression runner: suite invocation, JSON report parsing, Telegram alerting, version-pinned `claude` canary |
| e2e | `test_telegram_flow.py` | — | Live Telegram flow stubs |

## Fixtures

| Fixture | Scope | Source | Purpose |
|---------|-------|--------|---------|
| `mock_claude_sdk_cleanup` | autouse | `conftest.py` | SDK mock cleanup between tests |
| `redis_test_db` | autouse | `conftest.py` | Per-worker Redis db isolation |
| `sample_config` | function | `conftest.py` | 3-project sample configuration |
| `valor_project` | function | `conftest.py` | Single project config |
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
