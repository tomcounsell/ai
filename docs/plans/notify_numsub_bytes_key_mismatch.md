---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-06-29
tracking: https://github.com/tomcounsell/ai/issues/1811
last_comment_id:
---

# Notify-Listener NUMSUB Self-Check Livelock (bytes-vs-str key mismatch)

## Problem

The standalone worker (`python -m worker`) learns about newly-enqueued sessions
two ways: a fast **notify** path (Redis Pub/Sub on `valor:sessions:new`) and a
slower **polling** fallback. PR #1809 (closing #1804) added a *subscribe-time
NUMSUB self-check* to the notify listener: right after `pubsub.subscribe()`, it
issues `PUBSUB NUMSUB valor:sessions:new` and, if the count isn't `>= 1`, it
concludes the subscribe was a no-op, tears the listener down, and re-subscribes
after a 5 s backoff.

That self-check is defective: it mis-parses the `PUBSUB NUMSUB` reply because of
a **str-vs-bytes key mismatch**, so it always reads `0` subscribers and the
listener re-subscribes forever.

**Current behavior** (observed on a healthy single worker, PID 76026, 2026-06-29):

```
05:05:30 INFO    Session notify listener subscribed to valor:sessions:new
05:05:30 WARNING Session notify: NUMSUB check reports 0 subscribers after subscribe (attempt 3/3) — ... falling through teardown to re-subscribe
05:05:35 INFO    Session notify listener subscribed to valor:sessions:new
05:05:36 WARNING Session notify: NUMSUB check reports 0 subscribers after subscribe (attempt 3/3) — ...
```

This cycle repeated 52+ times, once every ~5 s, without converging. The listener
never reaches `pubsub.listen()` (line 880); it always hits the early `return`
(line 879) first.

**Root cause (confirmed in code + empirically):** the listener's dedicated
connection is built with `decode_responses=kw.get("decode_responses", False)`
(`agent/agent_session_queue.py:831`), and the POPOTO pool default is `False`. So
`pubsub_numsub("valor:sessions:new")` returns **bytes**-keyed results — the live
reply is a list of tuples `[(b'valor:sessions:new', 1)]`. The parse at
`agent/agent_session_queue.py:850-856` compares against the **str** key
`"valor:sessions:new"` in both the dict branch (`.get("valor:sessions:new", 0)`)
and the list branch (`ch == "valor:sessions:new"`), so both default to `0`
whenever keys are bytes. `_numsub_ok` stays `False`, the listener tears down, and
the outer `while True` re-subscribes — forever.

**Desired outcome:** with a live subscription present, the self-check computes
`count >= 1`, the listener proceeds into `pubsub.listen()`, the notify fast-path
delivers session pickups, and the recurring WARNING / subscribe churn stops. A
regression test asserts the parse yields the correct count for bytes-keyed
replies in both list-of-tuples and dict shapes so this cannot silently regress.

## Freshness Check

**Baseline commit:** `7f8572ee` (HEAD at plan time)
**Issue filed at:** 2026-06-29T06:08:15Z
**Disposition:** Unchanged

**File:line references re-verified against `main`:**
- `agent/agent_session_queue.py:831` — `decode_responses=kw.get("decode_responses", False)` — still holds, exact match.
- `agent/agent_session_queue.py:850-856` — dict branch `.get("valor:sessions:new", 0)` and list branch `ch == "valor:sessions:new"`, both defaulting to `0` — still holds, exact match.
- `agent/agent_session_queue.py:870-879` — `if not _numsub_ok:` WARNING + early `return` before `pubsub.listen()` at line 880 — still holds, exact match.

**Cited sibling issues/PRs re-checked:**
- #1804 — closed 2026-06-26 by PR #1809 (the self-check that introduced this bug).
- PR #1809 — merged 2026-06-26; it is the source of the defective parse.
- #1808 / #1767 (U-state wedged worker) — distinct concern (kernel-syscall hang), not related to this logic bug.

**Commits on main since issue was filed (touching `agent/agent_session_queue.py`):** none.

**Active plans in `docs/plans/` overlapping this area:** none. (`worker_watchdog_ustate_recovery.md` concerns the U-state wedge, a different problem.)

**Notes:** Issue is ~4 minutes old at plan time; verified anyway. No drift.

## Prior Art

- **PR #1809** ("fix(worker): notify listener NUMSUB self-check + VALOR_WORKER_MODE in plist", merged 2026-06-26): added the very self-check this issue fixes. Its intent — guard against a subscribe that silently fails to register — is sound; its parse is wrong for the bytes-keyed connection. This plan fixes the parse without removing the guard.
- **#1804** (closed by #1809): the original "notify-listener miss strands sessions" report that motivated the self-check.
- **#1808 / #1767** (U-state wedged worker): explicitly distinct — a worker hung in an uninterruptible kernel syscall, not a parse bug on a healthy worker. No overlap.

