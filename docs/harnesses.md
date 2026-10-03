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
signal channel. The gateway's pricing, opening, and charging belong to
`docs/architecture.md`, as do the task loop's states, the broker, approvals,
stop, and the execution record. This doc covers the harness's side of each.

## The harness port

Serves the constraint **bounded authority, metered spending**: the kernel runs any
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
knows at that moment: the gateway base URL issued for this turn, the dispatched
text rendered as the turn starts (persona, Brief, corrections), and the
turn's id. The builder returns the `TurnCommand`. The kernel records the argv
and the text whole in `turn.started`, so the ledger shows exactly what the
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
persistence, a system prompt of the dispatched text (persona, then Brief).
The live tests use it. It copies the kernel's environment minus Claude Code's
own variables and every libpq (`PG*`) and `VALOR_PG*` variable. It runs under no sandbox profile, so it can read whatever Tom's user can, the
kernel's password file included; it must not be given tools without one.

**`workspace_turn`** is one turn of a task that works in a directory, and is
what a real task runs. Its arguments:

| Argument | What it does | Serves |
|---|---|---|
| `-p --output-format json` | one non-interactive turn; one JSON result on stdout | the port's `parse` |
| `--safe-mode` | no hooks, skills, plugins, or `CLAUDE.md` from this machine | bounded authority: the prompt layer of the machine holds no authority over a turn |
| `--strict-mcp-config` | no MCP servers | bounded authority; least privilege [11] |
| `--permission-mode bypassPermissions` | edits files and runs commands without asking | Mission item 6: nobody is there to answer a prompt, and the kernel bounds the turn |
| `--disallowedTools WebFetch WebSearch` | no web tools | independent checks: a replay cannot fetch its own answer (rebuild-baseline.md, Caveats); retrieved pages carry no instructions into the turn [7] |
| `--system-prompt-snapshot off` with `--append-system-prompt` | keeps Claude Code's own system prompt and tools, and appends the dispatched text (persona first), re-rendered every turn | correctable: a correction recorded mid-task reaches the next turn |
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
  default), which sets the gateway's worst-case estimate per call, the charge only if the provider reports no usage.
- `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`: a `-p` turn that ends kills
  what it left running in the background, so commands run in the foreground.
  Two of three turns of a baseline run ended idle waiting on killed
  background tests before this (rebuild-baseline.md, Caveats). Mission
  item 1: tests that ran are tests that finished.
- `GIT_TERMINAL_PROMPT=0`; `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_NOSYSTEM=1`
  when the task gives a git config; `GH_CONFIG_DIR` when it gives a gh
  config directory.
- `DISABLE_AUTOUPDATER=1`; `TMPDIR` and `CLAUDE_CODE_TMPDIR` from the task's
  `tmpdir`; from its `claude_config_dir`, `CLAUDE_CONFIG_DIR` (its own Claude
  Code state, no login) and a placeholder `CLAUDE_CODE_OAUTH_TOKEN`.
- `VALOR_TURN`, set by the kernel: the turn's id, which the reaper uses.

