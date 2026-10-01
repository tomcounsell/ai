---
tracking: none
slug: m1-4-checks
type: build
status: 1.4a did-not-pass, proposed patch awaiting Tom; 1.4b, 1.4d, 1.4c planned
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
| **1.4a** | Kernel workspace provisioning (per-task clone, bare origin, kernel mirror read by the merge, `push_url`, per-task Postgres and Redis, project specs, `start --project`); per-turn `TMPDIR` and Claude Code config; fresh-session machinery; the critique runner; the fold fix; absorbs the replay teardown, shared role, and test-performer debts | nothing (waits for, or carries, 1.2's replace-ref fix) | `critique` |
| **1.4b** | Calibration records for breadth and governance (floors frozen first); then the test runner (breadth, then suite at base and head in fresh checkouts) and the docs runner (own checkout, path drop, governance after the turn) | nothing (live judgement spend under $1, through the builder's own key directory) | `test`, `docs` |
| **1.4d** | The GitHub credential for the merge, held in the kernel key directory; the merge-target list; `merge_url` honoured; transcript copies with digests; performers registered per task; awaitable performers | choosing Valor's account or his own, creating the token, and writing the merge-target list, for the one live push; everything else is built and tested against a local smart-HTTP server | none |
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
| `tools/push_branch.py` gains a GitHub credential held by the kernel and never by a turn, so a released merge reaches the rebuild branch on GitHub | d (`push_url` split in a) | `tools/push_branch.py`, `core/git.py`, `core/credentials.py`, `core/__main__.py` (`github-key`), the merge-target list |
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
    state/work/             the working session's own TMPDIR and Claude Code config dir; only the builder writes it
    checks/                 fresh checkouts, each with its own tmp/ and claude/; only the kernel and that check write it
    checks/seed/            the dependency cache the base's setup filled; written once by the kernel's base run
    pg/data                 the task's Postgres cluster (TCP on loopback, no unix socket); no turn reads or writes
    redis/                  the task's redis-server, when its project asks
```

Every turn and every suite run gets its own `TMPDIR` and its own Claude
Code config directory (`CLAUDE_CONFIG_DIR`) inside a path only it may
write: the working session keeps `state/work/` for the life of the task
(resume needs the same config directory, which also moves its session
files to `state/work/claude/projects/`), and each check gets
`checks/<name>/tmp` and `checks/<name>/claude`. So the builder and a fresh
session share no `/tmp`, no `/var/folders` temp directory, no
`~/.claude/todos`, `shell-snapshots`, `session-env`, or `~/.claude.json`.
**The login, checked at the start of the build: a per-turn config
directory loses it.** `claude -p` (2.1.287) with `CLAUDE_CONFIG_DIR` set to
a fresh directory answers "Not logged in" and sends no request. With a
dummy `CLAUDE_CODE_OAUTH_TOKEN` it sends `Authorization: Bearer <dummy>` to
`ANTHROPIC_BASE_URL` (checked against a local probe server). So **the
gateway adds the credential itself and every turn carries a dummy token**:
the gateway drops the turn's `authorization` and `x-api-key` headers and
sets `authorization: Bearer <real token>` on each forwarded call. The real
token comes from the kernel: a long-lived token in the kernel key
directory (`claude-token`, made by Tom with `claude setup-token`, a rollout
step) when present, otherwise the access token in the Keychain item
`Claude Code-credentials`, read through the root-owned `/usr/bin/security`
at most once a minute and again after any 401. The Keychain token lasts
about eight hours and is refreshed only by Claude Code sessions on the
default config directory; the kernel never refreshes it (refresh tokens
rotate, and a kernel refresh could sign Tom's own sessions out). When it
has expired, a call fails with "the Claude login's access token has
expired: run any claude session, or install a long-lived token", the turn
fails, and the next run retries. The turn now holds no credential at all;
its known opening (a deliberate Keychain read through `security`) is
unchanged. Shared `~/.claude.json` is never used.

### The kernel mirror

`<task>/kernel.git` is a bare repository only the kernel writes, seeded at
provisioning with the base commit from the kernel's own cache (never from
the builder's clone). When a turn's plan is recorded (`plan.written`) or
its candidate is collected (`turn.collected` verdict `candidate`), the
kernel fetches that one commit from the builder's clone into the mirror
under `refs/valor/plans/<turn>` or `refs/valor/candidates/<turn>`. Every
fresh checkout is made from the mirror, never from the builder's clone.
Docs commits are fetched into the mirror under `refs/valor/docs/<turn>`
and never into the builder's clone: from the docs session's checkout in
1.4b, and from the builder's clone for the manual `verdict TASK docs
--head SHA` until then. **1.4a switches the merge predicate's git facts and
the `merge` performer to read the mirror** for a provisioned task
(`Brief.mirror`); a task started with `--workspace` (scripts, older tests)
has no mirror and reads its workspace as today.

**The fetch from the builder's clone is hostile input.** The receiving
side is the kernel's git in a repository the turn cannot write; the
sending side (`git-upload-pack`) would otherwise run unsandboxed in the
builder's clone, reading its config, `objects/info/alternates`, and
`info/grafts`. So each fetch:

- runs `git.hostile(builder)` first and refuses on any finding;
- refuses a source with `objects/info/alternates` or
  `objects/info/http-alternates`, or a `shallow` file;
- runs the sending side inside the turn's sandbox, as
  `--upload-pack='/usr/bin/sandbox-exec -f <task>/home/profiles/turn.sb
  -D ... <trusted git's libexec>/git-upload-pack'`, every path absolute,
  so whatever upload-pack reads it reads with the turn's own reach;
- runs the receiving side with `transfer.fsckObjects=true`,
  `fetch.fsckObjects=true`, `--no-tags`, `--no-write-fetch-head`, no
  shallow update, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_GRAFT_FILE=/dev/null`,
  the git time limit, and a file-size limit on the receiving process
  (`RLIMIT_FSIZE`, setting `mirror_fetch_max_bytes`, default 2 GB), with
  `-c fetch.unpackLimit=1` so every fetch is kept as one pack file and the
  limit bounds the total; macOS does not enforce memory limits, so a
  watchdog reads the receiving process's footprint every half second and
  kills it past `mirror_fetch_max_footprint_mb` (default 1,024);
- names exactly one commit by sha and one destination ref.

A refused or failed fetch means no plan or no candidate, with the reason
in the next prompt, as a hostile config does today.

Why: the turn owns its workspace's refs and objects. It can delete or
move a ref the merge needs, rewrite `.git/info/grafts`, or add a replace
ref. Fetching one named commit into a repository the turn cannot write
puts the candidate, the docs head, and the merge's source out of its
reach.

**Dependency on 1.2's fix.** 1.4a reads git facts with the kernel's git
in both the builder's clone and the mirror, so it needs 1.2's
`GIT_NO_REPLACE_OBJECTS=1` and `GIT_GRAFT_FILE=/dev/null` in `core/git.py`'s
`env()`. 1.4a's build waits for that fix on 1.2's branch and rebases onto
it. If Tom's feedback on 1.2 has not landed when 1.4a's build starts,
1.4a carries the same two lines in its own first commit, flagged in the
build record as 1.2's, to be dropped on rebase.

### Fresh sessions

A fresh session is one `claude -p` turn that never resumes and never reads
the working session. The machinery is `core/fresh.py`:

1. **A checkout of its own**, made by the kernel at
   `<task>/checks/<stage>-<key12>/repo/`, beside that check's own `tmp/`
   and `claude/`, where the key is the plan digest
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
   written by the kernel: read and write only its checkout, its own
   `tmp/` and `claude/`; read the shared tools; on loopback reach the
   gateway, the task's Postgres port, and the dev ports. It denies
   `<work_dir>` as a whole, `/private/tmp`, `/private/var/folders`, and
   `~/.claude` (with `~/.claude.json`) before allowing its own paths back,
   so it cannot read the builder's clone (with `.valor/handled/*/done.md`),
   the builder's `TMPDIR` or Claude Code state, the mirror, other
   checkouts, other tasks, or any transcript. The profiles start from
   `(allow default)`, as every profile here does, so the blindness rests on
   two things together: nothing the builder writes lands outside paths the
   kernel names (its own `TMPDIR` and config directory, its clone, its
   caches), and every one of those paths is denied to the fresh session. A
   system tool that needs a `/var/folders` path the deny breaks is found by
   the build's tests and allowed back read-only by its exact subpath, each
   listed in the build record. Fresh sessions' `PATH` puts the trusted git's
   directory (the Command Line Tools') first, so `git` never goes through
   Apple's `xcrun` shim and its cache under `/var/folders`, which stays
   denied.
4. **A Brief** rendered with the stage file and a new channel file,
   `skills/sdlc/verdict.md`, in place of `channel.md`: no questions, no
   effects, one verdict file. `tasks.dispatch` takes `fresh=True` for
   this; no performer is offered.
5. **One turn** through `runs.run_turn` with `state` set to the stage
   (`critique`) or to `checks` with `check` set (`review`, `docs`), built
   by a new `claude_code.fresh_turn` (the `workspace_turn` flags without
   `--resume`, model from the stage's seat: `frontier` for critique and
   docs, `reviewer` for review).
6. **The verdict** read from `.valor/verdict.json`: opened component by
   component, each relative to its parent's descriptor (`os.open` with
   `dir_fd`), every component with `O_NOFOLLOW` and the last also with
   `O_NONBLOCK`, then `fstat` must show a regular file of at most 256 KB,
   read through that descriptor, parsed as a JSON object. A symlinked
   `.valor`, a symlinked file, a FIFO, a socket, or a device is refused
   without blocking and without reading anything it points to. Then moved
   to `.valor/handled/<turn_id>/`. The kernel validates it and writes
   the verdict row through `verdicts`; the session's text never names its
   own guard, leg, model, or cost.

A fresh turn that fails, is stopped, or leaves no valid verdict file
writes no verdict. The runner returns `failed` (or `stopped`), the router
returns, and the next run starts that stage or branch again, and only it.

**The fold fix.** `Fold.session` takes a session id only from a turn whose
recorded state is a working state (`clarify`, `plan`, `build`, `patch`).
`turn.started` for a fresh session carries `fresh: true` and the check, so
`entry_finished` and `last_collected` are untouched by it. The same bug
sits in `machine._legacy`, which takes the session id from every
`turn.ended`; it is fixed the same way there (a turn whose `turn.started`
says `fresh: true` never sets the session), and a test drives it through a
legacy fold.

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
merge_url = "https://github.com/tomcounsell/ai.git"   # where the merge lands from 1.4d; absent: the local origin
target_branch = "<the rebuild branch>"
[env]
VALOR_TEST_DB = "app_test"
```

The suite command comes from the spec, never from the candidate, because a
command the candidate chose could be `true`. The spec is read from the
kernel checkout, not from any task's clone; but a candidate can edit
`projects/valor.toml`, and once Tom merges it, later tasks read the edit.
So the spec never decides where a merge may land: that is the
**merge-target list** (1.4d, the task that first honours `merge_url`):
ledger rows `merge_target.granted` with Tom's provenance, written only by
`python -m core merge-target add URL BRANCH --note TEXT` (always Tom's,
never role-played, like `guard.granted`), and read by the kernel only from
the ledger. `start` refuses a spec whose `merge_url` and `target_branch`
are not granted, and the `merge` performer refuses at request and at
release a payload not granted. A local `origin.git` of the task's own is
always allowed. A pair naming the remote repository's default branch (what
its `HEAD` names, read when the merge runs) is refused even if granted, so
`main` of `tomcounsell/ai` can never be a target. In 1.4a every merge goes
to the task's local `origin.git`, so no list is needed yet. A candidate's change to the spec's suite command still reaches
later tasks after Tom's merge; the reviewer and Tom see it in the diff. The spec for this repository
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
| `verdict.json` or `.valor` is a symlink (for example to the kernel's `pgpass`), the file is a FIFO, or it is over 256 KB | refused unread and without blocking; `failed` with that reason |
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

Services after a crash, on the 16 GB budget:
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
credential and fetching private repositories; transcripts; the performer
registry. Concurrent runs of two tasks' turns (one slot, machine.md).

## 1.4b outline: calibration, then the test and docs runners

### Calibration first

Neither breadth nor governance routes work before its record exists, so
the records come first in the task, against a build database
(`valor_rebuild_m14_calib`) with the builder's own key directory
(`VALOR_PG_PASSFILE=~/.config/valor-kernel-m14/pgpass`), never the
kernel's. judgement-layer.md binds: labels come from humans, and the
landing bars per tier are Tom's to set.

- **Frozen before run 1, by digest.** The floors (as 1.3 left them:
  breadth 0.70 primary, 0.75 fallback; governance 0.65 primary, 0.70
  fallback), the case set, and every label are written to the cases files
  and their SHA-256 digests recorded in the build record before the first
  call. Up to five runs per site; between runs only wording and a leg's
  fixed rendering change, each listed. No case is added, removed, or
  relabelled after run 1.
- **Breadth cases** (`checks.test.breadth.json`, outside the repo). Rule:
  every baseline run and demonstration delivery whose item has hidden
  reference tests, **except the three items 1.5's takeover gate scores**
  (popoto #191, psyoptimal #872, popoto #633), so the gate is not measured
  on cases the classifier was tuned on. Each case is the candidate diff
  with three labels (`gap_state`, `gap_enum`, `gap_bound`). Labels come
  from the merged reference work, which humans wrote: a gap where a hidden
  reference test failed on the candidate, by what that test exercises; no
  gap where every hidden test passed. Source `reference`, with an evidence
  pointer per label. Where the reference does not settle which of the
  three kinds a failure is, that label is `drafted`.
- **Governance cases** (`governance.adds.json`, at most 50 hunks). Rule:
  every hunk with added lines in (a) the commits of 1.2 and 1.3 that seed
  or route the checkpoints Tom granted on 2026-10-01 (positives by his
  grant), (b) the commits on `main` that added a file under
  `.claude/hooks/validators/` or a hook registration (positives, the kind
  his paragraph names), and (c) the ten most recent commits on `main`
  before 2026-10-01 that touch none of those paths (negatives), taking at
  most five hunks per commit in diff order until 50. A label with a
  decision of Tom's behind it (his grant, his paragraph's named kinds) is
  source `tom`; every other label, all of (c) included, is `drafted`.
- **Drafted labels count in neither bar** unless Tom confirms them. The
  build sends Tom one message listing the drafted labels (case, hunk or
  diff, proposed label, why), not a labelling session; each he confirms
  becomes source `tom` in a new cases file and a new record. Until then the
  record reports drafted cases' results as information only.
- **The bars go to Tom** (Questions, 5). Until he sets them, a site routes
  on its **entry check**: both legs right on every non-drafted case of
  the record. For governance, "right" counts both directions: a wrong
  `false` lets governance through without a tap, and a wrong `true` puts a
  tap on every diff that touches that kind of code, so a false positive
  fails the entry check as surely as a false negative. Brier scores and
  confusion counts are recorded with n, as information.
- **When a record misses its bar** (or its entry check, while the bars are
  open): the site does not route. Its dependent runners are not
  registered in `RUNNERS`, so those stages keep the manual `verdict` path
  and the router stops at them with `no runner`, as today; nothing is set
  to caution by default, because caution on every breadth question would
  send every candidate to the repair round and caution on every hunk
  would ask Tom for a tap per hunk. Concretely: breadth missing keeps
  `test` manual; governance missing keeps `docs` manual in 1.4b and
  `review` manual in 1.4c, and the `verdict` command is then not deleted.
  The build stops and asks Tom, as 1.3 did for the judge.
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
   mirror), each with the checks' pgpass copy and its own `tmp/`.
   **Caches.** The builder's caches are never used. The base's setup runs
   first, with `checks/seed/` writable, and fills it; the base is the
   merged commit the task started from, so nothing a candidate wrote is in
   the seed. Every later run (each head, and a base rerun) gets its own
   copy-on-write clone of the seed (APFS `clonefile`, near free on disk),
   writable during its setup and its suite (a read-only cache breaks
   `uv run`, which syncs first; the test runs the real suite command), and
   deleted after the run. So a
   candidate's setup or suite can write only its own copy, and nothing it
   writes reaches the next candidate's run.
3. The suite under the check profile, marked and reaped, with a time limit
   (`suite_timeout_s`, default 1,800), writing JUnit XML to a path the
   kernel gives (`{junit}`). Each run writes `suite.ran`: commit, role
   (`base` or `head`), command, an environment digest (lockfile digests,
   the setup commands, the spec's `env`, and the versions of the tools in
   `bin/`, uv's included), exit code, test ids by outcome (passed, failed,
   errored, skipped), duration, output tail, peak footprint, and an
   `infrastructure` flag. A `suite.ran` with the same commit, command, and
   environment digest is reused, so the base runs once per task and a
   crash after a suite does not rerun it, **except** one that timed out or
   failed for an infrastructure reason (setup failed, the run was killed or
   stopped, the task's Postgres was down, the JUnit file is missing or
   unreadable while the exit code says the runner itself failed): those are
   never reused, and run again.
4. Failures: test ids failing or erroring at head that passed (or did not
   exist) at base; and **every test that passed at base and is absent or
   skipped at head**, unless the diff deletes its definition (the test's
   file is deleted, or a removed line in the diff of that file defines the
   test's function or class name, parametrized ids reduced to the name).
   A skip is counted as an absence because a candidate can skip a failing
   test as easily as delete it. When the diff touches a test's
   parametrize decorator, its ids missing at head count as deleted. For
   non-Python kinds the rule is per kind: where the JUnit id names a test
   whose name string the diff removes, it is deleted; otherwise the missing
   ids are listed for the reviewer and Tom rather than judged. Deleted
   definitions are listed on
   `test.decided` as `deleted_at_head`, for the reviewer and Tom. A suite
   that gives no per-test result (no JUnit file) falls back to the exit
   code: red when head fails and base passes, and red with "the suite
   fails at base too, and without per-test results no failure at head can
   be told apart" when both fail. This defines how the existing
   `checks.test` question ("the suite passes at head where it passed at
   base") is answered; it adds no check, and the build record says so.
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
   This follows sdlc-state-machine.md (the governance boolean "over their
   diff") and overrides valor-rebuild.md's 1.4 Done line, which says one
   governance judgement per hunk "asked before the reviewer's or docs
   turn": before is impossible for docs, whose diff the turn makes. The
   build fixes valor-rebuild.md to say before the reviewer's turn and
   after the docs turn.
5. `record_check(DOCS, verdict, head=kept, governance_from=ids, ...)`; any
   dropped commit makes the verdict `changes`.

Every fresh checkout in 1.4b and 1.4c (test, docs, review) is made the way
1.4a's critique checkout is after its patch: a candidate whose tree holds a
top-level `.valor` (any letter case) is refused at collection and at
checkout, `.valor` must not exist before the kernel makes it, inputs and
any password-file copy are written relative to a descriptor with
`O_NOFOLLOW` and `O_EXCL`, no verdict file may exist before the turn, and
any turn-chosen text the kernel embeds in a file it writes is JSON-quoted.
For docs, whose checkout is a real clone, the same refusal covers the docs
session's own commits.

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
(every base test absent at head); one that marks a failing test `skip` is
red; one that deletes a test's definition is not red and lists it under
`deleted_at_head`; one that deletes a test's file but keeps the function
elsewhere under the same name is judged by the name's removal; a head
suite that writes into its cache leaves the next candidate's run
unaffected (the next run's copy is cloned from the seed, and a planted
file is absent from it); a `suite.ran` that timed out is run again, never
reused; changing a tool's version in `bin/` changes the environment digest
and reruns the base;
calibration records' digests equal the declarations'.

## 1.4d outline: the GitHub credential, transcripts, the performer registry

### The credential

- **Whose token: two rollout options, Tom chooses** (Questions, 3). The
  code is the same for both; only who owns the token differs.
  - **Recommended (the driving session's decision, Tom to confirm): a
    GitHub account of Valor's own**, added to `tomcounsell/ai` as a
    collaborator with write access, holding a fine-grained token for that
    repository (Contents read and write; Metadata read is implied; nothing
    else; 90-day expiry; named `valor-kernel-push`). Because it is not
    Tom, a repository ruleset on `main` (restrict updates, Valor not on the
    bypass list) makes GitHub itself refuse any push of Valor's to `main`,
    whatever the kernel does. A second ruleset may let Valor update only
    the rebuild branch.
  - **Fallback: Tom's own fine-grained token** with the same scope. No
    ruleset can refuse a push Tom himself could make, so with this token
    only the kernel's restrictions keep it off `main`.
  - Either way the kernel refuses `main` on its own (below). A classic
    token's `repo` scope reaches every repository its owner has; a GitHub
    App is more machinery than one repository needs.
- **Where the merge may land.** Only a (URL, branch) pair granted by a
  `merge_target.granted` ledger row (`python -m core merge-target add URL
  BRANCH --note TEXT`, Tom's alone, never role-played; see Project specs),
  never the remote's default branch, checked at `start`, at the merge
  request, and at release. A candidate's edit to `projects/valor.toml` cannot add a target.
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
  another's arguments: the critique of this plan confirmed that
  `pgrep -lf` run inside `sandbox-exec` shows other processes' full
  arguments. A test checks that while a push runs, the token is in no
  process's arguments or environment, both from the kernel (`ps -E -ww`)
  and from a probe running under `turn.sb` (`pgrep -lf`, `ps -E -ww`).
- **`push_branch` stays local.** `push_url` (1.4a) keeps `push_branch` on
  the local `origin.git`. 1.4d is where `origin_url` becomes the spec's
  `merge_url` for a pair in the merge-target list. So the credential is
  used for exactly one (URL, branch) per task: the target branch the merge
  payload names and Tom's approval binds. A turn's own branches never
  reach GitHub.
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
  workspace or mirror holding any `http.*` key is still refused; a merge
  to a pair missing from the merge-target list, or to the remote's default
  branch (the local server's `HEAD`), is refused at request and at
  release, with an edited `projects/valor.toml` naming it. Live, at
  rollout, with the chosen token: one released merge to a scratch branch
  on `tomcounsell/ai`.

### Transcripts

When any turn ends (working or fresh), the kernel copies its Claude Code
session file into a `transcript` document (id: the turn id) and records
`transcript: {document, sha256, bytes, offset, prefix_changed}` on
`turn.ended`. With the per-turn config directory (1.4a) the session file
is under the turn's own `<config dir>/projects/`. Subagents a turn starts
write their transcripts as separate files beside the session's (in the
session's directory), and those are copied too, each with its own digest,
listed on the same `turn.ended`. Each file is opened component by
component without following symlinks (as `verdict.json` is), must be a
regular file under that directory, and is
capped at 50 MB (over that, the digest is kept and the text is not). A
resumed session's file grows, so each copy stores the bytes appended since
the previous copy when the earlier prefix still has the digest recorded
for it; otherwise it stores the whole file and marks `prefix_changed`,
which is itself the sign that something rewrote the transcript (compaction
or the turn). Tests: a turn that replaces its transcript with a symlink to
a scratch secret stores nothing and records why; an edited earlier line
shows `prefix_changed`; a live turn that starts a subagent (`VALOR_LIVE`,
under $0.30) has the subagent's transcript copied with its own digest.

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
  is checked before every call, and so is every helper the CLI runs under
  `/usr/local/libexec/container/` (the API server and the runtime and
  network plugins), each with `binaries.require`. Rosetta is installed for
  the image builder, which needs it even for arm64.
- **The trust boundary is the daemon and its data, not the CLI.** The
  container system runs as Tom's user from launch agents, keeps images and
  VM state in its data directory, and is reached over its mach services.
  So every profile here (turn, fresh session, suite, service) denies
  executing `/usr/local/bin/container` and anything under
  `/usr/local/libexec/container/`, looking up the container services' mach
  names, reading or writing the runtime's data directory, and writing its
  launch-agent plists (covered by the `~/Library/LaunchAgents` write deny
  of 1.4a, and named explicitly here). The exact data path and mach names
  are read from the installed release at build and written in the build
  record.
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
- **Isolation.** Images are run by digest, never by tag. With the denies
  above, no turn can retag, replace, or enter an image or a VM. The VM mounts the exported source read-only and one
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
fails in the VM; a helper under `/usr/local/libexec/container/` made
group-writable in a scratch copy of the layout is refused by
`binaries.require`; under every profile, writing a scratch plist named
like the runtime's in `~/Library/LaunchAgents` and reading the data
directory are refused; the review verdict's governance comes from the judgement
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
3. **The GitHub credential, one of two options** (1.4d; Questions, 3).
   *Recommended:* create a GitHub account for Valor, add it to
   `tomcounsell/ai` as a collaborator with write access, add a repository
   ruleset on `main` restricting updates with Valor not on the bypass list,
   and create Valor's fine-grained token for the one repository.
   *Fallback:* create Tom's own fine-grained token with the same scope.
   Either way, put it in the vault `.env` as `GITHUB_PUSH_TOKEN` and run
   `python -m core github-key` from the kernel checkout. Needed only for
   1.4d's live push and for the first real merge.
4. **Grant the merge targets**: `python -m core merge-target add
   https://github.com/tomcounsell/ai.git <rebuild branch> --note "..."` (and
   the scratch branch for the live test), from the kernel checkout. Needed
   at 1.4d's rollout.
5. **Create a scratch branch** on `tomcounsell/ai` for the live push test,
   or say the test may create `valor/push-check` itself.
6. **At each task's merge, in the kernel checkout**: `uv sync`; for 1.4a,
   create `~/valor-tasks`, and rebase onto 1.2's merged fix (or drop the
   carried copy of it); for 1.4b, the two calibration runs against the
   real ledger and the digest comparison. No migration in any task.

