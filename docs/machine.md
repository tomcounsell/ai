# The machine

One Valor install runs on one Mac, and the design is for one machine. Valor
has four Macs; they exist for project isolation only. Each runs its own
install, with its own Postgres, ledger, and bridges, for the projects it
owns. Nothing is shared between them: no cross-machine ledger, no hub, no
replication. Production runs on Valor's Macs, never on Tom's (Tom,
2026-10-01). The design target for one install is a MacBook Air M4 with
16 GB of RAM, Mac native. This doc says what runs on it all the time, what runs only while work is in flight,
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
the one the RAM plan uses. For Postgres both overcount, because each process
that touched a shared buffer counts it; the true cluster cost is bounded by
`shared_buffers` plus each process's private memory.

## What runs resident

Resident means started by launchd at login and kept alive. Four things, and
nothing else.

| Component | What it is | Serves | Measured | Planned |
|---|---|---|---|---|
| Postgres, the machine cluster | Postgres 18 holding the kernel's state: tasks, the ledger, approvals, metered spending, steering, corrections | Constraint "a ledger the system cannot edit"; Mission item 6 (the attention log lives in the ledger) | Fresh idle cluster: 36 MB footprint summed over 9 processes (58 MB RSS). The kernel's own cluster after 32 hours of light use: 90 MB. One idle client connection: 6 MB (10 MB RSS) | 256 MB, with `shared_buffers` at its default 128 MB |
| The kernel process | The control loop, the model gateway, and the broker in one Python process | Constraint "bounded authority, metered spending": every model call passes the gateway, every effect the broker | Python with `core` imported, the gateway listening, one kernel connection open: 41 MB (56 MB RSS). Bare Python 3.14: 16 MB RSS | 150 MB (estimate; streaming calls and the broker's held effects add to the idle figure) |
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

| Component | When | Serves | Measured | Planned |
|---|---|---|---|---|
| One `claude -p` turn | Each turn of a task: build, clarify, feedback, and the blind verifier's turn | Mission item 1 (the frontier tier does the hard work) | A safe-mode `claude -p` with no model reply yet: 116 MB (235 MB RSS). Two long interactive Claude Code sessions on the same Mac: 150 MB and 233 MB (430 MB and 535 MB RSS) | 1,000 MB (estimate, covering subagents a turn starts and a long session's growth) |
| The turn's work | Commands the turn runs: test suites, dev servers on ports 8000 to 8009, package installs, builds | Mission item 1 ("testing actual use") | Not measured | 3,000 MB (estimate; a Django test run with its own Postgres database is the reference load) |
| The workspace cluster | A Postgres cluster of the workspace's own, separate from the machine cluster, for the app's tests | Constraint "a ledger the system cannot edit": a turn's tests never share a cluster with the kernel's ledger (rebuild-demonstration.md, Kernel findings 1) | Fresh: 36 MB, as above. The replay series' workspace cluster after every run's test suites: 352 MB summed (383 MB RSS), of which the checkpointer and background writer each count the touched shared buffers | 400 MB |
| A Redis per run | Only when the project under work needs Redis for its tests | Mission item 1 | An idle `redis-server`: 2 MB RSS | 50 MB |
| One Apple container | Work that needs a VM boundary rather than a process sandbox (see Sandboxes) | Constraint "bounded authority, metered spending" | Not measured for memory. Boot about 1 s, an exec 65 to 110 ms, a write 45 ms, a read 30 ms, destroy 1.5 s with a one-second grace, export of a 285 MB root filesystem 2.4 to 14.6 s, on apple/container 1.4.1 | 1,024 MB (estimate: the VM's default allocation) |
| A headless browser | When a turn opens the app it built to look at it | Mission item 1 ("testing actual use"); rebuild-demonstration.md, Recommendations; rebuild-baseline.md, Browser use | Not measured | 600 MB (estimate) |
| The dashboard (`ui/`) | When Tom opens it | Mission item 6 (the attention log is readable without asking) | Not measured | 100 MB (estimate) |
| A routine's run | When launchd fires its schedule | As the routine's objective names | Same as a turn, since a routine is a task | Counted in the turn's line: a routine's turn takes the turn slot like any other |
| Judgement calls | Every classification, routing, and cheap check | The three-tier constraint: judgement is hosted | HTTP from the kernel process; no local model | Included in the kernel process |

A turn's processes do not outlive it. The kernel names each turn's
processes by its id and reaps them when the turn ends: SIGTERM, then
SIGKILL two seconds later, with a `turn.reaped` ledger row listing them.
Daemons a test suite leaves behind (a daemonized `redis-server`, a dev
server) are in that list. This is what keeps on-demand memory on demand,
and it serves the constraint "reliable stop, recovery, and correction".

Each task the kernel provisions has a workspace cluster of its own (and a
Redis when its project asks). They start when a run of the task first
needs them and stop when that run returns, and every run first stops the
services a killed kernel left up, for other tasks and for provisionings
that died, unless their own run or provisioning is live, so on the 16 GB
machine only the running task's services are up. Idle, a workspace cluster
costs 36 MB; after a test suite has filled its buffers it costs ten times
that, which is why it is stopped rather than left up.

## The RAM plan

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

The 6 GB left is file cache and memory compression. The machine is
Valor's, so no desktop apps of Tom's share it. It is not room for a second
turn: a second turn and its
work add about 4 GB, which leaves almost nothing for the cache that keeps
git, the test runner, and Postgres fast. It is not room for a local model
either (see Judgement).

## Concurrency

**One turn at a time.** The kernel runs one `claude -p` turn per machine at
once, whatever task it belongs to; the blind verifier's turn, a routine's
turn, and a build turn all take the same slot. A task whose turn is ready
while the slot is held waits in Postgres, in order. This is a scheduling
rule, not a check on the agent: it enforces the 16 GB constraint, and it
serves Mission item 1, since a turn swapped to disk or killed by memory
pressure is a lost turn Tom has to notice.

**The checks after a candidate.** Test, review, and docs run in parallel
in the design ([sdlc-state-machine.md](sdlc-state-machine.md)), but on
the Air each takes the one slot: the review and docs turns are turns, and
the test branch's suite run is turn-sized work (the 3,000 MB line above).
So the Air runs them back to back, test, review, then docs, with the same
verdicts and the same join as running them at once.

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

Judgement goes to a hosted Jev-class API (TypeSafe's Jev). Nothing in the
judgement tier runs a model on the Air. An open-weight model behind the
same port is the fallback for when the hosted leg fails or abstains, and it
is not resident on the Air.

Why: a Jev-class open-weight model in the 7 to 8 billion parameter range,
quantized to 4 bits, needs about 5 GB for its weights and cache (estimate;
not measured). Resident, it takes most of the 6 GB headroom with every
turn. Loaded beside a turn, it pushes the machine into swap.

The fallback is hosted too: Qwen3-235B-A22B Instruct 2507 on Parasail,
reached through OpenRouter. Tom asked for Jev's own model on a second
provider (2026-10-01), but Jev's base model is not published. It costs the
Air no memory and stays available while a turn holds the slot. A call
neither leg answers with confidence takes its abstain route, per
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

Both serve the constraint "bounded authority, metered spending" by least privilege
[11]. On the Air, at most one Apple container runs at a time, in the turn
slot. Which work runs under which mechanism is
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
  starts a task through the kernel (`python -m core start`), so a
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

Kernel-held secrets live in the kernel key directory, the directory of
the `pg_passfile` setting (`~/.config/valor-kernel/`, mode 700), which every
sandbox profile denies. They are not in the Keychain, because a turn
can read the login keychain (below). A secret is read by name when the
process that needs it starts; a missing name fails the start with the name
in the error. No secret is in the repository or in a turn's environment.
This serves the constraint "bounded authority, metered spending": a credential is
authority, and a turn holds none.

| Secret | Read by | Today |
|---|---|---|
| The Anthropic credential for frontier turns | The gateway, which sets it on every call; a turn carries only a placeholder, since it runs with its own Claude Code config directory | `claude-token` in the kernel key directory (a long-lived token from `claude setup-token`) when present; otherwise Claude Code's own login, read from the Keychain through `/usr/bin/security`, which lasts about eight hours and is refreshed only by Claude Code sessions on the default config directory |
| The judgement legs' keys, `TYPESAFE_API_KEY` and `OPENROUTER_API_KEY` | The kernel process, when `run` or `calibrate` builds the judgement port, and only for a leg pointed at its default endpoint | `judgement-keys` in the kernel key directory (mode 600, `NAME=value` lines), written only by `python -m core judgement-keys`, which copies them from the vault `.env` and prints each name with `written`, `kept`, or `missing`, never a value. Held in the two adapter objects, never in `os.environ`, a ledger row, an exception, or a log line |
| Telegram API id, hash, and session | The Telegram bridge | Not built |
| Mail credentials | The email bridge | Not built |
| The GitHub push token, `GITHUB_PUSH_TOKEN` | The merge performer of a task the kernel provisioned, for a released merge to a granted remote, never the turn and never `push_branch` | `github-keys` in the kernel key directory (mode 600), written only by `python -m core github-key`, which copies it from the vault `.env` and prints `written`, `kept`, or `missing`. For each git call against the remote the kernel writes a config file of its own, mode 600, in the same directory, holding one header (`Authorization: Basic`, user `x-access-token`) scoped to that exact URL, with `http.followRedirects=false` so the header never follows a redirect, gives git its path as `GIT_CONFIG_GLOBAL`, and removes it when git exits; a file a crash left is removed by the next merge once it is older than twice `git_timeout_s`. GitHub's refusal of the token fails the merge saying to rotate it |

**Where a merge lands.** A merge to a remote lands only on a (URL,
branch) pair on the merge-target list, which Tom grants with `python -m
core merge-target add URL BRANCH --note TEXT` and anyone removes with
`merge-target remove`; `merge-target list` shows it. The URL is plain
`https://host/path`, with no user, port, query, or quoting. `start
--project` refuses a spec whose `merge_url` and branch are not granted,
and refuses the branch the remote's HEAD names (its default branch), read
at start and again before each push. A merge to a local origin needs no
grant and no token; a `--workspace` task merges with no token.

A turn's environment is an allowlist (`HOME`, `USER`, `PATH`, and a few
more) with no tokens and no agent sockets, git's credential helper is
blocked in the sandbox, and gh runs with an empty config.

A turn runs as the machine's one macOS user, so it can read that user's
login keychain on purpose through the `security` tool, the machine's Claude
login included (rebuild-demonstration.md, Setup: Isolation). Tom decided on
2026-10-01 not to add a separate macOS user for turns: a turn that used the
login to call the provider around the gateway is an accepted risk, because
the gateway is for visibility and honest metering, not a hard wall.

**The kernel key directory.** The passwords for `valor_kernel` and the
owner role on the kernel databases live in a libpq password file,
`~/.config/valor-kernel/pgpass` (the `pg_passfile` setting; mode 600), and
the judgement keys in `judgement-keys` and the optional long-lived Claude
token in `claude-token` beside it, paths derived from the password file's
so the sandbox deny, derived from the same setting, cannot drift from
them. A Keychain item is readable by a turn through `security`,
and the vault `.env` syncs to iCloud and is loaded into the environment of
the old system's unsandboxed sessions; this directory is neither, and every
sandbox profile denies it. libpq reads the password file for every kernel
connection, so no kernel process holds the password in a string or its
environment. `python -m core secure-login` makes it and is the only code
that writes it. Tom's own `psql` reaches the kernel databases by
`export PGPASSFILE=~/.config/valor-kernel/pgpass` in his shell, which no
turn inherits: `turn` drops every `PG*` variable, and `workspace_turn`
copies only its allowlist.

## Postgres on the machine

Two kinds of cluster run on each machine, never one shared.

- **The machine cluster**, resident, on the default port and a Unix
  socket. The kernel connects as `valor_kernel`, which can read and append
  and never update or delete the ledger. Roles and grants are
  [data.md](data.md).
- **A workspace cluster** per task the kernel provisions, on demand, on
  its own port (5440 to 5599; its Redis, 6400 to 6499) with password
  authentication, owned by the workspace, holding only the app's test
  databases. The turn's sandbox reaches it and is denied the machine
  cluster.

The separation exists because a machine cluster that trusted loopback let
a turn reach the ledger as the kernel's role (rebuild-demonstration.md,
Kernel findings 1). It costs one more Postgres, 36 MB idle.

