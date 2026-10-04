---
tracking: none
slug: m1-4-checks
type: build
status: 1.4a merged; popoto #191 trial done, held at the merge (not released), $8.77 metered; 1.4b in build (m1-4b-runners.md, both critique rounds done); then 1.4d; 1.4c in two parts: part one, the review runner (m1-4c-review.md), built, merged, and registered, while docs stays unregistered until governance passes its entry check, with `verdict` kept for docs until then; part two, the container verifier (m1-4c-verifier.md), built
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
- Apple's `container` 1.5.0 is installed from its signed package, and
  Rosetta is installed (`arch -x86_64 /usr/bin/true` succeeds).
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
| **1.4b** | Frozen case sets for breadth and governance, routing on the entry check; the test runner (breadth, then suite at base and head in fresh checkouts) and the docs runner (own checkout, path drop, governance after the turn) | nothing (live judgement spend metered, expected about $1, through the builder's own key directory) | `test`, `docs` |
| **1.4d** | The GitHub credential for the merge, held in the kernel key directory; the merge-target list; `merge_url` honoured; transcript copies with digests; performers registered per task; awaitable performers | granting the merge targets for the one live push (the token is in the vault); everything else is built and tested against a local smart-HTTP server | none |
| **1.4c** | In two parts. Part one ([m1-4c-review.md](m1-4c-review.md)): the review runner (Opus, blind, governance first, the kernel's rerun on the host), registered with docs once governance passes its entry check, when the `verdict` command is deleted. Part two ([m1-4c-verifier.md](m1-4c-verifier.md)): the container verifier (kernel-built images, a fresh VM per verification, RAM measured) | installing `container` and Rosetta, starting the container system | `review`; the command is deleted |

**Order: a, b, d, c.** 1.4a first because every runner needs provisioned
checkouts and the fresh-session machinery. 1.4b next because its runners
route on breadth's and governance's entry checks, and the review runner
(1.4c) routes on governance's too. 1.4d
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
| The blind verifier: Opus in a fresh session, rerunning the tests in an Apple container built by the kernel; `review.decided` carries the governance boolean; container RAM measured | c (part one reruns on the host; part two moves the rerun into the container) | `core/container.py` (new), `core/checks.py`, `core/binaries.py`, `docs/machine.md` |
| `tools/push_branch.py` gains a GitHub credential held by the kernel and never by a turn, so a released merge reaches the rebuild branch on GitHub | d (`push_url` split in a) | `tools/push_branch.py`, `core/git.py`, `core/credentials.py`, `core/__main__.py` (`github-key`), the merge-target list |
| Transcript copies kept in the store with a digest | d | `core/transcripts.py` (new), `core/runs.py` |
| Review and docs runners always pass `governance_from`, the test runner always passes `breadth`; `record_check` accepts neither as optional from a runner | b (test, docs), c (review) | `core/verdicts.py` |
| Breadth and governance route on the entry check over frozen cases and log every row; floors from human labels once real tasks have produced thirty or more; each re-checks Jev's estimate overhead against its own rows | b | `core/judgement_tasks.py`, `tools/jev.py`, cases under `~/src/valor-demo/items/judgement/` |
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
   `O_NONBLOCK`. The file is first moved to `.valor/handled/<turn_id>/`,
   then opened there and `fstat` must show a regular file with one link and
   no holes; it is read through that descriptor to the size `fstat` showed
   and no further, and parsed as a JSON object. A symlinked `.valor`, a
   symlinked file, a FIFO, a socket, or a device is refused without
   blocking and without reading anything it points to. A file that
   vanishes between the move and the read is refused with that reason. The kernel validates it and writes
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

## 1.4a in full

Provisioning, fresh sessions, and the critique runner: see
[m1-4a-provisioning.md](m1-4a-provisioning.md).

## 1.4b in full: calibration, the test runner, the docs runner

The whole plan is [m1-4b-runners.md](m1-4b-runners.md): the Done items it
closes, its threat model, the frozen case sets breadth and governance
route on, the test runner (breadth, then the suite at base and at head,
each in a fresh checkout with setup from the lock and fresh services on
the task's ports), the docs runner (its own clone, the head fetched from
it into the mirror, the doc-path prefix kept, governance after the turn),
the answers to the popoto #191 trial's findings, tests, rollout, and what
was decided by default. It has no questions for Tom.

## 1.4d outline

The GitHub credential, transcripts, and the performer registry: see
[m1-4d-outline.md](m1-4d-outline.md).

## 1.4c outline

The container verifier and the review runner: see
[m1-4c-outline.md](m1-4c-outline.md). Built in two parts:
[m1-4c-review.md](m1-4c-review.md), the review runner, with the kernel's
rerun on the host until part two; [m1-4c-verifier.md](m1-4c-verifier.md),
the rerun in a container VM. Those files govern where they differ from the
outline.

## Machine changes needing Tom (rollout steps, none done in a build)

1. **Install Rosetta**: `softwareupdate --install-rosetta --agree-to-license`.
   Before 1.4c's build.
2. **Install `container`** from the signed package on Apple's GitHub
   releases (`apple/container`, the current release at install time),
   which puts `container` in `/usr/local/bin`; then
   `container system start`, which registers its launch agents and asks to
   download its default Linux kernel. Before 1.4c's build. The builder
   then pulls the pinned Debian base image once.
3. **The GitHub credential** (1.4d; Questions, 3): the token is in the
   vault `.env` as `GITHUB_PUSH_TOKEN`; the build session runs
   `python -m core github-key` from the kernel checkout at 1.4d's rollout.
   Tom adds a ruleset on `main` with `valorengels` off the bypass list when
   he can; nothing waits for it.
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
3. **Whose GitHub token.** Answered by Tom on 2026-10-02: Valor's own
   account (`valorengels`, a collaborator with write access to
   `tomcounsell/ai`), holding a classic token with the `repo` scope that
   expires 2026-12-31, in the vault `.env` as `GITHUB_PUSH_TOKEN` and in
   1Password as "GitHub Push Token". A fine-grained token cannot reach this
   repository: GitHub scopes those to the token owner's repositories or an
   organisation's, and `tomcounsell/ai` belongs to a user account. The
   classic token reaches every repository Valor's account can write to, so
   the kernel's merge-target list and its refusal of the default branch are
   the restriction, plus a ruleset on `main` with `valorengels` off the
   bypass list once Tom adds it (Valor's account has push, not admin).
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
- The verdict file is `.valor/verdict.json`, a regular file with one link and no holes.
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
- Container: 4 GB and 4 CPUs per VM by default, no network at run time.
- Transcripts: deltas per turn, raw bytes as base64, no size cap.
- The token: Valor's classic `repo` token (Tom's answer to Questions, 3),
  restricted by the merge-target list and the default-branch refusal.
- Live spend for the milestone's builds is metered, expected about $8 in
  all, each live test declaring its spend; nothing refuses or pauses on
  money.

## Records

The critique rounds, the 1.4a build and patch rounds, its deliveries, and
the popoto #191 trial run: see [m1-4-checks-record.md](m1-4-checks-record.md).
