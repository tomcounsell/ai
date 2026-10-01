---
tracking: none
slug: m1-4-checks
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.4 The checks and the merge

Milestone 1.4 of [valor-rebuild.md](valor-rebuild.md). Goal: delivery
counts only after an independent check, and a merge reaches the real
branch only on Tom's tap (Mission item 1; Evidence "Independent checks").
The contracts are [sdlc-state-machine.md](../sdlc-state-machine.md)
(critique, `checks.test`, `checks.review`, `checks.docs`, the join, the
merge predicate, scheduling), [architecture.md](../architecture.md)
(verification, the sandbox split, execution records),
[harnesses.md](../harnesses.md) (fresh sessions, the sandbox, transcripts,
the workspace), [judgement-layer.md](../judgement-layer.md) (breadth and
governance, calibration discipline), and [machine.md](../machine.md) (RAM,
containers, the turn slot, the kernel key directory). Where this plan and a
doc differ, the plan says so and the build fixes the doc.

Built on `m1.3-judgement` at 17e193b45, which is stacked on milestone
1.2's proposed patch 2 (`m1.2-state-machine`, 40db35f8e). Neither is
merged: 1.2 is a delivery that did not pass and waits for Tom's feedback,
and 1.3 passed its checks and is held behind it. The fix recommended for
1.2 (`GIT_NO_REPLACE_OBJECTS=1` and `GIT_GRAFT_FILE=/dev/null` in
`core/git.py`'s `env()`, and the small non-blocking items) belongs to 1.2's
branch, not this one. This plan names designs (the router's runner
mapping, `verdicts.record_check`, `judgement_sites.breadth` and
`governance`), not line numbers, and each task's branch is rebased when the
stack below it moves.

**Stakes.** The milestone changes the kernel (the router's runners, the
fold, the broker's performers, the sandbox profiles) and opens the first
path from the kernel to a real GitHub branch with a credential the kernel
holds. A mistake here either merges work no independent check passed, or
hands a turn a credential. So every task is `critique_rounds: 2`,
`review_rounds: 2`.

## What is true today (checked, not assumed)

- `core/__main__.py` registers runners for `judge`, `clarify`, `plan`,
  `build`, and `patch`. `critique`, `checks.test`, `checks.review`, and
  `checks.docs` have none, so `python -m core run` returns `no runner` and
  a person records them with `python -m core verdict` (`leg: manual`).
  `verdicts.MANUAL_STAGES` lists `critique`, `test`, `review`, `docs`, and
  `manual_allowed` refuses a stage the runner mapping covers.
- The router (`core/router.py`) runs the first branch of `checks` that has
  a runner and no verdict for the current candidate, and returns on any
  result but `moved`. A branch whose runner fails leaves no verdict, so
  the next run starts it again and skips the branches that have one. The
  router passes `Context.check` to a check runner.
- `machine.fold` sets `Fold.session` from the `session_id` of **any**
  `turn.ended`. A fresh session's turn would therefore become the working
  session the next build or patch turn resumes. This must change before
  any fresh session runs.
- `runs.run_turn` takes the state a turn works in and records it on
  `turn.started`; `tasks.dispatch` renders `channel.md` (the working
  session's signal channel, with the effects the registered performers
  offer) and the stage file for a `State`. A `Check` is not a `State`.
- `record_check` already takes `breadth=` (test) and `governance_from=`
  (review, docs) and reads the judgement rows itself; both are optional
  today. `record_critique` takes `leg`, `model`, `usd_micros`, and no
  turn id.
- `broker.PERFORMERS` is one module-global dict; `__main__._performers`
  registers `PushBranch` and `Merge` for whichever task is being run or
  released. `Performer.perform` and `lookup` are synchronous and run git
  inside the event loop. Neither was changed by 1.2.
- `PushBranch` and `Merge` both push to `Brief.origin_url`, read from the
  workspace's `origin` at start; for every workspace so far that is a
  local bare repository. The broker reads the merge predicate's git facts
  from the builder's workspace (`broker._git_facts`), which the turn owns.
- Workspaces are built by scripts, outside the kernel:
  `scripts/replay_workspace.py` (clone at a base with no later history, a
  bare `origin.git`, `home/` with git config, empty gh config, sandbox
  profile, `harness.json`) and `scripts/demo_workspace.sh`. Every replay
  database lives in one shared cluster on `127.0.0.1:5439`, owned by one
  `test` role with the password `test`; teardown drops the database and
  the directory and leaves the run's redis-server running.
- `tools/workspace.py` holds `WorkspaceWrite` and `OutboxAppend`, used
  only by tests.
- The replay sandbox profile denies `~/src`, so a kernel workspace cannot
  live there. It starts from `(allow default)`, denies, then allows back
  the run directory.
- `core/binaries.py` lets the kernel run, outside a sandbox, only programs
  that are root's alone, file and every directory above it.
  `/usr/local/bin` on this Mac is `root:wheel 755`. Homebrew's prefix is
  Tom's.
- Apple's `container` CLI is not installed. Rosetta is not installed
  (`oahd` is not running; `/Library/Apple/usr/libexec/oah` holds only
  `RosettaLinux`).
- `tomcounsell/ai` is public on GitHub; its default branch is `main`, the
  live old system. A clone or fetch needs no credential; a push does.
- The rebuild repository's own suite is macOS-bound in places: it runs
  `/usr/bin/sandbox-exec`, `libsandbox`'s `sandbox_check`, `/bin/ps -E`,
  and requires the Command Line Tools' git under
  `/Library/Developer/CommandLineTools`. It also needs a Postgres where it
  can create the `valor_kernel` role and databases (`db.migrate`).
- Baseline material for calibration exists outside the repo in
  `~/src/valor-demo/items/` (`cut-a`, `pop-a` to `pop-c`, `pso-a`,
  `pso-a2`, `pso-b`, each with a `.key.md` and reference tests under
  `ref/`) and `~/src/valor-demo/results/`.
- Tests at the base: 435 passed, 5 live tests skipped.

## Recommendation: split 1.4 into four tasks

Yes, split. The milestone has four separable pieces of machinery, two of
which wait on machine changes only Tom can make. Each task goes through the
whole pipeline on its own branch, stacked on the one before.

| Task | What it lands | Needs Tom first | Closes in `verdict` |
|---|---|---|---|
| **1.4a** | Kernel workspace provisioning (per-task clone, bare origin, kernel mirror, per-task Postgres and Redis, project specs, `start --project`); fresh-session machinery; the critique runner; the fold fix; absorbs the replay teardown, shared role, and test-performer debts | nothing | `critique` |
| **1.4b** | Calibration records for breadth and governance (floors frozen first); then the test runner (breadth, then suite at base and head in fresh checkouts) and the docs runner (own checkout, path drop, governance after the turn) | nothing (live judgement spend under $1, through the builder's own key directory) | `test`, `docs` |
| **1.4d** | The GitHub credential for the merge, held in the kernel key directory; `push_branch` kept local; transcript copies with digests; performers registered per task; awaitable performers | creating the token, for the one live push; everything else is built and tested against a local smart-HTTP server | none |
| **1.4c** | The container verifier (kernel-built images, a fresh VM per verification, RAM measured) and the review runner (Opus, blind, governance first); the `verdict` command deleted | installing `container` and Rosetta, starting the container system | `review`; the command is deleted |

**Order: a, b, d, c.** 1.4a first because every runner needs provisioned
checkouts and the fresh-session machinery. 1.4b next because its runners
cannot route work until breadth and governance have calibration records,
and the review runner (1.4c) depends on the governance record too. 1.4d
before 1.4c because it can be built and tested in full without Tom (the
live push is a rollout step), while 1.4c cannot be built at all until the
container runtime is on the machine (no mocks). If Tom installs the
container runtime before 1.4b finishes, c and d may swap; the `verdict`
command is deleted by whichever task lands the last runner, which is
review in 1.4c.

## Every Done item, where it lands

From [valor-rebuild.md](valor-rebuild.md), 1.4, including what 1.2 and 1.3
added to it.

| Done item | Task | Main files |
|---|---|---|
| The kernel provisions each task's workspace, lifted from `scripts/replay_workspace.py`, including the app's environment so the suite can run | a | `core/workspace.py` (new), `core/__main__.py` (`start --project`, `workspace remove`), `core/settings.py`, `projects/` (new), `scripts/replay_workspace.py` (delegates) |
| Fresh sessions for critique, review, and docs as runners in `RUNNERS`; each stage removed from `verdict` as its runner lands; the command deleted | a (critique), b (docs), c (review, delete) | `core/fresh.py` (new), `harnesses/claude_code.py` (`fresh_turn`), `core/machine.py`, `core/tasks.py`, `core/verdicts.py`, `skills/sdlc/verdict.md` (new) |
| Through the router: docs commits outside `machine.is_doc_path` dropped at turn end and recorded as a `changes` finding; a failed or stopped branch leaves no verdict and the next run reruns only it; the docs session works in its own checkout so docs commits do not ride into the next candidate | b | `core/fresh.py`, `core/workspace.py` (mirror), `core/verdicts.py` |
| `checks.test` runs the suite at head and base, then the breadth call; `test.decided` carries the command, the failures at head that do not fail at base, the behaviors, and breadth's model, confidence, cost, guard id; the order of breadth and suite settled | b | `core/checks.py` (new), `core/verdicts.py` |
| The blind verifier: Opus in a fresh session, rerunning the tests in an Apple container built by the kernel; `review.decided` carries the governance boolean; container RAM measured | c | `core/container.py` (new), `core/checks.py`, `core/binaries.py`, `docs/machine.md` |
| `tools/push_branch.py` gains a GitHub credential held by the kernel and never by a turn, so a released merge reaches the rebuild branch on GitHub | d | `tools/push_branch.py`, `core/git.py`, `core/credentials.py`, `core/__main__.py` (`github-key`) |
| Transcript copies kept in the store with a digest | d | `core/transcripts.py` (new), `core/runs.py` |
| Review and docs runners always pass `governance_from`, the test runner always passes `breadth`; `record_check` accepts neither as optional from a runner | b (test, docs), c (review) | `core/verdicts.py` |
| Calibration records for breadth and governance, setting their floors, before either routes work; each re-checks Jev's reservation overhead against its own rows | b | `core/judgement_tasks.py`, `tools/jev.py`, cases under `~/src/valor-demo/items/judgement/` |
| Absorbs: Redis left running at replay teardown | a | `core/workspace.py` |
| Absorbs: replay databases sharing one `test` role | a | `core/workspace.py` (a cluster and role per task) |
| Absorbs: `tools/workspace.py` test-only performers moved to `tests/` | a | `tests/performers.py` |
| Absorbs: the broker's synchronous `perform` made awaitable | d | `core/broker.py`, `tools/push_branch.py` |
| Absorbs: performers in a module-global dict, keyed per task | d | `core/broker.py`, `core/__main__.py`, `core/tasks.py` |
| Leaves out: the headless browser (milestone 3); routing turns into containers | | |

No task adds a check, gate, hook, validator, review round, or approval
step. Each runner plays a checkpoint already in the granted pipeline (the
critique loop, review, the breadth check), firing the guard 1.2 seeded for
it (`critique.loop`, `review.loop`, `checks.test.breadth`), and the
governance boolean is the governance paragraph's own. The docs path rule is
the state machine doc's. The container rerun is the verifier the paragraph
names. Narrowing where the GitHub credential can reach, and denying a turn
the container runtime, take authority away; they hold no work.

## Shared design, used by every task

### The task directory

Kernel workspaces live under a new setting, `work_dir` (`VALOR_WORK`,
default `~/valor-tasks`), outside `~/src` because the turn sandbox denies
`~/src`. One directory per task:

```
<work_dir>/
  cache/<name>.git          bare clone per repository, fetched by the kernel; no turn reads it
  bin/                      shared tools (uv), read-only to every sandbox
  <task_id>/
    repo/                   the builder's clone: the working session's cwd
    origin.git/             local bare origin: push_branch's target; no turn writes it
    kernel.git/             the kernel mirror: plan commits, candidates, docs heads; no turn writes it
    home/                   gitconfig, empty gh config, pgpass, profiles/*.sb; turns read, never write
    cache/                  the builder's uv and npm caches
    checks/                 fresh checkouts and their caches; only the kernel and the check sessions write here
    pg/data, pg/run         the task's Postgres cluster and its socket; no turn reads or writes
    redis/                  the task's redis-server, when its project asks
```

### The kernel mirror

`<task>/kernel.git` is a bare repository only the kernel writes. When a
turn's plan is recorded (`plan.written`) or its candidate is collected
(`turn.collected` verdict `candidate`), the kernel fetches that one commit
from the builder's clone into the mirror under `refs/valor/plans/<turn>`
or `refs/valor/candidates/<turn>`; a fetch the kernel's git refuses
(`git.hostile`) means no plan or no candidate, as today. Every fresh
checkout is made from the mirror, never from the builder's clone. Docs
commits (1.4b) are fetched into the mirror under `refs/valor/docs/<turn>`
and never into the builder's clone. For a provisioned task the merge
predicate's git facts and the `merge` performer read the mirror
(`Brief.mirror`); a task started with `--workspace` (scripts, older tests)
has no mirror and reads its workspace as today.

Why: the turn owns its workspace's refs and objects. It can delete or
move a ref the merge needs, rewrite `.git/info/grafts`, or add a replace
ref. Fetching one named commit into a repository the turn cannot write
puts the candidate, the docs head, and the merge's source out of its
reach. It narrows 1.2's replace-ref finding without replacing 1.2's fix.

### Fresh sessions

A fresh session is one `claude -p` turn that never resumes and never reads
the working session. The machinery is `core/fresh.py`:

1. **A checkout of its own**, made by the kernel under
   `<task>/checks/<stage>-<key12>/`, where the key is the plan digest
   (critique) or the candidate's sha and turn (review, docs, test). The
   directory is removed and made again on every run, never followed
   through a symlink. Critique and review get a **blind checkout**: a new
   repository holding exactly two commits made by the kernel, `base` (the
   task's base tree) and `candidate` (the plan commit's or the candidate's
   tree), so `git diff HEAD~1` shows the change and no builder commit
   message, intermediate commit, or `.valor/` file exists. Docs gets a
   real clone of the candidate from the mirror, because its commits must
   descend from the candidate (predicate term 4).
2. **Inputs as files** under `.valor/inputs/`, written by the kernel from
   ledger rows: `request.md` (the instruction, verbatim), `answers.md`
   (each question, Tom's answer, and his feedback, with provenance),
   `diff.patch` (the change against the base), and per stage what the
   section below lists. The prompt names the files under short labels;
   what to do with them is the stage file.
3. **A sandbox profile of its own** (`home/profiles/<stage>-<key12>.sb`),
   written by the kernel: read and write only its checkout and the one
   `~/.claude/projects/` directory for that checkout; read the shared
   tools; on loopback reach the gateway, the task's Postgres port, and the
   dev ports. It denies `<work_dir>` as a whole before allowing its
   checkout back, so it cannot read the builder's clone (with
   `.valor/handled/*/done.md`), the mirror, other checkouts, other tasks,
   or the builder's transcripts. This is what makes the blindness
   structural rather than a request in a prompt.
4. **A Brief** rendered with the stage file and a new channel file,
   `skills/sdlc/verdict.md`, in place of `channel.md`: no questions, no
   effects, one verdict file. `tasks.dispatch` takes `fresh=True` for
   this; no performer is offered.
5. **One turn** through `runs.run_turn` with `state` set to the stage
   (`critique`) or to `checks` with `check` set (`review`, `docs`), built
   by a new `claude_code.fresh_turn` (the `workspace_turn` flags without
   `--resume`, model from the stage's seat: `frontier` for critique and
   docs, `reviewer` for review).
6. **The verdict** read from `.valor/verdict.json`: opened without
   following symlinks, a regular file, at most 256 KB, a JSON object. Then
   moved to `.valor/handled/<turn_id>/`. The kernel validates it and writes
   the verdict row through `verdicts`; the session's text never names its
   own guard, leg, model, or cost.

A fresh turn that fails, is stopped, or leaves no valid verdict file
writes no verdict. The runner returns `failed` (or `stopped`), the router
returns, and the next run starts that stage or branch again, and only it.

**The fold fix.** `Fold.session` takes a session id only from a turn whose
recorded state is a working state (`clarify`, `plan`, `build`, `patch`).
`turn.started` for a fresh session carries `fresh: true` and the check, so
`entry_finished` and `last_collected` are untouched by it.

### Project specs

A project is a TOML file in `projects_dir` (setting `VALOR_PROJECTS`,
default `<kernel checkout>/projects/`), reviewed like kernel code because
it names the commands the kernel runs to decide `red` or `pass`. Read once
at `start` and copied into the Brief (`Brief.project`), so editing a spec
never changes a running task.

```toml
name = "valor"
repo = "https://github.com/tomcounsell/ai.git"   # or a local path
branch = "<the rebuild branch>"                  # base: this branch's head unless --base
kind = "python-uv"                               # python-uv | django | node | plain
services = ["postgres"]                          # postgres, redis, or none
roles = ["valor_kernel"]                         # extra Postgres login roles the suite needs
setup = ["uv sync --frozen"]                     # makes a checkout runnable
suite = "uv run pytest -q -p no:cacheprovider --junitxml={junit}"
lint = "uv run ruff check ."
merge_url = "https://github.com/tomcounsell/ai.git"   # where the merge lands; absent: the local origin
target_branch = "<the rebuild branch>"
[env]
VALOR_TEST_DB = "app_test"
```

The suite command comes from the spec, never from the candidate, because a
command the candidate chose could be `true`. The spec for this repository
is the only one committed in 1.4a; the replay items keep their own (built
by `scripts/replay_workspace.py` from the item file). Client project specs
are added when a client task first starts (milestone 2), and may live
outside the repository through the setting.

## 1.4a in full: provisioning, fresh sessions, the critique runner

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
   `origin_url` (`merge_url` if the spec names one, else `origin.git`),
   and `project` (the spec, the allocated ports, the role names).

Provisioning runs before `task.started`, in a directory named by the new
task id, under a session advisory lock (`workspace:ports`) held through
the start transaction. Anything that fails before `task.started` is
written removes the directory and stops its services, and no task row
exists. `python -m core start INSTRUCTION --project NAME [--base SHA]
--budget-usd N ...` provisions and starts; `--workspace DIR` stays for the
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
password from the task's file. Which of this repository's tests run inside
a turn's sandbox (it runs `sandbox-exec` itself, nested) is **measured at
build** and written in the build record; it is the first project the
kernel will build after takeover.

### 2. A Postgres cluster and a Redis per task

The shared replay cluster and its shared `test` role are replaced by a
cluster per task, which closes both the "every replay database is owned by
one role with one password" opening and the "a turn could connect to
another run's database" opening.

- **Cluster.** `initdb` into `<task>/pg/data` with `--auth=scram-sha-256`
  and a random superuser password from a file deleted as soon as the app
  role exists; nothing keeps it. `listen_addresses = '127.0.0.1'`, the
  unix socket in `<task>/pg/run`, the port allocated below.
- **Roles.** `app` (login, `CREATEDB`, owner of database `app`), plus each
  of the spec's `roles`, each with a random password written to
  `<task>/home/pgpass` (mode 600), the one credential the app is meant to
  have. When the spec lists roles, `app` also gets `CREATEROLE`, which on
  Postgres 18 manages only roles it created and grants no superuser.
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
  service mark reaped, as the turn reaper does. An idle cluster costs
  36 MB and one after a suite about 350 MB (machine.md), so on the 16 GB
  machine only the running task's services are up.
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
| `turn.sb` (working session, setup) | `<task>/repo`, `<task>/cache`, its transcripts directory | `<work_dir>` outside them; `<task>/kernel.git`, `origin.git`, `home`, `checks`, `pg`, `redis` writes; `pg/data` reads | gateway, the task's ports, 8000 to 8009 |
| `<stage>-<key>.sb` (a fresh session, a suite run) | its checkout, its transcripts directory | `<work_dir>` outside its checkout, including `<task>/repo` and `kernel.git` | as above |
| `service.sb` | `<task>/pg` or `<task>/redis` | `<work_dir>` outside them; no outbound | binds its port only |

The kernel paths (the key directory, the machine cluster's data directory,
the backup disk) stay denied in all three.

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
| `task.started` | `start` | unchanged; the Brief document gains `mirror` and `project` |
| `turn.started` | `run_turn` | `fresh: true` and `check` on a fresh session's turn |
| `turn.ended` | `run_turn` | unchanged; the fold reads its session id only for working states |
| `critique.decided` | the critique runner | `leg: "session"`, `turn_id`, `model`, `usd_micros`; no provenance |
| `workspace.removed` | `workspace remove` | new: `by`, `via`, `at`, `role_played` |

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
| The kernel dies mid-run | services stay up until the next run's start (idempotent) or removal; the reaper's service mark finds them |
| A critique turn fails, is stopped, or leaves no or a malformed verdict file | no `critique.decided`; the run returns `failed`; the next run runs critique again on the same plan |
| `verdict.json` is a symlink (for example to the kernel's `pgpass`) or over 256 KB | refused unread; `failed` with that reason |
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

The fold and the router:
- After a critique turn, the next build turn resumes the working session's
  id, not the critique's (the bug fixed here; fails on the base).
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
  no row.

Live (`VALOR_LIVE=1`, declared spend $0.60): one real fresh critique
session on a toy repository writes a valid verdict through the gateway,
metered.

### Docs 1.4a makes true

`docs/architecture.md` (workspace provisioning built; the kernel mirror),
`docs/harnesses.md` (the workspace, per-task services, fresh sessions'
checkouts and profiles, the shared-role and Redis openings closed),
`docs/sdlc-state-machine.md` (critique runner exists; the manual verdict's
stages), `docs/machine.md` (one task's services up at a time),
`docs/tech-stack.md` (workspaces in the kernel), `docs/data.md`
(`workspace.removed`, the Brief's new fields, `critique.decided`'s leg),
`core/README.md`, `tools/README.md`, `tests/README.md`, and a
`projects/README.md` whose Not-here section carries the governance
paragraph verbatim.

### Out of scope for 1.4a

The test, review, and docs runners; calibration; the container; the GitHub
credential and fetching private repositories; transcripts; the performer
registry. Concurrent runs of two tasks' turns (one slot, machine.md).

## 1.4b outline: calibration, then the test and docs runners

### Calibration first

Neither breadth nor governance routes work before its record exists, so
the records come first in the task, against a build database
(`valor_rebuild_m14_calib`) with the builder's own key directory
(`VALOR_PG_PASSFILE=~/.config/valor-kernel-m14/pgpass`), never the
kernel's.

- **Floors frozen before run 1**, as 1.3 left them: breadth 0.70 primary,
  0.75 fallback; governance 0.65 primary, 0.70 fallback. Up to five runs
  per site; between runs only wording and a leg's fixed rendering change,
  each listed in the build record.
- **Breadth cases** (`checks.test.breadth.json`, outside the repo): each
  candidate diff from the baseline's thirteen runs and the
  demonstration's three deliveries, with three labels per case
  (`gap_state`, `gap_enum`, `gap_bound`). Labels come from the merged
  reference work, which humans wrote: a gap is labelled where a hidden
  reference test failed on the candidate, by what that test exercises
  (#872's archived-team guards, #191's list key name and hash exclusion,
  #633's bound in an existing test); no gap where every hidden test
  passed. Each label carries its evidence pointer and source `reference`.
- **Governance cases** (`governance.adds.json`, at most 50 hunks):
  positives from Tom's recorded decisions (the hunks of 1.2 and 1.3 that
  seed or route the checkpoints he granted on 2026-10-01; hunks on `main`
  that added a hook, validator, or approval step, the kind his paragraph
  names), negatives from hunks that add none (feature code, fixes,
  removals of a check, dependency bumps, docs). A label drafted from the
  paragraph's definition, with no decision of Tom's behind it, is marked
  `drafted`, and the record counts it apart, as judgement-layer.md
  requires ("how many labels are Tom's own").
- **The bar**, decided by default while Tom's per-tier numbers are a gap:
  governance lands when neither leg answers proceed (`false`) on any
  positive case at its floor, since a wrong `false` is the error that lets
  governance through without a tap, and a wrong `true` only costs one;
  breadth lands when each leg's errors are at most one per question and no
  case errs on all three. Brier and confusion counts are recorded with n,
  as information.
- **Jev's reservation overhead re-checked** from the records' own rows:
  every charge at most its reservation, and the largest billed over
  estimated ratio reported per site. Breadth inputs run to thousands of
  tokens against the judge's 1,016, so if any charge exceeds its
  reservation the estimate in `tools/jev.py` changes and the record is
  made again.
- **Landing**: `BREADTH.calibrated` and `GOVERNANCE.calibrated` hold the
  records' `task_sha256`. At rollout one more run writes each record to
  the real ledger and the digests are compared by hand, as 1.3 did.

### The order: breadth, then the suite

Settled: **breadth first, then the suite at base, then at head.** Breadth
reads only the diff, costs under a cent and seconds, and is reused on a
rerun by candidate; the suite costs minutes and the largest slice of the
16 GB machine (machine.md, "The turn's work", 3,000 MB). An outage in both
judgement legs then costs no suite run, and a red suite costs one wasted
breadth call. If breadth is unanswered with reruns left, the runner stops
before the suite and the branch reruns.

### The test runner: `core/checks.py` (new), `Check.TEST`

1. `judgement_sites.breadth(port, dsn, task)`, reused if answered for this
   candidate.
2. A fresh checkout at the base and one at the candidate (from the
   mirror), each made runnable by the spec's `setup` with the checks'
   own caches (`<task>/checks/cache`, which the builder cannot write, so a
   cache the builder poisoned cannot pass the kernel's run).
3. The suite under the check profile, marked and reaped, with a time limit
   (`suite_timeout_s`, default 1,800), writing JUnit XML to a path the
   kernel gives (`{junit}`). Each run writes `suite.ran`: commit, role
   (`base` or `head`), command, an environment digest (lockfile digests
   and the setup commands), exit code, failing test ids, duration, output
   tail, peak footprint. A `suite.ran` with the same commit, command, and
   environment digest is reused, so the base runs once per task and a
   crash after a suite does not rerun it.
4. Failures: test ids failing at head and not at base. A suite that gives
   no per-test result (no JUnit file) falls back to the exit code: red
   when head fails and base passes, and red with "the suite fails at base
   too, and without per-test results no failure at head can be told
   apart" when both fail. A head run that collected no test where the base
   collected some is a failure ("no test ran at head"). Tests that passed at
   base and are absent at head are listed on `test.decided` as
   `missing_at_head`, not as failures, since deleting a test that encoded
   the old behavior can be right; the reviewer and Tom see the list.
5. `record_check(TEST, verdict, breadth=id, command=..., failures=...,
   leg="session"... )`; the kernel computes the verdict.

### The docs runner: `Check.DOCS`

1. A real clone of the candidate from the mirror, under its own profile.
   Inputs: request, plan, `diff.patch`, and `previous-docs.patch` (the
   docs commits on the previous candidate, if any, so the session keeps
   what still holds).
2. One fresh turn (seat `frontier`); `verdict.json` gives the verdict and
   findings; the commits are read from git, never from the file.
3. The kernel reads the commits from the candidate to the checkout's head
   (refusing a hostile config, a head that does not descend, or a merge
   commit: each is a `changes` finding, so the branch ends), keeps the
   longest run of commits from the candidate that touch only doc paths
   (`machine.is_doc_path`, `git diff --no-renames`), and drops the first
   commit outside doc paths and every one after it, each dropped commit a
   `changes` finding naming its paths. The kept head is fetched into the
   mirror under `refs/valor/docs/<turn>`, never into the builder's clone.
4. Governance over the kept docs diff (candidate to head), asked after the
   turn because the diff exists only then: `judgement_sites.governance`.
5. `record_check(DOCS, verdict, head=kept, governance_from=ids, ...)`; any
   dropped commit makes the verdict `changes`.

`record_check` refuses a non-manual test verdict without `breadth` and a
non-manual review or docs verdict without `governance_from`. `test` and
`docs` leave `MANUAL_STAGES`.

### Tests (outline)

Every join row through the router with real runners and the scripted
session; a docs commit touching `core/` dropped and later doc-only commits
dropped with it; `CLAUDE.md` and `Skills/x.md` (case) dropped; after a
send-back the next candidate does not contain the docs commits and the
next docs session sees `previous-docs.patch`; a test runner whose head
suite is stopped leaves no verdict, and the next run reruns test only
while review's verdict stands; breadth both legs failing stops before any
suite runs; the base suite runs once across two candidates; a JUnit file
that is not XML, or a symlink, is a suite with no per-test result, never
a crash; a candidate whose `conftest.py` exits 0 before collecting is red
against a base that collects tests ("no test ran at head"), while one that
deletes a single test is not red and lists it under `missing_at_head`;
calibration records' digests equal the declarations'.

## 1.4d outline: the GitHub credential, transcripts, the performer registry

### The credential

- **Token.** A fine-grained personal access token, resource owner
  `tomcounsell`, repository access "only select repositories:
  `tomcounsell/ai`", permission Contents read and write (Metadata read is
  implied), nothing else, 90-day expiry, named `valor-kernel-push`. A
  classic token's `repo` scope reaches every repository Tom has; a GitHub
  App is more machinery than one repository needs.
- **Where it lives.** The vault `.env` holds the durable copy as
  `GITHUB_PUSH_TOKEN`, beside the judgement keys. `python -m core
  github-key` reads it and writes one file in the kernel key directory,
  `github-push.gitconfig` (mode 600, path derived from `pg_passfile` like
  `judgement-keys`), holding only
  `[http "https://github.com/tomcounsell/ai.git"] extraHeader =
  Authorization: Basic <base64 of x-access-token:TOKEN>`, scoped by git's
  URL matching to that one repository. It prints `written`, `kept`, or
  `missing`, never a value, comparing SHA-256 digests. It is the only code
  that writes the file.
- **How it reaches only the push.** `core/git.py`'s push and its remote
  reads take an optional credential file, used as `GIT_CONFIG_GLOBAL` for
  that one call instead of `/dev/null`. Only the `merge` performer passes
  it, only when the merge URL matches the file's URL. The header is a
  pinned header, as 1.2's review proposed, since credential helpers are
  refused and the PATH is system-only; it is delivered as a file path
  rather than as `-c http.<url>.extraHeader=...` on the command line,
  because any process of Tom's user, a running turn included, can read
  another's arguments with `ps`. A test checks that while a push runs, the
  token is in no process's arguments or environment (`ps -E -ww`).
- **`push_branch` stays local.** The Brief gains `push_url` (the local
  `origin.git`), which `push_branch` uses; `origin_url` stays the merge's
  destination. So the credential is used for exactly one (URL, branch)
  per task: the target branch the merge payload names and Tom's approval
  binds. A turn's own branches never reach GitHub.
- **How a turn can never read it.** The key directory is denied by every
  profile (turn, fresh session, suite, service); no environment a turn
  gets names it; the container never mounts it; no ledger row, exception,
  or log line carries the token (push errors are reported by git's exit
  code and stderr, which never echo the header, and a test asserts the
  token's digest appears in no row).
- **Rotation.** Tom creates a new token, replaces it in the vault `.env`,
  runs `python -m core github-key`, and revokes the old one. A refused
  push fails the merge effect with "GitHub refused the credential; rotate
  it" and the next run requests the merge again, as a failed outcome does
  now.
- **Tests** against a local smart-HTTP server (the trusted git's own
  `git http-backend` behind a loopback HTTP server that refuses a push
  without the expected header): a released merge lands with the file, is
  refused without it, `push_branch` never sends the header, and a
  workspace or mirror holding any `http.*` key is still refused. Live, at
  rollout, with Tom's token: one released merge to a scratch branch on
  `tomcounsell/ai`.

### Transcripts

When any turn ends (working or fresh), the kernel copies its Claude Code
session file into a `transcript` document (id: the turn id) and records
`transcript: {document, sha256, bytes, offset, prefix_changed}` on
`turn.ended`. The file is opened without following symlinks, must be a
regular file under the turn's own `~/.claude/projects/` directory, and is
capped at 50 MB (over that, the digest is kept and the text is not). A
resumed session's file grows, so each copy stores the bytes appended since
the previous copy when the earlier prefix still has the digest recorded
for it; otherwise it stores the whole file and marks `prefix_changed`,
which is itself the sign that something rewrote the transcript (compaction
or the turn). Tests: a turn that replaces its transcript with a symlink to
a scratch secret stores nothing and records why; an edited earlier line
shows `prefix_changed`.

### The performer registry, awaitable

The composition root builds a `broker.Performers` per task (`push_branch`
to `push_url`, `merge` from the mirror) and passes it to `request`,
`release`, `reconcile`, and `tasks.dispatch`. No module-global dict
remains. `perform` and `lookup` become `async`, with git run in a worker
thread, so a push no longer blocks the event loop the gateway's streams
run on (the bridges need this too). Test: two tasks with different
origins in one process never push to each other's.

## 1.4c outline: the container verifier and the review runner

### The review runner, `Check.REVIEW`

1. **Governance first**: `judgement_sites.governance(port, dsn, task,
   base, candidate)`, so an outage costs no container run and no Opus turn.
2. **The container rerun**, by the kernel, before any session reads
   anything: the candidate's tree exported from the mirror
   (`git archive`), the project's image, a fresh VM, the spec's offline
   setup, suite, and lint, results written to `verify.ran`: candidate,
   image digest, command, exit, failing test ids, lint result, duration,
   the memory limit, the measured peak footprint. Reused on a rerun of the
   review branch for the same candidate and image (after Tom's governance
   grant, only review reruns).
3. **The blind session** (seat `reviewer`, Opus) in a blind checkout. It
   **reads**: the request, Tom's answers and feedback, the plan file and
   the docs at the candidate (in the tree), `diff.patch`, `verify.json`
   (the container's results), and `effects.md` (the effect ledger's held,
   released, and refused effects). It **never reads**: `done.md` or
   any `.valor/` file of the builder, any transcript, the builder's commit
   messages, `turn.collected` text, the test branch's results, or the docs
   session's work. The profile makes the never-reads unreachable, not
   merely unmentioned. It may also run commands in its own checkout under
   its profile, with its own database in the task's cluster.
4. `verdict.json`: verdict, findings with kinds, governance instances by
   path and line with summary, incident, mission item, notes by instance
   id, `predicted_failure`, and a result per requirement.
5. `record_check(REVIEW, ..., governance_from=ids, governance=specs,
   notes=..., leg="session", turn_id, model, usd_micros)`;
   `review.decided` carries the governance boolean and its instances, as
   the writer computes them now.

`review` leaves `MANUAL_STAGES`, and with every stage covered the
`verdict` command, `MANUAL_STAGES`, `manual_allowed`, the `_manual`
helper, and the `--behavior` path are deleted. Old `leg: manual` rows fold
as before and stay in the attention log.

### The container: `core/container.py` (new)

- **Runtime.** Apple's `container`, installed from Apple's signed package
  to `/usr/local/bin` (root-owned), not from Homebrew, because the kernel
  runs only root-owned programs outside a sandbox; `binaries.CONTAINER`
  is checked before every call. Rosetta is installed for the image
  builder, which needs it even for arm64.
- **Base image**, built once per pinned base digest:
  `debian:bookworm-slim` for arm64 by digest; git with
  `/usr/libexec/git-core` present (so this repository's
  `binaries.require_git` accepts `/usr/bin/git` via `VALOR_GIT`); uv with
  the pinned Pythons; Node LTS from a pinned tarball; PostgreSQL 18 and
  redis-server from Debian or PGDG by pinned version; a kernel-owned
  entrypoint `/valor/run.sh` that starts the services the spec names
  inside the VM, copies the read-only source mount to `/work`, runs the
  offline setup, the suite, and the lint, and writes `/out/result.json`
  and the JUnit file.
- **The repository's own environment**: a dependency image per project and
  lockfile digest, `FROM` the base by digest, copying only the lockfiles
  and installing dependencies at build time with the network open
  (`uv sync --frozen --no-install-project`, `npm ci`). The run itself has
  no network, so the tests reach nothing; the project installs offline
  from what the image holds. A patch that changes no lockfile reuses the
  image. The newest three dependency images per project are kept, the
  rest pruned after a build; the builder VM is stopped after each build to
  give its memory back.
- **Isolation.** Images are run by digest, never by tag. The turn
  profiles deny executing `/usr/local/bin/container` and its helpers,
  looking up the container services' mach names, and reading or writing
  the runtime's data directory, so no turn can retag, replace, or enter
  an image or a VM. The VM mounts the exported source read-only and one
  output directory; nothing else of the host, never the key directory. It
  is killed, not stopped, on a stop or a timeout (machine.md: a graceful
  stop left the workload running), and removed after every run.
- **RAM.** `verify_memory_mb` (default 2,048) and `verify_cpus` (4). On
  the 16 GB machine the VM runs alone in the turn slot, before the Opus
  turn, with only the kernel, Postgres, and the task's services beside it
  (about 4.7 GB with the bridges); 2 GB fits the slot's budget. The build
  measures, on this 64 GB machine, the VM's footprint idle, under this
  repository's suite, and under a Django suite with Postgres, at 1 GB, 2
  GB, and 4 GB limits, plus the container system's resident daemons, and
  replaces machine.md's 1,024 MB estimate with the measurements.
- **This repository in Linux.** Its macOS-bound tests (sandbox-exec,
  `sandbox_check`, `/bin/ps -E`, the Command Line Tools' git) skip off
  Darwin under a `macos` marker; the container covers the rest, and the
  test branch's host run in a fresh checkout covers all of them. The
  count of tests run in each place is on `verify.ran`. See Questions.

### Tests (outline)

A container killed on a stop leaves no VM; an image retagged by hand is not
used (digest); a turn under its profile cannot run `container list`; the
VM reaches no network (a probe to the internet and to a loopback-bound
host port both fail); a passing candidate whose suite reads a host file
fails in the VM; the review verdict's governance comes from the judgement
rows, and a reviewer's line merges into a kernel instance; a review
rerun after a grant reuses `verify.ran`; `verdict` is gone from the
command line; live (`VALOR_LIVE=1`, under $3): one real blind Opus review
on a toy candidate with a container rerun.

## Machine changes needing Tom (rollout steps, none done in a build)

1. **Install Rosetta**: `softwareupdate --install-rosetta --agree-to-license`.
   Before 1.4c's build.
2. **Install `container`** from the signed package on Apple's GitHub
   releases (`apple/container`, the current release at install time),
   which puts `container` in `/usr/local/bin`; then
   `container system start`, which registers its launch agents and asks to
   download its default Linux kernel. Before 1.4c's build. The builder
   then pulls the pinned Debian base image once.
3. **Create the GitHub token** as specified under 1.4d, put it in the
   vault `.env` as `GITHUB_PUSH_TOKEN`, and run `python -m core github-key`
   from the kernel checkout. Needed only for 1.4d's live push and for the
   first real merge.
4. **Create a scratch branch** on `tomcounsell/ai` for the live push test,
   or say the test may create `valor/push-check` itself.
5. **At each task's merge, in the kernel checkout**: `uv sync`; for 1.4a,
   create `~/valor-tasks`; for 1.4b, the two calibration runs against the
   real ledger and the digest comparison. No migration in any task.

## Failure modes across the milestone

| Failure | Caught by |
|---|---|
| A fresh session reads the builder's narration | its profile denies the builder's clone, the mirror, and other transcripts; blind checkouts hold no builder commit |
| The next build resumes a fresh session | the fold reads session ids only from working-state turns |
| The suite command chosen by the candidate | the command comes from the spec copied into the Brief at start |
| A builder poisons the cache the kernel's suite uses | checks use their own caches the builder cannot write; the container rebuilds from the lockfile |
| Docs code rides into a merge | the path drop at turn end and predicate term 4 |
| Docs commits ride into the next candidate | they live only in the mirror |
| A provider outage spends a suite run or an Opus turn | breadth and governance are asked first and reused |
| A turn reads the GitHub token | key directory denied everywhere; the token never in argv or environment |
| The credential pushes somewhere Tom did not approve | used only by `merge`, to the URL and branch the approval binds; `push_branch` is local |
| A turn tampers with the verifier's image | runtime denied to every profile; images run by digest |
| A container outlives a stop | killed and removed; the review branch leaves no verdict |
| A transcript symlinked to a secret | opened without following links, checked to be a regular file in its own directory |
| One task's performer pushes for another | performers built per task |
| Container runtime not running | the review branch returns `failed` naming it; no verdict; the next run retries |

## Out of scope

- The headless browser (milestone 3) and routing turns into containers
  (open until one replay runs end to end in one).
- A second reviewer vendor (milestone 3).
- Fetching private client repositories beyond the one credential's
  repository (milestone 5's tools, or when a client task first needs it).
- Deleting removed workspaces automatically (a routine, milestone 4).
- The emulator and the takeover gate (1.5).
- 1.2's open fix in `core/git.py` (1.2's branch).
- Concurrent check branches; the router keeps running them one at a time,
  which the state machine doc allows.

## Tech debt paid

| Debt | Task |
|---|---|
| Redis left running at replay teardown | a |
| Replay databases sharing one `test` role and one password | a |
| `tools/workspace.py` test-only performers | a |
| `Fold.session` taken from any turn | a |
| The broker's synchronous `perform` | d |
| Performers in a module-global dict | d |
| The manual `verdict` command | a, b, c |
| machine.md's container memory estimate | c |
| harnesses.md's "the transcript copy is design" | d |

## Questions for Tom (assumed answers; the build proceeds on them)

1. **Split into four tasks, built a, b, d, c.** Assumed yes.
2. **The container rerun on this repository.** Its own suite is partly
   macOS-bound, and no Linux VM can run `sandbox-exec`. Assumed: the
   container runs every test that does not need macOS, the test branch's
   host run in a fresh checkout runs all of them, and `verify.ran` reports
   both counts. The alternative is a macOS VM, which Apple's `container`
   does not run.
3. **The token is Tom's own fine-grained token**, so the kernel's pushes
   appear on GitHub as pushed by Tom. Assumed acceptable. The alternative
   is a GitHub account of Valor's own with write access to the one
   repository, which would also let a branch rule on `main` refuse it;
   with Tom's token no branch rule can stop a push Tom himself could make,
   so the kernel's own restriction (the merge only, to the approved URL
   and branch) is what keeps it off `main`.
4. **The pinned header goes in through a kernel-owned config file, not
   `-c` on the command line**, because arguments are readable by a running
   turn. Assumed yes; it is still a pinned header, and helpers stay
   refused.
5. **Calibration labels without a labelling session.** Assumed: breadth
   labels come from the replays' hidden reference tests, governance labels
   from Tom's 2026-10-01 grants and the kinds of addition his paragraph
   names, the rest marked `drafted` and counted apart, with the landing
   bar in 1.4b. Tom may want to read the drafted governance labels before
   rollout's real-ledger run; that would be one message, not a session.
6. **Installing `container` from Apple's package rather than Homebrew.**
   Assumed yes: the kernel runs only root-owned programs outside a
   sandbox, and Homebrew's prefix is Tom's.

## Decided by default (reversible)

- `work_dir` is `~/valor-tasks`; project specs in `<kernel>/projects/`.
- One Postgres cluster and role set per task; ports 5440 to 5599 and 6400
  to 6499; services up only while the task's run holds the router.
- Workspace disk kept until `workspace remove`, which only Tom runs and
  only for a stopped or merged task.
- A failing `setup` does not refuse the start.
- Blind checkouts with two kernel commits for critique and review; a real
  clone for docs.
- Seats: `frontier` for critique and docs, `reviewer` for review.
- The verdict file is `.valor/verdict.json`, at most 256 KB.
- Breadth first, then the base suite, then the head suite; `suite.ran`
  reused by commit, command, and environment digest.
- The docs drop keeps the longest doc-only run of commits from the
  candidate.
- Governance for docs asked after the docs turn, for review before it.
- Container: 2 GB and 4 CPUs per VM by default, no network at run time,
  three dependency images kept per project.
- Transcripts: deltas per turn, 50 MB cap per copy.
- The token: fine-grained, one repository, Contents read and write, 90
  days.
- Live spend for the whole milestone's builds under $8, each live test
  declaring its spend.
