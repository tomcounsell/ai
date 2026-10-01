---
tracking: none
slug: m1-1-store-hardening
type: build
status: merged
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

- The machine cluster (Postgres 18.6, data directory
  `/opt/homebrew/var/postgresql@18`) has `trust` for every local and
  loopback line in `pg_hba.conf`, and serves other databases too
  (`psyoptimal`). `valor_kernel` has no password. `tomcounsell` is a
  superuser.
- `core/db.py` connects with a password-free DSN; `migrate` prints that DSN.
  `tests/conftest.py` calls `db.migrate` on the machine cluster every
  session.
- Both turn sandbox profiles (`scripts/demo_workspace.sh` heredoc and
  `scripts/replay_workspace.py`, `sandbox_profile`) deny reads under
  `~/Desktop`, port 5432, and the `/tmp` socket. Neither denies the machine
  cluster's data directory (the demo's `$PG/data` deny is the workspace
  cluster's). Both are `(allow default)` otherwise, so a turn can run
  `/usr/bin/security` and read a login Keychain item whose ACL admits that
  tool.
- The vault `.env` (`~/Desktop/Valor/.env`) syncs to iCloud, and the system
  on `main` loads it into the environment of its unsandboxed sessions
  (`bridge/telegram_bridge.py`, `bridge/email_bridge.py`). The system on
  `main` also uses `~/.config/valor/` for a sentinel file. No `~/.pgpass`
  exists.
- `valor_rebuild` holds 1,416 rows of 18 event types, 10 MB, and correction
  1 already. Its 19 `approval.granted` rows carry `by: tom` and `note` only,
  including ones the replay driver wrote; 1 `question.answered` and 2
  `feedback.given` rows have no `role_played`.
- Remaining money is computed twice: `budget.REMAINING_SQL` (budget from the
  `documents` row) and `tasks.status` (budget from `task.started`).
- No default test meters a successful call; only `test_live_turn.py` does,
  under `VALOR_LIVE=1`.
- The backup volume `/Volumes/<U+F028>/valor_temp` exists, is exFAT,
  writable, and empty.

## What will be built, per Done item

### 1. The kernel databases need a credential only the kernel holds

**The credential file.** `settings.pg_passfile`, default
`~/.config/valor-kernel/pgpass`: a libpq password file (mode 600, directory
mode 700), outside iCloud, outside the vault, and outside `~/.config/valor/`
that the system on `main` uses. It holds one line for `valor_kernel` and one
for the owner role, scoped to the kernel databases. Every kernel and owner
connection passes `passfile=<path>` to libpq, so the Python process never
holds the password as a string, DSNs stay password-free (the `migrate`
output, `Gateway.dsn`, and ledger rows carry at most the path), and nothing
puts it in `os.environ`. Tom's own `psql` keeps working by setting
`PGPASSFILE` to the same path in his shell (a path, not a secret).

Not the Keychain: a sandboxed turn can read a login Keychain item through
`security`. Not the vault `.env`: it syncs to iCloud and the system on
`main` loads it into unsandboxed sessions. `docs/machine.md`, Keychain,
gains this file as the one stated exception and why.

**Which logins need it.** Every role, on the kernel databases only
(`settings.database` and `settings.test_database`, i.e. `valor_rebuild`
and `valor_rebuild_test`): `scram-sha-256` lines for `local`,
`127.0.0.1/32`, and `::1/128` ahead of every other rule. Other databases
(`psyoptimal`, `postgres`) stay on `trust`. So `valor_kernel` without its
credential is refused, and so is the superuser on the kernel databases.

**`python -m core secure-login`**, the only code that touches the role
passwords, the credential file, or `pg_hba.conf`. `db.migrate` and every
test fixture touch none of them. In order, under an exclusive `flock` on
`<pg_passfile>.lock`:

1. If the credential file is missing, create it with `O_CREAT | O_EXCL`,
   mode 600, holding two random passwords (`secrets.token_urlsafe(32)`).
   An existing file is never rewritten; a concurrent second run waits on
   the lock, then finds the file.
2. `ALTER ROLE <role> PASSWORD '<SCRAM verifier>'` for `valor_kernel` and
   the owner, the verifier computed in Python so no plaintext reaches the
   server or its log.
