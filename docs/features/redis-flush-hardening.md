# Redis Flush Hardening

Two production Redis flushes, six weeks apart, both from a script that believed it was targeting a
test database. Issue #2645 closes the gap between "believed" and "was" with four independent layers,
each guarding a different boundary the offending code has to cross.

Two of them enforce as shipped. Layer 1 alone would have stopped both incidents, and Layer 3 would
have stopped the command that started the second one. Layer 2 ships as a planner and a runbook, so it
stops nothing until an operator applies it per machine; Layer 4 is a knowledge layer, not an
enforcement one. The table below is precise about which is which.

## The Two Incidents

**2026-06-03.** A `flushdb()`/`flushall()` call against Redis db 0 wiped the production dataset:
memories, Telegram history, chats, knowledge docs. AOF was not yet enabled, so the loss was permanent.
The fix that shipped afterward was a guard in `tests/conftest.py`, installed at conftest import time.
It is scoped to pytest by design: it protects a `pytest` run, not a process.

**2026-08-07.** An ad-hoc debug script called `POPOTO_REDIS_DB.flushdb()`. It meant to target test db
15 and tried to set that with `os.environ.setdefault("REDIS_URL", ".../15")`. The shell already had
the production `REDIS_URL` exported, so `setdefault` was a silent no-op: it never overrides a key that
already has a value. The client built against the pre-existing production URL and landed on db 0.
25,825 keys of live agent state (`AgentSession`, `Room`, `Job`, subconscious memory, steering lists, DM
coverage epochs) were destroyed. Recovery took an AOF point-in-time restore, about 30 seconds of Redis
downtime, and about 2 minutes of discarded writes. The conftest guard from the first incident never
applied: an ad-hoc script that never imports `tests/conftest.py` is not a pytest run.

The through-line across both incidents: every prior control sat at a boundary the offending code did
not cross. This work moves the same rule to boundaries every caller has to cross, whether or not it
ever imports the test suite.

## The Four Layers

| Layer | Boundary | What it stops | What it doesn't |
|---|---|---|---|
| 1. Process-wide flush guard | Every Python process started inside a repo venv | Any `.flushdb()` on db 0, any `.flushall()`, from any first-party or ad-hoc script | Non-Python clients; a process outside every discovered venv; a flush issued as execute_command("FLUSHDB") or through redis.cluster.RedisCluster, neither of which the monkeypatch covers |
| 2. Redis ACL | The Redis server itself | Non-Python clients (`redis-cli`, other languages, other checkouts) reaching db 0 | Nothing yet on its own. This PR ships the planner only; see below |
| 3. PreToolUse hook validator | Agent-issued Bash commands | A flush written inline in a command Claude is about to run | A flush buried inside a file that's already been written; Bash is the only surface it sees; and it fails open: if the validator itself raises, the dispatcher logs and allows, matching every sibling validator except validate_merge_guard |
| 4. This documentation and the `CLAUDE.md` note | The agent's read-before-testing surface | The reasoning error at its source, before either script or command exists | Nothing mechanical: it's a knowledge layer, not an enforcement layer |

### Layer 1: process-wide flush guard

`tools/redis_flush_guard.py` monkeypatches `flushdb`/`flushall` on `redis.Redis` and
`redis.asyncio.Redis`. It guards the operation, not the construction (D1): building a client on db 0
stays unrestricted, because db 0 is legitimate production traffic for every first-party call site.
Only the destructive calls are intercepted.

- `flushdb()` raises when the client's own `connection_pool.connection_kwargs["db"]` resolves to 0.
  A client whose db number can't be introspected is treated as db 0 (fail closed), never assumed safe.
- `flushall()` raises unconditionally, because it wipes every db including 0.
- Both checks read the override (below) at call time, never at import time.

This is the load-bearing layer: it is what would have stopped the 2026-08-07 script even though the
production `REDIS_URL` reached it, because the guard checks the resolved db number, not the intent
behind the script.

### Layer 2: Redis ACL (planner only in this PR)

