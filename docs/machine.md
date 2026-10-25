# The machine

One Valor install runs on one Mac, and the design is for one machine. Valor
has four Macs, one per client (Tom, 2026-10-11). Every project of a client,
or every internal Yudame project, runs on that client's Mac, and Valor is
logged into the same accounts for all of them. Each Mac keeps its own
kernel Postgres, ledger, and bridges. Exactly two things cross machines:
the code that runs Valor, and the memory store on a shared Postgres (see
The shared memory store). There is no cross-machine ledger, no hub, and no
replication. The hard boundary between clients is the Mac and the accounts
on it (docs/business.md, constraint "Generic across businesses").
Production runs on Valor's Macs, never on Tom's (Tom, 2026-10-01). The
design target for one install is a MacBook Air M4 with 16 GB of RAM, Mac
native. This doc says what runs on it all the time, what runs only while work is in flight,
how much memory each part may take, how many turns run at once, where
secrets live, and how launchd starts things. It enforces the plan
constraint "16 GB of RAM": Postgres, up to `max_turns` (default 2) `claude -p`
turns at a time, and the bridges coexist, and no model runs resident.

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
| Postgres, the machine cluster | Postgres 18 holding the kernel's state: tasks, the ledger, metered spending, steering, corrections | Constraint "a ledger the system cannot edit"; Mission item 6 (the attention log lives in the ledger) | Fresh idle cluster: 36 MB footprint summed over 9 processes (58 MB RSS). The kernel's own cluster after 32 hours of light use: 90 MB. One idle client connection: 6 MB (10 MB RSS) | 256 MB, with `shared_buffers` at its default 128 MB |
| The kernel process | The control loop, the model gateway, and the broker in one Python process | Constraint "bounded authority, metered spending": every model call passes the gateway, every effect the broker | Python with `core` imported, the gateway listening, one kernel connection open: 41 MB (56 MB RSS). Bare Python 3.14: 16 MB RSS | 150 MB (estimate; streaming calls and the broker's effects in flight add to the idle figure) |
| Telegram bridge | One Telethon client: receive, normalize, hand to `core/`; deliver what the broker releases | Mission item 1 (Tom talks to one place); constraint "one identity" (every message leaves as Valor) | Telethon imported and a client built, not connected: 80 MB (223 MB RSS) | 250 MB (estimate; a connected client caches entities and grows) |
| Email bridge | IMAP receive, SMTP deliver, same port | Mission item 1; constraint "one identity" | Python with psycopg, imaplib, smtplib, and ssl imported: 31 MB (45 MB RSS) | 100 MB (estimate) |
| Slack bridge | One Socket Mode connection and Web API client: receive, normalize, hand to `core/`; deliver what the broker releases | Mission item 1; constraint "one identity" | Not measured | 100 MB (estimate; measured after a day connected in the live window) |

Resident total: about 760 MB above macOS. macOS itself, with its system
services and no user apps, is estimated at 3.5 GB on a 16 GB Air.

**What exists today.** The machine cluster runs under launchd as a
Homebrew service. The kernel process is `python -m core serve`, kept alive
by its LaunchAgent (`python -m core serve --plist` prints it); the gateway
lives in it and outlives any one task. The email bridge is in
`bridges/email/`, kept alive by its own LaunchAgent
(`python -m bridges.email --plist` prints it); the Telegram bridge is in
`bridges/telegram/`, kept alive the same way (`python -m bridges.telegram
--plist`); the Slack bridge is in `bridges/slack/`, kept alive the same
way (`python -m bridges.slack --plist`); the local chat bridge is in
`bridges/local/`, kept alive the same way (`python -m bridges.local --plist`).

## What runs on demand

On demand means started for one run of one task and gone when it ends.
The kernel starts and stops these; launchd never does.

| Component | When | Serves | Measured | Planned |
|---|---|---|---|---|
| One `claude -p` turn | Each turn of a task: build, clarify, feedback, and the verifier's turn | Mission item 1 (the frontier tier does the hard work) | A `claude -p` with no model reply yet: 116 MB (235 MB RSS). Two long interactive Claude Code sessions on the same Mac: 150 MB and 233 MB (430 MB and 535 MB RSS) | 1,000 MB (estimate, covering subagents a turn starts and a long session's growth) |
| One Pi turn | A turn of a task whose Brief names the `pi` harness, in place of a `claude -p` turn | Mission item 5 (a second harness behind the same port) | A Pi 0.73.1 turn on a scripted one-reply provider, under the turn's sandbox profile: 116 MB (156 MB RSS) | Takes the `claude -p` row's allowance, since it runs in that turn's place |
| The turn's work | Commands the turn runs: test suites, dev servers, package installs, builds | Mission item 1 ("testing actual use") | Not measured | 3,000 MB (estimate; a Django test run with its own Postgres database is the reference load) |
| The workspace cluster | A Postgres cluster of the workspace's own, separate from the machine cluster, for the app's tests | Constraint "a ledger the system cannot edit": a turn's tests never share a cluster with the kernel's ledger (rebuild-demonstration.md, Kernel findings 1) | Fresh: 36 MB, as above. The replay series' workspace cluster after every run's test suites: 352 MB summed (383 MB RSS), of which the checkpointer and background writer each count the touched shared buffers | 400 MB |
| A Redis per run | Only when the project under work needs Redis for its tests | Mission item 1 | An idle `redis-server`: 2 MB RSS | 50 MB |
| A headless browser | When a turn opens the app it built to look at it | Mission item 1 ("testing actual use"); rebuild-demonstration.md, Recommendations; rebuild-baseline.md, Browser use | One page of a Django app's admin login, rendered by `look` (a screenshot run and a page-dump run), five times: peak resident memory of the browser's process tree 372, 343, 344, 330, and 343 MB, summed per process by `ps` so shared pages count more than once | 372 MB |
| The dashboard (`ui/`) | When Tom opens it | Mission item 6 (the attention log is readable without asking) | Not measured | 100 MB (estimate) |
| A routine's run | When launchd fires its schedule | As the routine's objective names | Same as a turn, since a routine is a task | Counted in the turn's line: a routine's turn takes a turn slot like any other |
| Judgement calls | Every classification, routing, and cheap check | The three-tier constraint: judgement is hosted | HTTP from the kernel process; no local model | Included in the kernel process |

