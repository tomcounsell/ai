---
tracking: none
slug: m3-pi-harness
type: build
status: merged
critique_rounds: 1
review_rounds: 1
---

# 3b: Pi, the harness contract suite, and the reviewer seat

Task 3b of milestone 3 of [valor-rebuild.md](valor-rebuild.md). It adds
Pi as the second harness behind the port `TurnCommand`, proves both
harnesses keep the same contract in one test suite, records the harness
and its version on every `turn.started`, gives the blind verifier a Pi
seat on GPT-6.1, measures compaction once on each harness, and settles
whether corrections reach Claude Code's subagents.

Pi spends only through the gateway's OpenAI route, task 3a
([m3-openai-route.md](m3-openai-route.md)). 3b builds against 3a's
interface (`<gateway>/t/<token>/openai/v1`) and its offline tests run on
a local replaying upstream, so the two build in parallel. Its live Done
items run after 3a merges and the key is in place. It merges after 1.5.

## Stakes

`critique_rounds: 1`, `review_rounds: 1`, as valor-rebuild.md sets for
milestone 3 outside the OpenAI route. The task changes the composition
root and adds one field to `turn.started`. It adds no effect, no spend
path, and no credential: Pi's calls go through 3a's route, and the turn
profile it runs under is the one Claude Code runs under, with Pi's state
added to the denied places.

## The Done items it closes

From milestone 3:

- **A contract suite both harnesses pass.** `tests/test_harness_contract.py`
  runs one set of cases, parametrized over `claude_code` and `pi`, each
  with the real binary under the real turn profile against a local
  scripted upstream: resume carries context, signals are collected, a
  stop mid-call kills and reaps the turn's whole process group, and every
  model call is metered through the gateway. Evidence: the suite's run
  output with both parameters passing, in the build report.
- **Pi runs on GPT-6.1, metered.** A workspace turn with `--harness pi`
  reaches GPT-6.1 through the gateway's OpenAI route, and every call has
  a `gateway.opened` row and a charge. Evidence: one live turn
  (`VALOR_LIVE=1`) whose `turn.ended` shows Pi's own reported cost beside
  the ledger's charge for the same turn.
- **The harness and its version on `turn.started`.** Every
  `turn.started` row carries `harness` (as it does) and `harness_version`.
  Evidence: a test reading the row for each harness.
- **Pi with GPT-6.1 is a blind verifier seat.** A seat `reviewer_openai`
  resolves to Pi and GPT-6.1, and a fresh session at that seat runs in a
  blind checkout with the same inputs and verdict file as the Opus seat.
  Evidence: the seat's argv and profile in a test, and one live fresh
  critique at that seat on a recorded candidate, its verdict read by
  `workspace.read_verdict`.
- **Compaction measured once on a long session.** Evidence: one live
  long session per harness whose `turn.ended` holds the compaction the
  harness reported (tokens before, the trigger) and the turn's total
  charge; the numbers go into `docs/harnesses.md`.
- **Corrections reaching subagents, verified or a recorded gap.**
  Evidence: a contract case in which the scripted upstream asks Claude
  Code to start a subagent, and the subagent's request body, as the
  upstream received it, either holds the correction text or does not. The
  result is written into `docs/harnesses.md` and `docs/architecture.md`
  as what holds.

Two Done items of milestone 3 wait on work outside this task:

- **One review on each harness of the same candidate, both recorded.**
  The review runner is 1.4c's ([m1-4-checks.md](m1-4-checks.md), 1.4c
  outline). 3b makes the seat; the build session runs the pair of reviews
  once 1.4c has merged, as a rollout step of whichever task merges second.
- **Pi carries popoto #633 to a held merge.** It needs the whole pipeline
  and 1.5's emulator. 3b makes `--harness pi` work for every working
  stage; the build session runs #633 on Pi after 1.5 merges.

## Threat model

- A Pi turn controls everything a Claude Code turn controls: its
  workspace, its commands, its requests to the gateway. It runs under
  the same turn profile, marked `valor.turn.<id>`, stopped and reaped the
  same way.
- Pi reads configuration from places a candidate or the machine user
  controls: `~/.pi/agent/` (the machine user's auth, settings, extensions,
  and skills), and a project's `.pi/settings.json`, `.pi/extensions/`,
  `.pi/skills/`, `.pi/prompts/`, `.pi/SYSTEM.md`, and `.pi/APPEND_SYSTEM.md`
  inside the clone, and `AGENTS.md` and `CLAUDE.md`. A candidate could
  plant an extension that runs at Pi's start, settings that disable
  compaction or point a provider elsewhere, or a skill that rewrites the
  system prompt.
