---
tracking: none
slug: m1-1-store-hardening
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.1 Store and settings

Milestone 1.1 of [valor-rebuild.md](valor-rebuild.md). Goal: a ledger the
system cannot edit, with provenance on every row Tom writes (**Reliable
stop, recovery, and correction**; Mission item 6).

Stakes: this changes the kernel's authentication, its money fold, and its
stored data, so `critique_rounds: 2`, `review_rounds: 2`.

## What is true today (checked, not assumed)

- The machine cluster (Postgres 18.6, `/opt/homebrew/var/postgresql@18`)
  has `trust` for every local and loopback line in `pg_hba.conf`.
  `valor_kernel` has no password. `tomcounsell` is a superuser.
- `core/db.py` connects with a password-free DSN; `migrate` prints that DSN.
- Both turn sandbox profiles (`scripts/demo_workspace.sh` heredoc and
  `scripts/replay_workspace.py`, `sandbox_profile`) deny reads under
  `~/Desktop`, which holds the vault `.env`, and deny port 5432 and the
  `/tmp` socket. Both are `(allow default)` otherwise, so a turn can run
  `/usr/bin/security` and read any login Keychain item whose ACL admits
  that tool (accepted for the Claude login in `docs/machine.md`).
- `valor_rebuild` holds 1,416 rows of 18 event types, 10 MB, and correction
  1 already. Its 19 `approval.granted` rows carry `by` and `note` only.
- Remaining money is computed twice: `budget.REMAINING_SQL` (budget from the
  `documents` row) and `tasks.status` (budget from `task.started`).
- No default test meters a successful call; only `test_live_turn.py` does,
  under `VALOR_LIVE=1`.
- The backup volume `/Volumes//valor_temp` exists, is writable, and is
  empty.

## What will be built, per Done item

### 1. `valor_kernel` needs a credential only the kernel holds

- **Where the secret lives.** `VALOR_KERNEL_PG_PASSWORD` in the vault `.env`
  (`settings.vault_env`, default `~/Desktop/Valor/.env`). Not the Keychain:
  a sandboxed turn can call `security` and the Keychain item would be
  readable by it, while both profiles already deny the vault directory.
  Settings reads the key from the file when asked (`settings.kernel_password()`),
  never from or into `os.environ`, so `claude_code.turn` (which copies the
  environment) and `workspace_turn` (an allowlist) cannot carry it.
- **Both profiles deny the vault file's directory by its settings value**,
  not only by the `~/Desktop` entry, so moving the vault keeps it denied.
- **`db.migrate`** (owner connection): if the vault has no key, generate one
  (`secrets.token_urlsafe(32)`) and append it to the vault file; then
  `ALTER ROLE valor_kernel LOGIN PASSWORD '<SCRAM verifier>'`, the verifier
  computed in Python so the plaintext never reaches the server or its log.
- **`db.secure_login()`**, separate from `migrate`: reads `SHOW hba_file`,
  and if the file lacks them, prepends three lines above every other rule
  (`local all valor_kernel scram-sha-256`, and `host` lines for
  `127.0.0.1/32` and `::1/128`), then `SELECT pg_reload_conf()` and reads
  `pg_hba_file_rules` to confirm the file parsed. The CLI `python -m core
  migrate` runs both. Test fixtures call `db.migrate` only, so a test run
  never rewrites the machine cluster's `pg_hba.conf`.
- **`db.connect`** (and a sync `db.connect_sync` for tests and `migrate`)
  passes the password as a keyword when the role is `valor_kernel`. DSN
  strings stay password-free, so nothing printed or stored (the `migrate`
  output, `Gateway.dsn`, ledger rows) carries it.
- **When the machine cluster switches.** Only at merge: the release runs
  `python -m core migrate` from the kernel checkout, after which a kernel
  process without this code cannot connect. During the build, the refusal is
  proven on a scratch cluster (below), so `~/src/valor-rebuild` and anything
  else using the machine cluster keep working.
