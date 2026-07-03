---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1825
last_comment_id:
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

**Baseline commit:** `b99e295821573d011c2981c401c8977ee87fe045` (main, plan time)
**Issue filed at:** #1825, part of the #1818 resilience cluster; #1814 merged as PR #1824.
**Disposition:** Anchors re-verified at HEAD during the post-critique revision. Two
worker startup anchors were corrected (the `if dry_run: return` guard at 746-751 and the
`register_callbacks`/index-rebuild layout at 753-764) so restore lands below the dry-run
guard; the heartbeat-thread anchor was tightened to 1003-1005. The `id`/`AutoKeyField`
key-preservation fact and the two-writer thread model were added below. Second revision
(critique round 2): confirmed the dead-worker-sweep ordering anchors
(`_sweep_dead_worker_sessions` at `agent/session_health.py:835`,
`_recover_interrupted_agent_sessions_startup` at line 599) for the stale-`claude_pid`
note; verified the lazy-import shape in `session_lifecycle.py` (`from agent.session_archive
import export_session` + call = the `export_session(` call-site grep), and empirically
validated the two `agent_session_id`-key greps against both correct and buggy simulated
code (the negative check is deliberately pipe-free — a `\|` alternation is silently
mis-parsed as a literal pipe by BSD `grep -E` once markdown-escaped, which would false-green).

**File:line references verified at HEAD (`b99e2958`):**
- `models/session_lifecycle.py:221` — `finalize_session(...)` signature. Confirmed.
  The terminal write is `session.save()` at **line 474**; the function's last side
  effects are the defensive `srem` (476-498), the `TaskTypeProfile` update (500-510),
  and the analytics `record_metric` block (512-525). The archive hook belongs **after
  line 525** (the current end of the function body), so it runs strictly after the
  authoritative Redis save. Confirmed `TERMINAL_STATUSES` at line 61 and `ALL_STATUSES`
  at line 97.
- `worker/__main__.py:695-703` — "Redis connection verified" block (`AgentSession.query.filter(status="pending")`).
  Re-verified at HEAD.
- `worker/__main__.py:746-751` — the `if dry_run: ... return` guard. **Restore MUST run
  below this guard**, not after line 703, or a `--dry-run` boot against an empty Redis
  would rehydrate/mutate Redis (a dry run must never write). Re-verified at HEAD.
- `worker/__main__.py:753-762` — callback/handler registration (`register_callbacks`).
  Restore slots into the **751→753 gap**: after the dry-run `return` and **before**
  handler registration and the Step 1 index rebuild. This keeps Race Condition #2's
  invariant (restore completes before any handler can create a new session) while
  respecting the dry-run guard. Re-verified at HEAD.
- `worker/__main__.py:764` — Step 1 index rebuild (`run_cleanup()`); restore precedes it
  so the rebuild reindexes the freshly-restored rows (they are otherwise not queryable —
  see the cold-boot success criterion). Re-verified at HEAD.
- `worker/__main__.py:1003-1005` — heartbeat daemon thread spawn
  (`threading.Thread(target=_heartbeat_thread_main, name="worker-heartbeat", daemon=True)`).
  The export daemon thread is spawned adjacent to it using the identical pattern.
  Re-verified at HEAD.
- `agent/session_health.py:3001-3011` — `_write_worker_heartbeat()` uses the
  write-temp-then-`os.replace()` atomic file pattern. This is the in-repo precedent for
  atomic writes (though the archive uses a SQLite transaction instead — see Technical
  Approach for why). Confirmed.
- `analytics/collector.py:9-31` — existing in-repo SQLite usage: `sqlite3`,
  `_DB_DIR = Path(__file__).parent.parent / "data"`, `PRAGMA journal_mode=WAL`,
  `_SQLITE_TIMEOUT = 5`, **and a single module-level `_sqlite_conn` opened with the
  default `check_same_thread=True`** (line 30-31). The archive borrows the file/WAL/
  timeout shape but **must NOT** copy the single-shared-connection detail: analytics is
  written from one thread, whereas this archive is written from **two** (see the
  two-writer note below). Re-verified at HEAD.
