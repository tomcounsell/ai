# Harnesses

A harness is the agent program that does a turn's work: Claude Code today,
Codex and Pi later. It sits in the agents tier of the three (README, "Three
tiers"): the kernel decides what a turn may do and pays for it, and the
harness does the work inside those bounds. This doc covers the port every
harness conforms to, the Claude Code wrapper, how a task's turns share one
session, how a turn talks back to the kernel, the sandbox a turn runs under,
what happens to the processes it leaves behind, and the workspace it works in.

`harnesses/` holds the wrappers. `core/runs.py` holds the port and runs one
turn; `core/session.py` runs a task's turns; `core/signals.py` holds the
signal channel. The gateway's pricing, reservation, and charging belong to
`docs/architecture.md`, as do the task loop's states, the broker, approvals,
stop, and the execution record. This doc covers the harness's side of each.

## The harness port

Serves the constraint **bounded authority and spend**: the kernel runs any
harness without knowing which one, so authority never moves into a wrapper.

The port is one value, `TurnCommand`, built fresh for each turn:

| Field | What it carries |
|---|---|
| `argv` | the command line that runs one turn |
| `env` | the turn's whole environment; nothing is inherited past it |
| `cwd` | the workspace the turn works in |
| `harness` | the harness's name, recorded in `turn.started` |
| `parse` | reads the harness's stdout into the fields the ledger keeps |

A wrapper supplies a builder. The kernel calls it with three values only it
knows at that moment: the gateway base URL issued for this turn, the Brief
rendered from the ledger as the turn starts (corrections included), and the
turn's id. The builder returns the `TurnCommand`. The kernel records the argv
and the Brief whole in `turn.started`, so the ledger shows exactly what the
turn was given, then runs the command.

`parse` returns at least `text`, `is_error`, and `session_id`, and may
return what the harness says it spent (`harness_reported_usd`). The kernel
keeps that figure beside the gateway's metered figure and never in its place:
a harness's account of itself is narration, and the ledger records what
passed the gateway [6]. In the demonstration the two agreed to $0.000024 over
$2.97 (rebuild-demonstration.md, Money).

A wrapper holds no effect class. Anything a turn does beyond its workspace
reaches the world as a request the broker decides (see the signal channel
below).

## Running one turn

Serves the constraints **reliable stop** and **16 GB of RAM**.

`core.runs.run_turn` starts the command with stdin closed, stdout and stderr
captured, and a new process group, then waits for whichever comes first: the
process exits, or a stop notification for the task arrives from Postgres. On
a stop it revokes the gateway, which cuts every in-flight model call, sends
SIGKILL to the whole process group, waits for every call to be charged, and
writes `turn.ended` with outcome `stopped`. Nothing inside a turn is trusted
to honor a gentler signal, and nothing the turn owns lives only in the
process, so the stop loses nothing. The off switch is the kernel's, never the
model's incentive [8, 9, 10]. `tests/test_live_turn.py` runs one real turn
through the gateway and a real stop mid-stream.

`turn.ended` carries the outcome (`done`, `failed`, `stopped`), the return
code, the parsed result, the last 400 bytes of stderr, and the metered total
for the turn.

One harness turn runs at a time on the machine. The baseline series ran
three replays at once under slot locks on a 64 GB machine
(rebuild-baseline.md, Infrastructure); the target is a 16 GB MacBook Air,
where Postgres, the bridges, and one `claude -p` share the RAM. The RAM a
turn takes is `docs/machine.md`'s to state.

## The Claude Code wrapper

`harnesses/claude_code.py` builds two kinds of turn.

**`turn`** is one self-contained call: no tools by default, no session
persistence, a system prompt of the persona followed by the Brief. The
kernel's smoke script uses it.

**`workspace_turn`** is one turn of a task that works in a directory, and is
what a real task runs. Its arguments:

| Argument | What it does | Serves |
|---|---|---|
| `-p --output-format json` | one non-interactive turn; one JSON result on stdout | the port's `parse` |
| `--safe-mode` | no hooks, skills, plugins, or `CLAUDE.md` from this machine | bounded authority: the prompt layer of the machine holds no authority over a turn |
| `--strict-mcp-config` | no MCP servers | bounded authority; least privilege [11] |
| `--permission-mode bypassPermissions` | edits files and runs commands without asking | Mission item 6: nobody is there to answer a prompt, and the kernel bounds the turn |
| `--disallowedTools WebFetch WebSearch` | no web tools | independent checks: a replay cannot fetch its own answer (rebuild-baseline.md, Caveats); retrieved pages carry no instructions into the turn [7] |
| `--system-prompt-snapshot off` with `--append-system-prompt` | keeps Claude Code's own system prompt and tools, and appends the persona and the Brief, re-rendered every turn | correctable: a correction recorded mid-task reaches the next turn |
| `--resume SESSION` | continues the task's session | Mission item 1 (see below) |
| `--` before the prompt | the prompt is never read as an option | Mission item 1: a request starting "- Create new flag" failed a baseline run before this (rebuild-baseline.md, Infrastructure 3) |

The demonstration checked the system-prompt row: every `turn.started` row
carried correction 1 in the Brief and in the `--append-system-prompt`
argument that reached `claude -p` (rebuild-demonstration.md, Correction 1
rendering).

**The environment** is an allowlist [11]: `HOME`, `USER`, `LOGNAME`, `PATH`,
`SHELL`, `TMPDIR`, `LANG`, `LC_ALL`, `TERM`, plus the workspace's own
variables from the task's harness settings. Tokens, SSH and 1Password agent
sockets, and Claude Code's own session variables stay behind. The wrapper
then sets:

- `ANTHROPIC_BASE_URL`: the gateway URL issued for this turn.
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: the per-call output cap (32,000 by
  default), which sets the gateway's worst-case reservation per call.
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`: a `-p` turn that ends kills
  what it left running in the background, so commands run in the foreground.
  Two of three turns of a baseline run ended idle waiting on killed
  background tests before this (rebuild-baseline.md, Caveats). Mission
  item 1: tests that ran are tests that finished.
- `GIT_TERMINAL_PROMPT=0`; `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM=1`
  when the task gives a git config; `GH_CONFIG_DIR` when it gives a gh
  config directory.
- `VALOR_TURN`, set by the kernel: the turn's id, which the reaper uses.

**The task's harness settings** come from `python -m core start
--harness-config FILE` and are stored on the task: `sandbox_profile`,
`gitconfig`, `gh_config_dir`, `env`, and `max_output_tokens`. With a
sandbox profile the wrapper prefixes the argv with `sandbox-exec -D
GATEWAY_PORT=<port> -D VALOR_TURN=<turn id> -f <profile>`.

**The result** is Claude Code's JSON: `result` becomes `text`, and
`is_error`, `num_turns`, `total_cost_usd` (as `harness_reported_usd`), and
`session_id` are kept. Unparseable output is kept as its last 400 bytes.

The persona passed in is the one identity of `docs/persona.md`; the current
composition root passes only "You are Valor." ahead of the Brief, and
rendering the full persona into every turn is part of the design.

## A task's turns and session resume

Serves Mission item 1 (own outcomes across the whole job: one working
context from inspection to delivery) and Mission item 6 (Tom's answer lands
in the context that asked, so he never restates the task).

A task's first frontier turn opens a Claude Code session, the working
session; every later clarify, plan, build, patch, and docs turn resumes it
with `--resume`. `core.session.next_prompt` reads the session id from the
last `turn.ended` that carried one, and chooses the prompt:

| After | The turn's prompt |
|---|---|
| nothing (first turn) | the task's instruction |
| Tom's answer to a question | the answer |
| a critique's findings | the findings, asking for a revised plan or, with no rounds left, for the build |
| a `red` test run, breadth gaps, or review findings | the failures, gaps, or findings, asking for a new `done.md` (a patch) |
| Tom's feedback on a delivery | the feedback, framed as project-manager review, asking for a new `done.md` (a patch) |
| a review that passed | the doc paths and the diff, asking for the docs the change made untrue |
| a turn that left neither | "Continue." |

**Patch is a resume.** A patch never starts a new agent: it is the session
that built, resumed with what came back (Tom, 2026-10-01). The session knows
why each line is there; a fresh agent would rebuild that context from the
diff at the cost of a full read and lose the decisions not written down.

**Fresh sessions for critique and review.** Critique and review each run in
a session of their own that never resumes, and never reads, the working
session. Their inputs are files and ledger rows, so their independence is
structural. The reviewer runs the same Opus model or an Opus-class model
from another vendor through that vendor's harness; the port below is what
lets either run without the kernel knowing which.

The kernel's support today: the working session for first turn, answers,
and feedback. The other rows, and the fresh sessions, are design.

Each prompt is followed by what became of the effects the previous turn
requested, read from the ledger now, so a push Tom has since released reads
as done. An answer or feedback is spent only by a turn that finishes: after
a failed or stopped turn the next one opens with it again. That rule exists
because a failed turn once consumed feedback 1 and the retry was prompted
"Continue." (rebuild-demonstration.md, Kernel findings 4).

Every turn gets the Brief re-rendered from the ledger regardless of resume,
so the session's memory of an older Brief never stands in for the current
one.

Claude Code keeps the session as a JSONL file under
`~/.claude/projects/<workspace path with / and . as ->/`. Resume is scoped to
that directory: a session resumes only from the same workspace path.

### What resume costs

Each resumed turn re-sends the whole session. In the demonstration the first
call of the task carried about 30,000 input tokens and the last about
109,000; cache reads totalled 4.5 million tokens against 35,113 output
tokens. Delivery 2 resumed over an hour after delivery 1 and wrote 77,602
cache tokens in 9 calls, against 25,613 in 20 calls for delivery 3, which
followed promptly (rebuild-demonstration.md, Money).

**Compaction, never replacement.** The working session is always resumed.
When it nears the model's context limit it is compacted in place, by
Claude Code's own compaction, and resumed; it is never swapped for a fresh
session seeded with a summary. The demonstration and the baseline ran on
resume throughout (rebuild-baseline.md, Aggregate). **Gap:** no run here
has reached compaction, so its cost and what it drops are unmeasured; the
first long task records both in `turn.ended`.

## The signal channel: `.valor/`

Serves Mission item 6 (every question and delivery is a ledger row, so the
attention a task cost is a fold over its ledger) and the constraint
**bounded authority** (an effect is a request the broker decides).

A turn reaches Tom and the world through files under `.valor/` in its
workspace, which the kernel reads when the turn ends:

| File | Meaning | What the kernel does |
|---|---|---|
| `.valor/question.md` | a question for Tom | `question.asked`; the task waits for `python -m core answer` |
| `.valor/done.md` | a candidate delivery: what, how it was verified, what Tom should know | today `task.delivered`; in the design a candidate that test, breadth, review, and docs pass before `task.delivered` (`docs/sdlc-state-machine.md`) |
| `.valor/effects/<name>.json` | one request `{"action_type", "target", "payload"}` | each goes to the broker, which performs, holds for Tom, or refuses |

`core.signals.collect` reads them, then moves each to
`.valor/handled/<turn_id>/`, so no signal is read twice. An effect file that
is not a JSON object with `action_type` and `target` is recorded with an
error and no request. Everything a turn left is one `turn.collected` row; a
question takes precedence over `done.md` in the same turn. `.valor/` is in
the clone's `.git/info/exclude`, so signals never enter a commit.

Files, because writing one is a deliberate tool call that survives whatever
prose follows it, and a turn killed midway leaves what it wrote readable.
The text of the protocol is `signals.PROTOCOL`, carried in every workspace
turn's Brief; it lists the effects available (today, `push_branch`) and says
pushing any other way is unavailable, which the sandbox makes true.

The text inside a signal file grants nothing. A question is shown to Tom; an
effect request is classified and bounded by the broker against the task's
ceiling. The turn's own words never decide what it may do [7].

Asking before building is decided outside the harness: the judgement step
that routes an underspecified request to a clarify turn is
`docs/judgement-layer.md`'s. A clarify turn uses this same channel: it
inspects, writes its questions and intended approach to `question.md`, and
ends. The current kernel carries that instruction as the `clarify` mode's
section of the Brief (`signals.CLARIFY`).

## Transcripts

Serves the Evidence item **independent checks**, and the constraint **docs
describe reality** by giving a verifier more than the turn's own summary.

Three records of a turn exist:

1. **The ledger.** `turn.started` (argv, Brief, its digest, corrections),
   the gateway's `gateway.charged` and `gateway.refused` rows for every
   model call, `turn.collected`, `turn.reaped`, and `turn.ended`. Written by
   the kernel; the turn cannot edit it.
2. **Claude Code's session file**, the JSONL under `~/.claude/projects/`:
   every message, tool call, and tool result. The baseline's leak scan read
   every tool call of every run from these files (rebuild-baseline.md,
   Caveats).
3. **The workspace's git history and the bare origin's reflog**
   (`core.logAllRefUpdates always`).

The session file lives in a directory the turn must write to keep its
session, so it is the turn's own account, as editable by the turn as
`done.md`. It is evidence for a verifier and a debugger, never a ledger.

**Design, beyond the current kernel.** When a turn ends, the kernel copies
the session file into the document store with its SHA-256 recorded in the
ledger, so a later edit is detectable and the transcript survives Claude
Code's own housekeeping. The current kernel records the session id and
leaves the file where Claude Code wrote it. The copy is a new document
kind, which needs no migration (`docs/data.md`, documents).

## Metering through the gateway

Serves the constraint **bounded authority and spend**: every model call a
turn makes is metered against the task's one budget.

The wrapper points the harness at the gateway with `ANTHROPIC_BASE_URL`,
carrying a per-turn token in the path. Claude Code sends every call through
that base URL: the main loop, its own side calls, and any subagent it starts
inside the turn. The demonstration's 69 model calls all passed the gateway,
and the gateway and the harness agreed on the total (rebuild-demonstration.md,
Money). The gateway forwards each call with the harness's own credentials;
the harness never holds a budget, and a refused call reaches it as an API
error.

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` bounds each call's output, and the gateway
reserves each call's worst case from that bound before forwarding. A lower
cap keeps the reservation close to what a call really costs, so a nearly
spent budget still admits calls that fit.

Two points the harness relies on and has to keep true:

- **Every call takes the base URL.** A harness that reaches a provider by
  another path would spend outside the budget. For Claude Code this held in
  every run so far; the sandbox does not enforce it, since a turn reaches
  the public internet (see the sandbox below).
- **Corrections reaching subagents.** The Brief, corrections included, is in
  the system prompt of the main session. Whether a subagent Claude Code
  starts inside a turn sees it was not checked
  (rebuild-demonstration.md, Correction 1 rendering). **Gap**: the plan says
  corrections reach every agent; for Claude Code subagents that is unverified.

## The turn sandbox

Serves the constraint **bounded authority and spend** (a turn cannot perform
an effect except through the broker), the constraint **a ledger the system
cannot edit**, and the Evidence item **independent checks** (a replay cannot
read its answer). The stance is AI Control's: the protocol holds even if the
model tries to get around it [4].

A workspace turn runs under a sandbox-exec profile written for its workspace
(`scripts/demo_workspace.sh` for the demonstration, `sandbox_profile` in
`scripts/replay_workspace.py` for replays). The profile starts from
`(allow default)` and denies what a turn must not reach. It runs as Tom's
user.

### Files

- **Denied, read and write:** `~/src` (Tom's checkouts, which hold the
  answers), `~/work-vault`, Desktop, Documents, Downloads, Dropbox, iCloud
  and cloud storage, Mail, Messages, `~/.ssh`, `~/.config/gh`, and Claude
  Code's projects, plans, file history, and `history.jsonl`.
- **Allowed back, read and write:** the run's own directory, and the one
  `~/.claude/projects/` directory holding this workspace's sessions.
- **Read only:** the shared `bin/` (uv), first on the turn's `PATH`.
- **Stat only:** the run directory's ancestors, so tools that resolve real
  paths (uv building a virtualenv) work; listing them stays denied.
- **Denied writes inside the run:** the bare origin, the run's `home/` (its
  own profile, git config, and harness settings), and `replay.json`.