A turn's processes do not outlive it. The kernel names each turn's
processes by its id and reaps them when the turn ends: SIGTERM, then
SIGKILL two seconds later, with a `turn.reaped` ledger row listing them.
Daemons a test suite leaves behind (a daemonized `redis-server`, a dev
server) are in that list. This is what keeps on-demand memory on demand,
and it serves the constraint "reliable stop, recovery, and correction".

Each task the kernel provisions has a workspace cluster of its own (and a
Redis when its project asks). They start when a run of the task first
needs them. A `python -m core run` stops them when it returns; the resident
kernel keeps a task's services up between its steps and stops them when the
task needs Tom, is done, or is stopped. Every run first stops the services a
killed kernel left up, for other tasks and for provisionings that died,
unless their own run, their services' lock `services:<task>`, or their
provisioning is live, so on the 16 GB machine only the working tasks'
services are up. Idle, a workspace cluster
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
| Slack bridge (estimate; measured after a day connected in the live window) | 100 |
| **Resident subtotal** | **4,356** |
| One turn: `claude -p` 1,000, its work 3,000, workspace cluster 400, Redis 50 | 4,450 |
| Two turns (`max_turns`) | 8,900 |
| Headless browser (one turn at a time opens it) | 372 |
| Dashboard | 100 |
| **Peak with two turns running** | **13,728** |
| **Left of 16,384** | **about 2,650** |

The 2.6 GB left is file cache and memory compression. The machine is Valor's, so no desktop apps of
Tom's share it. A third turn adds another 4,450 MB and leaves about -1,800 MB, which is swap, and a
turn swapped to disk is slower than two turns run back to back. It is not room for a local model
either (see Judgement).

## Concurrency

**Two turns at a time.** The kernel runs at most `settings.max_turns` harness turns or checks at
once on a machine (`VALOR_MAX_TURNS`, default 2, set from the RAM plan above), whatever task they
belong to; a build turn, the verifier's turn, and a routine's turn take the same slots. A task whose
turn is ready while every slot is held waits in Postgres, in order, foreground work first. This is a
scheduling rule, not a check on the agent: it fits the 16 GB constraint, and it serves Mission item
1, since a turn swapped to disk or killed by memory pressure is a lost turn Tom has to notice.