**The task's harness settings** come from provisioning (`python -m core
start --project`) or from `--harness-config FILE`, and are stored on the
task: `sandbox_profile`, `gitconfig`, `gh_config_dir`, `tmpdir`,
`claude_config_dir`, `env`, and `max_output_tokens`.
`sandbox_profile` is required: `workspace_turn` raises `Unsandboxed` for a
task without one, before anything is spawned, and `python -m
core run` reports it. The wrapper prefixes the argv with `sandbox-exec -D
GATEWAY_PORT=<port> -D VALOR_TURN=<turn id> -f <profile>`.

**The result** is Claude Code's JSON: `result` becomes `text`, and
`is_error`, `num_turns`, `total_cost_usd` (as `harness_reported_usd`), and
`session_id` are kept. Unparseable output is kept as its last 400 bytes.

The persona is not a harness parameter. `core/tasks.py`, `dispatch`, renders
the one identity of `docs/persona.md` at the head of the dispatched text;
the harness passes that text through byte for byte as `turn.started` records it.

## A task's turns and session resume

Serves Mission item 1 (own outcomes across the whole job: one working
context from inspection to delivery) and Mission item 6 (Tom's answer lands
in the context that asked, so he never restates the task).

A task's first frontier turn opens a Claude Code session, the working
session; every later clarify, plan, build, and patch turn resumes it
with `--resume`. `core.session.next_prompt` reads the session id from the
last `turn.ended` that carried one, and builds the prompt as data from the
row that moved the task into its state, under a short label. What the turn
should do with it is the stage file its Brief carries
(`skills/sdlc/<state>.md`), never sentences in the kernel:

| After | The turn's prompt |
|---|---|
| nothing (first turn) | the task's instruction |
| Tom's answer to a question | the answer |
| clarify found nothing to ask | its statement, as the plan stage opens |
| a critique's verdict | the verdict and its findings (a revision, or the build when no rounds are left) |
| findings from the checks (test failures, breadth gaps, review findings, docs findings, joined) | every finding together (a patch) |
| Tom's feedback on a delivery | the feedback (a patch) |
| a turn in the same state that finished | "Continue." |

**Patch is a resume.** A patch never starts a new agent: it is the session
that built, resumed with what came back (Tom, 2026-10-01). The session knows
why each line is there; a fresh agent would rebuild that context from the
diff at the cost of a full read and lose the decisions not written down.

**Fresh sessions for critique, review, and docs.** Each runs in a session
of its own that never resumes, and never reads, the working session. Their
inputs are files and ledger rows, so their independence is structural.
Review and docs start on every candidate, concurrently with the test run
when the machine's turn slots allow (`docs/machine.md`); the docs session
works in its own checkout of the candidate and commits only doc paths. Its
prompt is the request, the plan, and the diff, asking for
the docs the change made untrue. The reviewer runs the same Opus model or
an Opus-class model from another vendor through that vendor's harness; the
port below is what lets either run without the kernel knowing which.

The kernel's support today: the working session for every row above, and
critique as a fresh session (`core/fresh.py`): a blind checkout of the base's
and the plan's trees as two kernel commits from the mirror, inputs under
`.valor/inputs/`, its own profile, `TMPDIR`, and Claude Code config, the
verdict channel (`skills/sdlc/verdict.md`) in its Brief, and one verdict file,
`.valor/verdict.json`, read without following links or blocking. Review and
docs follow in milestone 1.4.

Each prompt is followed by what did not count from the previous turn
(`errors` on its `turn.collected`) and what became of the effects it
requested, read from the ledger now, so a push Tom has since released reads
as done. An answer or feedback is spent only by a turn that finishes: after
a failed or stopped turn the next one opens with it again. That rule exists
because a failed turn once consumed feedback 1 and the retry was prompted
"Continue." (rebuild-demonstration.md, Kernel findings 4).

Every turn gets the Brief re-rendered from the ledger regardless of resume,
so the session's memory of an older Brief never stands in for the current
one.

Claude Code keeps the session as a JSONL file under `<config
dir>/projects/<workspace path with / and . as ->/`, where the config directory
is the turn's own (`CLAUDE_CONFIG_DIR`; for a kernel workspace,
`state/work/claude` for the working session, kept for the life of the task).
Resume is scoped to that directory: a session resumes only from the same
workspace path and config directory.

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
attention a task costs is a fold over its ledger) and the constraint
**bounded authority** (an effect is a request the broker decides).

A turn reaches Tom and the world through files under `.valor/` in its
workspace, which the kernel reads when the turn ends:

| File | Meaning | What the kernel does |
|---|---|---|
| `.valor/question.md` | a question for Tom, in clarify, plan, build, or patch | `question.asked` naming the state the answer returns to; the task waits for `python -m core answer` |
| `.valor/no_question.md` | clarify: why no question would change the result, and the approach | the verdict `no_material_question`; the plan follows without Tom |
| `.valor/plan.json` | plan: the plan file's path, stakes, both loop counts, scope additions | read from the committed file at HEAD; `plan.written` with its commit and digest. A plan not committed, or counts outside 0 to 2, is an error and no plan |
| `.valor/done.md` | build or patch: a candidate, what it is and how it was verified | with a clean tree, the head commit and the turn are the candidate, and the checks run; uncommitted changes, or git config the kernel refuses (`core/git.py`), are an error and no candidate. `task.delivered` waits for the checks (`docs/sdlc-state-machine.md`) |
| `.valor/effects/<name>.json` | one request `{"action_type", "target", "payload"}` | each goes to the broker, which performs, holds for Tom, or refuses; a `merge` request is recorded with an error and never reaches it |

`core.signals.collect` reads them, then moves each to
`.valor/handled/<turn_id>/`, so no signal is read twice. An effect file that
is not a JSON object with `action_type` and `target` is recorded with an
error and no request. Everything a turn left is one `turn.collected` row
with the state it ran in and its verdict; a question takes precedence over
the other signals in the same turn, and a signal that means nothing in the
state (a `done.md` during plan) is an error, not acted on. `.valor/` is in
the clone's `.git/info/exclude`, so signals never enter a commit.

Files, because writing one is a deliberate tool call that survives whatever
prose follows it, and a turn killed midway leaves what it wrote readable.
The text of the channel is `skills/sdlc/channel.md`, carried in every
workspace turn's Brief; it lists the effects the registered performers
offer (each performer's `usage` line; the merge offers none) and says
pushing any other way is unavailable, which the sandbox makes true.

The text inside a signal file grants nothing. A question is shown to Tom; an
effect request is classified and bounded by the broker against the task's
ceiling. The turn's own words never decide what it may do [7].

Asking before building is decided outside the harness: the judgement step
that routes an underspecified request to a clarify turn is
`docs/judgement-layer.md`'s, and the `judge` state in
`docs/sdlc-state-machine.md` routes it. A clarify turn uses this same
channel: it inspects, then writes its questions, assumed answers, and
intended approach to `question.md`, or says in `no_question.md` that none
would change the result (`skills/sdlc/clarify.md`).

## Transcripts

Serves the Evidence item **independent checks**, and the constraint **docs
describe reality** by giving a verifier more than the turn's own summary.

Three records of a turn exist:

1. **The ledger.** `turn.started` (argv, Brief, its digest, corrections),
   the gateway's `gateway.opened`, `gateway.charged`, and `gateway.refused` rows for every
   model call, `turn.collected`, `turn.reaped`, and `turn.ended`. Written by
   the kernel; the turn cannot edit it.
2. **Claude Code's session file**, the JSONL under `projects/` in the
   turn's config directory: every message, tool call, and tool result. The
   baseline's leak scan read every tool call of every run from these files
   (rebuild-baseline.md, Caveats).
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

Serves the constraint **bounded authority, metered spending**: every model call a
turn makes is metered and its spending recorded on the task.

The wrapper points the harness at the gateway with `ANTHROPIC_BASE_URL`,
carrying a per-turn token in the path. Claude Code sends every call through
that base URL: the main loop, its own side calls, and any subagent it starts
inside the turn. The demonstration's 69 model calls all passed the gateway,
and the gateway and the harness agreed on the total (rebuild-demonstration.md,
Money). The gateway checks each path as it arrived, undecoded, and forwards it
byte for byte; a percent escape, a backslash, or an empty, `.`, or `..`
segment is a 400. A turn's own Claude Code config directory holds no login, so
it carries a placeholder (`CLAUDE_CODE_OAUTH_TOKEN`), and the gateway sets the
kernel's credential, only on `v1/messages`, its `count_tokens`, `v1/models`,
and `v1/models/<id>` (letters, digits, `.`, `_`, `-`), refusing any other path
with a 403: a long-lived token in the kernel key directory when one is there,
else the machine's Claude Code login read from the Keychain at most once a
minute (the first 401 after a read allows one more). The kernel never
refreshes the login, and an expired one fails the call naming the remedy. The
harness never holds a credit or a limit, and a call refused because the task is stopped reaches it as an API error.

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` bounds each call's output, and the gateway
takes each call's worst-case estimate from that bound. The estimate is the
charge only when the provider reports no usage, so a lower cap keeps that
fallback close to what a call really costs.

