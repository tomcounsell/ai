# The machine

Valor runs on one MacBook Air M4 with 16 GB of RAM, Mac native. This doc
says what runs on it all the time, what runs only while work is in flight,
how much memory each part may take, how many turns run at once, where
secrets live, and how launchd starts things. It enforces the plan
constraint "16 GB of RAM": Postgres, one container runtime, one `claude -p`
at a time, and the bridges coexist, and no model runs resident.

Which data lives in Postgres is [data.md](data.md). How a turn is built is
[harnesses.md](harnesses.md). What a routine is allowed to do is
[routines.md](routines.md). This doc places those parts on the machine and
gives each one its memory.

## How the numbers are labelled

Every memory figure here is one of two kinds.

- **Measured** means measured on 2026-10-01 on a 64 GB M1 Max MacBook Pro
  (macOS 27.0.1, Postgres 18.6 from Homebrew, Python 3.14.7, Claude Code
  2.1.286), with nothing spent. The Air was not available. Per-process
  memory on Apple silicon does not depend on how much RAM the machine has,
  so these carry over; the totals at the end are what changes.
- **Estimate** means not measured anywhere. Each estimate says what it
  rests on, and each is a gap until the Air measures it.

Two measures appear. *Footprint* is macOS's physical footprint
(`footprint -p PID`): the memory the process costs, compressed pages
included. *RSS* is `ps -o rss`: resident pages, which counts shared
libraries and shared memory in every process that maps them. Footprint is
the one the budget uses. For Postgres both overcount, because each process
that touched a shared buffer counts it; the true cluster cost is bounded by
`shared_buffers` plus each process's private memory.

## What runs resident

Resident means started by launchd at login and kept alive. Four things, and
nothing else.