**The checks after a candidate.** Test, review, and docs run in parallel
in the design ([sdlc-state-machine.md](sdlc-state-machine.md)), and on the Air they take the slots
that are free: the review and docs turns are turns, and the test branch's suite run is turn-sized
work (the 3,000 MB line above). With two slots, two of them run at once and the third waits, with
the same verdicts and the same join. A suite run stops the task's own Postgres and Redis and starts
fresh ones on the same ports for itself, so a check adds no service to the memory budget; its copy
of the package caches is an APFS clone of `checks/seed/`, near free on disk and no RAM.

A turn slot is one of the session advisory locks `turn-slot:<machine>:<i>`, `i` below `max_turns`
(`core/slot.py`): every harness turn holds one, so a second kernel or a `python -m core run` beside
`serve` waits for a free one. It is reentrant within the task that holds it. The test, review, and
docs checks hold one for their whole run, and the turn a review or docs check runs takes it again as
a no-op.

**What runs beside the turns.** Bridges keep receiving and delivering released messages while turns
run; an incoming request becomes a task in one queue across projects: foreground work first, then
start order, moved by the writers' placements (`python -m core shape TASK --before T`), which picks
the next ready task for a free slot and never refuses one. Judgement calls are hosted HTTP and run concurrently
with a turn at no memory cost, so a request can be classified (and sent to clarify or build, per
[judgement-layer.md](judgement-layer.md)) while other tasks' turns hold the slots.

**What the baseline says about concurrency.** The baseline series
(rebuild-baseline.md) ran its 13 replay runs on a 64 GB machine, up to
three at once, each run with one
Valor turn at a time metered through the kernel's gateway beside its
stand-in and judge calls, and met no rate limit. That bounds the API side: at this
account's limits, the Air will not be throttled by running two turns at a
time. It says nothing about the Air's memory, which is the
binding limit.

**Time on the slot.** A turn that waits on a question or on feedback has
ended; its slot is free until the answer arrives and the next turn starts.
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
not measured). Resident, it takes more than the 4 GB headroom with every
turn. Loaded beside a turn, it pushes the machine into swap.

The fallback is hosted too: Qwen3-235B-A22B Instruct 2507 on Parasail,
reached through OpenRouter. Tom asked for Jev's own model on a second
provider (2026-10-01), but Jev's base model is not published. It costs the
Air no memory and stays available while a turn holds the slot. A call
neither leg answers with confidence takes its abstain route, per
[judgement-layer.md](judgement-layer.md).

## Sandbox

**sandbox-exec** wraps the turn's process tree in a profile. It costs no memory of its own. The
kernel runs every workspace turn under one; the profile denies the kernel key directory, the machine
cluster's data directory, the backup disk, and the machine cluster on loopback (port 5432 and its
socket), and allows everything else ([workspace.md](workspace.md)). Denies come before allows.

## launchd

launchd starts everything that starts on its own. There is no other
scheduler, supervisor, or watchdog on the machine.