- **Denied entirely:** the workspace Postgres cluster's data directory.

### Loopback connects, and rule order

A turn reaches the public internet, so package installs work. On loopback it
reaches only the gateway (at whatever port the OS gave it, passed in as
`GATEWAY_PORT`), the services its run asked for, and dev servers on 8000 to
8009. The machine's own Postgres (5432 and its socket, which hold the
kernel's ledger) and Redis (6379) are out of reach.

**Denies come first and allows last.** sandbox-exec refused allowed loopback
ports for roughly two in five port numbers whenever any network-outbound rule
followed the allow block. The demonstration's second turn failed with
"Unable to connect to API (EPERM)" because its gateway drew such a port; the
first turn's port happened to work (rebuild-demonstration.md, Kernel
findings 3). The profile opens with a deny of all of loopback and the
machine Postgres sockets, then allows the gateway, the run's services, and
the dev ports. `tests/test_demo_sandbox.py` expands both profiles and probes
them under the real sandbox-exec at 24 OS-assigned gateway ports.

### Binding

A turn may listen only on 8000 to 8009 and on unix sockets inside its run.
sandbox-exec matches a bind rule on the port alone: `localhost:8001` admits
`0.0.0.0:8001`, `[::]:8001`, and the LAN address, and a literal address does
not parse. So the dev ports are reachable from the LAN while something
listens on them, which lasts only as long as the turn (see reaping). Every
other port, on every address, is refused. The bind rules sit before the
outbound denies, keeping the outbound allows last.

