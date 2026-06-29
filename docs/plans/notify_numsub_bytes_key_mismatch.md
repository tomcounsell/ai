---
status: Complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-29
tracking: https://github.com/tomcounsell/ai/issues/1811
last_comment_id:
---

# Notify-listener NUMSUB self-check livelocks: bytes-vs-str key mismatch

## Problem

The standalone worker discovers newly-enqueued sessions via a fast Redis Pub/Sub
**notify** path (channel `valor:sessions:new`) and a slower **polling** fallback.
PR #1809 (closing #1804) added a subscribe-time **NUMSUB self-check** to the notify
listener: right after `pubsub.subscribe()`, it issues `PUBSUB NUMSUB valor:sessions:new`
and, if the count isn't `>= 1`, tears the listener down and re-subscribes after a 5 s
backoff.

That self-check is defective. It mis-parses the `PUBSUB NUMSUB` reply because of a
**str-vs-bytes key mismatch**, so it always reads `0` subscribers — even when the
subscription registered correctly — and the listener tears itself down forever.

**Current behavior** (observed on a healthy single worker, PID 76026, 2026-06-29):

```
05:05:30 INFO    Session notify listener subscribed to valor:sessions:new
05:05:30 WARNING Session notify: NUMSUB check reports 0 subscribers after subscribe (attempt 3/3) — ...
05:05:35 INFO    Session notify listener subscribed to valor:sessions:new
05:05:36 WARNING Session notify: NUMSUB check reports 0 subscribers after subscribe (attempt 3/3) — ...
```

The cycle repeated 52+ times, once every ~5 s, and does not converge. The listener
never reaches `pubsub.listen()` (line 880); it always hits the early `return` at line 879.

**Impact:**
1. The notify fast-path is effectively dead — sessions are only picked up by the slower
   polling fallback, adding latency to every session pickup.
2. Log spam — a `WARNING` every 5 s, indefinitely.
3. Continuous subscribe/teardown churn against Redis.

This is distinct from the recently-closed wedge work (#1808 detection harness, #1767
U-state recovery), which concern a worker hung in an uninterruptible kernel syscall.
This is a pure logic bug in the #1809 self-check, firing on a fully healthy worker.

**Desired outcome:**
With a live subscription present, the self-check computes `count >= 1`, the listener
proceeds into `pubsub.listen()`, no recurring WARNING fires, and the notify fast-path
delivers session pickups.

## Freshness Check

**Baseline commit:** `7f8572ee3bf1936536ba07bccde6912f35536d39`
**Issue filed at:** 2026-06-29T06:08:15Z
**Disposition:** Unchanged

**File:line references re-verified** (against `agent/agent_session_queue.py` at the baseline commit):
- `agent/agent_session_queue.py:831` — `decode_responses=kw.get("decode_responses", False)` — **still holds.** POPOTO pool default is `False`, so `pubsub_numsub` replies are bytes-keyed.
- `agent/agent_session_queue.py:850-856` — both parse branches compare against the str key `"valor:sessions:new"` — **still holds** verbatim (dict branch `_numsub_result.get("valor:sessions:new", 0)`; list branch `next((c for ch, c in _numsub_result if ch == "valor:sessions:new"), 0)`).
- `agent/agent_session_queue.py:870-879` — `_numsub_ok` stays `False`, WARNING fires, early `return` before `pubsub.listen()` at line 880 — **still holds.**

**Cited sibling issues/PRs re-checked:**
- #1809 — MERGED 2026-06-26T16:34:55Z ("fix(worker): notify listener NUMSUB self-check + VALOR_WORKER_MODE in plist (#1804)"). This is the PR that introduced the bug.
- #1808, #1767 — referenced only to disambiguate scope; unrelated U-state wedge work, no overlap.

**Commits on main since issue was filed (touching `agent/agent_session_queue.py`):** None.

