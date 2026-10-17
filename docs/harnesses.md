# Harnesses

A harness is the agent program that does a turn's work: Claude Code, Pi and Codex. It sits in the
agents tier of the three (README, "Three tiers"): the kernel decides what a turn may do and pays for
it, and the harness does the work inside those bounds. This doc covers the port every harness
conforms to, the Claude Code wrapper, how a task's turns share one session, how a turn talks back to
the kernel, the sandbox a turn runs under, what happens to the processes it leaves behind, and the
workspace it works in (workspace.md).

`harnesses/` holds the wrappers. `core/runs.py` holds the port and runs one
turn; `core/session.py` runs a task's turns; `core/signals.py` holds the
signal channel. The gateway's pricing, opening, and charging belong to
`docs/architecture.md`, as do the task loop's states, the broker,
stop, and the execution record. This doc covers the harness's side of each.

## The harness port

Serves the constraint **bounded authority, metered spending**: the kernel runs any
harness without knowing which one, so authority never moves into a wrapper.

The port is one value, `TurnCommand`, built fresh for each turn:

| Field | What it carries |
|---|---|
| `argv` | the command line that runs one turn |
| `env` | the turn's whole environment |
| `cwd` | the workspace the turn works in |
| `harness` | the harness's name, recorded in `turn.started` |
| `harness_version` | the installed release, read from its files and recorded in `turn.started` |
| `stdin` | the prompt, for a harness that takes it on standard input; none otherwise |
| `tmpdir` | the turn's `TMPDIR`, when its harness settings name one: the kernel empties it before the turn starts and removes it once the turn ends, however it ended (`core/runs.py`) |
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
under the work dir, and node aborts at
startup when its stdout or stderr is a file at a path it cannot read. It then
waits for whichever comes first: the
process exits, or a stop notification for the task arrives from Postgres. On
a stop it revokes the gateway, which cuts every in-flight model call, sends
SIGKILL to the whole process group, waits for every call to be charged, and
writes `turn.ended` with outcome `stopped`. Nothing inside a turn is trusted
to honor a gentler signal, and nothing the turn owns lives only in the
process, so the stop loses nothing. The off switch is the kernel's, never the
model's incentive [8, 9, 10]. `tests/test_serve.py` stops a task mid-turn.

`turn.ended` carries the outcome (`done`, `failed`, `stopped`), the return
code, the parsed result, the paths of the turn's whole stdout and stderr
(`<work dir>/<task>/turns/<turn>.stdout` and `.stderr`, which no turn can
write), and the metered total for the turn, the numeric sum of the charges
on its calls. A failed turn's status line names its stderr file.

The turn's `TMPDIR` (`TurnCommand.tmpdir`) is emptied before the process
starts and removed once the turn slot is released, whatever the outcome. A
later turn of the session resumes from its harness's config directory, never
from `TMPDIR`, so nothing in it is kept. A failed removal leaves the turn's
result as it is; the next turn's start clears what is left.

At most `settings.max_turns` harness turns run at once on the machine (default 2), each holding one
turn slot. The target is a 16 GB MacBook Air, where Postgres, the bridges, and two `claude -p`
turns share the RAM. The RAM a turn takes is `docs/machine.md`'s to state.

## The Claude Code wrapper

`harnesses/claude_code.py` builds `workspace_turn`, one turn of a task that works in a directory.
It runs as a developer's Claude Code would there: the project's CLAUDE.md, settings, hooks, skills,
MCP servers and web tools all load (WebFetch and WebSearch run inside the web agent alone; see
Skill rendering), and so do the plugin directories in `settings.plugin_dirs`: Valor's own,
`skills/valor` in the kernel's checkout, always first, then `VALOR_PLUGIN_DIRS` (colon-separated;
by default the yudame and yudame-dev plugins of the yudame marketplace Claude Code keeps under
`~/.claude/plugins/marketplaces/`), each passed as `--plugin-dir` when it exists. The operator's own user-scope MCP servers (`mcpServers` in
`settings.claude_json`, `VALOR_CLAUDE_JSON`, default `~/.claude.json`; the logged-in browser
among them) are passed as `--mcp-config`, since the turn's own config directory holds none.

