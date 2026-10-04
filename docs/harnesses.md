# Harnesses

A harness is the agent program that does a turn's work: Claude Code and Pi. It sits in the
agents tier of the three (README, "Three tiers"): the kernel decides what a turn may do and pays for
it, and the harness does the work inside those bounds. This doc covers the port every harness
conforms to, the Claude Code wrapper, how a task's turns share one session, how a turn talks back to
the kernel, the sandbox a turn runs under, what happens to the processes it leaves behind, and the
workspace it works in (workspace.md).

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
| `harness_version` | the installed release, read from its files and recorded in `turn.started` |
| `stdin` | the prompt, for a harness that takes it on standard input; none otherwise |
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

A wrapper holds no effect class. Anything a turn does beyond its workspace reaches the world as a
request the broker decides (see the signal channel below).

## Running one turn

Serves the constraints **reliable stop** and **16 GB of RAM**.

`core.runs.run_turn` starts the command with stdin closed (or holding the
prompt, for a harness that reads it there), stdout and stderr on pipes, and
a new process group. The kernel copies both pipes at once, whole, into the
turn's two output files until EOF, which comes once the reap has ended every
process of the turn. The harness never holds the files themselves: they sit
under the work dir, which every turn profile denies, and node aborts at
startup when its stdout or stderr is a file at a path it cannot read. It then
waits for whichever comes first: the
process exits, or a stop notification for the task arrives from Postgres. On
a stop it revokes the gateway, which cuts every in-flight model call, sends
SIGKILL to the whole process group, waits for every call to be charged, and
writes `turn.ended` with outcome `stopped`. Nothing inside a turn is trusted
to honor a gentler signal, and nothing the turn owns lives only in the
process, so the stop loses nothing. The off switch is the kernel's, never the
model's incentive [8, 9, 10]. `tests/test_live_turn.py` runs one real turn
through the gateway and a real stop mid-stream.

`turn.ended` carries the outcome (`done`, `failed`, `stopped`), the return
code, the parsed result, the paths of the turn's whole stdout and stderr
(`<work dir>/<task>/turns/<turn>.stdout` and `.stderr`, which no turn can
write), and the metered total for the turn, the numeric sum of the charges
on its calls. A failed turn's status line names its stderr file.

One harness turn runs at a time on the machine. The baseline series ran three replays at once under
slot locks on a 64 GB machine (rebuild-baseline.md, Infrastructure); the target is a 16 GB MacBook
Air, where Postgres, the bridges, and one `claude -p` share the RAM. The RAM a turn takes is
`docs/machine.md`'s to state.

## The Claude Code wrapper

`harnesses/claude_code.py` builds two kinds of turn.

**`turn`** is one self-contained call: no tools by default, no session
persistence, safe mode and `--setting-sources ""` as below, a system prompt of the dispatched text (persona, then Brief).
The live tests use it. It copies the kernel's environment minus Claude Code's
own variables and every libpq (`PG*`) and `VALOR_PG*` variable. It runs under no sandbox profile, so it can read whatever Tom's user can, the
kernel's password file included; it must not be given tools without one.

**`workspace_turn`** is one turn of a task that works in a directory, and is
what a real task runs. Its arguments:

| Argument | What it does | Serves |
|---|---|---|
| `-p --output-format json` | one non-interactive turn; one JSON result on stdout | the port's `parse` |
| `--safe-mode` | no hooks, skills, plugins, or `CLAUDE.md` from this machine | bounded authority: the prompt layer of the machine holds no authority over a turn |
| `--setting-sources ""` | no settings file is read: not a `.claude/settings.json` or `settings.local.json` in the working directory, and not the `settings.json` of the turn's own config directory, which the turn and everything it runs can write and which Claude Code re-reads mid-turn; so nothing the turn runs can set the model URL, environment, model, permissions, or an `apiKeyHelper`. The kernel puts nothing in that file: the gateway supplies the credential and the environment is the kernel's | metered spending: every model call goes through the gateway; a candidate's code does not set what its reviewer runs with |
| `--strict-mcp-config` | no MCP servers | bounded authority; least privilege [11] |
| `--permission-mode bypassPermissions` | edits files and runs commands without asking | Mission item 6: nobody is there to answer a prompt, and the kernel bounds the turn |
| `--disallowedTools WebFetch WebSearch` | no web tools | independent checks: a replay cannot fetch its own answer (rebuild-baseline.md, Caveats); retrieved pages carry no instructions into the turn [7] |
| `--system-prompt-snapshot off` with `--append-system-prompt` | keeps Claude Code's own system prompt and tools, and appends the dispatched text (persona first), re-rendered every turn | correctable: a correction recorded mid-task reaches the next turn |
| `--session-id ID` or `--resume SESSION` | a new session under an id the kernel chose, or the task's session continued | Mission item 1 (see below); the kernel knows which transcript is the turn's |
| `--` before the prompt | the prompt is never read as an option | Mission item 1: a request starting "- Create new flag" failed a baseline run before this (rebuild-baseline.md, Infrastructure 3) |

