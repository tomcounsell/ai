# Granite Agent Loop (historical)

**Status:** Superseded by the
[interactive TUI session runner](granite-interactive-tui.md). This
doc is historical; the interactive-TUI runner is the source of truth. See
that doc for the 3-layer architecture, the 10 invariants,
the persona-priming flow, the granite classification + translation
taxonomy, and the steady-state loop. The interjection text on line
294-296 of this doc is out of date (older TUI text); the live runner's
`INTERRUPTED_RE` regex accepts both forms.

See
[`docs/plans/granite-agent-loop-poc.md`](../plans/granite-agent-loop-poc.md)
for the original plan and
[`docs/plans/granite-agent-loop-poc-results.md`](../plans/granite-agent-loop-poc-results.md)
for the run assessment.

## Why

Today's `agent/sdk_client.py` couples session execution to `claude-agent-sdk`
and the Anthropic API key billing path. PM vs Dev role differentiation is
all Python-side (persona injection, hook filtering); the PM model never
actually "thinks about what to tell Dev."

The Granite Agent Loop demonstrates an alternative substrate:

- **granite4.1:3b** (local, via ollama) plays the role of *operator* --
  the same role a human plays at a terminal driving Claude Code by hand.
- **Opus** is the PM session (planner / reviewer).
- **Sonnet** is the Dev session (implementer).
- Both Claude sessions run under the **Max subscription OAuth path**, not
  API keys.

If validated, this architecture replaces the SDK and removes the
per-turn `--resume <uuid>` respawn pattern.

## Architecture

```
+-----------------+
|  user task str  |
+--------+--------+
         v
+--------------------+              +-----------------+
| GraniteAgentLoop   |              | GraniteRouter   |
| (sequential loop)  |--- chat ---->| ollama          |
|                    |<- decision --| granite4.1:3b   |
+----+----------+----+              +-----------------+
     |          |
     v          v
+---------+ +---------+
| PM      | | Dev     |
| Opus    | | Sonnet  |
| Claude  | | Claude  |
| (-p     | | (-p     |
|  stream-| |  stream-|
|  json)  | |  json)  |
+---------+ +---------+
```

Three new modules live under `agent/`:

- [`agent/claude_session.py`](../../agent/claude_session.py) --
  `ClaudeSession` wraps one Claude Code subprocess with
  `claude -p --verbose --input-format stream-json --output-format stream-json`.
- [`agent/granite_router.py`](../../agent/granite_router.py) --
  `GraniteRouter` calls `ollama.chat('granite4.1:3b', messages, tools)`
  with five operator tools (see below).
- [`agent/granite_agent_loop.py`](../../agent/granite_agent_loop.py) --
  `GraniteAgentLoop` ties the two ClaudeSessions and the GraniteRouter
  into a sequential turn loop with a per-run trace log.

CLI entry point: [`scripts/granite_poc.py`](../../scripts/granite_poc.py).

## Operator tool taxonomy

Granite NEVER speaks free-form. Every routing turn dispatches to exactly
one of these tools:

| Tool | When granite calls it | Effect |
|---|---|---|
| `extract_dev_prompt` | PM just produced instructions intended for Dev | Send `{"choice/text"}` to the Dev subprocess |
| `summarize_for_pm` | Dev just finished a turn -- raw stream-json must not flow to PM | Send a one-paragraph distillation to the PM subprocess |
| `handle_choice` | A session emitted a numbered multiple-choice prompt | Reply to the session with the chosen number |
| `probe_session` | A session went silent past the per-line timeout | Send "still working or wrapped up?" to the stalled session |
| `signal_done` | PM explicitly signaled "TASK COMPLETE" or otherwise indicated finished work | Exit the loop with the final payload |

Each tool is a Python function with an OpenAPI-style JSON schema; ollama
returns `response.message.tool_calls[0].function.{name, arguments}`. A
parse failure (no tool call, unknown name, ollama exception) raises
`GraniteRoutingError` -- the loop never silently keeps going.

## Stream-json envelope

The Claude CLI accepts `--input-format stream-json` only when paired with
`-p / --print` and `--verbose` (the CLI emits
`Error: When using --print, --output-format=stream-json requires --verbose`
otherwise). The verified per-turn input envelope written to subprocess
stdin is:

```json
{"type": "user", "message": {"role": "user", "content": "<text>"}}
```

Stream-json output events on stdout have a documented shape; the PoC
cares about three of them:

- `{"type": "result", "subtype": "success", "result": "...", ...}` --
  turn complete. `ClaudeSession.read_until_result()` returns on this.
- `{"type": "assistant", "message": {"content": [...]}}` -- mid-turn
  text / tool_use events that granite uses to summarize.