3. Read `SHOW hba_file`. Parse it into rules (skipping comments and blank
   lines) and look for our three rules by their fields. If they are not the
   first three rules: write the new file (our three, marked by a comment,
   then the original text unchanged) to a temp file in the same directory,
   mode 600, `fsync`, and `rename` over the original. `SELECT
   pg_reload_conf()`, then read `pg_hba_file_rules`; if any row has an
   `error`, rename the saved original back, reload, and exit non-zero with
   the error. A second run finds the rules and changes nothing.

`python -m core migrate` runs `db.migrate` and then `secure-login`, so a
fresh machine gets both from one command. The `migrate` CLI and
`secure-login` are run on the machine cluster only at merge (see Rollout).

**Sandbox profiles.** Both deny read and write on, each by its settings
value: the credential file's directory, the machine cluster's data
directory (`SHOW data_directory`, recorded in settings as
`pg_data_dir`), and `backup_dir`. This closes the route where a turn edits
`pg_hba.conf` or the heap files and reloads.

**What this meets and what still rests on the sandbox** (written into
data.md, gap 3): met: no role logs into a kernel database without the
credential, and a turn cannot read the credential file, the data
directory, or the dumps. Still on the sandbox: other databases on the
cluster trust local logins, so a turn that escaped its profile could log
in as the superuser to `postgres` and `ALTER ROLE` or `ALTER SYSTEM`; and
Tom's macOS user, outside a turn, can always edit the data directory.

Files: `core/settings.py`, `core/db.py`, `core/__main__.py`,
`scripts/demo_workspace.sh`, `scripts/replay_workspace.py`,
`tests/conftest.py`, `tests/test_kernel.py`, `tests/test_corrections.py`,
`tests/test_demo_sandbox.py`, `docs/machine.md`, `docs/data.md`.

### 2. A fresh `migrate` holds correction 1

`db.migrate`, after the schema, takes the `corrections` advisory lock and,
if no `correction.recorded` numbered 1 exists, appends it: the governance
paragraph read from the first `**Governance` line of `CLAUDE.md`, scope
`global`, source class `direct`, provenance `by: tom`, `via: CLAUDE.md,
seeded by migrate`, `at` now, `role_played: false`. The payload is built by
one function in `core/corrections.py` that `record` also uses. A database
that already has correction 1 (as `valor_rebuild` does) gets nothing.

Files: `core/db.py`, `core/corrections.py`, `tests/test_corrections.py`
(the `first` fixture reads correction 1 instead of recording it).

### 3. Approval provenance and approvals in the attention fold

- `broker.approve(conn, effect_id, *, note, by, via, role_played)` writes
  `approval.granted` with `provenance: {by, via, at, role_played}`, the
  shape answers and feedback use. CLI: `approve EFFECT_ID --note TEXT
  [--by B] [--via V] [--role-played]`.
- **One provenance reader** for answers, feedback, approvals, and raises:
  a field the row does not carry reads as `null`, never a default. So an
  old answer or feedback without `role_played` reads `role_played: null`
  (today `tasks._provenance` says `false`), and an old approval (top-level
  `by`, no provenance) reads `{by, via: null, at: <the row's at column>,
  role_played: null}`. No row is rewritten.
- `tasks.status` folds each approval into `attention` as
  `{"kind": "approval", approval_id, effect_id, note, provenance}`.
- `status` gains `attention_counts`: per kind (`question`, `feedback`,
  `approval`, `budget_raise`), `{"total": n, "role_played": m, "unknown":
  k}`, where `unknown` counts rows whose `role_played` is null. Approvals
  are counted apart from questions and feedback, per Tom.
- data.md and mission.md note that `by` on rows written before this
  milestone is unreliable: the replay driver's approvals say `by: tom`.
- `scripts/replay.py` approves its local pushes with
  `--by "replay driver" --role-played`, since that is what they are.

Files: `core/broker.py`, `core/tasks.py`, `core/__main__.py`,
`scripts/replay.py`, `docs/data.md`, `docs/mission.md`.

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