The demonstration checked the system-prompt row: every `turn.started` row carried correction 1 in
the Brief and in the `--append-system-prompt` argument that reached `claude -p`
(rebuild-demonstration.md, Correction 1 rendering).

**The environment** is an allowlist [11]: `HOME`, `USER`, `LOGNAME`, `PATH`,
`SHELL`, `TMPDIR`, `LANG`, `LC_ALL`, `TERM`, plus the workspace's own
variables from the task's harness settings. Tokens, SSH and 1Password agent
sockets, and Claude Code's own session variables stay behind. The wrapper
then sets:

- `ANTHROPIC_BASE_URL`: the gateway URL issued for this turn.
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: set only when the task's harness
  settings name `max_output_tokens`; otherwise Claude Code's own per-model
  default applies. The gateway's worst-case estimate per call comes from
  the `max_tokens` of each request body, and is the charge only if the
  provider reports no usage.
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
`session_id` are kept. Unparseable output is `{"unparsed": true}`; the
output itself is whole in the turn's stdout file.

The persona is not a harness parameter. `core/tasks.py`, `dispatch`, renders
the one identity of `docs/persona.md` at the head of the dispatched text;
the harness passes that text through byte for byte as `turn.started` records it.

## A task's turns and session resume

Serves Mission item 1 (own outcomes across the whole job: one working context from inspection to
delivery) and Mission item 6 (Tom's answer lands in the context that asked, so he never restates the
task).

A task's first frontier turn opens a Claude Code session, the working session; every later clarify,
plan, build, and patch turn resumes it with `--resume`. `core.session.next_prompt` reads the session
id from the last `turn.ended` that carried one, and builds the prompt as data from the row that
moved the task into its state, under a short label. What the turn should do with it is the stage
file its Brief carries (`skills/sdlc/<state>.md`), never sentences in the kernel:

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
`.valor/verdict.json`, read by the same walk as the signals. Docs runs the
same way in its own clone (`fresh.docs_runner`). Review
(`fresh.review_runner`) runs in a set-up checkout with fresh Postgres and
Redis, the kernel's own suite and lint run (`verify.ran`) among its inputs.
The reviewer runs the candidate's code, and a process that code leaves
running can rewrite any file in the checkout until the turn is reaped, so
review's verdict is the session's final message, read from the turn's
result on the harness's stdout, which no process the session starts holds
(`fresh.final_verdict`).
Its setup runs under a profile that writes the checkout, its caches,
and its own `setup-tmp/`, never a `.git` in the checkout or the checkout
directory itself, and reads the services' password file, so the candidate's setup cannot write the session's Pi or
Claude Code directory, its `TMPDIR`, or the repository whose config and
commit subjects Claude Code reads at start.
Review is registered in the kernel's `runners()`; docs is registered once
governance's judgement passes its entry check.

Each prompt is followed by what did not count from the previous turn (`errors` on its
`turn.collected`) and what became of the effects it requested, read from the ledger now, so a push
Tom has since released reads as done. An answer or feedback is spent only by a turn that finishes:
after a failed or stopped turn the next one opens with it again. That rule exists because a failed
turn once consumed feedback 1 and the retry was prompted "Continue." (rebuild-demonstration.md,
Kernel findings 4).

Every turn gets the Brief re-rendered from the ledger regardless of resume, so the session's memory
of an older Brief never stands in for the current one.

