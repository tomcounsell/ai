# 1.4a in full: provisioning, fresh sessions, the critique runner

Part of task 1.4a of [m1-4-checks.md](m1-4-checks.md), which holds the shared design this section builds on.


### 1. `core/workspace.py` (new): provisioning

Lifted from `scripts/replay_workspace.py`; the script keeps its command
line and calls this module, adding only what is replay-specific (the
cache step through `gh`, `replay.json`, the leak check's inputs).

`provision(task_id, spec, *, base=None, source=None) -> Provisioned`:

1. **Cache.** A bare clone per repository in `<work_dir>/cache/`, made and
   fetched by the kernel's trusted git: over HTTPS anonymously for a
   public GitHub URL, or from a local path. `source` lets the replay
   script hand in its own cache. Fetching a private repository needs the
   credential, which is 1.4d's, so 1.4a refuses a non-public HTTPS
   repository with that reason.
2. **Clone** at the base (the branch's head unless `--base`), history up
   to the base only, no tags, the reflog expired and garbage collected,
   on a work branch (`valor/<task_id[:8]>`), `.valor/` in
   `.git/info/exclude`. Identical to the replay script's clone.
3. **`origin.git`**: bare, `HEAD` naming the target branch, the base
   pushed there, every ref update logged, non-fast-forward pushes refused,
   the clone's only remote.
4. **`kernel.git`**: bare, empty, owned by the kernel.
5. **`home/`**: git config (Valor's identity, no credential helper), an
   empty gh config, `pgpass` for the app's roles, and the working
   session's profile `profiles/turn.sb`.
6. **Services**, below.
7. **Setup**: each `setup` command run once in `repo/` under `turn.sb`,
   with the turn's environment and the task's caches, network open (so
   `uv sync` and `npm ci` work), marked with a provisioning id and reaped
   when it ends. A setup that fails is recorded on the Brief as
   `setup_failed` with its output tail, and the task still starts: making
   the environment work is then the build's job, and the turn sees why.
8. **The Brief's fields**: `workspace` (`repo/`), `mirror`, `harness`
   (profile, git config, gh config, env), `base_sha`, `target_branch`,
   `push_url` (the local `origin.git`, where `push_branch` goes),
   `origin_url` (the merge's destination), and `project` (the spec, the
   allocated ports, the role names). **In 1.4a `origin_url` is always the
   local `origin.git`**, so a project task merged with 1.4a alone works end
   to end on the local origin, with the merge read from the mirror;
   `merge_url` is honoured only from 1.4d, when the credential and the
   merge-target list exist. `PushBranch` takes `push_url` (falling back to
   `origin_url` for a Brief without one), so its destination is split from
   the merge's here, not in 1.4d.

Provisioning runs before `task.started`, in a directory named by the new
task id, under a session advisory lock (`workspace:ports`) held through
the start transaction. Anything that fails before `task.started` is
written removes the directory and stops its services, and no task row
exists. `python -m core start INSTRUCTION --project NAME [--base SHA]
...` provisions and starts; `--workspace DIR` stays for the
scripts and the tests that build their own.

**Per repository kind**, what "the app's environment so the suite can
run" means:

| Kind | Setup | Environment | Notes |
|---|---|---|---|
| `python-uv` | `uv sync --frozen` | `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR` under the task's `cache/`; `PATH` with `bin/` first | the rebuild repository and popoto |
| `django` | `uv sync --frozen` (or the spec's) | as `python-uv`, plus `DATABASE_URL`, `PG*`, `TEST_DB_*`; the app role has `CREATEDB` so Django can make its test database | psyoptimal, cuttlefish |
| `node` | `npm ci` | `npm_config_cache` under the task's `cache/` | dev servers on 8000 to 8009 |
| `plain` | the spec's | the spec's `env` only | |

For this repository, the spec adds `roles = ["valor_kernel"]` and env
`VALOR_PGHOST=127.0.0.1`, `VALOR_PGPORT=<task port>`, `VALOR_PG_OWNER=app`,
`VALOR_PG_PASSFILE=<task>/home/pgpass`, so the suite's `db.migrate` runs
against the task's cluster as its owner and logs `valor_kernel` in with a
password from the task's file. Nested `sandbox-exec` works, but `/bin/ps`
is setuid and fails with `EPERM` inside any `sandbox-exec`, so this
repository's tests that reach the reaper's `ps` calls cannot pass inside a
turn's or a check's profile. They are host-only tests: 1.4b decides
whether the test branch runs them in an unsandboxed kernel step over the
head checkout or lists them on `test.decided` as not run, so they are never
hidden behind "fails at base too".

### 2. A Postgres cluster and a Redis per task

The shared replay cluster and its shared `test` role are replaced by a
cluster per task, which closes both the "every replay database is owned by
one role with one password" opening and the "a turn could connect to
another run's database" opening.

- **Cluster.** `initdb` into `<task>/pg/data` with `--auth=scram-sha-256`
  and a random superuser password from a file deleted as soon as the app
  role exists; nothing keeps it. `listen_addresses = '127.0.0.1'`, the
  TCP on loopback only (no unix socket: a socket path under a long work directory passes the 103-byte limit), the port allocated below.
- **Roles.** `app` (login, `CREATEDB`, owner of database `app`), plus each
  of the spec's `roles`, each with a random password written to
  `<task>/home/pgpass` (mode 600), the one credential the app is meant to
  have. When the spec lists roles, `app` also gets `CREATEROLE`, which on
  Postgres 18 manages only roles it created and grants no superuser.
  `app` is also a member of `pg_signal_backend` (a test fixture's
  `DROP DATABASE ... WITH (FORCE)` ends backends of roles it cannot
  otherwise signal: autovacuum's, one it created) and
  `pg_read_all_settings` (`SHOW data_directory`), and of no other
  predefined role.
- **Ports.** Postgres from 5440 to 5599, Redis from 6400 to 6499: the
  lowest port not named by any task document whose task has no
  `workspace.removed` row, and not bound now, chosen under the
  `workspace:ports` lock. Recorded in `Brief.project.ports`. The working
  and check profiles allow outbound only to their own task's ports.
- **Redis**, when the spec asks: one redis-server on the task's port,
  `127.0.0.1`, no persistence, no unix socket, protected configs, `DEBUG`,
  and `MODULE` closed, as the replay script does now.
- **Run sandboxed.** `initdb`, `pg_ctl`, `postgres`, and `redis-server` are
  Homebrew's, in Tom's prefix, which a turn can write. The kernel runs them
  only under a service profile (`home/profiles/service.sb`): read and write
  `<task>/pg` or `<task>/redis`, bind only the task's port, the kernel
  paths and `<work_dir>` outside the task denied, marked with the mach
  name `valor.service.<task_id>`. A replaced binary then has the service
  profile's reach and no more, and a superuser login (should one ever
  exist) cannot `COPY ... TO PROGRAM` outside the task's directory.
- **Lifecycle.** The router starts the task's services at the start of a
  run (idempotent) and stops them when the run returns, in a `finally`:
  `pg_ctl stop -m fast`, the redis pid, then every process carrying the
  service mark reaped, as the turn reaper does. A `finally` does not run
  when the kernel is killed, so **at the start of every router run** the
  kernel stops the services of every other task **whose run is not live**:
  under the `workspace:ports` lock, for each other task with services, it
  tries that task's router lock (`pg_try_advisory_lock` on `run:<task>`);
  only when it gets it (no run holds it) does it stop that task's services
  (SIGTERM, then SIGKILL after the reap grace, to every process under a
  sandbox denying that task's `valor.service.<id>` name), then release the
  lock. A task whose run is live keeps its services, since the kernel has
  no machine-wide turn slot yet. The stopped processes are listed in a
  `services.reaped` row on the run's task. An idle cluster
  costs 36 MB and one after a suite about 350 MB (machine.md), so on the
  16 GB machine only the running task's services are up.
- **Removal.** `python -m core workspace remove TASK` (Tom's command;
  refused unless the task is `stopped` or `merged`) stops the services,
  deletes the directory, and writes `workspace.removed` with provenance,
  which frees the ports. Disk is otherwise kept after a stop
  (architecture.md: "retains its disk after a stop").

### 3. Sandbox profiles

`sandbox_profile` moves into `core/workspace.py` and takes what to allow
instead of one run directory. Three profiles, each with denies before
allows as today:

| Profile | Reads and writes | Denied beyond today's list | Loopback |
|---|---|---|---|
| `turn.sb` (working session, setup) | `<task>/repo`, `<task>/cache`, `<task>/state/work` (its `TMPDIR` and Claude Code config, so its transcripts) | `<work_dir>` outside them; `<task>/kernel.git`, `origin.git`, `home`, `checks`, `pg`, `redis` writes; `pg/data` reads | gateway, the task's ports, 8000 to 8009 |
| `<stage>-<key>.sb` (a fresh session, a suite run) | its checkout, its `tmp/` and `claude/`; for a suite run, its per-run cache | `<work_dir>` outside them, including `<task>/repo`, `state/`, `kernel.git`; `/private/tmp`, `/private/var/folders`, `~/.claude`, `~/.claude.json` | as above |
| `service.sb` | `<task>/pg` or `<task>/redis` | `<work_dir>` outside them; no outbound | binds its port only |

In all three: the kernel paths (the key directory, the machine cluster's
data directory, the backup disk) stay denied, and **writes are denied to
`~/Library/LaunchAgents`, the shell startup files (`~/.zshrc`,
`~/.zprofile`, `~/.zshenv`, `~/.bash_profile`, `~/.bashrc`, `~/.profile`),
`~/.local/bin`, `~/.local/share/claude` (where Claude Code installs
itself), all of `~/.claude` and `~/.claude.json` (each turn has its own
config directory), `~/.gitconfig`, `~/.config/git`, and `/opt/homebrew`**,
and every turn runs with `DISABLE_AUTOUPDATER=1`. This only removes
authority. It **narrows**, not closes, the harnesses.md Known opening that
a turn could leave a program where a later unsandboxed process of the user
runs it (other such places remain, and `/var/folders` caches stay
writable to the working session); the build record and harnesses.md say
"narrowed".

**The check's database password.** The check profiles deny `<task>/home`,
so the kernel copies the app's `pgpass` into each check's own directory
(`checks/<name>/tmp/pgpass`, mode 600, inside a path its profile allows
and outside the checkout's tree) for that run, and points `PGPASSFILE` and the spec's passfile variable at the copy.

### 4. Fresh sessions and the critique runner: `core/fresh.py` (new)

`critique_runner()` is registered for `State.CRITIQUE`:

1. Fold. Not in `critique`: `moved`. Stopped: `stopped`.
2. A blind checkout of the plan commit (from the mirror) against the base.
   Inputs: `request.md`, `answers.md`, `diff.patch` (base to plan commit),
   `plan.md` naming the plan file's path, its stakes sentence, and both
   counts as the builder set them, and `critiques.md` with earlier critique
   verdicts' findings on this task (kernel rows, so a second round can see
   whether the first round's findings were met).
3. One fresh turn (seat `frontier`) under its own profile.
4. `.valor/verdict.json` of the form
   `{"verdict": "sound"|"revise", "findings": [{"kind", "text"}],
   "raise": {"critique_rounds": n, "review_rounds": n}}`.
5. `verdicts.record_critique(..., leg="session", turn_id=..., model=...,
   usd_micros=<the turn's metered spend from turn.ended>)`. The writer
   checks the plan digest, the raises (0 to 2, never lowering), and sets
   `guard_id` to `critique.loop` when a `revise` sends the plan back, as
   today.
6. Return `moved`; the router folds to `plan` or `build`.

`record_critique` and `record_check` refuse a non-manual leg without a
`turn_id` and a `model`. `critique` leaves `MANUAL_STAGES`; `RUNNERS`
gains it, so `python -m core verdict TASK critique ...` is refused as
"has a runner".

### Rows

| Row | Written by | New or changed |
|---|---|---|
| `task.started` | `start` | unchanged; the Brief document gains `mirror`, `push_url`, and `project` |
| `turn.started` | `run_turn` | `fresh: true` and `check` on a fresh session's turn |
| `turn.ended` | `run_turn` | unchanged; the fold reads its session id only for working states |
| `critique.decided` | the critique runner | `leg: "session"`, `turn_id`, `model`, `usd_micros`; no provenance |
| `workspace.removed` | `workspace remove` | new: `by`, `via`, `at`, `role_played` |
| `services.reaped` | the router, at run start | new: the other tasks' service processes it stopped (pid, command, task, signal) |

No migration: a new event type and new Brief fields need none
(data.md, documents). `Brief.load` already ignores unknown fields.

### Absorbed in 1.4a

- **Redis left running at teardown.** Removal (and the replay script's
  teardown, which now calls it) stops the task's redis-server and reaps
  the service mark.
- **Replay databases sharing one `test` role.** Replays provision a
  cluster and roles per run through `core/workspace.py`; nothing uses the
  shared 5439 cluster but `scripts/demo_workspace.sh`, which builds only
  the demonstration's workspace and moves with the scripts in 1.5.
- **`tools/workspace.py`**: `WorkspaceWrite` and `OutboxAppend` move to
  `tests/performers.py`; `tools/README.md` stops naming them; every
  import changes in the same commit.

### Failure modes (1.4a)

| Failure | What happens |
|---|---|
| Clone or fetch fails, base not in the repository, spec unreadable or missing `suite` | `start` refuses with the reason; no directory, no task row |
| No free port in a range | `start` refuses, naming the range and the tasks holding it |
| `setup` fails | the task starts with `setup_failed` on its Brief; the first turn's prompt carries the output tail |
| The task's Postgres will not start at run start | the run returns `failed` naming the log path; no turn spends |
| The kernel dies mid-run | the next router run, for any task, stops every other task's services by their mark first and records `services.reaped` |
| The builder's clone carries alternates, a graft, a replace ref, a shallow file, or a hostile config | the fetch into the mirror is refused (alternates, shallow, config) or reads nothing the turn planted (grafts and replace refs off, upload-pack sandboxed); no plan or no candidate, with the reason |
| A critique turn fails, is stopped, or leaves no or a malformed verdict file | no `critique.decided`; the run returns `failed`; the next run runs critique again on the same plan |
| `verdict.json` or `.valor` is a symlink (for example to the kernel's `pgpass`), the file is a FIFO, it has more than one link, or it has holes | refused unread and without blocking; `failed` with that reason |
| The builder plants a file or symlink under `checks/` | its profile denies writes there; the kernel also removes and makes each checkout without following links |
| The fresh turn writes `.valor/effects/*.json` or `question.md` | ignored: a fresh session requests nothing and asks Tom nothing |
| A plan revision | a new plan digest, so a new checkout; the old one is left for the record until removal |

### Tests (1.4a)

All against real Postgres, real git, real `sandbox-exec`, and real
subprocesses; the scripted harness (`tests/scripted.py`) plays the fresh
session the way it plays the working session, reading its stage from the
Brief and writing `.valor/verdict.json` as a test steers it.

Provisioning:
- A provisioned clone holds no commit after the base (`rev-list --all
  --not BASE` is empty), has `origin.git` as its only remote with the
  target branch at the base, refuses a non-fast-forward push, and excludes
  `.valor/`; the Brief carries every field.
- Two tasks get different Postgres ports and passwords. Under the real
  sandbox, task A's `turn.sb` cannot connect to task B's port, cannot read
  B's `pgpass`, and cannot list `<work_dir>/B`.
- After provisioning no file holds the superuser password; the `app` role
  cannot `COPY ... TO PROGRAM`, cannot create a superuser, and can create
  a database. With `roles`, `app` creates and drops its own role.
- The cluster's `postgres` processes sit under the service sandbox
  (`sandbox_check` on the mark) and are gone after the run returns.
- Provisioning that fails after the clone (a bad `setup` is not a failure;
  a bad base is) leaves no directory and no `task.started`.
- `workspace remove` refuses a task in `build`; after removing a merged
  task, its ports are offered to the next task; its redis-server is gone.
- The replay script's existing tests pass through the new module, and a
  replay run no longer touches the shared 5439 cluster.
- A project task provisioned and run with 1.4a alone (manual verdicts for
  test, review, docs) reaches `merged` on its local `origin.git`: the
  manual docs head is fetched into the mirror, the predicate reads the
  mirror, the merge pushes from it, and `push_branch` goes to `push_url`.

The mirror fetch:
- A builder's clone carrying `objects/info/alternates` (pointing at a
  repository holding an object the candidate needs), a `shallow` file, or
  a hostile config is refused, and no `plan.written` or candidate is
  recorded.
- A clone with `.git/info/grafts` rewriting the candidate's parent, and one
  with a `refs/replace/` ref swapping the candidate's tree: the mirror
  holds the real commit and tree, and the predicate's paths are computed
  from them (each fails on a kernel without the mirror and 1.2's fix).
- The sending side runs under the turn's sandbox (`sandbox_check` on the
  upload-pack process during a fetch), and a fetch past
  `mirror_fetch_max_bytes` fails without filling the disk; a small pack
  whose deltas expand far past the limits (as large as this machine
  safely tolerates, never exhausting its memory) is cut by the size limit
  or the footprint watchdog and leaves no ref.

Services after a crash, on the 16 GB machine:
- Task A's run is killed with SIGKILL while its cluster and Redis are up;
  task B's next run stops them first (`services.reaped` lists them), and
  the summed footprint of every service process left on the machine is
  within machine.md's 400 MB workspace-cluster line plus 50 MB for Redis.
- Two runs at once: while task A's run holds its router lock with its
  services up, task B's run starts and leaves A's services running.

The fold and the router:
- After a critique turn, the next build turn resumes the working session's
  id, not the critique's (the bug fixed here; fails on the base); the same
  rows folded as a legacy task (no `sdlc` marker) give the working
  session's id through `_legacy` too.
- `sound` goes to `build`; `revise` with a round left goes to `plan`;
  `revise` with none goes to `build` with the findings in the build
  prompt; a raise applies and a lowering is refused.
- A critique turn that exits non-zero, one that is stopped mid-turn, one
  that writes no verdict file, and one that writes a malformed one: each
  leaves no `critique.decided`, and the next run runs exactly one more
  critique turn, not a plan turn.
- `verdict TASK critique sound` is refused as having a runner.
- A fresh turn's `.valor/effects/push.json` reaches no broker.

Blindness, under the real sandbox:
- The critique checkout's `git log` shows only the kernel's two commits;
  no builder message, no intermediate commit, no `.valor/handled`.
- The critique profile denies reading `<task>/repo/.valor/handled/*/done.md`,
  the builder's transcripts directory, and `<task>/kernel.git`, and
  allows its own checkout and transcripts directory.
- A `verdict.json` symlinked to a scratch file standing in for the
  kernel's `pgpass` is refused and the scratch file's contents appear in
  no row; a `.valor` directory that is a symlink to a directory holding a
  valid `verdict.json` is refused; a `verdict.json` that is a FIFO with no
  writer is refused at once rather than hanging the run.
- The builder writes a file into its `$TMPDIR` and into its Claude Code
  `todos` directory; the critic, under its profile, can read neither, nor
  anything under `/private/tmp`, `/private/var/folders`, or `~/.claude`
  but its own config directory.
- Under `turn.sb` and a check profile, creating a scratch file in
  `~/Library/LaunchAgents` and `~/.local/bin`, and opening `~/.zshrc` for
  append (writing nothing), are refused.

Live (`VALOR_LIVE=1`, declared spend $0.60): one real fresh critique
session on a toy repository writes a valid verdict through the gateway,
metered.

### Docs 1.4a makes true

`docs/architecture.md` (workspace provisioning built; the kernel mirror),
`docs/harnesses.md` (the workspace, per-task services, fresh sessions'
checkouts and profiles, per-turn `TMPDIR` and Claude Code config, the
shared-role and Redis openings closed and the user-startup-files opening narrowed, the kernel
mirror's sandboxed fetch),
`docs/sdlc-state-machine.md` (critique runner exists; the manual verdict's
stages), `docs/machine.md` (one task's services up at a time),
`docs/tech-stack.md` (workspaces in the kernel), `docs/data.md`
(`workspace.removed`, the Brief's new fields, `critique.decided`'s leg),
`core/README.md`, `tools/README.md`, `tests/README.md`, and a
`projects/README.md` whose Not-here section carries the governance
paragraph verbatim.

### Out of scope for 1.4a

The test, review, and docs runners; calibration; the container; the GitHub
credential; transcripts; fetching private repositories (left out of
1.4d too: `tomcounsell/ai` is public); the performer
registry. Concurrent runs of two tasks' turns (one slot, machine.md).