## Failure modes across the milestone

| Failure | Caught by |
|---|---|
| A fresh session reads the builder's narration | the builder writes only paths the kernel names (its clone, caches, its own `TMPDIR` and Claude Code config); every fresh profile denies all of them and `/private/tmp`, `/private/var/folders`, `~/.claude`; blind checkouts hold no builder commit |
| The mirror fetch runs the turn's choices | hostile config, alternates, and shallow refused; upload-pack sandboxed; grafts and replace refs off; fsck, size and time caps |
| A merge target edited into the repository's spec | merge targets are Tom's ledger rows, never the spec; the remote's default branch is always refused |
| A test skipped or deleted to go green | an absent or skipped test that passed at base is a failure unless the diff deletes its definition |
| Services left up after a kernel crash | every router run first stops other tasks' services by their mark |
| The next build resumes a fresh session | the fold reads session ids only from working-state turns |
| The suite command chosen by the candidate | the command comes from the spec copied into the Brief at start |
| A builder or a candidate poisons the cache the kernel's suite uses | checks never use the builder's caches; each run gets a clone of a seed only the base filled, deleted after the run; the container rebuilds from the lockfile |
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
| `Fold.session` taken from any turn (in the fold and in `_legacy`) | a |
| harnesses.md's opening: a turn can write `~/Library/LaunchAgents`, shell rc files, `~/.local/bin` (narrowed) | a |
| A shared `TMPDIR`, `/tmp`, and Claude Code state between turns | a |
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
3. **Whose GitHub token.** Decided by the driving session, Tom to
   confirm: a GitHub account of Valor's own with write access to
   `tomcounsell/ai` and a ruleset on `main` that refuses it, so GitHub
   itself stops a push to `main` whatever the kernel does. Fallback, if Tom
   prefers: his own fine-grained token, where only the kernel's merge-target
   list and its refusal of the default branch keep pushes off `main`. Both
   are planned as rollout options; the code is the same.