- **LaunchAgents with `KeepAlive`**: the machine cluster, the kernel
  process, and each bridge. launchd restarts one that exits. The one
  other restart is the kernel's, of a bridge a merge left on old code:
  each bridge holds its lock on a connection named
  `valor-<channel>@<commit>`, the commit it started at (`@none` when it
  could not read it). When a kernel start ends one of its own rollouts
  (after the migrate, before `rollout.ended`), it reads the connection
  holding each bridge's lock in the kernel database, and restarts with
  `launchctl kickstart -k gui/<uid>/<label>`, one at a time, waiting at
  most 10 s each, every bridge whose diff from that commit to the running
  one reaches its code: `bridges/<name>/`, any `core/` or `bridges/` file
  it imports (`core/bridge.py`, `core/intake.py` and `core/channels.py`
  for every bridge; not `core/serve.py`, `core/rollout.py` or
  `core/router.py`), `uv.lock` or `pyproject.toml`. A bridge with no
  commit in its name (started before the stamp) is restarted; one at
  `@none` is not. The `rollout.ended` row names the bridges restarted and
  any that failed, and a failed one is tried again only at the next start
  that ends a rollout. A merge of only `docs/`, `tests/`, `persona/`,
  `skills/` or top-level `*.md` restarts nothing. Every update's
  `rollout.ended` row also names each installed bridge `connected since`
  the second its own connection came up, or `not connected`, waiting up
  to 60 s for one it just restarted or installed
  ([telegram.md](bridges/telegram.md#the-outbox), Connected). The kick restarts
  launchd's instance of the label, so a bridge started by hand that holds
  the lock keeps running its old code until it is stopped. A bridge
  reconciles its outbox on start, so a send the kill cut off is looked up
  before it is tried again. Nothing kills a process on a heuristic such
  as age or memory; stop and recovery are the kernel's (constraint
  "reliable stop, recovery, and correction").
- **LaunchAgents with `StartCalendarInterval`**: routines. Each firing
  runs `python -m core routine NAME`, which starts or continues a run that
  is a child of the routine's objective, so a routine's turn takes the turn
  slot and its spend is metered like any other. `routine NAME --plist`
  prints the plist for this Mac; see [routines.md](routines.md).
- **Every job runs through Valor.app.** A plist's first argument is
  `settings.launcher`, `~/Applications/Valor.app`'s `valor-run`, which runs
  the rest as its child and exits as it did. macOS attaches a privacy grant
  to that app instead of to the Homebrew Python's path, which changes with
  every upgrade and was asked for each grant again. `python -m core
  permissions` (`core/permissions.py`) builds the app once and never
  rebuilds it, since a rebuild drops its grants, and asks for the grants at
  the Mac ([rollout.md](rollout.md), step 9). Every update checks this
  (`permissions.ensure`): it builds a missing app, puts the launcher first
  in any `com.valor.*` job still running a bare Python, and reloads it.
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
sleep disabled is Valor's operational call for each Mac, not yet made.

## Keychain

Kernel-held secrets live in the kernel key directory, the directory of
the `pg_passfile` setting (`~/.config/valor-kernel/`, mode 700), which the
sandbox profile denies. They are not in the Keychain, because a turn
can read the login keychain (below). A secret is read by name when the
process that needs it starts; a missing name fails the start with the name
in the error. No kernel secret is in the repository or in a turn's environment.
This serves the constraint "bounded authority, metered spending": the
kernel's credentials (its databases, its model and judgement keys) are
authority a turn never holds.

The businesses' own credentials are another matter. Yudame shares them
through its shared 1Password vaults: `Yudame Secrets` for company services,
`m-valor` and `Valor env` for Valor's own accounts and for tokens Tom
makes for Valor (the Mercury token is item `Mercury API Token` in `m-valor`). A turn reads one at use time with `op read`,
holds it in memory only, and never writes it to a file, a commit, a log or
a message. `op` signs in with the 1Password service account,
`OP_SERVICE_ACCOUNT_TOKEN`, which a turn's environment carries (checked
2026-10-21) and which the vault `.env` holds for a process that lacks it
(`scripts/deploy-site.sh`, `mercury.py`). A credential Valor makes for
itself goes into `m-valor`.