`scripts/update/redis_acl.py` computes the target ACL rule set and, on request, applies it. **This PR
ships the planner, the target rule set, the staged `aclfile` directive, the doctor check, and the
apply runbook below. It mutates nothing.** `/update` calls `apply_redis_acl()` with no arguments,
every time, which is the report-only path: it reads `ACL LIST`, computes the commands that would
converge the server, and returns them without issuing a single `ACL SETUSER`. The server-side rule set
is effective only after an operator runs the runbook by hand.

### Layer 3: PreToolUse hook validator

`.claude/hooks/validators/validate_no_redis_flush.py` blocks `.flushdb(...)` / `.flushall(...)` call
shapes and `redis-cli ... FLUSHDB` / `FLUSHALL` invocations in any Bash command an agent is about to
run, unconditionally, with no Popoto-vocabulary gate (unlike the raw-delete validator, which only
fires alongside a `_POPOTO_CONTEXT` token). It catches a flush the moment it's typed. It cannot catch
one already sitting inside a file Claude wrote in an earlier step, because a Bash-string validator
only sees the command it's given, not the file's contents. That gap is exactly why Layer 1 exists and
why it is the layer that actually stopped the incident class: file contents at execution time reach
the guard regardless of how the command that runs them was assembled.

### Layer 4: documentation

This document and the `CLAUDE.md` § Manual Testing Hygiene paragraph. Layers 1 through 3 are all
enforcement; Layer 4 is the one that keeps an agent from writing the `setdefault` mistake in the first
place.

## The override: `REDIS_PRODUCTION_FLUSH_OK`

Both Layer 1 and Layer 3 share a single escape hatch: `REDIS_PRODUCTION_FLUSH_OK=1`. One name to
learn instead of two.

- Only the exact string `"1"` disarms it. `""`, `"0"`, `"false"`, and `"no"` all leave the guard armed.
  The comparison is exact-equality, not `bool(...)`, because a truthiness bug here would be the one
  place this whole layer silently disables itself.
- It is read at call time, never at import time, so setting it after a process starts still works.
- On Layer 3 it works as a command prefix: `REDIS_PRODUCTION_FLUSH_OK=1 python -c "..."`.

Using it is legitimate when you genuinely mean to flush a per-process claimed test db and the guard's
own db-number check happens to be wrong for your setup, or when you're intentionally exercising flush
behavior in a throwaway sandbox. It is never legitimate to point it at a command that could reach
production; if you're unsure which db a client targets, inspect first with `redis-cli -n <db> DBSIZE`
rather than reach for the override.

## The `.pth` install mechanism

Layer 1 has to be live before any user code runs, in every venv, without the script author opting in.
Two mechanisms were considered; only one works.

**`sitecustomize.py` is unusable.** Homebrew ships its own `sitecustomize.py` earlier on `sys.path`
than any venv-level copy, and that shadowing is permanent: a guard installed as `sitecustomize.py`
inside a venv would silently never run. This was verified empirically and is recorded here as a closed
question so nobody retries it.

**A `.pth` file does run**, per site-directory, immune to that shadowing. `scripts/update/redis_flush_guard_pth.py`
writes two files into a venv's `site-packages`:

- `_redis_flush_guard_boot.py`, a shim whose entire body imports `tools.redis_flush_guard` and calls
  `arm()`, swallowing any exception.
- `zzz_redis_flush_guard.pth`, one line: `import _redis_flush_guard_boot`.

The `zzz_` prefix is load-bearing. `.pth` files are processed in sorted order, and this one has to run
after `_editable_impl_valor_bridge.pth`, the file that puts the repo root on `sys.path` via this
project's editable install. `_` is ASCII `0x5F` and `z` is `0x7A`, so `zzz_redis_flush_guard.pth`
always sorts after any `_`-prefixed `.pth`. Without the repo root already on `sys.path`, `import
tools.redis_flush_guard` inside the shim would fail, silently, since the shim swallows every
exception, leaving the guard installed on disk but inert.

`arm()` itself stays cheap on the hot path: it never imports `redis`. It only inserts a `sys.meta_path`
finder that runs `install()` after the *first real* import of `redis` or `redis.asyncio`, against a
fully initialized module. A process that never touches Redis pays nothing beyond that one finder
insertion and a single `stat()` self-heal check.

## Propagation and the self-heal