4. **The pinned header goes in through a kernel-owned config file, not
   `-c` on the command line**, because arguments are readable by a running
   turn. Assumed yes; it is still a pinned header, and helpers stay
   refused.
5. **Calibration bars.** judgement-layer.md leaves the bars per error
   tier to Tom. Assumed until he sets them: each site routes on its entry
   check (both legs right on every case whose label is `reference` or
   `tom`), with false positives counted for governance; drafted labels
   count only after he confirms them from one message listing them; a site
   that misses keeps its stages on the manual path. Tom may set n minimums
   and Brier ceilings per tier instead.
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
- Governance for docs asked after the docs turn, for review before it
  (overriding valor-rebuild.md's wording, fixed in 1.4b's build).
- Merge targets as Tom's ledger rows (1.4d).
- Every turn's own `TMPDIR` and Claude Code config directory.
- A test that passed at base and is absent or skipped at head fails unless
  the diff deletes its definition.
- Container: 2 GB and 4 CPUs per VM by default, no network at run time,
  three dependency images kept per project.
- Transcripts: deltas per turn, 50 MB cap per copy.
- The token: fine-grained, one repository, Contents read and write, 90
  days, owned by Valor's account unless Tom picks his own.
- Live spend for the whole milestone's builds under $8, each live test
  declaring its spend.