| Secret | Read by | Today |
|---|---|---|
| The Anthropic credential for frontier turns | The gateway, which sets it on every call; a turn carries only a placeholder, since it runs with its own Claude Code config directory | `claude-token` in the kernel key directory (a long-lived token from `claude setup-token`) when present; otherwise Claude Code's own login, read from the Keychain through `/usr/bin/security`, which lasts about eight hours and is refreshed only by Claude Code sessions on the default config directory |
| The judgement legs' keys, `TYPESAFE_API_KEY` and `OPENROUTER_API_KEY` | The kernel process, when `run`, `calibrate`, or `serve` builds the judgement port, and only for a leg pointed at its default endpoint | `judgement-keys` in the kernel key directory (mode 600, `NAME=value` lines), written only by `python -m core judgement-keys`, which copies them from the vault `.env` and prints each name with `written`, `kept`, or `missing`, never a value. Held in the two adapter objects, never in `os.environ`, a ledger row, an exception, or a log line |
| The OpenAI key, `OPENAI_API_KEY` | The gateway, which sets it on every call of the OpenAI route and drops any key, organization, project, or `proxy-authorization` the turn sent; a gateway with no key forwards the turn's own `authorization`, and a 401 is answered with a body naming which key was refused | `openai-key` in the kernel key directory (mode 600), written only by `python -m core openai-key [--name NAME]`, which copies the vault's key (default `OPENAI_API_KEY`) over the one held and prints `written`, `kept`, or `missing`, never a value |
| Telegram API id, hash, and session | The Telegram bridge, at start | `telegram-keys` (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, mode 600) in the kernel key directory, written only by `python -m bridges.telegram keys`, which copies them from the vault `.env` and prints each name with `written`, `kept`, or `missing`; the session is `telegram.session` beside it, mode 600, written by `python -m bridges.telegram login`, which Tom runs and types the code and password into. No output holds any part of the hash |
| Slack app and user tokens | The Slack bridge, at start | `slack-keys` (`<team>_SLACK_APP_TOKEN`, `<team>_SLACK_USER_TOKEN`, mode 600) in the kernel key directory, written only by `python -m bridges.slack keys`, which copies them from the vault `.env` under the team id `auth.test` names and prints each name with `written`, `kept`, or `missing`. No output holds any part of a token |
| Mail credentials, `IMAP_USER`, `IMAP_PASSWORD`, `SMTP_USER`, `SMTP_PASSWORD` (a Gmail app password) | The email bridge, at start | `mail-keys` in the kernel key directory (mode 600), written only by `python -m bridges.email keys`, which copies them from the vault `.env` and prints each name with `written`, `kept`, or `missing`, never a value. Held in the bridge's `Config` only, never in `os.environ`, a ledger row, or a log line |
| The GitHub push token, `GITHUB_PUSH_TOKEN` | The merge performer of a task the kernel provisioned, for a merge to a remote, and the `conditions` routine, for fetching a business repository; never the turn and never `push_branch` | `github-keys` in the kernel key directory (mode 600), written only by `python -m core github-key`, which copies it from the vault `.env` and prints `written`, `kept`, or `missing`. For each git call against the remote the kernel writes a config file of its own, mode 600, in the same directory, holding one header (`Authorization: Basic`, user `x-access-token`) scoped to that exact URL, with `http.followRedirects=false` so the header never follows a redirect, gives git its path as `GIT_CONFIG_GLOBAL`, and removes it when git exits; the call also pins no redirect and no proxy on its command line, which outranks the repository's config; the file is named by the PID of the process writing it, and a file a crash left is removed by the next merge once that process no longer exists. GitHub's refusal of the token fails the merge saying to rotate it |

**Where a merge lands.** A merge lands on the URL and target branch the
kernel recorded in the task's Brief at start: the project spec's
`merge_url`, else the task's own bare origin, and the spec's
`target_branch` or `branch`, else the branch the source's HEAD names. No
turn changes either, and the broker refuses a merge naming any other. Any
branch may be the target, the remote's default branch included. The URL
the header is written for is plain `https://host/path`, with no user,
port, query, or quoting. A merge to a local origin needs no token; a
`--workspace` task merges with no token.

What a merge can reach beyond its Brief is bounded by the token alone.
`GITHUB_PUSH_TOKEN` belongs to Valor's own GitHub account, `valorengels`,
and that account is a collaborator only on the repositories Valor builds,
so the token writes nothing else; a repository Valor must not write is one
the account is not a collaborator on. Adding the account as a collaborator
is each repository owner's grant; replacing the token with a fine-grained
one limited to those repositories is Valor's, since the account is its own.

**Reading what a turn wrote.** A file or directory under a turn's own
state is the turn's to change, so the kernel opens it component by
component from a directory it owns, following no link at any step
(`workspace.open_turn_dir`). `open_turn_file` keeps a file only when it is
regular, has one link (a hard link could name a file in the kernel key
directory), and is not sparse, and otherwise returns why. The transcript copy
([harnesses.md](harnesses.md)) is read this way.