- `{"type": "system", ...}` -- init/rate-limit/etc., logged but not
  routed.

Any line that fails JSON decode, any per-line readline that exceeds
30s, and any broken pipe is surfaced as a SYNTHETIC event
(`decode_error`, `timeout`, `broken_pipe`) appended to the events list
rather than raised. The loop's "operator_events" routing path uses
these as input to granite.

## Authentication: Max OAuth, not API key

`ClaudeSession._build_env` sets `ANTHROPIC_API_KEY = ""` in the
subprocess environment. This is the documented way to force the Claude
CLI onto the Max subscription OAuth path even when the launching shell
has an API key set. The PoC verifies this via subprocess.Popen spy in
the test suite.

This is the entire point of the architecture from a billing perspective:
all Claude usage rides the user's Max subscription, with zero
per-request API key cost.

## Session isolation

In this superseded loop, each `ClaudeSession` got a unique
`CLAUDE_CODE_TASK_LIST_ID` per PM/Dev role. This prevented its
task list from polluting any concurrent dev session running under
`session/<slug>` worktrees. The variable was set at subprocess spawn
time, before any Claude logic ran. (The live interactive-TUI runner
inherits its task-list isolation from the bridge-originated session;
see [`granite-interactive-tui.md`](granite-interactive-tui.md).)

## Failure modes

- **Per-line readline timeout (30s)** -- granite is informed via
  `operator_events: [{"type": "timeout", ...}]` and typically responds
  by calling `probe_session`. Hard cap is the overall `read_until_result`
  timeout (180s default in the loop).
- **JSON decode error on stdout** -- the bad line is captured as
  `{"type": "decode_error", "raw": "...", "error": "..."}` and surfaced
  as an operator event; the loop continues reading.
- **Broken pipe / EOF** -- `{"type": "broken_pipe", "reason": "..."}`
  is surfaced; on the next send the loop calls `target_session.resume()`
  (context-preserving `claude --resume <session_id>`, falling back to a fresh
  session only if no id was captured) and routes with
  `operator_events: [{"type": "crash", "session": "dev|pm",
  "recovered_via": "resume|restart"}]`.
- **granite itself fails** -- `GraniteRoutingError` is raised; the
  loop exits with `status="granite_routing_error"` and writes a final
  trace entry. This is the only path that surfaces an error to the
  caller.
- **max_turns reached** -- the loop exits with
  `status="max_turns_reached"`; granite's last partial result is in
  `final_payload`. Hard cap defaults to 10.
- **SIGTERM / SIGINT** -- the loop's `_on_signal` handler tears down
  both subprocesses via `atexit` and re-raises 128+signo so the parent
  process exits cleanly. No zombie Claude processes leak.

## Trace log

Every turn appends a line to `logs/granite_poc_trace.jsonl`. Schema:

```json
{
  "ts": <epoch seconds>,
  "turn": <int>,
  "stage": "send_to_dev" | "send_to_dev_result" | ... | "done",
  "session": "pm" | "dev",
  "granite_tool": "extract_dev_prompt" | ...,
  "prompt_preview": "<first 400 chars>",
  "events_count": <int>,
  "operator_events": [...],
  "duration_ms": <int>
}
```

This is the input to the assessment doc -- see
[`docs/plans/granite-agent-loop-poc-results.md`](../plans/granite-agent-loop-poc-results.md)
for the schema fields it must populate.

## Running the PoC

```bash
# Prereqs verified by docs/plans/granite-agent-loop-poc.md ## Prerequisites
python -c "import ollama"
ollama show granite4.1:3b
claude auth status   # must show "Logged in via OAuth"

# Run from any cwd; the PoC will use $PWD as the Claude sessions' working dir.
python scripts/granite_poc.py "write a Python file named hello_poc.py that prints 'Hello from granite PoC' when run"
```

Output is the `LoopResult` dataclass as JSON. Trace lines stream to
`./logs/granite_poc_trace.jsonl`.

Smoke gate (runs in ~25 seconds; required before any code changes to
the operator-tool schema):

```bash
python scripts/granite_smoke_test.py
```

This must report parse-error-rate <= 20% or the architecture is
considered abandoned (kill criterion in the plan).

## Testing & emulation

Claude Code sessions have peculiarities the operator must survive: a session
can pose a numbered **multiple-choice question**, surface a **permission /
feedback prompt**, **time out**, emit a malformed line, or **crash** mid-run.
Reproducing those against a live `claude` subprocess is slow and flaky, so the
suite emulates them deterministically and reserves one gated live test for the
thing that can't be faked (granite actually *recognizing* a real question).

### Test layout

