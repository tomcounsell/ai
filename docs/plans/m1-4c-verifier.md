---
tracking: none
slug: m1-4c-verifier
type: build
status: planned
critique_rounds: 2
review_rounds: 2
---

# 1.4c in full: the container verifier and the review runner

Task 1.4c of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md). It lands the runner for
`checks.review`: governance per hunk, then the kernel's own rerun of the
candidate's setup, suite, and lint in a fresh Apple container VM from a
kernel-built image, then a blind Opus session. With every stage covered,
`python -m core verdict` is deleted.

It builds on the interface 1.4b leaves (m1-4b-runners.md): judgement sites
reading the mirror, `read_turn_file`, `check_harness` with fresh services
on the task's ports, `run_setup`, the environment digest, `suite.ran`,
stops raced through `runs._stop_heard` and `os.killpg`, and
`MANUAL_STAGES == {"review": Check.REVIEW}`. The shared design (the task
directory, the kernel mirror, fresh sessions, blind checkouts, project
specs) is m1-4-checks.md's and is not repeated here.

The build starts when `container` is installed (Rollout prerequisite) and
merges after 1.5 (the build skill's waves). Until it merges, review is
recorded with `python -m core verdict review`, and the 1.5 gate plays it
that way.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel (the
router's runner mapping, `record_check`'s callers, every sandbox profile,
the program allowlist in `core/binaries.py`), stored data (a `verify.ran`
event, new fields on `review.decided`), and the command line (one command
deleted). A mistake either passes a candidate on its own claim, lets a
turn reach the container runtime (and through it every image and VM on
the machine), or runs candidate code outside a VM or a profile.

## The Done items it closes

From valor-rebuild.md, 1.4:

| Done item | What closes it here |
|---|---|
| The blind verifier: Opus in a fresh session, rerunning the tests; `review.decided` carries the governance boolean | `fresh.review_runner`: seat `reviewer`, a blind checkout, the container rerun before the session reads anything, `record_check(REVIEW, ..., governance_from=...)` |
| Fresh sessions for critique, review, and docs registered in `RUNNERS`; each stage removed from `verdict` as its runner lands, and the command deleted | review leaves `MANUAL_STAGES`; `verdict`, `MANUAL_STAGES`, `manual_allowed`, `_manual`, and the `--behavior` path are deleted in the same commit that registers the runner |
| The review and docs runners always pass `governance_from` | the review runner asks governance first and passes its ids; `record_check` already refuses a session-leg review without them (1.4b) |
| The container verifier (listed under Leaves out, after takeover) | `core/container.py`, kernel-built images, a fresh VM per verification, footprint measured and written into machine.md; merging after 1.5 keeps it after takeover |

The Done line's "in a fresh sandboxed checkout like the test branch's" is
stale against the waves (the review rerun is this task's, in the
container). The build amends that line and Leaves out to say the review
rerun runs in a fresh VM, and that the test branch's host run in a fresh
sandboxed checkout covers the macOS-bound tests the VM skips.

## Threat model

What the turns control:

- **The candidate's tree**: its code, `conftest.py`, lockfiles, setup
  inputs, test ids, every file it writes while running in the VM
  (including anything under the output mount it can reach), its exit
  codes, and its JUnit file.
- **The reviewer's turn**: its blind checkout, its `tmp/`, its own
  database in the task's cluster, and `.valor/verdict.json`.
- **The builder's session**: its clone, caches, `TMPDIR`, Claude Code
  config, and the task's live services, as in 1.4b.

What the kernel must never do with any of it:

- Run candidate code anywhere but inside the VM (the rerun) or under the
  check profile (the reviewer's own commands). The host never extracts
  the candidate's tree: `git archive` writes a tar from the mirror, the VM
  mounts it read-only, and `/valor/run.sh` unpacks it inside the VM.
- Let a turn, a check, or a service reach the container runtime. Every
  profile denies executing the CLI and its helpers, looking up its mach
  services, reading or writing its data directory, and writing its
  launch-agent plists. A turn that could reach the runtime could retag an
  image, enter a VM, or mount the host.
- Mount anything of the host into the VM but the read-only source tar and
  one output directory the kernel made for that run. Never the key
  directory, the workspace, the mirror, or a home path.
- Give the VM a network during the run, or run an image by tag.
- Take the suite, setup, or lint command from the candidate. They come
  from the project spec at the task's base, read in the mirror.
- Trust `/out/result.json` or the JUnit file beyond the candidate's own
  claim made inside a VM it could not leave. Both are read through
  `read_turn_file` (no link followed, regular file only, size-bounded).
- Read builder narration into the reviewer: no `done.md`, no builder
  `.valor/` file, no transcript, no commit message, no `turn.collected`
  text, no test-branch result, no docs work. The reviewer's profile makes
  these unreachable, not merely unmentioned.
- Leave a VM running past a stop, a timeout, or a killed kernel.

The dependency image build runs the project's installers with the network
open (`uv sync`, `npm ci` run package build scripts). That runs inside the
builder VM, which holds only the lockfiles and the base image: no source,
no secret, no host mount.

## Design

### The review runner (`core/fresh.py`)

`review_runner(fresh_for, model=None) -> Runner` for `Check.REVIEW`,
following `critique_runner`'s shape:

1. Fold; `b.mirror` is required. **Governance first**:
   `ids = await judgement_sites.governance(port, ctx.dsn, ctx.task_id,
   b.base_sha, candidate)`, so a judge outage costs no VM and no Opus
   turn.
2. **The container rerun**, by the kernel, before any session reads
   anything: `container.verify(lay, b, candidate, ctx)` (below). A
   `verify.ran` with the same task, candidate, image digest, and command
   and `cause` other than `kernel` is reused, so a review rerun after
   Tom's governance grant runs no second VM. A `kernel` cause records no
   verdict and the runner returns `failed`.
3. **The blind session**: `workspace.fresh_dir(lay.checks /
   f"review-{candidate[:12]}")`, `blind_checkout` from the mirror,
   `check_harness(lay, check_dir, ports, env, services=True)` so the
   reviewer may run commands with its own Postgres and Redis on the task's
   ports. `write_inputs` puts in `.valor/inputs/`:
   - `request.md`, `answers.md` (Tom's answers and feedback, quoted);
   - `diff.patch` (base to candidate, from the mirror);
   - `verify.json` (the `verify.ran` fields below, no prose);
   - `effects.md` (the task's held, released, and refused effects).
   The plan file and the docs are read in the tree at the candidate.
   `runs.run_turn(..., model=SEATS["review"], state=..., fresh=...)`.
4. `read_verdict` gives: `verdict` (`pass`, `changes`,
   `governance_refused`); `findings`, each with a kind; `governance`, the
   reviewer's instances by path and line with summary, incident, and
   mission item; `notes` by instance id; `predicted_failure` (0 to 1);
   `requirements`, one result per requirement. A malformed file is
   `Malformed`, as for critique.
5. `record_check(conn, task_id, Check.REVIEW, verdict,
   governance_from=ids, governance=specs, notes=..., findings=...,
   predicted_failure=..., requirements=..., verify=verify_event_id,
   leg="session", turn_id=..., model=..., usd_micros=...)`. The writer
   computes the governance boolean and instances as it does for any
   review: kernel instances from the judgement rows, a reviewer line
   inside a kernel hunk merged into that instance by `_union`, a reviewer
   instance never removing one.

`ctx.alive()` is checked before the VM, before the turn, and before the
write. A stop during the VM kills it (below); a stop during the turn is
`run_turn`'s. Either returns `stopped` with nothing recorded.

### The container module (`core/container.py`, new)

**Runtime.** Apple's `container`, installed from Apple's signed package
to `/usr/local/bin` (root-owned). `binaries.CONTAINER` is checked with
`binaries.require` before every call, and so is each helper the CLI runs
under `/usr/local/libexec/container/` (the API server and the runtime and
network plugins). The kernel calls the CLI outside any sandbox, as it
calls git and `sandbox-exec`, because the runtime is reached over its
launch agents' mach services.

**The trust boundary is the daemon and its data, not the CLI.** The
runtime runs as the user from launch agents and keeps images and VM state
in its data directory. `workspace.profile()` adds, for every profile
(turn, check, fresh session, service), before any allow:

- deny `process-exec` of `/usr/local/bin/container` and the subpath
  `/usr/local/libexec/container`;
- deny `mach-lookup` of the runtime's service names;
- deny `file-read*` and `file-write*` of its data directory;
- deny `file-write*` of its launch-agent plists (already under 1.4a's
  `~/Library/LaunchAgents` write deny; named here so the test can say
  which rule holds).

The exact data path and mach names are read from the installed release
during the build (`launchctl print gui/$UID`, the plists, `container
system status`) and written as constants in `core/container.py`, with the
release version they were read from.

**Base image**, built once per pinned base digest, from a kernel-owned
build file in `core/images/base/`:

- `debian:bookworm-slim` for arm64, by digest;
- git with `/usr/libexec/git-core` present, so this repository's
  `binaries.require_git` accepts `/usr/bin/git` through `VALOR_GIT`;
- uv with the Pythons the projects pin; Node LTS from a pinned tarball
  with its checksum; PostgreSQL 18 and redis-server by pinned version;
- an unprivileged user `valor`;
- the entrypoint `/valor/run.sh` (kernel-owned, in the image).

`run.sh`, as root: unpacks `/src/source.tar` into `/work` owned by
`valor`; starts the services the spec names on loopback inside the VM;
then, as `valor`, runs the spec's setup offline, the suite with
`{junit}` replaced by `/home/valor/junit.xml`, and the lint, each with its
exit code taken by `run.sh` itself; as root, copies the JUnit file and
writes `/out/result.json` (exit codes, durations, peak memory from
`/sys/fs/cgroup/memory.peak`). `/out` is writable only by root inside the
VM, so candidate code writes there only by escaping its user.

**The project's environment**: a dependency image per project and
lockfile digest (the same environment digest 1.4b computes, from the
mirror at the candidate), `FROM` the base by digest, copying only the
lockfiles and installing at build time with the network open (`uv sync
--frozen --no-install-project`, `npm ci`). A patch that changes no
lockfile reuses the image. After a build, dependency images not named by
any open task's latest `verify.ran` or by the project's base are pruned,
and the builder VM is stopped to give its memory back.

**The run.** `container run` with:

- the image by digest, never by tag; the digest is read back from the
  build and recorded, and the run refuses a tag;
- `--memory {verify_memory_mb}M --cpus {verify_cpus}`;
- no network: the release's no-network option, read during the build. If
  the installed release has none, `run.sh` as root deletes every
  interface but `lo` before the first command as `valor`; the network
  tests prove whichever holds;
- two mounts: the source tar read-only at `/src`, and
  `lay.checks/review-<key>/out` at `/out`; nothing else;
- the name `valor-verify-<task>-<key>` and the label `valor.task=<task>`.

The CLI call is an async subprocess in its own session, raced against
`runs._stop_heard` and `settings.suite_timeout_s` plus
`settings.setup_timeout_s` (1.4b's, no new limit). On a stop or a
timeout the kernel runs `container kill`, then `container delete
--force`, never a graceful stop (machine.md: a graceful stop left the
workload running). Every run ends in `container delete --force`. At the
start of every run of a task, containers labelled with that task and not
owned by a live run are killed and deleted, as 1.4b reaps by mark.

**Reading the result.** `read_turn_file` reads `out/result.json` and
`out/junit.xml` (1.4b's walk, the same bounds). The JUnit file is parsed
by 1.4b's `read_junit`. Failures compare with the base the same way as
`checks.test` (`compare`), using the base's `verify.ran` for the same
image, run once per task and reused.

**`verify.ran`**, one event per run: task, candidate, base, image digest,
base image digest, command (setup, suite, lint), exit codes, failing and
erroring test ids, `failing_at_base`, `deleted_at_head`, counts by
outcome, lint exit and its findings by rule, path, and line, duration,
`memory_mb`, `cpus`, `peak_mb`, the runtime's release, and `cause`
(`kernel` or `commit`, classed as 1.4b classes them: a runtime not
running, an image that would not build because the network failed, a
stop, or a killed kernel is `kernel`; a lockfile that will not install, a
setup or suite timeout, or a missing JUnit file is `commit`). A `commit`
cause goes to the reviewer as part of `verify.json` and is a finding the
reviewer weighs; it never sends the branch round again.

### This repository in Linux

Its macOS-bound tests (sandbox-exec, `sandbox_check`, `/bin/ps -E`, the
Command Line Tools' git, `security`) carry a `macos` marker, registered
in `pyproject.toml`; `tests/conftest.py` skips the marker off Darwin. The
VM runs everything else; the test branch's host run in a fresh sandboxed
checkout runs all of them. `verify.ran` counts what ran in the VM, and
the reviewer sees both counts.

### Settings (`core/settings.py`)

`verify_memory_mb` (`VALOR_VERIFY_MEMORY_MB`, default 2,048, replaced by
the measurement below if it shows a different need) and `verify_cpus`
(`VALOR_VERIFY_CPUS`, 4). The VM needs a memory size and a CPU count to
start; neither is a cap on work. Timeouts are 1.4b's.

### RAM, measured

The build measures on this machine (a 16 GB M4, the design target's
class): the VM's footprint idle, under this repository's suite, and under
a Django suite with Postgres, at 1, 2, and 4 GB limits, plus the runtime's
resident daemons idle and during a run. machine.md's "1,024 MB
(estimate)" rows are replaced with the measurements and their date, and
`verify_memory_mb`'s default is set from them. On the 16 GB machine the
VM runs alone in the turn slot, before the Opus turn.

### Deleting `verdict` (`core/__main__.py`, `core/verdicts.py`)

In the commit that registers `review_runner` in `runners()`:

- `verdicts.MANUAL_STAGES`, `manual_allowed`, `_manual`, and the
  `--behavior` path are deleted; `record_check` takes `leg` in
  `{"session", "kernel"}`;
- the `verdict` subcommand, its parser, its usage text, `_verdict`, and
  `_instance` are deleted;
- `_status_line`'s "no runner" text names the stage and says the build
  has no runner for it, since every stage has one;
- `leg: manual` rows in the ledger still fold as before and stay in the
  attention log.

### Skills

`skills/sdlc/review.md` gains the `verdict.json` shape (step 4) and the
inputs list. `skills/sdlc/verdict.md` drops the manual channel.

### Docs fixed in the same build

- `docs/machine.md`: the container rows measured.
- `docs/architecture.md`, Verification: the verifier as built (the rerun
  in a fresh VM, then the blind session); the sandbox-split paragraph
  names `core/container.py`.
- `docs/sdlc-state-machine.md`, checks.review and "What exists": the
  review runner; no manual command.
- `docs/harnesses.md`: the container runtime's place in the profiles.
- `docs/data.md`: `verify.ran` and the new `review.decided` fields.
- `docs/tech-stack.md`: the runtime's release and install path.
- `docs/plans/valor-rebuild.md`: the 1.4 Done line and Leaves out, as in
  Done items.
- `docs/plans/m1-4-checks.md`: the split table's 1.4c row and the
  "on this 64 GB machine" line in the outline point here.
- `core/README.md`, `tests/README.md`: the module, the marker, the tests.

## Failure modes

| Failure | What happens |
|---|---|
| The container runtime is not running or not installed | `binaries.require` or `container system status` fails; the branch returns `failed` naming it, no verdict; the next run retries |
| The judge for governance is down | step 1 fails before any VM; `failed`, no verdict |
| The image build fails on the network | `cause: kernel`, `failed`, retried |
| The candidate's lockfile does not install | `cause: commit`; the reviewer sees it in `verify.json` |
| The suite hangs | killed at the timeout, `cause: commit` |
| A stop during the VM | `container kill`, `delete --force`, `stopped`, nothing recorded |
| A kernel killed mid-run | the next run of the task kills and deletes the task's labelled containers first |
| A forged JUnit file or exit 0 from `conftest.py` | recorded as the candidate's claim; the reviewer reads the diff, `conftest.py` included |
| `verdict.json` missing or malformed | `Malformed`, no verdict, the branch reruns |

## Tests

Unit and router tests run with `VALOR_TEST_DB`. Tests that need the
runtime carry a `container` marker and skip when
`/usr/local/bin/container` is absent or the runtime is not running, with
the skip reason naming which.

**The review runner (scripted session, no runtime).**

- Governance comes from the judgement rows: a scripted reviewer with no
  governance instances still yields the kernel's instances on
  `review.decided`.
- A reviewer line inside a kernel hunk merges into that instance; a
  reviewer instance outside every hunk is added; none is removed.
- Governance runs before the VM: with the judge stubbed down, no
  `container` call is made and no turn starts.
- A review rerun after a grant reuses `verify.ran` (one VM call across
  both runs); a `kernel` cause is not reused.
- The reviewer's checkout holds no `.valor/` from the builder, and its
  profile refuses reading the builder clone's `.valor/done.md`, the
  builder's `TMPDIR`, `~/.claude`, and the test branch's check directory.
- `verify.json` carries no free text field: a test failure message from
  the candidate does not appear in it.
- A malformed `verdict.json` records nothing and the branch reruns.
- A stop during the scripted turn records nothing and returns `stopped`.

**Deletion.**

- `python -m core verdict` exits with the parser's unknown-command error.
- `record_check(..., leg="manual")` raises.
- A ledger holding `leg: manual` review rows folds to the same state.

**The container (runtime required).**

- A container killed on a stop leaves no VM: `container list --all` shows
  no `valor-verify-<task>-*` after the runner returns `stopped`.
- A kernel killed with SIGKILL mid-run: the next run's start deletes the
  task's labelled container.
- An image retagged by hand to the expected tag is not used: the run is by
  the recorded digest, and a tag passed to `run` raises.
- No network: a probe to a public address and a probe to a host port bound
  on `127.0.0.1` and on the host's LAN address both fail from inside the
  VM, as the `valor` user and as root after `run.sh`'s network step.
- A candidate whose suite reads a host file (`~/.ssh/known_hosts`, a path
  under the work directory) passes on the host and fails in the VM.
- A candidate whose `conftest.py` writes `/out/result.json` fails: `/out`
  is root's. A link at `out/junit.xml` (planted by a test that runs `run.sh`
  with a scratch script in its place) is read by the walk as "no per-test
  result".
- The source tar is unpacked only in the VM: a tar entry naming
  `../../escape` leaves nothing outside the run's directories on the host.
- A turn under each profile (turn, check, fresh, service) cannot run
  `/usr/local/bin/container list`, cannot exec a helper under
  `/usr/local/libexec/container/`, cannot look up the runtime's mach
  services (a `sandbox_check` per name), cannot read the data directory,
  and cannot write a scratch plist named like the runtime's in
  `~/Library/LaunchAgents`.
- A helper under a scratch copy of `/usr/local/libexec/container/` made
  group-writable is refused by `binaries.require`.
- A lockfile change builds a second dependency image; an unchanged
  lockfile reuses the first; pruning keeps both while a task names each.
- This repository's suite in the VM: every `macos`-marked test skips and
  the rest run; the counts are on `verify.ran`.
- `peak_mb` is a positive number below `memory_mb`.

**Live** (`VALOR_LIVE=1`, metered): one real blind Opus review of a toy
candidate with a container rerun, recorded through the router; and one of
a toy candidate whose diff adds a validator, which must come back with a
governance instance on its hunk.

## Files it changes

Other tasks change `core/` too; these are the files this one touches.

| File | Change |
|---|---|
| `core/container.py` | new: build, run, kill, reap, read |
| `core/images/base/Containerfile`, `core/images/base/run.sh`, `core/images/deps/Containerfile` | new: the kernel-owned image files |
| `core/fresh.py` | `review_runner`; `SEATS["review"]` used |
| `core/verdicts.py` | delete `MANUAL_STAGES`, `manual_allowed`, `_manual`, the manual leg; docstring |
| `core/__main__.py` | register `review_runner`; delete `verdict`, `_verdict`, `_instance`, usage text; `_status_line` |
| `core/binaries.py` | `CONTAINER`, `CONTAINER_HELPERS` |
| `core/workspace.py` | the container denies in `profile()`; `write_inputs` gains `effects.md` and `verify.json` |
| `core/settings.py` | `verify_memory_mb`, `verify_cpus` |
| `core/checks.py` | `compare` and `read_junit` used from the container module (no change expected beyond an import) |
| `core/README.md` | the module |
| `skills/sdlc/review.md`, `skills/sdlc/verdict.md` | the verdict shape; no manual channel |
| `pyproject.toml`, `tests/conftest.py` | the `macos` and `container` markers |
| `tests/test_container.py`, `tests/test_review.py` | new |
| `tests/test_pipeline.py`, `tests/test_fresh.py`, `tests/test_workspace.py`, `tests/test_attention.py`, `tests/scripted.py` | review through the router; profile denies; callers of `verdict` and the manual leg moved to the runner or deleted |
| `tests/test_live_fresh.py` | the two live reviews |
| macOS-bound test files | the `macos` marker |
| `tests/README.md` | the markers |
| `docs/machine.md`, `docs/architecture.md`, `docs/sdlc-state-machine.md`, `docs/harnesses.md`, `docs/data.md`, `docs/tech-stack.md`, `docs/plans/valor-rebuild.md`, `docs/plans/m1-4-checks.md` | as in Docs fixed |

No migration: `verify.ran` is appended with `ledger.append` to the existing
ledger table.

## Tech debt absorbed

- The manual `verdict` command and every path that serves it.
- machine.md's container memory estimate, replaced with measurements on
  the target class.
- The stale 1.4 Done line and the stale split-table row (Done items).
- `test.decided`'s standing caveat that the suite's report is the
  candidate's own claim made on the host: review has a rerun the builder
  never touched.

## Left out

- Routing turns into containers: turns stay under sandbox-exec
  (architecture.md, the sandbox split).
- macOS-bound tests in the VM: they cannot run on Linux; the host test
  branch covers them.
- An Opus-class reviewer from another vendor: the seat is the builder's
  Opus model; the other vendor's route is milestone 3's.
- Candidates arriving as a `git bundle`: the first task after takeover.
- The calibration and audit sample of review verdicts: they need real
  verdicts first (architecture.md, Calibration and autonomy).
- A headless browser in the VM: milestone 3.

## Expected spend, as information

The live reviews: about $3 each at the reviewer seat, about $6 in all. A
real task's review: about $2 to $4 per round. The VM costs no money. All
metered; nothing refuses or pauses on money.

## Rollout prerequisite

**Install `container`.** From the signed package on Apple's GitHub
releases (`apple/container`, the current release), which puts `container`
in `/usr/local/bin` and its helpers in `/usr/local/libexec/container/`;
then `container system start`, accepting its default Linux kernel
download. Rosetta, which the image builder needs, is already installed on
this machine (`arch -x86_64 /usr/bin/true` succeeds); if it is missing,
`softwareupdate --install-rosetta --agree-to-license` comes first.

## Rollout

1. The build pulls the pinned Debian base once and builds the base image.
2. The build reads the runtime's data path, mach names, and no-network
   option from the installed release and records them in the module and
   the build record.
3. The RAM measurements; machine.md and `verify_memory_mb` set from them.
4. At merge, in the kernel checkout: `uv sync`, then restart the kernel
   so `RUNNERS` holds `review_runner`. No migration.
5. The first real task after the merge runs review through the runner;
   its `verify.ran` and `review.decided` are read by hand once.

## Decided by default

Reversible calls made by the build session, not questions for Tom.

1. **Install from Apple's package, not Homebrew.** The kernel runs only
   root-owned programs outside a sandbox, and Homebrew's prefix is the
   user's.
2. **The host never unpacks the candidate.** The tar is mounted read-only
   and unpacked inside the VM, so no path in it can land on the host.
3. **Base and head both run in the VM, each once per task and image.**
   The comparison is 1.4b's, so the reviewer's failures mean the same
   thing as the test branch's.
4. **The suite runs as an unprivileged user; `/out` is root's.** Exit
   codes and peak memory are then the kernel's script's record, not the
   candidate's.
5. **`verify.json` carries ids, counts, codes, and lint locations, no
   free text.** Failure messages are candidate-written and could carry
   narration aimed at the reviewer; the reviewer can rerun any test in
   its checkout to see the message.
6. **Images are pruned by reference, not by count.** Dependency images no
   open task's latest run and no project base names are deleted; no fixed
   number is kept.
7. **Timeouts are 1.4b's setup and suite timeouts.** No new limit.
8. **The reviewer gets its own services on the task's ports**, so it can
   run the suite itself in its checkout, as review.md asks.
9. **A `commit` cause in the VM goes to the reviewer, not round again.**
   As in 1.4b, a candidate's own fault never loops.
10. **`verdict` is deleted in the commit that registers the runner**, so
    no build of the kernel has neither.
11. **The `container` test marker skips with a named reason when the
    runtime is absent**, so the suite runs on a machine without it and
    says so.

## Questions for Tom

1. **Intent: the VM rerun inside review is the rerun the 1.4 Done line
   and architecture.md's Verification already name, so it needs no
   separate governance grant.** It fills the existing review stage, adds
   no stage, round, or approval step, and removes one manual step.
   *Assumed: yes; the build ledgers no new guard.*
