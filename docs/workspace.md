# The workspace

A task's workspace is the clone a turn works in, the bare origin it pushes to, the kernel's mirror, and
the services its tests use. The harness side (the sandbox, the signal channel, reaping) is in
[harnesses.md](harnesses.md).

Serves Mission item 1 (delivering within authority) and the constraint
**bounded authority**: a turn works on a real clone and every push is an
`act` effect the kernel performs at request, inside the task's ceiling.

`core/workspace.py` provisions a task's workspace from a project spec
(`projects/`) under `work_dir/<task id>/` before the task starts, cloning from
a bare cache per repository URL (keyed by the URL's digest); a replay writes
its spec (`tests/emulator/workspace.py`).

| Part | What it is |
|---|---|
| `repo/` | the repository at the base commit with no later history, tags, or remotes besides `origin`; reflog expired and garbage collected; on a work branch; `.valor/` excluded from git |
| `origin.git/` | a local bare repository, the clone's only remote, the target branch at the base, every ref update logged, non-fast-forward pushes refused. No turn writes it; only the broker's `push_branch` and `merge`, from the kernel's process, after Tom releases the push. After a merge the kernel also puts the merged head on the target branch from its mirror, and the clone's work branch is fast-forwarded to it before each `patch` turn (`workspace.bring_merged`) |
| `kernel.git/` | the kernel mirror: seeded with the base, and fed each plan commit, candidate, and docs head (a docs session's kept head under `refs/valor/docs/`); the merge predicate and the merge read it. No turn reads or writes it |
| `home/` | git config (Valor's identity, no credential helper), an empty gh config, `pgpass`, and the profiles: `turn.sb`, each fresh session's, and `service.sb` |
| `cache/`, `state/work/`, `checks/` | the builder's package caches; the working session's `TMPDIR` and Claude Code config; each fresh session's checkout, `tmp/`, and `claude/`; each suite run's blind checkout and its own copy of the caches, cloned from `checks/seed/` (the base's setup output) |
| `pg/`, `redis/`, `ports.json` | the task's services, below, and their ports, recorded when chosen |
| `setup/`, `turns/` | each setup command's whole output, and each turn's whole stdout and stderr; no turn can read or write them |

The setup commands run once in `repo/` under `turn.sb`, with no time limit. A command's stdout and
stderr are one pipe the kernel copies into its log, since node aborts at startup when its stdout is a
file at a path the profile denies; a step ends when its own process ends, and a child it leaves
behind is reaped by the command's mark before the copy waits for EOF. A failure is recorded on
the Brief (`project.setup_result`) and the task still starts. A command's whole output, stdout
and stderr, goes to `setup/<n>.log`, and its result is `{command, exit, output}`, `output` naming
that file. Provisioning's git calls have no time limit either, and git's output, like a service
program's, goes to a file the kernel holds, not a pipe, so a call ends when git exits. Those files are made in `output/` under `settings.performing_dir` (mode 0700), a kernel path every task profile denies, so no turn, setup command, or service can open, list, or truncate one. A profile denies each kernel path both as written and with symlinks resolved, since the sandbox matches the resolved path. An interrupt of `start` (Ctrl-C, or SIGTERM or SIGHUP of the kernel) ends them: the
process groups get TERM, then KILL after `reap_grace_s`, the clone's temporary ref is removed, and
provisioning is refused as interrupted. Nothing reaches GitHub, and the clone has no PR, issue, or
later commit to read. A second signal during that cleanup does not end it: provisioning waits until the
cleanup is done, so `provision:<task>` is held until then. The resident kernel provisions the
same way: closing it interrupts a provisioning it runs and waits for that cleanup before the lock
is released.

**A provisioning that died.** Provisioning's git calls carry the mark `VALOR_TURN=provision-<task>`
in their environment, which every program git starts inherits. A kernel killed mid-provision
(SIGKILL, SIGTERM, a crash) leaves the task's directory, and its git, a setup command, or the
task's Postgres may run on, each in a session of its own. Recovery does not touch it; provisioning
redoes it. The kernel schedules provisioning for a task with a project and no workspace, and
under `provision:<task>` reads again whether the task still needs one: not stopped or merged, no
`workspace.provisioned` row, and no `workspace.failed` without a later steer. If it does and the
directory is there, the kernel removes it and makes it again, and the ports its `ports.json` held
are free to choose again. Removing a task directory (`workspace.remove`) stops the task's
services, reaps the mark `provision-<task>` and the mark `setup-<task>-<n>` of each
`setup/<n>.log`, and then clears the tree without following any link and clearing each entry's
flags and ACL first. Only a provisioning holding `provision:<task>` starts `provision-<task>` git;
the redo and the removal of an unfinished provisioning or an orphan reap only while holding that
lock, and the kernel lock keeps a second kernel from running, so what the reap finds is a dead
holder's. Removing a provisioned task's workspace takes no such lock: once its
`workspace.provisioned` row exists no provisioning runs for that task. A task carrying a
`workspace.failed` is provisioned again on Tom's next steer.

**The mirror's fetch** treats the builder's clone as hostile: its config
checked first; a gitfile, `commondir`, alternates, and shallow clones refused;
the sending side inside the turn's sandbox; the receiving git with fsck, one
pack under a size limit, a footprint watchdog, and replace refs and grafts
off. The receiving git's file-size limit covers its stderr file too, so a fetch whose stderr passes the limit is killed. A tree holding a top-level `.valor` (any case) is refused, since the
kernel makes `.valor` itself for a fresh session's inputs, written through
descriptors that follow no link and overwrite no file. A refused plan,
candidate, or docs head does not count.

## Per-task services

A project declares the services its tests need (`postgres`, `redis`, or none).
The binaries are Homebrew's, in a prefix a turn can write, so each runs under
`service.sb`: its own directory and port only, no outbound connection, marked
`valor.service.<task id>`. A port is the lowest free one that no unremoved
task and no task directory's `ports.json` names, chosen under the
`workspace:ports` lock. `python -m core workspace remove TASK` (stopped or
merged only) stops the services, deletes the directory, and frees the ports.
It also takes the directory a provisioning that died left for a task with no
workspace, once the task is stopped or merged and its provisioning is not
live (otherwise "being provisioned now"), and records `workspace.removed`.
For such a stopped task it is what stops whatever that provisioning left
running (its Postgres or Redis, a setup command, its git) and frees its
ports: the sweep never sees them, since the task has a row and no record
names its services.

A service program (`initdb`, `pg_ctl`, `redis-server`) runs to its own end,
with no time limit of Valor's; `pg_ctl` waits up to its own default of 60
seconds. A run starts the services before its first runner (the resident kernel
keeps them up between a task's steps and stops them when the task needs Tom,
is done, or is stopped, and a sweep leaves alone the services a live kernel
holds under `services:<task>`), and a stop of
the task (or a cancel of the run) interrupts the start the way an interrupt
of `start` ends provisioning; whatever had started is stopped when the run
returns. A program's output goes to a file in that same `output/` directory. A program's refusal carries its stderr, up to the file-size limit.

- **Postgres.** A cluster of the task's own on `127.0.0.1` only (no unix
  socket), port 5440 to 5599, scram-sha-256 on every login; the superuser
  password is random and removed once the roles exist. `app` owns database
  `app` and may create databases (and manage only the spec's extra roles); the
  passwords are in `home/pgpass`, and `PG*`, `DATABASE_URL`, and `TEST_DB_*`
  point the app at it. Separate clusters answer the demonstration's first
  incident, a machine cluster that trusted loopback (`docs/data.md`).
- **Redis.** One redis-server when the project asks, port 6400 to 6499, on
  127.0.0.1, no persistence, no unix socket, and the protected configs (`dir`,
  `dbfilename`), `DEBUG`, and `MODULE` closed; `REDIS_URL` points the app at
  it. A dependency of the repository under test, not the kernel.

**A check's services.** Each suite run of the test runner gets fresh
instances on the task's own ports (`workspace.check_services`): the task's
own are stopped first, the check's cluster gets the project's roles with new
passwords, its Redis starts empty, and on exit the check's instances are
stopped and removed (on macOS the removal clears every user flag, such as
`uchg`, and the ACL of each entry the suite wrote, before reading it, so none
blocks it) and the task's
own started again, even when the stop or the removal fails. No run sees what
another run, or the working session, wrote.

**A reviewer's setup.** Review sets up its checkout from the same check
services, then runs the project's setup commands there under a profile
(`workspace.setup_profile`) that writes only the checkout, its `cache/`, and
`setup-tmp/`, which is the setup's `TMPDIR` and holds uv's cache and managed
Pythons, and reads the services' password file in the session's `tmp/`, so
`psql` and libpq defaults work. The setup is candidate code and runs before
the session, so it cannot write the session's Claude Code or Pi directory or
its `TMPDIR`. Nor can it write a `.git` anywhere in the checkout, in any case,
or rename, remove, or change the mode, flags, or ACL of the checkout
directory itself: the repository the session finds holds only the kernel's
two commits and the kernel's config, so no commit message, hook, or
fsmonitor of the setup's reaches the session or runs in it. The session
itself runs the candidate's code too, which can write whatever the session
can, its Claude Code directory and `.valor/` included, until the turn is
reaped. So the session's Claude Code reads no settings file at all
(`--setting-sources ""`), and the reviewer's verdict is its final message on
the harness's stdout, not a file in the checkout. The rest of the
checkout stays writable, since the project's setup (`uv sync --frozen`)
makes `.venv` there. Afterwards the kernel reads the checkout through
descriptors with no link followed (`workspace.setup_left`): no directory
where the checkout was, a `.valor` or `.pi` entry in it, or an error reading
or writing it, is treated as the candidate's, and review records `changes`
naming it (leg `kernel`, no session, no reviewer's verdict).