| File | Kind | Covers |
|---|---|---|
| [`tests/unit/granite_session_emulator.py`](../../tests/unit/granite_session_emulator.py) | fixture (not collected) | `FakeClaudeSession`, `FakeRouter`, stream-json + peculiarity event builders |
| [`tests/unit/test_claude_session.py`](../../tests/unit/test_claude_session.py) | unit | env, envelope, `_build_cmd` flag guard, `read_until_result` failure modes, lifecycle |
| [`tests/unit/test_granite_router.py`](../../tests/unit/test_granite_router.py) | unit | tool dispatch, dict/object response shapes, truncation, decision defaults, ollama-missing |
| [`tests/unit/test_granite_agent_loop.py`](../../tests/unit/test_granite_agent_loop.py) | unit | every loop exit path, crash→restart, operator-event forwarding, teardown, trace log |
| [`tests/unit/test_granite_peculiarities.py`](../../tests/unit/test_granite_peculiarities.py) | unit | multiple-choice, feedback-prompt, crash/resume-gap emulation |
| [`tests/unit/test_granite_questions_game.py`](../../tests/unit/test_granite_questions_game.py) | unit | the questions-game harness parsing helpers |
| [`tests/integration/test_granite_questions_game.py`](../../tests/integration/test_granite_questions_game.py) | integration (gated) | live: granite answering a real Claude quiz |
| [`tests/integration/test_claude_session_resume.py`](../../tests/integration/test_claude_session_resume.py) | integration (gated) | live: ctrl-c a real session, resume, recall context |

### The emulator

`tests/unit/granite_session_emulator.py` is a fixture library (no `test_`
prefix, so pytest does not collect it). It provides:

- **`FakeClaudeSession`** — API-compatible drop-in for `ClaudeSession`. It
  replays a *script*: a list of turns, where each `(send_message,
  read_until_result)` pair consumes one. A turn that is a `list[dict]` is
  returned as events; a turn that is an `Exception` is raised from
  `send_message` to emulate a crash. It records `sent_messages`,
  `restart_count`, and `stop_count` so tests can assert routing and teardown.
- **`FakeRouter`** — replays scripted `RouterDecision`s and records every
  `route()` call (so a test can assert exactly which `operator_events` were
  forwarded to granite). Exhaustion returns a `done` decision so loops never
  hang; a scripted `Exception` raises to emulate `GraniteRoutingError`.
- **`patch_sessions(monkeypatch, pm, dev)`** — patches the loop's internal
  `ClaudeSession` constructor, dispatching PM (`model='opus'`) vs Dev
  (`model='sonnet'`) to the right pre-scripted fake.
- **Event/peculiarity builders** — `system_init_event`, `assistant_text_event`,
  `result_event`, `timeout_event`, `decode_error_event`, `broken_pipe_event`,
  plus `multiple_choice_turn`, `feedback_prompt_turn`, and `crash_turn`.

### Peculiarity event shapes

The shapes were cross-checked against how `siteboon/claudecodeui` parses
Claude Code output in its raw-CLI era (which runs the same
`claude --print --output-format stream-json --verbose` path this PoC uses):

- **Multiple-choice question.** In headless `-p` mode Claude does NOT render an
  interactive TUI menu; the question arrives as ordinary assistant/result
  *text* containing numbered options. The canonical interactive shape is
  `❯ N. text` (U+276F arrow on the selected row) matched by siteboon's option
  regex `/[❯\s]*(\d+)\.\s+(.+)/`. `multiple_choice_text()` reproduces both
  renderings. `summarize_events` surfaces the question + options to granite,
  which is expected to answer via `handle_choice`.
- **Permission / feedback prompt.** Under `--permission-mode bypassPermissions`
  no approval prompt reaches stdout (siteboon likewise *avoids* them rather
  than parsing them). `feedback_prompt_turn()` models the text one *would* take
  so the operator's handling is exercised regardless.
- **Crash.** `crash_turn()` raises `BrokenPipeError` on send; the loop catches
  it, calls `restart()`, and routes `operator_events=[{"type":"crash",...}]`.

### Crash recovery via `claude --resume` (context-preserving)

A crashed or interrupted session is recovered **with its context intact**, the
same way `siteboon/claudecodeui` does it:

- `ClaudeSession` captures the Claude session UUID from the stream-json output —
  every event carries `session_id`, and the `system/init` event is the first to
  do so. `_capture_session_id()` records it as each line is parsed.
- As a fallback, when no `session_id` was seen before the process died,
  `_scan_stderr_for_session_id()` reads buffered stderr for the on-exit hint
  Claude prints — `Resume this session with:\nclaude --resume <uuid>` — and
  extracts the UUID via `--resume\s+<uuid>`.