## Critique round 1 (of 2)

Verdict `revise`; the split and the order were found sound. Each finding
is resolved in this revision:

1. Check profiles now get a per-run `pgpass` copy in their own `tmp/`;
   caches are a seed filled only by the base's setup, cloned per run,
   deleted after the run; test that a head suite's
   cache write does not reach the next candidate.
2. Every turn and check has its own `TMPDIR` and Claude Code config
   directory; fresh profiles deny `/private/tmp`, `/private/var/folders`,
   `~/.claude`, `~/.claude.json` except their own; tests that the critic
   cannot read the builder's `$TMPDIR` or `todos`. The config-directory
   login premise is checked first, with a fallback.
3. Calibration: case sets, labels, and floors frozen by digest before run
   1, chosen by stated rules; drafted labels count in neither bar until
   Tom confirms; the bars are a question to Tom, with the entry check
   (counting governance false positives) until then; 1.5's takeover items
   excluded from breadth; a site that misses keeps its stages manual.
4. `push_url` lands in 1.4a; `origin_url` stays the local origin until
   1.4d; 1.4a switches merge reads to the mirror and the manual docs head
   is fetched into it.
5. The mirror fetch: `hostile` first, alternates and shallow refused,
   upload-pack under `turn.sb`, fsck, no tags, size and time caps, replace
   refs and grafts off; the mirror seeded with the base; 1.4a waits for or
   carries 1.2's fix, flagged; tests with alternates, a graft, a replace
   ref.
