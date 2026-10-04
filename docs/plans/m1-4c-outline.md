# 1.4c outline: the container verifier and the review runner

Outline of task 1.4c from [m1-4-checks.md](m1-4-checks.md); [m1-4c-review.md](m1-4c-review.md) and [m1-4c-verifier.md](m1-4c-verifier.md) are the plans and govern where they differ.


Built in two parts: [m1-4c-review.md](m1-4c-review.md), the review runner,
with the kernel's rerun on the host until part two;
[m1-4c-verifier.md](m1-4c-verifier.md), the rerun in a container VM. Those
files govern where they differ from this outline.

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
4. The verdict, the session's final message as one JSON object: verdict, findings with kinds, governance instances by
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
  image. After a verification, the dependency images of this database that
  no open task's latest VM `verify.ran` names are deleted; the builder VM is stopped after each build to
  give its memory back.
- **Isolation.** A local image is run by tag, its digest recorded at build
  time and read back just before the run; a mismatch deletes the tag. With
  the denies above, no turn can retag, replace, or enter an image or a VM. The VM mounts the exported source read-only and one
  output directory; nothing else of the host, never the key directory. It
  is killed, not stopped, on a stop (machine.md: a graceful stop left the
  workload running), and removed after every run. No step has a time limit.
- **RAM.** `verify_memory_mb` (default 4,096) and `verify_cpus` (4). On
  the 16 GB machine the VM runs alone in the turn slot, before the Opus
  turn, with only the kernel, Postgres, and the task's services beside it
  (about 4.7 GB with the bridges); 4 GB fits the slot. The build
  measures, on this 16 GB M4, the VM's footprint idle, under this
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
command line; live (`VALOR_LIVE=1`, metered, expected about $3): one real blind Opus review
on a toy candidate with a container rerun.

