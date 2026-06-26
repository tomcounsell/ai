---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-26
tracking: https://github.com/tomcounsell/ai/issues/1804
last_comment_id: none
revision_applied: true
---

# Standalone worker mode hardening + notify-listener self-healing

## Problem

The standalone worker (`python -m worker`) is the sole session-execution engine.
Cross-process wakeups for newly-enqueued sessions arrive over a Redis pub/sub
channel (`valor:sessions:new`) consumed by `_session_notify_listener`. The issue
reports two concerns: (1) the worker plist never sets `VALOR_WORKER_MODE`, and
(2) on at least one boot `PUBSUB NUMSUB valor:sessions:new` returned **0** — the
listener never subscribed — and a re-enqueued session sat `pending` until a
manual restart.

**Current behavior:**
- The notify listener (`agent/agent_session_queue.py:786`) calls
  `pubsub.subscribe("valor:sessions:new")` and logs success, but **never
  verifies the subscription actually registered**. If the subscribe silently
  no-ops, or the listener thread dies after construction but in a way that
  doesn't surface, the fast (~1 s) pickup path is lost with no self-heal short
  of an actual raised exception triggering the 5 s thread-restart loop.
- The worker plist (`com.valor.worker.plist`) and `scripts/install_worker.sh`
  do not set `VALOR_WORKER_MODE`, so `ps eww` on the live process shows no such
  variable — which *looks like* bridge mode at a glance.

**Desired outcome:**
- The notify listener proves its own subscription (`PUBSUB NUMSUB >= 1`) after
  subscribing and re-subscribes on failure (via its existing outer `while True`
  loop / 5 s backoff). A no-op subscribe at boot can no longer silently degrade
  session pickup — it is detected in-thread and re-attempted, owned entirely by
  the listener's own thread (no cross-thread machinery).
- `VALOR_WORKER_MODE=standalone` is explicit in the worker plist/installer, so
  operational inspection (`ps eww`) and any non-`main()` import path agree with
  the runtime behavior.
- The residual "subscription silently drops *after* a previously-good subscribe"
  risk is covered by the existing 300 s `_agent_session_health_check` backstop
  (`agent/session_health.py:2552`), which already re-scans pending sessions and
  nudges/starts workers. No new periodic probe is added.

## Freshness Check