- **Recording**, once, by `tests/fixtures/record_messages.py` under
  `VALOR_LIVE=1`: start a `Gateway` on the test database with a task
  carrying a $0.01 budget, post one streamed and one non-streamed Messages
  call to `claude-haiku-4-5` through a turn token (the recorder supplies
  the vault's `ANTHROPIC_API_KEY` as the client header; the kernel never
  reads it), and save the response bodies only, no headers (they carry the
  organization id), to `tests/fixtures/messages_stream_haiku.sse` and
  `messages_haiku.json`. The spend lands in the test ledger like any other
  call; the gateway's charge for each is written beside the fixtures with
  the date, model, and price date.
- **The test** (`tests/test_gateway_meter.py`) starts an aiohttp server on
  `127.0.0.1:0` that replays a fixture, points a `Gateway` at it, posts
  through a turn token, and compares the charge to a micro-dollar figure
  **hard-coded in the test and worked by hand** in a comment from the
  fixture's usage numbers and the dated price, not computed by
  `budget.cost`.

Files: `tests/fixtures/`, `tests/test_gateway_meter.py`.

### 6. One typed settings module with seats and dated prices

`core/settings.py` becomes the one place for tunables and paths, each a
typed field with a default for this Mac and a `VALOR_*` override:

- Postgres: host, port, socket path (derived), database, test database
  (moved from `tests/conftest.py`), kernel role, owner, `pg_bin` (where
  `pg_dump`, `pg_restore`, `initdb`, `pg_ctl` live), `pg_data_dir`,
  `pg_passfile`.