**Active plans in `docs/plans/` overlapping this area:** None. The recent wedged-worker plans (#1808) concern U-state recovery, a separate concern.

**Notes:** No drift. All cited line numbers are exact at the baseline commit. The bug is confirmed present on current main.

## Prior Art

- **PR #1809** (MERGED 2026-06-26): "fix(worker): notify listener NUMSUB self-check + VALOR_WORKER_MODE in plist (#1804)" — added the NUMSUB self-check that this issue fixes. The self-check logic was correct in intent (verify the subscribe registered) but the reply parsing assumed str keys while the connection yields bytes keys. See **Why Previous Fixes Failed** below.
- No other prior issues/PRs address the bytes-vs-str parsing of `pubsub_numsub`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #1809 | Added a subscribe-time NUMSUB self-check to confirm the Pub/Sub subscription registered before entering `pubsub.listen()`. | The self-check parses `pubsub_numsub()` output by matching the channel against the **str** key `"valor:sessions:new"`, but the listener's connection is built with `decode_responses=False` (POPOTO default), so the reply is **bytes**-keyed (`[(b'valor:sessions:new', 1)]`). The comparison `b'valor:sessions:new' == "valor:sessions:new"` is never `True`, so `_count` always defaults to `0`. The guard meant to protect against a failed subscribe instead fires on every healthy subscribe. |

**Root cause pattern:** The self-check's tests mocked `pubsub_numsub` with str keys (`{"valor:sessions:new": 1}`), matching the parsing code's assumption rather than the real `decode_responses=False` connection behavior. The test fixture masked the production bytes-keyed reply shape, so the defect shipped green.

## Data Flow

1. **Entry point:** `_session_notify_listener()` coroutine spawns `_listen_in_thread` on a background thread.
2. **Connection build** (`agent/agent_session_queue.py:825-834`): a dedicated `redis.Redis` connection is built inheriting `decode_responses=False` from the POPOTO pool kwargs → all replies on this connection are bytes.
3. **Subscribe** (line ~836): `pubsub.subscribe("valor:sessions:new")` registers the subscription server-side.
4. **NUMSUB self-check** (lines 844-879): `conn.pubsub_numsub("valor:sessions:new")` returns `[(b'valor:sessions:new', 1)]` (list-of-tuples on this redis-py) → parse compares against str key → `_count = 0` → `_numsub_ok = False` → WARNING + early `return`.
5. **Teardown + re-subscribe:** the `finally` teardown runs, the outer `while True` sleeps 5 s and re-subscribes → loop forever; **never reaches** step 6.
6. **Listen loop** (line 880, currently unreachable): `for message in pubsub.listen()` → `json.loads(message["data"])` → `loop.call_soon_threadsafe(notify_queue.put_nowait, ...)`.

The fix lives entirely at step 4. Steps 1-3 and 6 are correct and unchanged.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (root cause confirmed empirically; no scope ambiguity)
- Review rounds: 1 (standard SDLC critique + review)

## Prerequisites

No prerequisites — this is a self-contained logic fix in already-loaded code paths; no new external dependencies, secrets, or services.

## Solution

### Key Elements

- **Bytes-aware NUMSUB parse**: normalize the channel comparison so it matches whether the `pubsub_numsub` reply key is `bytes` or `str`, in both the dict and list-of-tuples reply shapes. This is the surgical fix that touches only lines 850-856.

### Technical Approach

Adopt the **bytes-aware parse** candidate from the issue (preferred over `decode_responses=True`, which would broaden the blast radius to every reply on the connection, including the `pubsub.listen()` message payloads).

Replace the two parse branches so the channel match accepts both key types. Concretely, compare each reply channel against the set `(b"valor:sessions:new", "valor:sessions:new")`, or decode bytes channels before comparing. Both the dict branch (`{channel: count}`, redis-py >= 4) and the list-of-tuples branch (older shape; the shape observed live) must be covered.

Confirmed non-issues that require **no** changes (per the issue's recon, re-verified):
- `message["type"]` in the listen loop is a redis-py-normalized `str` regardless of `decode_responses` — no change needed.
- `json.loads(message["data"])` at line ~884 accepts `bytes` — no change needed.

So the surgical parse fix needs no listen-loop changes; it is confined to the NUMSUB parsing block.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The NUMSUB block has a `try/except Exception` (lines ~864-871) that logs a WARNING ("NUMSUB check raised") and `return`s. This path is already covered by `test_numsub_raises_no_crash_and_logs_warning`; the fix does not change it, but the test must continue to pass.
- [ ] No new `except Exception: pass` blocks are introduced by this fix.

### Empty/Invalid Input Handling
- [ ] Document/verify behavior when `pubsub_numsub` returns an empty list `[]` or a dict without the channel key → `_count` correctly stays `0` and the existing teardown/re-subscribe path fires (this is the legitimate "subscribe failed" case the self-check was designed for). A regression test should cover the empty-reply case to confirm the fix does not mask genuine failures.

### Error State Rendering
- [ ] Not user-visible (background worker thread). The observable "error state" is the WARNING log; the fix's success is the **absence** of that WARNING on a healthy worker, asserted via `caplog` in the bytes-keyed regression test.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck._make_mocks` — UPDATE: the helper builds `pubsub_numsub.return_value` with **str** keys (`{"valor:sessions:new": numsub_return}`). Change it to return **bytes**-keyed shapes (matching the real `decode_responses=False` connection) so the existing cases exercise the real reply shape. Consider parametrizing the helper to emit both dict and list-of-tuples shapes.
- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck::test_numsub_ok_proceeds_to_listen` — UPDATE: with bytes-keyed mocks, this must still assert `pubsub.listen()` is reached (it will only pass after the fix; today it would fail with the bytes-keyed mock — the regression-proof of the bug).
- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck::test_numsub_zero_skips_listen_and_logs_warning` — UPDATE: feed a bytes-keyed empty/zero reply; assert teardown + WARNING still fire for a genuine zero-subscriber case.
- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck::test_numsub_raises_no_crash_and_logs_warning` — verify unchanged (exception path independent of key encoding); UPDATE only if the shared `_make_mocks` change affects it.
- [ ] `tests/integration/test_session_notify.py` (line ~145) — UPDATE: `mock_conn.pubsub_numsub.return_value = {"valor:sessions:new": 1}` uses a str key. Change to a bytes-keyed reply so the integration test exercises the real connection behavior and the listener proceeds.
- [ ] NEW regression cases in `tests/unit/test_agent_session_queue_async.py`: assert the parse computes `count == 1` for a bytes-keyed reply in **both** the list-of-tuples shape (`[(b"valor:sessions:new", 1)]`) and the dict shape (`{b"valor:sessions:new": 1}`), so this cannot silently regress.

## Rabbit Holes

- **`decode_responses=True` on the connection** — tempting as a "cleaner" fix, but it broadens blast radius to every reply on the listener connection (including `pubsub.listen()` message payloads and `json.loads` input). The issue explicitly weighs against it. Stay with the surgical parse fix.
- **Refactoring the whole notify-listener / retry loop** — out of scope. The retry/backoff structure is correct; only the parse is wrong.
- **"Fixing" `message["type"]` or `json.loads` for bytes** — confirmed non-issues. Do not touch the listen loop.

## Risks

### Risk 1: redis-py reply shape varies across versions
**Impact:** If the installed redis-py returns a shape the parse doesn't handle (e.g. dict vs list), the count could read 0 again.
**Mitigation:** The fix covers both dict and list-of-tuples shapes, each with bytes-aware key matching. Regression tests assert both shapes explicitly.

### Risk 2: Masking a genuine failed-subscribe
**Impact:** If the bytes-aware match is too permissive, the self-check could report `>=1` when the subscribe genuinely failed, defeating the #1804 guard.
**Mitigation:** The match is exact on channel name (just key-type-agnostic). The empty-reply regression test confirms `_count` stays `0` and the teardown path still fires for a real zero-subscriber case.

## Race Conditions

No new race conditions introduced. The NUMSUB read happens on the listener thread's own `socket_timeout=None` connection (same connection used for subscribe), with no cross-thread sharing of the reply. The existing retry loop (3 attempts, ~300 ms) handles the documented one-frame NUMSUB propagation lag; the fix does not alter timing, only the parse of the returned value.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The fix, the test updates, and the new regression cases all land together.

## Update System

No update system changes required — this is a pure logic fix in `agent/agent_session_queue.py`. No new dependencies, no config files, no migrations, no Popoto model changes. The fix takes effect on the next worker restart (`./scripts/valor-service.sh worker-restart`), which is part of the normal deploy.

## Agent Integration

No agent integration required — this is a worker-internal change to the session notify listener. No MCP surface, no `.mcp.json` change, no bridge import. The agent reaches sessions through the existing queue; this fix only restores the fast-path latency on the worker side.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-worker-architecture.md` (the notify/polling discovery section) if it documents the NUMSUB self-check behavior — add a note that the self-check is bytes-aware. If the self-check is not currently documented there, no change is needed and this checkbox is satisfied by confirming its absence.

### Inline Documentation
- [ ] Add a one-line comment at the parse site noting that `pubsub_numsub` keys are bytes under `decode_responses=False`, so the match must be key-type-agnostic (prevents a future regression to str-only matching).

## Success Criteria

- [ ] With a live subscription present, the NUMSUB self-check computes `count >= 1` and the listener proceeds into `pubsub.listen()` (no teardown).
- [ ] No recurring `NUMSUB check reports 0 subscribers` warnings on a healthy worker.
- [ ] The notify fast-path delivers session pickups (a newly-enqueued session is picked up via notify, not only polling).
- [ ] Regression tests cover bytes-keyed `pubsub_numsub` replies in both list-of-tuples and dict shapes.
- [ ] Existing unit/integration NUMSUB tests updated to bytes-keyed shapes and passing.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (notify-parse)**
  - Name: notify-parse-builder
  - Role: Apply the bytes-aware NUMSUB parse fix and update/add tests
  - Agent Type: builder
  - Resume: true

- **Validator (notify-parse)**
  - Name: notify-parse-validator
  - Role: Verify the fix, run the test suite, confirm no recurring WARNING
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply bytes-aware NUMSUB parse fix
- **Task ID**: build-numsub-parse
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_async.py, tests/integration/test_session_notify.py
- **Assigned To**: notify-parse-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `agent/agent_session_queue.py` lines 850-856: make both the dict branch and the list-of-tuples branch match the channel against both `bytes` and `str` forms of `"valor:sessions:new"` (e.g. compare `ch` against `(b"valor:sessions:new", "valor:sessions:new")`, or decode `ch` before comparing).
- Add a one-line inline comment noting `pubsub_numsub` keys are bytes under `decode_responses=False`.
- Do NOT switch the connection to `decode_responses=True`; do NOT touch the `pubsub.listen()` loop or `json.loads`.

### 2. Update existing tests to bytes-keyed shapes + add regression cases
- **Task ID**: build-numsub-tests
- **Depends On**: build-numsub-parse
- **Assigned To**: notify-parse-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck._make_mocks` to emit bytes-keyed `pubsub_numsub` replies; ensure `test_numsub_ok_proceeds_to_listen`, `test_numsub_zero_skips_listen_and_logs_warning`, and `test_numsub_raises_no_crash_and_logs_warning` pass against the fixed code.
- Add regression cases asserting `count == 1` for bytes-keyed replies in both list-of-tuples (`[(b"valor:sessions:new", 1)]`) and dict (`{b"valor:sessions:new": 1}`) shapes; add an empty-reply case asserting the genuine zero-subscriber teardown still fires.
- Update `tests/integration/test_session_notify.py` (line ~145) to a bytes-keyed `pubsub_numsub` return value.

### 3. Validation
- **Task ID**: validate-numsub
- **Depends On**: build-numsub-parse, build-numsub-tests
- **Assigned To**: notify-parse-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_queue_async.py tests/integration/test_session_notify.py -q` and confirm all pass.
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Confirm the success criteria are met and report pass/fail.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-numsub
- **Assigned To**: notify-parse-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-worker-architecture.md` notify section if it documents the self-check; otherwise confirm no doc change is needed.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| NUMSUB unit tests pass | `pytest tests/unit/test_agent_session_queue_async.py -q` | exit code 0 |
| Notify integration test passes | `pytest tests/integration/test_session_notify.py -q` | exit code 0 |
| Full suite passes | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No str-only NUMSUB match remains | `grep -n 'ch == "valor:sessions:new"' agent/agent_session_queue.py` | match count == 0 |
| Bytes-keyed channel handled | `grep -c "b\"valor:sessions:new\"\|b'valor:sessions:new'" agent/agent_session_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None. The root cause is confirmed empirically (live-Redis repro in the issue, re-verified
against the baseline commit), the fix approach is settled (bytes-aware parse over
`decode_responses=True`), and the test impact is fully enumerated. Ready for critique.