It defaults to the light seat's pinned model id
(`claude-haiku-5-5`), and a Brief to the frontier seat's, never an alias: the installed CLI
resolves an alias, and may resolve it to a model the gateway has no price
for, which the gateway charges at a stand-in price marked `unpriced`.

Its arguments:

| Argument | What it does | Serves |
|---|---|---|
| `-p --output-format json` | one non-interactive turn; one JSON result on stdout | the port's `parse` |
| `--permission-mode bypassPermissions` | edits files and runs commands without asking | Mission item 6: nobody is there to answer a prompt, and the kernel bounds the turn |
| `--plugin-dir DIR`, once per existing `settings.plugin_dirs` entry | loads Valor's plugin, then the yudame plugins | developer parity (C18); the web guard |
| `--settings '{"disableAllHooks": false}'` | keeps Valor's plugin hooks on: command-line settings outrank the project's and any the turn writes, which Claude Code reloads mid-turn | the web guard (Skill rendering) |
| `--system-prompt-snapshot off` with `--append-system-prompt` | keeps Claude Code's own system prompt and tools, and appends the dispatched text (persona first; a chat session's Brief with its `Room:` line), re-rendered every turn | correctable: a correction recorded mid-task reaches the next turn |
| `--session-id ID` or `--resume SESSION` | a new session under an id the kernel chose, or the task's session continued | Mission item 1 (see below); the kernel knows which transcript is the turn's |
| `--` before the prompt | the prompt is never read as an option | Mission item 1: a request starting "- Create new flag" failed a baseline run before this (rebuild-baseline.md, Infrastructure 3) |

The demonstration checked the system-prompt row: every `turn.started` row carried correction 1 in
the Brief and in the `--append-system-prompt` argument that reached `claude -p`
(rebuild-demonstration.md, Correction 1 rendering).

**The environment** is the operator's, less the kernel's own secrets
(`claude_code.DROP_PREFIXES` and `DROP_NAMES`: Anthropic and Claude Code variables, the kernel's
provider and GitHub keys, the libpq password variables), plus the workspace's own variables from the
task's harness settings. So the turn has Valor's `gh` login, git credential helper and SSH agent.
The wrapper then sets:

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
- `GIT_TERMINAL_PROMPT=0`.
- `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`: `claude_code.SPAWN_DEPTH`, so subagents start subagents
  past Claude Code's own cap of 3 (Skill rendering).
- `VALOR_SUBAGENT_START`: the SubagentStart hook output carrying the dispatched text, which Valor's
  plugin gives every subagent but the web agent (Corrections reaching subagents, below).
