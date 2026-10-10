# The turn sandbox and reaping

What a workspace turn runs under and how its processes are reaped. The mission, constraints, and tiers are in [architecture.md](architecture.md).

**Built.** A task's `harness` settings name the sandbox-exec profile every
workspace turn runs under (`workspace_turn` refuses a task without one);
[harnesses.md](harnesses.md) has its rules and the reaper's marks in full,
and this section states what they guarantee. The first demonstration and
the baseline ran every turn under one (rebuild-demonstration.md, Setup,
Isolation). The profile confines reads and writes to the workspace and
what the toolchain needs, away from Tom's other checkouts, notes,
transcripts, and keys; denies writes to the bare `origin`, so a push leaves
only through the broker's `push_branch` or `merge`; on loopback reaches
only the gateway, the workspace's own Postgres, and the app's dev ports,
never the kernel's database; and puts denies before allows, because
sandbox-exec refused allowed ports at random when a network rule followed
the allows (rebuild-demonstration.md, Kernel findings 3).

The environment is an allowlist carrying no tokens or agent sockets, with
an empty gh config and a git config without a credential helper. Web fetch
and web search are off. The public internet is reachable, so package
installs work.

**Reaping.** A turn's processes do not outlive it. When the turn ends,
every process of this user in its process group, carrying `VALOR_TURN=<id>`
in its environment, or under a sandbox denying the mach name
`valor.turn.<id>` receives `SIGTERM`, then `SIGKILL` two seconds later, and
`turn.reaped` lists them. The sandbox mark is the
one a daemon cannot shed: it survives `setsid`, re-parenting to launchd, and
a process overwriting its own environment. Serves: reliable stop; and the
16 GB machine, where a leaked test server holds memory a later turn needs.

**Workspace provisioning.** Built (`core/workspace.py`): `python -m core start
--project NAME` provisions a task's workspace from a project spec
(`projects/`) before the task starts: a clone holding history only up to the
base; a local bare origin as its only remote, where `push_branch` goes and,
when the spec names no `merge_url`, the merge; the kernel mirror, a bare repository
only the kernel writes, into which plan commits, candidates, and docs heads
are fetched and from which the merge predicate and the merge read; a Postgres
cluster of the task's own (and a Redis when the project asks) on its own
ports, under a service sandbox; and the project's setup, run once under the
turn's sandbox. Services run while a run of the task lasts, or, under the resident
kernel, between its steps until it needs Tom, is done, or is stopped; every
run first stops those a killed kernel left up whose run, services, or
provisioning is not live. The disk is kept until `python -m core workspace remove`. The mirror's
fetch treats the builder's clone as hostile (workspace.md).
Serves Mission item 1 and bounded authority.

**Design, the sandbox split.** This doc owns which work runs under which
sandbox. Turns, checks, and services run under sandbox-exec on the host.
The review's rerun runs in Apple `container` VMs started fresh from
kernel-built images with no network, because its value is an environment
the executor never touched (`core/container.py`). Valor's Mac and Tom's Mac have `container`
installed. A process under a sandbox profile reads the
runtime as absent, since every profile denies it (`container.present()`), so a
router's sweep run there leaves containers alone. The runtime is in
[tech-stack.md](tech-stack.md), its memory cost in [machine.md](machine.md).

**Images and their record.** Every image the kernel builds is named
`<REPO>/<name>:<key>` (`container.REPO`, `valor`): `valor/base:<digest of
core/images/base/>` and `valor/<project>:<dependency key>`. A project's
dependency image holds what its installing setup commands fetch with the
network open: each `uv sync`, `npm ci` or `npm install` in its setup, also
behind leading `NAME=value` assignments such as a Mac `PATH` or `SDKROOT`,
which run as written (a Mac path is absent in the VM and changes nothing).
The VM then runs the whole setup offline. Each build is
recorded in `images.json` beside the machine lock, with the digest and the
`valor.db` label of the database that built it. A prune deletes only images
carrying its own database's label, and never the base image. All runtime work
(start, build, check, prune, run, stop) happens while holding the machine lock,
which is one per machine because the runtime is.

**Tests that use the runtime.** A `container`-marked test shares the machine
lock and the runtime with every kernel on the machine and does its runtime work
only while it holds the lock, stopping the system before it releases it. It
keeps its image records in a directory of the test session's own and names its
images under `valor-test-<label>`, the label being `container.owner` of the
test database, so it never deletes, retags, or records an image a kernel built.
At session end the session takes the machine lock, deletes every image recorded
under its label, and drops the records. Every other test takes the machine lock
in a directory of the session's own, and the runtime reads as absent to it, as it
does under the check profile: its lock does not hold the machine's runtime, so
the sweep at the start of every run it makes never stops that runtime under a
kernel's verification.
