---
status: Planning
type: feature
appetite: Large
owner: Valor Engels
created: 2026-07-21
tracking: https://github.com/tomcounsell/ai/issues/2192
last_comment_id:
---

# Nightly Regression Detector & Sentry Triage Reflection — Dedupe, Readable Alerts, Auto-Triage

## Problem

This repo runs two automated nightly-cadence alert pipelines, and both share the same three shortcomings:

1. **`scripts/nightly_regression_tests.py`** (launchd `com.valor.nightly-tests`, daily 03:00 local): runs `pytest tests/unit/ -n auto`, serially re-confirms failing node IDs with `-n0` (#2180), diffs the confirmed set against `data/nightly_tests_last_run.json`, and Telegram-alerts on newly-confirmed failures.
2. **`reflections/sentry_triage.py::run_sentry_triage`** (`sentry-issue-triage` in `config/reflections.yaml`, run by the reflection scheduler subprocess): classifies unresolved Sentry issues into tiers A–E, auto-actions A/B/E, files a GitHub issue per Class C when `SENTRY_TRIAGE_APPLY=1`, delta-notifies on new C/D short-ids tracked in `data/sentry_triage_seen.json`.

**Current behavior:**

- **Duplicate sends.** `logs/nightly_tests.log` shows the full nightly run logged twice back-to-back (identical start/end timestamps to the second, identical Telegram text) on 2026-07-17/18/20. There is a single launchd job (`launchctl print` confirms `state = not running` between fires; `CronList` shows no session-scheduled duplicate). The literal cause of the double *fire* is unpinned (candidate: a launchd race or a `/update` plist reinstall race in `scripts/update/service.py::install_nightly_tests` → `scripts/install_nightly_tests.sh`'s bootout+bootstrap), but the duplicate *send* must go away regardless of trigger.
- **Unreadable alerts.** The nightly Telegram message is a raw dump of dotted-path pytest node IDs truncated at 5 — it conveys no severity, blast radius, or cause. The Sentry summary is more structured (tier counts + top-3 Class C short-id + truncated title) but still drops the richer `reason` string that `_classify_issue` already computes.
- **No action on findings.** Both pipelines end at "send a Telegram message." Even when the cause is a two-line stale-test fix (see worked example), a human must notice, open a terminal, and fix by hand. There is no automated triage/investigation step.

**Desired outcome:** Both pipelines (a) never send a duplicate alert, (b) emit human-readable, actionable alert text with a safe fallback, and (c) dispatch exactly one Eng-role AgentSession to investigate a newly-confirmed finding (hotfix directly or file a `/do-issue`-quality issue), deduped so nightly re-runs never re-dispatch for the same unresolved finding.

## Recon Summary

**Confirmed (evidence-backed against current main):**
- `scripts/nightly_regression_tests.py` exists (15.9 KB); `main()` runs tests → `reconfirm_serial` → `compute_new_failures` → `send_telegram`, with best-effort `send_telegram()` and `run_ttft_gate()` already following the "never crash on non-critical step" pattern this issue wants to reuse.
- `reflections/sentry_triage.py::run_sentry_triage` exists (24.5 KB); computes `new_cd_ids` delta and persists `data/sentry_triage_seen.json` via `_save_seen_ids`; Class C path calls `_file_github_issue` (a mechanical Sentry-data dump); `reason` is computed by `_classify_issue` but only short-id + truncated title reach `tg_lines`.
- Non-harness LLM transport already exists: `agent/llm/wrapper.py::run_typed(prompt, output_type, *, model=MODEL_FAST, ...)` (PydanticAI, schema-validated, double-timeout, `LLMCallError`). This is the correct summarization transport per the two-transport convention — NOT `claude_code_sdk`.
- Eng-session dispatch entry point: `python -m tools.valor_session create --role eng --message "..."` (`tools/valor_session.py`); returns once enqueued.
- Reflection concurrency guard: `is_reflection_running(state)` = `state.last_status == "running"` (`agent/reflection_scheduler.py:412`); `run_reflection` calls `state.mark_started()` (sets `last_status="running"`) at line 479, and the scheduler also tracks in-process dispatch via `self._running_tasks[entry.name]` (line 721). Single launchd `com.valor.reflection-worker` (KeepAlive) = single process, single asyncio event loop.

**Reference case (worked example, may already be fixed — fix opportunistically):** commit `8e019ab7d` (#2147) added a required `channel` param to `_notify_healthcheck_watchdog` and made `_push_agent_session` derive the channel via `notify_channel_for(...)` (db-scoped). Stale test helpers in `tests/unit/test_agent_session_queue_async.py` (`:473`, `:113`) still use the old signature / assert the old unscoped literal. This is exactly the "small, hotfix-able, test-only drift from an intentional landed change" shape requirement 3 should catch.

## Freshness Check

**Baseline commit:** 3b7c526a2 (HEAD at plan time)
**Issue filed at:** 2026-07-21T07:15:41Z (same day)
**Disposition:** Minor drift

**File:line references re-verified:**
- `scripts/nightly_regression_tests.py` — exists, structure matches issue description — still holds.
- `reflections/sentry_triage.py` — exists, `_file_github_issue`, `_classify_issue`, `new_cd_ids`/`_save_seen_ids` all present — still holds.
- `agent/agent_session_queue.py:818` `notify_channel_for` — present, but the current signature is `notify_channel_for(client)` (derives db from the client's connection pool), NOT `notify_channel_for(POPOTO_REDIS_DB)` as the issue text loosely phrased it. The underlying claim (db-scoped channel derivation added by #2147) still holds. This is cosmetic to the plan since the reference case is a triage *example*, not a deliverable.
- `agent/reflection_scheduler.py:412` `is_reflection_running` / `:479` `mark_started` / `:721` `_running_tasks` — all present as described.
- `tests/unit/test_agent_session_queue_async.py:473` / `:113` — helper and test present as described.

**Cited sibling issues/PRs re-checked:**
- #2180 (serial re-confirmation gate) — shipped; the gate is live in `reconfirm_serial`. This plan must not regress it.
- #2147 (`8e019ab7d`) — merged (via #2163); the reference-case drift source.
- #1227 (TTFT gate) — shipped; `run_ttft_gate` is live and must be unaffected.

**Commits on main since issue was filed (touching referenced files):** none (`git log --since` on the three target files returned empty).

**Active plans in `docs/plans/` overlapping this area:** `nightly-serial-reconfirm.md` — this is the already-shipped #2180 plan (the *detector's* pass/fail logic). No overlap: this plan touches the *alerting/triage layer* around the detector, explicitly out of #2180's scope. Not a blocker.

## Prior Art

- **#2180 (`nightly-serial-reconfirm.md`)**: Hardened the detector's confirmed-vs-artifact logic (serial `-n0` re-confirmation). Succeeded and shipped. This plan builds strictly on top of it (the alerting layer) and must leave `reconfirm_serial` untouched.
- **#1227 (TTFT gate)**: Added a best-effort post-run gate that surfaces cold-start regressions as Telegram alerts without changing exit code. Its swallow-all-exceptions pattern (`run_ttft_gate`) is the template for requirement 2's best-effort summarization and requirement 3's dispatch.
- **#1817 (resilience: double-exec, silent drops)**: Related in spirit (delivery-integrity hazards) but a different subsystem; no direct code reuse.
- No prior attempt at nightly-alert dedup or auto-triage dispatch found in closed issues / merged PRs. This is greenfield for the alerting/triage layer.

## Research

No external research required — this is purely internal work. Both required patterns already exist in-repo:
- **File lock:** `fcntl.flock(fd, LOCK_EX | LOCK_NB)` is used in `scripts/pr_shape_cache.py` (`_acquire_lock`, `DEFAULT_LOCK_PATH = data/*.lock`, `LOCK_UN` on release), `scripts/update/run.py`, and `utils/json_cache.py`. Reuse that idiom.
- **Non-harness LLM:** `agent/llm/wrapper.py::run_typed` (PydanticAI). No new dependency.

## Data Flow

**Scope 1 — nightly detector (`main()`):**
1. **Entry point**: launchd fires `python scripts/nightly_regression_tests.py`.
2. **[NEW] Lock acquire**: at the very top of `main()`, acquire `fcntl.flock(LOCK_EX|LOCK_NB)` on `data/nightly_tests.lock`. If it fails, log the collision and `return 0` (no tests, no send).
3. **Test run**: `run_tests()` (parallel) → `reconfirm_serial()` (serial) → `confirmed_failing`.
4. **Delta**: `compute_new_failures(prev, confirmed_failing)` → `new_failures`.
5. **[NEW] Dispatch**: `maybe_dispatch_triage_session(new_failures, prev)` — dedup against persisted dispatch state in `data/nightly_tests_last_run.json`; fire-and-forget `valor-session create --role eng`; returns session ID or None.
6. **[NEW] Summarize**: `summarize_failures(confirmed_failing, report)` — best-effort `run_typed` call → 2–4 actionable sentences; on any exception fall back to today's node-ID preview.
7. **Alert**: `send_telegram(msg)` with the summarized text + dispatched session ID reference.
8. **State**: `save_last_run(current)` including the new dispatch-tracking field.

**Scope 2 — Sentry triage (`run_sentry_triage()`):**
1. **Entry point**: reflection scheduler tick (guarded by `is_reflection_running` + `_running_tasks`) calls the function.
2. **Fetch + classify**: `_fetch_unresolved_issues` → `_classify_issue` → tiers A–E; `new_cd_ids = current_cd_ids - prev_seen`.
3. **[NEW] Dispatch**: for each newly-surfaced Class C short-id in `new_cd_ids`, dedup against `data/sentry_triage_seen.json` (already tracks surfaced ids) and fire-and-forget one `valor-session create --role eng` to investigate → hotfix or `/do-issue`-quality issue (replaces the mechanical `_file_github_issue` dump path for *new* C issues).
4. **[NEW] Summarize / thread reason**: thread `reason` into `tg_lines` for top Class C items; optionally a best-effort `run_typed` summary of the Class C pile with the same fallback discipline.
5. **Notify**: `_send_telegram_notification` (delta-gated as today), now carrying reason/summary + dispatched session IDs.

## Architectural Impact

- **New dependencies**: none new. Reuses `fcntl` (stdlib), `agent/llm/wrapper.py` (existing PydanticAI wrapper), and `tools.valor_session` (existing CLI).
- **Interface changes**: three new module-private helpers in `nightly_regression_tests.py` (`_acquire_run_lock`, `summarize_failures`, `maybe_dispatch_triage_session`); analogous additions in `sentry_triage.py`. No public signature changes to existing functions — additions wrap the existing `main()` body.
- **Coupling**: adds a coupling from both nightly pipelines to the Eng-session queue (via the `valor-session` CLI subprocess, not a direct import) — deliberately loose, fire-and-forget.
- **Data ownership**: `data/nightly_tests_last_run.json` gains a dispatch-tracking field; `data/nightly_tests.lock` is a new lockfile; `data/sentry_triage_seen.json` semantics unchanged (reused for dispatch dedup).
- **Reversibility**: high. Each requirement is an independent, additive wrapper; reverting any one restores prior behavior.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (Scope 1 vs Scope 2 split; Class D dispatch decision)
- Review rounds: 2+ (two call sites; LLM best-effort fallback correctness; dispatch-dedup correctness)

Large because there are six distinct deliverables (three requirements × two call sites), each with its own failure-path discipline and unit tests, plus an AgentSession dispatch integration that must be provably fire-and-forget and provably deduped.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` (for PydanticAI summarization) | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Non-harness LLM summarization call; feature degrades gracefully to raw format if absent |
| `valor-session` CLI available | `python -c "import tools.valor_session"` | Eng-session dispatch entry point |
| `valor-telegram` CLI available | `test -x .venv/bin/valor-telegram` | Existing alert delivery (unchanged) |

Run via `python scripts/check_prerequisites.py docs/plans/nightly-regression-triage.md`.

## Solution

### Key Elements

- **Run lock (Scope 1)**: `fcntl.flock(LOCK_EX|LOCK_NB)` on `data/nightly_tests.lock` acquired at the top of `main()`; second invocation logs the collision and exits 0 without running tests or sending. Process A's send path is never gated on B.
- **Concurrency-guard audit (Scope 2)**: verify (don't blindly add a lock) that `is_reflection_running` + in-process `_running_tasks` + single-process launchd deployment make `sentry-issue-triage` double-run-proof; document the finding; harden only if a real gap is found.
- **Best-effort summarizer (both)**: `summarize_failures(...)` / Class-C summarizer built on `agent/llm/wrapper.py::run_typed` with a Pydantic output schema; any exception → fall back to the current raw format. Never blocks or crashes the run.
- **Fire-and-forget triage dispatch (both)**: `maybe_dispatch_triage_session(...)` runs `valor-session create --role eng` once per new finding-set, deduped against persisted state; folds the returned session ID into the alert text.

### Flow

**Nightly:** launchd fires → acquire lock (or exit 0 on collision) → run + reconfirm tests → compute new failures → dispatch triage session (deduped) → summarize failures (fallback-safe) → Telegram alert w/ session ID → save state (incl. dispatch hash).

**Sentry:** scheduler tick (guarded) → fetch + classify → compute new C/D delta → dispatch triage session per new Class C (deduped via seen.json) → thread reason / summarize → delta-gated Telegram notify w/ session IDs → persist seen ids.

### Technical Approach

- **Lockfile**: mirror `scripts/pr_shape_cache.py::_acquire_lock` — open the lockfile fd, `flock(LOCK_EX|LOCK_NB)`, hold the fd for process lifetime (released on exit). On `BlockingIOError`, `log("collision — another run holds the lock; exiting")` and `return 0`.
- **Summarization**: define a small `BaseModel` (e.g. `FailureSummary(summary: str)`); build the prompt from confirmed node IDs grouped by file + their short tracebacks from the `--json-report` payload (`report["tests"][].call.longrepr` / `crash`). Call `run_typed(prompt, FailureSummary, model=MODEL_FAST)`. Wrap in try/except → fallback string. Keep the LLM call off the critical timeout budget.
- **Dispatch dedup (Scope 1)**: persist a hash of the sorted confirmed-failing node-ID set (plus dispatched session ID) in `data/nightly_tests_last_run.json`. Skip dispatch if the current confirmed set's hash equals the last-dispatched hash and that session hasn't concluded / the set hasn't changed.
- **Dispatch dedup (Scope 2)**: reuse `new_cd_ids` (already the notification-gating delta) so only genuinely-new Class C short-ids dispatch; `data/sentry_triage_seen.json` already suppresses the standing backlog.
- **Dispatch call**: `subprocess.run(["python","-m","tools.valor_session","create","--role","eng","--message", <triage prompt>], ...)` best-effort, capture the emitted session ID; fire-and-forget (returns on enqueue, not completion) so the nightly runtime budget is unaffected.
- **Class D**: not dispatched in this cut (see Open Questions / issue recommendation).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new `summarize_failures` / Class-C summarizer must catch all exceptions from `run_typed` and fall back to the raw format — test asserts the fallback string is used and a `log(...)`/`logger.warning(...)` is emitted (not silent).
- [ ] `maybe_dispatch_triage_session` must catch all subprocess exceptions (missing CLI, non-zero exit, timeout) and return `None` without crashing `main()` — test asserts the alert still sends with no session ID.
- [ ] Lock-acquire failure path (`BlockingIOError`) must log and `return 0` — asserted (see Test Impact).
- [ ] Existing `except Exception: pass` in `log()` and swallow-all in `send_telegram` / `_send_telegram_notification` remain; no new bare `except: pass` introduced.

### Empty/Invalid Input Handling
- [ ] `summarize_failures([], report)` and empty/malformed `report` → returns the raw fallback, no LLM call on empty input (`run_typed` also rejects empty prompt with `ValueError` — must be caught).
- [ ] `maybe_dispatch_triage_session([], prev)` → no dispatch (nothing new).
- [ ] Malformed/absent `data/nightly_tests.lock` parent dir → `DATA_DIR.mkdir(parents=True, exist_ok=True)` before opening (matches `save_last_run`).

### Error State Rendering
- [ ] When summarization fails, the Telegram alert still renders the raw node-ID list (the reader is never left with an empty/blank alert).
- [ ] When dispatch fails, the alert renders without a session-ID reference rather than omitting the alert.

## Test Impact

- [ ] `tests/unit/test_nightly_regression_tests.py` — UPDATE: `TestDeltaLogic` / `TestSendTelegram` may need small adjustments if the alert-text construction is refactored to call `summarize_failures`. Keep asserting the raw fallback text remains reachable.
- [ ] `tests/unit/test_nightly_regression_tests.py` — ADD: `TestRunLock` (contention no-op), `TestSummarizeFailures` (fallback-on-exception, empty-input), `TestMaybeDispatchTriage` (dispatch-once, dedup-across-two-runs, subprocess-failure-safe).
- [ ] `tests/unit/test_sentry_triage_apply.py` — UPDATE: `test_actionable_issue_triggers_notification` / digest tests to assert `reason` now appears in `tg_lines` for top Class C items.
- [ ] `tests/unit/test_sentry_triage_apply.py` — ADD: Class C dispatch-once + dedup-against-seen.json; concurrency-guard audit assertion (or a documented reasoning test if no code change).
- [ ] No existing test asserts the *current* raw node-ID dump as a hard contract that this change would break silently — the summarizer is additive with a fallback, so existing delta-logic tests should keep passing unchanged.

## Rabbit Holes

- **Root-causing the launchd double-*fire*.** Requires `sudo log show` (unavailable). The lockfile makes the duplicate *send* impossible regardless of trigger; do NOT spend the appetite chasing the launchd/plist-reinstall race. Defense-in-depth is the mandate.
- **Adding a redundant lock to `sentry-issue-triage`.** The issue explicitly asks to *verify* the existing guard is airtight, not to layer a second lock "for its own sake." Audit first; only harden a proven gap.
- **Over-engineering the LLM summary.** 2–4 sentences, one `run_typed` call, hard fallback. No multi-step chains, no tool use, no retries beyond the wrapper's built-in one.
- **Class D auto-dispatch.** Ambiguous-by-design tier; auto-triaging it risks noisy/wrong hotfixes. Out of scope for this cut.
- **Making dispatch synchronous / waiting on session completion.** Would put the nightly runtime budget at the mercy of triage-session duration. Fire-and-forget only.

## Risks

### Risk 1: Triage dispatch runaway (a fresh Eng session every night for the same unresolved failure)
**Impact:** Session-queue spam, wasted compute, noise.
**Mitigation:** Persist a dispatch hash (Scope 1) / reuse `new_cd_ids` + `seen.json` (Scope 2). Unit tests assert exactly-one dispatch across two consecutive runs with the same failure set.

### Risk 2: LLM summarization blocks or crashes the run
**Impact:** A genuine regression alert is delayed or lost.
**Mitigation:** Best-effort with hard try/except → raw fallback; keep the call off the pytest timeout budget; wrapper already enforces `sdk_timeout` + `hard_timeout`. Tests assert fallback on exception and on empty input.

### Risk 3: Lock never released / stale lock blocks all future runs
**Impact:** Nightly detector silently stops running.
**Mitigation:** `flock` (advisory, per-fd) is auto-released on process exit — a crashed process releases the lock at OS level; no stale-lock file cleanup needed. Test simulates a held lock in-process and asserts a clean exit-0 no-op.

### Risk 4: A real regression goes unreported because process B backed off
**Impact:** Missed alert — the worst outcome.
**Mitigation:** Only the lock *holder* (process A) runs and sends; B's early exit never carries send responsibility. A's send path is entirely independent of B. Explicitly asserted in the acceptance criteria.

## Race Conditions

### Race 1: nightly double-fire (two overlapping launchd invocations)
**Location:** `scripts/nightly_regression_tests.py::main()` (top).
**Trigger:** Two `com.valor.nightly-tests` invocations fire near-simultaneously (observed 2026-07-17/18/20).
**Data prerequisite:** `data/nightly_tests.lock` must exist/be creatable before either process runs tests.
**State prerequisite:** Exactly one process may hold `LOCK_EX` at a time.
**Mitigation:** `fcntl.flock(LOCK_EX|LOCK_NB)` at the very top of `main()`; loser logs + exits 0. OS releases the lock on holder exit.

### Race 2: sentry-triage check-then-act between `is_reflection_running` and `mark_started`
**Location:** `agent/reflection_scheduler.py` — guard read at `:682`, `state.mark_started()` (persisted "running") inside `run_reflection` at `:479`.
**Trigger:** Two scheduler ticks dispatch the same reflection before the first marks it running.
**Data prerequisite:** The `Reflection` state's `last_status` must read "running" before a second tick evaluates the guard.
**State prerequisite:** Single reflection-worker process, single asyncio event loop.
**Mitigation (audit conclusion to verify in build):** The scheduler is a *single* launchd process (`com.valor.reflection-worker`, KeepAlive) running a single asyncio event loop, so two ticks cannot execute concurrently; additionally `self._running_tasks[entry.name]` is an in-process guard set synchronously at dispatch (`:721`), closing the window between the persisted-state read and `mark_started`. Cross-process double-run would require two worker processes, which the single KeepAlive job precludes. Conclusion: the guard is effectively airtight for the deployed topology — document this in the plan/PR and add a reasoning/assertion test rather than a redundant lock. Harden only if the build surfaces a concrete gap (e.g. `_running_tasks` not consulted on the due path — confirm it is).

## No-Gos (Out of Scope)

- [EXTERNAL] Root-causing the launchd double-*fire* trigger via `sudo log show` — requires elevated log access unavailable to the agent; the lockfile neutralizes the symptom regardless.
- [SEPARATE-SLUG] Class D (investigate) auto-dispatch — deferred to a follow-up once Class C dispatch is proven out; recommend filing only if C-tier dispatch proves valuable. (No issue filed yet — this is a genuine scope boundary, not a tracking promise; if the reviewer wants it tracked, file before merge.)
- [DESTRUCTIVE] Auto-*merging* any hotfix a dispatched triage session produces — the dispatched Eng session follows the existing hotfix-vs-SDLC threshold and PM sign-off; the nightly/reflection scripts only *dispatch*, never merge.

## Update System

- `scripts/install_nightly_tests.sh` and `scripts/update/service.py::install_nightly_tests` are unchanged in wiring — the lockfile is created at runtime under `data/`, no plist change needed. (If the double-fire is later traced to the unconditional bootout+bootstrap on every `/update`, that is a separate `[EXTERNAL]`-adjacent fix; not required here since the lock handles the symptom.)
- No new config files or dependencies to propagate — `fcntl` is stdlib; `agent/llm/wrapper.py`, `tools.valor_session`, `valor-telegram` are already installed on every machine.
- `data/nightly_tests.lock` is created on first run; no migration needed. `data/nightly_tests_last_run.json` gains an additive field (missing-key-safe reads, matching `compute_new_failures`'s `prev.get(...)` pattern).

## Agent Integration

- No new MCP tool or bridge import is required. Both pipelines are already agent-reachable: the nightly script runs under launchd, the Sentry triage runs under the reflection scheduler. The *new* integration is outbound — both dispatch an Eng-role AgentSession via the existing `python -m tools.valor_session create --role eng` CLI (subprocess), which enqueues onto the worker's session queue.
- Integration coverage: a test asserting `maybe_dispatch_triage_session` invokes `valor-session create --role eng` with the expected message (mock the subprocess) and returns the parsed session ID; the reverse grep check (`grep` confirms the scripts reference `tools.valor_session` / `valor-session create`).
- The dispatched session ID is threaded into the Telegram alert so a human can `valor-session status --id <ID>`.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/nightly-alert-triage.md` documenting the run lock, best-effort summarization, and auto-triage dispatch for both pipelines (dedup semantics, fallback discipline, fire-and-forget contract).
- [ ] Add entry to `docs/features/README.md` index table.

### Inline Documentation
- [ ] Docstrings on `_acquire_run_lock`, `summarize_failures`, `maybe_dispatch_triage_session` and the Scope 2 analogues, each stating the best-effort/never-crash contract.
- [ ] A comment in `sentry_triage.py` recording the concurrency-guard audit conclusion (why no second lock).

## Success Criteria

- [ ] A second concurrent invocation of `nightly_regression_tests.py` exits 0 without re-running tests or re-sending an alert (unit test holds the lock, invokes `main()`, asserts no pytest subprocess and no telegram send).
- [ ] The nightly Telegram alert reads as prose a non-engineer can get the gist of, with a safe fallback to the current node-ID format when summarization fails.
- [ ] A newly-confirmed nightly regression triggers exactly one Eng-role AgentSession dispatch, not repeated on the next run for the same unresolved failure set.
- [ ] `sentry-issue-triage`'s `is_reflection_running` guard is confirmed race-free (or hardened) and the conclusion is documented in the plan/PR.
- [ ] Class C Sentry issues surface with their classification `reason` (or an LLM-summarized equivalent) in the Telegram alert.
- [ ] A newly-surfaced Class C Sentry issue triggers exactly one Eng-role AgentSession dispatch, deduped against `data/sentry_triage_seen.json`.
- [ ] The #2180 serial re-confirmation gate and #1227 TTFT gate behavior are unchanged (existing tests for both still pass).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms both scripts reference `tools.valor_session` / `valor-session create` (Agent Integration wiring present)

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (nightly)**
  - Name: `nightly-builder`
  - Role: Implement the run lock, `summarize_failures`, and `maybe_dispatch_triage_session` in `scripts/nightly_regression_tests.py` + unit tests.
  - Agent Type: builder
  - Domain: async/subprocess, best-effort-failure discipline
  - Resume: true

- **Builder (sentry)**
  - Name: `sentry-builder`
  - Role: Thread `reason`/summary into Telegram lines, add Class C dispatch + dedup, audit + document the concurrency guard in `reflections/sentry_triage.py` + unit tests.
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: `triage-validator`
  - Role: Verify dispatch-once/dedup, lock contention no-op, summarization fallback, and that #2180/#1227 behavior is untouched.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `triage-docs`
  - Role: Create `docs/features/nightly-alert-triage.md` + index entry.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Using `builder`, `validator`, `documentarian` (Tier 1). Concurrency/async framing pasted from `DOMAIN_FRAMING.md` into the builder tasks.

## Step by Step Tasks

### 1. Nightly run lock + tests
- **Task ID**: build-nightly-lock
- **Depends On**: none
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestRunLock`
- **Assigned To**: nightly-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_acquire_run_lock()` mirroring `scripts/pr_shape_cache.py::_acquire_lock`; acquire at top of `main()`, exit 0 with a log on `BlockingIOError`.
- Add unit tests for contention no-op and clean acquire/release.

### 2. Nightly summarizer + dispatch + tests
- **Task ID**: build-nightly-triage
- **Depends On**: build-nightly-lock
- **Validates**: `tests/unit/test_nightly_regression_tests.py::TestSummarizeFailures`, `::TestMaybeDispatchTriage`
- **Assigned To**: nightly-builder
- **Agent Type**: builder
- **Parallel**: false
- Add best-effort `summarize_failures(confirmed_failing, report)` via `agent/llm/wrapper.py::run_typed` with raw fallback; wire into the alert construction.
- Add `maybe_dispatch_triage_session(new_failures, prev)` with dispatch-hash dedup in `data/nightly_tests_last_run.json`; fire-and-forget `valor-session create --role eng`; thread session ID into alert.
- Tests: fallback-on-exception, empty-input, dispatch-once, dedup-across-two-runs, subprocess-failure-safe.

### 3. Sentry reason-threading + dispatch + guard audit + tests
- **Task ID**: build-sentry-triage
- **Depends On**: none
- **Validates**: `tests/unit/test_sentry_triage_apply.py` (updated + new Class C dispatch/dedup cases)
- **Assigned To**: sentry-builder
- **Agent Type**: builder
- **Parallel**: true
- Thread `reason` into `tg_lines` for top Class C items; optional best-effort Class-C summary via `run_typed` with fallback.
- Add Class C dispatch for `new_cd_ids` (fire-and-forget `valor-session create --role eng`), deduped against `data/sentry_triage_seen.json`.
- Audit `is_reflection_running` + `_running_tasks` + single-process topology; document the airtight conclusion in code + plan; harden only if a concrete gap is found.
- Tests: reason-in-digest, dispatch-once, dedup-against-seen, guard reasoning/assertion.

### 4. Validation
- **Task ID**: validate-all
- **Depends On**: build-nightly-triage, build-sentry-triage, document-feature
- **Assigned To**: triage-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria; confirm #2180/#1227 tests still pass; confirm exactly-one dispatch semantics and fallback discipline.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-nightly-triage, build-sentry-triage
- **Assigned To**: triage-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/nightly-alert-triage.md`; add index entry.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Nightly tests pass | `pytest tests/unit/test_nightly_regression_tests.py -q` | exit code 0 |
| Sentry tests pass | `pytest tests/unit/test_sentry_triage_apply.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lock wired | `grep -n "flock" scripts/nightly_regression_tests.py` | output contains flock |
| Nightly dispatch wired | `grep -n "valor.session\|valor_session" scripts/nightly_regression_tests.py` | output contains valor |
| Sentry dispatch wired | `grep -n "valor.session\|valor_session" reflections/sentry_triage.py` | output contains valor |
| Summarizer uses non-harness wrapper | `grep -n "run_typed\|agent.llm.wrapper" scripts/nightly_regression_tests.py` | output contains run_typed |
| No claude_code_sdk added | `grep -rn "claude_code_sdk" scripts/nightly_regression_tests.py reflections/sentry_triage.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Class D dispatch (Scope 2):** The issue recommends Class C only for the first cut (D is "ambiguous, needs human review" by design; auto-dispatching risks noisy hotfixes). This plan adopts that recommendation. Confirm — or should Class D also dispatch triage sessions now?
2. **Dispatch synchronicity:** The issue recommends fully fire-and-forget (`valor-session create` returns on enqueue). This plan adopts that. Confirm the nightly runtime budget should never wait on session creation completion.
3. **Scope split:** Land Scope 1 (nightly) and Scope 2 (Sentry) as one PR or two sequential PRs under this one plan/issue? Recommend two sequential PRs to reduce review risk (the issue explicitly permits this).
