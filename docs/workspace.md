# The workspace

A task's workspace is the clone a turn works in, the bare origin it pushes to, the kernel's mirror, and
the services its tests use. The harness side (the sandbox, the signal channel, reaping) is in
[harnesses.md](harnesses.md).

Serves Mission item 1 (delivering within authority) and the constraint
**bounded authority**: a turn works on a real clone and every push is an
`act` effect Tom releases.

`core/workspace.py` provisions a task's workspace from a project spec
(`projects/`) under `work_dir/<task id>/` before the task starts, cloning from
a bare cache per repository URL (keyed by the URL's digest); a replay writes
its spec (`scripts/replay_workspace.py`).

| Part | What it is |
|---|---|
| `repo/` | the repository at the base commit with no later history, tags, or remotes besides `origin`; reflog expired and garbage collected; on a work branch; `.valor/` excluded from git |
| `origin.git/` | a local bare repository, the clone's only remote, the target branch at the base, every ref update logged, non-fast-forward pushes refused. No turn writes it; only the broker's `push_branch` and `merge`, from the kernel's process, after Tom releases the push |
| `kernel.git/` | the kernel mirror: seeded with the base, and fed each plan commit, candidate, and docs head (a docs session's kept head under `refs/valor/docs/`); the merge predicate and the merge read it. No turn reads or writes it |
| `home/` | git config (Valor's identity, no credential helper), an empty gh config, `pgpass`, and the profiles: `turn.sb`, each fresh session's, and `service.sb` |
| `cache/`, `state/work/`, `checks/` | the builder's package caches; the working session's `TMPDIR` and Claude Code config; each fresh session's checkout, `tmp/`, and `claude/`; each suite run's blind checkout and its own copy of the caches, cloned from `checks/seed/` (the base's setup output) |
| `pg/`, `redis/`, `ports.json` | the task's services, below, and their ports, recorded when chosen |
| `setup/`, `turns/` | each setup command's whole output, and each turn's whole stdout and stderr; no turn can write them |

The setup commands run once in `repo/` under `turn.sb`, with no time limit. A command's stdout and
stderr are one pipe the kernel copies into its log, since node aborts at startup when its stdout is a
file at a path the profile denies; a step ends when its own process ends, and a child it leaves
behind is reaped by the command's mark before the copy waits for EOF. A failure is recorded on
the Brief (`project.setup_result`) and the task still starts. A command's whole output, stdout
and stderr, goes to `setup/<n>.log`, and its result is `{command, exit, output}`, `output` naming
that file. Provisioning's git calls have no time limit either. An interrupt of `start` (Ctrl-C, or SIGTERM or SIGHUP of the kernel) ends them: the
process groups get TERM, then KILL after `reap_grace_s`, the clone's temporary ref is removed, and
provisioning is refused as interrupted. Nothing reaches GitHub, and the clone has no PR, issue, or
later commit to read. A second signal during that cleanup does not end it: provisioning waits until the
cleanup is done, so `provision:<task>` is held until then.

**The mirror's fetch** treats the builder's clone as hostile: its config
checked first; a gitfile, `commondir`, alternates, and shallow clones refused;
the sending side inside the turn's sandbox; the receiving git with fsck, one
pack under a size limit, a footprint watchdog, and replace refs and grafts
off. A tree holding a top-level `.valor` (any case) is refused, since the
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

A service program (`initdb`, `pg_ctl`, `redis-server`) runs to its own end,
with no time limit of Valor's; `pg_ctl` waits up to its own default of 60
seconds. A run starts the services before its first runner, and a stop of
the task (or a cancel of the run) interrupts the start the way an interrupt
of `start` ends provisioning; whatever had started is stopped when the run
returns. A program's refusal carries its whole stderr.

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
stopped and removed (the removal clears every user flag, such as `uchg`,
and the ACL of each entry the suite wrote, before reading it, so none blocks
it) and the task's
own started again, even when the stop or the removal fails. No run sees what
another run, or the working session, wrote.
