---
tracking: none
slug: m1-4c-verifier
type: build
status: planned; revised after critique round 2 (both rounds spent)
critique_rounds: 2
review_rounds: 2
---

# 1.4c, part two: the container verifier

Task 1.4c of [m1-4-checks.md](m1-4-checks.md), milestone 1.4 of
[valor-rebuild.md](valor-rebuild.md), in two parts. Part one,
[m1-4c-review.md](m1-4c-review.md), lands the review runner with the
kernel's rerun on the host. This part moves that rerun into a fresh Apple
container VM started from a kernel-built image, so the environment the
candidate is judged in is one no turn ever touched. The review runner's
inputs, verdict, and registration are part one's and are not repeated.

The build starts when `container` is installed (Rollout prerequisite) and
merges after 1.5 (the build skill's waves), so the container stays after
takeover as valor-rebuild.md's Leaves out says.

## Stakes

`critique_rounds: 2`, `review_rounds: 2`. The task changes the kernel
(every sandbox profile, the program allowlist in `core/binaries.py`, the
review runner's rerun, the router's sweep) and stored data (`verify.ran`
fields). A mistake either lets a turn reach the container runtime (and
through it every image and VM on the machine), runs candidate code
outside a VM or a profile, or lets the candidate choose the environment
its own base is judged in.

## The Done items it closes

| Done item | What closes it here |
|---|---|
| valor-rebuild.md 1.4, Leaves out: the container verifier, after takeover | `core/container.py`; the review rerun runs in a fresh VM per verification from a kernel-built image run by digest; merged after 1.5 |
| architecture.md, Verification: "Runs deterministic checks first in a fresh container from a kernel-built image" | the rerun runs before the blind session reads anything, as in part one |
| machine.md: the container's memory cost, "1,024 MB (estimate)" | measured on this 16 GB M4 and written in |

## Threat model

What the turns control:

- **The candidate's tree**: code, `conftest.py`, lockfiles and manifests
  (so what the dependency build installs and runs with the network
  open), test ids, every file it writes in the VM, its exit codes, its
  JUnit file.
- **The reviewer's and builder's turns**: as in part one.

What the kernel must never do with any of it:

- Run candidate code anywhere but inside a VM (the rerun, the dependency
  build) or under the check profile (the reviewer's commands). The host
  never extracts the candidate's tree: `git archive` writes a tar from
  the mirror into a per-run directory holding nothing else, the VM
  mounts that directory read-only, and `/valor/run.sh` unpacks it in the
  VM.
- Let the candidate choose its base's environment. The base runs in the
  image built from the base's own manifests; a candidate's manifests
  build only the candidate's image.
- Let one build reach another. Every image build uses a fresh builder
  with no cache, deleted after the build.
- Let a turn, a check, or a service reach the container runtime: every
  profile denies the CLI, its helpers, its mach services, its data
  directory, and its launch-agent plists. Two openings are not closed by
  those denies (`launchctl submit` or `bootstrap` of a plist elsewhere,
  and `open` of a `.command` file); the build tests both from a turn and
  names whichever runs in harnesses.md's Known openings.
- Mount anything of the host into the VM but the run's source directory
  (read-only) and its output directory. Never the key directory, the
  workspace, the mirror, or a home path.
- Give the run a network, or run an image by an unchecked tag.
- Take the setup, suite, or lint command from the candidate. They come
  from the project spec stored in the task document at provisioning.
- Trust `/out/result.json` or the JUnit file beyond the candidate's own
  claim. Both are read through `read_turn_file`. When the build shows the
  suite's user can write `/out`, `verify.json` says the result is the
  candidate's claim (`result_owner: "candidate"`).
- Leave a VM, a builder, or the container system running past a stop, an
  interrupt, or a killed kernel.

## Design

### The runtime (`core/container.py`, new)

Apple's `container`, installed from Apple's signed package to
`/usr/local/bin` (root-owned). `binaries.CONTAINER` is checked with
`binaries.require` before every call, and so is each helper under
`/usr/local/libexec/container/` (the API server and the runtime and
network plugins). The kernel calls the CLI outside any sandbox, as it
calls git and `sandbox-exec`.

**Started and stopped around each verification.** tech-stack.md says the
runtime runs only while a sandbox is running, and that holds: the kernel
runs `container system start` at the start of a verification and
`container system stop` at its end, so nothing of the runtime is resident
between verifications. The start's latency is measured with the RAM
(below) and recorded on `verify.ran`.

**One verification at a time on the machine.** machine.md allows at most
one Apple container at a time on the 16 GB machine. The runtime is
machine-wide and a Postgres advisory lock is not (it holds per database,
and the kernel and every build's test database are separate), so the lock
is an `fcntl.flock` on a fixed file,
`~/Library/Application Support/valor/container.lock`, a constant in
`core/container.py` that no setting overrides, so every kernel and every
test database on the machine share it. No profile allows it, so no turn
can take it. It is taken before `system start` and held through every
build, the runs, the `verify.ran` append, the prune, and `system stop`;
the kernel's death releases it. A second verification waits on it in
one-second non-blocking tries raced against `runs._stop_heard`, so a
stopped task stops waiting and returns `stopped`.

**Owners.** Every container and builder carries `valor.db=<first 12 hex
of the sha256 of the owning database's name>` beside `valor.task=<task>`
(containers) or `valor.build` (builders); dependency images carry
`valor.db` too.

**Reaping what a killed kernel left.** At the start of every run of any
task, the router's sweep tries the lock without waiting. When it is free,
no verification is live on the machine: every container and builder
labelled with the sweep's own `valor.db` is killed and deleted, and the
container system is stopped only when no container or builder labelled
`valor.db` of any owner remains. Another owner's leftovers are its own
sweep's. When the lock is held, the sweep touches nothing.

### Profile denies (`core/workspace.py`)

`workspace.profile()` adds, for every profile (turn, check, fresh
session, service), after the allows and beside the kernel-path deny
(the last matching rule wins):

- deny `process-exec` of `/usr/local/bin/container` and the subpath
  `/usr/local/libexec/container`;
- deny `mach-lookup` of the runtime's service names;
- deny `file-read*` and `file-write*` of its data directory;
- deny `file-write*` of its launch-agent plists (already under 1.4a's
  `~/Library/LaunchAgents` write deny; named so a test can say which rule
  holds).

The data path and mach names are read from the installed release during
the build (`launchctl print gui/$UID`, the plists, `container system
status`) and written as constants in `core/container.py` with the release
version they were read from.

### Images

**Base image**, built once per pinned base digest from kernel-owned files
in `core/images/base/`, never from a candidate:

- `debian:bookworm-slim` for arm64, by digest;
- git with `/usr/libexec/git-core` present, so this repository's
  `binaries.require_git` accepts `/usr/bin/git` through `VALOR_GIT`;
- uv with the Pythons the projects pin; Node LTS from a pinned tarball
  with its checksum; PostgreSQL 18 and redis-server by pinned version;
- an unprivileged user `valor`;
- the entrypoint `/valor/run.sh`.

**Dependency images**, one per project and manifest digest (1.4b's
environment digest, read in the mirror), `FROM` the base by digest. The
build context holds only the manifests (`pyproject.toml`, `uv.lock`,
`package.json`, `package-lock.json`, as the spec's kind has them) and a
kernel-written `spec.json`. The build runs the spec's own setup commands
with the network open, uv commands given `--no-install-project` (so a
spec's `uv sync --frozen --extra dev` installs its extras). Each build
runs in a fresh builder with `--no-cache`, labelled `valor.build`, deleted
after. The build has no timeout: it is an async subprocess in its own
process group, raced against `runs._stop_heard`, and a stop or an
interrupt kills the group, then kills and deletes the builder.

**Base and head images.** The base runs in the dependency image of the
base's manifests; the candidate in the image of its own. When the two
digests differ, `verify.json` says `manifests_differ: true`, so the
reviewer reads the manifest changes in `diff.patch` with that in mind.

**Run by digest.** The digest is read back from the build and recorded.
If the installed release cannot run a local image by digest, the image is
tagged `valor/<project>:<digest hex>` and `container image inspect` must
give the recorded digest immediately before the run; a mismatch is a
`kernel` cause.

**Pruning.** Under the machine lock, after the runs: every dependency
image labelled with this database's `valor.db` and not named by the
latest `verify.ran` of an open task or by an open task's base manifests
is deleted; another database's images are its own to prune. Nothing else
holds the lock, so no build or run can lose its image.

### The run

`container run` with:

- the image by digest (or the checked tag);
- `--memory {verify_memory_mb}M --cpus {verify_cpus}`;
- no network: the release's no-network option, read during the build. If
  the release has none, `run.sh` as root deletes every interface but `lo`
  before its first command as `valor`;
- two mounts: `checks/review-<key>/src/` (holding only `source.tar` and
  the kernel-written `spec.json`) read-only at `/src`, and
  `checks/review-<key>/out/` at `/out`; nothing else;
- the name `valor-verify-<task>-<key>` and the label `valor.task=<task>`.

`run.sh`, as root: unpacks `/src/source.tar` into `/work` owned by
`valor`; creates a child cgroup `/sys/fs/cgroup/valor` with the memory
controller enabled; starts the services `spec.json` names on loopback,
creating the spec's roles with a fresh password in
`/home/valor/.pgpass`; renders the spec's `env`, `{port}` as the service
ports inside the VM and `{passfile}` as that file. Then, as `valor`, in
the child cgroup: the setup offline (`UV_OFFLINE=1`,
`npm_config_offline=true`), the suite with `{junit}` as
`/home/valor/junit.xml`, and the lint, each exit code taken by `run.sh`.
**No command's output is read through a pipe.** Each setup command, the
suite, and the lint runs in its own session (`setsid`) with stdout and
stderr sent to a file under `/home/valor/logs/`; `run.sh` waits on that
process's exit (`wait $pid`), never for end of file, then kills the rest
of its process group. So a setup command that leaves a child holding
stdout (`pg_ctl start` without `-l`, `x &`) cannot hang the run, and the
child goes with its group. The host does the same with the CLI: `container
build` and `container run` write to files under the run's check
directory, the kernel waits on the process, not on its output, and a stop
or an interrupt kills the group.
As root: copies the JUnit file and writes `/out/result.json` (exit codes,
durations, `memory.peak`, and `oom_kill` from `memory.events`). `/out`
is made root's with mode 755 before the suite starts. If the build shows
the child cgroup's files are absent on Apple's Linux kernel, `peak_mb` is
null and a memory kill is read from an exit of 137 with the kernel's OOM
line in `dmesg`.

The CLI call is an async subprocess in its own session, raced against
`runs._stop_heard`. Setup in the VM has no timeout; `run.sh` runs the
suite and the lint each under `timeout` with `settings.suite_timeout_s`
(passed in `spec.json`, as part one times them; no new limit), and a
timed-out suite or lint is `cause: commit`. On a stop or an interrupt:
`container kill`, then `container delete --force`, never a graceful stop
(machine.md: a graceful stop left the workload running). Every run ends
in `container delete --force`.

### Reading the result and `verify.ran`

`read_turn_file` reads `out/result.json` and `out/junit.xml` (1.4b's
walk); `read_junit` parses the JUnit file; `compare` gives the three
lists against the base run in the base's image. The lint record is part
one's (its parser, its `null`, no message).

**Reuse.** A VM `verify.ran` is reused when its key matches and its
`cause` is not `kernel`:

- head: candidate, image digest, `memory_mb`, and `where: "vm"`;
- base: base sha, base image digest, `memory_mb`, and `where: "vm"`, run
  once per task and image and shared by every candidate of the task.

`memory_mb` in the key means raising the default reruns a memory kill.
Part one's host key carries `where: "host"`, so a task in flight when
this part merges runs its next review in a VM and reuses no host
result. `verify.ran` gains, with
`where: "vm"`: image digest, base image digest, `manifests_differ`,
`memory_mb`, `cpus`, `peak_mb`, the runtime's release, system start
seconds, `result_owner`, the count of ids skipped under the `macos`
marker, and `cause`:

- `kernel`: the runtime will not start, an image build failed on the
  network, a digest mismatch, a stop, a killed kernel. No verdict; the
  branch reruns.
- `commit`: the candidate's manifests will not install, setup fails, the
  suite times out, no JUnit file. Goes to the reviewer.
- `memory`: `oom_kill` above zero, or `peak_mb` reached `memory_mb`. Goes
  to the reviewer as "the VM ran out of memory at N MB", not as a
  failure of the candidate, and is shown in the attention log so the
  default can be raised. It is reused like `commit`, so it never loops.

`verify.json` gives the reviewer these fields, still with no free text.

### Specs inside the VM

The spec is the task document's `project` (`setup`, `suite`, `lint`,
`services`, `roles`, `env`, `kind`), stored at provisioning
(`workspace.provision`), never read from the candidate. The kernel writes
it as `spec.json` into the run's source directory and the build context.

### This repository in Linux

Its macOS-bound tests (sandbox-exec, `sandbox_check`, `/bin/ps -E`, the
Command Line Tools' git, `security`) carry a `macos` marker, registered
in `pyproject.toml`; `tests/conftest.py` skips the marker off Darwin. The
VM runs the rest. The reviewer gets the VM's counts and the number of
`macos`-skipped ids, never the test branch's results; the test branch's
host run covers all of them.

### Settings (`core/settings.py`)

`verify_memory_mb` (`VALOR_VERIFY_MEMORY_MB`, default 2,048, then set from
the measurement) and `verify_cpus` (`VALOR_VERIFY_CPUS`, 4). The VM needs
both to start; neither is a cap on work. The only timeout is 1.4b's
suite timeout, on the suite and the lint.

### RAM, measured

On this machine (a 16 GB M4, the design target's class): the VM's
footprint idle, under this repository's suite, and under a Django suite
with Postgres, at 1, 2, and 4 GB limits; the runtime's daemons during a
run; and `system start` latency. machine.md's container rows get the
measurements and their date; `verify_memory_mb`'s default is set from
them.

### Docs fixed in the same build

- `docs/machine.md`: the container rows measured.
- `docs/architecture.md`, Verification and the sandbox split: the rerun
  in a fresh VM.
- `docs/harnesses.md`: the runtime denies in every profile; Known
  openings if `launchctl` or `open` reaches outside the sandbox.
- `docs/tech-stack.md`: the release, the install path, started and
  stopped per verification.
- `docs/data.md`: the `verify.ran` fields.
- `docs/plans/rebuild-handoff.md`: installing `container` (and Rosetta
  where missing) is a setup step on any machine the kernel runs on once
  this part merges, including the move to the build Mac.
- `docs/plans/m1-4-checks.md`: the 1.4c outline and split row point to
  the two part files; "on this 64 GB machine" and "Rosetta is not
  installed" corrected.
- `core/README.md`, `tests/README.md`.

## Failure modes

| Failure | What happens |
|---|---|
| `container` missing or the system will not start | `kernel` cause naming it; `failed`, no verdict, retried |
| Another verification holds the machine lock | this one waits for it, and stops waiting on a stop |
| A dependency build fails on the network | `kernel`; retried |
| The candidate's manifests will not install | `commit`; the reviewer sees it |
| The VM runs out of memory | `memory`; the reviewer and the attention log see it |
| A stop during a build or a run | builder or VM killed and deleted, system stopped, `stopped` |
| A kernel killed mid-verification | the next run of any task under the same database, finding the lock free, kills and deletes that database's labelled VMs and builders, and stops the system when no owner's remain |
| A forged JUnit file, or a `/out` the suite's user can write | recorded as the candidate's claim, `result_owner` says which |

## Tests

Tests that need the runtime carry a `container` marker and skip when
`/usr/local/bin/container` is absent, with the reason.

**Runtime and reaping.**

- A stop during the run leaves no VM: `container list --all` has no
  `valor-verify-<task>-*`, and the system is stopped.
- A stop during a build leaves no builder.
- A kernel killed with SIGKILL mid-run: the next run of a different task
  kills and deletes the labelled VM and stops the system.
- Two test databases: with a verification live under one, the other's
  sweep touches nothing; with the first's kernel killed (the lock free),
  the second's sweep leaves the first's labelled VM and does not stop the
  system; the first's next sweep deletes it and stops the system.
- A task stopped while waiting on the lock returns `stopped` without
  taking it.
- Raising `verify_memory_mb` reruns a `cause: memory` run; the base run
  is reused across two candidates of a task.
- After a verification the system is stopped (`container system status`).

**Images.**

- The base runs in the base manifests' image: a candidate whose manifest
  pulls a package whose install script writes a file the base's tests
  read does not change the base run, and `manifests_differ` is true.
- A candidate build leaves no builder and no cache another build reuses: a
  second build after a candidate build whose install script writes to the
  builder's cache does not see that file.
- An image retagged by hand is not used: the run is by digest, or the
  `image inspect` check gives a `kernel` cause.
- An unchanged manifest reuses the image; a changed one builds a second;
  pruning keeps both while an open task names each.
- A popoto-shaped spec (`uv sync --frozen --extra dev`) installs offline
  in the VM.

**Isolation.**

- No network during the run: probes to a public address, to a host port
  bound on `127.0.0.1`, to the host's LAN address, and to the vmnet
  gateway all fail, as `valor` and as root after `run.sh`'s network step.
- The builder, which has a network, cannot reach the kernel's Postgres or
  a task's services on the vmnet gateway or the LAN address.
- A candidate whose suite reads a host file (`~/.ssh/known_hosts`, a path
  under the work directory) passes on the host and fails in the VM.
- A candidate whose `conftest.py` writes `/out/result.json` fails, and
  `result_owner` is `kernel`; or, where the build shows the share
  ignores ownership, `result_owner` is `candidate`.
- The `/src` mount holds only `source.tar` and `spec.json`.
- Under each profile (turn, check, fresh, service): running
  `/usr/local/bin/container list`, executing a helper under
  `/usr/local/libexec/container/`, looking up each runtime mach name
  (`sandbox_check`), reading the data directory, and writing a scratch
  plist named like the runtime's in `~/Library/LaunchAgents` are each
  refused.
- From a turn, `launchctl submit` of a job that writes a marker file, and
  `open` of a `.command` file that writes one: the test asserts whichever
  outcome the build documents in harnesses.md.
- A helper in a scratch copy of the libexec layout made group-writable is
  refused by `binaries.require`.

**Results.**

- A spec whose setup runs `sh -c 'sleep 600 &'` (a child holding stdout)
  finishes setup as soon as the command exits, the suite runs, and no
  `sleep` is left in the VM; the same command in a dependency build
  finishes the build.

- A suite that allocates past `verify_memory_mb` gives `cause: memory`,
  not a failure finding, and the reviewer's `verify.json` says so.
- This repository's suite in the VM: every `macos` test skips, the rest
  run, and the skipped count is on `verify.ran`.
- `peak_mb` is positive and at most `memory_mb`, or null with the cgroup
  files absent.

**Live** (`VALOR_LIVE=1`, metered): one real blind Opus review of a toy
candidate with the rerun in a VM, through the router.

## Files it changes

Other tasks change `core/` too; these are the files this part touches.

| File | Change |
|---|---|
| `core/container.py` | new: system start and stop, build, run, kill, reap, read |
| `core/images/base/Containerfile`, `core/images/base/run.sh`, `core/images/deps/Containerfile` | new: kernel-owned image files |
| `core/fresh.py` | the review rerun calls `container.verify` |
| `core/binaries.py` | `CONTAINER`, `CONTAINER_HELPERS` |
| `core/workspace.py` | the runtime denies in `profile()` |
| `core/router.py` | the sweep reaps its own database's containers and builders when the machine lock is free |
| `core/settings.py` | `verify_memory_mb`, `verify_cpus` |
| `core/README.md` | the module |
| `pyproject.toml`, `tests/conftest.py` | the `macos` and `container` markers |
| macOS-bound test files | the `macos` marker |
| `tests/test_container.py` | new |
| `tests/test_review.py`, `tests/test_workspace.py`, `tests/test_live_fresh.py` | the VM rerun; profile denies; the live review |
| `tests/README.md` | the markers |
| `docs/machine.md`, `docs/architecture.md`, `docs/harnesses.md`, `docs/tech-stack.md`, `docs/data.md`, `docs/plans/rebuild-handoff.md`, `docs/plans/m1-4-checks.md` | as in Docs fixed |

No migration.

## Tech debt absorbed

- machine.md's container memory estimate, replaced with measurements on
  the target class.
- The stale Rosetta and "64 GB" lines in m1-4-checks.md.

## Left out

- Routing turns into containers: turns stay under sandbox-exec
  (architecture.md, the sandbox split).
- macOS-bound tests in the VM: they cannot run on Linux; the host test
  branch covers them.
- Candidates arriving as a `git bundle`: the first task after takeover.
- A headless browser in the VM: milestone 3.

## Expected spend, as information

The live review: about $3. The VM costs no money. All metered; nothing
refuses or pauses on money.

## Rollout prerequisite

**Install `container`.** From the signed package on Apple's GitHub
releases (`apple/container`, the current release), which puts `container`
in `/usr/local/bin` and its helpers in `/usr/local/libexec/container/`;
then `container system start` once, accepting its default Linux kernel
download. Rosetta, which the image builder needs, is installed on this
machine (`arch -x86_64 /usr/bin/true` succeeds); where missing,
`softwareupdate --install-rosetta --agree-to-license` comes first.

## Rollout

1. The build pulls the pinned Debian base once and builds the base image.
2. The build reads the runtime's data path, mach names, no-network option,
   run-by-digest behaviour, and `/out` ownership from the installed
   release and records them in the module and the build record.
3. The RAM measurements; machine.md and `verify_memory_mb` set from them.
4. At merge, in the kernel checkout: `uv sync`, then restart the kernel.
   No migration. Any other machine the kernel moves to installs
   `container` first (rebuild-handoff.md).
5. The first real task after the merge runs its review rerun in a VM; its
   `verify.ran` is read by hand once.

## Decided by default

Reversible calls made by the build session, not questions for Tom.

1. **Install from Apple's package, not Homebrew.** The kernel runs only
   root-owned programs outside a sandbox, and Homebrew's prefix is the
   user's.
2. **The container system is started and stopped around each
   verification**, rather than left resident with tech-stack.md amended.
   It keeps the runtime's memory off the 16 GB machine between reviews;
   the start latency is measured and on `verify.ran`.
3. **One verification at a time, by machine.md's one-container rule**,
   held by a file lock because the runtime is machine-wide.
4. **The host never unpacks the candidate.**
5. **Every image build gets a fresh builder with no cache.** Slower, and
   no candidate's install scripts reach another image.
6. **The suite runs as an unprivileged user in a child cgroup; `/out` is
   root's**, so exit codes and memory are `run.sh`'s record.
7. **A memory kill is its own cause**, so a default set too small never
   becomes a finding against every candidate.
8. **Images are pruned by reference, not by count.**
9. **The rerun in a VM needs no separate governance grant.** It moves
   part one's rerun, which fills the granted review stage, to a VM; its
   result decides nothing alone. The profile denies take authority away
   and hold no work back.

## Questions for Tom

None. No identity or credential choice is involved.

## Critique round 1 (of 2): revise

The report covered both parts. The lead split the task: findings 1, 2, 3,
5, 6, 11, 12, and 13 are handled in m1-4c-review.md; these are handled
here. The governance question moved to Decided by default (item 9 here,
item 7 in part one).

4. The candidate controlled its base's environment and the shared
   builder: the base runs in the base manifests' image, every build uses
   a fresh builder with no cache, `manifests_differ` goes to the
   reviewer, and the builder's reach to host services is tested (Images;
   Tests).
7. Pruning raced running verifications: the machine lock is held from
   system start to system stop, and pruning runs under it (Runtime;
   Images).
8. Orphan cleanup was too narrow: any task's run start reaps every
   labelled VM and builder when the lock is free; builds are raced
   against a stop (Runtime; Images).
9. The spec's source was wrong and its rendering unspecified: the spec is
   the task document's, written into the VM as `spec.json`; `run.sh`
   renders `env`, `{port}`, `{passfile}`, and the roles; dependency images
   run the spec's own setup (Specs inside the VM; Images; The run).
10. Deny placement and an overclaim: the denies sit after the allows; the
    `launchctl` and `open` openings are tested and documented, not
    claimed closed (Profile denies; Threat model; Tests).
14. A memory kill was blamed on the candidate: `cause: memory`; the suite
    runs in a child cgroup, with a fallback where its files are absent
    (The run; Reading the result).
15. Runtime behaviour unverified: fallbacks for the read-only mount (a
    directory holding only the tar and spec), run by digest (a checked
    tag), and `/out` ownership (`result_owner`) (The run; Images).
16. Rollout gaps: the install is in rebuild-handoff.md for any machine;
    the system is started and stopped per verification, keeping
    tech-stack.md's statement true (Runtime; Docs fixed; Rollout).
17. The `../../escape` test could not arise from `git archive` and is
    dropped; m1-4-checks.md's Rosetta and "64 GB" lines are in Docs fixed.

## Critique round 2 (of 2): revise

Both rounds are spent; every finding is folded in. Findings 1 and 3 to 11
are part one's (m1-4c-review.md); 2 and 12 are handled here.

2. A per-database advisory lock guarded a machine-wide runtime: the lock
   is an `fcntl.flock` on a fixed file, every container and builder is
   labelled with its owning database, a sweep reaps only its own label
   and stops the system only when no owner's container remains, tested
   with two test databases (Runtime; Tests).
12. Smaller gaps: the wait on the lock races a stop; the base run in the
    VM is reused by base sha, base image digest, `memory_mb`, and
    `where`; part one's key carries `where`, so no host result is reused
    for a VM run; `memory_mb` in every VM key, so a raised default reruns
    a memory kill; the lint runs under its own `suite_timeout_s` inside the
    VM (image builds and setup have no timeout, `setup_timeout_s` being
    removed by 1.4u);
    `read_turn_file` is cited for its walk only (Runtime; The run;
    Reading the result).

After round 2, from 1.4u's critique: with no timeout, a command whose
output is read through a pipe waits for end of file, so a setup command
that leaves a child holding stdout hangs. Every command in the VM and
every CLI call on the host writes its output to a file, is waited on by
its exit, and has its group killed after (The run; Tests, Results).

## Tom's feedback (2026-10-03)

Installing Apple's `container` from its signed package needs the Mac's admin password, which only Tom holds; the build asks him for it when 1.4c part two's rerun reaches this step, and otherwise the plan stands.
