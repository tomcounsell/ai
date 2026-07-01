---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-01
tracking: https://github.com/tomcounsell/ai/issues/1825
last_comment_id:
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
- `valor-session-archive` CLI (`export` / `restore` / `status` subcommands) for agent
  reachability and manual ops.
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
**Disposition:** Unchanged — all cited file:line anchors re-verified at HEAD below.

**File:line references verified at HEAD (`b99e2958`):**
- `models/session_lifecycle.py:221` — `finalize_session(...)` signature. Confirmed.
  The terminal write is `session.save()` at **line 474**; the function's last side
  effects are the defensive `srem` (476-498), the `TaskTypeProfile` update (500-510),
  and the analytics `record_metric` block (512-525). The archive hook belongs **after
  line 525** (the current end of the function body), so it runs strictly after the
  authoritative Redis save. Confirmed `TERMINAL_STATUSES` at line 61 and `ALL_STATUSES`
  at line 97.
- `worker/__main__.py:695-703` — "Redis connection verified" block (`AgentSession.query.filter(status="pending")`).
  The guarded restore step (new "Step 0") slots in **immediately after line 703** and
  **before** the Step 1 index rebuild at line 764. Confirmed.
- `worker/__main__.py:998-1008` — heartbeat daemon thread spawn
  (`threading.Thread(target=_heartbeat_thread_main, daemon=True)`). The export daemon
  thread is spawned adjacent to it using the identical pattern. Confirmed.
- `agent/session_health.py:3001-3011` — `_write_worker_heartbeat()` uses the
  write-temp-then-`os.replace()` atomic file pattern. This is the in-repo precedent for
  atomic writes (though the archive uses a SQLite transaction instead — see Technical
  Approach for why). Confirmed.
- `analytics/collector.py:9-31` — existing in-repo SQLite usage: `sqlite3`,
  `_DB_DIR = Path(__file__).parent.parent / "data"`, `PRAGMA journal_mode=WAL`,
  module-level connection, `_SQLITE_TIMEOUT = 5`. This is the pattern the archive
  mirrors. Confirmed.
- `ui/app.py:507-548` — `/dashboard.json` route; `_get_email_health()` (388-421) is the
  freshness-block template to mirror; `_session_to_json` at 424. Confirmed.
- `models/agent_session.py:84-336` — `AgentSession` field set (all `Field`/`KeyField`/
  `IndexedField`/`DatetimeField`/`DictField`/`ListField` declarations). `query.all()`
  used at line 988. Confirmed.
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
field map, and reconstruction is `AgentSession(**field_kwargs).save()` (the same path
`async_create` funnels through). Datetime/dict/list fields are the fidelity risk and
are handled explicitly (see Risks and Rabbit Holes).

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
   `_run_worker()` calls `session_archive.restore_if_empty()` right after the Redis
   connection is verified and **before** the index rebuild. The function checks the
   empty-Redis guard (below); on a provably-empty Redis it rehydrates every archived
   row back into Redis via `AgentSession(**fields).save()`; on any non-empty Redis it
   logs and returns without touching Redis.

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
  - `_connect()` — opens/creates `data/session_archive.db` with WAL journal mode and a
    bounded busy-timeout so concurrent reads (dashboard/CLI) never block the writer.
    One `sessions` table (promoted columns `agent_session_id` PK, `session_id`,
    `project_key`, `status`, `updated_at`, plus a `payload` JSON blob holding the full
    `_meta.fields` snapshot) and one `_meta` table (`last_export_ts`, `row_count`,
    `kind`).
  - `export_session(session)` — serialize one session's full field set, upsert one row
    in a single transaction, refresh `_meta` (`kind="terminal"`). Fast, single-row.
  - `export_all()` — `AgentSession.query.all()` → upsert every row in **one**
    transaction, refresh `_meta` (`kind="periodic"`). Rows present in the DB but absent
    from Redis are **retained** (the archive is a superset floor, never prunes live-set
    deletions — deletion is not a durability event we want to propagate on a cold start).
  - `restore_if_empty()` — apply the empty-Redis guard; on pass, iterate archived rows
    and `AgentSession(**fields).save()` each (idempotent — re-running on a now-populated
    Redis fails the guard and no-ops). Returns a structured result
    `{restored: int, skipped_reason: str|None}`.
  - `get_archive_status()` — read `_meta` + `sqlite_master`; return
    `{db_path, exists, row_count, last_export_ts, last_export_age_s, kind, healthy}`
    for the dashboard, `/health`, doctor, and CLI. Never raises (returns a
    `healthy=False` shape on any error).
- **Terminal hook** — one exception-isolated call at the end of `finalize_session`.
- **Periodic daemon thread** — `worker/__main__.py` spawns `worker-session-archive`
  alongside the heartbeat thread; each cycle calls `export_all()` and sleeps
  `SESSION_ARCHIVE_INTERVAL`.