- `VALOR_COMPACT`, `CLAUDE_CODE_AUTO_COMPACT_WINDOW` and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE`: for a
  chat session's turn only (`chat_channel` in the harness), its compaction instructions, which the
  PreCompact hook of Valor's plugin prints, and the window that makes Claude Code compact only
  past the forced compaction (`core.settings.compaction`, 650,000 on a 1M window); the two
  window variables only for a model `WINDOWS` lists (Compaction, below).
- `TMPDIR` and `CLAUDE_CODE_TMPDIR` from the task's
  `tmpdir` (the workspace's `tmp/` for the working session, a check's
  `checks/<name>/tmp/` for a fresh one; emptied at each turn's start and
  removed at its end, so nothing in it outlives the turn); from its `claude_config_dir`, `CLAUDE_CONFIG_DIR` (its own Claude
  Code state, no login) and a placeholder `CLAUDE_CODE_OAUTH_TOKEN`, which wins over a Keychain
  login (checked 2026-10-15 on Claude Code 2.1.300: every call reached a logging base URL carrying
  the placeholder).
- `VALOR_TURN`, set by the kernel: the turn's id, which the reaper uses.

**The task's harness settings** come from provisioning (`python -m core
start --project`) or from `--harness-config FILE`, and are stored on the
task: `sandbox_profile`, `tmpdir`,
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
critique as a fresh session (`core/fresh.py`): a worktree of the base's
and the plan's trees as two kernel commits from the mirror, inputs under
`.valor/inputs/`, its own profile, `TMPDIR`, and Claude Code config, the
verdict channel (`skills/sdlc/verdict.md`) in its Brief, and its verdict
as its final message. Docs runs the
same way in its own clone (`fresh.docs_runner`). Review
(`fresh.review_runner`) runs in a worktree of the candidate with fresh Postgres and
Redis.
The reviewer runs the candidate's code, and a process that code leaves
running can rewrite any file in the checkout until the turn is reaped, so
each stage's verdict is the session's final message, read from the turn's
result on the harness's stdout, which no process the session starts holds
(`fresh.final_verdict`).
Its setup runs under the turn profile.
Review and docs are registered in the kernel's `runners()`.

Each prompt is followed by what did not count from the previous turn (`errors` on its
`turn.collected`) and what became of the effects it requested, read from the ledger now, so a push
performed since reads as done. An answer or feedback is spent only by a turn that finishes:
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
When it nears the model's context limit (a chat session, earlier: below)
it is compacted in place, by Claude Code's own compaction, and resumed; it is never swapped for a fresh
session seeded with a summary. The demonstration and the baseline ran on
resume throughout (rebuild-baseline.md, Aggregate). Measured live:
Claude Code's calls fell from 947,155
input tokens to 75,066 at its compaction; Pi's, in `docs/pi.md`.

**A chat session controls its own compaction** (Tom, 2026-10-17: "you
should only get auto-compacted against your will until after you've had
many reminders and passed that 600k"). The summary is shaped the way
Hermes Agent's is (Tom, earlier the same day: "let's go with Hermes'
simpler solution"): positional, not by topic. Hermes
(`agent/context_compressor.py`) keeps the first 3 and at least the last
20 messages and writes the middle into one summary that quotes the
user's messages, newest first, within 24,000 characters. Every compaction
of a chat session (`harnesses/claude_code.py`, `chat_channel` in the
harness) is Claude Code's own, driven to that shape; no summarizer of
Valor's runs.

- *Reminders.* `core.settings.compaction`: on a 1M window, 150k, 250k,
  350k, 450k, 550k and 600k tokens, each scaled by the window over 1M
  below it. When the session's last turn passed one that the turn before
  had not (taken as 0 when the context shrank, as after a compaction),
  its next prompt says "Your conversation is about N tokens. You may
  compact it", so each is given once (`session.context_report`). A turn's
  size is its last complete, sent `gateway.charged` call on the session's
  model: input, cache read, cache creation and output. A model `WINDOWS`
  does not list gets no reminders.
- *When the session asks.* A chat turn that leaves `.valor/compact.md` and
  neither stopped nor moved the task is followed, in the same run, by a
  turn resuming its session with `/compact` and the file's text as extra
  instructions (Claude Code's own command, which runs in `-p`). Its
  `turn.started` says `compaction: true`: the fold moves the session but
  neither `steer_since` nor `entry_finished`, so messages that arrived
  meanwhile reach the next turn; it is never collected, and the kernel's
  restart (`serve.recollect`) passes it over, so the chat turn's report
  stays the last one. A compaction that does not finish is told to the
  session in its next prompt; the chat goes on. Measured (2026-10-17, a
  scratch session with the plugin and a Telegram `VALOR_COMPACT`, on
  Claude Code 2.1.302): `-p --resume <id> -- /compact` wrote a manual
  compact boundary (30,563 tokens to 1,382), and the summary had the
  three parts of `compact.md`, both open asks and their message ids.
- *Forced.* Past 650,000 tokens (`FORCE`, scaled like the reminders).
  Claude Code 2.1.302 compacts at 80% of `CLAUDE_CODE_AUTO_COMPACT_WINDOW`
  less the 20,000 tokens it keeps for output; the 80% is a fraction its
  server can lower (`tengu_amber_rokovoko`), and
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=80` keeps it from rising. So the turn
  sets the window to 832,500, and Claude Code's auto-compaction never
  fires earlier. A model `WINDOWS` does not list keeps Claude Code's
  default. Measured on the merged threshold (2026-10-17, a scratch
  session at a 60,000 target): every one of five compactions fired with
  62,724 to 67,691 tokens in context, the target plus the prompt that
  crossed it. Earlier (146317905) a chat session compacted at half its
  window within 150k to 600k.