Two points the harness relies on and has to keep true:

- **Every call takes the base URL.** A harness that reaches a provider by
  another path would spend outside the meter. For Claude Code this held in
  every run so far; the sandbox does not enforce it, since a turn reaches
  the public internet (see the sandbox below).
- **Corrections reaching subagents.** The Brief, corrections included, is in
  the system prompt of the main session. Whether a subagent Claude Code
  starts inside a turn sees it was not checked
  (rebuild-demonstration.md, Correction 1 rendering). **Gap**: the plan says
  corrections reach every agent; for Claude Code subagents that is unverified.

## The turn sandbox

Serves the constraint **bounded authority, metered spending** (a turn cannot perform
an effect except through the broker), the constraint **a ledger the system
cannot edit**, and the Evidence item **independent checks** (a replay cannot
read its answer). The stance is AI Control's: the protocol holds even if the
model tries to get around it [4].

A workspace turn runs under a sandbox-exec profile written for its workspace
by `core/workspace.py` (every task the kernel provisions, replays included) or
`scripts/demo_workspace.sh` (the demonstration, whose profile allows back its
run directory and its one `~/.claude/projects/` directory in place of the
allow rules below). The profile starts from `(allow default)` and denies what
a turn must not reach. It runs as Tom's user.

### Files

- **Denied, read and write:** `~/src` (Tom's checkouts, which hold the
  answers), `~/work-vault`, Desktop, Documents, Downloads, Dropbox, iCloud and
  cloud storage, Mail, Messages, `~/.ssh`, `~/.config/gh`, Claude Code's
  `~/.claude/projects` and `history.jsonl`, and the whole work directory
  (`work_dir`), other tasks included.