- `backup_dir` (default the string `"/Volumes/\uf028/valor_temp"` in
  Python, override `VALOR_BACKUP_DIR`), `backup_keep = 30`, `demo_dir`
  (default `~/src/valor-demo` from `Path.home()`), `claude`
  (`VALOR_CLAUDE`, else `shutil.which`, else `~/.local/bin/claude`).
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
- `python -m core settings` prints every value as `NAME=value` lines (the
  credential file's path, never its content); `scripts/demo_workspace.sh`
  reads `DEMO`, the socket path, the data directory, the credential file,
  `backup_dir`, and `pg_bin` from it.

Files: `core/settings.py`, `core/budget.py`, `core/gateway.py`,
`core/runs.py`, `core/session.py`, `core/__main__.py`,
`harnesses/claude_code.py`, `scripts/replay_common.py`,
`scripts/replay_workspace.py`, `scripts/demo_workspace.sh`,
`tests/conftest.py`, `tests/test_demo_sandbox.py`.

### 7. A schema change applies over existing history without rewriting it

This milestone changes no table, so the test applies a real additive change
to a copy of the populated ledger through `db.migrate`, which takes the
schema file as an argument (default `core/schema.sql`):

- `pg_dump -Fc valor_rebuild` (read only, as the owner), `pg_restore` into
  `valor_rebuild_copy_<pid>` on the machine cluster. Record per row `id`,
  `xmin`, and the SHA-256 of `task_id|type|payload|at`, and
  `pg_relation_filenode` of `events` and `documents`.
- Run `db.migrate` on the copy with a test schema: `core/schema.sql` plus a
  nullable column on `events`, a new plain index, and a new partial unique
  index (on `budget.raised`'s `raise_id`), each an additive change of the
  kind 1.2 will make.
- Assert: every existing row's `id`, `xmin`, and digest unchanged; both
  tables' filenodes unchanged; the new column, both indexes, the triggers,
  and the grants present; no correction 1 added; every task in the copy
  folds through the new `tasks.money` to the same committed, charged, and
  remaining as the old `REMAINING_SQL` (kept inline in the test as the
  oracle). Drop the copy.
- Skipped when `valor_rebuild` does not exist on the machine. Always run:
  the same change and checks over a test database holding one row of every
  type the kernel writes (the data.md table, `budget.raised`, and both
  approval shapes).

Files: `core/db.py`, `tests/test_migrate_history.py`.

### 8. Nightly dump, 30 kept, restore rehearsed

`core/backup.py`, entry points on the CLI:

- **`python -m core backup`.** Refuses if `settings.backup_dir` does not
  exist (never creates it, so an unmounted volume cannot fill the boot
  disk), or if it is on the same device as `pg_data_dir` (`st_dev`), since
  a dump on the cluster's own disk is not a backup. In a `REPEATABLE READ`
  transaction it exports a snapshot and computes, from that snapshot, the
  event count, max id, and SHA-256 over every event row in id order (with
  `TimeZone=UTC`), and the same over `documents`. `pg_dump
  --snapshot=... -Fc` writes `valor_rebuild-<YYYYmmddTHHMMSSZ>.dump.partial`,
  created with `O_CREAT | O_EXCL`; the run refuses if the final name
  already exists (no colons in names: the disk is exFAT). Then it computes
  the dump's SHA-256, writes the manifest (`<name>.dump.json`: database,
  the counts and digests, the dump's SHA-256, `pg_dump` version) to a temp
  name, `fsync`s it, renames it into place, and only then renames the dump
  from `.partial` to its final name.
- **Pruning** runs only after a successful dump. It considers only names
  matching `^valor_rebuild-\d{8}T\d{6}Z\.dump$` that have their manifest
  beside them (complete pairs), keeps the newest `backup_keep`, and
  deletes the older pairs. exFAT's `._valor_rebuild-*` AppleDouble files,
  stray `.partial` files, and anything else are never matched.
- **`python -m core restore DUMP [--keep]`.** Checks the dump's SHA-256
  against its manifest, `initdb`s a scratch cluster with its socket in a
  short directory (`tempfile.mkdtemp(dir="/tmp")`; a `tmp_path` socket path
  exceeds macOS's 103-byte limit), socket only (`listen_addresses = ''`),
  starts it, creates `valor_kernel`, runs `pg_restore`, recomputes the
  counts and digests on the restored database, compares, then stops and
  deletes the cluster (unless `--keep`). Exit 0 and a one-line summary on a
  match; non-zero naming the mismatch or the `pg_restore` error otherwise,
  with the cluster removed either way.
- **`python -m core backup --plist`** prints a launchd plist (label
  `com.valor.backup`, 03:00 daily, this interpreter, this checkout, logs
  under `~/Library/Logs/valor/`). Install steps go in `docs/machine.md`,
  including granting the interpreter access to removable volumes (macOS
  privacy control), which a launchd job needs and Terminal already has.
- **The rehearsal**, recorded in `docs/machine.md` (date, size, rows,
  seconds) and the build commit: one `backup` from the command line to the
  volume and one `restore` of it. The nightly half (a dump fired by
  launchd) happens at merge, below.

The scratch cluster helper (short socket directory included) is also what
the credential test runs `migrate` and `secure-login` against.

Files: `core/backup.py`, `core/__main__.py`, `tests/test_backup.py`,
`docs/machine.md`.

## Rollout at merge

Done for items 1 and 8 needs the machine, so the merge carries these steps,
each recorded in the merge commit:

1. Stop any running task (`python -m core stop`) and let no new run start:
   a kernel process from before the merge passes no password file and
   cannot connect once the rules are in.
2. Update the kernel checkout `~/src/valor-rebuild` to the merged branch.
3. From it, `python -m core migrate` (which runs `secure-login`).
4. Tom adds `export PGPASSFILE=~/.config/valor-kernel/pgpass` to his shell
   profile, so his own `psql` reaches the kernel databases.
5. Connect to `valor_rebuild` as `valor_kernel` and as the owner, over the
   socket, `127.0.0.1`, and `::1`, with no password file, and record that
   each is refused; connect with it and record success. This is a one-time
   verification recorded in the commit, not a check the system runs.
6. Tom installs and loads the plist (the coordinator asks him), grants the
   interpreter removable-volume access if macOS asks, and the job is fired
   once with `launchctl kickstart`; the dump it writes is restored with
   `python -m core restore` and both outputs recorded.

`docs/machine.md` gains an upgrade step: a Homebrew major upgrade of
Postgres runs `initdb` for a new data directory with `trust` everywhere, so
after any upgrade `python -m core secure-login` is run again and
`pg_data_dir` updated.

## Tech debt paid

| Change | Debt it pays |
|---|---|
| Constants and the price table into `core/settings.py` | `BYTES_PER_TOKEN`, `REAP_GRACE_S`, `IDLE_TURNS`, prices scattered across modules |
| `settings.claude` | the `~/.local/bin/claude` fallback in `harnesses/claude_code.py` and `CLAUDE = "claude"` in `scripts/replay_common.py` |
| Socket path and port from settings in both profiles and `test_demo_sandbox.py` | literal `/tmp/.s.PGSQL.5432` and `5432` in three places |
| `settings.demo_dir` | `/Users/tomcounsell/...` in `scripts/replay_common.py`, `scripts/replay_workspace.py`, `scripts/demo_workspace.sh` |
| `pg_bin` from settings | the Homebrew path literal in `scripts/demo_workspace.sh` |
| `settings.test_database` | `VALOR_TEST_DB` read only in `tests/conftest.py` |
| Delete "A test without a declared spend does not run." from `tests/README.md` | a sentence claiming enforcement nothing performs; no enforcement is added |
| `scripts/kernel_smoke.py` folded into `tests/test_live_turn.py` (its CLI approve and release on a live task); `scripts/session_smoke.py` becomes `tests/test_live_session.py`; both under `VALOR_LIVE=1` with a declared spend; the scripts are deleted | smoke scripts outside the suite |
| `core/backup.py` | data.md gap 5 |
| `tasks.money` | two computations of remaining |
| One provenance reader | old answers and feedback reading as not role-played when nothing was recorded |

## Tests that show it works

Default run (no spend), on real Postgres and real processes:

- **Credential** (`test_kernel.py`, on a scratch cluster with a short
  socket directory): after `migrate` and `secure-login`, on a kernel
  database, as `valor_kernel` and as the owner: an empty or wrong password
  is refused with SQLSTATE `28P01`; no password at all (`passfile=/dev/null`)
  fails with libpq's "no password supplied", which libpq raises only when
  the server asked for a password, and the same parameters against the
  `postgres` database on that cluster connect (so the refusal is our rule,
  not a dead socket); over the socket, `127.0.0.1`, and `::1`; the
  credential file connects. A second `secure-login` changes neither
  `pg_hba.conf` nor the credential file (bytes compared). Two concurrent
  `secure-login` runs leave one credential file whose passwords both
  connect. A `pg_hba.conf` that will not parse after the edit (forced by
  appending a bad line to the original first) is restored and the command
  exits non-zero, with the cluster still accepting the old logins.
- **Fixtures touch nothing**: `db.migrate` on the machine cluster leaves
  `pg_authid.rolpassword` for both roles, the credential file, and
  `pg_hba.conf` byte-identical.
- **The turn cannot obtain it** (`test_demo_sandbox.py`): a probe under the
  demo profile and under the replay profile is denied reading and writing
  the credential file, the machine cluster's data directory (its
  `pg_hba.conf` included), and `backup_dir`, including when the credential
  file is configured outside `~/.config`; the environments built by
  `turn()` and `workspace_turn()` carry no credential; the `migrate` and
  `settings` CLI output carries none.
- **Correction 1**: a fresh database holds correction 1 equal to the
  `CLAUDE.md` paragraph, `global`, `direct`, `by: tom`; a second `migrate`
  adds nothing; the next `correct` is number 2.
- **Approvals and provenance**: CLI approve with `--role-played --by
  "stand-in"` yields `role_played: true`, appears in `attention` as kind
  `approval` and in `attention_counts.approval.role_played`; a live tap
  counts in `total` only; an old-shape approval, an answer, and a feedback
  row inserted without `role_played` each read `role_played: null` and
  count in `unknown`; release still binds to the digest.
- **Budget raise**: a task exhausted by a reservation, then raised, reserves
  again; `status` and `reserve` agree on remaining at every step; `audit`
  stays empty with charges above the original budget but within the raised
  one; a raise on a stopped task errors and writes no row; zero and negative
  raises are refused; `session.run` on an exhausted task returns `budget
  exhausted`, and after a raise runs a turn (stand-in harness, as
  `test_session.py` does); racing raises and reservations on one task never
  let reservations exceed the committed total.
- **Metering** (`test_gateway_meter.py`), each against a hand-worked
  figure: the recorded stream; the same stream in 7-byte chunks (splitting
  lines); the non-streamed recording; the stream cut before
  `message_delta` (reported input plus every allowed output token,
  `complete: false`); an upstream 529 (charged 0). Each is forwarded byte
  for byte and leaves `audit` empty.
- **Settings**: a `VALOR_*` override reaches the value; prices carry a
  `date`; every seat names a priced id; a dated id resolves to its entry.
- **Migration over history** (`test_migrate_history.py`): item 7.
- **Backup** (`test_backup.py`, dumps into a temp directory on another
  device where one exists, else with the device check pointed at a test
  value): dump then restore matches its manifest; a truncated dump fails
  the SHA-256 check and the restore exits non-zero leaving no cluster; a
  manifest whose row digest was altered fails after restore; a missing
  `backup_dir` fails naming the path, writes nothing, prunes nothing; a
  `backup_dir` on the data directory's device is refused; 32 complete pairs
  plus `._valor_rebuild-*.dump` siblings, a dump without a manifest, a
  `.partial`, and an unrelated file prune to the newest 30 pairs and keep
  everything else; a failed `pg_dump` leaves no `.partial` and prunes
  nothing; an existing final name is refused; rows appended during the dump
  do not break the match (snapshot); `--plist` output parses with
  `plistlib` and names this interpreter.

Live (`VALOR_LIVE=1`, spend declared): `test_live_turn.py` (now also the CLI
approve and release on a live task) and `test_live_session.py` (the
question, answer, delivery, held push, release, and corrections in every
Brief, sandboxed).

Where the build put them: the credential tests in
`tests/test_credentials.py`, the approval and raise tests in
`tests/test_attention.py`, and the settings tests in
`tests/test_settings.py`, each on its own rather than inside
`test_kernel.py` or `test_session.py`.

The 39 existing tests keep passing, edited only where an API they call
changed (`approve`, `remaining`, `_provenance`'s null, the `first` fixture,
connections that now pass the credential file).

## Out of scope

The objective tree; snapshot documents; the state machine, verdicts, and
guards (1.2); judgement (1.3); checks, the workspace provisioner, the
GitHub credential (1.4); moving replay scripts into `tests/emulator/` (1.5);
password authentication on databases other than the kernel's; a migration
tool beyond the idempotent `schema.sql`; lowering a budget; backing up
anything but the kernel database.

## Questions for Tom

1. **The credential lives in `~/.config/valor-kernel/pgpass`**, not the
   Keychain (a turn can read it through `security`) and not the vault
   `.env` (iCloud, and loaded into the old system's sessions). Assumed: yes.
2. **Your superuser needs the password on `valor_rebuild` and
   `valor_rebuild_test`** (set `PGPASSFILE` to that file in your shell);
   other databases stay on `trust`. Assumed: yes.
3. **Loading the backup plist at merge**, with removable-volume access for
   the interpreter. Assumed: yes; the coordinator asks you at merge.

## Rollout record

Merged 2026-10-01 at `2d176c08e` after test, review (round 2), and docs
passed on candidate `3fb26024f`. Approved under Tom's standing instruction
of 2026-10-01 ("get as far as you can possibly get"), given while away, not
a live tap.

1. No kernel task was running.
2. `~/src/valor-rebuild` fast-forwarded to the merged branch.
3. `python -m core migrate` printed
   `{"passfile": "created", "pg_hba.conf": "written"}`. `pg_hba_file_rules`
   shows lines 2 to 4 as `scram-sha-256` for `valor_rebuild` and
   `valor_rebuild_test`, all users; every other database keeps `trust`
   (a login to `psyoptimal` with no password still connects).
4. Open for Tom: `export PGPASSFILE=~/.config/valor-kernel/pgpass` in his
   shell profile.
5. With no password file, `valor_kernel` and the owner were each refused
   over the socket, `127.0.0.1`, and `::1` ("fe_sendauth: no password
   supplied"); a wrong password was refused with "password authentication
   failed". With the password file all six connected. The default suite then
   passed on the secured cluster: 108 passed, 3 skipped.
6. Open for Tom: install and load the plist, grant removable-volume access.
   A dump run by hand after the rollout (1,416 events, 22 documents,
   160,622 bytes) restored with `"match": true`.
