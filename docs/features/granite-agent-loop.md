# Granite Agent Loop (PoC)

**Status:** Proof of concept -- not wired into production. See
[`docs/plans/granite-agent-loop-poc.md`](../plans/granite-agent-loop-poc.md)
for the plan and
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

Each `ClaudeSession` gets a unique `CLAUDE_CODE_TASK_LIST_ID` of the
form `granite-poc-{pm|dev}-<8 hex chars>`. This prevents the PoC's
task list from polluting any concurrent dev session running under
`session/<slug>` worktrees. The variable is set at subprocess spawn
time, before any Claude logic runs.

## Failure modes

- **Per-line readline timeout (30s)** -- granite is informed via
  `operator_events: [{"type": "timeout", ...}]` and typically responds
  by calling `probe_session`. Hard cap is the overall `read_until_result`
  timeout (180s default in the loop).
- **JSON decode error on stdout** -- the bad line is captured as
  `{"type": "decode_error", "raw": "...", "error": "..."}` and surfaced
  as an operator event; the loop continues reading.
- **Broken pipe / EOF** -- `{"type": "broken_pipe", "reason": "..."}`
  is surfaced; the loop calls `target_session.restart()` and routes
  with `operator_events: [{"type": "crash", "session": "dev|pm"}]`.
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