- `models/agent_session.py:137` — `id = AutoKeyField()` is the **real persisted key**;
  `agent_session_id` (lines 1124-1132) is only a `@property`/setter alias. Critically,
  `_normalize_kwargs` at **lines 805-806** *pops and discards* any `agent_session_id`
  kwarg (`# AutoKeyField, ignore`), while an explicit `id=` kwarg is honored. Verified
  empirically at HEAD: `AgentSession(id=X, ...).save()` preserves the key and
  `query.get(id=X)` finds it; `AgentSession(agent_session_id=X, ...)` regenerates a fresh
  UUID (X is dropped). AutoKeyField mints a new value only when `id` is empty
  (`auto_field_mixin.py:285-301`). **This is the crux of Blocker #1**: export/restore
  must key on `id`, not `agent_session_id`, or the key regenerates on restore and every
  `parent_agent_session_id` link dangles.
- **Two-writer thread model (Blocker #2, verified by design):** `export_all()` runs on
  the periodic **daemon thread**; `export_session()` runs inside `finalize_session`,
  which executes on the **asyncio event-loop thread**. SQLite connection objects are
  thread-affine under the default `check_same_thread=True` — sharing one module-level
  connection across these two threads raises `sqlite3.ProgrammingError`. WAL +
  busy-timeout do NOT fix this (they serialize *separate* connections/processes, not
  cross-thread reuse of one object). Mitigation stated in Technical Approach / Risks /
  Race Conditions.
- `ui/app.py:507-548` — `/dashboard.json` route; `_get_email_health()` (388-421) is the
  freshness-block template to mirror; `_session_to_json` at 424. Confirmed.
- `models/agent_session.py:84-336` — `AgentSession` field set (all `Field`/`KeyField`/
  `IndexedField`/`DatetimeField`/`DictField`/`ListField` declarations), with the key
  `id = AutoKeyField()` at line 137. `query.all()` used at line 988. Confirmed.
- `.gitignore:171-181` — `*.db`, `*.db-shm`, `*.db-wal`, and `data/` are already
  ignored, so `data/session_archive.db` and its WAL sidecars are never committed. Confirmed.

**Commits on main since #1814 merged (touching referenced files):** the only change to
`models/session_lifecycle.py` / `worker/__main__.py` since is unrelated (calendar
feature `b99e2958`); the `finalize_session` chokepoint and the startup sequence are
intact. Premises hold.

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
(`models/agent_session.py:805-806`) pops it, and Popoto mints a fresh UUID, orphaning
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
   return` guard** (`worker/__main__.py:746-751`) and **before** the Step 1 index rebuild
   (line 764) — i.e. in the 751→753 gap. Placing it below the dry-run guard is
   load-bearing: a `--dry-run` boot must observe/report but never mutate Redis, so restore
   (which writes) cannot run above the guard. The function checks the empty-Redis guard
   (below); on a provably-empty Redis (or when a prior partial restore left the
   `restore_in_progress` sentinel set) it sets/keeps the sentinel, rehydrates every
   archived row back into Redis via
   `AgentSession(id=<archived id>, **other_fields).save()` — **the archived `id` is passed
   explicitly** so the AutoKey is preserved and parent/child links stay intact (never the
   `agent_session_id` alias, which `_normalize_kwargs` drops) — and clears the sentinel
   only after every row is restored (see the restore-atomicity mechanism in Technical
   Approach). On any non-empty Redis with no pending sentinel it logs and returns without
   touching Redis. Rehydrated rows are not queryable by secondary index until the Step 1
   rebuild runs immediately after, which is why restore must precede it.

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
    `restore_complete`, `expected_row_count`).
  - `export_session(session)` — serialize one session's full field set (keyed on the
    real `id`, with the `payload` blob subject to the `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES`
    bound — see Serialization fidelity), upsert one row in a single transaction on its own
    connection, refresh `_meta` (`kind="terminal"`). Fast, single-row.
  - `export_all()` — `AgentSession.query.all()` → upsert every row in **one**
    transaction on its own connection, refresh `_meta` (`kind="periodic"`). Rows present
    in the DB but absent from Redis are **retained** (the archive is a superset floor,
    never prunes live-set deletions — deletion is not a durability event we want to
    propagate on a cold start).
  - `restore_if_empty()` — apply the empty-Redis guard (OR detect a still-set
    `restore_in_progress` sentinel from a prior interrupted restore); on pass, set the
    sentinel, iterate archived rows and `AgentSession(id=<archived id>, **other_fields).save()`
    each — **the archived `id` is passed explicitly so the AutoKey is preserved** (verified:
    an explicit `id=` survives save; the `agent_session_id` alias is dropped by
    `_normalize_kwargs`) — and clear the sentinel only after all rows are restored. A
    mid-loop failure leaves the sentinel set so the next boot resumes (idempotent upserts on
    the preserved `id`); the empty-Redis guard is bypassed for that one resume case so a
    partial restore is never mistaken for a populated Redis (see restore-atomicity in
    Technical Approach). Idempotent — re-running on a now-populated Redis with no pending
    sentinel fails the guard and no-ops. Returns a structured result
    `{restored: int, skipped_reason: str|None, resumed: bool}`.
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
- **Key preservation on restore.** The archive stores the real `id` (`AutoKeyField`) as
  the primary column and inside the JSON payload, and restore reconstructs via
  `AgentSession(id=<archived id>, **other_fields).save()`. This is mandatory: verified at
  HEAD, an explicit `id=` is honored and preserved through `save()`, whereas the
  `agent_session_id` alias is popped by `_normalize_kwargs` (`models/agent_session.py:805-806`)
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
  dict/list fields as JSON-native. The whole dict is stored as a JSON `payload` column
  (including the real `id`), with `id`/`session_id`/`project_key`/`status`/`updated_at`
  promoted to real columns for queryability and the restore ordering. On restore, ISO
  strings are parsed back to `datetime` for `DatetimeField`/`SortedField`/`created_at`,
  and the row is reconstructed via `AgentSession(id=<archived id>, **other_fields).save()`
  so the key is preserved (see Key preservation above). The datetime round-trip and the
  key/parent-child-graph preservation are the two fidelity risks and each gets a
  dedicated test.
- **Payload size bound.** The serialized JSON `payload` is bounded by
  `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` (named, env-overridable constant in
  `agent/constants.py`, provisional/tunable default, grain-of-salt comment). A payload
  exceeding the bound — typically a session with an unusually large `DictField`/`ListField`
  conversation history — is logged (`logger.warning` with the session `id` and byte size)
  and stored with the oversized field(s) truncated/omitted; the `id` and promoted columns
  are always preserved so the session (and its parent/child link) still round-trips. This
  prevents a single pathological session from ballooning the archive DB and slowing the
  sweep (bounds Risk #4 along the size dimension; see the No-Go on unbounded payloads).
- **Empty-Redis guard (exact).** In `restore_if_empty()`:
  1. `records = list(AgentSession.query.all())`; if `records` is non-empty **AND** no
     partial-restore sentinel is set → skip (`skipped_reason="redis_has_records"`).
     (The sentinel exception is the resume path below.)
  2. Bounded `SCAN` (`count=1000`, capped iterations) for `AgentSession*` keys via the
     Popoto client; if any key found (and no sentinel) → skip
     (`skipped_reason="redis_has_keys"`).
  3. Log `DBSIZE` as advisory (`DBSIZE==0` confirms a true cold start) — informational,
     not a gate.
  4. Only when 1 AND 2 are both empty (or a partial-restore sentinel is present):
     rehydrate. Guard is evaluated **once**, before any write; the whole function is a
     no-op on any non-empty Redis that has no pending sentinel.
- **Restore atomicity — partial-restore is detected and resumed, never silently
  half-completed.** A naive per-row loop with only whole-function exception isolation has
  a silent-data-loss failure mode: if `.save()` raises on row 51 of 100, Redis ends up
  with 50 records, and the **next** boot's empty-Redis guard sees those 50 rows, concludes
  "Redis already populated," and permanently no-ops the remaining 50 — indistinguishable
  from a clean restore. To close this, restore is bracketed by a **restore-in-progress
  sentinel** stored in the archive DB `_meta` table (single-file source of truth, survives
  a Redis wipe because it lives in SQLite, not Redis):
  1. Before writing the first row, set `_meta.restore_in_progress = 1` (with the total
     `expected_row_count`). This is a durable marker in the archive DB itself.
  2. Rehydrate every row via `AgentSession(id=<archived id>, **other_fields).save()`.
     Per-row failures are counted but do **not** silently abort the whole set — the loop
     records each failure and continues, so one bad row cannot strand the rest.
  3. Only after every row is attempted and the restored count matches
     `expected_row_count` (no per-row failures): clear the sentinel
     (`restore_in_progress = 0`) and stamp `restore_complete = 1`. If any row failed or the
     process died mid-loop, the sentinel stays set.
  4. **On the next boot**, `restore_if_empty()` first reads the sentinel. If
     `restore_in_progress == 1` (a prior restore was interrupted or partial), it treats
     Redis as "restore-not-finished" and **resumes** the rehydrate — re-running the loop is
     safe because `.save()` on the preserved `id` is an idempotent upsert (rows already
     present are simply re-written with identical state). The empty-Redis guard is
     **bypassed** in this one case precisely because the partial rows are ours, mid-restore.
     A partial restore is therefore always DETECTED and COMPLETED, never mistaken for a
     populated Redis.
  This makes restore effectively all-or-nothing at the boot boundary: either it completes
  (sentinel cleared) or the next boot finishes it (sentinel still set). The sentinel lives
  in the durable archive, so even a crash between rows is recoverable.
- **Startup ordering.** Restore runs **below the `if dry_run: return` guard**
  (`worker/__main__.py:746-751`) so a `--dry-run` boot never mutates Redis, and **before**
  the Step 1 index rebuild (line 764) so the rebuild indexes the freshly-restored rows
  (rehydrated rows are not queryable by secondary index until then). Concretely it slots
  into the 751→753 gap, ahead of handler registration (`register_callbacks`, 753-762) —
  which also upholds Race Condition #2's invariant that no new session can be created
  during restore. It is exception-isolated (a restore failure logs and must not block
  worker startup — a worker that can't start is worse than a worker with an un-restored
  archive).
- **Cadence.** `SESSION_ARCHIVE_INTERVAL` is a named, env-overridable constant in
  `agent/constants.py`, default `300` seconds — a provisional/tunable value (grain of
  salt: matched to the existing heartbeat/health cadence; loss window between periodic
  sweeps for non-terminal sessions is bounded by this, terminal sessions are exported
  immediately so the common case has no window). `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`
  (default `2 * SESSION_ARCHIVE_INTERVAL`) gates the doctor/dashboard "healthy" flag.
  `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` (provisional/tunable default, grain of salt) bounds
  the per-session JSON payload blob so an outsized conversation history can't balloon the
  archive DB (see Serialization fidelity and Risk #7).
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
- [ ] **Partial-restore recovery (Concern #3 — silent loss guard):** archive has N rows;
      monkeypatch `.save()` (or the per-row rehydrate) to raise on row `k` (0 < k < N) so
      the first `restore_if_empty()` writes only `k` rows and leaves the
      `restore_in_progress` sentinel set. Assert: (a) the archive `_meta.restore_in_progress`
      is still `1` after the interrupted run (not cleared); (b) a **second**
      `restore_if_empty()` call — even though Redis now has `k` records — DETECTS the
      sentinel, RESUMES, and restores all N rows (does NOT no-op via the empty-Redis guard);
      (c) after the successful second run the sentinel is cleared
      (`restore_in_progress == 0`, `restore_complete == 1`). This is the test that would
      catch a regression to the naive "empty-guard treats partial rows as populated" bug.

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
      the four restore-guard cases, **partial-restore recovery via the sentinel**, datetime
      fidelity, **key + parent/child graph preservation**, **two-thread connection safety**,
      oversized-payload bound, empty-input handling, `get_archive_status()` shapes,
      exception isolation. (Greenfield — the archive module is new.)
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
  that `_normalize_kwargs` (`models/agent_session.py:805-806`) *pops and discards*;
  restoring by it regenerates the `AutoKeyField` `id` and dangles every
  `parent_agent_session_id`. Export and restore the real `id`, and pass it explicitly to
  `AgentSession(id=..., ...)` on restore.
- **Do NOT** reuse a single module-level SQLite connection across threads (the way
  `analytics/collector.py` does). Exports come from two threads (daemon + event loop);
  the default `check_same_thread=True` would raise `ProgrammingError`. Use a
  per-operation connection (or `check_same_thread=False` + an explicit `Lock`).
- **Do NOT** run restore above the `if dry_run: return` guard in `_run_worker`. A
  `--dry-run` boot must never mutate Redis; restore belongs below the guard (751→753 gap).
- **Do NOT** expose `export` or live-`restore` write subcommands on the CLI. Writes run
  automatically (daemon + terminal hook / guarded startup); the CLI is read-only
  (`status`, `restore --dry-run`).
- **Do NOT** restore into a partially-populated Redis under any circumstance — the guard
  is all-or-nothing (both `query.all()` empty AND no `AgentSession*` keys).
- **Do NOT** expand scope to `Memory`, message history, or the bloom filter. This is
  AgentSession-only per the issue; other stores are separate durability work.
- **Do NOT** run the periodic export or restore inside the asyncio event loop (both are
  blocking); use the daemon thread + startup step.
- **Do NOT** store an unbounded per-session `payload` blob. Bound it with
  `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES`; an oversized conversation history is logged and its
  outsized field truncated/omitted (id + promoted columns always preserved), never written
  whole so it balloons the archive DB.
- **Do NOT** let a partial restore be mistaken for a populated Redis. A mid-loop restore
  failure must leave the durable `restore_in_progress` sentinel set so the next boot
  RESUMES and completes the remaining rows — never a silent half-restore that the
  empty-Redis guard permanently no-ops.
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
      `id`-key-preservation note, and the `restore_in_progress` sentinel that makes a
      partial restore resumable rather than silently half-completed), the dashboard/doctor
      freshness surfaces, and the
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
   call DETECTS the sentinel, RESUMES (bypassing the empty-Redis guard for that one case),
   and completes the remaining rows — verified by the partial-restore-recovery test. A
   partial restore is never mistaken for a populated Redis.
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
- Create `agent/session_archive.py`: `_connect()` (**per-operation connection**, WAL,
  busy-timeout, `SESSION_ARCHIVE_DB_PATH` override, pytest-safe — NOT a shared
  module-level connection, see Technical Approach on the two-writer thread model),
  `sessions` table keyed on the real **`id`** (not `agent_session_id`) + `_meta` table,
  `export_session`, `export_all` (single-transaction upsert), `restore_if_empty`
  (both-empty guard OR resume-on-`restore_in_progress`-sentinel; reconstructs via
  `AgentSession(id=..., ...)` to preserve the key; sets the sentinel before the first
  write and clears it only after all rows restore — see restore-atomicity in Technical
  Approach), `get_archive_status`. The `_meta` table carries the sentinel columns
  (`restore_in_progress`, `restore_complete`, `expected_row_count`).
- Add `SESSION_ARCHIVE_INTERVAL` (300), `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`
  (2×interval), and `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` (provisional default) to
  `agent/constants.py` as env-overridable named constants with a grain-of-salt
  "provisional/tunable" comment.
- Write `tests/unit/test_session_archive.py` (round-trip, 4 guard cases,
  **partial-restore recovery via the sentinel**, datetime fidelity, **key + parent/child
  graph preservation**, **two-thread connection safety**, oversized-payload bound, empty
  input, status shapes, exception isolation).

### 2. finalize_session terminal hook
- **Task ID**: build-finalize-hook
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_session_lifecycle.py (update)
- Add the `export_session(session)` call as the LAST side effect in
  `models/session_lifecycle.py::finalize_session` (after line 525), inside
  `try/except Exception: logger.warning(...)`. Lazy import to avoid cycles.
- Update/extend the lifecycle test to assert the hook runs after save and a raising
  hook does not break finalize.

### 3. Worker wiring (periodic thread + startup restore)
- **Task ID**: build-worker-wiring
- **Depends On**: build-archive-module
- **Validates**: tests/unit/test_session_archive.py (restore path), manual worker boot
- Add the guarded restore call in `_run_worker` **below the `if dry_run: return` guard
  (lines 746-751)** and before the Step 1 index rebuild (line 764) — i.e. in the 751→753
  gap, ahead of `register_callbacks` — exception-isolated. (Placing it after line 703
  would mutate Redis on a `--dry-run` boot — do NOT.)
- Spawn a `worker-session-archive` daemon thread near the heartbeat thread (line
  1003-1005) that calls `export_all()` every `SESSION_ARCHIVE_INTERVAL`; pytest no-op
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
| Four public entry points present | `grep -c "^def export_session\|^def export_all\|^def restore_if_empty\|^def get_archive_status" agent/session_archive.py` | output is `4` |
| Terminal hook wired | `grep -c "export_session(" models/session_lifecycle.py` | output is `1` (call site; the lazy `from … import export_session` line has no paren and is not counted) |
| Restore wired below dry-run guard, before index rebuild | `grep -n "restore_if_empty\|if dry_run\|Step 1: Rebuild" worker/__main__.py` | `restore_if_empty` line is ABOVE the "Step 1: Rebuild" line and BELOW the `if dry_run` guard line |
| Restore reconstructs on the real id | `grep -c "AgentSession(id=" agent/session_archive.py` | output is `1` or more (restore keys on `id`) |
| Restore never reconstructs on the alias | `grep -cE "AgentSession\(agent_session_id=" agent/session_archive.py` | output is `0` (pipe-free single branch; matches the buggy `AgentSession(agent_session_id=…)` reconstruction but NOT the required `parent_agent_session_id`, since that is never preceded by `AgentSession(`) |
| Per-operation connection, no shared module conn | `grep -c "_sqlite_conn\|module-level connection" agent/session_archive.py` | output is `0` |
| Periodic thread wired | `grep -c "worker-session-archive" worker/__main__.py` | output is `1` |
| Cadence constant named | `grep -c "SESSION_ARCHIVE_INTERVAL" agent/constants.py` | output greater than 0 |
| Payload-size bound named | `grep -c "SESSION_ARCHIVE_MAX_PAYLOAD_BYTES" agent/constants.py` | output greater than 0 |
| Restore-atomicity sentinel present | `grep -c "restore_in_progress" agent/session_archive.py` | output greater than 0 |
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
6. **Silent partial-restore data loss.** If `.save()` raises partway through the
   rehydrate loop (row 51 of 100), Redis is left half-populated; a naive design's next-boot
   empty-Redis guard would see the 50 written rows, assume "already restored," and
   permanently drop the remaining 50 — silent, irrecoverable loss indistinguishable from a
   clean restore. *Mitigation:* a durable `restore_in_progress` sentinel in the archive DB
   `_meta` table brackets the loop; it is set before the first write and cleared only after
   all rows are restored. On the next boot a still-set sentinel is DETECTED and the restore
   is RESUMED (idempotent upserts on the preserved `id`), bypassing the empty-Redis guard
   for that one case. Per-row failures are counted and do not silently abort the batch. A
   dedicated partial-restore-recovery test drives a mid-loop failure and asserts the next
   boot completes the remaining rows. See the restore-atomicity mechanism in Technical
   Approach and Success Criterion #5b.
7. **Oversized per-session payload blob.** A session with a very large `DictField`/
   `ListField` conversation history serializes to a large JSON `payload` column; an
   unbounded blob could bloat the archive DB and slow the sweep. *Mitigation:* a
   `SESSION_ARCHIVE_MAX_PAYLOAD_BYTES` guard (named, env-overridable constant, provisional
   default) — a payload exceeding the bound is logged (`logger.warning` with the session
   `id` and size) and the row is stored with the oversized field truncated/omitted rather
   than silently ballooning the DB; the session is still archived (id + promoted columns
   preserved) so restore of the graph skeleton is unaffected. See the No-Go on unbounded
   payloads. This bounds Risk #4 along the size dimension.

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
   the 751→753 gap of `_run_worker` — after the dry-run guard but **before**
   callbacks/handlers are registered (`register_callbacks`, lines 753-762), so no new
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
