---
tracking: none
slug: m1-4c-verifier
type: build
status: planned; revised after critique round 1, awaiting round 2
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
- Leave a VM, a builder, or the container system running past a stop, a
  timeout, or a killed kernel.

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
one Apple container at a time on the 16 GB machine. A session-level
advisory lock `container:machine` (as the broker holds one per effect) is
taken before `system start` and held through every build, the runs, the
`verify.ran` append, the prune, and `system stop`. A second task's review
waits on it.

**Reaping what a killed kernel left.** At the start of every run of any
task, the router's sweep tries `container:machine` without waiting. When
it is free, no verification is live: every container labelled
`valor.task=*` and every builder labelled `valor.build` is killed and
deleted, and the container system is stopped if it is running. When it
is held, the holder's own end does this.

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
after. The build is an async subprocess raced against `runs._stop_heard`
and `settings.setup_timeout_s`; a stop or a timeout kills and deletes the
builder.

**Base and head images.** The base runs in the dependency image of the
base's manifests; the candidate in the image of its own. When the two
digests differ, `verify.json` says `manifests_differ: true`, so the
reviewer reads the manifest changes in `diff.patch` with that in mind.

**Run by digest.** The digest is read back from the build and recorded.
If the installed release cannot run a local image by digest, the image is
tagged `valor/<project>:<digest hex>` and `container image inspect` must
give the recorded digest immediately before the run; a mismatch is a
`kernel` cause.

**Pruning.** Under `container:machine`, after the runs: every dependency
image not named by the latest `verify.ran` of an open task or by an open
task's base manifests is deleted. Nothing else holds the lock, so no
build or run can lose its image.

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
As root: copies the JUnit file and writes `/out/result.json` (exit codes,
durations, `memory.peak`, and `oom_kill` from `memory.events`). `/out`
is made root's with mode 755 before the suite starts. If the build shows
the child cgroup's files are absent on Apple's Linux kernel, `peak_mb` is
null and a memory kill is read from an exit of 137 with the kernel's OOM
line in `dmesg`.

The CLI call is an async subprocess in its own session, raced against
`runs._stop_heard` and `settings.setup_timeout_s` plus
`settings.suite_timeout_s` (1.4b's; no new limit). On a stop or a timeout:
`container kill`, then `container delete --force`, never a graceful stop
(machine.md: a graceful stop left the workload running). Every run ends
in `container delete --force`.

### Reading the result and `verify.ran`

`read_turn_file` reads `out/result.json` and `out/junit.xml` (1.4b's walk); `read_junit` parses the JUnit file; `compare` gives the three
lists against the base run in the base's image. `verify.ran` gains, with
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
both to start; neither is a cap on work. Timeouts are 1.4b's.

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
| Another task's verification holds `container:machine` | this one waits for it |
| A dependency build fails on the network | `kernel`; retried |
| The candidate's manifests will not install | `commit`; the reviewer sees it |
| The VM runs out of memory | `memory`; the reviewer and the attention log see it |
| A stop during a build or a run | builder or VM killed and deleted, system stopped, `stopped` |
| A kernel killed mid-verification | the next run of any task, finding the lock free, kills and deletes every labelled VM and builder and stops the system |
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
- With `container:machine` held by a live verification, another task's
  sweep touches nothing.
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
| `core/router.py` | the sweep reaps containers and builders when `container:machine` is free |
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
3. **One verification at a time, by machine.md's one-container rule.**
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
7. Pruning raced running verifications: `container:machine` is held from
   system start to system stop, and pruning runs under it (Runtime;
   Images).
8. Orphan cleanup was too narrow: any task's run start reaps every
   labelled VM and builder when the lock is free; builds are raced
   against a stop and timed (Runtime; Images).
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