The bind rules exist because of an incident: during the parallel baseline
series, popoto's test setup daemonized redis-servers that outlived their
turns, one on `*:6379` open to the LAN beside the production Redis, one on a
socket in `/tmp`, and one on `*:6390` (commit 76b164458, "Keep a turn from
listening beyond its dev ports and reap what it leaves running").

### Processes and credentials

- git's keychain credential helper (`git-credential-osxkeychain`) cannot be
  executed. The turn's git config names Valor as author and has no
  credential helper; gh's config directory is empty, so gh is
  unauthenticated.
- The profile denies the mach name `valor.turn.<VALOR_TURN>`, which marks
  every process of the turn for the reaper.

### Known openings

Stated so the boundary is drawn where it is [4]:

- The turn runs as the machine's user. A deliberate keychain read through
  `security` is not fenced, and Claude Code itself reads its credential
  from the keychain.
- The public internet is open, so a turn could reach a provider directly,
  outside the gateway, or reach GitHub anonymously. The baseline found no
  such call in any transcript (rebuild-baseline.md, Caveats).
- Both openings above are accepted (Tom, 2026-10-01): no separate macOS
  user, and budgets are for visibility and honest metering, not a hard
  wall.
- `/tmp` is shared between runs. Common file names recurred across items in
  the baseline.
- Every replay database is owned by the same `test` role with the same
  password, so a turn could connect to another run's database on 5439.
- sandbox-exec is marked deprecated by Apple. The plan names Apple
  containers for sandboxes; which one runs which work is
  `docs/architecture.md`'s (The turn sandbox and reaping). A container closes
  the keychain, internet, and `/tmp` openings by construction and costs RAM the 16 GB machine
  has to find.

## Reaping what a turn leaves

Serves the constraint **reliable stop, recovery, and correction**: a turn's
processes end with the turn, and the ledger says which were stopped.

When a turn ends, by exit or by stop, `core.runs.reap` finds every process
of this user that matches any of three marks:

1. in the turn's process group;
2. `VALOR_TURN=<turn id>` in its environment (read with `ps -E`);
3. running under a sandbox that denies the mach name `valor.turn.<turn id>`
   and not `valor.turn.none` (an App Sandbox denies every such name, so the
   second check keeps unrelated apps out). The check is libsandbox's
   `sandbox_check`, without logging.

Each gets SIGTERM, then SIGKILL after two seconds, and a `turn.reaped` row
lists pid, command, and signal. The sandbox mark is the one a daemon cannot
shed: it survives `setsid`, re-parenting to launchd, and a process retitling
itself over its environment, which redis-server does; platform binaries hide
their environment from other processes altogether. With the sandbox mark
disabled, the redis test's daemon survives. `tests/test_reap.py` reaps a
double-forked `setsid` daemon and a redis-server daemonized inside the replay
sandbox, and leaves a bystander alive.

The replay judge's verification commands run under the same profile with
their own mark, and are reaped the same way.

## The workspace

Serves Mission item 1 (delivering within authority) and the constraint
**bounded authority**: a turn works on a real clone and every push is an
`act` effect Tom releases.

`scripts/replay_workspace.py` builds a workspace for any repository and base
commit; `scripts/demo_workspace.sh` built the demonstration's. Each produces:

| Part | What it is |
|---|---|
| the clone | the repository at the base commit with no later history, tags, or remotes besides `origin`; reflog expired and garbage collected; on a work branch; `.valor/` excluded from git |
| `origin.git/` | a local bare repository, the clone's only remote, `main` at the base, every ref update logged, non-fast-forward pushes refused. The sandbox denies the turn writes to it. Only the broker's `push_branch` performer writes it, from the kernel's process, after Tom approves and releases the push |
| `home/` | the turn's git config (Valor's identity, no credential helper), an empty gh config, the sandbox profile, and `harness.json` for `core start --harness-config` |
| services | what the run's tests need, below |