`serve` builds the judgement port once, when it starts, and holds the
keys for its life, so a key rotated with `judgement-keys` reaches the
resident kernel only when it starts again (`launchctl kickstart -k
gui/$(id -u)/com.valor.kernel`).
With a judgement key missing, `serve` exits with `serve refused:` and the
key's name; launchd's `KeepAlive` starts it again, at most once every ten
seconds (launchd's default throttle), and each start writes the same line
to `kernel.log` until the key is installed.

The kernel also exits with status 1 to run its own merged code
(`rollout.restarting`, [architecture.md](architecture.md)); `KeepAlive`
starts it again, so a rollout restart needs no `kickstart`, and it loads
the judgement keys again too. The new kernel's start then kicks each
bridge the merge reached (the launchd section above).

A turn's environment is the operator's, less the kernel's secrets (the `ANTHROPIC_`, `CLAUDE`, and
`AI_AGENT` variables, the OpenAI, TypeSafe, OpenRouter, and GitHub push keys, and the `PG*` password
variables). Over it goes the repository's own `.env` from this Mac's checkout, `~/src/<repo>/.env`
(`workspace.repo_env`), as a developer working there has it. The kernel reads it at each step and
never records it. Setup, suite and lint get all of it; the turn's agent gets it less the kernel's
own names, so a project key never pays for a turn. The task's own variables go over both: a task
with its own database sets every libpq name and gets no `PGPASSWORD`, from the `.env` or anywhere,
so each role its password file covers logs in with its own line. It carries Valor's own
`gh` login, git credential helper, and SSH agent.

It also carries Valor's Google sign-in, valor@yuda.me, which `gws` uses
for Gmail, Calendar, Drive, Docs, Sheets, Slides, and Tasks. The refresh
token is `~/.config/gws/credentials.json`, mode 600: gws's plaintext
store, so it does not depend on a keychain key and copies to any Mac.
`token_cache.json` beside it holds the short-lived access token,
encrypted with the keychain's `gws-cli` key. That cache is disposable:
gws skips one it cannot decrypt and writes it again. The token holds only
Workspace scopes, never `cloud-platform` or `pubsub`. Yudame's Google
Cloud session control makes tokens with Cloud scopes sign in again, which
is the likely reason the June token, signed in with gws's default
scopes, failed with `invalid_rapt` on 2026-10-17. It ends
only when it is revoked, the account's password changes, or its
2-Step Verification changes. The Gmail app password in `mail-keys` ends
the same way. Signing in again: rollout step 6.

A turn runs as the machine's one macOS user, so it can read that user's
login keychain on purpose through the `security` tool, the machine's Claude
login included (rebuild-demonstration.md, Setup: Isolation). Tom decided on
2026-10-01 not to add a separate macOS user for turns: a turn that used the
login to call the provider around the gateway is an accepted risk, because
the gateway is for visibility and honest metering, not a hard wall.