- *What is kept.* `skills/sdlc/compact.md`, rendered per channel into
  `VALOR_COMPACT`, which the PreCompact hook of Valor's plugin prints and
  Claude Code takes as the compaction's custom instructions. The summary
  keeps who is who, open asks, tasks in flight and promises
  (`skills/sdlc/converse.md`), quotes the human messages verbatim within
  24,000 characters, and ends with the recent tail, all within about
  12,000 tokens; an email thread's also carries its opening forward, while
  Telegram, Slack and the local chat keep theirs only as any message.
  Claude Code itself also keeps its last few messages verbatim after the
  summary. Old tool output needs no clearing: the whole middle is replaced.
  Measured on the same scratch session: two open asks and a task id from
  before the first compaction survived nine compactions, the summary
  stayed under 17,000 characters, and an email thread's opening was quoted
  after five.
- *Older than the summary.* A compacted message is still in the chat
  (`valor-telegram read`, `valor-slack read`) and in the ledger, so no
  store of compacted turns is kept. Task sessions keep Claude Code's
  default, and Pi and Codex run no chat session.

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
| `.valor/advice.md` | clarify, plan, build, or patch: a question for a second opinion, with its evidence | counts only from an `idle` turn with no other stage signal beside it; the advisor (`fresh.advise`), a fresh session on the other vendor's seat, answers in its final message, recorded as `advice.given`, and the next turn opens with the answer, quoted. The working session decides |
| `.valor/plan.json` | plan: the plan file's path, stakes, both loop counts, scope additions | read from the committed file at HEAD; `plan.written` with its commit and digest. A plan is an error, with no `plan.written`, if: the file lacks `path` or `critique_rounds` or `review_rounds`; `path` is not a string; `critique_rounds` or `review_rounds` is not an integer (booleans are not accepted), or is out of range (each must be 0, 1, or 2; a `review_rounds` of 2 is recorded and read as 1); `stakes` (if present) is not a string; `scope` (if present) is not a list; the workspace is not a git repository, the path cannot be passed to git, git itself fails while reading the repository (the error is git's own text), or the plan is not committed at HEAD or is changed in the working tree (git is asked about that path alone, matched literally, through a fresh index read from HEAD and made in the kernel's `output/` directory, which no task profile reaches, so no index bit, cached stat, or `core.fileMode` the turn set hides a change; git's content filters, which the turn can set (`core.autocrlf`, the `text`, `eol`, `ident` and `working-tree-encoding` attributes), can still make differing bytes compare equal, which changes nothing recorded, since the digest is the committed blob's). |
| `.valor/done.md` | build or patch: a candidate, what it is and how it was verified | with a clean tree (checked the same way, through a fresh index, with the same filter caveat; the candidate is the commit), the head commit and the turn are the candidate, and the checks run; uncommitted changes, or git config the kernel refuses (`core/git.py`), are an error and no candidate. `task.delivered` waits for the checks (`docs/sdlc-state-machine.md`) |
| `.valor/stop.md` | clarify, plan, build, or patch: a note to Tom that the work is stopped, or, opening `Done:`, finished with nothing to merge | with no other stage signal beside it, the verdict `ended` and `task.ended` (`kind: decided`); Tom's report leads with "This is done; nothing needed merging." for a `Done:` note |
| `.valor/compact.md` | a chat session's turn: a request to compact the conversation, its text added to the compaction's instructions | the turn is followed, in the same run, by a `/compact` turn in its session (`turn.started` `compaction: true`, never collected); from any other turn an error, ignored (Compaction, above) |
| `.valor/effects/<name>.json` | one request `{"action_type", "target", "payload"}` | each goes to the broker, which performs or refuses; a `merge` request, one that is not JSON Python can parse, one holding a surrogate code point outside an escaped pair (not text), or one the `turn.collected` row cannot hold (below), is recorded with an error and never reaches it; a send whose text fields are not strings is refused for its shape |

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
the largest part left (an error, an effect entry, the question, the
no-question statement, the plan, the delivery note, or the screens) is
dropped and `turn.collected` written again alone, dropping the next largest
each time, until it is stored. A dropped effect entry keeps its
`effect_id`, `kind` and `file` with an error holding Postgres's reason, so
the effect rows the broker wrote still match it; a dropped error is
replaced by kernel text with the reason; any other dropped part is null (the screens, an empty list).
A reduced row's verdict is `idle` (or `failed` for a turn that did not
finish), its candidate is null, no `question.asked` or `plan.written` is
written beside it, and one error names the parts dropped and gives the
reason. If no reduction is stored,
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
pushing any other way is unavailable. The text inside a signal file grants nothing. A question is shown to Tom; an effect request is classified and bounded by the broker against the task's ceiling. The turn's own words never decide what it may do [7].

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
Memory takes its text entries as records labelled as the turn's, never as
Tom's (`docs/memory.md`).