- **Allowed back, read and write:** for the working session (`turn.sb`), its
  clone, caches, and `state/work/` (its `TMPDIR` and Claude Code config); for
  a fresh session, only its own check directory, with `/private/tmp`,
  `/private/var/tmp`, `/private/var/folders`, and `~/.claude*` denied.
- **Read only:** the shared `bin/` (uv), first on the turn's `PATH`; for the
  working session, its task's `home/` and bare origin. **Stat only:** the
  allowed directories' ancestors (not listable), so real paths resolve.
- **Denied entirely:** the kernel's own paths, each named by its setting: the
  kernel key directory (`pg_passfile`'s), the machine cluster's data directory
  (`pg_data_dir`), and the backup disk (`backup_dir`).

### Loopback connects, and rule order

A turn reaches the public internet, so package installs work. On loopback it
reaches only the gateway (at whatever port the OS gave it, passed in as
`GATEWAY_PORT`), its own task's service ports (none for critique), and dev
servers on 8000 to 8009. The machine's own Postgres (5432 and its socket, which hold the
kernel's ledger) and Redis (6379) are out of reach.

**Denies come first and allows last.** sandbox-exec refused allowed loopback
ports for roughly two in five port numbers whenever any network-outbound rule
followed the allow block. The demonstration's second turn failed with
"Unable to connect to API (EPERM)" because its gateway drew such a port; the
first turn's port happened to work (rebuild-demonstration.md, Kernel
findings 3). The profile opens with a deny of all of loopback and the
machine Postgres sockets, then allows the gateway, the task's services, and
the dev ports. `tests/test_demo_sandbox.py` expands both profiles and probes
them under the real sandbox-exec at 24 OS-assigned gateway ports.

### Binding

A turn may listen only on 8000 to 8009 and on unix sockets inside the
directories it may write.
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
  `security` is not fenced, though in a provisioned task Claude Code holds
  only a placeholder credential.