**Baseline commit:** `275f1a077020d157f8fccaec22c1b6c2deb9963a`
**Issue filed at:** 2026-06-26T10:21:38Z
**Disposition:** Major drift (premise #1 reframed) — proceeding on a revised premise; the valid robustness work remains in scope.

**File:line references re-verified:**
- `agent/agent_session_queue.py` `_worker_loop` — still reads
  `standalone = os.environ.get("VALOR_WORKER_MODE") == "standalone"` at **line 1250**. Holds.
- `worker/__main__.py:703` — `os.environ.setdefault("VALOR_WORKER_MODE", "standalone")` runs
  inside `main()` **before** `asyncio.run(_run_worker(...))` at line 729. **This is the load-bearing fact the issue missed:** at runtime the worker IS in standalone mode (loops `await event.wait()` indefinitely; they do NOT exit after drain). `ps eww` reports the launchd *launch* environment, not runtime `os.environ` mutations.
- `agent/agent_session_queue.py:786` `_session_notify_listener` — subscribes and logs
  `"Session notify listener subscribed to valor:sessions:new"`; **no NUMSUB verification**. Holds (this is the real gap).
- `agent/session_health.py:2552` — `_agent_session_health_check` re-scans `status="pending"` every
  `AGENT_SESSION_HEALTH_CHECK_INTERVAL=300` s, nudging live workers' events and starting a worker
  for orphaned pending sessions older than `AGENT_SESSION_HEALTH_MIN_RUNNING=300` s. Holds — a
  backstop already exists, so "sat pending indefinitely" is not fully explained by a dead listener.

**Cited sibling issues/PRs re-checked:**
- #824 — *closed 2026-04-08*; notify listener lost notifications via inherited `socket_timeout`.
  Its fix (a dedicated `socket_timeout=None` Redis connection) is present in `_listen_in_thread`.
  Relevant: this is the same code path; the new self-check must not regress that connection setup.

**Commits on main since issue was filed (touching referenced files):**
- `9e120e2b` `fix(worker): catch StatusConflictError in _worker_loop` (#1803) — touches `_worker_loop`
  but is unrelated to the notify path; no impact on this plan.

**Active plans in `docs/plans/` overlapping this area:** none current. `docs/plans/pubsub-notify-listener-socket-timeout.md` is the (already-shipped) #824 fix — historical reference only.

**Notes:** The major drift is on the *root-cause framing*, not on the existence of real work. Premise #1 ("worker runs in bridge mode") is a misdiagnosis; the env-var change is retained as cheap defense-in-depth/observability, not as the behavior fix. Premise #2 (notify-listener has no subscription self-verification) is **confirmed and valid**. The "indefinitely" symptom is unexplained by the dead-listener theory given the 300 s backstop — surfaced as an Open Question rather than silently designed around.

## Prior Art

- **Issue #824**: *pub/sub notify listener loses session notifications due to inherited socket_timeout (regression from PR #784)* — closed 2026-04-08. Root cause: the global `POPOTO_REDIS_DB` pool's `socket_timeout=5` caused spurious "Timeout reading from socket" exceptions and a 10 s reconnect window that dropped notifications. Fixed by giving `_listen_in_thread` a dedicated connection with `socket_timeout=None`. **Directly relevant**: the same function we are hardening; the new NUMSUB check must reuse / not disturb that dedicated connection.
- **Issue #831**: *worker_key computed property routes pm/dev/teammate sessions by actual isolation level* — establishes that wakeups are keyed by `worker_key`; the notify payload carries `worker_key`. Relevant to ensuring the self-heal restores the same channel.

## Research

No external WebSearch performed — the work is purely internal (redis-py pub/sub + this repo's worker loop). `PUBSUB NUMSUB <channel>` is a standard Redis introspection command (returns `[channel, subscriber_count]`); redis-py exposes it via `Redis.pubsub_numsub(channel)`. Proceeding with codebase context and training data.

## Data Flow

1. **Entry point**: A process (CLI `valor_session`, bridge, or a recovering health check) calls `enqueue_agent_session()` → `_push_agent_session()`.
2. **Publish**: `enqueue_agent_session` (`agent/agent_session_queue.py:404`) computes `worker_key` and `POPOTO_REDIS_DB.publish("valor:sessions:new", payload)`. Fire-and-forget; failure only warns.
3. **Subscribe (the fragile hop)**: in the worker process, `_session_notify_listener` runs `_listen_in_thread` on a dedicated `socket_timeout=None` connection that `pubsub.subscribe("valor:sessions:new")` then blocks on `pubsub.listen()`. **If the subscribe never registered, the published payload is dropped silently.**
4. **Wake**: each received message → `loop.call_soon_threadsafe(notify_queue.put_nowait, (worker_key, is_project_keyed))` → coroutine calls `_ensure_worker(worker_key)` and `_active_events[worker_key].set()`, waking the persistent `_worker_loop`.
5. **Backstop**: independently, every 300 s `_agent_session_health_check` re-scans pending sessions and nudges/starts workers (`agent/session_health.py:2552`).
6. **Output**: `_worker_loop` pops the session and executes it.

The fix inserts a single verification + re-subscribe step at hop 3 (subscribe-time `NUMSUB >= 1` self-check, in the listener's own thread). The residual "subscription drops after a good subscribe" mode is left to hop 5 (the existing 300 s backstop) rather than a new periodic probe.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|------------------------|
| #824 / PR #784-era | Gave `_listen_in_thread` a dedicated `socket_timeout=None` connection so `pubsub.listen()` blocks indefinitely and stops dropping notifications mid-reconnect | It fixed *notification loss while subscribed*. It did **not** add any guarantee that the subscription *succeeded in the first place*, nor any liveness probe — so a boot where subscribe silently no-ops (or the thread never reaches `subscribe`) still leaves `NUMSUB=0` with no recovery beyond a raised exception. |

**Root cause pattern:** the listener trusts `subscribe()` without observing its effect. The repo already verifies effects elsewhere (heartbeat freshness, worker liveness via `_active_workers[...].done()`); the notify subscription is the one liveness-critical resource with no effect-verification.

## Architectural Impact

- **New dependencies**: none (uses redis-py's existing `pubsub_numsub` / a raw `PUBSUB NUMSUB`).
- **Interface changes**: none public. Internal: `_listen_in_thread` gains a single post-subscribe verification step, executed entirely in the listener's own thread on its own connection. No module-level holder, no cross-thread machinery, no new threads.
- **Coupling**: confined to `agent/agent_session_queue.py` (subscribe-time verify) plus the `com.valor.worker.plist` / `scripts/install_worker.sh` env-var addition. **No change to `worker/__main__.py` runtime wiring** and no change to `_notify_task_done`.
- **Data ownership**: unchanged.
- **Reversibility**: high — both changes are additive guards; reverting restores prior behavior.

## Appetite

**Size:** Small

**Team:** Solo dev, async-specialist review

**Interactions:**
- PM check-ins: 1-2 (confirm the reframed scope is acceptable; resolve the "indefinitely" Open Question)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable (Popoto) | `python -c "from popoto.redis_db import POPOTO_REDIS_DB; POPOTO_REDIS_DB.ping()"` | Pub/sub + NUMSUB checks need a live Redis |
| Worker importable | `python -m worker --dry-run` | The change touches worker startup/queue code |

## Solution

### Key Elements

- **Subscription self-verification (the confirmed-bug fix — the entire code change)**: after `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, confirm `PUBSUB NUMSUB valor:sessions:new >= 1` (counting this connection) **on the listener's own `socket_timeout=None` connection, in the listener's own thread**. If it reports 0, treat it as a failed subscribe: log a WARNING with the observed count, then fall through the existing `finally` (unsubscribe/close ordering) and let the outer `while True` loop re-attempt after its existing 5 s backoff. This closes premise #2 (the only *confirmed* defect): a no-op subscribe at boot is detected immediately and re-attempted, so the fast pickup path can no longer silently degrade at startup. Because the check, the connection it reads, and the connection it re-subscribes are all owned by one thread, there is **no cross-thread hazard** — no `threading.Lock`, no module-level holder, no second thread.
- **Explicit `VALOR_WORKER_MODE=standalone`** in `com.valor.worker.plist` `EnvironmentVariables` and (belt-and-suspenders) asserted by `scripts/install_worker.sh`, so `ps eww` and any future non-`main()` import path agree with runtime. **This is observability/defense-in-depth, not a behavior fix** — `worker/__main__.py:703` already runs `os.environ.setdefault("VALOR_WORKER_MODE", "standalone")` inside `main()` before the worker loop, so runtime is already standalone. The `setdefault` stays (it wins when the var is absent and is harmless when present).

> **Why no periodic NUMSUB probe?** (resolves the re-critique BLOCKERS + CONCERNS) An earlier revision added a periodic background probe that, on alive-but-`NUMSUB==0`, called `unsubscribe()` on the listener's *live* pubsub connection from a *separate* thread to force a re-subscribe. The re-critique found this both unsound and unnecessary:
> 1. **B1 — `unsubscribe()` cannot unblock `listen()` here.** The listener's `for message in pubsub.listen():` loop `continue`-skips every non-`message` frame (`agent/agent_session_queue.py:832`). An `unsubscribe` produces an `unsubscribe` control frame, which is skipped — so `listen()` does **not** return and the intended re-subscribe never fires.
> 2. **B2 — cross-thread mutation corrupts the connection.** redis-py `PubSub`/`Connection` objects are not thread-safe; issuing `unsubscribe()`/`close()` from the probe thread while the listener thread is mid-`listen()` races a write against a read on the same socket and can strand or corrupt the connection.
> 3. **It heals a never-observed drift mode** (silent `NUMSUB→0` *after* a good subscribe) and **overlaps two existing safety nets** — the subscribe-time self-verify (boot-time no-op subscribe) and the 300 s health backstop (any later drop). Cutting it removes both blockers and all four concerns at once with zero loss of confirmed-bug coverage.
>
> The residual "subscription silently drops after a previously-good subscribe" risk is therefore left to the **existing 300 s `_agent_session_health_check` backstop** (`agent/session_health.py:2552`), which already re-scans `status="pending"` sessions and nudges/starts workers. A dedicated probe is not added.

> **#824 invariant preserved.** The subscribe-time NUMSUB read happens on the listener's existing dedicated `socket_timeout=None` connection — no new connection, no new timeout, nothing that could resurrect #824's dropped-notification behavior.

### Flow

Worker boot → `main()` starts the existing `notify_task` (no new threads) → `_listen_in_thread` subscribes → **verify `NUMSUB >= 1` on its own `socket_timeout=None` connection** → (if 0) WARNING + fall through the existing `finally` → outer `while True` re-subscribes after its existing 5 s backoff; (if ≥1) block on `listen()` as today. The residual post-subscribe drop mode is recovered by the existing 300 s health backstop, not by any new code.

### Technical Approach

- **Subscribe-time check (the only code change in `agent/agent_session_queue.py`)**: after `subscribe`, read the subscribe acknowledgment (or bounded-retry `conn.pubsub_numsub("valor:sessions:new")` — up to ~3 reads over ~300 ms to absorb registration latency) on the listener's own dedicated `socket_timeout=None` connection. On a confirmed 0, log a WARNING with the observed count and fall through the existing `finally` so the outer loop re-subscribes after its 5 s backoff. All of this runs in `_listen_in_thread`'s own thread — no holder, no lock, no second thread.
- **No `worker/__main__.py` change**: `notify_task` wiring and `_notify_task_done` are untouched. No probe thread is added and no shutdown-join is needed.
- **#824 invariant**: the listener's `listen()` connection keeps `socket_timeout=None`. The subscribe-time NUMSUB read happens on that same connection (no new timeout, no new connection).
- **Fail-silent-but-logged**: the NUMSUB read is wrapped in try/except that logs a WARNING and falls through to the existing teardown; a raised `pubsub_numsub` must never crash the listener thread — the existing thread-error path already re-enters the outer loop, and the 300 s health backstop remains the final safety net.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new NUMSUB verification must wrap the Redis call in try/except that logs (WARNING) and falls through to teardown — assert via a test that injects a raising `pubsub_numsub` and verifies the listener thread does not crash and a warning is logged.
- [ ] Existing `except Exception` blocks in `_session_notify_listener` (thread-error path, line ~851) are unchanged; add a test asserting the restart-after-error path still fires.

### Empty/Invalid Input Handling
- [ ] `PUBSUB NUMSUB` returning `0` (or an empty/malformed reply) must be treated as "not subscribed" and trigger re-subscribe via the existing teardown + outer-loop re-attempt — test with a stubbed connection reporting 0.
- [ ] A `None`/missing channel reply must not raise — test the defensive parse.

### Error State Rendering
- [ ] Not user-visible. Operator-visible signal: a WARNING log line on subscribe-verify failure carrying the observed NUMSUB count (so a no-op subscribe is visible in logs). Test asserts the log line is emitted (caplog).

## Test Impact

- [ ] `tests/integration/test_session_notify.py::TestNotifyListenerSocketTimeout::test_notify_listener_uses_no_socket_timeout` — UPDATE: extend (do not break) to also assert the post-subscribe NUMSUB verification runs on the same `socket_timeout=None` connection; the #824 invariant must still hold.
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: add cases for the NUMSUB self-check only (subscribe→NUMSUB≥1 happy path; NUMSUB==0 → falls through teardown so the outer loop re-subscribes; raising NUMSUB → logged, no crash, teardown still runs). **No probe/holder/cross-thread test cases** — the periodic probe was cut. Existing tests in this file are not expected to change behavior.
- [ ] `tests/unit/test_worker_persistent.py` — VERIFY (no change expected): these already patch `VALOR_WORKER_MODE=standalone`; confirm the explicit plist var doesn't alter their assumptions. If a test asserts the var is *unset by default*, UPDATE it to reflect the explicit-in-plist intent. No new probe-thread wiring is added to `worker/__main__.py`, so no worker-startup test changes are required for the probe.

No test is expected to be DELETED — all changes are additive guards.

## Rabbit Holes

- **Re-adding a periodic background NUMSUB probe.** A prior revision tried this; the re-critique found it unsound (cross-thread `unsubscribe()` can't unblock the `continue`-skipping `listen()` loop, and cross-thread mutation corrupts the redis-py connection) and redundant with the subscribe-time self-verify + the 300 s backstop. **Do not reintroduce it.** Post-subscribe drift is the 300 s health backstop's job; if that proves insufficient in practice, redesign it as a thread-safe mechanism in a *separate* issue — do not bolt a cross-thread probe onto this listener.
- **Rewriting the notify listener as a fully async redis pub/sub (`redis.asyncio`) consumer.** Tempting, but it would re-litigate the #824 socket-timeout fix and the thread/queue bridge. Out of scope — add a guard to the existing design, don't replace it.
- **Chasing the "sat pending indefinitely" symptom into the health-check/worker-wedge path.** The 300 s backstop *should* recover stranded pending sessions; if it didn't, that's a separate defect (a wedged-but-alive worker), not the notify subscription. Do not expand this plan to fix a hypothesized wedge — file separately if reproduced.
- **Tuning `AGENT_SESSION_HEALTH_MIN_RUNNING` / health interval.** Reducing the backstop latency is a different lever; leave it.

## Risks

### Risk 1: Subscribe-time NUMSUB read races the subscribe registration
**Impact:** A NUMSUB read immediately after `subscribe()` could momentarily report 0 before Redis registers the subscriber, causing a spurious re-subscribe loop.
**Mitigation:** Read the subscribe confirmation reply (redis-py returns a subscribe acknowledgment) before / instead of an immediate NUMSUB, or add a tiny bounded retry (e.g. up to 3 reads over ~300 ms) before declaring failure. The check runs once per subscribe, in the listener's own thread, so there is no concurrent reader to race.

### Risk 2: Re-subscribe after a failed subscribe costs one backoff cycle
**Impact:** When the subscribe-time check reports 0, the listener tears down and the outer `while True` loop re-subscribes only after its existing `await asyncio.sleep(5)` backoff (`agent/agent_session_queue.py:896`) — i.e. re-subscribe is **~5 s per attempt, not sub-second**. A burst of failed subscribes at boot could take a few 5 s cycles to settle.
**Mitigation:** Acceptable and well within budget — even three failed cycles (~15 s) land comfortably under the 30 s end-to-end criterion and far under the 300 s health backstop. No tightening of the 5 s backoff is in scope; the contradiction with the earlier "sub-second" claim is resolved in favor of the real ~5 s figure.

### Risk 3: Regressing the #824 socket_timeout fix
**Impact:** Reintroducing a `socket_timeout=5` connection for the NUMSUB check would resurrect dropped-notification behavior.
**Mitigation:** Perform NUMSUB on the listener's own dedicated `socket_timeout=None` connection (or a clearly-scoped short-lived connection that is closed immediately and never used for `listen()`). The updated test asserts the listener connection keeps `socket_timeout=None`.

## Race Conditions

### Race 1: subscribe-vs-NUMSUB visibility
**Location:** `agent/agent_session_queue.py` `_listen_in_thread`, immediately after `pubsub.subscribe(...)`.
**Trigger:** NUMSUB read issued before Redis finishes registering the subscriber on this connection.
**Data prerequisite:** the subscription must be registered server-side before NUMSUB is meaningful.
**State prerequisite:** the dedicated connection is connected and the subscribe command acknowledged.
**Mitigation:** consume the subscribe acknowledgment message first, or bounded-retry the NUMSUB read; only declare failure after the bounded window.

*(The periodic-probe races from the prior revision are removed — the probe was cut, so there is no longer a cross-thread reader/writer on the listener's connection. The subscribe-time check is single-threaded and owns its connection, eliminating the cross-thread hazard class entirely.)*

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1804] The "pending session sat **indefinitely**" symptom: the existing 300 s health backstop should recover stranded pending sessions, so an indefinite hang implies a distinct wedged-but-alive-worker defect. This plan hardens the notify subscription only; if an indefinite hang is reproduced after this ships, it must be investigated as its own issue (tracked under this issue's discussion until then).
- Nothing else deferred — the env-var change and the listener self-heal are both in scope for this plan.

## Update System

`com.valor.worker.plist` is a deployed artifact installed by `scripts/install_worker.sh` (run on every machine via `/update`). Adding `VALOR_WORKER_MODE=standalone` to the plist template means existing installations must **re-run the installer** to pick up the new env var — this happens automatically on the next `/update` (which re-installs the worker plist). No new dependency or config file is introduced. Call this out in the install script's output so operators know a worker reinstall is required for the var to appear in `ps eww`. No `scripts/update/run.py` or `migrations.py` changes required (no Popoto schema change).

## Agent Integration

No agent integration required — this is a worker-internal change (Redis pub/sub robustness + a launchd env var). No new CLI entry point, no `mcp_servers/` / `.mcp.json` change, and the bridge does not import the modified code paths. The agent surface is unaffected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` — document the notify-listener subscribe-time self-verification (post-subscribe `NUMSUB >= 1` check + re-subscribe via the outer loop) and that `VALOR_WORKER_MODE=standalone` is now explicit in the worker plist, with a note that runtime mode was already standalone via `setdefault`. Note that post-subscribe drift is covered by the 300 s health backstop, not a dedicated probe.
- [ ] If a worker-mode/notify section exists in `docs/features/README.md`, ensure the index entry reflects the self-verify behavior.

### Inline Documentation
- [ ] Docstring update on `_listen_in_thread` describing the subscribe-time NUMSUB self-check (in-thread, on the listener's own connection) and that post-subscribe drift is left to the 300 s health backstop.
- [ ] Comment in `com.valor.worker.plist` near the new env var explaining it mirrors the runtime `setdefault` for observability.

## Success Criteria

- [ ] `com.valor.worker.plist` `EnvironmentVariables` contains `VALOR_WORKER_MODE=standalone`.
- [ ] `_listen_in_thread` verifies `NUMSUB >= 1` after subscribe and, when it reports 0, logs a WARNING with the observed count and falls through the existing `finally` teardown so the outer `while True` loop re-subscribes (after its existing 5 s backoff).
- [ ] **No new threads and no `worker/__main__.py` changes:** the self-verify is entirely in-thread on the listener's own connection. `grep -n 'daemon=True' worker/__main__.py` shows no notify-probe thread, and `_notify_task_done` is untouched.
- [ ] The #824 invariant holds: the listener's `listen()` connection still uses `socket_timeout=None`, and the NUMSUB read uses that same connection (asserted by the updated integration test).
- [ ] New unit tests cover exactly three cases: NUMSUB≥1 happy path; NUMSUB==0 → falls through teardown → outer loop re-subscribes (WARNING with count emitted); raising `pubsub_numsub` → logged + listener thread does not crash + teardown still runs. **No probe/holder/cross-thread test cases.**
- [ ] **Falsifiable end-to-end (the "indefinitely pending" symptom is gone):** with the subscribe-time self-verify in place, a listener whose first `subscribe` is simulated to register `NUMSUB==0` detects the failure, re-subscribes via the outer loop, and a pending session enqueued at boot is picked up within **30 s** — well before the 300 s health backstop could fire. This validates the in-thread subscribe-time fix (not the removed probe and not the pre-existing backstop).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (notify-selfheal)**
  - Name: notify-builder
  - Role: Implement the subscribe-time NUMSUB self-verification (in-thread) in the notify listener; add explicit plist env var. No periodic probe, no worker/__main__.py changes.
  - Agent Type: async-specialist
  - Resume: true

- **Validator (notify-selfheal)**
  - Name: notify-validator
  - Role: Verify subscription self-heal, #824 invariant preserved, tests cover failure paths
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: worker-doc
  - Role: Update bridge-worker-architecture docs + docstrings
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add explicit VALOR_WORKER_MODE to plist + installer
- **Task ID**: build-env-var
- **Depends On**: none
- **Validates**: `grep VALOR_WORKER_MODE com.valor.worker.plist`
- **Assigned To**: notify-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Add `<key>VALOR_WORKER_MODE</key><string>standalone</string>` to `com.valor.worker.plist` `EnvironmentVariables`.
- Add an explanatory comment; ensure `scripts/install_worker.sh` env-injection logic does not strip it (plist values take precedence over `.env`).
- Note in install output that a worker reinstall is needed for `ps eww` to show it.

### 2. Notify-listener subscribe-time self-verification (in-thread)
- **Task ID**: build-notify-selfheal
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_async.py, tests/integration/test_session_notify.py
- **Informed By**: #824 (preserve `socket_timeout=None` connection)
- **Assigned To**: notify-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- After `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, verify `NUMSUB >= 1` (consume the subscribe ack or bounded-retry `pubsub_numsub` — up to ~3 reads over ~300 ms — on the listener's own dedicated `socket_timeout=None` connection); on a confirmed 0, log WARNING with the observed count and fall through the existing `finally` teardown so the outer `while True` loop re-subscribes after its existing 5 s backoff.
- Wrap the NUMSUB read in try/except: a raised `pubsub_numsub` logs WARNING and falls through teardown — it must not crash the listener thread.
- **Do NOT add a periodic probe, a module-level holder, a `threading.Lock`, or any new thread. Do NOT modify `worker/__main__.py` or `_notify_task_done`.** The entire change is confined to `_listen_in_thread` in `agent/agent_session_queue.py`.

### 3. Validation
- **Task ID**: validate-notify-selfheal
- **Depends On**: build-env-var, build-notify-selfheal
- **Assigned To**: notify-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm NUMSUB self-check + re-subscribe behavior; confirm #824 `socket_timeout=None` invariant; run new + existing notify tests.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-notify-selfheal
- **Assigned To**: worker-doc
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` and docstrings.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: notify-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full verification table; confirm all success criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Plist sets standalone mode | `grep -A1 VALOR_WORKER_MODE com.valor.worker.plist \| grep -c standalone` | output contains 1 |
| NUMSUB self-check present | `grep -rn 'NUMSUB\|pubsub_numsub' agent/agent_session_queue.py` | exit code 0 |
| No new notify-probe thread added | `git diff main -- worker/__main__.py` | empty (no `worker/__main__.py` change) |
| No cross-thread holder/lock added for notify | `grep -rn 'pubsub.*holder\|_notify.*Lock' agent/agent_session_queue.py` | exit code 1 (no match) |
| #824 invariant preserved | `grep -c 'socket_timeout=None' agent/agent_session_queue.py` | output > 0 |
| Notify tests pass | `pytest tests/integration/test_session_notify.py tests/unit/test_agent_session_queue_async.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |

## Critique Results

### Second critique pass (re-critique of the 1st revision) — RESOLVED by cutting the probe

<!-- /do-plan-critique re-critique 2026-06-26T15:04Z. Verdict: NEEDS REVISION (2 blockers + 4 concerns) — ALL targeting the periodic NUMSUB probe (Solution element #2 of the 1st revision). 2nd revision applied 2026-06-26: the probe is CUT entirely, which resolves every finding at once. -->

| Severity | Finding (all targeting the periodic probe) | Resolution (2nd revision) |
|----------|--------------------------------------------|---------------------------|
| BLOCKER (B1) | The probe's cross-thread `unsubscribe()` cannot unblock the listener's `pubsub.listen()`: the loop `continue`-skips every non-`message` frame (`agent/agent_session_queue.py:832`), so the `unsubscribe` control frame is skipped and `listen()` never returns → the intended re-subscribe never fires. | **RESOLVED — probe CUT.** No cross-thread `unsubscribe()` exists anymore. Re-subscribe is driven only by the in-thread subscribe-time self-verify falling through the existing teardown → outer loop. |
| BLOCKER (B2) | Cross-thread mutation corrupts the connection: redis-py `PubSub`/`Connection` are not thread-safe; the probe thread's `unsubscribe()`/`close()` races the listener thread's `listen()` read on the same socket. | **RESOLVED — probe CUT.** The only NUMSUB read now happens in the listener's own thread on its own connection; no second thread touches it. |
| CONCERN ×4 | (a) Probe heals a never-observed drift mode; (b) overlaps the subscribe-time self-verify; (c) overlaps the 300 s backstop; (d) added cross-thread complexity (holder + Lock + generation token + daemon thread + shutdown join). | **RESOLVED — probe CUT.** All four dissolve with removal. Post-subscribe drift is left to the existing 300 s `_agent_session_health_check` backstop; no new threads/holders/locks are introduced. |
| Prose (C1) | "outer loop re-subscribes on its next iteration (sub-second)" contradicted the real `await asyncio.sleep(5)` backoff at `agent/agent_session_queue.py:896`. | **RESOLVED.** Risk 2 rewritten to state re-subscribe is **~5 s per attempt**; a few cycles still land under the 30 s E2E criterion. |

### First critique pass (of the original plan) — RESOLVED by the 1st revision

<!-- Populated by /do-plan-critique (war room) 2026-06-26 — FULL depth (3 critics). Verdict: NEEDS REVISION (1 blocker). Revision applied 2026-06-26 — all findings RESOLVED below. -->
<!-- NOTE: the 1st-revision rows below describe the now-CUT periodic probe; they are retained as history. The probe they introduced was removed in the 2nd revision (see table above). -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency (Consistency Auditor) | Success criterion "periodic probe restarts notify task on alive-but-NUMSUB==0 without worker restart" contradicts the approach: cancelling the asyncio task fires `_notify_task_done` which returns on `t.cancelled()` with no resurrection → listener silently goes dark. "Reuses _notify_task_done callback pattern" cannot deliver the criterion. | RESOLVED (revision) | Redesigned: the probe signals the *inner thread* to exit via `unsubscribe()`/`close()` on the live pubsub (exposed through a Lock-guarded module-level holder) → `pubsub.listen()` returns → existing `finally` puts `None` → the existing outer `while True` (line 796) re-subscribes. The asyncio `notify_task` is never cancelled and `_notify_task_done` is untouched. Criterion 3 reworded to "triggers re-subscription of the notify listener." See Solution → Key Elements (the two callout blocks). |
| CONCERN | Risk & Robustness (Adversary) + History & Consistency (Archaeologist) | Periodic probe is placed as an `asyncio.create_task` inside the very event loop it guards, and calls synchronous redis-py (`pubsub_numsub`) directly — stalling the loop for the round-trip (or indefinitely on a hung Redis). This also violates the plan's own cited #1767 precedent ("liveness checks outside the saturable loop"), whose actual fix was a daemon thread. | RESOLVED (revision) | Probe is now a `threading.Thread(target=..., daemon=True)` started in `worker/__main__.py` and stopped via a `threading.Event`+`join()` in shutdown (mirrors the #1767 heartbeat thread). It never runs on the asyncio loop. Its NUMSUB read uses a bounded `socket_connect_timeout` (~2 s) connection. See Technical Approach. |
| CONCERN | Scope & Value (Simplifier) | The periodic probe targets an unconfirmed failure mode (alive-but-NUMSUB==0 after a successful subscribe — never observed) and overlaps both the subscribe-time self-verify (which fixes the confirmed bug) and the existing 300 s backstop. | RESOLVED (revision) | Decision: keep the probe (the issue's explicit desired outcome asks for periodic re-subscribe self-heal) but (a) the plan now states plainly that the **subscribe-time self-verify is the confirmed-bug fix** and the probe is defense-in-depth for the unobserved drift mode, and (b) the probe MUST use a dedicated short-lived bounded-timeout connection for the NUMSUB read — never the listener's `socket_timeout=None` pubsub connection (which can itself block on the wedge it detects). Added as a Solution callout block and a Success Criterion. |
| NIT | Risk & Robustness (Operator) | Probe-triggered self-heal restarts are invisible in logs: `_notify_task_done` returns silently on the cancelled-task path, indistinguishable from clean shutdown. | RESOLVED (revision) | Probe logs a WARNING immediately before signalling re-subscription (e.g. `"Session notify self-heal: NUMSUB==0 with listener alive; re-subscribing"`). Added to Success Criteria, Error State Rendering test, and the probe task step. |
| NIT | Scope & Value (User) | No falsifiable acceptance criterion proves the original "indefinitely pending" symptom is gone; current criteria are all internal. | RESOLVED (revision) | Added Success Criterion: after a simulated NUMSUB==0 startup, a pending session is picked up within 30 s (via the subscribe-time self-verify, before the 300 s backstop could fire). |
| OPEN-Q | (plan Open Question #1) | Whether the "indefinitely pending" symptom is a separate wedged-but-alive-worker defect distinct from a dead notify listener. | RESOLVED (revision) | **File separately.** The 300 s `_agent_session_health_check` backstop (`agent/session_health.py:2552`) already re-scans pending sessions, so an *indefinite* hang implies a distinct wedged-worker defect, not the notify subscription. This plan hardens the notify subscription only; the wedge is captured in No-Gos as `[SEPARATE-SLUG #1804]` and will be filed as its own issue if reproduced after this ships. |

---

## Resolved Decisions (from critique revision)

1. **The "indefinitely" symptom → file separately.** The 300 s `_agent_session_health_check` backstop already re-scans pending sessions, so an *indefinite* hang implies a distinct wedged-but-alive-worker defect rather than a dead notify listener. This plan hardens the notify subscription only; the wedge path is captured in No-Gos (`[SEPARATE-SLUG #1804]`) and will be filed as its own investigation issue if reproduced after this ships.
2. **Scope.** The notify-listener subscribe-time self-verify is the *confirmed-bug* core of this work; the `VALOR_WORKER_MODE=standalone` plist entry is observability/defense-in-depth (runtime is already standalone via `setdefault`).
3. **Periodic NUMSUB probe → CUT entirely (2nd revision).** The first revision's daemon-thread probe was found unsound and redundant by the re-critique: (B1) `unsubscribe()` cannot unblock the listener's `listen()` loop because non-`message` frames are `continue`-skipped; (B2) cross-thread mutation of the non-thread-safe redis-py `PubSub`/connection corrupts it; and it heals a never-observed drift mode while overlapping the subscribe-time self-verify and the 300 s backstop. Removing it eliminates both blockers and all four concerns at once. Post-subscribe drift is left to the existing 300 s health backstop. The subscribe-time self-verify (in-thread, owning-thread-safe) is the only code change to the listener.