**The kernel key directory.** The passwords for `valor_kernel`,
`valor_memory`, and the owner role on the kernel databases live in a libpq
password file,
`~/.config/valor-kernel/pgpass` (the `pg_passfile` setting; mode 600), and
the judgement keys in `judgement-keys`, the mail credentials in `mail-keys`, the Slack tokens in `slack-keys`, the OpenAI key in `openai-key`, and the optional long-lived Claude
token in `claude-token` beside it, paths derived from the password file's
so the sandbox deny, derived from the same setting, cannot drift from
them. A Keychain item is readable by a turn through `security`,
and the vault `.env` syncs to iCloud and is loaded into the environment of
the old system's unsandboxed sessions; this directory is neither, and the
sandbox profile denies it. libpq reads the password file for every kernel
connection, so no kernel process holds the password in a string or its
environment. `python -m core secure-login` makes it, adds `valor_memory`'s
lines when it lacks them (written to a file beside it and renamed over it),
and is the only code that writes it. An update runs `db.migrate` alone, never
`secure-login`, so a Mac whose password file predates `valor_memory` runs it
by hand once; until then memory's logins fail with `fe_sendauth: no password
supplied`. Tom's own `psql` reaches the kernel databases by
`export PGPASSFILE=~/.config/valor-kernel/pgpass` in his shell, which no
turn inherits: `workspace_turn` drops the `PG*` password variables.

## Postgres on the machine

Two kinds of cluster run on each machine, and neither is shared with
another Mac or with a turn.

- **The machine cluster**, resident, on the default port and a Unix
  socket. The kernel connects as `valor_kernel`, which can read and append
  and never update or delete the ledger; memory connects as `valor_memory`,
  which owns the schema `memory` and reaches nothing else. Roles and grants are
  [data.md](data.md). The schema `memory` is here until the `memory_dsn`
  setting names the shared memory store (below).
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
cluster keep their `trust` rules. The workspace sandbox profile denies the
cluster's data directory (the `pg_data_dir` setting), so a turn cannot
edit `pg_hba.conf` or the heap files.

**After a Homebrew major upgrade of Postgres.** The upgrade runs `initdb`
for a new data directory, whose `pg_hba.conf` trusts every local login
again. Update the `pg_data_dir` setting to the new directory (the nightly
dump fails, naming both paths, until it matches), then run `python -m core
secure-login` and check that a login without the password file is
refused.

## The shared memory store

popoto's store ([memory.md](memory.md)) goes on one Postgres that all four
Macs reach, so a standard of working Valor learned for one client is
recalled on another client's Mac (docs/business.md, constraint "Generic
across businesses"). Every record carries its client, its project, and its
ledger. Each Mac's kernel ingests its own ledger's rows into it and recalls
from it; no ledger row, task, or grant crosses with them. The store holds
only what memory ingests from the four ledgers, and it grants nothing
([memory.md](memory.md), Memory grants nothing).

The code is built: the setting `memory_dsn` (`VALOR_MEMORY_DSN`) names the
store, apart from the kernel's DSN. Until the host is chosen it is unset,
and each Mac's memory is the schema `memory` in its own machine cluster.

**Chosen when a second Mac is installed.** At cutover the store stays
where the Cowboy's kernel has it: `memory_dsn` unset, the schema `memory`
in the Cowboy's own machine cluster. Where the shared Postgres runs, how
each Mac reaches it (address, transport, and the role each Mac's kernel
logs in as, which `memory_dsn` then names), and, if it runs on one of the
four Macs, its line in that Mac's RAM plan are Valor's operational call,
made with the Captain's install, the first second Mac, and recorded here
then. Until then the RAM plan above has no line for it.

**When a Mac cannot reach the store.** Its server not accepting a
connection within two seconds, the turn runs without memory recall and
does not wait for the store, and its Brief records `Memory: recall
skipped: the store could not be reached (<reason>)`. The ingest after the
turn is skipped the same way; the next one that reaches the store takes
the rows.

## Backups

`python -m core backup` dumps the kernel database to the `backup_dir`
setting, by default `~/valor-backups` on the Mac's own disk, which covers a
bad migration or a damaged database but not the disk itself. On the Cowboy
it is set by `VALOR_BACKUP_DIR` to its external disk,
`/Volumes/PINK/valor_temp`, carried in the backup job and in the kernel's
job, whose rollout backs up before a schema change. Each dump is a
`pg_dump` custom-format file with a manifest beside it (counts and SHA-256
digests of the events and documents, read from the dump's own snapshot,
and the dump's own SHA-256). The newest 30 are kept; the ledger itself is
kept forever. The dump refuses a missing directory (an unmounted disk) and a
`pg_data_dir` setting that is
not the cluster's real data directory (read from the server), since every
sandbox profile denies that setting's path. One dump runs at a time per
directory, under an exclusive lock on `.valor_rebuild.lock` there: the Cowboy's disk
is exFAT, which has neither hard links nor an exclusive rename, so the lock
is what keeps two dumps from taking one name. The directory is synced to
disk after each dump's renames. `python -m core restore DUMP`
restores a dump into a scratch cluster under the kernel key directory (the
`pg_scratch` setting, `~/.config/valor-kernel/run`, which no sandbox profile
can read or write), compares it with its manifest, and removes the cluster.
The digests cover `events` and `documents`, not the schema `memory`, whose
records are rebuilt by ingesting the ledger again. Once memory is on the
shared store, a rebuild ingests all four Macs' ledgers, each from its own
kernel database; what is taken is kept per ledger. A restore is made with
`--no-owner`, so after restoring a dump for use, run `python -m core
migrate` against it: it gives the schema `memory` and its tables back to
`valor_memory`.

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
The grant follows the interpreter's binary, so an upgrade of
`python@3.14` voids it and the job then hangs opening the backup
directory. `brew pin python@3.14` keeps the nightly brew job from
upgrading it; after a deliberate upgrade, grant and kick again.
`launchctl bootout gui/$(id -u)/com.valor.backup` removes the job.

## Gaps

Each estimate above is a gap until measured on the Air:

- macOS's own resident memory on a 16 GB Air.
- A connected Telegram bridge after a week of traffic.
- A connected Slack bridge after a day in the live window.
- A working `claude -p` turn's peak, subagents included, and the peak of
  the work it runs (a Django test suite is the first case to measure).
- A Django suite's peak with every test reaching its database (a project's `extensions` make it in the task's Postgres).
