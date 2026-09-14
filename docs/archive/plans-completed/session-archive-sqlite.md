---
status: docs_complete
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1825
last_comment_id: 4880726096
revision_applied: true
---

# Durable Secondary Store — Periodic AgentSession Export to SQLite

## Problem

`#1814` (merged as PR #1824) enabled Redis AOF (`appendfsync everysec`), which
bounds write loss to ~1 second *inside a running Redis process*. It does **nothing**
for total loss of the Redis data directory: `FLUSHALL`, disk failure, an accidental
`rm -rf` of the data dir, or a fresh machine that comes up with an empty Redis. In
every one of those cases **every `AgentSession` record is gone** and there is no
second copy to rehydrate from. There is documented precedent for exactly this class
of event: on 2026-06-03 a `flushdb()` against db=0 wiped production (that drove
`tests/unit/test_redis_flush_guard.py`). AOF would not have helped if the whole
data dir had been lost.

The persona/doc claim that "Popoto handles persistence" only becomes fully true
once a **transactional secondary store** exists. `#1814` explicitly deferred that
work (its "Fix #2", ~1-2d) into this issue, and left `models/session_lifecycle.py`
untouched so this plan can hook the single terminal-write chokepoint cleanly.

**Current behavior:** `AgentSession` state lives in exactly one place — Redis. A
total data-dir loss is unrecoverable; the worker restarts against an empty Redis and
silently proceeds as if no sessions ever existed. There is no operator signal that a
second copy is missing, stale, or was never written.

**Desired outcome:** Every `AgentSession` is periodically exported to a
transactional SQLite store on disk (`data/session_archive.db`) — on a cadence **and**
immediately on any terminal-status transition — using crash-safe atomic writes. On
worker startup, if (and only if) Redis is **provably empty of AgentSession data**,
the archive is rehydrated back into Redis. The restore path is idempotent and refuses
to run against a partially-populated Redis, so it can never clobber live state. The
export freshness (last-export age + row count) is surfaced on `dashboard.json`,
mirroring the existing email/heartbeat freshness pattern.

## Scope

**IN SCOPE (Medium appetite):**
- New `agent/session_archive.py`: a transactional SQLite archive with three public
  entry points — `export_all()` (periodic full sweep), `export_session(session)`
  (single-row terminal upsert), `restore_if_empty()` (startup guarded rehydrate) —
  plus `get_archive_status()` for the operator surfaces.
- Hook `models/session_lifecycle.py::finalize_session` to call `export_session(...)`
  as the **last** side effect, unconditionally **after** `session.save()` succeeds,
  inside `try/except`.
- A periodic export daemon thread in `worker/__main__.py` (mirrors the existing
  heartbeat daemon thread — runs off the asyncio event loop because SQLite +
  `query.all()` are blocking).
- A guarded restore step at worker startup (`worker/__main__.py::_run_worker`),
  placed **before** the index-rebuild step, that runs only on a provably-empty Redis.
- Operator surface: `data/session_archive.db` `_meta` row (last-export ts, row count,
  kind), a new `archive` block on `dashboard.json` + `/health`, and a
  `session-archive-freshness` doctor check.
- `valor-session-archive` CLI for agent reachability and safe manual ops: a `status`
  subcommand (prints `get_archive_status()` JSON) and a **read-only** `restore --dry-run`
  subcommand (reports the guard decision + would-restore count without writing). The
  write paths (`export`, live `restore`) are deliberately **not** exposed as CLI
  subcommands — export runs automatically (daemon thread + terminal hook) and restore
  runs automatically at guarded startup, so a manual write subcommand would only
  duplicate that and add a footgun. See No-Gos.
- Docs: implement the currently roadmap-only "Fix #2" section of
  `docs/features/redis-durability.md`.

**OUT OF SCOPE (deferred, tracked elsewhere):**
- Fix #4 — moving hot-path Redis off the event loop (separate issue).
- Fix #5 — Redis replication + Sentinel (already shipped, #1827).
- Archiving `Memory`, Telegram/email history, or the bloom filter. This plan covers
  `AgentSession` only, exactly as the issue scopes it. (See No-Gos.)

## Freshness Check

**Baseline commit:** `dbe9682d9d5832087b6825b9c883b71775f82788` (main, HEAD at supervision
time — was `b99e2958` at plan time). **Disposition: Minor drift** — every claim still
holds; line numbers moved (worker startup shifted ~140 lines) and two sibling PRs from the
same #1818 cluster merged after the plan was written. No structural change to the approach.
**Issue filed at:** #1825, part of the #1818 resilience cluster; #1814 merged as PR #1824.

**Third revision (supervision-loop freshness re-verification, baseline `dbe9682d`):** two
sibling PRs from the #1818 resilience cluster merged after the plan's `b99e2958` baseline
and were reconciled here:
- **#1826 (`51ecfd0c`, "Move hot-path Redis off the event loop")** added
  `agent/redis_offload.py` — an `async def offload_redis(fn, ...)` bulkhead over a bounded
  `ThreadPoolExecutor` that moves a synchronous Popoto/redis-py call off the event loop.
  Its docstring and the single production call site
  (`agent/agent_session_queue.py:1665`, the drain-loop idle-check) confirm it targets the
  **ONE on-loop read hot-path**, not the finalize write path. **Impact on this plan: none
  structurally — the plan deliberately does NOT wrap the archive hook in `offload_redis`,
  and this is now stated explicitly** (see Architectural Impact and the Technical Approach
  "Off-loop execution" note). Rationale: `finalize_session` is a **sync** function called
  from `async def _execute_agent_session` (`agent/session_executor.py:754`), so it cannot
  `await offload_redis(...)`; and the authoritative `session.save()` at
  `session_lifecycle.py:482` — the line immediately before the hook — is itself a
  synchronous, un-offloaded Redis write on the same loop. Offloading only the *secondary*
  archive write while the *authoritative* save stays on-loop would be incoherent. The
  periodic `export_all()` sweep is already off-loop on its own daemon thread (it mirrors
  the heartbeat thread, a pattern that predates `offload_redis`), so it needs no offload
  seam either. If finalize's on-loop blocking is ever addressed, `offload_redis` is the
  sanctioned mechanism and the whole finalize path (save + archive) must move together — a
  separate follow-up, out of this Medium appetite.
- **#1828 (`dbe9682d`, "Reflection scheduler subprocess split")** moved reflections out of
  the worker into their own launchd subprocess (`python -m reflections`,
  `com.valor.reflection-worker` — confirmed by `worker/__main__.py:1183` and
  `reflections/__main__.py`). **Impact on this plan: none — the periodic export is a
  worker daemon thread, NOT a reflection.** The plan never wired the export as a
  reflection, so the subprocess split does not touch it; the export must stay co-located
  with the worker anyway (the terminal hook lives in the worker's `finalize_session` path,
  and both writers share the two-writer connection model). The heartbeat daemon-thread
  pattern the export mirrors is intact at `worker/__main__.py:1164-1170`.

Prior revisions: first (post-critique) corrected the worker startup anchors so restore
lands below the dry-run guard and added the `id`/`AutoKeyField` key-preservation fact and
the two-writer thread model. Second (critique round 2) confirmed the dead-worker-sweep
ordering anchors and empirically validated the two `agent_session_id`-key greps against
correct and buggy simulated code (the negative check is deliberately pipe-free — a `\|`
alternation is silently mis-parsed as a literal pipe by BSD `grep -E` once markdown-escaped,
which would false-green).

**Fourth revision (critique NEEDS REVISION — 1 blocker + 4 concerns + 1 nit):** addressed
without touching the four preserved core design points (Popoto `query.all()` reads, the
empty-Redis dual-guard, the atomic `BEGIN IMMEDIATE … COMMIT` export transaction, and the
deliberate non-use of #1826's `offload_redis` for the finalize hook). **Blocker** (stuck
`restore_in_progress` sentinel bypassing the empty-Redis guard forever): the guard-bypass is
now re-decided every boot against the freshly recomputed `live_count < expected_row_count`,
never the persisted flag; resume attempts are bounded (`SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`)
and poison rows are quarantined past `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` so one bad row can
never wedge restore. **Concern 1** (export_all fault isolation): per-row `try/except`
serialization isolation added before the single atomic COMMIT. **Concern 2** (on-loop
busy-timeout stall): tight `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` (250 ms) for the on-loop
single-row write, distinct from the 5 s sweep timeout. **Concern 3** (speculative payload
truncation): `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` cut entirely — payloads stored verbatim.
**Nit** (two `\|`-alternation greps false-greening on darwin): converted to `grep -E` with
real `|`.

**File:line references re-verified at HEAD (`dbe9682d`) — all drifted line numbers below
are the CURRENT values:**
- `models/session_lifecycle.py:229` — `finalize_session(...)` signature (was 221). Confirmed.
  The terminal write is `session.save()` at **line 482** (was 474); the function's last side
  effects are the defensive `srem` (484-506), the `TaskTypeProfile` update (508-518), and
  the analytics `record_metric` block (520-533). The archive hook belongs **after line 533**
  (the current end of the function body, before `def transition_status` at 536), so it runs
  strictly after the authoritative Redis save. `TERMINAL_STATUSES` at line 61 (unchanged);
  `ALL_STATUSES` at line 105 (was 97).
- `agent/session_executor.py:754` — `async def _execute_agent_session(session)` is the
  async caller that invokes `finalize_session` synchronously (call sites at 843, 892,
  2092), confirming the hook runs **on the event-loop thread**. Newly anchored this revision
  (grounds the two-writer model and the #1826 offload reconciliation).
- `worker/__main__.py:789-790` — "Redis connection verified" block
  (`AgentSession.query.filter(status="pending")`) (was 695-703). Re-verified.
- `worker/__main__.py:885-890` — the `if dry_run: ... return` guard (was 746-751). **Restore
  MUST run below this guard**, not after the connection-verified block, or a `--dry-run` boot
  against an empty Redis would rehydrate/mutate Redis (a dry run must never write).
- `worker/__main__.py:892-901` — callback/handler registration (`register_callbacks`) (was
  753-762). Restore slots into the **890→892 gap** (was 751→753): after the dry-run `return`
  and **before** handler registration and the Step 1 index rebuild. This keeps Race
  Condition #2's invariant (restore completes before any handler can create a new session)
  while respecting the dry-run guard.
- `worker/__main__.py:903` (comment) / `911` (`run_cleanup()` call) — Step 1 index rebuild
  (was line 764); restore precedes it so the rebuild reindexes the freshly-restored rows
  (they are otherwise not queryable — see the cold-boot success criterion).
- `worker/__main__.py:1164-1170` — heartbeat daemon thread spawn
  (`threading.Thread(target=_heartbeat_thread_main, name="worker-heartbeat", daemon=True)`)
  (was 1003-1005). The export daemon thread is spawned adjacent to it using the identical
  pattern.
- `agent/session_health.py:4125` — `_write_worker_heartbeat()` uses the
  write-temp-then-`os.replace()` atomic file pattern (`os.replace` at 4135; was 3001-3011).
  In-repo precedent for atomic writes (the archive uses a SQLite transaction instead — see
  Technical Approach for why). Confirmed.
- `analytics/collector.py:17-74` — existing in-repo SQLite usage: `_DB_DIR = Path(__file__).parent.parent / "data"`
  (line 17), `_SQLITE_TIMEOUT = 5` (line 25), `PRAGMA journal_mode=WAL` (line 63), **and a
  single module-level `_sqlite_conn` opened at line 73 with the default
  `check_same_thread=True`** (`_sqlite_conn` declared at line 31). The archive borrows the
  file/WAL/timeout shape but **must NOT** copy the single-shared-connection detail: analytics
  is written from one thread, this archive from **two** (see the two-writer note below).
  Re-verified.
- `models/agent_session.py:139` — `id = AutoKeyField()` is the **real persisted key** (was
  137); `agent_session_id` (property at line 1164, setter 1169; was 1124-1132) is only an
  alias. `_normalize_kwargs` at **line 850** (was 805-806) *pops and discards* any
  `agent_session_id` kwarg (`# AutoKeyField, ignore`), while an explicit `id=` kwarg is
  honored. Verified empirically: `AgentSession(id=X, ...).save()` preserves the key and
  `query.get(id=X)` finds it; `AgentSession(agent_session_id=X, ...)` regenerates a fresh
  UUID. **Crux of Blocker #1**: export/restore must key on `id`, not `agent_session_id`, or
  the key regenerates on restore and every `parent_agent_session_id` link dangles.
- **Two-writer thread model (Blocker #2, verified by design):** `export_all()` runs on
  the periodic **daemon thread**; `export_session()` runs inside `finalize_session`,
  which executes on the **asyncio event-loop thread** (confirmed via
  `_execute_agent_session` above). SQLite connection objects are thread-affine under the
  default `check_same_thread=True` — sharing one module-level connection across these two
  threads raises `sqlite3.ProgrammingError`. WAL + busy-timeout do NOT fix this (they
  serialize *separate* connections/processes, not cross-thread reuse of one object).
  Mitigation stated in Technical Approach / Risks / Race Conditions.
- `agent/session_health.py` — dead-worker sweep `_sweep_dead_worker_sessions` at **line
  1168** (was 835) and `_recover_interrupted_agent_sessions_startup` at **line 932** (was
  599), for the stale-`claude_pid` note in Data Flow. Re-verified; sweep→recover ordering
  intact.
- `ui/app.py:729-730` — `/dashboard.json` route (was 507-548); `_get_email_health()` at 559
  (was 388-421) is the freshness-block template to mirror; `_session_to_json` at 627 (was
  424). Confirmed.
- `models/agent_session.py` — `AgentSession` field set with `id = AutoKeyField()` at line
  139; `query.all()` used at line 1045 (was 988). Confirmed.
- `.gitignore` — `*.db`, `*.db-shm`, `*.db-wal`, and `data/` are already ignored, so
  `data/session_archive.db` and its WAL sidecars are never committed. Confirmed.

**Commits on main since the `b99e2958` plan baseline (touching referenced files):** #1826
(`51ecfd0c`) and #1828 (`dbe9682d`) are the material ones and are reconciled above; both
added lines to `worker/__main__.py` (hence the ~140-line startup drift) but changed neither
the `finalize_session` chokepoint nor the dry-run→register→Step-1 startup ordering. Other
commits since (#1877 lifecycle notification gaps, #1817/#1820 worker delivery-integrity)
did not alter the archive's insertion points. Premises hold.

**Active plans in `docs/plans/` overlapping this area:** none. The parent
`docs/plans/completed/redis-durability-hardening.md` explicitly split this out.

## Prior Art

- **`analytics/collector.py`** — the canonical in-repo SQLite pattern: a `data/*.db`
  file, WAL journal mode, a `_SQLITE_TIMEOUT`, a reused module-level connection, and
  `CREATE TABLE IF NOT EXISTS`. The archive follows the same shape (its own DB file,
  its own connection). Test precedent: `tests/unit/test_analytics_collector.py`,
  `tests/unit/test_analytics_query.py`.
- **`agent/session_health.py::_write_worker_heartbeat`** — the write-temp-then-
  `os.replace()` atomic-file idiom already used for the heartbeat file.
- **`agent/redis_offload.py::offload_redis` (#1826, merged after this plan's baseline)** —
  the repo's now-sanctioned bulkhead for moving a synchronous Popoto/redis-py call **off
  the event loop** (bounded `ThreadPoolExecutor`, kill-switch, latency gauges). Its sole
  production caller is the drain-loop idle-check. This plan **does not** route the archive
  hook through it — the terminal `export_session` runs inline in the sync `finalize_session`
  (matching the un-offloaded `session.save()` that precedes it) and the periodic sweep is
  already off-loop on its own daemon thread — but the module is cited so a reviewer sees the
  offload seam was considered and deliberately left for a future finalize-path offload
  (which would move `save()` + archive together). See the Freshness Check reconciliation.
- **`tests/conftest.py::redis_test_db`** — the fixture that isolates `POPOTO_REDIS_DB`
  per test; the archive's restore/export tests reuse it so they never touch prod Redis
  or prod `data/session_archive.db` (tests point the archive at a `tmp_path` DB).
- **Hermes SQLite session store** (external pattern reference cited by the issue): a
  durable second copy of session records keyed by session id, upserted on state change
  and swept periodically, restored on a cold start. This plan adopts the same
  "upsert-on-terminal + periodic-sweep + cold-start-restore" shape.
- **`#1814` critique CONCERN (Risk & Robustness / Adversary)** already reasoned about
  this hook and prescribed: insert the archive call as the LAST side effect,
  unconditionally AFTER `session.save()` succeeds, inside `try/except`, never before
  the CAS check or save — because a mid-sequence `StatusConflictError` would otherwise
  record a "completed" row for a session Redis never finalized. This plan bakes that in.

## Research

No external research needed — `sqlite3` is stdlib and already used in-repo. The only
substrate question (how to enumerate/rebuild all `AgentSession` fields for a
high-fidelity round-trip) is answered by Popoto source: `session._meta.fields` is the
field map, and reconstruction is `AgentSession(**field_kwargs).save()`. **Key
preservation is mandatory** — the field map includes the real `id` (`AutoKeyField`), and
the restore kwargs MUST carry `id=<archived id>` so the AutoKey is *not* regenerated.
Passing the `agent_session_id` alias instead is a trap: `_normalize_kwargs`
(`models/agent_session.py:850`) pops it, and Popoto mints a fresh UUID, orphaning
every `parent_agent_session_id` reference. Empirically verified at HEAD: explicit `id=`
survives `save()` and `query.get(id=...)`; `agent_session_id=` does not. Datetime/dict/
list fields are the additional fidelity risk and are handled explicitly (see Risks and
Rabbit Holes).

## Data Flow

This change adds a **second sink** and a **cold-start source**, both off the request
path. Three flows:

1. **Terminal transition → archive (synchronous, single row).**
   `finalize_session(session, <terminal>)` runs its existing side effects, does the
   authoritative `session.save()`, and *then* (last, in `try/except`) calls
   `session_archive.export_session(session)`, which upserts exactly one row into
   `data/session_archive.db` inside a SQLite transaction. A failure here logs and is
   swallowed — losing a second copy must never fail the terminal transition.

2. **Periodic sweep → archive (background thread, full snapshot).**
   A daemon thread wakes every `SESSION_ARCHIVE_INTERVAL` seconds, calls
   `session_archive.export_all()`, which reads `AgentSession.query.all()` and upserts
   every current record in **one** SQLite transaction, then stamps the `_meta` row
   (`last_export_ts`, `row_count`, `kind="periodic"`). Runs off the event loop because
   both the Redis scan and the SQLite write are blocking.

3. **Cold start → restore (startup, guarded).**
   `_run_worker()` calls `session_archive.restore_if_empty()` **after the `if dry_run:
   return` guard** (`worker/__main__.py:885-890`) and **before** the Step 1 index rebuild
   (comment at line 903, `run_cleanup()` at 911) — i.e. in the 890→892 gap. Placing it
   below the dry-run guard is
   load-bearing: a `--dry-run` boot must observe/report but never mutate Redis, so restore
   (which writes) cannot run above the guard. The function checks the empty-Redis guard
   (below); on a provably-empty Redis — or, when a prior restore left the
   `restore_in_progress` sentinel set, **only while the freshly recomputed live count
   `len(AgentSession.query.all())` is still strictly below `expected_row_count` and the
   resume-attempt cap is not exhausted** — it sets/keeps the sentinel, rehydrates every
   archived row (skipping quarantined poison `id`s) back into Redis via
   `AgentSession(id=<archived id>, **other_fields).save()` — **the archived `id` is passed
   explicitly** so the AutoKey is preserved and parent/child links stay intact (never the
   `agent_session_id` alias, which `_normalize_kwargs` drops) — and clears the sentinel once
   `restored + quarantined == expected_row_count` (see the restore-atomicity mechanism in
   Technical Approach). On any Redis whose live count already meets `expected_row_count` it
   clears any stale sentinel and returns without touching Redis, and on a normal non-empty
   Redis with no pending sentinel it logs and returns — **a persisted sentinel can never
   force a rewrite of an already-populated Redis**. Rehydrated rows are not queryable by
   secondary index until the Step 1 rebuild runs immediately after, which is why restore
   must precede it.

   A restored session may carry a **stale `claude_pid`** (the pid of a worker process
   that no longer exists after the data-dir loss). Such rows in `running` status flow
   through the worker's existing **Step 3a dead-worker sweep**
   (`agent/session_health.py::_sweep_dead_worker_sessions`, which sweeps `running`
   sessions whose `claude_pid` fails `os.kill(pid, 0)` to `killed`) **before** the
   **Step 3b** recovery pass (`_recover_interrupted_agent_sessions_startup`) — exactly as
   any pre-existing `running` session would after a normal worker restart. Restore does
   not need to scrub `claude_pid`; the existing sweep→recover ordering already handles the
   stale-pid case, and this plan changes nothing about it.

**Empty-Redis guard (precise):** restore proceeds **iff BOTH** are true:
`AgentSession.query.all()` returns zero records **AND** a bounded `SCAN` for keys
matching the `AgentSession*` prefix returns zero keys (this catches orphaned index
sets a `query.all()` might not surface). `DBSIZE == 0` is checked and **logged** as
advisory confirmation but is **not** required — a real cold start after total data-dir
loss has `DBSIZE == 0`, but AOF/partial loss could wipe `AgentSession*` while leaving
`Memory:*` / bloom keys, and we still want to restore sessions in that case. If even a
single `AgentSession` record or index key exists, Redis is treated as
partially-populated and restore is a **no-op** (never merge, never clobber).

## Architectural Impact

- **New dependencies:** none (`sqlite3` is stdlib).
- **New module:** `agent/session_archive.py` (single-responsibility: the archive).
- **Interface changes:** one additive call inside `finalize_session` (last side
  effect, exception-isolated); one additive startup step + one daemon thread in the
  worker. No signatures change.
- **Data ownership:** Redis stays authoritative. SQLite is a strictly-secondary,
  write-only-in-normal-operation copy; it is read only on a cold start. The archive
  DB is **machine-local** (each machine has its own Redis → its own archive) and is
  never synced or committed (`.gitignore` covers `*.db` + `data/`).
- **Coupling:** one Popoto coupling point — enumerating `session._meta.fields` for a
  full-fidelity export/restore. Flagged for re-verification on Popoto upgrade (mirrors
  the existing `_saved_field_values` coupling note in `session_lifecycle.py`).
- **Reversibility:** high. Deleting `data/session_archive.db`, removing the daemon
  thread, and no-opping the two hooks fully reverts; Redis operation is unaffected.

## Appetite

**Size:** Medium (~1-2d, matches the #1814 estimate for Fix #2)

**Team:** Solo dev + code reviewer.

**Interactions:**
- PM check-ins: 1 (confirm the empty-Redis guard semantics and the export cadence).
- Review rounds: 1 (the restore guard correctness and the terminal-hook placement are
  the two things to review carefully).

Most of the effort is the SQLite serialization/round-trip fidelity and the precise,
well-tested empty-Redis guard — not volume of code.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Python `sqlite3` (stdlib) | `python -c "import sqlite3; print(sqlite3.sqlite_version)"` | Archive store backend |
| Writable `data/` dir | `test -w data && echo ok` | Archive DB location |
| Redis reachable | `redis-cli ping` | Export reads / restore writes |
| `AgentSession.query.all()` works | `python -c "from models.agent_session import AgentSession; print(len(list(AgentSession.query.all())))"` | Export source enumeration |

## Solution

### Key Elements

- **`agent/session_archive.py`** — the whole secondary store in one module:
  - `_connect()` — **opens a fresh connection per operation** (open at the start of each
    `export_session`/`export_all`/`restore_if_empty`/`get_archive_status` call, close in a
    `finally`), created with WAL journal mode and a bounded busy-timeout. This differs
    deliberately from `analytics/collector.py`'s single module-level connection: the
    archive is written from **two threads** (the periodic daemon thread and the
    event-loop `finalize_session` hook), and a shared connection under the default
    `check_same_thread=True` would raise `sqlite3.ProgrammingError` when reused across
    threads. A per-operation connection sidesteps thread-affinity entirely; WAL +
    busy-timeout then serialize the two short-lived connections at the DB level. (An
    equivalent alternative — one `check_same_thread=False` connection guarded by an
    explicit `threading.Lock` — is viable but strictly more error-prone; per-operation
    connections are chosen for simplicity and are cheap for this low-frequency workload.)
    One `sessions` table (promoted columns **`id` PK** — the real `AutoKeyField` key,
    NOT the `agent_session_id` alias — `session_id`, `project_key`, `status`,
    `updated_at`, plus a `payload` JSON blob holding the full `_meta.fields` snapshot,
    which itself includes `id`) and one `_meta` table (`last_export_ts`, `row_count`,
    `kind`, plus the restore-atomicity sentinel fields `restore_in_progress`,
    `restore_complete`, `expected_row_count`, `resume_attempts`) and a small
    `_restore_quarantine` table (poison `id`s past the per-row attempt cap, so one
    permanently-unrestorable row can never wedge restore forever — see the Blocker fix in
    Technical Approach).
  - `export_session(session)` — serialize one session's full field set (keyed on the
    real `id`, **full payload stored verbatim — no size cap**, see Serialization fidelity),
    upsert one row in a single transaction on its own connection opened with a **tight
    on-loop busy-timeout** (`SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS`, see the Concern-2
    bound in Technical Approach), refresh `_meta` (`kind="terminal"`). Fast, single-row;
    on a WAL-lock timeout it logs and returns — the terminal transition still completes and
    the periodic sweep re-covers the row.
  - `export_all()` — `AgentSession.query.all()` → serialize each row **inside a per-row
    `try/except`** (a row that fails to serialize is logged with its `id` and skipped, so
    one bad row can never abort the sweep) → upsert every successfully-serialized row in
    **one** `BEGIN IMMEDIATE … COMMIT` transaction on its own connection, refresh `_meta`
    (`kind="periodic"`). The single atomic transaction is deliberate (crash-safe consistent
    snapshot); per-row serialization isolation is the fault-isolation layer that keeps a
    single poison row from stranding the batch (see Concern-1 note in Technical Approach).
    Rows present in the DB but absent from Redis are **retained** (the archive is a superset
    floor, never prunes live-set deletions — deletion is not a durability event we want to
    propagate on a cold start).
  - `restore_if_empty()` — the guard is gated on **live Redis emptiness recomputed every
    boot**, never on a persisted sentinel alone: it restores on a provably-empty Redis, and
    on a pending `restore_in_progress` sentinel it **bypasses the empty-Redis guard ONLY
    while `len(AgentSession.query.all()) < expected_row_count` AND `resume_attempts` is
    under the cap** — so a fully-populated healthy Redis is never clobbered even if the
    sentinel is stuck (see the Blocker fix in Technical Approach). On pass it sets the
    sentinel, iterates archived rows (skipping quarantined poison `id`s) and
    `AgentSession(id=<archived id>, **other_fields).save()` each — **the archived `id` is
    passed explicitly so the AutoKey is preserved** (verified: an explicit `id=` survives
    save; the `agent_session_id` alias is dropped by `_normalize_kwargs`) — and clears the
    sentinel once `restored + quarantined == expected_row_count`. A mid-loop failure leaves
    the sentinel set so the next boot resumes (idempotent upserts on the preserved `id`); a
    row that fails past the per-row attempt cap is quarantined so it can never wedge the
    resume. Idempotent — re-running on a now-fully-populated Redis no-ops via the live-count
    gate. Returns a structured result `{restored: int, skipped_reason: str|None, resumed:
    bool, quarantined: int}`.
  - `get_archive_status()` — open a short-lived read connection, read `_meta` +
    `sqlite_master`; return
    `{db_path, exists, row_count, last_export_ts, last_export_age_s, kind, healthy}`
    for the dashboard, `/health`, doctor, and CLI. Never raises (returns a
    `healthy=False` shape on any error).
- **Terminal hook** — one exception-isolated call at the end of `finalize_session`.
- **Periodic daemon thread** — `worker/__main__.py` spawns `worker-session-archive`
  alongside the heartbeat thread; each cycle calls `export_all()` and sleeps
  `SESSION_ARCHIVE_INTERVAL`.
- **Guarded restore step** — one call in `_run_worker` startup, below the dry-run guard
  and before the Step 1 index rebuild.
- **`valor-session-archive` CLI** — read-only surface only: `status` (prints
  `get_archive_status()` as JSON) and `restore --dry-run` (reports the guard decision +
  would-restore count without writing). No `export` or live-`restore` write subcommands —
  those paths run automatically (daemon thread + terminal hook / guarded startup) and a
  manual write subcommand would only duplicate them and add a footgun.
- **Doctor + dashboard** — freshness surfaces mirroring email/heartbeat.

### Flow

Normal operation: sessions finalize → one row upserted each; every
`SESSION_ARCHIVE_INTERVAL` the full set is re-upserted → `data/session_archive.db`
tracks Redis with a bounded lag; `dashboard.json.archive.last_export_age_s` stays
small. Cold start after total Redis loss: worker boots → Redis verified reachable but
empty → `restore_if_empty()` guard passes → all archived sessions rehydrated → index
rebuild (Step 1) then reindexes them → normal recovery proceeds. Restart with a
healthy Redis: guard fails (records present) → restore no-ops → no clobbering.

### Technical Approach

- **Connection model — per-operation connection (NOT a shared module-level one).**
  Every public entry point opens its own `sqlite3.Connection` at the top of the call and
  closes it in a `finally`. This is a deliberate divergence from
  `analytics/collector.py`'s reused module-level `_sqlite_conn`. The reason is the
  **two-writer thread model**: `export_all()` runs on the periodic **daemon thread**
  while `export_session()` runs (via the `finalize_session` hook) on the **asyncio
  event-loop thread**. A SQLite connection under the default `check_same_thread=True` is
  bound to its creating thread; reusing one shared connection across those two threads
  raises `sqlite3.ProgrammingError`. WAL + busy-timeout do **not** address this — they
  serialize *distinct* connections/processes at the file level, not cross-thread reuse of
  a single connection object. A per-operation connection removes thread-affinity
  concerns entirely and is cheap at this workload (single-row terminal upserts + a 5-min
  full sweep). (The alternative — one `check_same_thread=False` connection + an explicit
  `threading.Lock` around every use — is functionally equivalent but easier to get wrong;
  per-operation is preferred.)
- **Off-loop execution & the #1826 `offload_redis` seam (deliberately not used here).**
  `#1826` (merged after this plan's baseline) added `agent/redis_offload.py`, the repo's
  sanctioned `async` bulkhead for pushing a synchronous Redis call off the event loop. This
  plan **does not** route either archive write through it, by design:
  - **Terminal hook (`export_session`) — stays inline.** It runs inside `finalize_session`,
    a **synchronous** function called from `async def _execute_agent_session`
    (`agent/session_executor.py:754`). A sync function cannot `await offload_redis(...)`.
    More importantly, the authoritative `session.save()` at `session_lifecycle.py:482` — the
    line immediately preceding the hook — is itself a synchronous, un-offloaded Redis write
    on the same loop; offloading only the *secondary* SQLite write while the *authoritative*
    Redis save blocks in place would be incoherent. The single-row WAL upsert is a small
    marginal cost on top of the save that is already there.
  - **Periodic sweep (`export_all`) — already off-loop.** It runs on the
    `worker-session-archive` **daemon thread**, which mirrors the heartbeat daemon thread and
    predates `offload_redis`. A call that already executes on its own thread needs no
    event-loop-offload seam.
  - **If** the finalize path's on-loop Redis/SQLite blocking is ever addressed, the
    sanctioned mechanism is `offload_redis`, and the whole finalize path (`save()` + archive)
    must move together — a separate follow-up, out of this Medium appetite. This bullet exists
    so critique does not flag "why wasn't the new offload seam used?" — it was considered and
    deliberately deferred.
- **Key preservation on restore.** The archive stores the real `id` (`AutoKeyField`) as
  the primary column and inside the JSON payload, and restore reconstructs via
  `AgentSession(id=<archived id>, **other_fields).save()`. This is mandatory: verified at
  HEAD, an explicit `id=` is honored and preserved through `save()`, whereas the
  `agent_session_id` alias is popped by `_normalize_kwargs` (`models/agent_session.py:850`)
  and a fresh UUID is minted — which would silently dangle every `parent_agent_session_id`
  / child linkage at cold start. Never key on `agent_session_id`.
- **Atomicity mechanism — SQLite transaction, deliberately NOT temp-file-rename.**
  The crash-safe primitive here is a single `BEGIN IMMEDIATE ... COMMIT` transaction:
  SQLite in WAL mode is ACID, so a crash mid-commit rolls the whole batch back and a
  reader always sees a consistent prior state. We **deliberately do not** write a temp
  DB and `os.replace()` it (the pattern used for the plain-text heartbeat file):
  replacing a live SQLite file discards its WAL, races concurrent readers
  (dashboard/CLI), and corrupts open connections. The transaction *is* the atomic,
  crash-safe primitive for a database — it is strictly better than temp-rename here.
  This is the intentional divergence from the "write-temp-then-rename" idiom; it is
  called out so review does not flag it as a missing atomicity guard.
- **Serialization fidelity.** `export_*` enumerates `session._meta.fields` and reads
  each attribute into a dict, converting `datetime` → ISO-8601 string and leaving
  dict/list fields as JSON-native. The whole dict is stored **verbatim, with no size
  cap**, as a JSON `payload` column (including the real `id`), with
  `id`/`session_id`/`project_key`/`status`/`updated_at` promoted to real columns for
  queryability and the restore ordering. On restore, ISO strings are parsed back to
  `datetime` for `DatetimeField`/`SortedField`/`created_at`, and the row is reconstructed
  via `AgentSession(id=<archived id>, **other_fields).save()` so the key is preserved (see
  Key preservation above). The datetime round-trip and the key/parent-child-graph
  preservation are the two fidelity risks and each gets a dedicated test.
- **No payload truncation in v1 (Concern #3 — cut).** An earlier draft bounded the
  `payload` blob with a `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` cap that truncated/omitted an
  oversized field. That is **removed**: truncation is speculative (no observed size
  problem), it silently corrupts round-trip fidelity (a restored session missing a field
  is not the session that was archived), and a half-serialized row is itself a poison-row
  vector for the restore blocker. v1 stores full payloads verbatim. If a real size problem
  ever appears (measured, not hypothesized), an incremental-by-`updated_at` sweep or a
  compression pass is the clean follow-up — revisited then, not pre-emptively. See the
  Rabbit Holes note.
- **Per-row serialization isolation in `export_all()` (Concern #1).** The whole-batch
  single-transaction commit is deliberately kept (it is the crash-safe primitive — see
  Atomicity mechanism below — and gives a reader a consistent snapshot). Fault isolation is
  layered **before** the write, not by splitting the transaction: `export_all()` serializes
  each `query.all()` row inside its own `try/except`, and a row that fails to serialize is
  logged (`logger.warning` with the session `id` and the exception) and skipped, so the
  atomic `COMMIT` still writes every good row and one pathological session cannot abort the
  entire periodic sweep. This makes the export loop as fault-tolerant as the restore loop
  (the asymmetry the critique flagged), while preserving the single atomic transaction. The
  single-row `export_session()` is already isolated by the `finalize_session` `try/except`
  that wraps it.
- **Tight on-loop busy-timeout for `export_session()` (Concern #2).** The terminal
  `export_session()` runs inline on the asyncio event-loop thread (see Off-loop execution
  below), so a WAL write-lock held by the concurrent periodic sweep could otherwise stall
  the loop for up to the full SQLite busy-timeout. The on-loop connection is therefore
  opened with a **tight** `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` (named, env-overridable
  constant in `agent/constants.py`, default **250 ms**, grain-of-salt/tunable) — an order
  of magnitude below the periodic sweep's own busy-timeout (`SESSION_ARCHIVE_BUSY_TIMEOUT_MS`,
  default 5000 ms, matching `analytics/collector.py`'s `_SQLITE_TIMEOUT`). If the tight
  bound is exceeded the single-row write raises `sqlite3.OperationalError` (lock timeout),
  which is swallowed by the `finalize_session` `try/except`; the terminal transition
  completes and the next periodic sweep re-covers the row. The event loop is thus never
  blocked more than ~250 ms by the archive hook. (Moving the single-row write fully
  off-loop was considered and rejected: it would require the sync `finalize_session` to
  schedule onto the loop or a thread, adding ordering complexity for a write that is
  already a small marginal cost bounded to 250 ms — see the Off-loop execution note on why
  the whole finalize path, save + archive, must move together if it moves at all.)
- **Empty-Redis guard (exact) — bypass gated on LIVE emptiness, never on the sentinel
  alone (Blocker fix).** In `restore_if_empty()`:
  1. Read the sentinel from `_meta`: `restore_in_progress`, `expected_row_count`,
     `resume_attempts`. Compute `live_count = len(list(AgentSession.query.all()))`
     **fresh, every boot** — this recomputed live count is the authoritative gate, not any
     persisted flag.
  2. **Cold-start path (no pending sentinel):** if `live_count == 0` **AND** a bounded
     `SCAN` (`count=1000`, capped iterations) for `AgentSession*` keys via the Popoto client
     returns zero keys → restore. If `live_count > 0` → skip
     (`skipped_reason="redis_has_records"`); if any `AgentSession*` key exists → skip
     (`skipped_reason="redis_has_keys"`). `DBSIZE` is logged as advisory only, never a gate.
  3. **Resume path (sentinel set):** bypass the empty-Redis guard **ONLY IF**
     `live_count < expected_row_count` **AND** `resume_attempts < SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`.
     If `live_count >= expected_row_count`, Redis is already whole → clear the sentinel and
     no-op (`skipped_reason="restore_already_complete"`) — **this is the invariant fix: a
     stuck sentinel can never clobber a healthy populated Redis, because the live count, not
     the flag, decides.** If `resume_attempts >= cap`, stop resuming, log a **wedged-restore
     operator error** and surface it on the dashboard/doctor, and no-op
     (`skipped_reason="restore_wedged"`) — a poison row can no longer trigger an unbounded
     clobber-on-every-boot loop.
  4. Guard is evaluated **once**, before any write.
- **Restore atomicity — partial restore is detected, resumed, and bounded, never wedged
  and never a silent half-loss (Blocker + Concern-3 fix).** A naive per-row loop with only
  whole-function exception isolation has a silent-data-loss failure mode: if `.save()`
  raises on row 51 of 100, Redis ends up with 50 records and a next boot could mistake them
  for a clean restore. But a sentinel that clears **only** on `restored == expected_row_count`
  has the *opposite* failure mode the critique caught: a single permanently-unrestorable
  row (an id collision, a rejected payload) means the sentinel NEVER clears, and every
  later boot — including a healthy boot against a full Redis — would bypass the guard and
  re-upsert every archived row by preserved id, resurrecting deleted sessions and
  overwriting live ones. Both modes are closed by three mechanisms working together:
  1. **Durable sentinel + fresh live-count gate.** Before writing the first row, set
     `_meta.restore_in_progress = 1` with the total `expected_row_count` (a durable marker
     in the archive DB, which survives a Redis wipe). But the **bypass decision is gated on
     the freshly recomputed `live_count < expected_row_count` every boot** (guard step 3
     above), not on the sentinel alone — so the sentinel only ever *enables* a resume; it
     can never *force* a clobber of an already-whole Redis.
  2. **Bounded resume attempts.** Each resume increments `_meta.resume_attempts`. Past
     `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP` (named constant, default **5**) the restore is
     declared *wedged*, logged as an operator error, surfaced on the dashboard/doctor
     freshness block, and no longer bypasses the guard — so a permanently-failing row can
     never drive an unbounded re-upsert loop.
  3. **Per-row poison quarantine.** Each archived `id` carries a per-row attempt counter;
     a row that fails to `.save()` more than `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` times
     (named constant, default **3**) is written to the `_restore_quarantine` table, logged
     with its `id`, and **skipped on all future resumes**. The completion condition is
     therefore `restored + quarantined == expected_row_count` → clear the sentinel
     (`restore_in_progress = 0`, stamp `restore_complete = 1`). One poison row is quarantined
     after a few tries, the remaining good rows finish, and the sentinel clears cleanly —
     restore completes despite the bad row instead of wedging on it forever.
  Rehydrate itself is `AgentSession(id=<archived id>, **other_fields).save()` per row —
  idempotent on the preserved `id`, so a resumed loop simply re-writes already-present rows
  with identical state. The net invariant: restore either completes (sentinel cleared, poison
  rows quarantined) or is cleanly declared wedged after a bounded number of attempts, and at
  **no** point can a persisted flag override a live, populated Redis.
- **Startup ordering.** Restore runs **below the `if dry_run: return` guard**
  (`worker/__main__.py:885-890`) so a `--dry-run` boot never mutates Redis, and **before**
  the Step 1 index rebuild (comment at line 903, `run_cleanup()` at 911) so the rebuild
  indexes the freshly-restored rows (rehydrated rows are not queryable by secondary index
  until then). Concretely it slots into the 890→892 gap, ahead of handler registration
  (`register_callbacks`, 892-901) — which also upholds Race Condition #2's invariant that
  no new session can be created during restore. It is exception-isolated (a restore failure
  logs and must not block
  worker startup — a worker that can't start is worse than a worker with an un-restored
  archive).
- **Cadence & tuning constants.** All named, env-overridable constants in
  `agent/constants.py` with grain-of-salt/provisional defaults:
  - `SESSION_ARCHIVE_INTERVAL` (default `300` s) — periodic sweep cadence (matched to the
    heartbeat/health cadence; the loss window between sweeps for non-terminal sessions is
    bounded by this, terminal sessions export immediately so the common case has no window).
  - `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S` (default `2 * SESSION_ARCHIVE_INTERVAL`) — gates
    the doctor/dashboard "healthy" flag.
  - `SESSION_ARCHIVE_BUSY_TIMEOUT_MS` (default `5000`) — busy-timeout for the off-loop
    periodic sweep and the CLI/read connections (matches `analytics/collector.py`'s 5 s).
  - `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` (default `250`) — the **tight** busy-timeout for
    the on-loop terminal `export_session()` write, bounding event-loop stall on WAL-lock
    contention to ~250 ms (Concern #2).
  - `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP` (default `5`) — max whole-restore resume attempts
    before a restore is declared *wedged* and stops bypassing the guard (Blocker fix).
  - `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` (default `3`) — max per-row `.save()` attempts before
    an archived `id` is quarantined as poison and skipped on future resumes (Blocker fix).
- **Test isolation.** Under `PYTEST_CURRENT_TEST`, the periodic daemon thread is not
  started and the module honors a `SESSION_ARCHIVE_DB_PATH` override so tests write to
  `tmp_path`, never `data/session_archive.db`. Restore/export tests run against the
  `redis_test_db` fixture's isolated Redis db.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `export_session()` failure inside `finalize_session` is swallowed with a
      `logger.error/warning` (observable) and the terminal transition still completes —
      test asserts the session reaches its terminal status even when the archive write
      raises (monkeypatch `export_session` to raise; assert status + logged line).
- [ ] `restore_if_empty()` failure at startup logs and returns without raising — test
      asserts `_run_worker` startup proceeds when restore raises (monkeypatch to raise).
- [ ] `get_archive_status()` never raises on a missing/corrupt DB — returns
      `healthy=False`; tested against a nonexistent path and a truncated DB file.
- [ ] **`export_all()` per-row serialization isolation (Concern #1):** with N sessions
      where exactly one raises during serialization (monkeypatch its serialization to
      raise), assert the sweep logs+skips the bad row and still commits the other N−1 rows
      in one transaction — the good rows are all present in the archive and the sweep does
      not abort. Proves the export loop is as fault-tolerant as the restore loop.
- [ ] **On-loop tight busy-timeout (Concern #2):** assert the connection opened by
      `export_session()` sets `PRAGMA busy_timeout` to `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS`
      (the tight bound), distinct from the periodic sweep's `SESSION_ARCHIVE_BUSY_TIMEOUT_MS`
      — so a WAL-lock stall on the event loop is bounded to the tight value.

### Empty/Invalid Input Handling
- [ ] `export_all()` on zero sessions writes an empty snapshot + a `_meta` row with
      `row_count=0` (does not crash on empty `query.all()`).
- [ ] `restore_if_empty()` with an empty archive DB and empty Redis restores nothing
      and returns `{restored: 0}` (no spurious writes).
- [ ] Datetime round-trip: a session with `created_at`/`completed_at`/`scheduled_at`
      set exports and restores with identical timestamps (the fidelity risk).
- [ ] **Key + parent/child graph preservation (Blocker #1):** export a parent session
      and a child whose `parent_agent_session_id` points at the parent's `id`, restore
      both into an empty Redis, and assert (a) each restored row's `id` **equals** the
      original archived `id` (not a regenerated UUID), and (b) the child's
      `parent_agent_session_id` still resolves to the restored parent via
      `AgentSession.query.get(id=parent_id)`. This is distinct from the datetime
      round-trip test and specifically guards against keying on the popped
      `agent_session_id` alias.
- [ ] **Two-thread connection safety (Blocker #2):** invoke `export_session()` from a
      spawned `threading.Thread` while `export_all()` runs on the main thread against the
      same DB path, and assert neither raises `sqlite3.ProgrammingError` and both rows
      land (proves the per-operation-connection model is thread-safe, not just WAL).

### Error State Rendering
- [ ] `dashboard.json.archive` renders `healthy=False` + `last_export_age_s=None` when
      the archive DB is missing (not a crash) — asserted via a TestClient hitting
      `/dashboard.json` with the archive path pointed at a nonexistent file.
- [ ] The `session-archive-freshness` doctor check renders an actionable red result
      when `last_export_age_s` exceeds `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`.

### Restore Guard (the load-bearing case)
- [ ] **Empty Redis → restore runs:** archive has N rows, Redis has zero AgentSession
      records/keys → `restore_if_empty()` returns `{restored: N}` and the rows are
      queryable in Redis afterward.
- [ ] **Partial Redis → restore does NOT run:** archive has N rows, Redis has 1
      pre-existing AgentSession record → `restore_if_empty()` returns
      `{restored: 0, skipped_reason: "redis_has_records"}` and the pre-existing record
      is untouched (assert its fields unchanged — no clobber).
- [ ] **Orphan-index-only Redis → restore does NOT run:** no records via `query.all()`
      but an `AgentSession*` index key exists → skip with
      `skipped_reason="redis_has_keys"`.
- [ ] **Idempotency:** running `restore_if_empty()` twice — the second call sees a
      populated Redis (from the first) and no-ops (`restored: 0`).
- [ ] **Partial-restore recovery (silent-loss guard):** archive has N rows;
      monkeypatch `.save()` (or the per-row rehydrate) to raise on row `k` (0 < k < N) so
      the first `restore_if_empty()` writes only `k` rows and leaves the
      `restore_in_progress` sentinel set. Assert: (a) the archive `_meta.restore_in_progress`
      is still `1` after the interrupted run (not cleared); (b) a **second**
      `restore_if_empty()` call — even though Redis now has `k` records — DETECTS the
      sentinel, RESUMES (because live_count `k` < `expected_row_count` N), and restores all
      N rows; (c) after the successful second run the sentinel is cleared
      (`restore_in_progress == 0`, `restore_complete == 1`). Catches a regression to the
      naive "empty-guard treats partial rows as populated" bug.
- [ ] **Stuck sentinel never clobbers a healthy full Redis (Blocker fix — the central
      invariant):** set `_meta.restore_in_progress = 1` with `expected_row_count = N`, then
      populate Redis with all N live sessions (some deliberately mutated vs. their archived
      copy, and one *deleted* in the archive but present live). Call `restore_if_empty()`
      and assert: (a) it does NOT bypass the guard (live_count N ≥ expected N) →
      `skipped_reason="restore_already_complete"`; (b) `restored == 0`; (c) every live
      session is byte-for-byte unchanged (no re-upsert, no resurrection of the
      archived-but-deleted row); (d) the stale sentinel is cleared. This is the test that
      would catch a regression to the old "bypass the guard whenever the sentinel is set"
      behavior.
- [ ] **Poison-row quarantine + wedged cap (Blocker fix — one bad row cannot wedge
      restore forever):** archive has N rows; monkeypatch the per-row rehydrate so one
      specific `id` **always** raises (a permanently-unrestorable row). Drive
      `restore_if_empty()` repeatedly and assert: (a) after `SESSION_ARCHIVE_ROW_ATTEMPT_CAP`
      failures that `id` is written to `_restore_quarantine` and skipped; (b) the remaining
      N−1 good rows restore and the sentinel clears once `restored + quarantined == N`
      (restore COMPLETES despite the poison row); (c) in the variant where enough rows fail
      to exceed `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`, restore is declared *wedged*
      (`skipped_reason="restore_wedged"`), surfaces on the doctor/dashboard, and STOPS
      bypassing the guard (no unbounded re-upsert-every-boot loop).

### Cold-boot recovery, end-to-end (validation altitude — Concern #4)
- [ ] **Full boot ordering against a wiped Redis** (`tests/integration/test_session_archive_cold_boot.py`,
      CREATE): seed a populated archive DB (parent + child + a datetime-bearing row),
      point Redis at an isolated empty test db, then drive the real worker startup
      restore→rebuild sequence (invoke `restore_if_empty()` then the same
      `run_cleanup()` index rebuild the worker runs at Step 1, in that order) and assert
      the sessions become **queryable via the normal secondary-index paths**
      (`AgentSession.query.filter(status=...)`, `query.get(id=...)`, and the child's
      parent link resolves) — i.e. the rows are not merely written but reachable through
      the index the app actually uses. This proves the ordering claim (restore must
      precede rebuild) end-to-end rather than validating components in isolation; a
      restore that ran *after* the rebuild would leave rows unindexed and this test would
      fail.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` (or the file covering `finalize_session`)
      — UPDATE: add a case asserting the archive hook is called as the last side effect
      after `session.save()` and that a raising archive hook does not break finalize.
      (If no such file exists, this becomes a CREATE under the new test file below.)
- [ ] `tests/conftest.py` — UPDATE (verify-only): confirm the archive module honors the
      `SESSION_ARCHIVE_DB_PATH` override and the pytest no-op guard so no test writes to
      the real `data/session_archive.db`; add a fixture pointing the archive at
      `tmp_path` if not already covered by `redis_test_db`.
- [ ] `tests/unit/test_doctor.py` — UPDATE: add a case for the new
      `session-archive-freshness` doctor check (healthy + stale rendering).
- [ ] New: `tests/unit/test_session_archive.py` — CREATE: export/restore round-trip,
      the four restore-guard cases, **partial-restore recovery via the sentinel**,
      **stuck-sentinel-never-clobbers-a-full-Redis**, **poison-row quarantine + wedged
      cap**, datetime fidelity, **key + parent/child graph preservation**, **two-thread
      connection safety**, **`export_all()` per-row serialization isolation**, **on-loop
      tight busy-timeout**, empty-input handling, `get_archive_status()` shapes, exception
      isolation. (Greenfield — the archive module is new.)
- [ ] New: `tests/integration/test_session_archive_cold_boot.py` — CREATE: the full
      restore→rebuild→query cold-boot recovery test (see Cold-boot recovery above).
      (Greenfield.)
- [ ] New: `tests/unit/test_session_archive_cli.py` — CREATE: `status` and
      `restore --dry-run` exit codes and JSON output; assert there is **no** `export`
      write subcommand and no live-`restore` write path. (Greenfield.)

No existing test asserts AgentSession-to-SQLite behavior today (greenfield module), so
the only *changes* to existing tests are the additive `finalize_session` and doctor
cases above; everything else is new coverage.

## No-Gos

- **Do NOT** make SQLite authoritative or dual-write on the hot path. Redis remains the
  single source of truth; SQLite is written asynchronously/last and read only on a cold
  start.
- **Do NOT** prune the archive to mirror Redis deletions. The archive is a durability
  floor (a superset); a deleted-in-Redis session lingering in SQLite is harmless (the
  restore guard only fires on a totally-empty Redis, and stale rows are re-superseded by
  normal operation). Pruning risks racing a legitimate delete against a durability copy.
- **Do NOT** temp-write-then-`os.replace()` the SQLite file (corrupts WAL / races
  readers) — the transaction is the atomic primitive here.
- **Do NOT** key the archive or restore on `agent_session_id`. It is a `@property` alias
  that `_normalize_kwargs` (`models/agent_session.py:850`) *pops and discards*;
  restoring by it regenerates the `AutoKeyField` `id` and dangles every
  `parent_agent_session_id`. Export and restore the real `id`, and pass it explicitly to
  `AgentSession(id=..., ...)` on restore.
- **Do NOT** reuse a single module-level SQLite connection across threads (the way
  `analytics/collector.py` does). Exports come from two threads (daemon + event loop);
  the default `check_same_thread=True` would raise `ProgrammingError`. Use a
  per-operation connection (or `check_same_thread=False` + an explicit `Lock`).
- **Do NOT** run restore above the `if dry_run: return` guard in `_run_worker`. A
  `--dry-run` boot must never mutate Redis; restore belongs below the guard (890→892 gap).
- **Do NOT** expose `export` or live-`restore` write subcommands on the CLI. Writes run
  automatically (daemon + terminal hook / guarded startup); the CLI is read-only
  (`status`, `restore --dry-run`).
- **Do NOT** restore into a partially-populated Redis under any circumstance — the guard
  is all-or-nothing (both `query.all()` empty AND no `AgentSession*` keys).
- **Do NOT** expand scope to `Memory`, message history, or the bloom filter. This is
  AgentSession-only per the issue; other stores are separate durability work.
- **Do NOT** run the periodic export or restore inside the asyncio event loop (both are
  blocking); use the daemon thread + startup step.
- **Do NOT** truncate or omit `payload` fields in v1 (Concern #3). Store each session's
  payload verbatim; a size cap that drops a field silently corrupts the round-trip (a
  restored session missing a field is not the archived session) and is itself a poison-row
  vector for the restore guard. Revisit a size mitigation (incremental-by-`updated_at`
  sweep / compression) only if a *measured* size problem appears — not pre-emptively.
- **Do NOT** let a partial restore be mistaken for a populated Redis. A mid-loop restore
  failure must leave the durable `restore_in_progress` sentinel set so the next boot
  RESUMES and completes the remaining rows — never a silent half-restore that the
  empty-Redis guard permanently no-ops.
- **Do NOT** gate the guard-bypass on the persisted sentinel alone (Blocker). The bypass
  must be re-decided every boot against the **freshly recomputed** live count
  (`len(AgentSession.query.all()) < expected_row_count`); a stuck sentinel can NEVER force a
  re-upsert of an already-populated Redis, or it would overwrite live sessions and
  resurrect deleted ones on every boot. Bound resume attempts
  (`SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`) and quarantine poison rows
  (`SESSION_ARCHIVE_ROW_ATTEMPT_CAP`) so one unrestorable row can never wedge restore forever.
- **Do NOT** commit `data/session_archive.db` (already `.gitignore`d; do not add a
  tracked fixture DB).

## Update System

- **No `scripts/update/run.py` step required.** The archive DB is machine-local and
  auto-created on first export; there is no config to propagate and no cross-machine
  state (each machine's archive mirrors its own local Redis). The `data/` directory
  already exists on every machine and is created by the module if absent.
- **No new dependency** to sync (`sqlite3` is stdlib) — no `pyproject.toml`/lockfile
  propagation beyond the new CLI entry point (below), which flows through the normal
  `pip install -e .` step `/update` already runs.
- **Doctor coverage** — the new `session-archive-freshness` check ships inside
  `tools/doctor.py`, which `/update` and the pre-push hook already invoke, so drift
  (a machine whose worker stopped exporting) is caught with no extra update wiring.
- **Migration for existing installations:** none. On first worker boot after deploy the
  archive is created and back-filled by the first periodic sweep; no manual step.

## Agent Integration

- **New CLI entry point required.** Add `valor-session-archive = "tools.session_archive_cli:main"`
  to `pyproject.toml [project.scripts]` so the agent can invoke it via Bash. The CLI is
  **read-only**: `valor-session-archive status` and `valor-session-archive restore
  --dry-run`. `status` is the only subcommand required for agent reachability; `restore
  --dry-run` is included because it is safe (never writes) and useful for operator
  triage. This is the agent-reachable surface for the new module — the
  `agent/session_archive.py` functions alone are invisible to the agent until wired here.
- **Bridge internal import:** none required. The bridge does not need to call the
  archive directly; export is driven by the worker (daemon thread + `finalize_session`
  hook) and restore by worker startup.
- **Integration test for agent reachability:** `tests/unit/test_session_archive_cli.py`
  verifies the CLI runs end-to-end (the agent's actual invocation path — `status` and
  `restore --dry-run`), and a dashboard test confirms `dashboard.json.archive` is present
  (the agent reads `curl localhost:8500/dashboard.json` for system state).

## Documentation

- [ ] Update `docs/features/redis-durability.md` — replace the roadmap-only "Fix #2"
      row in the "Deferred Durability Work" table with an **implemented** section
      documenting: the SQLite archive location, the export cadence + terminal hook, the
      empty-Redis restore guard (with the precise both-must-be-empty rule, the
      `id`-key-preservation note, and the `restore_in_progress` sentinel + **live-count
      guard-bypass gate** + poison-row quarantine that make a partial restore resumable
      without ever clobbering a repopulated Redis or wedging on one bad row), the
      dashboard/doctor freshness surfaces, and the
      operational runbook (how to inspect the archive via `valor-session-archive status`
      and dry-run a restore via `valor-session-archive restore --dry-run` — noting that
      export and live restore are automatic, not manual CLI actions).
- [ ] Add a `## Knowledge Base` / cross-link entry in `docs/features/README.md` index if
      the durability doc is not already listed there (verify during build).
- [ ] Add a `valor-session-archive` row to the CLI table in `CLAUDE.md` Quick Commands
      (`status`, `restore --dry-run`).

## Success Criteria

1. `agent/session_archive.py` exists with `export_session`, `export_all`,
   `restore_if_empty`, `get_archive_status`.
2. `finalize_session` calls `export_session` as its last side effect, after
   `session.save()`, exception-isolated — verified by test and by grep.
3. A `worker-session-archive` daemon thread runs `export_all()` on the
   `SESSION_ARCHIVE_INTERVAL` cadence.
4. `restore_if_empty()` rehydrates on a provably-empty Redis and is a **no-op** on a
   partial Redis — all four guard cases pass in tests.
5. Restore is idempotent (second run no-ops).
5b. **Restore has no silent partial-loss mode:** a restore interrupted mid-loop leaves a
   durable `restore_in_progress` sentinel in the archive DB; the next `restore_if_empty()`
   call DETECTS the sentinel and RESUMES **while the freshly recomputed live count is still
   below `expected_row_count`**, completing the remaining rows — verified by the
   partial-restore-recovery test. A partial restore is never mistaken for a populated Redis.
5c. **A stuck sentinel can never clobber a populated Redis, and one poison row can never
   wedge restore (Blocker fix):** the guard-bypass is re-decided every boot against the live
   `AgentSession.query.all()` count, not the persisted flag, so a healthy full Redis is left
   byte-for-byte untouched even with the sentinel set; resume attempts are bounded
   (`SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`) and a permanently-failing row is quarantined past
   `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` so the sentinel clears on
   `restored + quarantined == expected_row_count` — verified by the
   stuck-sentinel-no-clobber and poison-row-quarantine tests.
6. **Restore preserves the real `id` and the parent/child graph:** a restored session's
   `id` equals its archived `id` (not a regenerated UUID) and a child's
   `parent_agent_session_id` still resolves to its parent — verified by a dedicated test.
7. **Cold-boot recovery works end-to-end:** against a wiped Redis, the real startup
   sequence (`restore_if_empty()` → Step 1 index rebuild) leaves the rehydrated sessions
   **queryable via secondary index** (`query.filter(status=...)`, `query.get(id=...)`,
   and the parent link) — proven by an integration test, not just component tests.
8. `dashboard.json` exposes an `archive` block (`last_export_age_s`, `row_count`,
   `healthy`); `/health` and a `session-archive-freshness` doctor check agree.
9. `valor-session-archive` CLI (`status`, `restore --dry-run`; no write subcommands)
   works and is wired in `pyproject.toml`.
10. `docs/features/redis-durability.md` Fix #2 section documents the implemented store.
11. `python -m ruff check .` and `python -m ruff format --check .` pass; new tests pass.

## Step by Step Tasks

### 1. Archive module (core)
- **Task ID**: build-archive-module
- **Depends On**: none
- **Validates**: tests/unit/test_session_archive.py (create)
- Create `agent/session_archive.py`: `_connect(on_loop=False)` (**per-operation
  connection**, WAL, busy-timeout selected by caller — the tight
  `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` for the on-loop `export_session`, the longer
  `SESSION_ARCHIVE_BUSY_TIMEOUT_MS` for the sweep/reads — `SESSION_ARCHIVE_DB_PATH`
  override, pytest-safe — NOT a shared module-level connection, see Technical Approach on
  the two-writer thread model), `sessions` table keyed on the real **`id`** (not
  `agent_session_id`) + `_meta` table + `_restore_quarantine` table,
  `export_session` (tight on-loop busy-timeout), `export_all` (**per-row `try/except`
  serialization isolation** then a single `BEGIN IMMEDIATE … COMMIT` upsert of the good
  rows), `restore_if_empty` (guard-bypass gated on the **freshly recomputed
  `live_count < expected_row_count`**, never the sentinel alone; resume attempts bounded by
  `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`; per-row failures counted, and an `id` past
  `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` written to `_restore_quarantine` and skipped;
  reconstructs via `AgentSession(id=..., ...)` to preserve the key; clears the sentinel on
  `restored + quarantined == expected_row_count` — see restore-atomicity in Technical
  Approach), `get_archive_status`. The `_meta` table carries the sentinel columns
  (`restore_in_progress`, `restore_complete`, `expected_row_count`, `resume_attempts`).
- Add `SESSION_ARCHIVE_INTERVAL` (300), `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`
  (2×interval), `SESSION_ARCHIVE_BUSY_TIMEOUT_MS` (5000),
  `SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS` (250), `SESSION_ARCHIVE_RESUME_ATTEMPT_CAP` (5),
  and `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` (3) to `agent/constants.py` as env-overridable named
  constants with a grain-of-salt "provisional/tunable" comment. **No
  `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES`** — payloads are stored verbatim (Concern #3).
- Write `tests/unit/test_session_archive.py` (round-trip, 4 guard cases,
  **partial-restore recovery**, **stuck-sentinel-never-clobbers-a-full-Redis**,
  **poison-row quarantine + wedged cap**, datetime fidelity, **key + parent/child graph
  preservation**, **two-thread connection safety**, **`export_all()` per-row serialization
  isolation**, **on-loop tight busy-timeout**, empty input, status shapes, exception
  isolation).

### 2. finalize_session terminal hook
- **Task ID**: build-finalize-hook
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_session_lifecycle.py (update)
- Add the `export_session(session)` call as the LAST side effect in
  `models/session_lifecycle.py::finalize_session` (after line 533, the end of the
  `record_metric` block, before `def transition_status` at 536), inside
  `try/except Exception: logger.warning(...)`. Lazy import to avoid cycles.
- Update/extend the lifecycle test to assert the hook runs after save and a raising
  hook does not break finalize.

### 3. Worker wiring (periodic thread + startup restore)
- **Task ID**: build-worker-wiring
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_session_archive.py (restore path), manual worker boot
- Add the guarded restore call in `_run_worker` **below the `if dry_run: return` guard
  (lines 885-890)** and before the Step 1 index rebuild (comment at line 903,
  `run_cleanup()` at 911) — i.e. in the 890→892 gap, ahead of `register_callbacks`
  (892-901) — exception-isolated. (Placing it above the dry-run guard would mutate Redis
  on a `--dry-run` boot — do NOT.)
- Spawn a `worker-session-archive` daemon thread near the heartbeat thread (line
  1164-1170) that calls `export_all()` every `SESSION_ARCHIVE_INTERVAL`; pytest no-op
  guard.

### 4. Operator surfaces (dashboard + doctor)
- **Task ID**: build-surfaces
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_doctor.py (update), dashboard TestClient test
- Add an `archive` block to `/dashboard.json` and `/health` in `ui/app.py` from
  `get_archive_status()` (mirror `_get_email_health`).
- Add a `session-archive-freshness` check to `tools/doctor.py`.

### 5. CLI + agent integration
- **Task ID**: build-cli
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_session_archive_cli.py (create)
- Create `tools/session_archive_cli.py` with **read-only** subcommands only: `status`
  and `restore --dry-run`. No `export` or live-`restore` write subcommands (writes run
  automatically via the daemon thread + terminal hook / guarded startup).
- Wire `valor-session-archive` into `pyproject.toml [project.scripts]`.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-archive-module, build-worker-wiring, build-surfaces, build-cli
- Update `docs/features/redis-durability.md` Fix #2 section; add the CLI to `CLAUDE.md`
  Quick Commands; verify the README index entry.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: all above
- Run the Verification table; confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| New unit tests pass | `pytest tests/unit/test_session_archive.py tests/unit/test_session_archive_cli.py -q` | exit code 0 |
| Cold-boot integration test passes | `pytest tests/integration/test_session_archive_cold_boot.py -q` | exit code 0 |
| Lifecycle + doctor tests pass | `pytest tests/unit/test_session_lifecycle.py tests/unit/test_doctor.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Archive module exists | `test -f agent/session_archive.py && echo ok` | output contains `ok` |
| Four public entry points present | `grep -cE "^def export_session|^def export_all|^def restore_if_empty|^def get_archive_status" agent/session_archive.py` | output is `4` (uses `grep -E` with real `|` alternation — a bare-`grep` `\|` is silently mis-parsed as a literal pipe on BSD/darwin) |
| Terminal hook wired | `grep -c "export_session(" models/session_lifecycle.py` | output is `1` (call site; the lazy `from … import export_session` line has no paren and is not counted) |
| Restore wired below dry-run guard, before index rebuild | `grep -nE "restore_if_empty|if dry_run|Step 1: Rebuild" worker/__main__.py` | `restore_if_empty` line is ABOVE the "Step 1: Rebuild" line and BELOW the `if dry_run` guard line (`grep -E`, not bare-`grep` `\|`) |
| Restore reconstructs on the real id | `grep -c "AgentSession(id=" agent/session_archive.py` | output is `1` or more (restore keys on `id`) |
| Restore never reconstructs on the alias | `grep -cE "AgentSession\(agent_session_id=" agent/session_archive.py` | output is `0` (pipe-free single branch; matches the buggy `AgentSession(agent_session_id=…)` reconstruction but NOT the required `parent_agent_session_id`, since that is never preceded by `AgentSession(`) |
| Per-operation connection, no shared module conn | `grep -cE "_sqlite_conn|module-level connection" agent/session_archive.py` | output is `0` (`grep -E` with real `|` — a bare-`grep` `\|` would search for the literal string on BSD/darwin and false-green this negative check at `0`) |
| Periodic thread wired | `grep -c "worker-session-archive" worker/__main__.py` | output is `1` |
| Cadence constant named | `grep -c "SESSION_ARCHIVE_INTERVAL" agent/constants.py` | output greater than 0 |
| On-loop tight busy-timeout named | `grep -c "SESSION_ARCHIVE_ONLOOP_BUSY_TIMEOUT_MS" agent/constants.py` | output greater than 0 |
| Resume + row attempt caps named | `grep -cE "SESSION_ARCHIVE_RESUME_ATTEMPT_CAP|SESSION_ARCHIVE_ROW_ATTEMPT_CAP" agent/constants.py` | output is `2` |
| No payload-size cap (Concern #3 cut) | `grep -rn "SESSION_ARCHIVE_MAX_PAYLOAD_BYTES" agent/` | empty output (the truncation cap was removed) |
| Restore-atomicity sentinel present | `grep -c "restore_in_progress" agent/session_archive.py` | output greater than 0 |
| Poison-row quarantine table present | `grep -c "_restore_quarantine" agent/session_archive.py` | output greater than 0 |
| Guard-bypass gated on live count | `grep -c "expected_row_count" agent/session_archive.py` | output greater than 0 |
| Dashboard field present | `grep -c "\"archive\"" ui/app.py` | output greater than 0 |
| Doctor check present | `grep -c "session-archive-freshness" tools/doctor.py` | output greater than 0 |
| CLI entry point wired | `grep -c "valor-session-archive" pyproject.toml` | output is `1` |
| Doc section implemented | `grep -c "session_archive.db" docs/features/redis-durability.md` | output greater than 0 |
| Archive DB not committed | `git ls-files data/session_archive.db` | empty output |

## Risks

1. **Serialization fidelity (datetime/dict/list round-trip).** A field that survives a
   Redis write but not a JSON→SQLite→JSON→Redis round-trip would restore a subtly-wrong
   session. *Mitigation:* enumerate `_meta.fields` (not a hand-maintained list), convert
   datetimes to ISO-8601 explicitly, store dict/list natively as JSON, and add a
   dedicated datetime round-trip test. Any unmappable field type fails loudly in the
   test, not silently in prod.
2. **Popoto internal coupling + key regeneration.** Reading `session._meta.fields` and
   reconstructing via `AgentSession(id=..., **fields).save()` depends on Popoto internals
   (as does the existing `_saved_field_values` code). The specific trap: `id` is an
   `AutoKeyField` and `_normalize_kwargs` pops the `agent_session_id` alias — restoring by
   the alias would silently regenerate the PK and dangle parent/child links.
   *Mitigation:* archive and restore the real `id` explicitly; one clearly-commented
   coupling point with a "re-verify on Popoto upgrade" note; the key + parent/child graph
   preservation test catches a break loudly.
3. **Cross-thread SQLite misuse.** The archive is written from two threads (periodic
   daemon + event-loop `finalize_session` hook). A shared connection under the default
   `check_same_thread=True` would raise `sqlite3.ProgrammingError` at runtime — a
   false-green in single-thread unit tests. *Mitigation:* per-operation connections
   (no shared connection object) plus an explicit two-thread concurrent-write test that
   would surface any regression to a shared connection.
4. **Export cost at scale.** `export_all()` reads every session each cycle; a large
   backlog could make the sweep slow. *Mitigation:* it runs off-loop in a daemon thread
   on a 5-min cadence, single transaction; terminal sessions are the common case and are
   covered by the fast single-row hook. If the full-set count grows large, the interval
   is env-tunable and a future incremental-by-`updated_at` sweep is a clean follow-up.
5. **False-green freshness.** A daemon thread that dies silently would let the archive
   go stale unnoticed. *Mitigation:* the `session-archive-freshness` doctor check +
   `dashboard.json.archive.healthy` surface staleness (age > threshold), mirroring the
   email/heartbeat pattern the issue explicitly asks us to follow.
6. **Restore-atomicity failure modes — silent half-loss AND stuck-sentinel clobber.**
   Two opposite failures live here. **(a) Silent partial loss:** if `.save()` raises partway
   through the rehydrate loop (row 51 of 100), Redis is left half-populated and a naive
   next-boot guard would see the 50 rows and permanently drop the other 50. **(b) Stuck-
   sentinel clobber (the Blocker the critique caught):** if the sentinel clears only on
   `restored == expected_row_count`, one permanently-unrestorable row keeps the sentinel set
   forever, so *every* later boot — including a healthy boot against a full Redis — bypasses
   the guard and re-upserts every archived row by preserved id, overwriting live sessions and
   resurrecting deleted ones. *Mitigation (both closed together):* a durable
   `restore_in_progress` sentinel in the archive `_meta` table brackets the loop, but the
   **guard-bypass is re-decided every boot against the freshly recomputed live count**
   (`live_count < expected_row_count`), never on the flag alone — so a stuck sentinel can
   never clobber an already-whole Redis (mode b). Resume attempts are bounded
   (`SESSION_ARCHIVE_RESUME_ATTEMPT_CAP`) and a row failing past
   `SESSION_ARCHIVE_ROW_ATTEMPT_CAP` is quarantined and skipped, so the sentinel clears on
   `restored + quarantined == expected_row_count` and one poison row can never wedge restore
   (mode b). Per-row failures are counted and do not silently abort the batch, and a
   still-set sentinel with `live_count < expected` DETECTS and RESUMES on the next boot (mode
   a). Three dedicated tests drive each path (partial-restore recovery, stuck-sentinel-no-
   clobber, poison-row quarantine + wedged cap). See the restore-atomicity mechanism in
   Technical Approach and Success Criterion #5b.
7. **Large per-session payload blobs (accepted, not truncated).** A session with a very
   large `DictField`/`ListField` conversation history serializes to a large JSON `payload`
   column. *Decision (Concern #3):* v1 stores it **verbatim** — truncation is speculative
   and silently corrupts round-trip fidelity (a restored session missing a field is not the
   archived session), and a half-serialized row is itself a poison-row vector for the restore
   guard. No size problem has been observed; the sweep is off-loop on a 5-min cadence and the
   interval is env-tunable (Risk #4). If a *measured* size problem ever appears, the clean
   mitigation is an incremental-by-`updated_at` sweep or a compression pass — a follow-up,
   revisited then. See the No-Go on payload truncation.

## Race Conditions

1. **Concurrent export writers (periodic thread vs. terminal hook), on DIFFERENT threads.**
   `export_all()` (periodic daemon thread) and `export_session()` (event-loop
   `finalize_session` hook) can run at the same instant against the same DB **from two
   different threads**. Two distinct mechanisms are needed and both are provided:
   (a) **thread-affinity** — each operation opens its **own** connection (no shared
   `sqlite3.Connection` reused across threads), so no `ProgrammingError`; and (b)
   **write serialization** — SQLite WAL + a busy-timeout serializes those two
   independent connections at the DB level, so worst case one waits briefly for the
   other. Note (a) and (b) are orthogonal: WAL alone does **not** make a single
   connection thread-safe — (a) is the reason there is no shared connection object.
   No read-modify-write across the two — each upserts by primary key (`id`), so ordering
   is irrelevant (last writer for a given row wins, and both write the same authoritative
   Redis state).
2. **Restore vs. incoming enqueue at startup.** A message could arrive and create a new
   AgentSession while `restore_if_empty()` is mid-flight. *Mitigation:* restore runs in
   the 890→892 gap of `_run_worker` — after the dry-run guard but **before**
   callbacks/handlers are registered (`register_callbacks`, lines 892-901), so no new
   session can be created during the guard evaluation or the rehydrate. The guard is also
   evaluated once, atomically-read, before any write.
3. **Dashboard/CLI reading the DB during a write.** *Mitigation:* WAL mode gives readers
   a consistent snapshot without blocking the writer; `get_archive_status()` opens its
   own read connection and never holds a write lock.

## Rabbit Holes

- **Making SQLite a true replica (pruning, deletes, bidirectional sync).** Do NOT. The
  archive is a one-way durability floor read only on a cold start. Full replication is
  Fix #5 (already shipped) territory, not this.
- **Hand-maintaining the exported field list.** Do NOT enumerate fields manually — it
  will drift as `AgentSession` gains fields (it has many). Enumerate `_meta.fields`.
- **A pluggable storage backend / ORM abstraction over SQLite.** Over-engineering for a
  single-table, single-purpose archive. One module, stdlib `sqlite3`, done.
- **Incremental/delta export by `updated_at` in v1.** Tempting for cost, but adds
  tombstone/consistency complexity. Ship the full-sweep + terminal-hook first; the
  interval is tunable and delta export is a clean follow-up if the sweep ever hurts.
- **Restoring child/parent graph ordering explicitly.** Because each row is restored with
  its **real preserved `id`**, `parent_agent_session_id` links resolve regardless of the
  order rows are saved in. Popoto reconstructs indexes on `.save()` + the Step 1 rebuild;
  do not build a topological restore — just save every row (with its original `id`) and
  let the existing recovery/index machinery sort it out. (This is why key preservation,
  not ordering, is the load-bearing requirement.)
- **Cross-machine archive sharing.** Each machine's archive mirrors its own Redis; do
  not attempt to sync archives (would violate single-machine-ownership and could restore
  another machine's sessions).