- What holds: the turn's Pi agent directory is its own
  (`PI_CODING_AGENT_DIR`), written by the kernel before the turn with one
  provider, the gateway; `~/.pi` is denied to every workspace profile, so
  the machine user's `auth.json` is unreadable; Pi starts with
  `--no-extensions --no-skills --no-prompt-templates --no-themes`, so
  nothing under the clone's `.pi/` loads code or prompts. Every turn also
  passes `--system-prompt` (the kernel's own, so `.pi/SYSTEM.md` is not
  read) and `--no-context-files` (`AGENTS.md` and `CLAUDE.md` are not
  read). A project's `.pi/settings.json` is still read by Pi; the build
  settles from Pi's source which keys can matter. A blind checkout leaves
  `.pi/` out of the tree for every harness, and a working clone is never
  edited. The result is in `docs/pi.md`.
- The kernel never reads a directory the turn owns to learn the harness's
  version: it reads the installed package's own files, which every turn
  profile denies writing.

## Design

### The Pi wrapper (`harnesses/pi.py`)

`workspace_turn(prompt, cwd, resume, model, harness, ...)` returns a
builder `build(base_url, brief, turn_id) -> TurnCommand`, matching
`claude_code.workspace_turn`. It raises `Unsandboxed` without
`harness["sandbox_profile"]`, as Claude Code's does.

- **Binary.** `settings.pi`, default `/opt/homebrew/bin/pi`, at the
  version pinned in `harnesses/pi.py` (`PINNED`), a release that has
  `--system-prompt` and `--no-context-files`. Pi lives in Homebrew's
  prefix, which a turn's profile denies writing but the machine user can
  write, so the kernel runs it only inside the sandbox, as it runs
  `claude`.
- **Agent directory.** `harness["pi_agent_dir"]`, the turn's own
  (`work_state/pi` for a working session, `<check_dir>/pi` for a fresh
  one), made by provisioning. Before each turn the builder writes
  `models.json` there: one provider `valor`, `api: "openai-responses"`,
  `baseUrl: "<base_url>/openai/v1"`, `apiKey` the placeholder
  `TURN_TOKEN` (the gateway sets the real key), and one model entry for
  the resolved model with `maxTokens` from `harness["max_output_tokens"]`,
  `contextWindow` from the price table's `context_window`, and `cost` copied from
  `spending.openai_prices`. Pi's own reported cost is an approximation of
  the price; the gateway's meter is the record. It writes `settings.json` with compaction on at Pi's
  defaults and retries at Pi's defaults. The files are written through
  descriptors that follow no link, as `.valor` inputs are.
- **Environment.** The allowlist `KEEP_ENV` that Claude Code's wrapper
  keeps, plus `PI_CODING_AGENT_DIR`, `PI_OFFLINE=1` (no update or package
  check at start), `TMPDIR`, and the git and gh settings Claude Code's
  wrapper sets.
- **Argv.** `/usr/bin/sandbox-exec -D GATEWAY_PORT=.. -D VALOR_TURN=..
  -f <profile> <pi> --no-extensions --no-skills --no-prompt-templates
  --no-themes --no-context-files --offline --provider valor --model
  <model> --mode json -p --session-dir <agent dir>/sessions [--session
  <id>] --system-prompt <the kernel's> --append-system-prompt <the
  Brief>`, with the prompt on stdin (`TurnCommand.stdin`), so a prompt
  starting with `-` or `@` is never read as an option or a file. Pi's default
  tools are read, bash, edit, and write; it has no web tools.
- **Resume.** By session id. Pi looks the id up in its own session
  directory, inside the sandbox, so the kernel never opens a file the turn
  wrote. An id Pi cannot find ends the turn with an error, which `parse`
  reports as `is_error`, never a prompt waiting on stdin (stdin is closed).
- **`parse(stdout)`.** The JSONL stream: the session id from the first
  line (`{"type": "session", ...}`); `text` from the last assistant
  message; `is_error` from `agent_end` and any error event; `num_turns`
  from `turn_end` events; `harness_reported_usd` summed from the
  assistant messages' usage cost; `compaction` from `compaction_start` and
  `compaction_end` (reason, tokens before, tokens after); `session_id`. It
  returns the keys Claude Code's `parse` returns, so `session.next_prompt`
  and the fold read both alike.

### The harness version (`harnesses/*.py`, `core/runs.py`)

- `TurnCommand` gains `harness_version: str | None`. `run_turn` writes it
  on `turn.started` beside `harness`.
- Each wrapper reads its version once per kernel process from the
  install's own files, never by running the binary outside the sandbox:
  Pi from the `package.json` beside the resolved `pi` script; Claude Code
  from the versioned install directory the resolved `claude` path names.
  A version the wrapper cannot read is recorded as None, and the turn runs.
- A Pi whose version differs from `PINNED` runs and records what it is.
  The pin is what the contract suite ran on; the row says what ran.

### Choosing the harness (`core/settings.py`, `core/tasks.py`, `core/__main__.py`)

