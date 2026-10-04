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
   gpt-6.1`, carried to a held merge, its ledger summarized in the report.

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

Built on branch `m3b-pi`, against 3a's interface (branch `m3-harnesses`,
not yet merged).

- **Pi.** Pinned at 0.73.1 (`PINNED`), selected with the `VALOR_PI`
  setting. The prompt goes on stdin; the system prompt and
  `--no-context-files` are always passed. The contract case plants
  `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, `AGENTS.md`, `CLAUDE.md`, and
  `.pi/settings.json` and asserts none reaches the model. A blind checkout
  leaves `.pi/` out of the tree for every harness (the diff and both
  commits keep it); a working clone is untouched.
- **Contract suite.** `tests/test_harness_contract.py`, both harnesses
  against the real binaries under real turn profiles, a real gateway, and
  `tests/scripted_upstream.py`. The Pi parameter skips unless the gateway
  has an OpenAI route (3a) and Pi is at `PINNED`; it passes with 3a's
  gateway overlaid.
- **Corrections and subagents.** A Claude Code subagent does not receive
  the correction: the case asserts that absence, and the docs name the
  gap. A fix is a kernel decision (disallow the Task tool, or put
  corrections in the subagent's prompt) and is not added.
- **Compaction.** In Pi's print mode the stream ends on `compaction_start`
  while the session still saves the compaction entry; `parse` records a
  start as unfinished and upgrades it on `compaction_end`. Measured live
  (`tests/test_compaction_live.py`): Pi compacted at 914,292 input tokens
  against a threshold of 905,616, ran on at 316,591, and the session cost
  $8.87 at the gateway against $2.63 Pi reported. Claude Code's calls fell
  from 947,155 to 75,066 at its compaction, in 38 calls for $17.27.
- **Context window.** Pi's `contextWindow` is the price table's
  `context_window` less its `max_output`, 922,000, from `pi.context_window`.
  The API refuses a request above that (901,587 input tokens accepted,
  950,000 refused), so the whole 1,050,000 would have Pi compact only
  after a failed call. No second constant.
- **Assumptions about 3a.** Model id `gpt-6.1-sol`; `openai_prices`
  returns tiers (`tiers.default.base`); `Gateway(openai_upstream=,
  openai_credential=)`; route `<gateway>/t/<token>/openai/v1`. Rebased
  onto 3a at 2861e2f5b, then again onto 3a's final head b1fbdffcb; they
  held. `pi.context_window` finds the model through `openai_prices`
  (exact or dated id), and a test covers a dated id and `-pro`.
- **Live runs.** One Pi turn on GPT-6.1 (Pi reported $0.0083, the gateway
  charged $0.0093), a critique at `reviewer_openai` on a recorded candidate
  (verdict `revise`, $0.0086), and the two compaction sessions above. Total
  live spend about $77, much of it failed attempts while the compaction
  sessions were being sized (a Claude Code reader that stopped at truncated
  output, a Pi session that quit early, two Pi attempts that overflowed the
  window): metered by the gateway, nothing else.
- **Not run.** Rollout steps 3 and 4 (the pair of reviews after 1.4c, popoto
  #633 after 1.5).

### Patch round 1

From the review (`changes`) and the test check (`gaps`):

- **Blind checkout.** The sparse pattern is `!/.pi`, so a committed `.pi`
  link is left out as well as a directory; a test commits `.pi` as a link
  to a directory.
- **Pi install.** `settings.pi` defaults to `/opt/homebrew/bin/pi`, not a
  PATH lookup. Every turn profile denies writing the directory above the
  first `node_modules` of the resolved `VALOR_PI` (`workspace.pi_install`),
  with a test. `docs/pi.md` says both. The machine's own Pi (0.66.1) is
  not upgraded here; that is rollout step 1.
- **Stop and reap.** The case runs a `sleep` the turn backgrounds, records
  its PID and process group before the stop, and asserts after it that the
  PID and every process of the group are gone.
- **Docs.** An unknown session exits 1 (`docs/pi.md`, `parse`). The Files
  list no longer names a moved `test_session.py` test.
- **Test gaps.** The `~/.pi` denial is tested under the workspace profile
  too; the metered case asserts the turn token header. `docs/pi.md` says
  how to run the Pi cases (`VALOR_PI`).
- **Evidence.** The Pi cases ran with `VALOR_PI` set to
  `~/.cache/valor-pi-0.73.1/node_modules/.bin/pi`: `tests/test_pi.py` and
  `tests/test_harness_contract.py` 62 passed, 1 skipped (Pi has no
  subagents).

## Checks after patch round 1, at 74431ce59 (review round 1 of 1)

- Test: `gaps`. With `VALOR_PI` at the 0.73.1 install, 693 passed and 14
  skipped; the default run skips about 20 Pi cases on the version
  mismatch. Real `sandbox-exec` refuses writes to the Pi install under
  both profiles. The `~/.pi` test passes with the denial removed: it runs
  `sandbox-exec` without `-D GATEWAY_PORT` and `-D VALOR_TURN`, so the
  profile fails to load before `cat` runs. Pass the `-D` flags as
  `tests/test_demo_sandbox.py` does.
- Review: `changes`; governance boolean no; no invented caps. `_node()`
  finds `node` with `shutil.which`, and `~/.bun/bin` and `~/.opencode/bin`
  come before `/opt/homebrew/bin` on the kernel's PATH; a turn can write
  both, so a planted `node` runs the `reviewer_openai` Pi session, which
  writes the verdict. Minor: `pi_install()` returns nothing when the Pi
  path has no `node_modules`, and does not cover a `VALOR_PI` that is a
  link outside the install.
- Docs: `updated`, 0c3d8a438 on `m3b-docs2`.

## Delivery: delivered, not passed

The review rounds are spent. The recommendation is one more patch:
`node` at a fixed path, as 1.4v does for `claude` and the Postgres
programs (`docs/plans/m1-4v-binary-paths.md`); `pi_install()` covering an
install with no `node_modules` and the resolved target of `VALOR_PI`; the
`~/.pi` test given its `-D` flags.

## Tom's feedback (2026-10-03)

Tom, on one more patch round for nine deliveries with the scopes and order put to him: "All as recommended". The order: 1.4v, 2.1, 1.4b, 1.4s, 2.2, 2.3, 3b, 1.4u, 1.5. Valor decides any further round and the merge (valor-rebuild-feedback.md, Tom's feedback of 2026-10-03).

Scope: the Delivery's recommendation (`node` at a fixed path, after 1.4v; `pi_install()` covering no `node_modules` and the resolved `VALOR_PI` target; the `~/.pi` test given its `-D` flags). Pi 0.73.1 is installed by the build session at rollout, as planned.

### Patch round 2

Rebased onto `valor-cori-rebuild` at ca620a91f with 3a's commits dropped
(3a is merged there under other SHAs); the conflicts in `core/runs.py`
(`harness_version` added to the merged `turn.started`), `docs/README.md`,
and `docs/architecture.md` were resolved onto the merged text.

- **`node` at a fixed path.** `settings.node` defaults to
  `/opt/homebrew/bin/node`, inside the prefix every turn profile denies
  writing; `VALOR_NODE` overrides it. The `shutil.which("node")` lookup is
  gone, so a `node` planted ahead on the kernel's PATH is never run. A
  missing `node` fails at exec. Test: the default holds with a fake `node`
  first on PATH, and `VALOR_NODE` wins.
- **`pi_install()`.** It returns the directory above the first
  `node_modules` of the resolved `VALOR_PI`, or the resolved target's own
  directory when there is none, plus the directory that holds `VALOR_PI`
  itself (where a link could be replaced). Tests: no `node_modules`, and a
  link whose target sits in another install.
- **The `~/.pi` test.** It runs `sandbox-exec` with `-D GATEWAY_PORT` and
  `-D VALOR_TURN`, reads a file in the work directory first to show the
  profile loads, then asserts the refusal is "Operation not permitted".
- **Docs.** `docs/pi.md` says all three; `docs/architecture.md` stays under
  600 lines.
- **Checks.** `tests/test_pi.py` and `tests/test_harness_contract.py` with
  Pi 0.73.1 installed: 65 passed, 1 skipped. Full suite: 767 passed, 17
  skipped. `ruff check` clean; `ruff format --check` flags only
  `docs/bridges/telegram.md` and one `docs/plans/m2-1-*.md`.

### Patch round 3

The lead's decision after the round 2 review and test: three fixes, each
reproduced first under the real turn profile (`kws.profile`, `sandbox-exec`)
and then covered by a test.

- **Ancestors of the install.** With the layout `docs/pi.md` names under a
  turn-writable parent, a turn renamed the parent away and put its own tree
  there, so `pi._entry()` pointed at the turn's `cli.js`. The profile now
  also denies writing, as `literal`, every ancestor of each denied
  directory and each symlink component of `VALOR_PI` and `VALOR_NODE`
  (`workspace.pi_install_held`). Creating entries beside them still works.
  The denies close a read of turn-owned state the threat model names; they
  are not a new check.
- **The link's directory.** `pi_install` takes it with `os.path.realpath`
  on the directory only, not on the link, because Seatbelt matches resolved
  paths. Test: a `VALOR_PI` spelled through a symlinked parent.
- **`VALOR_NODE`** stays as an override (the lead's decision). Its resolved
  interpreter goes through the same denials as Pi's install: its directory,
  the ancestors, the symlink components. `docs/pi.md` says so.
- **Tests** (`tests/test_pi.py`): ancestor rename refused with the docs
  layout, the symlinked parent, and a node outside Homebrew, each under
  `sandbox-exec`.
- **Checks.** Each new test fails on the round 2 code and passes now. Full
  suite with `VALOR_PI` set: 770 passed, 17 skipped. With it unset: 769
  passed, 17 skipped, and one "the task's Postgres did not start" port
  flake that passes alone. `ruff check` clean; `ruff format --check` flags
  only `docs/bridges/telegram.md` and one `docs/plans/m2-1-*.md`.

### Patch round 4

The lead's decision after the round 3 review: links reached through other
links were not held. Reproduced under the real turn profile with temp trees:
a two-hop chain whose middle link sits in a writable directory, and a
symlinked directory inside the link's target replaced by the turn.

- **Fix.** `workspace.pi_install_held` follows `VALOR_PI` and `VALOR_NODE`
  hop by hop (`_links_on_the_way`): each symlink among a path's components,
  then the components of its target, and so on. Every link found, by its
  spelling and by its resolved directory, goes in as a `literal` write deny
  with all of its ancestors, so the middle directory cannot be renamed
  either. No new check; the same deny read.
- **Tests** (`tests/test_pi.py`), each for `VALOR_PI` and `VALOR_NODE`
  under `sandbox-exec` on temp trees: the two-hop chain, and the symlinked
  directory inside the target. Each fails on the round 3 code.
- **Not changed.** The `~/.node_modules` note stays, as the lead said.

### Patch round 5

The round 4 review found `_links_on_the_way` resolved `..` by text while the
kernel follows a link first and goes up from where it points: a middle link
whose target is `sub/../inst/node_modules/p/cli.js`, with `sub` a symlink,
left `sub` unheld.

- **Fix.** The walk is one component at a time from `/`: `..` goes to the
  parent of the directory already reached, a link is recorded and its
  target's components go in front of the rest, and it stops after 32 hops
  (`MAXSYMLINKS`, macOS's limit, a protocol fact). No normpath or abspath.
  `_program_dirs` joins the working directory instead of `abspath`, so the
  operator's spelling is not normalized either.
- **Test** (`tests/test_pi.py`), for `VALOR_PI` and `VALOR_NODE` under
  `sandbox-exec`: the `sub/../inst/...` shape; replacing `sub` is refused.
- **Docs.** `docs/pi.md` says exactly what is held (the path to the
  program and every link on it with their ancestors, and the install
  directory) and that an install whose other directories are symlinks
  (pnpm, `npm link`) is not held, so Pi is installed by Homebrew or plain
  npm. Lead decision: those layouts are not covered in code.

## Merged

Merged 2026-10-04 by the merge train, fast-forward to abcd8da75.

- **Checks.** review-3b-p5 `pass` on 7b426b15a (governance boolean: no).
  test-3b-p5 `pass` (base 774 passed, head 776 passed, 17 skipped each).
  docs-3b-p5 `no_change`.
- **Rebase.** Squashed from m3b-pi at 7b426b15a onto 1.4u's merge. Folds:
  1.4v's fixed `claude` path stays and `pi` and `node` are added at fixed
  Homebrew paths; a turn's stdout and stderr go whole to files (1.4u) and
  a prompt on stdin goes through `communicate`; `fresh_dir` keeps
  `rmtree` and makes `pi`; the docs runner takes its seat's harness
  through `resolve_seat`, as critique does; harnesses-codex-pi.md gives
  way to harnesses.md's Pi section, and the Pi install denial goes into
  sandbox-openings.md.
- **Suite.** 1007 passed, 19 skipped; `ruff check` clean; `ruff format
  --check` flags only docs/bridges/telegram.md and docs/plans/m2-1-port.md.
- **Backup.** valor_rebuild-20261003T202331Z.dump.
- **Rollout.** 1: `openai-key` reports the key kept; Pi is 0.73.1. 2
  (backup first; spend metered by the gateway): working turn: passed, Pi reported $0.0157, the gateway charged $0.0180.
  Compaction survived: passed. Pi compaction: 8 calls, input 914,278 then
  591,229 and 323,666, charged $8.84, Pi reported $2.62. Claude Code
  compaction: 37 calls, 971,417 down to 78,312, charged $17.41. Critique
  at `reviewer_openai`: failed. node aborts at start (signal 6) because
  the turn's stdout and stderr files (`<work>/<task>/turns/`) sit where
  the fresh profile denies reads, and node needs `file-read-metadata` on
  its standard streams. The same case passed at 7b426b15a ($0.0160).
- **Follow-ups.** The turn output files under a denied path: in a
  provisioned task `<work>` is denied to every profile, so a working Pi
  turn there likely aborts too (not run). Steps 3 and 4 wait for 1.4c, 1.5.