- `ClaudeSession.resume()` respawns with `claude --resume <session_id>`,
  preserving the conversation. It returns `True` when a captured id was used,
  or `False` if it fell back to a fresh session because none is known yet.
  `restart()` remains the deliberate context-*losing* fresh respawn.
- `GraniteAgentLoop`'s crash path calls `resume()` first and only falls back to
  a fresh session if no id is known; the routed `operator_event` records
  `recovered_via: "resume" | "restart"` so granite (and the trace) can see which
  happened.

**Ctrl-C semantics.** In headless `-p` mode a single SIGINT terminates the
subprocess immediately — verified by
`tests/integration/test_claude_session_resume.py`, which ctrl-c's a real
session and then resumes it to recall a planted codeword. The interactive TUI
behaves differently: the *first* ctrl-c shows `Interrupted · What should Claude
do instead?` and stays in the session; only the *second* ctrl-c exits and
prints the `claude --resume <uuid>` hint. That two-stage interjection is a TUI
affordance and does not occur on the headless stdio path. On SIGINT/SIGTERM the
loop's `_log_resume_hints()` logs `claude --resume <uuid>` for each live session
so a human can pick the work back up by hand.

> **Note from spike #1547:** the TUI v2.1.160 first-ctrl-c text is actually
> "Press Ctrl-C again to exit" (not `Interrupted · What should Claude do
> instead?`). The two-stage behavior is correct, but the literal text above
> is out of date. See the spike report at
> [`docs/research/spikes/granite-tui-pty-spike.md`](../research/spikes/granite-tui-pty-spike.md)
> (constraint C2) before relying on either form.

## TUI PTY Spike

The feasibility of driving a real interactive Claude Code TUI through a
pseudo-terminal was tested in spike [#1547](https://github.com/tomcounsell/ai/issues/1547)
(precondition for [#1546](https://github.com/tomcounsell/ai/issues/1546)).
**Verdict: drivable with caveats — use pexpect.** The full report lives at
[`docs/research/spikes/granite-tui-pty-spike.md`](../research/spikes/granite-tui-pty-spike.md);
load-bearing constraints (submit key, ctrl-c text, resume-UUID gating, /help
overlay, idle signal) are documented there as C1–C5.

The spike produced two reusable scripts that survive as research tools (not
production paths):
- `scripts/granite_tui_pty_spike.py` — stdlib `pty`+`select` driver
- `scripts/granite_tui_pty_spike_pexpect.py` — `pexpect` driver
- `scripts/granite_tui_pty_spike_report.py` — post-run analyzer

Re-runnable with the one-liner in the report's "Re-running the Spike"
section.

### Questions game (live operator benchmark)

[`scripts/granite_questions_game.py`](../../scripts/granite_questions_game.py)
is the live answer to "how well does granite actually *enter* answers?". It
spawns one real `ClaudeSession`, asks it to run an N-question multiple-choice
quiz one question per turn, and for each turn hands the events to the real
`GraniteRouter`, requiring a `handle_choice` whose payload is an in-range
option number. It reports `handle_choice_rate`, `in_range_rate`, and mean
router latency to `logs/granite_questions_game.json`.

```bash
python scripts/granite_questions_game.py --questions 5 --model haiku
```

The gated integration test wraps this and asserts granite produces a valid
in-range answer on the majority of question turns:

```bash
GRANITE_LIVE=1 pytest tests/integration/test_granite_questions_game.py -v -m slow
```

## What this is NOT

- **Not a production replacement.** `sdk_client.py`, `session_executor.py`,
  and the worker continue to run all real sessions. The PoC files are
  additive and standalone.
- **Not integrated with `queued_steering_messages`.** Granite writes
  directly to subprocess stdin -- this is an alternative to the
  existing session steering model, not a layer on top of it. If the
  PoC graduates to production, the steering model itself is in scope
  for redesign.
- **Not a quality judge.** Granite is a control-plane operator: it
  routes messages between sessions and detects completion. Quality
  assessment is the PM session's job.

## See also

- [`docs/plans/granite-agent-loop-poc.md`](../plans/granite-agent-loop-poc.md)
  -- the plan that produced this PoC
- [`docs/plans/granite-agent-loop-poc-results.md`](../plans/granite-agent-loop-poc-results.md)
  -- assessment of the end-to-end run
- [`docs/features/harness-abstraction.md`](harness-abstraction.md) --
  the existing `claude -p` harness this PoC explicitly does NOT use
- [`docs/features/pm-dev-session-architecture.md`](pm-dev-session-architecture.md)
  -- production PM/Dev session model the PoC parallels
