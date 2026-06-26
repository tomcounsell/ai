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
  subscribing and re-subscribes on failure, and a lightweight periodic check
  restarts the listener if its subscription has silently dropped. A missed
  subscription can no longer silently degrade session pickup.
- `VALOR_WORKER_MODE=standalone` is explicit in the worker plist/installer, so
  operational inspection (`ps eww`) and any non-`main()` import path agree with
  the runtime behavior.

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
- **Issue #1767**: heartbeat moved to a dedicated daemon thread so event-loop saturation can't starve it. Pattern precedent: liveness-critical checks belong outside the saturable asyncio loop where feasible. Informs where the periodic NUMSUB check should live.

## Research

No external WebSearch performed — the work is purely internal (redis-py pub/sub + this repo's worker loop). `PUBSUB NUMSUB <channel>` is a standard Redis introspection command (returns `[channel, subscriber_count]`); redis-py exposes it via `Redis.pubsub_numsub(channel)`. Proceeding with codebase context and training data.

## Data Flow

1. **Entry point**: A process (CLI `valor_session`, bridge, or a recovering health check) calls `enqueue_agent_session()` → `_push_agent_session()`.
2. **Publish**: `enqueue_agent_session` (`agent/agent_session_queue.py:404`) computes `worker_key` and `POPOTO_REDIS_DB.publish("valor:sessions:new", payload)`. Fire-and-forget; failure only warns.
3. **Subscribe (the fragile hop)**: in the worker process, `_session_notify_listener` runs `_listen_in_thread` on a dedicated `socket_timeout=None` connection that `pubsub.subscribe("valor:sessions:new")` then blocks on `pubsub.listen()`. **If the subscribe never registered, the published payload is dropped silently.**
4. **Wake**: each received message → `loop.call_soon_threadsafe(notify_queue.put_nowait, (worker_key, is_project_keyed))` → coroutine calls `_ensure_worker(worker_key)` and `_active_events[worker_key].set()`, waking the persistent `_worker_loop`.
5. **Backstop**: independently, every 300 s `_agent_session_health_check` re-scans pending sessions and nudges/starts workers (`agent/session_health.py:2552`).
6. **Output**: `_worker_loop` pops the session and executes it.

The fix inserts a verification + re-subscribe step at hop 3, plus a periodic liveness check that re-runs hop 3 if `NUMSUB` falls to 0.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|------------------------|
| #824 / PR #784-era | Gave `_listen_in_thread` a dedicated `socket_timeout=None` connection so `pubsub.listen()` blocks indefinitely and stops dropping notifications mid-reconnect | It fixed *notification loss while subscribed*. It did **not** add any guarantee that the subscription *succeeded in the first place*, nor any liveness probe — so a boot where subscribe silently no-ops (or the thread never reaches `subscribe`) still leaves `NUMSUB=0` with no recovery beyond a raised exception. |

**Root cause pattern:** the listener trusts `subscribe()` without observing its effect. The repo already verifies effects elsewhere (heartbeat freshness, worker liveness via `_active_workers[...].done()`); the notify subscription is the one liveness-critical resource with no effect-verification.

## Architectural Impact

- **New dependencies**: none (uses redis-py's existing `pubsub_numsub` / a raw `PUBSUB NUMSUB`).
- **Interface changes**: none public. Internal: `_session_notify_listener` / `_listen_in_thread` gain a post-subscribe verification step and publish their live pubsub handle to a `threading.Lock`-guarded module-level holder; a dedicated `daemon` probe thread is added in `worker/__main__.py`.
- **Coupling**: stays within `agent/agent_session_queue.py` (subscribe-time verify + holder) and `worker/__main__.py` (daemon probe wiring + shutdown join). No change to `_notify_task_done`.
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

- **Subscription self-verification (the confirmed-bug fix)**: after `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, confirm `PUBSUB NUMSUB valor:sessions:new >= 1` (counting this connection). If it reports 0, treat it as a failed subscribe: tear down (the existing `finally` unsubscribe/close ordering) and let the outer `while True` re-attempt. Log at WARNING with the observed count so the failure is visible. This alone closes premise #2 (the only *confirmed* defect): a no-op subscribe is detected immediately and re-attempted, so the fast pickup path can no longer silently degrade at boot.
- **Periodic liveness probe — daemon thread, resurrection via unsubscribe (not task-cancel)**: a lightweight check runs on a `threading.Thread(daemon=True)` started in `worker/__main__.py` (per the #1767 precedent — liveness-critical checks live *outside* the saturable asyncio loop). Every ~60 s it opens a **dedicated short-lived Redis connection** (closed immediately after the read; `socket_connect_timeout≈2 s`) and runs `PUBSUB NUMSUB valor:sessions:new`. If the listener is supposed to be running but `NUMSUB == 0` (silent drop after a previously-good subscribe), it logs a WARNING and **triggers re-subscription of the listener — without restarting the asyncio task and without restarting the worker** — by calling `unsubscribe()` (fallback: `close()`) on the listener's *live* pubsub connection. That makes the blocked `pubsub.listen()` return → the existing `finally` puts `None` on the queue → the existing `while True` outer loop in `_session_notify_listener` re-subscribes on its next iteration. The asyncio `notify_task` never completes, so `_notify_task_done` is **not** involved and needs no change.
- **Explicit `VALOR_WORKER_MODE=standalone`** in `com.valor.worker.plist` `EnvironmentVariables` and (belt-and-suspenders) asserted by `scripts/install_worker.sh`, so `ps eww` and any future non-`main()` import path agree with runtime. The existing `os.environ.setdefault` in `worker/__main__.py` stays (it already wins when the var is absent and is harmless when present).

> **Why not cancel the asyncio task to restart?** (resolves the critique BLOCKER) `_notify_task_done` (`worker/__main__.py:554`) returns on `t.cancelled()` with no resurrection logic, so cancelling `notify_task` would leave the listener permanently dark. The correct seam already exists: `_session_notify_listener`'s outer `while True` (line 796) re-subscribes whenever the inner thread signals exit via `None` on the queue (line 870). The probe therefore signals the *inner thread* to return, never the outer task.

> **Why a dedicated probe connection?** (resolves the connection-hazard CONCERN) The listener's own connection uses `socket_timeout=None` and can itself block indefinitely on the very wedge the probe is trying to detect — a NUMSUB read on it would provide no safety guarantee. The probe must use a separate, short-lived, bounded-timeout connection for the *read*; it only touches the listener's live pubsub connection for the *unsubscribe signal*.

### Flow

Worker boot → `main()` starts `notify_task` **and** a `daemon` NUMSUB-probe thread → `_listen_in_thread` subscribes → **verify NUMSUB ≥ 1 on the dedicated `socket_timeout=None` conn** → (if 0) WARNING + tear down → outer loop re-subscribes; (if ≥1) publish the live pubsub handle to a module-level holder and block on `listen()` → … meanwhile every ~60 s the daemon probe opens a short-lived bounded-timeout conn, reads `NUMSUB` → if listener-should-be-up but `NUMSUB == 0`: WARNING + `unsubscribe()` (fallback `close()`) on the held live pubsub → `listen()` returns → `finally` puts `None` → outer loop re-subscribes. No task cancel, no worker restart.

### Technical Approach

- **Subscribe-time check**: after `subscribe`, read the subscribe acknowledgment (or bounded-retry `conn.pubsub_numsub("valor:sessions:new")` — up to ~3 reads over ~300 ms to absorb registration latency) on the listener's own dedicated `socket_timeout=None` connection. On a confirmed 0, WARNING and fall through the existing `finally` so the outer loop re-subscribes.
- **Expose the live pubsub handle**: store the active `pubsub` (and a generation/identity token) in a module-level holder guarded by a `threading.Lock` so the daemon probe can call `unsubscribe()`/`close()` on it cross-thread. Clear the holder in the `finally` teardown so the probe never signals a torn-down connection.
- **Periodic probe placement**: a `threading.Thread(target=_notify_subscription_probe, daemon=True)` started near the `notify_task` creation in `worker/__main__.py` (~line 552), stopped via a `threading.Event` in the shutdown sequence (mirroring the `_heartbeat_stop_event`/`heartbeat_thread.join()` pattern at line 638). Each tick: open dedicated conn → `pubsub_numsub` → close → if `NUMSUB == 0` and the holder shows a live listener, WARNING + signal re-subscribe. A simple "re-subscribe in progress" guard (compare the holder's generation token across ticks) prevents a second signal while the first re-subscribe is settling.
- **#824 invariant**: the listener's `listen()` connection keeps `socket_timeout=None`. The subscribe-time NUMSUB read happens on that same connection (no new timeout). The probe's NUMSUB read uses a *separate* short-lived connection with a bounded `socket_connect_timeout`; it is never used for `listen()`.
- **Fail-silent-but-logged**: every probe Redis call is wrapped in try/except that logs WARNING and continues; a probe error must never crash the worker — the 300 s health backstop remains the final safety net.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new NUMSUB verification and daemon probe must wrap Redis calls in try/except that logs (WARNING) and continues — assert via a test that injects a raising `pubsub_numsub` and verifies the worker does not crash and a warning is logged.
- [ ] Existing `except Exception` blocks in `_session_notify_listener` (thread-error path, line ~851) are unchanged; add a test asserting the restart-after-error path still fires.
- [ ] The probe's cross-thread `unsubscribe()` raising must fall back to `close()` without crashing — test with a stubbed live pubsub whose `unsubscribe` raises.

### Empty/Invalid Input Handling
- [ ] `PUBSUB NUMSUB` returning `0` (or an empty/malformed reply) must be treated as "not subscribed" and trigger re-subscribe — test with a stubbed connection reporting 0.
- [ ] A `None`/missing channel reply must not raise — test the defensive parse.
- [ ] A cleared/None holder (mid-teardown) must make the probe skip the tick, not raise — test the holder-empty branch.

### Error State Rendering
- [ ] Not user-visible. Operator-visible signal: a WARNING log line on subscribe-verify failure AND a distinct WARNING emitted by the probe immediately before it signals re-subscription (so self-heal is visible in logs, addressing the operator NIT). Test asserts both log lines are emitted (caplog).

## Test Impact

- [ ] `tests/integration/test_session_notify.py::TestNotifyListenerSocketTimeout::test_notify_listener_uses_no_socket_timeout` — UPDATE: extend (do not break) to also assert the post-subscribe NUMSUB verification runs on the same `socket_timeout=None` connection; the #824 invariant must still hold.
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: add cases for the NUMSUB self-check (subscribe→NUMSUB≥1 happy path; NUMSUB==0 → re-subscribe; raising NUMSUB → logged, no crash) and the probe signal path (alive-but-NUMSUB==0 → live pubsub `unsubscribe()` called via the holder → outer loop re-subscribes; `unsubscribe()` raising → `close()` fallback). Existing tests in this file are not expected to change behavior.
- [ ] `tests/unit/test_worker_persistent.py` — VERIFY (no change expected): these already patch `VALOR_WORKER_MODE=standalone`; confirm the explicit plist var doesn't alter their assumptions. If a test asserts the var is *unset by default*, UPDATE it to reflect the explicit-in-plist intent.

No test is expected to be DELETED — all changes are additive guards.

## Rabbit Holes

- **Rewriting the notify listener as a fully async redis pub/sub (`redis.asyncio`) consumer.** Tempting, but it would re-litigate the #824 socket-timeout fix and the thread/queue bridge. Out of scope — add a guard to the existing design, don't replace it.
- **Chasing the "sat pending indefinitely" symptom into the health-check/worker-wedge path.** The 300 s backstop *should* recover stranded pending sessions; if it didn't, that's a separate defect (a wedged-but-alive worker), not the notify subscription. Do not expand this plan to fix a hypothesized wedge — file separately if reproduced.
- **Tuning `AGENT_SESSION_HEALTH_MIN_RUNNING` / health interval.** Reducing the backstop latency is a different lever; leave it.

## Risks

### Risk 1: NUMSUB probe races the subscribe registration
**Impact:** A NUMSUB read immediately after `subscribe()` could momentarily report 0 before Redis registers the subscriber, causing a spurious re-subscribe loop.
**Mitigation:** Read the subscribe confirmation reply (redis-py returns a subscribe acknowledgment) before / instead of an immediate NUMSUB, or add a tiny bounded retry (e.g. up to 3 reads over ~300 ms) before declaring failure. The periodic probe uses a generous interval (~60 s) so it never races a fresh subscribe.

### Risk 2: Re-subscription drops in-flight notifications
**Impact:** During a self-heal re-subscribe (unsubscribe → `listen()` returns → outer loop re-subscribes), a publish in the gap is lost. The asyncio task is *not* restarted, but the brief unsubscribed window still exists.
**Mitigation:** Acceptable — the 300 s health backstop catches anything missed in the re-subscribe window, and re-subscribes are rare (only on detected NUMSUB==0 after a previously-good subscribe). The outer loop re-subscribes on its next iteration (sub-second).

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

### Race 2: periodic probe vs. re-subscription in progress
**Location:** the daemon probe thread in `worker/__main__.py` and the module-level pubsub holder in `agent/agent_session_queue.py`.
**Trigger:** the probe samples NUMSUB while a self-heal re-subscribe is mid-flight (inner thread has returned, the outer loop's fresh `subscribe` not yet acknowledged) and would observe a transient 0.
**Data prerequisite:** a single in-flight re-subscribe must not trigger a second concurrent signal.
**State prerequisite:** "re-subscribe in progress" must be observable to the probe.
**Mitigation:** the holder carries a generation token bumped on each fresh subscribe and cleared in the `finally` teardown. The probe only signals when the holder shows a *live, unchanged* generation reporting `NUMSUB == 0`; it skips a tick when the holder is cleared (mid-teardown) or its generation changed since the prior tick (a re-subscribe it already initiated is settling).

### Race 3: cross-thread unsubscribe on the live pubsub connection
**Location:** the daemon probe calling `unsubscribe()`/`close()` on the holder's live pubsub while the listener thread blocks in `pubsub.listen()`.
**Trigger:** redis-py `PubSub` objects are not fully thread-safe; an `unsubscribe()` write from the probe thread races the `listen()` read in the listener thread.
**Data prerequisite:** the unsubscribe must reach the socket so `listen()` returns; a half-applied write must not corrupt the connection in a way that strands the listener.
**State prerequisite:** the holder reference must point at the *current* live pubsub (not a torn-down one).
**Mitigation:** the holder is guarded by a `threading.Lock`; the probe takes the lock, re-checks the generation token, then issues a single `unsubscribe()` (the documented way to break a blocked `listen()`), falling back to `close()` if `unsubscribe()` raises. Either way the listener thread's `finally` runs, puts `None`, and the outer loop rebuilds a fresh connection+pubsub — so even a corrupted connection is fully replaced, not reused.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1804] The "pending session sat **indefinitely**" symptom: the existing 300 s health backstop should recover stranded pending sessions, so an indefinite hang implies a distinct wedged-but-alive-worker defect. This plan hardens the notify subscription only; if an indefinite hang is reproduced after this ships, it must be investigated as its own issue (tracked under this issue's discussion until then).
- Nothing else deferred — the env-var change and the listener self-heal are both in scope for this plan.

## Update System

`com.valor.worker.plist` is a deployed artifact installed by `scripts/install_worker.sh` (run on every machine via `/update`). Adding `VALOR_WORKER_MODE=standalone` to the plist template means existing installations must **re-run the installer** to pick up the new env var — this happens automatically on the next `/update` (which re-installs the worker plist). No new dependency or config file is introduced. Call this out in the install script's output so operators know a worker reinstall is required for the var to appear in `ps eww`. No `scripts/update/run.py` or `migrations.py` changes required (no Popoto schema change).

## Agent Integration

No agent integration required — this is a worker-internal change (Redis pub/sub robustness + a launchd env var). No new CLI entry point, no `mcp_servers/` / `.mcp.json` change, and the bridge does not import the modified code paths. The agent surface is unaffected.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` — document the notify-listener self-healing (NUMSUB verification + periodic re-subscribe) and that `VALOR_WORKER_MODE=standalone` is now explicit in the worker plist, with a note that runtime mode was already standalone via `setdefault`.
- [ ] If a worker-mode/notify section exists in `docs/features/README.md`, ensure the index entry reflects the self-heal behavior.

### Inline Documentation
- [ ] Docstring update on `_session_notify_listener` / `_listen_in_thread` describing the NUMSUB self-check and the periodic probe contract.
- [ ] Comment in `com.valor.worker.plist` near the new env var explaining it mirrors the runtime `setdefault` for observability.

## Success Criteria

- [ ] `com.valor.worker.plist` `EnvironmentVariables` contains `VALOR_WORKER_MODE=standalone`.
- [ ] `_listen_in_thread` verifies `NUMSUB >= 1` after subscribe and re-subscribes (via the existing teardown + outer-loop re-attempt) when it reports 0, emitting a WARNING with the observed count.
- [ ] A periodic liveness probe (daemon thread) **triggers re-subscription of the notify listener** when it is alive-but-`NUMSUB==0`, without restarting the asyncio `notify_task` and without restarting the worker process. The probe logs a WARNING immediately before signalling re-subscription.
- [ ] The probe runs on a `threading.Thread(daemon=True)` (not an asyncio task inside the guarded loop) and reads NUMSUB on a dedicated short-lived bounded-timeout connection — never the listener's `socket_timeout=None` connection.
- [ ] The #824 invariant holds: the listener's `listen()` connection still uses `socket_timeout=None` (asserted by the updated integration test).
- [ ] New unit tests cover: NUMSUB≥1 happy path, NUMSUB==0 → re-subscribe, raising NUMSUB → logged + no crash, and the probe's signal path (alive-but-NUMSUB==0 → live pubsub `unsubscribe()` invoked → outer loop re-subscribes).
- [ ] **Falsifiable end-to-end (the "indefinitely pending" symptom is gone):** after a simulated NUMSUB==0 at startup, an enqueued/pending session is picked up within **30 s** — i.e. via the subscribe-time self-verify path, well before the 300 s health backstop could fire. This distinguishes the new subscribe-time fix from the pre-existing backstop.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (notify-selfheal)**
  - Name: notify-builder
  - Role: Implement NUMSUB verification + periodic re-subscribe in the notify listener; add explicit plist env var
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

### 2. Notify-listener subscription self-verification + self-heal
- **Task ID**: build-notify-selfheal
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_async.py, tests/integration/test_session_notify.py
- **Informed By**: #824 (preserve `socket_timeout=None` connection)
- **Assigned To**: notify-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- After `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, verify `NUMSUB >= 1` (consume the subscribe ack or bounded-retry NUMSUB on the dedicated `socket_timeout=None` connection); on 0, log WARNING and fall through the existing `finally` teardown so the outer `while True` loop re-subscribes.
- Publish the live `pubsub` handle + a generation token to a `threading.Lock`-guarded module-level holder; clear it in the `finally` teardown.
- Add a periodic liveness probe as a `threading.Thread(target=..., daemon=True)` started near the `notify_task` creation in `worker/__main__.py` (~line 552) and stopped via a `threading.Event` + `join()` in the shutdown sequence (mirror `_heartbeat_stop_event` at ~line 638). Each tick reads NUMSUB on a **dedicated short-lived connection** with bounded `socket_connect_timeout`; on alive-but-`NUMSUB==0` it logs WARNING then takes the holder lock and calls `unsubscribe()` (fallback `close()`) on the live pubsub to trigger re-subscription. Guard against double-signalling via the generation token. **Do NOT cancel `notify_task` and do NOT modify `_notify_task_done`.**
- Keep all probe Redis calls fail-silent-but-logged.

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
| Daemon probe thread present | `grep -rn 'daemon=True' worker/__main__.py` | matches the notify-probe thread |
| Probe does NOT cancel notify_task for self-heal | `grep -n 'notify_task.cancel' worker/__main__.py` | only the shutdown cancel (line ~649), none in the probe |
| #824 invariant preserved | `grep -c 'socket_timeout=None' agent/agent_session_queue.py` | output > 0 |
| Notify tests pass | `pytest tests/integration/test_session_notify.py tests/unit/test_agent_session_queue_async.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room) 2026-06-26 — FULL depth (3 critics). Verdict: NEEDS REVISION (1 blocker). Revision applied 2026-06-26 — all findings RESOLVED below. -->
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
2. **Scope.** The notify-listener subscribe-time self-verify is the *confirmed-bug* core of this work; the `VALOR_WORKER_MODE=standalone` plist entry is observability/defense-in-depth (runtime is already standalone via `setdefault`), and the periodic daemon probe is defense-in-depth for the unobserved silent-drop mode.
3. **Periodic probe placement → dedicated daemon thread.** Implemented as a `threading.Thread(daemon=True)` (~60 s tick) per the #1767 precedent, *not* folded into the asyncio health loop (which would block the loop on a synchronous redis-py call) and *not* an asyncio task.