| Component | What it is | Serves | Measured | Budget |
|---|---|---|---|---|
| Postgres, the machine cluster | Postgres 18 holding the kernel's state: tasks, the ledger, approvals, budgets, steering, corrections | Constraint "a ledger the system cannot edit"; Mission item 6 (the attention log lives in the ledger) | Fresh idle cluster: 36 MB footprint summed over 9 processes (58 MB RSS). The kernel's own cluster after 32 hours of light use: 90 MB. One idle client connection: 6 MB (10 MB RSS) | 256 MB, with `shared_buffers` at its default 128 MB |
| The kernel process | The control loop, the model gateway, and the broker in one Python process | Constraint "bounded authority and spend": every model call passes the gateway, every effect the broker | Python with `core` imported, the gateway listening, one kernel connection open: 41 MB (56 MB RSS). Bare Python 3.14: 16 MB RSS | 150 MB (estimate; streaming calls and the broker's held effects add to the idle figure) |
| Telegram bridge | One Telethon client: receive, normalize, hand to `core/`; deliver what the broker releases | Mission item 1 (Tom talks to one place); constraint "one identity" (every message leaves as Valor) | Telethon imported and a client built, not connected: 80 MB (223 MB RSS) | 250 MB (estimate; a connected client caches entities and grows) |
| Email bridge | IMAP receive, SMTP deliver, same port | Mission item 1; constraint "one identity" | Python with psycopg, imaplib, smtplib, and ssl imported: 31 MB (45 MB RSS) | 100 MB (estimate) |

Resident total: about 760 MB above macOS. macOS itself, with its system
services and no user apps, is estimated at 3.5 GB on a 16 GB Air.

**What exists today.** The machine cluster runs under launchd as a
Homebrew service. The kernel has no resident process yet: the gateway lives
inside each `python -m core run` and exits with it. A resident kernel
process is the design, so the bridges have a live port to hand messages to
and the gateway outlives any one run. The bridges are not in this branch
yet.

## What runs on demand

On demand means started for one run of one task and gone when it ends.
The kernel starts and stops these; launchd never does.

| Component | When | Serves | Measured | Budget |
|---|---|---|---|---|
| One `claude -p` turn | Each turn of a task: build, clarify, feedback, and the blind verifier's turn | Mission item 1 (the frontier tier does the hard work) | A safe-mode `claude -p` with no model reply yet: 116 MB (235 MB RSS). Two long interactive Claude Code sessions on the same Mac: 150 MB and 233 MB (430 MB and 535 MB RSS) | 1,000 MB (estimate, covering subagents a turn starts and a long session's growth) |
| The turn's work | Commands the turn runs: test suites, dev servers on ports 8000 to 8009, package installs, builds | Mission item 1 ("testing actual use") | Not measured | 3,000 MB (estimate; a Django test run with its own Postgres database is the reference load) |
| The workspace cluster | A Postgres cluster of the workspace's own, separate from the machine cluster, for the app's tests | Constraint "a ledger the system cannot edit": a turn's tests never share a cluster with the kernel's ledger (rebuild-demonstration.md, Kernel findings 1) | Fresh: 36 MB, as above. The replay series' workspace cluster after every run's test suites: 352 MB summed (383 MB RSS), of which the checkpointer and background writer each count the touched shared buffers | 400 MB |
| A Redis per run | Only when the project under work needs Redis for its tests | Mission item 1 | An idle `redis-server`: 2 MB RSS | 50 MB |
| One Apple container | Work that needs a VM boundary rather than a process sandbox (see Sandboxes) | Constraint "bounded authority and spend" | Not measured for memory. Boot about 1 s, an exec 65 to 110 ms, a write 45 ms, a read 30 ms, destroy 1.5 s with a one-second grace, export of a 285 MB root filesystem 2.4 to 14.6 s, on apple/container 1.4.1 | 1,024 MB (estimate: the VM's default allocation) |
| A headless browser | When a turn opens the app it built to look at it | Mission item 1 ("testing actual use"); rebuild-demonstration.md, Recommendations; rebuild-baseline.md, Browser use | Not measured | 600 MB (estimate) |
| The dashboard (`ui/`) | When Tom opens it | Mission item 6 (the attention log is readable without asking) | Not measured | 100 MB (estimate) |
| A routine's run | When launchd fires its schedule | As the routine's objective names | Same as a turn, since a routine is a task | Counted in the turn's budget: a routine's turn takes the turn slot like any other |
| Judgement calls | Every classification, routing, and cheap check | The three-tier constraint: judgement is hosted | HTTP from the kernel process; no local model | Included in the kernel process |

A turn's processes do not outlive it. The kernel names each turn's
processes by its id and reaps them when the turn ends: SIGTERM, then
SIGKILL two seconds later, with a `turn.reaped` ledger row listing them.
Daemons a test suite leaves behind (a daemonized `redis-server`, a dev
server) are in that list. This is what keeps on-demand memory on demand,
and it serves the constraint "reliable stop, recovery, and correction".

The workspace cluster and a run's Redis start when the run is provisioned
and stop when its task ends. Idle, the workspace cluster costs 36 MB;
after a test suite has filled its buffers it costs ten times that, which
is why it is stopped rather than left up.

## The RAM budget

| Line | MB |
|---|---|
| macOS and system services (estimate) | 3,500 |
| Postgres, machine cluster | 256 |
| Kernel process | 150 |
| Telegram bridge | 250 |
| Email bridge | 100 |
| **Resident subtotal** | **4,256** |
| One `claude -p` turn | 1,000 |
| The turn's work | 3,000 |
| Workspace cluster | 400 |
| Redis per run | 50 |
| One Apple container | 1,024 |
| Headless browser | 600 |
| Dashboard | 100 |
| **Peak with everything on demand running at once** | **10,430** |
| **Left of 16,384** | **about 6,000** |

The 6 GB left is file cache, memory compression, and anything Tom runs on
the Air himself. It is not room for a second turn: a second turn and its
work add about 4 GB, which leaves almost nothing for the cache that keeps
git, the test runner, and Postgres fast. It is not room for a local model
either (see Judgement).

Whether the Air is also Tom's desktop changes this table. On the
measuring Mac, the Claude desktop app's largest renderer was 923 MB RSS and
Slack's was 452 MB. If the Air carries those, the headroom is about half.

## Concurrency

**One turn at a time.** The kernel runs one `claude -p` turn on the Air at
once, whatever task it belongs to; the blind verifier's turn, a routine's
turn, and a build turn all take the same slot. A task whose turn is ready
while the slot is held waits in Postgres, in order. This is a scheduling
rule, not a check on the agent: it enforces the 16 GB constraint, and it
serves Mission item 1, since a turn swapped to disk or killed by memory
pressure is a lost turn Tom has to notice.

The current kernel runs turns one after another within a task and has no
cross-task scheduler. The replay scripts held a lock-file slot per run; a
kernel-held turn slot in Postgres is the design.

**What runs beside the turn.** Bridges keep receiving and delivering
released messages while a turn runs; an incoming request becomes a queued
task. Judgement calls are hosted HTTP and run concurrently with a turn at
no memory cost, so a request can be classified (and sent to clarify or
build, per [judgement-layer.md](judgement-layer.md)) while another task's
turn holds the slot.

**What the baseline says about concurrency.** The baseline series
(rebuild-baseline.md) ran its 13 replay runs on a 64 GB machine, up to
three at once (three lock-file slots, `VALOR_DEMO_SLOTS`), each run with one
Valor turn at a time metered through the kernel's gateway beside its
stand-in and judge calls, and met no rate limit. That bounds the API side: at this
account's limits, the Air will not be throttled by running one turn at a
time, or several. It says nothing about the Air's memory, which is the
binding limit. Run serially, the same series is about 2.6 hours of turn
wall time (85 minutes bare and 49 clarify in the Aggregate section, 21
for the pso-a bare rerun), plus question and feedback waits, which hold no turn slot.

**Time on the slot.** A turn that waits on a question or on feedback has
ended; the slot is free until the answer arrives and the next turn starts.
Resume re-sends the whole session (rebuild-demonstration.md, Money: about
30,000 input tokens on the first call, 109,000 on the last), which costs
money and wall time, not local memory.

## Judgement: hosted, with a fallback that is never resident

Judgement goes to a hosted Jev-class API. Nothing in the judgement tier
runs a model on the Air. The open-weight equivalent behind the same port is
the fallback for when the hosted leg is down or refuses, and it is not
resident on the Air.

Why: a Jev-class open-weight model in the 7 to 8 billion parameter range,
quantized to 4 bits, needs about 5 GB for its weights and cache (estimate;
not measured). Resident, it takes most of the 6 GB headroom with every
turn. Loaded beside a turn, it pushes the machine into swap.

The fallback's placement is open (see Open questions). Two shapes fit 16
GB: the same open-weight model hosted by a second provider, reached through
the same port; or a local copy loaded only when no turn holds the slot,
unloaded after the call, and never started by launchd. The first costs no
memory and keeps the fallback available while a turn runs. The second
works offline and makes every fallback call wait for the slot. In both
cases a low-confidence call takes its abstain route, per
[judgement-layer.md](judgement-layer.md).

## Sandboxes

Two isolation mechanisms exist on macOS, and they cost memory differently.

- **sandbox-exec** wraps the turn's process tree in a profile. It costs no
  memory of its own. The current kernel runs every workspace turn under
  one: the turn reaches the gateway, the workspace cluster, and ports 8000
  to 8009 on loopback; the machine cluster's port and socket are denied;
  denies come before allows, since a network rule after the allows refused
  allowed gateway ports at random (rebuild-demonstration.md, Kernel
  findings 3).
- **Apple containers** run each sandbox as a lightweight Linux VM with its
  own memory allocation. The network mode is set on the host and cannot be
  changed from inside: a host-only network reaches exactly one host, the
  Mac, where the gateway runs. Reaching a package registry from a
  host-only network needs a proxy on the Mac beside the gateway. The
  builder VM needs Rosetta installed even for arm64 builds. A stop must
  kill rather than stop gracefully: `sleep infinity` as PID 1 ignores
  SIGTERM, and a graceful stop returned in 2.8 s median with the workload
  still running for one more step, while a kill returned in 1.7 s and cut
  the workload's open connection in 265 ms median.

Both serve the constraint "bounded authority and spend" by least privilege
[11]. On the Air, at most one Apple container runs at a time, in the turn
slot's budget. Which work runs under which mechanism is
[architecture.md](architecture.md)'s to settle; this doc only fixes that a
container costs about 1 GB and sandbox-exec costs nothing.

## launchd

launchd starts everything that starts on its own. There is no other
scheduler, supervisor, or watchdog on the machine.

- **LaunchAgents with `KeepAlive`**: the machine cluster, the kernel
  process, and each bridge. launchd restarts one that exits. Nothing else
  restarts anything, and nothing kills a process on a heuristic such as
  age or memory; stop and recovery are the kernel's (constraint "reliable
  stop, recovery, and correction").
- **LaunchAgents with `StartCalendarInterval`**: routines. Each firing
  starts a budgeted task through the kernel (`python -m core start`), so a
  routine's turn takes the turn slot and its spend is metered like any
  other. A schedule is not a standing approval; see
  [routines.md](routines.md).
- **Stop is not `launchctl`.** Stopping a task is `python -m core stop`,
  which fences it in the database, revokes its gateway token, and kills the
  turn's process group. Unloading a LaunchAgent stops a service; it is not
  how work is stopped.

**Sleep.** A MacBook Air sleeps when its lid closes. launchd runs a
calendar job missed during sleep once on wake. A turn in flight when the
Air sleeps is suspended with it, and its stream to the API may not survive
the gap. Holding a power assertion (`caffeinate -i`) for the life of a
turn is the design, so a turn finishes or is stopped by the kernel, never
by the lid (Mission item 1). Whether the Air stays on mains power with
sleep disabled is Tom's call.

## Keychain

Secrets the running system reads live in the macOS Keychain, read by name
at process start; a missing name fails the start with the name in the
error. No secret is in a dotfile, in the repository, or in a turn's
environment. This serves the constraint "bounded authority and spend": a
credential is authority, and a turn holds none.

| Secret | Read by | Today |
|---|---|---|
| The Anthropic credential for frontier turns | The gateway, which forwards the harness's own credential upstream | Claude Code's own login, which Claude Code keeps in the Keychain. The kernel holds no API key of its own |
| The judgement API key | The kernel process | Not built |
| Telegram API id, hash, and session | The Telegram bridge | Not built |
| Mail credentials | The email bridge | Not built |
| Git hosting tokens | The broker's performer for a released push, never the turn | Not needed yet: pushes go to a local bare origin |

A turn's environment is an allowlist (`HOME`, `USER`, `PATH`, and a few
more) with no tokens and no agent sockets, git's credential helper is
blocked in the sandbox, and gh runs with an empty config.