6. The merge-target list in the kernel key directory, the default branch
   always refused; Valor's own GitHub account recommended with Tom's token
   as fallback, both rollout options; writes denied to
   `~/Library/LaunchAgents`, shell rc files, and `~/.local/bin` in every
   profile, narrowing the harnesses.md opening.
7. Absent or skipped tests that passed at base fail unless the diff
   deletes their definition, named as defining the existing check; no
   reuse of a timed-out or infrastructure-failed `suite.ran`; the spec's
   env and `bin/` versions in the environment digest.
8. Every router run first stops other tasks' services by their mark
   (`services.reaped`); tested after a SIGKILL against the 16 GB lines.
9. `_legacy` fixed too, with a test through it.
10. `verdict.json` opened component by component with no-follow and
    non-blocking; tests for a symlinked `.valor` and a FIFO.
11. Docs governance after the turn stated as overriding valor-rebuild.md,
    which the 1.4b build fixes.
12. `binaries.require` on the helpers in `/usr/local/libexec/container/`;
    the runtime's launch-agent plists and data directory denied in every
    profile.
13. Premises recorded: arguments are visible from inside `sandbox-exec`
    (a `pgrep` probe from `turn.sb` joins the no-leak test); nested
    `sandbox-exec` works.
14. Subagent transcript files are copied too, with a live test.