Nothing reaches GitHub. A push to GitHub is a different performer behind the
same broker and the same `act` class.

The clone itself has no PR, issue, or later commit to read, and a leak check
greps the base tree for the feature before the first turn
(rebuild-demonstration.md, Setup). That check is the emulator's
(`docs/emulator.md`); the workspace is what makes it possible.

### Per-run services

A run declares the services its tests need (`postgres`, `redis`, or none).
Services run outside the sandbox, so each is configured so a turn cannot use
it as a way out:

- **Postgres.** One cluster for workspaces, separate from the kernel's, on
  `127.0.0.1:5439` with scram-sha-256 auth on every login. The superuser
  password is random and discarded once the `test` role exists, because a
  superuser login to a server outside the sandbox would be an escape. Each
  run gets its own database owned by `test`. The turn's environment points
  the app at it (`TEST_DB_*`, `DATABASE_URL`, `PG*`). This separation came
  from the demonstration's first incident: the machine cluster trusted every
  loopback connection, so a turn could have written ledger rows as the
  kernel's role or a superuser (rebuild-demonstration.md, Kernel findings 1;
  commit 923e16e7f). The kernel database's role separation is
  `docs/data.md`'s.
- **Redis.** One redis-server per run that asks for it, on its own port
  (6391 to 6399), bound to 127.0.0.1, no persistence, no unix socket, and
  the protected configs (`dir`, `dbfilename`), `DEBUG`, and `MODULE` closed,
  since those would let a turn write files through a server outside the
  sandbox.
  Postgres being the only database in Valor's own design, Redis here is a
  dependency of the repository under test (popoto), never of the kernel.