A turn runs as Tom's macOS user, so it can still read his login keychain
on purpose through the `security` tool (rebuild-demonstration.md, Setup:
Isolation). Running turns as a separate macOS user with no keychain of its
own closes that by structure, per least privilege [11]. It is not built;
see Open questions.

## Postgres on the machine

Two kinds of cluster run on the Air, never one shared.

- **The machine cluster**, resident, on the default port and a Unix
  socket. The kernel connects as `valor_kernel`, which can read and append
  and never update or delete the ledger. Roles and grants are
  [data.md](data.md).
- **A workspace cluster**, on demand, on its own port with password
  authentication, owned by the workspace, holding only the app's test
  databases. The turn's sandbox reaches it and is denied the machine
  cluster.

The separation exists because a machine cluster that trusted loopback let
a turn reach the ledger as the kernel's role (rebuild-demonstration.md,
Kernel findings 1). It costs one more Postgres, 36 MB idle.

## Open questions for Tom

1. **Is the Air dedicated?** The budget leaves about 6 GB. Desktop apps on
   the same machine take about half of it.
2. **Where does the open-weight fallback run?** Hosted at a second provider
   (no memory, available during turns) or local and loaded only when the
   turn slot is free (offline, waits for the slot).
3. **A separate macOS user for turns?** It removes the turn's path to the
   login keychain and to Tom's files without a sandbox rule for each.
4. **Mains power and sleep.** Whether the Air is kept awake, or a power
   assertion per turn is enough.

## Gaps

Each estimate above is a gap until measured on the Air:

- macOS's own resident memory on a 16 GB Air.
- A connected Telegram bridge after a week of traffic.
- A working `claude -p` turn's peak, subagents included, and the peak of
  the work it runs (a Django test suite is the first case to measure).
- An Apple container's real memory cost at its default allocation.
- A headless browser rendering one page of a workspace app.
- The open-weight fallback's memory at a quantization good enough to stand
  in for the hosted leg.