## Critique round 2 (of 2), carried into the build

Verdict `sound`, with findings folded in above: other tasks' services are
stopped only when their router lock is free; the startup-files deny widened
(`~/.local/share/claude`, `~/.claude`, `~/.gitconfig`, `~/.config/git`,
`/opt/homebrew`, `DISABLE_AUTOUPDATER=1`) and called narrowed, not closed;
the login check's result and the gateway-held credential; `fetch.unpackLimit=1`
and a footprint watchdog on the mirror fetch; no read-only cache (1.4b);
`/bin/ps` fails inside any sandbox, so the reaper's tests are host-only
(1.4b decides how); merge targets as Tom's ledger rows (1.4d); the
parametrize and non-Python rules (1.4b); the trusted git first on fresh
sessions' `PATH`. 1.2's replace-ref fix is carried in 1.4a's first commit
(1a1a6235d), "carried from 1.2's open finding pending Tom".

## Build record (1.4a)

Built on `m1.4-checks`. The first commit (1a1a6235d) carries 1.2's
replace-ref fix, "carried from 1.2's open finding pending Tom", with a test
for each of a replace ref and a graft; it is dropped on rebase onto 1.2's
own fix.

**The login check.** `claude -p` 2.1.287 with a fresh `CLAUDE_CONFIG_DIR`
answers "Not logged in" and sends nothing; with a dummy
`CLAUDE_CODE_OAUTH_TOKEN` it sends `Authorization: Bearer <dummy>` to the
base URL (both checked against a local probe server). So the gateway holds
the credential (`core/gateway.py`, `ClaudeLogin`): `claude-token` in the
kernel key directory when present, otherwise the Keychain login's access
token. Claude Code also keeps scratch under `/tmp/claude-<uid>` unless
`CLAUDE_CODE_TMPDIR` names another place; every turn sets it to its own
`TMPDIR`. One live fresh critique (Haiku) then ran under the fresh profile,
with `/private/tmp`, `/private/var/folders`, and `~/.claude` denied, needed
no path allowed back, wrote a valid verdict, and kept its transcript in its
own config directory.