When a turn with its own config directory ends, stopped or not, the
kernel copies the session file and its subagents' files into the store as
`transcript` documents (`core/transcripts.py`, `docs/data.md`), each
file's SHA-256 on `turn.ended`, so an edit is detectable and the copy
outlives Claude Code's housekeeping. It follows no link, skips a file not
regular, sparse, or with more links than one; a failed copy says `no_transcript`.
A Codex turn's session file is its rollout (below), copied the same way.

## Metering through the gateway

Serves the constraint **bounded authority, metered spending**: every model call a
turn makes is metered and its spending recorded on the task.

The wrapper points the harness at the gateway with `ANTHROPIC_BASE_URL`, carrying a per-turn token
in the path. Claude Code sends every call through that base URL: the main loop, its own side calls,
and any subagent it starts inside the turn. The demonstration's 69 model calls all passed the
gateway, and the gateway and the harness agreed on the total (rebuild-demonstration.md, Money). The
gateway forwards any path on the route byte for byte, and meters `POST v1/messages`. A turn's own Claude Code config directory
holds no login, so it carries a placeholder (`CLAUDE_CODE_OAUTH_TOKEN`), and the gateway sets the
kernel's credential on every call: a long-lived token in the
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
  every run so far; nothing enforces it, since a turn reaches
  the public internet.
- **Corrections reaching subagents.** The Brief, corrections included, is in
  the system prompt of the main session. A subagent Claude Code starts
  inside a turn (`cc_is_subagent=true` on its requests) gets Claude Code's
  own subagent system prompt and the prompt the parent wrote, and, at every
  depth, the Brief as its opening context: the wrapper puts the
  SubagentStart hook output in `VALOR_SUBAGENT_START`
  (`claude_code.subagent_start`), and Valor's plugin's SubagentStart hook
  (`skills/valor/hooks/subagent_start.py`) prints it for every subagent but
  the web agent, which reads untrusted pages and gets only its prompt.
  Claude Code keeps a hook output of more than about 10,000 characters in a
  file and gives the subagent a preview and the file's path, so a long
  Brief arrives that way, read with the subagent's Read tool. Pi starts no
  subagents.

## The turn sandbox

Serves the constraint **a ledger the system cannot edit**: the kernel's ledger, key directory, and
backup stay out of a turn's reach.

A workspace turn runs under one sandbox-exec profile (`turn.sb`) written for its workspace by
`core/workspace.py`, as the Mac's one macOS user. The profile starts from `(allow default)` and denies
four things:

- the kernel key directory (`pg_passfile`'s), the effect lock and output files directory
  (`performing_dir`), the machine cluster's data directory (`pg_data_dir`), and the backup disk
  (`backup_dir`), read and write, as written and with symlinks resolved;
- renaming any directory above those paths;
- the machine cluster on loopback: port 5432 and its unix socket;
- the mach name `valor.turn.<VALOR_TURN>`, which marks every process of the turn for the reaper.

Everything else on the Mac is open to a turn: the public internet, loopback, its own task's services,
dev servers on any port, and the operator's files. The one incident behind the four denies is the
demonstration's Postgres trust hole (rebuild-demonstration.md, Kernel findings 1). The services of a
task run under the same profile marked `valor.service.<task id>` ([workspace.md](workspace.md)).

## Reaping what a turn leaves

Serves the constraint **reliable stop, recovery, and correction**: a turn's
processes end with the turn, and the ledger says which were stopped.

When a turn ends, by exit or by stop, `core.runs.reap` finds every process
of this user that matches any of three marks:

1. in the turn's process group;
2. `VALOR_TURN=<turn id>` as an entry of its initial environment;
3. running under a sandbox that denies the mach name `valor.turn.<turn id>`
   and not `valor.turn.none` (an App Sandbox denies every such name, so the
   second check keeps unrelated apps out). The check is libsandbox's
   `sandbox_check`, without logging.

The process table is read in-process, not with `/bin/ps`: that is setuid, and a sandboxed caller, such as
the suite under the verify check, may not run it. On macOS libproc gives each pid's owner and group and
`sysctl` `KERN_PROCARGS2` its arguments and environment; in the verification VM the same come from
`/proc/<pid>/stat`, the directory's owner, `cmdline`, and `environ`. A process that has exited, a zombie not yet collected included,
reads as gone.

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
(`services.reaped`).

## The workspace

A task's workspace, its provisioning, the kernel mirror, and its per-task services are in
[workspace.md](workspace.md).


## Testing actual use: a browser

The workspace has `look`, a script that screenshots a page served on a dev port
and dumps its DOM into `.valor/screens/`. The kernel records those screens when
it collects the turn. [browser.md](browser.md) specifies it.

## Reading a chat's history

The workspace also has `valor-telegram read` and `valor-slack read`
(`tools/valor_read.py`), which read or search a chat through its running
bridge ([bridges/telegram.md](bridges/telegram.md), Reading history for a
turn). The kernel installs every workspace tool into the shared `bin/`
before each turn (`workspace.install_tools`), so a session provisioned
before a tool changed has the current one, and sets the turn's
`VALOR_CHAT` (the session's chat, else the project's first) and the read
ports.

## Pi, Codex, and further harnesses

Pi is the second harness: the same port, sandbox profile and `VALOR_TURN`
mark (`docs/pi.md`). It loads the project's `AGENTS.md`, `CLAUDE.md` and
skills and the yudame plugins' skills, and the operator's MCP servers from
the `mcp.json` it writes in the turn's Pi directory.

Codex is the third (`harnesses/codex.py`, ported from the old system's
adapter; `--harness codex`, with an OpenAI model such as `--model gpt-6.1-sol`).
It runs `settings.codex` (`VALOR_CODEX`, default `~/.local/bin/codex`, the
standalone installer's link; `harness_version` is read from the release
directory it resolves to) as `codex exec --json
--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -m <model>
[resume <thread>] -`, under the same profile and mark, the prompt on stdin.
Its home is the turn's own (`codex_home`: `state/work/codex`, or
`checks/<name>/codex`), holding no login, where the kernel writes
`config.toml` each turn:

- **Provider.** One, `valor`, on the gateway's Responses route, keyed by
  the placeholder (`env_key`), with `supports_websockets = false` and
  `model_context_window` from the OpenAI price table less the maximum
  output, as Pi's. Codex has no output cap to set. Measured against a
  recording stub (codex-cli 0.153.4, 2026-10-16): every call, compaction
  included, is `POST <gateway>/openai/v1/responses` carrying the
  placeholder, so the gateway meters all of it unchanged. Its hosted tool is
  `web_search`, which the price table prices.