Claude Code keeps the session as a JSONL file under `<config
dir>/projects/<workspace path with / and . as ->/`, where the config directory
is the turn's own (`CLAUDE_CONFIG_DIR`; for a kernel workspace,
`state/work/claude` for the working session, kept for the life of the task).
Resume is scoped to that directory: a session resumes only from the same
workspace path and config directory.

### What resume costs

Each resumed turn re-sends the whole session. In the demonstration the first call of the task
carried about 30,000 input tokens and the last about 109,000; cache reads totalled 4.5 million
tokens against 35,113 output tokens. Delivery 2 resumed over an hour after delivery 1 and wrote
77,602 cache tokens in 9 calls, against 25,613 in 20 calls for delivery 3, which followed promptly
(rebuild-demonstration.md, Money).

**Compaction, never replacement.** The working session is always resumed.
When it nears the model's context limit it is compacted in place, by
Claude Code's own compaction, and resumed; it is never swapped for a fresh
session seeded with a summary. The demonstration and the baseline ran on
resume throughout (rebuild-baseline.md, Aggregate). Measured live
(`tests/test_compaction_live.py`): Claude Code's calls fell from 947,155
input tokens to 75,066 at its compaction; Pi's, in `docs/pi.md`.

## The signal channel: `.valor/`

Serves Mission item 6 (every question and delivery is a ledger row, so the
attention a task costs is a fold over its ledger) and the constraint **bounded
authority** (an effect is a request the broker decides).

A turn reaches Tom and the world through files under `.valor/` in its
workspace, which the kernel reads when the turn ends:

| File | Meaning | What the kernel does |
|---|---|---|
| `.valor/question.md` | a question for Tom, in clarify, plan, build, or patch | `question.asked` naming the state the answer returns to; the task waits for `python -m core answer` |
| `.valor/no_question.md` | clarify: why no question would change the result, and the approach | the verdict `no_material_question`; the plan follows without Tom |
| `.valor/plan.json` | plan: the plan file's path, stakes, both loop counts, scope additions | read from the committed file at HEAD; `plan.written` with its commit and digest. A plan not committed, changed in the working tree (git is asked about that path alone, matched literally, through a fresh index read from HEAD and made in the kernel's `output/` directory, which no task profile reaches, so no index bit, cached stat, or `core.fileMode` the turn set hides a change; git's content filters, which the turn can set (`core.autocrlf`, the `text`, `eol`, `ident` and `working-tree-encoding` attributes), can still make differing bytes compare equal, which changes nothing recorded, since the digest is the committed blob's), or with counts outside 0 to 2, is an error and no plan |
| `.valor/done.md` | build or patch: a candidate, what it is and how it was verified | with a clean tree (checked the same way, through a fresh index, with the same filter caveat; the candidate is the commit), the head commit and the turn are the candidate, and the checks run; uncommitted changes, or git config the kernel refuses (`core/git.py`), are an error and no candidate. `task.delivered` waits for the checks (`docs/sdlc-state-machine.md`) |
| `.valor/effects/<name>.json` | one request `{"action_type", "target", "payload"}` | each goes to the broker, which performs, holds for Tom, or refuses; a `merge` request, one that is not JSON Python can parse, one holding a surrogate code point outside an escaped pair (not text), or one the `turn.collected` row cannot hold (below), is recorded with an error and never reaches it; a send whose text fields are not strings is refused for its shape |

