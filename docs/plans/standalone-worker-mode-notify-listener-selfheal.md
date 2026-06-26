---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-26
tracking: https://github.com/tomcounsell/ai/issues/1804
last_comment_id: none
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
- **Interface changes**: none public. Internal: `_session_notify_listener` / `_listen_in_thread` gain a post-subscribe verification step; a small periodic check is added (either folded into the existing 5-min health loop or a dedicated short-interval task).
- **Coupling**: unchanged — stays within `agent/agent_session_queue.py` (+ optionally `worker/__main__.py` for the periodic check wiring).
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

- **Subscription self-verification**: after `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, confirm `PUBSUB NUMSUB valor:sessions:new >= 1` (counting this connection). If it reports 0, treat it as a failed subscribe: tear down (the existing `finally` unsubscribe/close ordering) and let the outer `while True` re-attempt after the existing 5 s backoff. Log at WARNING with the observed count so the failure is visible.
- **Periodic liveness probe**: a lightweight check (every ~60 s, or folded into the existing health loop) verifies the notify task is still running *and* `NUMSUB >= 1`. If the task is alive but `NUMSUB == 0` (silent drop), cancel/restart the notify task so a fresh subscribe runs. This is the actual "self-healing" the issue asks for — recovery without a full worker restart.
- **Explicit `VALOR_WORKER_MODE=standalone`** in `com.valor.worker.plist` `EnvironmentVariables` and (belt-and-suspenders) asserted by `scripts/install_worker.sh`, so `ps eww` and any future non-`main()` import path agree with runtime. The existing `os.environ.setdefault` in `worker/__main__.py` stays (it already wins when the var is absent and is harmless when present).

### Flow

Worker boot → start notify task → `_listen_in_thread` subscribes → **verify NUMSUB ≥ 1** → (if 0) tear down + 5 s backoff + retry; (if ≥1) block on `listen()` → … meanwhile every ~60 s a liveness probe checks task-alive + NUMSUB ≥ 1 → (if drifted to 0) restart notify task → fresh subscribe.

### Technical Approach

- Use a `PUBSUB NUMSUB valor:sessions:new` introspection on a short-lived check connection (or the listener's own connection inside the thread immediately after subscribe). Inside the thread, prefer the listener's own pubsub connection: after `subscribe`, read back confirmation via the subscribe reply or a `pubsub_numsub` on the dedicated `conn`.
- For the periodic probe, reuse the worker's existing async task infrastructure in `worker/__main__.py` (where `notify_task` is created at line 552) — add a small supervisor that, on each tick, checks `notify_task.done()` and `NUMSUB`. Restarting = cancel the old task (its `finally` cleans up the subscription) and `create_task(_session_notify_listener())` again, reusing the existing `_notify_task_done` callback pattern.
- Preserve the #824 connection setup exactly (dedicated `socket_timeout=None` connection). The verification must not introduce a `socket_timeout=5` path that re-opens that regression.
- Keep all failure handling fail-silent-but-logged: a NUMSUB probe error must never crash the worker; it logs WARNING and relies on the next tick / the 300 s health backstop.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The new NUMSUB verification and periodic probe must wrap Redis calls in try/except that logs (WARNING) and continues — assert via a test that injects a raising `pubsub_numsub` and verifies the worker does not crash and a warning is logged.
- [ ] Existing `except Exception` blocks in `_session_notify_listener` (thread-error path, line ~851) are unchanged; add a test asserting the restart-after-error path still fires.

### Empty/Invalid Input Handling
- [ ] `PUBSUB NUMSUB` returning `0` (or an empty/malformed reply) must be treated as "not subscribed" and trigger re-subscribe — test with a stubbed connection reporting 0.
- [ ] A `None`/missing channel reply must not raise — test the defensive parse.

### Error State Rendering
- [ ] Not user-visible. Operator-visible signal: a WARNING log line on subscribe-verify failure and on self-heal restart. Test asserts those log lines are emitted (caplog).

## Test Impact

- [ ] `tests/integration/test_session_notify.py::TestNotifyListenerSocketTimeout::test_notify_listener_uses_no_socket_timeout` — UPDATE: extend (do not break) to also assert the post-subscribe NUMSUB verification runs on the same `socket_timeout=None` connection; the #824 invariant must still hold.
- [ ] `tests/unit/test_agent_session_queue_async.py` — UPDATE: add cases for the NUMSUB self-check (subscribe→NUMSUB≥1 happy path; NUMSUB==0 → re-subscribe; raising NUMSUB → logged, no crash). Existing tests in this file are not expected to change behavior.
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

### Risk 2: Restarting the notify task drops in-flight notifications
**Impact:** During a self-heal restart, a publish in the gap is lost.
**Mitigation:** Acceptable — the 300 s health backstop catches anything missed in the restart window, and restarts are rare (only on detected NUMSUB==0). The restart re-subscribes within one tick.

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

### Race 2: periodic probe vs. legitimate restart in progress
**Location:** the new probe in `worker/__main__.py`.
**Trigger:** the probe samples NUMSUB while a self-heal restart is mid-flight (old task torn down, new subscribe not yet acknowledged).
**Data prerequisite:** a single in-flight restart should not trigger a second concurrent restart.
**State prerequisite:** restart-in-progress must be observable to the probe.
**Mitigation:** guard restarts with a simple "restart in progress" flag (or check `notify_task` identity/`done()` state) so the probe skips a tick while a restart it initiated is settling.

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
- [ ] `_listen_in_thread` verifies `NUMSUB >= 1` after subscribe and re-subscribes (via the existing teardown + 5 s backoff) when it reports 0, emitting a WARNING with the observed count.
- [ ] A periodic liveness probe restarts the notify task when it is alive-but-`NUMSUB==0`, without restarting the worker process.
- [ ] The #824 invariant holds: the listener's `listen()` connection still uses `socket_timeout=None` (asserted by the updated integration test).
- [ ] New unit tests cover: NUMSUB≥1 happy path, NUMSUB==0 → re-subscribe, raising NUMSUB → logged + no crash.
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
- After `pubsub.subscribe("valor:sessions:new")` in `_listen_in_thread`, verify `NUMSUB >= 1` (consume the subscribe ack or bounded-retry NUMSUB on the dedicated connection); on 0, log WARNING and fall through the existing `finally` teardown so the outer loop re-subscribes after 5 s.
- Add a periodic liveness probe (in `worker/__main__.py` near the `notify_task` creation, ~line 552) that restarts the notify task when alive-but-`NUMSUB==0`, guarded against concurrent restarts.
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
| #824 invariant preserved | `grep -c 'socket_timeout=None' agent/agent_session_queue.py` | output > 0 |
| Notify tests pass | `pytest tests/integration/test_session_notify.py tests/unit/test_agent_session_queue_async.py -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py worker/__main__.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **The "indefinitely" symptom.** The 300 s `_agent_session_health_check` backstop re-scans pending sessions and starts/nudges workers, so a stranded pending session should recover within ~5–10 min, not indefinitely. Was the observed indefinite hang (session 166) actually a *wedged-but-alive worker* (which the nudge path would keep `continue`-ing past) rather than a dead notify listener? If so, do you want a separate investigation issue for the wedge path, or is the notify-listener hardening sufficient for now?
2. **Scope confirmation.** Given the worker already runs standalone at runtime (the env-var change is observability-only), is the notify-listener self-heal the intended core of this work — or did the report expect a behavior change from the env var itself?
3. **Periodic probe placement.** Prefer folding the NUMSUB liveness check into the existing 300 s health loop (simpler, slower recovery) or a dedicated ~60 s task (faster recovery, one more task)? Default in this plan: a dedicated ~60 s probe.