No prior attempts to fix *this* bytes-vs-str mismatch exist; this is the first fix.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Was Incomplete |
|-----------|-------------|------------------------|
| PR #1809 | Added a NUMSUB self-check after `subscribe()` to verify the subscription registered before entering `listen()`. | Parsed the `PUBSUB NUMSUB` reply assuming **str** keys, but the listener's connection uses `decode_responses=False` (bytes keys). The check therefore always reads `0` and the guard it added livelocks on a healthy worker. The guard logic was correct; only the reply-key encoding was mishandled. |

**Root cause pattern:** the self-check and the connection it queries were written with mismatched assumptions about `decode_responses`. The connection inherits POPOTO's `False`; the parse assumed `True`. The fix makes the parse encoding-agnostic so the two cannot drift.

## Data Flow

1. **Listener startup** (`_session_notify_listener` → `_listen_in_thread`, `agent/agent_session_queue.py:~820`): builds a dedicated `redis.Redis` conn from the POPOTO pool kwargs with `decode_responses=False`.
2. **Subscribe** (line 836): `pubsub.subscribe("valor:sessions:new")`.
3. **Self-check** (lines 845-869): up to 3× `conn.pubsub_numsub("valor:sessions:new")`. With `decode_responses=False`, the reply is `[(b'valor:sessions:new', 1)]` (bytes-keyed list of tuples) or `{b'valor:sessions:new': 1}` (bytes-keyed dict on some redis-py versions).
4. **Parse** (lines 850-856) — **bug here**: compares bytes channel against str literal → `_count = 0`.
5. **Decision** (lines 857-879): `_count >= 1` would set `_numsub_ok=True` and `break`; instead it stays `False`, logs WARNING, and `return`s (teardown).
6. **Intended path** (line 880+): `pubsub.listen()` consumes `message` frames; `message["data"]` (bytes under `decode_responses=False`) is fed to `json.loads` (which accepts bytes); `message["type"]` is a redis-py-normalized `str` regardless of `decode_responses`. The surgical fix does not touch this path.

## Appetite

**Size:** Small

**Team:** Solo dev, 1 review round

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (code review of the parse fix + regression tests)

## Prerequisites

No prerequisites — this work has no external dependencies. (Redis is already
required by the test suite; the regression tests mock `pubsub_numsub` and need no
live broker.)

## Solution

### Key Elements

- **Encoding-agnostic NUMSUB parse**: normalize the channel key from each
  `pubsub_numsub` reply entry to `str` before comparing, so the count is read
  correctly whether redis-py returns `bytes` or `str` keys, and whether the reply
  is a `dict` or a list of `(channel, count)` tuples.
- **Regression tests**: assert the parse yields the correct count for
  **bytes-keyed** replies in both list-of-tuples and dict shapes, plus the
  existing str-keyed shapes, so neither encoding can silently regress.

### Technical Approach

Apply the **bytes-aware parse** (the surgical option from the issue), not
`decode_responses=True`. Rationale: switching the connection to `decode_responses=True`
would broaden the blast radius to every reply on that connection — including the
`pubsub.listen()` message payloads — for no functional gain, since the parse fix
is fully contained.

Extract a tiny module-level helper that both the dict and list branches use, so
the comparison happens in exactly one place:

```python
def _numsub_count(numsub_result, channel: str) -> int:
    """Read the subscriber count for `channel` from a pubsub_numsub reply,
    tolerating bytes- or str-keyed dict and list-of-tuples shapes."""
    def _key(ch):
        return ch.decode() if isinstance(ch, (bytes, bytearray)) else ch
    items = numsub_result.items() if isinstance(numsub_result, dict) else numsub_result
    for ch, count in items:
        if _key(ch) == channel:
            return int(count)
    return 0
```

Then lines 850-856 collapse to:

```python
_count = _numsub_count(_numsub_result, "valor:sessions:new")
```

- The helper is module-level (not a closure) so the regression test can import
  and call it directly with synthetic replies — decoupling the assertion from the
  thread/asyncio machinery.
- `int(count)` guards against count arriving as bytes/str on exotic clients.
- No change to the connection, the subscribe call, the retry loop, the WARNING
  text, or the `listen()` consumption.

### Flow