Three mechanisms keep every venv guarded:

1. **`/update` Step 3.05** calls `redis_flush_guard_pth.install_fleet()`, which discovers every
   `.venv`, `.worktrees/*/.venv`, and `.claude/worktrees/*/.venv` under the repo root and installs
   into each. It runs after dependency sync (not before), so a venv `uv sync` just created in the same
   `/update` run is guarded within that same run rather than left unguarded until the next update.
2. **The worktree venv bootstrap** (`agent/worktree_manager.py`) calls the same `install_into()`
   primitive when it provisions a new worktree venv.
3. **The guard's own self-heal**, triggered from `arm()` and from `install()`. `tools/__init__.py`
   carries the trigger: importing `tools` (which every hook, every `python -m tools.*` CLI, and every
   first-party module does) calls `arm()`, wrapped in a bare `except Exception: pass`, because nothing
   may ever break `import tools`. The self-heal checks whether `zzz_redis_flush_guard.pth` already
   exists in the current interpreter's site-packages (one `stat()` on the hit path) and, if it's
   missing, installs it, scoped only to the venv running the current interpreter.

`.claude/worktrees/{agent}/` checkouts are harness-created and reach neither of the first two wiring
points, which is exactly why the self-heal exists.

**The honest residual**: the very first interpreter start in a freshly created, unhealed venv, before
anything has imported `tools`, is unguarded. This is a convergence mechanism, not a first-run
guarantee. `python -m tools.doctor`'s `redis_flush_guard` check exists to make that remaining gap
visible: it spawns a subprocess per discovered venv and probes
`getattr(redis.Redis.flushdb, "_prod_flush_guarded", False)` in a fresh interpreter, because the boot
shim's own exception-swallowing means a broken install looks identical to a working one from the
filesystem alone. A failing check names the unguarded venv and its one-line remediation:
`python -m scripts.update.redis_flush_guard_pth --venv <path>`.

## The ACL user model: identity, not database number

The obvious-looking design, "let dbs 1-15 flush, deny db 0", does not exist as a server-side
mechanism. Two forms were tried and both failed:

- `ACL SETUSER u db:1` returns `ERR Error in ACL SETUSER modifier 'db:1': Syntax error`. There is no
  db-scoped ACL syntax at all.
- ACL selectors accept a key pattern, but `FLUSHDB`/`FLUSHALL` take no key arguments, so a selector's
  key pattern is vacuously satisfied no matter what it says. A user configured with
  `-flushdb '(+flushdb ~onlythis:*)'` successfully flushed db 0 in testing. A selector here looks like
  a solution and is a trap: it would silently reopen the incident this work exists to close.

Plain command denial works and is deliberately db-blind: `NOPERM ... has no permissions to run the
'flushdb' command`, in every database. So Layer 2 discriminates by **user identity** instead:

- **`valor-app`**, the production application user, gets `on >... ~* &* +@all -flushdb -flushall`.
  Full access to every command except the two that matter.
- **`default`** keeps `flushdb` and loses `flushall`. The test suite's bare `redis.Redis(db=N)`
  clients carry no credentials and authenticate as `default`, so they need zero test-side changes to
  keep working: `#2628`'s test-db-ownership guard (see below) can keep issuing `flushdb()` against a
  claimed test db exactly as before.

`scripts/update/redis_acl.py`'s planner always issues the complete declarative rule set, never a
delta, so re-running it twice converges to the same state regardless of interleaving.

## Applying the Redis ACL — requires human sign-off, not performed by this PR

`/update` never mutates the live Redis ACL. It calls `apply_redis_acl()` with no arguments, which is
report-only: it reads `ACL LIST`, computes the four commands that would converge the server, and
returns them. Applying those commands for real is a human-signed runbook, gated behind two independent
operator actions that `/update` never supplies together: a marker file and an explicit environment
opt-in.

Operator checklist, on the machine being applied:

1. Record the real `REDIS_APP_PASSWORD` in the vault `~/Desktop/Valor/.env`. Only on that machine.
2. `touch data/redis-acl-enabled`
3. `REDIS_ACL_APPLY=true python -m scripts.update.redis_acl --apply`
4. Add the staged `aclfile` directive to `/opt/homebrew/etc/redis.conf` by hand. The planner prints
   the exact line (`aclfile /opt/homebrew/etc/users.acl`) for the runbook; it never opens
   `redis.conf` for writing itself.