`core.signals.collect` moves each to `.valor/handled/<turn_id>/` and reads
it there, in a worker thread, so no signal is read twice. For a turn that ended and
was never collected, `recollect`, in a worker thread as well, reads what is still in `.valor/` first
(moving it aside), then what `handled/<turn_id>/` holds; a file in both
places is read once, as the copy in `.valor/`, which replaces the filed one,
so what is read is what is kept. The turn controls
these files: the kernel follows no link, never blocks on a FIFO, and reads
only a regular file with one link and no holes (a sparse file claims a size
the turn never wrote), and only up to the size it checked, so a file that
grows afterward is not read past that. Anything else goes to `Signals.unreadable` with its reason (from the operating system's error; a
linked `.valor` reads "not a plain directory"), never its contents. An
entry that vanishes before it is read is recorded the same way, an effect
file with an error. An entry that cannot be moved is removed unread (`docs/architecture.md`). An effect file that is not a JSON
object with `action_type` and `target` is recorded with an error and no
request. Everything a turn left is one `turn.collected` row with the state it
ran in and its verdict. Postgres judges that row once, before the verdict
and the broker (`session._storable`): when jsonb refuses it (a NUL
character, a NaN or infinite number, nesting past the server's stack depth,
more than one jsonb value holds), each part is asked alone, nested as the
row nests it (each text signal, the plan, the screens, each request), and a
part refused alone is recorded as unreadable with Postgres's reason, never
its contents. If the row is still refused, the largest part left is
recorded so and the row asked again, until it is stored, so the parts that
fit together are kept; if none is left and it is still refused, every part
is, and the row keeps the request file names and what the kernel wrote. The row is judged again
where it is written: `turn.collected` and the row that goes with it
(`question.asked` or `plan.written`) are appended in one savepoint, and
when jsonb refuses them, each error and each effect entry it refuses alone
is replaced by kernel text with Postgres's reason; if that is refused too,
`turn.collected` holds nothing the turn wrote (no signals or effects, the
verdict `idle`, or `failed` for a turn that did not finish) and one error
with Postgres's reason. The broker's rows for each request are judged the
same way where they are written (`docs/data.md`). A verdict row (critique, review, docs) jsonb refuses is a refused verdict with Postgres's reason, and a fresh check's turn then ends `failed` with that reason as its result. A reason, key or error
the kernel writes names the field and what is wrong with it (its JSON type,
Postgres's reason, git's answer about a path), never the turn's value. A turn whose collection
still fails does not stop the kernel: `serve` logs it and collects the turn
in a job before the task's next step, parked between tries like any failed
job. No size or depth is the kernel's own. A question takes precedence over the other signals in
the same turn, and a signal that means nothing in the state (a `done.md`
during plan) is an error, not acted on. `.valor/` is in the clone's
`.git/info/exclude`, so signals never enter a commit. Files, because writing
one is a deliberate tool call that survives whatever prose follows it, and a
turn killed midway leaves what it wrote readable. The text of the channel is `skills/sdlc/channel.md`, carried in every
workspace turn's Brief; it lists the effects the task's own performers
offer (each performer's `usage` line; the merge offers none) and says
pushing any other way is unavailable, which the sandbox makes true. The text inside a signal file grants nothing. A question is shown to Tom; an effect request is classified and bounded by the broker against the task's ceiling. The turn's own words never decide what it may do [7].

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

When a turn with its own config directory ends, stopped or not, the
kernel copies the session file and its subagents' files into the store as
`transcript` documents (`core/transcripts.py`, `docs/data.md`), each
file's SHA-256 on `turn.ended`, so an edit is detectable and the copy
outlives Claude Code's housekeeping. It follows no link, skips a file not
regular, sparse, or with more links than one; a failed copy says `no_transcript`.

## Metering through the gateway

Serves the constraint **bounded authority, metered spending**: every model call a
turn makes is metered and its spending recorded on the task.

The wrapper points the harness at the gateway with `ANTHROPIC_BASE_URL`, carrying a per-turn token
in the path. Claude Code sends every call through that base URL: the main loop, its own side calls,
and any subagent it starts inside the turn. The demonstration's 69 model calls all passed the
gateway, and the gateway and the harness agreed on the total (rebuild-demonstration.md, Money). The
gateway checks each path as it arrived, undecoded, and forwards it byte for byte; a percent escape,
a backslash, or an empty, `.`, or `..` segment is a 400. A turn's own Claude Code config directory
holds no login, so it carries a placeholder (`CLAUDE_CODE_OAUTH_TOKEN`), and the gateway sets the
kernel's credential, only on `v1/messages`, its `count_tokens`, `v1/models`, and `v1/models/<id>`
(letters, digits, `.`, `_`, `-`), refusing any other path with a 403: a long-lived token in the
kernel key directory when one is there, else the machine's Claude Code login read from the Keychain
at most once a minute (the first 401 after a read allows one more, even when the client has already left). A login is used until its stated
expiry. The kernel never refreshes the login, and an expired one fails the call naming the remedy.
The harness never holds a credit or a limit, and a call refused because the task is stopped reaches
it as an API error.

The gateway takes each call's worst-case estimate from the `max_tokens` the
request body carries, whatever value Claude Code sends. The estimate is the
charge only when the provider reports no usage. A task that names
`max_output_tokens` in its harness settings sets Claude Code's output limit
(`CLAUDE_CODE_MAX_OUTPUT_TOKENS`); Valor sets none of its own.

**The gateway sets no connect or read timeout.** A call ends when the
provider answers or fails, or when no client is waiting for it: the turn
exited, was stopped, or dropped its connection. Such a call is cut and
charged by the usual rules (every allowed output token if it had started,
the worst case if not), and a call cut while it was still opening is not
sent and is charged 0. A call that could not reach the provider, or whose
connection ended before it answered, returns a 502 that says which and
names only the exception's type.

Two points the harness relies on and has to keep true:

- **Every call takes the base URL.** A harness that reaches a provider by
  another path would spend outside the meter. For Claude Code this held in
  every run so far; the sandbox does not enforce it, since a turn reaches
  the public internet (see the sandbox below).
- **Corrections reaching subagents.** The Brief, corrections included, is in
  the system prompt of the main session. A subagent Claude Code starts
  inside a turn (`cc_is_subagent=true` on its requests) gets Claude Code's
  own subagent system prompt and only the prompt the parent wrote, so the
  Brief and the corrections in it stop at the main session. The contract
  suite pins this (`test_corrections_do_not_reach_a_claude_code_subagent`).
  **Gap**: the plan says corrections reach every agent; for Claude Code
  subagents they do not. Pi starts no subagents.

## The turn sandbox

Serves the constraint **bounded authority, metered spending** (a turn cannot perform an effect
except through the broker), the constraint **a ledger the system cannot edit**, and the Evidence
item **independent checks** (a replay cannot read its answer). The stance is AI Control's: the
protocol holds even if the model tries to get around it [4].

A workspace turn runs under a sandbox-exec profile written for its workspace
by `core/workspace.py` (every task the kernel provisions, replays
included). The profile starts from `(allow default)` and denies what a turn
must not reach. It runs as Tom's user.

### Files

- **Denied, read and write:** `~/src` (Tom's checkouts, which hold the
  answers), `~/work-vault`, Desktop, Documents, Downloads, Dropbox, iCloud and
  cloud storage, Mail, Messages, `~/.ssh`, `~/.config/gh`, Claude Code's
  `~/.claude/projects` and `history.jsonl`, and the whole work directory
  (`work_dir`), other tasks included.
- **Allowed back, read and write:** for the working session (`turn.sb`), its
  clone, caches, and `state/work/` (its `TMPDIR` and Claude Code config),
  with `/private/tmp`, `/private/var/tmp`, and `/private/var/folders` denied,
  so no two tasks share a temp directory; for a fresh session, its own check
  directory, the same three and `~/.claude*` denied. Its `UV_CACHE_DIR` is
  `tmp/uv` and its `UV_PYTHON_INSTALL_DIR` is `tmp/python` in that directory,
  since `~/.cache/uv` and `~/.local/share/uv` are write-denied. The emulator's
  verification shares the temp directories and adds its tree, as its baseline did.
- **Read only:** the shared `bin/` (uv, and the kernel's `mktemp`, which hands
  macOS `mktemp` the turn's `TMPDIR` when it is given no directory and no
  template, reading its arguments as macOS `mktemp` does), first on the
  turn's `PATH`, then the trusted git's directory, so `git` and `python3` are not `/usr/bin`'s shims,
  which cache in the user temp directory; for the working session, its task's
  `home/` and bare origin. **Stat only:** the allowed directories'
  ancestors (not listable), so real paths resolve.
- **Denied entirely:** the kernel's own paths, each named by its setting: the
  kernel key directory (`pg_passfile`'s), the machine cluster's data directory
  (`pg_data_dir`), and the backup disk (`backup_dir`).
- **Above every denied path:** each directory above a path any rule denies,
  up to but not including `/`, is denied as itself (not its entries), as
  written and with symlinks resolved. Otherwise renaming `~/.config` would move the kernel key directory to a name no rule
  covers, where a turn could read it, and renaming `~/.local` would let a turn
  put its own `bin/claude` in its place; a disk image mounted over a denied
  directory would do the same. Entries inside those directories stay writable
  (`~/.config/<app>`, `~/Library/Caches/<x>`). A directory above a denied path
  that does not exist when the profile is written cannot be made by a turn.
  On this Mac each one exists except the backup disk's mount point while the
  disk is out, which only macOS makes, in root's `/Volumes`; nothing a turn
  needs lives under any of them.
- **No mount, no `open`:** a turn can make no mount call and cannot reach
  `diskarbitrationd`, so `hdiutil attach` and `diskutil mount` mount nothing,
  with or without a mount point. Otherwise a turn that unmounted the backup
  disk could mount its own image at `/Volumes/<label>` under the disk's label
  and catch the next dump. `open` would hand a disk image, or an app the turn
  wrote, to a process outside the sandbox; the profile denies the Launch
  Services database (`com.apple.lsd.*`), the quarantine resolver, and Apple
  events, without which `open` and AppleScript launch nothing.
  `launchservicesd` itself stays reachable, since Claude Code hangs without it.

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

The bind rules exist because of an incident: during the parallel baseline series, popoto's test
setup daemonized redis-servers that outlived their turns, one on `*:6379` open to the LAN beside the
production Redis, one on a socket in `/tmp`, and one on `*:6390` (commit 76b164458, "Keep a turn
from listening beyond its dev ports and reap what it leaves running").

### Processes and credentials

- git's keychain credential helper (`git-credential-osxkeychain`) cannot be
  executed. The turn's git config names Valor as author and has no
  credential helper; gh's config directory is empty, so gh is
  unauthenticated.
- The profile denies the mach name `valor.turn.<VALOR_TURN>`, which marks
  every process of the turn for the reaper.

### Known openings

The openings the sandbox leaves, and what the kernel runs outside it, are in
[sandbox-openings.md](sandbox-openings.md).

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

Each gets SIGTERM, then SIGKILL after two seconds, and a `turn.reaped` row lists pid, command, and
signal. The sandbox mark is the one a daemon cannot shed: it survives `setsid`, re-parenting to
launchd, and a process retitling itself over its environment, which redis-server does; platform
binaries hide their environment from other processes altogether. With the sandbox mark disabled, the
redis test's daemon survives. `tests/test_reap.py` reaps a double-forked `setsid` daemon and a
redis-server daemonized inside a task turn's sandbox, and leaves a bystander alive.

A task's services, marked `valor.service.<task id>`, stop when its run returns; under the
resident kernel they stay up between steps and stop when the task waits on Tom, reaches merge, or
is stopped. After a killed kernel, the next run of any task, if `workspace:ports` is free, stops those of tasks whose run is
not live and of row-less task directories whose provisioning (`provision:<id>`) is not live
(`services.reaped`). The replay judge's commands run under `turn.sb` with their own mark.

## The workspace

A task's workspace, its provisioning, the kernel mirror, and its per-task services are in
[workspace.md](workspace.md).


## Testing actual use: a browser

The workspace has `look`, a script that screenshots a page served on a dev port
and dumps its DOM into `.valor/screens/`. The kernel records those screens when
it collects the turn. [browser.md](browser.md) specifies it.

## Pi, and further harnesses

Pi is the second harness: the same port, sandbox profile and `VALOR_TURN`
mark (`docs/pi.md`). Every harness meets `tests/test_harness_contract.py`,
run against its real binary. Codex has not been run here.

The gateway meters two formats: Anthropic's Messages API, and OpenAI's
Responses API at `<gateway>/t/<token>/openai/v1` (architecture.md, Metered
spending). A harness on another format needs a route that meters it first.

## Skill rendering

Each wrapper will turn a versioned skill into what its harness loads. The
skill system is deferred until Tom's requirements are gathered
(`skills/README.md`). Until then a workspace turn loads no skills from this
machine (safe mode). The stage instructions are plain files in
`skills/sdlc/`, one per stage, which the kernel renders into the Brief as
text, so they need nothing harness-specific. A repository's own `CLAUDE.md`
and `.claude/` stay in the clone as files the turn can read, as they did in
the baseline (rebuild-baseline.md, Setup); Claude Code itself loads neither
(safe mode, `--setting-sources ""`).