- **Residual, recorded not fixed.** The owner role (Tom's superuser) still
  logs in by `trust`; only the sandbox keeps a turn from it. data.md gap 3
  is narrowed to that sentence.

Files: `core/settings.py`, `core/db.py`, `core/__main__.py`,
`scripts/demo_workspace.sh`, `scripts/replay_workspace.py`,
`tests/conftest.py`, `tests/test_kernel.py`, `tests/test_corrections.py`.

### 2. A fresh `migrate` holds correction 1

`db.migrate`, after the schema, takes the `corrections` advisory lock and,
if no `correction.recorded` numbered 1 exists, appends it: the governance
paragraph read from the first `**Governance` line of `CLAUDE.md`, scope
`global`, source class `direct`, provenance `by: tom`, `via: CLAUDE.md,
seeded by migrate`, `at` now. The payload is built by one function in
`core/corrections.py` that `record` also uses, so the shape lives in one
place. A database that already has correction 1 (as `valor_rebuild` does)
gets nothing.

Files: `core/db.py`, `core/corrections.py`, `tests/test_corrections.py`
(the `first` fixture reads correction 1 instead of recording it).

### 3. Approval provenance and approvals in the attention fold

- `broker.approve(conn, effect_id, *, note, by, via, role_played)` writes
  `approval.granted` with `provenance: {by, via, at, role_played}`, the
  shape answers and feedback use. CLI: `approve EFFECT_ID --note TEXT
  [--by B] [--via V] [--role-played]`.
- `tasks.status` folds each approval into `attention` as
  `{"kind": "approval", approval_id, effect_id, note, provenance}`.
  An old-shape row (top-level `by`, no provenance) reads as
  `{by, via: null, at: <the row's at column>, role_played: null}`: null,
  not false, because two of the demonstration's approvals were made under
  standing permission and false would be untrue. No row is rewritten.
- `status` gains `attention_counts`: per kind (`question`, `feedback`,
  `approval`, `budget_raise`), `{"total": n, "role_played": m}`. Approvals
  are counted apart from questions and feedback, per Tom.
- `scripts/replay.py` approves its local pushes with
  `--by "replay driver" --role-played`, since that is what they are.

Files: `core/broker.py`, `core/tasks.py`, `core/__main__.py`,
`scripts/replay.py`.

### 4. `budget raise` and one computation of remaining

- `python -m core budget raise TASK N [--note] [--by] [--via]
  [--role-played]` appends `budget.raised` `{raise_id, usd_micros, note,
  provenance}` under the task's lock. N is US dollars, greater than zero. A
  stopped task refuses it with an error and no row, as it refuses feedback
  (stop is final). A delivered or waiting task takes it.
- **One computation.** `tasks.money(rows)`, a pure fold over a task's rows:
  committed = `task.started` budget plus every `budget.raised`; charged;
  open reservations; remaining. `tasks.status` calls it on the rows it
  already reads; `budget.reserve` reads only the task's `task.started`,
  `budget.raised`, `gateway.reserved`, and `gateway.charged` rows under the
  lock and calls the same function. `REMAINING_SQL` and `budget.remaining`
  are deleted. `tasks.audit` compares charged against committed including
  raises.
- `tasks.dispatch` renders "Budget:" as the committed total from the fold,
  so a turn after a raise is told the real figure.
- Raises enter `attention` as `kind: "budget_raise"` with provenance: Tom
  acting on a task is attention (architecture.md, The attention log).

Files: `core/budget.py`, `core/tasks.py`, `core/session.py`,
`core/__main__.py`.

### 5. A default test meters a successful streamed call

- Record once: one `stream: true` Messages call to `claude-haiku-4-5` with
  the vault's `ANTHROPIC_API_KEY`, raw SSE bytes saved to
  `tests/fixtures/messages_stream_haiku.sse`, and one non-streamed call to
  `messages_haiku.json`. Spend under $0.001, recorded in the fixture's
  sibling `README` line with date, model, and command. The bodies hold no
  credential.
- The test starts an aiohttp server on `127.0.0.1:0` that replays the
  fixture, points a `Gateway` at it, and posts through a turn token.

Files: `tests/fixtures/`, `tests/test_gateway_meter.py`.

### 6. One typed settings module with seats and dated prices

`core/settings.py` becomes the one place for tunables and paths, each a
typed field with a default for this Mac and a `VALOR_*` override:

- Postgres: host, port, socket path (derived), database, kernel role,
  owner, `pg_bin` (where `pg_dump`, `pg_restore`, `initdb`, `pg_ctl` live).
- `vault_env`, `backup_dir` (default `"/Volumes//valor_temp"`,
  override `VALOR_BACKUP_DIR`), `backup_keep = 30`, `demo_dir` (default
  `~/src/valor-demo` from `Path.home()`), `claude` (`VALOR_CLAUDE`, else
  `shutil.which`, else `~/.local/bin/claude`).
- Moved in: `BYTES_PER_TOKEN`, `REAP_GRACE_S`, `IDLE_TURNS`, the price table.
- `Price(input, output, cache_write, cache_read, checked: date)` per pinned
  model id. The build checks each price against Anthropic's pricing page on
  the day and records that date. An entry that cannot be checked and that
  no seat uses and the ledger never charged is removed; if a seat's price
  cannot be checked, the build stops and asks.
- `SEATS = {"frontier": ..., "reviewer": ..., "light": ...}` with pinned ids
  (`claude-opus-5-5`, `claude-opus-5-5`, `claude-haiku-4-5`). `start
  --model` accepts a seat name or a model id and records the resolved id in
  the Brief; its default stays `haiku` (seat `light`) so no command gets
  dearer by this change.
- `gateway.charged` gains `price_checked`, the date of the price it used,
  so a later price change is visible in the ledger.
- `python -m core settings` prints every value (never the password) as
  `NAME=value` lines; `scripts/demo_workspace.sh` reads `DEMO`, the socket
  path, and `pg_bin` from it.

Files: `core/settings.py`, `core/budget.py`, `core/gateway.py`,
`core/runs.py`, `core/session.py`, `core/__main__.py`,
`harnesses/claude_code.py`, `scripts/replay_common.py`,
`scripts/replay_workspace.py`, `scripts/demo_workspace.sh`,
`tests/test_demo_sandbox.py`.

### 7. A schema change applies over existing history without rewriting it

This milestone changes no table. Its DDL is the role password and the
re-applied `schema.sql`; its data change is the seeded correction; its shape
changes (approval provenance, `budget.raised`) are reader-side. The test
proves the path the next milestones' table changes will take:

- `pg_dump -Fc valor_rebuild` (read only, as the owner), `pg_restore` into
  `valor_rebuild_copy_<pid>` on the machine cluster; record per row `id`,
  `xmin`, and the SHA-256 of `task_id|type|payload|at`, plus
  `pg_relation_filenode('events')`; run `db.migrate` on the copy; assert
  every row and the filenode unchanged, no correction 1 added, triggers and
  grants in place, and every task in the copy folds through the new
  `status` with the same committed, charged, and remaining as the old
  `REMAINING_SQL` gave (kept inline in the test as the oracle). Drop the
  copy. Skipped when `valor_rebuild` does not exist on the machine.

Files: `tests/test_migrate_history.py`.

### 8. Nightly dump, 30 kept, restore rehearsed

`core/backup.py`, entry points on the CLI:

- `python -m core backup`: refuses if `settings.backup_dir` does not
  exist (never creates it, so an unmounted volume cannot fill the boot
  disk). In a `REPEATABLE READ` transaction it exports a snapshot and
  computes a manifest from it: event count, max id, SHA-256 over every
  event row in id order (with `TimeZone=UTC`), and the same over
  `documents`. `pg_dump --snapshot=... -Fc` writes
  `valor_rebuild-<YYYYmmddTHHMMSSZ>.dump.partial`, renamed on success with
  its `.json` manifest beside it (no colons in names; the disk is exFAT).
  Then it deletes the oldest pairs past `backup_keep`, touching only files
  that match the name pattern, and never after a failed dump.
- `python -m core restore DUMP [--keep]`: `initdb` a scratch cluster in a
  temp directory, socket only (`listen_addresses = ''`), start it, create
  `valor_kernel`, `pg_restore`, recompute the manifest on the restored
  database, compare, then stop and delete the cluster (unless `--keep`).
  Exit 0 and a one-line summary on a match; non-zero naming the mismatch or
  the `pg_restore` error otherwise, with the cluster removed either way.
- `python -m core backup --plist` prints a launchd plist (label
  `com.valor.backup`, 03:00 daily, this interpreter, this checkout, logs
  under `~/Library/Logs/valor/`). Install steps go in `docs/machine.md`;
  the build does not load it.
- **The rehearsal.** One real `backup` to the volume and one `restore` of
  that dump, outputs recorded in `docs/machine.md` (date, size, rows,
  seconds) and in the build commit.

The scratch cluster helper is also what the credential test (item 1) runs
`migrate` and `secure_login` against.

Files: `core/backup.py`, `core/__main__.py`, `tests/test_backup.py`,
`docs/machine.md`.

## Tech debt paid

| Change | Debt it pays |
|---|---|
| Constants and the price table into `core/settings.py` | `BYTES_PER_TOKEN`, `REAP_GRACE_S`, `IDLE_TURNS`, prices scattered across modules |
| `settings.claude` | the `~/.local/bin/claude` fallback in `harnesses/claude_code.py` and `CLAUDE = "claude"` in `scripts/replay_common.py` |
| Socket path and port from settings in both profiles and `test_demo_sandbox.py` | literal `/tmp/.s.PGSQL.5432` and `5432` in three places |
| `settings.demo_dir` | `/Users/tomcounsell/...` in `scripts/replay_common.py`, `scripts/replay_workspace.py`, `scripts/demo_workspace.sh` |
| `PG_BIN` from settings | the Homebrew path literal in `scripts/demo_workspace.sh` |
| Delete "A test without a declared spend does not run." from `tests/README.md` | a sentence claiming enforcement nothing performs; no enforcement is added |
| `scripts/kernel_smoke.py` folded into `tests/test_live_turn.py` (its CLI approve and release on a live task); `scripts/session_smoke.py` becomes `tests/test_live_session.py`; both under `VALOR_LIVE=1` with a declared spend; the scripts are deleted | smoke scripts outside the suite |
| `core/backup.py` | data.md gap 5 |
| `tasks.money` | two computations of remaining |

## Tests that show it works

Default run (no spend), on real Postgres and real processes:

- **Credential** (`test_kernel.py`, on a scratch cluster): after `migrate`
  and `secure_login`, `valor_kernel` with no password (with
  `passfile=/dev/null`) is refused over the socket, `127.0.0.1`, and `::1`;
  a wrong password is refused; the settings password connects. Running
  `secure_login` twice leaves one copy of the lines. The owner still
  connects.
- **The turn cannot obtain it**: a probe under the demo profile and under
  the replay profile is denied reading `settings.vault_env`, including when
  `vault_env` points outside `~/Desktop`; the environments built by
  `turn()` and `workspace_turn()` contain no value equal to the password;
  `os.environ` of the test process does not either after a kernel connect.
  The `migrate` CLI output contains no password.
- **Correction 1**: a fresh database holds correction 1 equal to the
  `CLAUDE.md` paragraph, `global`, `direct`, `by: tom`; a second `migrate`
  adds nothing; the next `correct` is number 2.
- **Approvals**: CLI approve with `--role-played --by "stand-in"` yields
  provenance with `role_played: true`, appears in `attention` as kind
  `approval` and in `attention_counts.approval.role_played`; a live tap
  counts in `total` only; an old-shape row inserted directly reads with
  `role_played: null` and `at` from its column; release still binds to the
  digest.
- **Budget raise**: a task exhausted by a reservation, then raised, reserves
  again; `status` and `reserve` agree on remaining at every step; `audit`
  stays empty with charges above the original budget but within the raised
  one; a raise on a stopped task errors and writes no row; zero and negative
  raises are refused; `session.run` on an exhausted task returns `budget
  exhausted`, and after a raise runs a turn (stand-in harness, as
  `test_session.py` does); racing raises and reservations on one task never
  let reservations exceed the committed total.
- **Metering** (`test_gateway_meter.py`): the recorded stream is charged
  exactly `budget.cost(usage from the fixture)` and forwarded byte for byte;
  the same stream split into 7-byte chunks (splitting lines) is charged the
  same; the non-streamed recording is charged the same way; the stream cut
  before `message_delta` is charged reported input plus every allowed
  output token with `complete: false`; an upstream 529 is charged 0; each
  leaves `audit` empty.
- **Settings**: a `VALOR_*` override reaches the value; prices carry a
  `date`; every seat names a priced id; a dated id resolves to its entry.
- **Migration over history** (`test_migrate_history.py`): the copy of
  `valor_rebuild` as in item 7; plus, always run, a test database holding
  one row of every type the kernel writes (the data.md table, `budget.raised`,
  and both approval shapes) re-migrated with the same no-rewrite checks.
- **Backup** (`test_backup.py`, dumps into `tmp_path`): dump then restore
  into a scratch cluster matches its manifest; a truncated dump fails the
  restore, exits non-zero, and leaves no cluster; a manifest whose digest
  was altered fails; a missing `backup_dir` fails with the path named,
  writes nothing, prunes nothing; 32 dated pairs plus an unrelated file
  prune to the newest 30 and keep the unrelated file; a failed `pg_dump`
  leaves no `.partial` file and prunes nothing; rows appended during the
  dump do not break the manifest match (snapshot); `--plist` output parses
  with `plistlib` and names this interpreter.

Live (`VALOR_LIVE=1`, spend declared): `test_live_turn.py` (now also the CLI
approve and release on a live task) and `test_live_session.py` (the
question, answer, delivery, held push, release, and corrections in every
Brief, sandboxed).

The 39 existing tests keep passing, edited only where an API they call
changed (`approve`, `remaining`, the `first` fixture).

## Out of scope

The objective tree; snapshot documents; the state machine, verdicts, and
guards (1.2); judgement (1.3); checks, the workspace provisioner, the
GitHub credential (1.4); moving replay scripts into `tests/emulator/` (1.5);
a password for the owner role; a migration tool beyond the idempotent
`schema.sql`; lowering a budget; loading the backup plist into launchd;
backing up anything but the kernel database.

## Questions for Tom

1. **The credential lives in the vault `.env`, not the Keychain.** A
   sandboxed turn can read a login Keychain item through `security`; it
   cannot read `~/Desktop`. Assumed: yes, vault only for this one.
2. **`python -m core migrate` edits the machine cluster's `pg_hba.conf`**
   (prepends three `valor_kernel` lines and reloads), run at merge time,
   and Tom's superuser stays on `trust`. Assumed: yes to both; the owner's
   trust stays a recorded gap.
3. **This milestone changes no table**, so the migration evidence is the
   populated copy re-migrated with row-for-row and no-rewrite proof, and
   1.2's first table change reuses the same test. Assumed: that satisfies
   the Done item.
