# Redis Flush Hardening

Two production Redis flushes, six weeks apart, both from a script that believed it was targeting a
test database. Issue #2645 closed the gap between "believed" and "was" with three independent layers,
each guarding a different boundary the offending code has to cross. The safety story is entirely
client-side and works from a bare Redis connection string alone — no server-side access control, no
provisioned credential, no `redis.conf` edit on any machine (issue #3004, operator decision
2026-08-25: "our stack should simply need the redis connection string and be able to safely work with
that").

Two of the three layers enforce. Layer 1 alone would have stopped both incidents, and Layer 2 would
have stopped the command that started the second one. Layer 3 is a knowledge layer, not an enforcement
one. The table below is precise about which is which.

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

## The Three Layers

| Layer | Boundary | What it stops | What it doesn't |
|---|---|---|---|
| 1. Process-wide flush guard | Every Python process started inside a repo venv | Any `.flushdb()` on db 0, any `.flushall()`, from any first-party or ad-hoc script | Non-Python clients; a process outside every discovered venv; a flush issued as execute_command("FLUSHDB") or through redis.cluster.RedisCluster, neither of which the monkeypatch covers |
| 2. PreToolUse hook validator | Agent-issued Bash commands | A flush written inline in a command Claude is about to run | A flush buried inside a file that's already been written; Bash is the only surface it sees; and it fails open: if the validator itself raises, the dispatcher logs and allows, matching every sibling validator except validate_merge_guard |
| 3. This documentation and the `CLAUDE.md` note | The agent's read-before-testing surface | The reasoning error at its source, before either script or command exists | Nothing mechanical: it's a knowledge layer, not an enforcement layer |

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

### Layer 2: PreToolUse hook validator

`.claude/hooks/validators/validate_no_redis_flush.py` blocks `.flushdb(...)` / `.flushall(...)` call
shapes and `redis-cli ... FLUSHDB` / `FLUSHALL` invocations in **executable command position** in any
Bash command an agent is about to run, with no Popoto-vocabulary gate (unlike the raw-delete
validator, which only fires alongside a `_POPOTO_CONTEXT` token). Since #3021 it scans commands, not
raw text co-occurrence: quoted-delimiter heredoc bodies whose consumer is not an interpreter are
stripped, and single-quoted plus substitution-free double-quoted string contents are masked, so prose
that merely mentions a flush (`git commit -m '...'`, `gh issue create --body "..."`, a heredoc
writing a doc file) passes. The masking stands down whenever an interpreter token (`bash`, `sh`,
`python*`, `node`, `xargs`, `eval`, `redis-cli`, and kin) survives outside the inert regions —
quoted code fed to `python -c` or piped into `bash` still blocks — and unterminated quotes or
heredocs fail closed. It catches a flush the moment it's typed. It cannot catch
one already sitting inside a file Claude wrote in an earlier step, because a Bash-string validator
only sees the command it's given, not the file's contents. That gap is exactly why Layer 1 exists and
why it is the layer that actually stopped the incident class: file contents at execution time reach
the guard regardless of how the command that runs them was assembled.

### Layer 3: documentation

This document and the `CLAUDE.md` § Manual Testing Hygiene paragraph. Layers 1 and 2 are both
enforcement; Layer 3 is the one that keeps an agent from writing the `setdefault` mistake in the first
place.

## The override: `REDIS_PRODUCTION_FLUSH_OK`

Both Layer 1 and Layer 2 share a single escape hatch: `REDIS_PRODUCTION_FLUSH_OK=1`. One name to
learn instead of two.

- Only the exact string `"1"` disarms it. `""`, `"0"`, `"false"`, and `"no"` all leave the guard armed.
  The comparison is exact-equality, not `bool(...)`, because a truthiness bug here would be the one
  place this whole layer silently disables itself.
- It is read at call time, never at import time, so setting it after a process starts still works.
- On Layer 2 it works as a command prefix: `REDIS_PRODUCTION_FLUSH_OK=1 python -c "..."`.

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
writes two files into a venv's `site-packages` for this guard (plus the two checkout-pin files described in
[worktree-venv-isolation.md](worktree-venv-isolation.md#bare-scripts-from-a-worktree-3141), whose `.pth` sorts
before this one so `import tools` inside the shim resolves to the invoking checkout):

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

## Doctor checks

`python -m tools.doctor` runs one check under the `Services` category for this layer:

- **`redis_flush_guard`**: spawns a fresh interpreter per discovered venv and asserts the guard is
  live in each. Never a filesystem check; the boot shim's exception-swallowing means the `.pth` and
  shim files can exist on disk while the guard is inert.

## What these layers do not cover

None of the three layers above can see a client outside a repo venv: a `redis-cli` typed in a bare
shell, a GUI Redis client, a client written in another language, or a Python process running outside
every discovered venv. Such a client can flush production db 0 today, and nothing in this repository
will stop it. This is accepted, on the record, for two reasons and no others:

1. **Nothing was switched off to accept this.** A fourth, server-side layer — an access-control rule
   set denying the all-databases flush command to a dedicated application user — was designed (#2645)
   but never applied on any machine, and was deleted rather than converged on (#3004). The
   outside-the-venv gap is today's actual posture, not a new one; what was abandoned was a *plan* to
   close it.
2. **The accepted premise is that safety belongs in the stack, not in server configuration.** A defense
   that requires provisioning a credential and editing `redis.conf` on every machine is not compatible
   with the stack needing only a connection string.

**Residual mitigation:** AOF is enabled ([`redis-durability.md`](redis-durability.md)), and the worker
exports every `AgentSession` to `data/session_archive.db` (`session-archive-freshness` doctor check).
Recovery, not prevention, is the answer for a flush issued from outside a repo venv.

## See also

- [`docs/features/test-db-ownership.md`](test-db-ownership.md) (#2628): the companion guard on the
  test side, per-process test-db ownership, so `tests/conftest.py`'s guard flushes only a db the
  current process actually claimed. That guard and this one are nested, not duplicated. #2628 stops a
  test process from flushing an *unowned* test db; this work stops *any* process from flushing
  *production*.
- [`docs/features/redis-durability.md`](redis-durability.md): the AOF floor that made the 2026-08-07
  incident recoverable at all, and the residual mitigation for the outside-the-venv gap above; also the
  structural precedent (idempotent, non-fatal, `/update`-driven, doctor-checked) Layer 1's propagation
  reuses.
