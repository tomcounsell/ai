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
sandbox. Turns run under sandbox-exec on the host, as built and as both
experiments ran. The verifier re-executes in an Apple container started
fresh from a kernel-built image, because its value is an environment the
executor never touched. The runtime's status is in [tech-stack.md](tech-stack.md),
its memory cost in [machine.md](machine.md).