- `settings.SEATS` maps a seat to a `(harness, model)` pair:
  `frontier`, `reviewer`, and `light` keep Claude Code and their models;
  `reviewer_openai` is `("pi", "gpt-6.1-sol")`. `resolve_model` keeps its
  signature and returns the model; `resolve_seat(seat)` returns both.
- `python -m core start --harness {claude_code,pi}` (default
  `claude_code`) records the harness name in its own Brief field,
  `harness_name`.
- `HARNESSES = {"claude_code": claude_code, "pi": pi}` in the composition
  root. `_turn_for` picks the wrapper from the Brief's harness name;
  `_fresh_for` from the seat's harness.
- `fresh.SEATS` keeps `review: "reviewer"`. The Pi seat is chosen per
  call: `fresh.critique_runner` takes a `seat` and passes the harness
  name into `_fresh_for`. 1.4c's review runner (branch `m1-4c-verifier`)
  needs the same; whichever of the two merges second carries it.
- Provisioning (`core/workspace.py`) makes `work_state/pi` and the fresh
  session's `pi` directory, and adds `.pi` to `HOME_DENIED`.

### The contract suite (`tests/test_harness_contract.py`)

One module, every case parametrized over both harnesses, each with the
real binary under a real turn profile, a real gateway, real Postgres, and
a local scripted upstream: `tests/scripted_upstream.py`, one HTTP server
that speaks Anthropic Messages SSE and OpenAI Responses SSE, plays a
script of replies per call (text, a tool call, a long sleep), and records
every request body it received. Scripted replies are built from the
recorded fixtures' event shapes, so the harnesses parse what the
providers send.

Cases:

- **A turn runs and ends.** One scripted reply; `turn.started` holds
  `harness` and `harness_version`; `turn.ended` holds `ok`, the text, the
  session id.
- **Resume carries context.** Turn one is told a word; turn two resumes
  by session id; the upstream's second request body holds turn one's
  messages.
- **Signals are collected.** The scripted upstream asks for a bash tool
  call that writes a `.valor` signal file; the kernel collects it on
  `turn.collected` as for any turn.
- **Stop mid-call and reap.** The upstream sleeps inside a streamed
  reply; the task is stopped by NOTIFY; the harness's process group and a
  `sleep` child it spawned are gone; `turn.reaped` and `turn.ended` are
  written; the in-flight call is charged its worst case.
- **Every call is metered.** For a turn of three scripted calls, the
  upstream's request count equals the number of `gateway.opened` rows,
  and each has a charge. No request reaches the upstream except through
  the gateway (the upstream listens on a port the profile does not
  allow).
- **A stray credential is never used.** The machine user's `~/.pi` and
  `~/.claude` are denied; a fake `~/.pi/agent/auth.json` in a scratch home
  is not read (the upstream sees only the placeholder key the gateway
  replaces).
- **A candidate's harness configuration does not load.** A clone holding
  `.pi/extensions/x.ts` that writes a file at load, and `.claude/settings.json`
  with a hook that writes a file: neither file appears.
- **An unknown session id fails cleanly.** `is_error`, no hang, no prompt.
- **Corrections reach the session.** A recorded correction's text is in
  the system prompt of every request of the main session.
- **Corrections and subagents (Claude Code).** The upstream scripts a
  Task tool call; the subagent's requests are recorded and the case
  asserts what they hold. If the correction is absent, the case is
  written to assert that absence, with the gap named in the docs, so the
  suite states what is true either way. Pi starts no subagents; its
  parameter of this case is skipped with that reason.

The suite costs nothing and runs in the default run. If the real
`claude` refuses to start against a scripted upstream (a login check or a
call to a host other than its base URL), the build reports which, and the
Claude Code parameter runs the same cases under `VALOR_LIVE=1` against
the real gateway instead, with its spend marked.

### Compaction measured (live, once per harness)

`tests/test_compaction_live.py`, `VALOR_LIVE=1`. A turn is given a task
that reads a large generated file in pieces until the harness compacts.
Pi reports `compaction_start` and `compaction_end` in its JSON stream;
Claude Code reports a compaction boundary in its transcript, which the
wrapper reads into `compaction` only if its JSON result carries it,
otherwise the case records the token counts of the requests before and
after the drop seen at the gateway. `turn.ended` records the compaction;
the build report gives tokens before, tokens after, the charge of the
compaction call, and the turn's total charge per harness.

## Tech debt absorbed

- `claude_code.turn()` stays: tests and the docs still name it as the one
  tool-less unsandboxed turn.
- Corrections reaching subagents leaves the gap lists of
  `docs/architecture.md`, `docs/harnesses.md`, `docs/persona.md`, and
  `docs/data.md`, replaced by the contract case's result.

## Left out