Settled while building:

- The task's Postgres listens on TCP loopback only: a unix socket under a
  long work directory passed the 103-byte path limit.
- `GATEWAY_PORT=1` for sandboxed steps with no gateway (0 does not parse).
- The file-size limit on the mirror fetch is set by `/bin/sh -c 'ulimit -f'`,
  since a preexec function is unsafe in the threaded kernel.
- The sweep runs at the start of every router run, for every task; it looks
  for marked service processes with one process listing and stops a task's
  only when its router lock is free.
- `start --project` takes `--branch`, since the rebuild's branch is not the
  repository's default; `projects/valor.toml` names none.
- Replays provision through the kernel: `scripts/replay_workspace.py`
  writes the spec, `replay.py` starts with `--project` and reads the
  workspace back with `workspace show`, and the judge verifies inside the
  task's own state with its services started and stopped by the kernel. The
  replay profile tests moved to the kernel's profiles
  (`tests/test_demo_sandbox.py`, `tests/test_reap.py`).
- `verdict` lost `--raise-critique` and `--raise-review` with the critique
  stage; a stage with a runner is refused as having one.
- A raise outside 0 to 2 in a verdict file is no verdict; a lower raise
  changes nothing (the fold takes the higher count).

Evidence: `cd ~/src/valor-rebuild-m14 && VALOR_TEST_DB=valor_rebuild_test_m14
.venv/bin/python -m pytest -q tests` (see the done note for the counts);
`uvx ruff check .` and `uvx ruff format --check` on the code clean.
`VALOR_LIVE=1 ... tests/test_live_fresh.py` passed once, metering $0.034
(an earlier attempt that ran out of its $0.15 budget metered $0.066).
`tests/test_live_session.py` was rewritten for `start --project` and the
critique runner and not run (it now runs an Opus critique, up to $1.00).

## Patch round 1 (review round 1 of 2)

On top of the docs session's `0f24a571c`. Every finding resolved:

- **R1.** A plan or candidate whose tree holds a top-level `.valor` (any
  case) is no plan and no candidate (`session._keep`), and `blind_checkout`
  refuses one; `workspace.write_inputs` makes `.valor` itself (refusing one
  that exists), writes each input relative to a descriptor with
  `O_NOFOLLOW` and `O_EXCL`, and refuses a verdict file before the turn;
  turn-chosen text in `plan.md` is JSON-quoted. Both reproductions are
  tests (a committed `.valor/inputs` symlink to a stand-in for
  `~/.zshenv`, and a committed `.valor/verdict.json`). 1.4b's outline says
  the same holds for test, docs, and review.
- **R2.** The file-size limit runs under `/bin/bash` with 1024-byte blocks;
  a test writes 4 MB under a 1 MB limit and finds exactly 1 MB.
- **R3.** A refused or killed fetch deletes `tmp_*` packs, `incoming-*`, and
  `tmp_objdir-*`; the size-limit and delta tests assert none remain.
- **R4.** The comments in `core/workspace.py` (no socket; gateway port 1)
  and `core/fresh.py` (what blindness covers) say what is true.
- **R5.** Fresh profiles deny `/private/var/tmp`; harnesses.md's Known
  openings says blindness holds for the paths the kernel names.