- **Context and skills.** `project_doc_fallback_filenames = ["CLAUDE.md"]`,
  so a project with no `AGENTS.md` gives its `CLAUDE.md`. Each yudame
  plugin's `skills` directory is linked into `<codex_home>/skills`, which
  Codex searches beside the project's `.agents/skills` and
  `~/.agents/skills`; the stub run listed all 31 yudame skills.
- **MCP.** The operator's user MCP servers (`claude_code.user_mcp_servers`)
  as `mcp_servers`: a command, its arguments, environment and directory, or
  a URL and its headers.
- **The Brief.** A new session gets it as `developer_instructions`, after
  Codex's own instructions. A resumed session keeps the developer message
  it started with and reads no new one, so a resumed turn's re-rendered
  Brief opens the prompt on stdin.
- **Session and transcript.** Codex names the thread; `parse` reads it from
  `thread.started`, and a resumed turn names it to `resume`. The rollout,
  `<codex_home>/sessions/<y>/<m>/<d>/rollout-<time>-<thread>.jsonl`, takes
  every turn of the thread, and is copied as the turn's transcript. `parse`
  reads the last `agent_message` as the text; no `turn.completed`, or a
  `turn.failed` or `error` event, is an error. Codex reports no cost.

The gateway meters two formats: Anthropic's Messages API, and OpenAI's
Responses API at `<gateway>/t/<token>/openai/v1` (architecture.md, Metered
spending). A harness on another format needs a route that meters it first.

## Skill rendering

Skills come from three sources (`skills/README.md`): Valor's own plugin,
`skills/valor` in the kernel's checkout, always first in
`settings.plugin_dirs`; the yudame plugins after it; and the project's own
skills. A workspace turn loads the plugins and the
project's skills (The Claude Code wrapper; Pi and Codex under "Pi, Codex,
and further harnesses"). The stage instructions are plain files in
`skills/sdlc/`, one per stage, which the kernel renders into the Brief as
text, so they need nothing harness-specific.

**The web agent** ([plans/orchestration.md](plans/orchestration.md)).
WebFetch and WebSearch run only inside `valor:web`, the leaf agent in Valor's
plugin (`skills/valor/agents/web.md`) whose tools are those two alone. The
plugin's PreToolUse hook (`skills/valor/hooks/web_only.py`) denies them to
any other caller, naming the agent, so an agent that needs a search or a
page starts it and gets findings back as data, never a page. The hook fails
closed: it runs `/usr/bin/python3 -I`, a call it cannot read is denied, and
any failure exits 2, which Claude Code treats as a block; `--settings` keeps
it on. The guard covers those two tools only: a command (`curl`, `gh api`)
and an MCP server's own fetch tool still reach the network. This is the guard
`web.agent_only`, granted by Tom on 2026-10-16 against injection through
retrieved content (#3648), ledgered by `core/guards.py` with its ninety-day
expiry. A session-wide `--disallowedTools` would remove the tools from the
web agent too, so the hook is the one mechanism. Subagents nest without
limit: the wrapper sets `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` to
`claude_code.SPAWN_DEPTH` (Claude Code's own cap is 3), and with background
tasks off a chain returns in order, so the web is always one subagent away.
A Codex turn keeps its built-in `web_search`, since Codex has no subagent
to hand it to; Pi has no web tool.

**Planned** ([plans/orchestration.md](plans/orchestration.md), wave 2):
workers carry the skills, not the persona. The persona's conversation turn reads
(the chat, memory, task status, the repository) and dispatches (starts,
steers, answers, stops work); it never edits, runs a skill, or uses the
web. A small thing goes to a Claude Code subagent inside its turn; anything
with a deliverable or a merge is a kernel task, which reports to the
persona and never to a comms channel. Until this is built, the working
session that speaks also does the work.