- Codex and every other harness.
- Per-harness skill rendering; skills stay off on both harnesses.
- Pi on Anthropic models through the Anthropic route. Pi speaks
  `anthropic-messages`, and adding it is one more provider in the
  turn's `models.json`; nothing needs it yet.
- Updating Pi's pin on a schedule. The pin moves when a task moves it,
  with the contract suite run on the new version.

## Tests

The contract suite above, and:

- `harnesses/pi.py` argv: every isolation flag present; the prompt on stdin, including one that starts with a dash or `@`, run
  against the real binary; `--session` present only
  on resume; `Unsandboxed` without a profile.
- `models.json`: the base URL is the gateway's, the key is the
  placeholder, the cost matches `OPENAI_PRICES`; a link planted at the
  file's path is not followed.
- `parse`: a recorded Pi JSONL stream for a plain reply, a tool call, an
  error, an aborted stream (no `agent_end`), and one with a compaction;
  each gives the expected dict; a stream with no session line gives
  `session_id` None and `is_error`.
- `harness_version`: the package's version for Pi; Claude Code's from its
  install path; None for a binary in an unrecognized layout.
- `reviewer_openai` resolves to Pi and GPT-6.1.
- `.pi` in `HOME_DENIED`, read by a sandboxed `cat` of a scratch
  `~/.pi/agent/auth.json` failing.
- Live (`VALOR_LIVE=1`): one Pi working turn on GPT-6.1 through the real
  OpenAI route, `pytest.mark.spend(usd=0.10)`; one fresh critique at
  `reviewer_openai` on a recorded candidate, `pytest.mark.spend(usd=0.50)`;
  the compaction cases, `pytest.mark.spend(usd=3.00)` each.

The existing suite runs and passes, Claude Code's turns unchanged.

## Files it changes

- `harnesses/pi.py` (new wrapper), `harnesses/claude_code.py` (version), `harnesses/README.md`.
- `core/runs.py` (`harness_version` on `TurnCommand` and `turn.started`).
- `core/settings.py` (`SEATS` pairs, `resolve_seat`, `settings.pi`).
- `core/__main__.py` (`HARNESSES`, `_turn_for`, `_fresh_for`,
  `start --harness`).
- `core/fresh.py` (the seat passed through to `_fresh_for`).
- `core/tasks.py` (the harness name in the Brief's settings).
- `core/workspace.py` (`.pi` in `HOME_DENIED`; the Pi directories made at
  provisioning and for a fresh session).
- `tests/test_harness_contract.py`, `tests/scripted_upstream.py`,
  `tests/test_pi.py`, `tests/test_compaction_live.py`,
  `tests/fixtures/pi/` (recorded JSONL streams).
- `docs/harnesses.md`, `docs/architecture.md`, `docs/persona.md`,
  `docs/data.md`, `docs/machine.md` (Pi's install and pin).

3a changes `core/settings.py` and `core/__main__.py`; 3c changes
`core/workspace.py` and `docs/harnesses.md`. Whichever merges later
rebases.

## Expected spend, as information

The contract suite and unit tests spend nothing. The live cases above
come to about ten dollars, most of it the two long compaction sessions.

## Rollout

1. After 3a merges and `python -m core openai-key` reports the key in
   place: install Pi at `PINNED` (`npm install -g
   @mariozechner/pi-coding-agent@<PINNED>`), the build session's step.
2. Run the live cases; report Pi's turn charge beside its own reported
   cost, the critique verdict at `reviewer_openai`, and the compaction
   numbers per harness.
3. After 1.4c merges: one review at `reviewer` and one at
   `reviewer_openai` on the same candidate, both recorded, reported side
   by side.
4. After 1.5 merges: popoto #633 started with `--harness pi --model
   gpt-6.1-sol`, carried to a held merge, its ledger summarized in the report.

## Questions for Tom

- **The Pi seat adds to the reviewer, it does not replace it.** Assumed:
  Opus at `reviewer` stays the default blind verifier; `reviewer_openai`
  is a second seat, run once for the recorded comparison and whenever a
  task asks for it.

## Decided by default

- **Pi version.** The newest release on the build day is pinned, since
  the installed 0.66.1 is several releases behind. The contract suite is
  the evidence the pin works.
- **The turn's own Pi directory, written by the kernel each turn.** Pi
  reads providers and keys from its agent directory; a directory the
  kernel writes per turn is the only one whose provider is known to be
  the gateway.
- **Resume by id inside the sandbox.** The kernel holds the id from the
  previous `turn.ended`, and never reads the session file.
- **The version from installed files, not from running the binary.** The
  kernel runs no harness outside the sandbox.
- **The subagent case asserts what is true.** A failing assertion would
  leave the suite red over a fact about Claude Code; the docs carry the
  gap if there is one.

## Build record

The build record, patch rounds, merge, and the records of Rollout 3 and 4
are in [m3-pi-harness-record.md](m3-pi-harness-record.md).