On the machine cluster, every role needs its password on the kernel
databases (`valor_rebuild`, `valor_rebuild_test`): `python -m core
secure-login` puts three `scram-sha-256` rules (socket, `127.0.0.1`, `::1`)
in a marked block ahead of every other rule in `pg_hba.conf`, written to a
temporary file and renamed into place, and puts the original back without
reloading if the server would not parse the result. Other databases on the
cluster keep their `trust` rules. Every workspace sandbox profile denies the
cluster's data directory (the `pg_data_dir` setting), so a turn cannot
edit `pg_hba.conf` or the heap files.

**After a Homebrew major upgrade of Postgres.** The upgrade runs `initdb`
for a new data directory, whose `pg_hba.conf` trusts every local login
again. Update the `pg_data_dir` setting to the new directory (the nightly
dump fails, naming both paths, until it matches), then run `python -m core
secure-login` and check that a login without the password file is
refused.

## Backups

`python -m core backup` dumps the kernel database to the `backup_dir`
setting, an external disk (`/Volumes/<U+F028>/valor_temp` today; the
volume's name is that one private-use character, which macOS shows as
blank, and renaming it means changing the setting). Each dump is a
`pg_dump` custom-format file with a manifest beside it (counts and SHA-256
digests of the events and documents, read from the dump's own snapshot,
and the dump's own SHA-256). The newest 30 are kept; the ledger itself is
kept forever. The dump refuses a missing directory (an unmounted disk), a
directory on the cluster's own disk, and a `pg_data_dir` setting that is
not the cluster's real data directory (read from the server), since every
sandbox profile denies that setting's path. One dump runs at a time per
directory, under an exclusive lock on `.valor_rebuild.lock` there: the disk
is exFAT, which has neither hard links nor an exclusive rename, so the lock
is what keeps two dumps from taking one name. The directory is synced to
disk after each dump's renames. `python -m core restore DUMP`
restores a dump into a scratch cluster under `/tmp`, compares it with its
manifest, and removes the cluster.

**Rehearsed on 2026-10-01** from the command line: `backup` wrote
`valor_rebuild-20261001T151206Z.dump` (1,416 events, max id 1,416, 22
documents, 160,622 bytes) in 4.7 seconds, and `restore` of it matched its
manifest in 1.2 seconds, leaving no cluster behind.

**Installing the nightly job** (03:00; launchd runs a job missed during
sleep once on wake):

```bash
mkdir -p ~/Library/Logs/valor
.venv/bin/python -m core backup --plist > ~/Library/LaunchAgents/com.valor.backup.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.valor.backup.plist
launchctl kickstart gui/$(id -u)/com.valor.backup   # one dump now, by launchd
tail ~/Library/Logs/valor/backup.log
```

Run the first two lines from the kernel checkout: the plist names that
checkout and its interpreter. A launchd job needs macOS's permission to
read and write removable volumes, which Terminal already has; if the
kicked dump fails with "Operation not permitted", grant it to that
interpreter in System Settings, Privacy and Security, then kick it again.
`launchctl bootout gui/$(id -u)/com.valor.backup` removes the job.

## Open questions for Tom

1. **Mains power and sleep.** Whether the machine is kept awake, or a power
   assertion per turn is enough.

## Gaps

Each estimate above is a gap until measured on the Air:

- macOS's own resident memory on a 16 GB Air.
- A connected Telegram bridge after a week of traffic.
- A working `claude -p` turn's peak, subagents included, and the peak of
  the work it runs (a Django test suite is the first case to measure).
- An Apple container's real memory cost at its default allocation.
- A headless browser rendering one page of a workspace app.