- **R6.** With a credential, the gateway forwards only `v1/messages`,
  `v1/messages/count_tokens`, and `v1/models`; anything else is a 403.
  (Review round 2 found the `v1/models/` prefix match let dot segments and
  encoded slashes through; the proposed patch below closes it.)
- **R7.** The sweep also stops the services of task directories with no task
  row whose `provision:<id>` lock is free; `workspace remove ID` removes
  such a directory.
- **R8.** `start --project` holds `workspace:ports` only to choose the ports
  and record them in the task's directory (`ports.json`, which
  `taken_ports` reads), and holds `provision:<id>` through provisioning;
  the sweep only try-locks `workspace:ports` and skips when it is busy.
- **R9.** A `.git` gitfile or a `commondir` refuses the fetch.
- **R10.** The kernel's cache is keyed by the URL's digest.
- **R11.** The Keychain's expiry is parsed inside the check; the Keychain is
  read at most once a minute, failures included and whatever a 401 asked;
  `advice.graftFileDeprecated=false` is pinned.
- **T1 to T15** are tests: a cluster that will not start fails the run
  naming its log with no turn; the cluster is up during a turn and down
  after; `start --project` refusals, `--branch`, two concurrent starts on
  distinct ports, a failing start removing its workspace; a provisioning
  whose cluster will not start leaving nothing; the cache's refusals; a
  refused mirror fetch is no plan; the fetch's time limit, sha, and ref
  refusals; `ClaudeLogin` through an injected Keychain reader (no test reads
  the real Keychain); `CLAUDE_CODE_TMPDIR`; session verdicts naming their
  turn and model; a mismatched plan digest; a lower raise; a docs head that
  is not a full sha or cannot be fetched; the replay workspace's
  build, attach, and teardown through the kernel; critique with no database
  credential and no service port (`check_harness(services=False)`); and a
  merged task's workspace and Redis removed through the command line.

## 1.4a delivery 1 (did not pass)

Review round 2, the last the plan allows, on `88029a1f6` (docs at
`7b2c5c4be`): review `changes`, test `gaps`, docs `updated`. With both
review rounds spent, 1.4a goes to Tom as a delivery that did not pass.

Review findings, blocking:

- **B1.** The gateway's allowlist accepted any tail starting with
  `v1/models/`, the upstream URL was built as a string, and the client's URL
  parser resolves dot segments and decodes `%2f`, so
  `/v1/models/../../api/oauth/profile`,
  `/v1/models/%2e%2e/%2e%2e/api/oauth/profile`,
  `/v1/models/..%2f..%2fapi/oauth/profile`, and
  `/v1/models%2f..%2f..%2fapi/oauth` reached other upstream paths carrying
  Tom's bearer token.

Non-blocking: N2, `_orphan` and the sweep read the task row before taking
`provision:<id>`; N3, a first 401 should allow one Keychain re-read despite
the once-a-minute bound, since Tom's sessions rotate the token; N4,
`_docs_into_mirror` should refuse a docs head that commits `.valor`.

Test gaps: G1 the commondir test should point at a valid repository and
match the refusal; G2 `write_inputs` with a pre-existing `.valor`, a planted
verdict file, and its exclusive and no-follow creation each exercised; G3
the orphan path through the command line, including the refusal while
`provision:<id>` is held; G4 `stop_services`' `stopped` entries; G5 a lower
raise through the runner; G6 the private-HTTPS refusal test without
github.com.

## 1.4a proposed patch, awaiting Tom's feedback

Prepared while Tom is away; not authorised by the pipeline, which has spent
its review rounds. It is a candidate for his decision: his feedback on the
delivery is what would send it through the checks.

- **B1.** The gateway checks each path as it arrived, undecoded, and
  refuses (400) any tail with a percent escape, a backslash, or an empty,
  `.`, or `..` segment; with a credential it forwards only `v1/messages`,
  `v1/messages/count_tokens`, `v1/models`, and `v1/models/<id>` with an id
  of letters, digits, `.`, `_`, `-` and no `..` (403 otherwise); the upstream
  URL is built byte for byte (`yarl.URL(..., encoded=True)`), never
  re-normalised. A test sends the four reproduced paths and the earlier
  refused ones exactly as written and finds the upstream saw only the two
  allowed paths.
- **N2.** `_orphan` and the sweep look for the task row again after taking
  `provision:<id>`, and leave a directory that became a task to its own run.
- **N3.** The first 401 after a Keychain read allows one more read at once;
  later ones wait out the minute. Tested.
- **N4.** A docs head that commits `.valor` is refused. Tested.
- **G1.** The commondir test points at a valid repository and matches
  "commondir"; the gitfile test matches its own refusal.
- **G2.** `write_inputs` refuses an existing `.valor` (nothing written), a
  planted verdict file, and a linked checkout; `write_files` refuses an
  existing name (exclusive creation), a link to a real file, and a dangling
  link (nothing created through it). With both flags always set, a link is
  refused by either; the existing plain file isolates exclusive creation.
- **G3.** `workspace show` and `workspace remove` on a directory with no task
  row through the command line, and the refusal while `provision:<id>` is
  held.
- **G4.** A clean Postgres stop is reported with `stopped` entries naming the
  postmaster.
- **G5.** A lower raise from a critique turn leaves the review rounds at the
  plan's count.
- **G6.** The HTTPS refusal test fetches from a loopback port where nothing
  listens; the credential refusal is tested on git's own error text.