- The public internet is open, so a turn could reach a provider directly,
  outside the gateway, or reach GitHub anonymously. The baseline found no
  such call in any transcript (rebuild-baseline.md, Caveats).
- Both openings above are accepted (Tom, 2026-10-01): no separate macOS
  user, and the gateway is for visibility and honest metering, not a hard
  wall.
- A fresh session's blindness covers the paths the kernel names for the
  builder (Files, above). The working session has its own `TMPDIR` but can
  still write `/tmp`, which every task shares (common file names recurred
  across items in the baseline), and places such as `~/Library/Caches`,
  `~/.cache`, or `/Users/Shared`, which a fresh session can read, so a builder
  could leave a note there for a reviewer. Nothing reads one on purpose;
  independence rests on the inputs, the checkout, and the reviewer's own
  reruns.
- Narrowed for kernel workspaces, not closed: their profiles deny writes to
  the machine user's startup places, `~/.local/bin` (which the user's PATH
  puts before `/usr/bin`), `~/Library/LaunchAgents`, the shell's rc files
  (`~/.zshrc` and the like), Claude Code's install (`~/.local/share/claude`)
  and state (`~/.claude`, `~/.claude.json`), git's global config, and
  Homebrew's prefix, and every turn runs with `DISABLE_AUTOUPDATER=1`. The
  demonstration's profile denies none of these, and other places remain where
  a turn could leave a program a later unsandboxed process of the user runs,
  such as the caches under `/var/folders` that Apple's `/usr/bin` shims read
  (`/usr/bin/git` is the `xcrun` shim, which finds the real git through one).
  The kernel runs nothing from that reach outside the sandbox: its git is the
  Command Line Tools' install, its `ps` is `/bin/ps`, and the sandbox's own
  launcher is `/usr/bin/sandbox-exec`, each checked before it runs to be
  root's alone, file and every directory above it (`core/binaries.py`), with a
  PATH of system directories only. `claude` lives in the user's own
  directories and a turn can replace it; the kernel runs it only inside the
  sandbox, where a replaced one has the turn's own reach. The one exception is
  `claude_code.turn`, a tool-less turn with no workspace that runs unsandboxed
  and only in tests (the router never builds one). Homebrew's Postgres tools,
  which `python -m core backup` runs from launchd, sit in the user's own
  prefix too; a task's Postgres and Redis run only under `service.sb`.
- The demonstration's databases share one `test` role and password on
  5439, so a turn there could connect to another run's database. A task
  the kernel provisions has its own cluster, roles, and passwords, and its
  profiles reach only its own ports.
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
double-forked `setsid` daemon and a redis-server daemonized inside a task
turn's sandbox, and leaves a bystander alive.

A task's services, marked `valor.service.<task id>`, stop when its run
returns. After a killed kernel, the next run of any task, if `workspace:ports`
is free, stops those of tasks whose run is not live and of row-less task
directories whose provisioning (`provision:<id>`) is not live
(`services.reaped`). The replay judge's commands run under `turn.sb` with
their own mark.

## The workspace

Serves Mission item 1 (delivering within authority) and the constraint
**bounded authority**: a turn works on a real clone and every push is an
`act` effect Tom releases.