5. Restart Redis on your own schedule. `aclfile` is immutable at runtime (`CONFIG SET aclfile` errors
   with "can't set immutable config"), so persistence across a restart needs one restart. The runtime
   ACL rules from step 3 take effect immediately, with or without it.
6. Verify: `redis-cli ACL GETUSER valor-app` and `redis-cli ACL LIST`.
7. Rollback if needed: `redis-cli ACL SETUSER default ... +@all`, `redis-cli ACL DELUSER valor-app`,
   and drop the `aclfile` line from `redis.conf`.

There is no `.env.example` placeholder for `REDIS_APP_PASSWORD` and no blank-line pre-step in `.env`.
An earlier draft of this plan had one; it was dropped deliberately, because a placeholder would trip
`check_env_completeness` on every machine for a credential this PR never reads. `config/settings.py`'s
`RedisSettings.app_password` defaults to `""` for exactly this reason, and the report path plans
against a literal `<REDIS_APP_PASSWORD>` placeholder regardless of whether the setting is populated.

## The `REDIS_URL` rotation (#2661) is a separate step

`config/redis_bootstrap.py::configure_resilient_redis()` now forwards `parsed.username` into
`popoto.redis_db.set_REDIS_DB_settings(...)`. Today `parsed.username` is always `None`, because the
production `REDIS_URL` carries no username, so this line is byte-identical to prior behavior. It
exists to close the one first-party site that hand-parses `REDIS_URL` instead of using
`redis.Redis.from_url`, which is also the exact client the 2026-08-07 incident flushed.

That forwarding stays inert until `REDIS_URL` itself is rotated to `redis://valor-app:<pw>@host/0`,
tracked separately as **#2661**, gated on every machine in the fleet having the ACL from the runbook
above already applied. Rotating `REDIS_URL` before every machine has the ACL applied would mean popoto
tries to authenticate as `valor-app` against a server that doesn't recognize that user yet, taking the
worker, bridge, and dashboard down fleet-wide. Rotating it after is what makes Layer 2 effective for
the db-0 traffic that matters: popoto's client, once authenticated as `valor-app`, loses `flushdb` and
`flushall` at the server, closing the last gap Layer 1 alone can't reach (a non-Python client, or a
Python process outside every discovered venv).

## Doctor checks

`python -m tools.doctor` runs two checks under the `Services` category:

- **`redis_flush_guard`**: spawns a fresh interpreter per discovered venv and asserts the guard is
  live in each. Never a filesystem check; the boot shim's exception-swallowing means the `.pth` and
  shim files can exist on disk while the guard is inert.
- **`redis_acl`**: report-only, mirroring `/update` Step 3.135. Calls `apply_redis_acl()` with no
  arguments and fails the check (with a pointer to this doc) when the current ACL state drifts from
  the target rule set. It cannot fix drift; the runbook above is the only path to that.

`/update`'s own drift warning emits once per state transition rather than every 30-minute cycle
(`warn_state`, #2845): the signature is a digest of the planned commands, so a *changed* drift
re-warns, and a resolution clears the suppression and emits one resolved note. `python -m
tools.doctor` (a full run, not `--quick`) remains the unconditional on-demand check regardless of
suppression state — see [`update-warning-channel.md`](update-warning-channel.md).

## See also

- [`docs/features/test-db-ownership.md`](test-db-ownership.md) (#2628): the companion guard on the
  test side, per-process test-db ownership, so `tests/conftest.py`'s guard flushes only a db the
  current process actually claimed. That guard and this one are nested, not duplicated. #2628 stops a
  test process from flushing an *unowned* test db; this work stops *any* process from flushing
  *production*.
- [`docs/features/redis-durability.md`](redis-durability.md): the AOF floor that made the 2026-08-07
  incident recoverable at all, and the structural precedent (idempotent, non-fatal, `/update`-driven,
  doctor-checked) both Layer 1's propagation and Layer 2's planner reuse.