- **Guarded restore step** — one call in `_run_worker` startup before index rebuild.
- **`valor-session-archive` CLI** — `export` (force full sweep), `restore`
  (`--dry-run` reports the guard decision + would-restore count without writing),
  `status` (prints `get_archive_status()` as JSON).
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
  dict/list fields as JSON-native. The whole dict is stored as a JSON `payload` column,
  with `agent_session_id`/`session_id`/`project_key`/`status`/`updated_at` promoted to
  real columns for queryability and the restore ordering. On restore, ISO strings are
  parsed back to `datetime` for `DatetimeField`/`SortedField`/`created_at` before
  `AgentSession(**fields).save()`. The datetime round-trip is the one fidelity risk
  and gets a dedicated test.
- **Empty-Redis guard (exact).** In `restore_if_empty()`:
  1. `records = list(AgentSession.query.all())`; if `records` is non-empty → skip
     (`skipped_reason="redis_has_records"`).
  2. Bounded `SCAN` (`count=1000`, capped iterations) for `AgentSession*` keys via the
     Popoto client; if any key found → skip (`skipped_reason="redis_has_keys"`).
  3. Log `DBSIZE` as advisory (`DBSIZE==0` confirms a true cold start) — informational,
     not a gate.
  4. Only when 1 AND 2 are both empty: rehydrate. Guard is evaluated **once**, before
     any write; the whole function is a no-op on any non-empty Redis.
- **Startup ordering.** Restore runs after "Redis connection verified"
  (`worker/__main__.py:703`) and **before** the Step 1 index rebuild (line 764), so the
  index rebuild indexes the freshly-restored rows. It is exception-isolated (a restore
  failure logs and must not block worker startup — a worker that can't start is worse
  than a worker with an un-restored archive).
- **Cadence.** `SESSION_ARCHIVE_INTERVAL` is a named, env-overridable constant in
  `agent/constants.py`, default `300` seconds — a provisional/tunable value (grain of
  salt: matched to the existing heartbeat/health cadence; loss window between periodic
  sweeps for non-terminal sessions is bounded by this, terminal sessions are exported
  immediately so the common case has no window). `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`
  (default `2 * SESSION_ARCHIVE_INTERVAL`) gates the doctor/dashboard "healthy" flag.
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
      the four restore-guard cases, datetime fidelity, empty-input handling,
      `get_archive_status()` shapes, exception isolation. (Greenfield — the archive
      module is new.)
- [ ] New: `tests/unit/test_session_archive_cli.py` — CREATE: `export`/`status`/
      `restore --dry-run` exit codes and JSON output. (Greenfield.)

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
- **Do NOT** restore into a partially-populated Redis under any circumstance — the guard
  is all-or-nothing (both `query.all()` empty AND no `AgentSession*` keys).
- **Do NOT** expand scope to `Memory`, message history, or the bloom filter. This is
  AgentSession-only per the issue; other stores are separate durability work.
- **Do NOT** run the periodic export or restore inside the asyncio event loop (both are
  blocking); use the daemon thread + startup step.
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
  to `pyproject.toml [project.scripts]` so the agent can invoke it via Bash
  (`valor-session-archive status`, `valor-session-archive export`,
  `valor-session-archive restore --dry-run`). This is the agent-reachable surface for
  the new module — the `agent/session_archive.py` functions alone are invisible to the
  agent until wired here.
- **Bridge internal import:** none required. The bridge does not need to call the
  archive directly; export is driven by the worker (daemon thread + `finalize_session`
  hook) and restore by worker startup.