Tearing a run down drops its database and deletes its directory. **Gap**:
it leaves the run's redis-server running; stopping it belongs in teardown.

## Testing actual use: a browser

Serves Mission item 1 ("testing actual use"). A capability for the turn,
never a gate.

**Gap.** No run used a browser. Valor never looked at the demonstration's
banner in a browser in any round and said so each time
(rebuild-demonstration.md, What was delivered), and no baseline run looked at
a page, including the two UI items (rebuild-baseline.md, Browser use). The
workspace has what the app needs to run (its own database, dev ports 8000 to
8009 open for binding), and nothing to look at it with.

The design adds a headless browser the turn drives against the dev server on
a dev port, with screenshots written into the run directory and named in
`done.md`. Unmeasured: whether a headless Chromium runs under the turn's
sandbox-exec profile (it brings a sandbox of its own), and its RAM on the
16 GB machine, which `docs/machine.md` has to budget. The emulator can score
it on the UI items (#894, #872, #893) once it exists.

## Further harnesses: Codex and Pi

A second harness conforms to the same port. Its wrapper builds argv, env,
and cwd from the base URL, Brief, and turn id; parses a result carrying
`text`, `is_error`, and a session id; resumes by that id; runs under the same
sandbox profile with the same `VALOR_TURN` mark; and honors the signal
channel, which needs nothing harness-specific beyond writing files.

**Gap.** The gateway speaks the Anthropic Messages wire format. A harness
whose provider speaks another format needs a gateway route that meters that
format before its wrapper can exist, since a turn that bypasses the gateway
spends outside the budget. The first use is the review seat: an Opus-class
model from another vendor reviewing through its own harness. Nothing about
Codex or Pi has been run here.

## Skill rendering

Each wrapper will turn a versioned skill into what its harness loads. The
skill system is deferred until Tom's requirements are gathered
(`skills/README.md`). Until then a workspace turn loads no skills from this
machine (safe mode). A repository's own `CLAUDE.md` and `.claude/` stay in
the clone as files the turn can read, as they did in the baseline
(rebuild-baseline.md, Setup).