`core/workspace.py` provisions a task's workspace from a project spec
(`projects/`) under `work_dir/<task id>/` before the task starts, cloning from
a bare cache per repository URL (keyed by the URL's digest); a replay writes
its spec (`scripts/replay_workspace.py`).

| Part | What it is |
|---|---|
| `repo/` | the repository at the base commit with no later history, tags, or remotes besides `origin`; reflog expired and garbage collected; on a work branch; `.valor/` excluded from git |
| `origin.git/` | a local bare repository, the clone's only remote, the target branch at the base, every ref update logged, non-fast-forward pushes refused. No turn writes it; only the broker's `push_branch` and `merge`, from the kernel's process, after Tom releases the push |
| `kernel.git/` | the kernel mirror: seeded with the base, and fed each plan commit, candidate, and docs head (a docs session's kept head under `refs/valor/docs/`); the merge predicate and the merge read it. No turn reads or writes it |
| `home/` | git config (Valor's identity, no credential helper), an empty gh config, `pgpass`, and the profiles: `turn.sb`, each fresh session's, and `service.sb` |
| `cache/`, `state/work/`, `checks/` | the builder's package caches; the working session's `TMPDIR` and Claude Code config; each fresh session's checkout, `tmp/`, and `claude/`; each suite run's blind checkout and its own copy of the caches, cloned from `checks/seed/` (the base's setup output) |
| `pg/`, `redis/`, `ports.json` | the task's services, below, and their ports, recorded when chosen |

The setup commands run once in `repo/` under `turn.sb`; a failure is recorded
on the Brief (`project.setup_result`) and the task still starts. Nothing
reaches GitHub, and the clone has no PR, issue, or later commit to read.

**The mirror's fetch** treats the builder's clone as hostile: its config
checked first; a gitfile, `commondir`, alternates, and shallow clones refused;
the sending side inside the turn's sandbox; the receiving git with fsck, one
pack under a size limit, a footprint watchdog, and replace refs and grafts
off. A tree holding a top-level `.valor` (any case) is refused, since the
kernel makes `.valor` itself for a fresh session's inputs, written through
descriptors that follow no link and overwrite no file. A refused plan,
candidate, or docs head does not count.

### Per-task services

A project declares the services its tests need (`postgres`, `redis`, or none).
The binaries are Homebrew's, in a prefix a turn can write, so each runs under
`service.sb`: its own directory and port only, no outbound connection, marked
`valor.service.<task id>`. A port is the lowest free one that no unremoved
task and no task directory's `ports.json` names, chosen under the
`workspace:ports` lock. `python -m core workspace remove TASK` (stopped or
merged only) stops the services, deletes the directory, and frees the ports.

- **Postgres.** A cluster of the task's own on `127.0.0.1` only (no unix
  socket), port 5440 to 5599, scram-sha-256 on every login; the superuser
  password is random and removed once the roles exist. `app` owns database
  `app` and may create databases (and manage only the spec's extra roles); the
  passwords are in `home/pgpass`, and `PG*`, `DATABASE_URL`, and `TEST_DB_*`
  point the app at it. Separate clusters answer the demonstration's first
  incident, a machine cluster that trusted loopback (`docs/data.md`).
- **Redis.** One redis-server when the project asks, port 6400 to 6499, on
  127.0.0.1, no persistence, no unix socket, and the protected configs (`dir`,
  `dbfilename`), `DEBUG`, and `MODULE` closed; `REDIS_URL` points the app at
  it. A dependency of the repository under test, not the kernel.

**A check's services.** Each suite run of the test runner gets fresh
instances on the task's own ports (`workspace.check_services`): the task's
own are stopped first, the check's cluster gets the project's roles with new
passwords, its Redis starts empty, and on exit the check's instances are
stopped and removed and the task's own started again. No run sees what
another run, or the working session, wrote.

## Testing actual use: a browser

The workspace has `look`, a script that screenshots a page served on a dev port
and dumps its DOM into `.valor/screens/`. The kernel records those screens when
it collects the turn. [browser.md](browser.md) specifies it.

## Further harnesses: Codex and Pi

A second harness conforms to the same port; see
[harnesses-codex-pi.md](harnesses-codex-pi.md).

## Skill rendering

Each wrapper will turn a versioned skill into what its harness loads. The
skill system is deferred until Tom's requirements are gathered
(`skills/README.md`). Until then a workspace turn loads no skills from this
machine (safe mode). The stage instructions are plain files in
`skills/sdlc/`, one per stage, which the kernel renders into the Brief as
text, so they need nothing harness-specific. A repository's own `CLAUDE.md` and `.claude/` stay in
the clone as files the turn can read, as they did in the baseline
(rebuild-baseline.md, Setup).