- **Integration test for agent reachability:** `tests/unit/test_session_archive_cli.py`
  verifies the CLI runs end-to-end (the agent's actual invocation path), and a
  dashboard test confirms `dashboard.json.archive` is present (the agent reads
  `curl localhost:8500/dashboard.json` for system state).

## Documentation

- [ ] Update `docs/features/redis-durability.md` — replace the roadmap-only "Fix #2"
      row in the "Deferred Durability Work" table with an **implemented** section
      documenting: the SQLite archive location, the export cadence + terminal hook, the
      empty-Redis restore guard (with the precise both-must-be-empty rule), the
      dashboard/doctor freshness surfaces, and the operational runbook (how to inspect
      the archive, force an export, dry-run a restore).
- [ ] Add a `## Knowledge Base` / cross-link entry in `docs/features/README.md` index if
      the durability doc is not already listed there (verify during build).
- [ ] Add a `valor-session-archive` row to the CLI table in `CLAUDE.md` Quick Commands
      (export/restore/status).

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
6. `dashboard.json` exposes an `archive` block (`last_export_age_s`, `row_count`,
   `healthy`); `/health` and a `session-archive-freshness` doctor check agree.
7. `valor-session-archive` CLI (`export`/`restore`/`status`) works and is wired in
   `pyproject.toml`.
8. `docs/features/redis-durability.md` Fix #2 section documents the implemented store.
9. `python -m ruff check .` and `python -m ruff format --check .` pass; new tests pass.

## Step by Step Tasks

### 1. Archive module (core)
- **Task ID**: build-archive-module
- **Depends On**: none
- **Validates**: tests/unit/test_session_archive.py (create)
- Create `agent/session_archive.py`: `_connect()` (WAL, busy-timeout,
  `SESSION_ARCHIVE_DB_PATH` override, pytest-safe), `sessions` + `_meta` tables,
  `export_session`, `export_all` (single-transaction upsert), `restore_if_empty`
  (both-empty guard), `get_archive_status`.
- Add `SESSION_ARCHIVE_INTERVAL` (300) and `SESSION_ARCHIVE_FRESHNESS_THRESHOLD_S`
  (2×interval) to `agent/constants.py` as env-overridable named constants with a
  grain-of-salt "provisional/tunable" comment.
- Write `tests/unit/test_session_archive.py` (round-trip, 4 guard cases, datetime
  fidelity, empty input, status shapes, exception isolation).

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
- Add the guarded restore call in `_run_worker` after "Redis connection verified"
  (after line 703), before the Step 1 index rebuild, exception-isolated.
- Spawn a `worker-session-archive` daemon thread near the heartbeat thread (line 1008)
  that calls `export_all()` every `SESSION_ARCHIVE_INTERVAL`; pytest no-op guard.

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
- Create `tools/session_archive_cli.py` with `export`/`restore`(`--dry-run`)/`status`.
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
| New tests pass | `pytest tests/unit/test_session_archive.py tests/unit/test_session_archive_cli.py -q` | exit code 0 |
| Lifecycle + doctor tests pass | `pytest tests/unit/test_session_lifecycle.py tests/unit/test_doctor.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Archive module exists | `test -f agent/session_archive.py && echo ok` | output contains `ok` |
| Four public entry points present | `grep -c "^def export_session\|^def export_all\|^def restore_if_empty\|^def get_archive_status" agent/session_archive.py` | output is `4` |
| Terminal hook wired | `grep -c "export_session" models/session_lifecycle.py` | output is `1` |
| Restore step wired before index rebuild | `grep -n "restore_if_empty" worker/__main__.py` | prints a line number below 764 and above the Step 1 block start |
| Periodic thread wired | `grep -c "worker-session-archive" worker/__main__.py` | output is `1` |
| Cadence constant named | `grep -c "SESSION_ARCHIVE_INTERVAL" agent/constants.py` | output greater than 0 |
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
2. **Popoto internal coupling.** Reading `session._meta.fields` and reconstructing via
   `AgentSession(**fields).save()` depends on Popoto internals (as does the existing
   `_saved_field_values` code). *Mitigation:* one clearly-commented coupling point with
   a "re-verify on Popoto upgrade" note; the round-trip test catches a break.
3. **Export cost at scale.** `export_all()` reads every session each cycle; a large
   backlog could make the sweep slow. *Mitigation:* it runs off-loop in a daemon thread
   on a 5-min cadence, single transaction; terminal sessions are the common case and are
   covered by the fast single-row hook. If the full-set count grows large, the interval
   is env-tunable and a future incremental-by-`updated_at` sweep is a clean follow-up.
4. **False-green freshness.** A daemon thread that dies silently would let the archive
   go stale unnoticed. *Mitigation:* the `session-archive-freshness` doctor check +
   `dashboard.json.archive.healthy` surface staleness (age > threshold), mirroring the
   email/heartbeat pattern the issue explicitly asks us to follow.

## Race Conditions

1. **Concurrent export writers (periodic thread vs. terminal hook).** The daemon thread's
   `export_all()` and a `finalize_session` `export_session()` can run at the same instant
   against the same DB. *Mitigation:* SQLite WAL + a busy-timeout serializes writers; each
   is a self-contained transaction, so worst case one waits briefly for the other. No
   read-modify-write across the two — each upserts by primary key, so ordering is
   irrelevant (last writer for a given row wins, and both write the same authoritative
   Redis state).
2. **Restore vs. incoming enqueue at startup.** A message could arrive and create a new
   AgentSession while `restore_if_empty()` is mid-flight. *Mitigation:* restore runs at
   the very top of `_run_worker` **before** callbacks/handlers are registered
   (registration happens at line 753+), so no new session can be created during the
   guard evaluation or the rehydrate. The guard is also evaluated once, atomically-read,
   before any write.
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
- **Restoring child/parent graph ordering explicitly.** Popoto reconstructs indexes on
  `.save()` + the Step 1 rebuild; do not build a topological restore — just save every
  row and let the existing recovery/index machinery sort it out.
- **Cross-machine archive sharing.** Each machine's archive mirrors its own Redis; do
  not attempt to sync archives (would violate single-machine-ownership and could restore
  another machine's sessions).
