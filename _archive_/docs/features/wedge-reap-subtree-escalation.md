# Wedge-Reap Subtree Escalation + Durable Boot Kill-List

**Issue:** #2146 · **Status:** Shipped · **Lineage:** extends #1938 (teardown reap
confirms group death) and #1271 (boot-time cross-process orphan reaper).

## Problem

When the session-health loop wedge-kills a timed-out tool call, the session
runner reaps the turn's `claude -p` process **group** via `killpg(pgid,
SIGKILL)` in `HeadlessSessionRunner._reap_turn_group` (issue #1938). That
primitive has a blind spot: a tool subprocess that calls `setsid` — for example
`scripts/pytest-clean.sh` or a `pytest -n auto` xdist run — moves into its **own**
process group and is no longer a member of the harness group. `killpg` of the
harness `pgid` never touches it.

Observed live 2026-07-17 14:09:17:

```
[runner] reap SIGKILL failed pgid=61593: [Errno 1] Operation not permitted
```

The "killed" tree survived as a full integration test suite in a **different**
process group (`pgid 64644`). It ran unattended against the live machine for 25+
minutes — restarting the production worker with SIGTERMs and publishing fixture
session-notify events onto the live `valor:sessions:new` channel — until manually
killed. Two gaps combined:

1. **`killpg` misses `setsid` children.** Group-scoped killing cannot reach a
   subtree that escaped the group.
2. **A reap that cannot confirm death was logged and forgotten.** `_reap_turn_group`
   returned `(False, pgid)` and `_record_reap_failed` emitted a
   `runner_reap_failed` marker (used only to skip worktree deletion). No per-PID
   escalation, no verify, no durable follow-up. The boot-time orphan reaper
   (#1271) only recognizes `claude`/MCP-named processes, so a bash/pytest subtree
   was invisible to it.

## Design

### 1. Pre-kill descendant snapshot (`_default_enum_subtree`, the load-bearing fix)

Before any kill, while the harness pid is still alive, the runner snapshots the
descendant tree:

```python
psutil.Process(handle.pid).children(recursive=True)  # -> [(pid, create_time), …]
```

`setsid` changes a process's **pgid** but preserves its **ppid**, so a
parentage walk reaches the escaped subtree that `killpg` cannot. The snapshot
**must** be taken pre-kill: once the harness dies, its children reparent to
`launchd` (ppid==1) and a post-hoc walk from `handle.pid` returns nothing. The
enumeration is best-effort and fail-silent (`[]` if psutil is missing, the pid is
already gone, or access is denied). It is injected via the `enum_subtree_fn`
seam for unit testing.

### 2. Failure-gated per-PID escalation (`_escalate_subtree`)

`_reap_turn_group` keeps the #1938 happy path unchanged: `killpg(pgid, SIGKILL)`
then a bounded synchronous confirm poll. On the happy path (group killed and
confirmed dead) it returns immediately — **no per-PID sweep, no persistence**.

Escalation fires **only on EPERM or unconfirmed group death**. It iterates the
pre-kill snapshot, issues a per-PID `SIGKILL` to each still-live PID (this is how
a `setsid` escapee is reached), then runs a bounded verify. Any PID still alive
after the verify is returned as a `(pid, create_time, pgid)` **survivor**.

**Scope note (intentional):** the sweep is failure-gated by design — a `setsid`
escapee whose harness group nonetheless SIGKILLs cleanly (no EPERM, group
confirms dead) is *not* swept. The plan's cost trade-off is that the snapshot is
always taken (one cheap `children()` call) but the sweep is reserved for the
trouble path. The 2026-07-17 incident carried EPERM, so it is covered. If the
gap ever needs closing, the happy-path gate in `_reap_turn_group` can be widened
to check the snapshot for survivors before returning.

**Cancellation-proof (#1938 invariant preserved):** there is no `await` anywhere
in `_reap_turn_group` or `_escalate_subtree` — only `time.sleep` / `time.monotonic`
and the injected sync seams (`_killpg`, `_kill`, `_pid_alive`, `_enum_subtree`).
The recovery path double-cancels this coroutine, and a re-delivered
`CancelledError` cannot abort an uninterruptible synchronous SIGKILL sweep.

### 3. Durable boot kill-list (`agent/reap_killlist.py`)

Survivors are persisted to a Redis hash `valor:reap:killlist`
(`str(pid) -> json({pid, create_time, pgid, session_ref, ts})`). This is worker
infrastructure state, **not** a Popoto-managed model key, so it uses
`POPOTO_REDIS_DB` directly — the same precedent as `worker:registered_pid:*` and
the heartbeat keys, and outside the raw-Redis validator's scope (which guards
`.delete`/`.srem`/`.sadd`/`.zrem`/`.zadd` on Popoto model keys only).

- `add(entries)` — best-effort persist of survivors; fail-silent (a reap must
  never crash on persistence). A 24h TTL bounds accumulation on a machine that
  never reboots.
- `drain_and_kill()` — the consumer. For each stored entry it reconstructs the
  live process's `create_time` and **guards against PID recycle**: `None` (gone)
  → no kill; mismatch (recycled to an unrelated process) → skip; match → SIGKILL.
  Every entry is then removed unconditionally (one-shot drain). Returns the count
  killed; fail-silent per-entry and overall.

### 4. Boot + hourly drain (`_reap_orphan_session_processes` Step 0)

The cross-process orphan reaper (#1271) gains a first pass that calls
`reap_killlist.drain_and_kill()` and folds the count into its return. Because
that reaper runs both at worker startup (Step 4) **and** on the hourly
`agent-session-cleanup` reflection, the drain is **idempotent and safe on every
invocation** — a survivor left by a worker restart *without a machine reboot* (the
exact observed incident) is cleaned at the next hourly pass, not only at the next
full boot. The drain is **recorded-PID-only** and `create_time`-guarded, so it
never widens the reaper's claude/MCP name-net.

### Two distinct reap-failure consumers

Do not conflate them:

| State | Writer | Reader | Purpose |
|-------|--------|--------|---------|
| `runner_reap_failed` session event (with additive `survivor_pids`) | `_record_reap_failed` | `session_executor.py` | SKIP worktree deletion under a possibly-live child (#1938) |
| `valor:reap:killlist` Redis key | `reap_killlist.add` | `_reap_orphan_session_processes` → `drain_and_kill` | Actually kill survivors at boot/hourly (#2146) |

The `survivor_pids` marker field is additive observability only; the kill-list is
the authoritative drain source. If the marker payload were dropped, correctness
would be unaffected.

## EPERM Root-Cause Postmortem (AC3)

`killpg(pgid, SIGKILL)` raising `EPERM` (errno 1) means the kernel refused
delivery to at least one member of the target group. In order of likelihood for
the 2026-07-17 incident:

1. **Recycled / foreign pgid leader (most likely).** The harness group leader
   (`claude -p`, pid==pgid 61593) exited when the wedge-kill first hit it; macOS
   later recycled the pgid *number* to an unrelated process in a different
   context, so a follow-up `killpg(61593)` targeted a group no longer ours →
   EPERM. Consistent with the survivor carrying a **different** pgid (64644): the
   dangerous child had already `setsid`'d away and was never in 61593.
2. **Different-euid member.** A test shelling out under `sudo` (or any member with
   a differing real/effective UID) makes the whole `killpg` fail with EPERM even
   for same-uid members.
3. **Zombie / reparenting-member race.** A member mid-reparent to `launchd`
   during the kill.

**Conclusion:** regardless of which cause fired, group-scoped killing is the wrong
primitive for a subtree that can `setsid`. Parentage-based per-PID escalation with
`create_time` recycle guards is correct under all three branches. No live EPERM
repro is required — the survivor tree was manually cleaned on 2026-07-17, and
manufacturing a cross-euid or recycled-pgid group in CI is flaky and
root-privilege-adjacent (a deliberate No-Go). The escalation is exercised via
injected fakes.

## Files

| File | Role |
|------|------|
| `agent/reap_killlist.py` | Durable kill-list: `add()` / `drain_and_kill()` |
| `agent/session_runner/runner.py` | `_default_enum_subtree`, `_reap_turn_group` (snapshot + escalation), `_escalate_subtree`, `_record_reap_failed` persistence, `enum_subtree_fn` seam |
| `agent/session_health.py` | `_reap_orphan_session_processes` Step 0 drain |

## Tests

- `tests/unit/test_reap_killlist.py` — add/persist + TTL; drain kills on
  `create_time` match; **skips + discards on recycle mismatch** (the
  recorded-PIDs-only guard); skips dead PID; Redis-unavailable fail-silence.
- `tests/unit/session_runner/test_runner_preempt.py` —
  `test_reap_escalates_per_pid_on_eperm_and_persists_survivor` (EPERM → per-PID
  sweep over exactly the snapshot, only the surviving setsid child returned +
  persisted) and `test_reap_happy_path_skips_escalation_and_snapshot_sweep`
  (regression: clean group kill → no sweep, no persist, snapshot taken ≤once).

## Constraints honored

- **Recorded-PIDs-only:** kill logic targets only the snapshotted subtree or the
  persisted kill-list — no name-pattern sweeps beyond the existing #1271 rules.
- **PID-recycle safe:** every boot-drain kill is `create_time`-guarded.
- **Cancellation-proof:** no `await` in the reap or escalation path (#1938).
- **Fail-silent:** persistence and drain never raise into reap teardown or boot.