Worker start → notify listener thread subscribes → `_numsub_count(reply, ch)` reads
`1` from the bytes-keyed reply → `_numsub_ok=True` → `pubsub.listen()` blocks for
notifications → newly-enqueued session published to `valor:sessions:new` → listener
delivers `(worker_key, is_pk)` to the async queue → session picked up via notify
(not only polling).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The NUMSUB retry loop already has an `except Exception` (lines 860-867) that logs a WARNING and `return`s. The fix does not change it; the existing `test_numsub_raises_no_crash_and_logs_warning` covers it. No new exception handler is introduced.
- [ ] The new `_numsub_count` helper performs no I/O and raises only on genuinely malformed input; it is exercised directly by unit tests with both valid and edge-shaped inputs.

### Empty/Invalid Input Handling
- [ ] `_numsub_count` returns `0` for an empty dict `{}` and an empty list `[]` (channel absent) — add explicit test cases.
- [ ] A reply for a *different* channel (e.g. `[(b'other', 3)]`) returns `0` — add a test case.

### Error State Rendering
- [ ] No user-visible output. The failure surface is a worker-log WARNING; the existing `test_numsub_zero_skips_listen_and_logs_warning` asserts the WARNING path still fires when the count is genuinely `0`.

## Test Impact

- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck` (`_make_mocks`, ~lines 292-399) — UPDATE: `_make_mocks` currently returns a **str**-keyed dict (`{"valor:sessions:new": numsub_return}`), which masked this bug. Update it (or add a parameter) so the "ok" cases exercise the **bytes**-keyed list-of-tuples shape that production actually returns, ensuring `test_numsub_ok_proceeds_to_listen` would have caught the regression.
- [ ] `tests/unit/test_agent_session_queue_async.py::TestNumsubSelfCheck` — ADD direct unit tests for the new `_numsub_count` helper covering: bytes-keyed list-of-tuples, bytes-keyed dict, str-keyed list, str-keyed dict, empty reply, and wrong-channel reply.
- [ ] `tests/integration/test_session_notify.py::test_notify_listener_uses_no_socket_timeout` (line 145) — UPDATE: `mock_conn.pubsub_numsub.return_value = {"valor:sessions:new": 1}` uses a str key. Change to the bytes-keyed shape `[(b"valor:sessions:new", 1)]` so the integration mock matches `decode_responses=False` reality and continues to drive the listener into `listen()`.

No tests are deleted — the existing self-check tests remain valid behavior contracts; they are tightened to use realistic bytes-keyed inputs.

## Rabbit Holes

- **Do NOT flip the connection to `decode_responses=True`.** It changes the encoding of every `pubsub.listen()` payload on that connection for no benefit and widens the blast radius. The parse fix is self-contained.
- **Do NOT refactor or "harden" the broader notify/poll architecture.** The polling fallback, the 5 s backoff, and the retry-count are out of scope; this is a one-line parse correctness fix plus tests.
- **Do NOT chase the original psyoptimal stall incident.** It was a *contributing factor* mention; this plan restores the fast-path, it does not re-litigate that incident.

## Risks

### Risk 1: redis-py returns a shape the helper doesn't anticipate
**Impact:** `_numsub_count` reads `0`, re-introducing the livelock on some redis-py version.
**Mitigation:** The helper handles both documented shapes (dict and list-of-tuples) and both key encodings (bytes/str) via a single normalization. Unit tests cover all four combinations plus empty/wrong-channel. `int(count)` tolerates non-int counts.

### Risk 2: A real subscribe failure is now masked
**Impact:** If a subscribe genuinely fails to register, the corrected check could (in theory) read a stale non-zero count from another connection's subscription.
**Mitigation:** `PUBSUB NUMSUB` is server-global and counts the live subscription this thread just created; with the parse fixed it reads the true count (`>= 1` when this subscribe registered). The guard's purpose — detect a no-op subscribe (count `0`) — still works, because a genuine no-op still yields `0`. Net behavior is strictly closer to intent.

## Race Conditions

No new race conditions introduced. The NUMSUB read already happens on the
listener thread's own `socket_timeout=None` connection (per the existing comment
at lines 842-843), with no cross-thread sharing. `_numsub_count` is a pure
function over the reply value and touches no shared state. The fix is a parse
correction within the existing single-threaded read.

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The fix, the
helper extraction, and the regression/integration test updates are all completed
within this plan.

## Update System

No update system changes required — this is a pure in-process logic fix to
`agent/agent_session_queue.py`. No new dependencies, config, or migration steps;
no Popoto model changes. The corrected worker behavior propagates with the normal
code update + worker restart (`./scripts/valor-service.sh worker-restart`).

## Agent Integration

No agent integration required — this is a worker-internal fix to the session
notify listener. No CLI entry point, MCP surface, or bridge import changes. The
agent reaches sessions through the existing enqueue/notify/poll machinery, which
is unchanged in interface; only the internal NUMSUB parse is corrected.

## Documentation

No new feature documentation needed — this is a bug fix to existing internal
behavior, not a new capability.

### Inline Documentation
- [ ] Add a one-line comment at the `_numsub_count` helper noting it tolerates
      bytes/str keys because the listener connection uses `decode_responses=False`
      (POPOTO pool default), referencing #1811 so the rationale survives future edits.
- [ ] Update the existing NUMSUB-check comment block (lines 838-843) only if the
      line references shift; keep it accurate.

(If a reviewer judges that `docs/features/bridge-worker-architecture.md` should
note the notify fast-path correctness fix, add a one-line entry — otherwise no
docs change is warranted for a parse bug.)

## Success Criteria

- [ ] With a live subscription present, the NUMSUB self-check computes `count >= 1` and the listener proceeds into `pubsub.listen()` (no teardown).
- [ ] No recurring `NUMSUB check reports 0 subscribers` WARNINGs on a healthy worker (verify via `logs/worker.log` after a worker restart).
- [ ] A newly-enqueued session is picked up via the notify fast-path (log line `Received session notify: worker_key=... session_id=...`), not only via polling.
- [ ] Regression tests cover bytes-keyed `pubsub_numsub` replies in both list-of-tuples and dict shapes (and the str-keyed shapes), all green.
- [ ] Tests pass (`/do-test`).
- [ ] Lint and format clean.

## Team Orchestration

When this plan is executed, the lead agent orchestrates. The lead does not build directly.

### Team Members

- **Builder (notify-parse)**
  - Name: notify-parse-builder
  - Role: Extract `_numsub_count` helper, rewire lines 850-856, update unit + integration tests.
  - Agent Type: builder
  - Resume: true

- **Validator (notify-parse)**
  - Name: notify-parse-validator
  - Role: Verify the parse fix, run the targeted tests, confirm no recurring WARNING path on the corrected code.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement bytes-aware NUMSUB parse
- **Task ID**: build-numsub-parse
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_queue_async.py, tests/integration/test_session_notify.py
- **Informed By**: Recon (bytes-keyed reply confirmed empirically as `[(b'valor:sessions:new', 1)]`)
- **Assigned To**: notify-parse-builder
- **Agent Type**: builder
- **Parallel**: false
- Add module-level helper `_numsub_count(numsub_result, channel)` in `agent/agent_session_queue.py` that normalizes bytes/str channel keys and handles dict + list-of-tuples shapes, returning `int(count)` or `0`.
- Replace the parse at lines 850-856 with `_count = _numsub_count(_numsub_result, "valor:sessions:new")`.
- Add an inline comment referencing #1811 and the `decode_responses=False` rationale.

### 2. Add/Update regression tests
- **Task ID**: build-numsub-tests
- **Depends On**: build-numsub-parse
- **Validates**: tests/unit/test_agent_session_queue_async.py, tests/integration/test_session_notify.py
- **Assigned To**: notify-parse-builder
- **Agent Type**: builder
- **Parallel**: false
- Add direct unit tests for `_numsub_count`: bytes-keyed list-of-tuples → correct count; bytes-keyed dict → correct count; str-keyed list and dict → correct count; empty `[]`/`{}` → 0; wrong-channel reply → 0.
- Update `TestNumsubSelfCheck._make_mocks` so the "ok" path drives a **bytes-keyed** reply (`[(b"valor:sessions:new", N)]`), confirming `test_numsub_ok_proceeds_to_listen` now exercises production reality.
- Update `tests/integration/test_session_notify.py:145` to `mock_conn.pubsub_numsub.return_value = [(b"valor:sessions:new", 1)]`.

### 3. Validation
- **Task ID**: validate-numsub
- **Depends On**: build-numsub-parse, build-numsub-tests
- **Assigned To**: notify-parse-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_agent_session_queue_async.py tests/integration/test_session_notify.py -q`.
- Confirm `git grep` shows the str-key literal comparison at lines 850-856 is gone (replaced by the helper call).
- Run `python -m ruff check .` and `python -m ruff format --check .`.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Notify-listener tests pass | `pytest tests/unit/test_agent_session_queue_async.py tests/integration/test_session_notify.py -q` | exit code 0 |
| Helper exists | `grep -c "_numsub_count" agent/agent_session_queue.py` | output > 0 |
| Old str-key parse removed | `grep -c 'ch == "valor:sessions:new"' agent/agent_session_queue.py` | match count == 0 |
| No decode_responses flip | `grep -c "decode_responses=True" agent/agent_session_queue.py` | match count == 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

None. The root cause is confirmed in code and empirically; the fix is surgical
and self-contained. Ready for critique.
