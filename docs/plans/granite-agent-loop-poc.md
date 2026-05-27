---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-05-28
tracking: https://github.com/tomcounsell/ai/issues/1486
last_comment_id:
---

# PoC: Granite-Orchestrated Dual Claude Code Session Executor

## Problem

**Current behavior:**
The `AgentSession` executor in `agent/sdk_client.py` uses `claude-agent-sdk` to spawn short-lived `claude -p` subprocesses — one per turn, respawned with `--resume <uuid>` to maintain context. The SDK mediates every interaction: it handles turn streaming, registers hooks for tool use, and routes output. This creates tight coupling between session execution and the SDK billing path.

The result is a system where the PM role and Dev role are differentiated by Python logic (persona injection, hook filtering) rather than by actual model behavior — the PM session does not "think about what to tell Dev" any more than a subprocess with a different system prompt does.

**Desired outcome:**
A standalone proof-of-concept demonstrating a three-layer architecture:
- **granite4.1:3b** (local via ollama) — session operator, handles all non-deterministic Claude Code behaviors
- **Opus session** — PM role, persistent interactive Claude Code session primed to direct Dev work
- **Sonnet session** — Dev role, persistent interactive Claude Code session primed for implementation

Both Claude Code sessions run under Max subscription (OAuth), not API keys, and are driven by granite the same way a human drives Claude Code at a terminal. No `claude -p`, no `claude-agent-sdk`.

## Freshness Check

**Baseline commit:** `129336b0`
**Issue filed at:** 2026-05-28
**Disposition:** Unchanged — issue was filed today, all claims verified below.

**File:line references re-verified:**
- `agent/sdk_client.py:2180-2195` — `_HARNESS_COMMANDS` confirms the current system uses `claude -p --verbose --output-format stream-json` — still holds
- `agent/sdk_client.py:2396-2398` — `--resume <prior_uuid>` is the per-turn respawn mechanism — still holds
- `agent/sdk_client.py:1560` — Max subscription OAuth fallback (strip `ANTHROPIC_API_KEY`) — still holds

**Cited sibling issues/PRs re-checked:**
- #1129 (closed 2026-04-23) — per-session model routing wired to `--model` flag; foundation this PoC builds on
- #1106 (closed 2026-04-22) — model routing gap doc, resolved by #1129

**Active plans in `docs/plans/` overlapping this area:**
- `agentsession-harness-abstraction.md` (status: docs_complete, tracking #780) — that plan decoupled dev session spawning from the Agent tool; this PoC goes further by replacing the entire execution substrate. Overlap is intentional — this is a successor experiment.

## Prior Art

- **#780 / `agentsession-harness-abstraction.md`**: Decoupled PM-spawned dev sessions from the Agent SDK tool; made `AgentSession` the harness abstraction. This PoC extends the direction of that work — replacing `claude -p` subprocess harness with persistent interactive sessions.
- **#1129**: Wired `AgentSession.model` to `--model` flag so PM→Opus and Dev→Sonnet routing is possible. The PoC builds on this foundation but bypasses `sdk_client.py` entirely.

## Research

**Queries used:**
- "ollama Python library chat API multi-turn conversation history 2025 2026"
- "Claude Code CLI --resume session headless multi-turn stream-json 2025 2026"

**Key findings:**
- **ollama multi-turn**: `ollama.chat(model, messages=[...])` maintains history via an explicit messages list. Append both user and assistant messages to continue a conversation. Full tool calling support available — tools defined as Python functions or JSON schema dicts. ([DeepWiki ollama-python](https://deepwiki.com/ollama/ollama-python/4.7-conversation-history))
- **granite4.1:3b tool calling**: Confirmed `tools` capability in `ollama show granite4.1:3b`. Response surface: `response.message.tool_calls[N].function.{name, arguments}`. Spike confirmed working locally.
- **`--input-format stream-json`**: The CLI mechanism for persistent bidirectional programmatic communication without `-p`. Session stays alive, reads JSON messages from stdin per turn, emits stream-json events to stdout. `{"type": "result", ...}` marks turn completion. ([Claude Code headless docs](https://code.claude.com/docs/en/headless))
- **Session continuity**: Current codebase uses per-turn respawn with `--resume <uuid>` (sdk_client.py:2396). The PoC uses persistent subprocess (no respawn needed) — simpler and closer to interactive behavior.

## Spike Results

### spike-1: Does `--resume` work in headless `-p` mode?
- **Assumption**: "We need `--resume <uuid>` for multi-turn continuity"
- **Method**: code-read
- **Finding**: Current system uses per-turn respawn with `--resume`. But `--input-format stream-json` enables a persistent subprocess that never respawns — no UUID management needed. UUID is only required for the per-turn respawn pattern.
- **Confidence**: high
- **Impact on plan**: ClaudeSession uses persistent subprocess with stdin/stdout, not per-turn respawn. No `claude_session_uuid` storage needed in the PoC.

### spike-2: Does granite4.1:3b support tool calling via ollama?
- **Assumption**: "granite4.1:3b can call tools defined in Python"
- **Method**: code-read + ollama show
- **Finding**: `ollama show granite4.1:3b` confirms `tools` capability. ollama Python library supports tool definitions as Python functions with Google-style docstrings.
- **Confidence**: high
- **Impact on plan**: GraniteRouter can define operator tools as Python functions directly — clean, readable tool definitions.

### spike-3: How does `--input-format stream-json` message format work?
- **Assumption**: "We can write JSON to stdin and read events from stdout"
- **Method**: code-read (sdk_client.py:2180-2220)
- **Finding**: Current system already uses `--output-format stream-json`. `--input-format stream-json` is the complement for persistent sessions — accepts JSON-encoded user turns on stdin. The PoC must verify the exact input schema; likely `{"prompt": "..."}` or `{"type": "user", "message": "..."}`.
- **Confidence**: medium (exact input schema needs one-turn verification during implementation)
- **Impact on plan**: ClaudeSession.send_message() must use the correct JSON envelope. Add a quick format verification test in the implementation.

## Data Flow

```
User provides initial task string
        ↓
GraniteAgentLoop.run(task)
        ↓
GraniteRouter.start_task(task) → formats initial PM prompt
        ↓
PMSession.send_message(prompt) → writes JSON to stdin
        ↓
PMSession stdout: stream-json events...
  {"type": "tool_use", ...}    ← PM reads/writes plan docs
  {"type": "result", "result": {"content": "..."}}  ← turn complete
        ↓
GraniteRouter.handle_pm_output(events) → extracts Dev prompt from PM result
        ↓
DevSession.send_message(dev_prompt) → writes JSON to stdin
        ↓
DevSession stdout: stream-json events...
  {"type": "tool_use", ...}    ← Dev runs bash, edits files
  {"type": "text", ...}        ← Dev intermediate output
  {"type": "result", ...}      ← turn complete (or timeout → probe)
        ↓
GraniteRouter.handle_dev_output(events) → summarizes for PM
        ↓
PMSession.send_message(summary) → loop continues
        ↓ (until granite detects PM signals completion)
GraniteAgentLoop returns final result
```

**Non-deterministic events** (granite handles inline, never forwarded):
- Multiple-choice prompt → `GraniteRouter.handle_operator_event()` → `DevSession.send_message("1")`
- Feedback rating request → dismiss with `DevSession.send_message("skip")`
- Stream silence > 120s → `DevSession.send_message("still working or wrapped up?")`
- Process exit (crash) → `ClaudeSession.restart()` + re-prime

## Architectural Impact

- **New files only** — `agent/granite_router.py`, `agent/claude_session.py`, `agent/granite_agent_loop.py`, `scripts/granite_poc.py`
- **No changes to existing code** — `sdk_client.py`, `session_executor.py`, `worker/` are untouched
- **New dependency**: `ollama` Python library (already available via `pip install ollama`)
- **Coupling**: Zero — the PoC is fully standalone. The existing executor continues running all production sessions.
- **Reversibility**: Delete the four new files. No migration needed.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (experiment, not gated on approval)
- Review rounds: 1 (code review after PoC runs end-to-end)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ollama` Python library installed | `python -c "import ollama; print(ollama.__version__)"` | GraniteRouter chat calls |
| granite4.1:3b pulled | `ollama list \| grep granite4.1:3b` | Local operator model |
| Claude Max subscription active | `claude auth status 2>&1 \| grep -i "logged in"` | PM and Dev session auth |

## Solution

### Key Elements

- **`ClaudeSession`** (`agent/claude_session.py`): Persistent subprocess wrapping `claude --input-format stream-json --output-format stream-json --model <opus|sonnet> --permission-mode bypassPermissions` with `ANTHROPIC_API_KEY=""` in env. Exposes `send_message(text)` and `read_until_result() -> list[dict]` (reads stream-json events until `{"type": "result"}` or timeout).

- **`GraniteRouter`** (`agent/granite_router.py`): Wraps `ollama.chat('granite4.1:3b', messages=[], tools=[...])`. Defines operator tools as Python functions: `extract_dev_prompt`, `summarize_for_pm`, `handle_choice`, `probe_session`, `signal_done`. Maintains routing message history across turns.

- **`GraniteAgentLoop`** (`agent/granite_agent_loop.py`): Instantiates PM and Dev ClaudeSession, wires the loop: PM → granite → Dev → granite → PM until `signal_done` tool is called. Emits a structured trace log after each turn.

- **`scripts/granite_poc.py`**: CLI entrypoint — `python scripts/granite_poc.py "write a hello world Python file"` runs the full loop and prints the trace.

### Flow

Task provided → GraniteAgentLoop starts → granite formulates PM prompt → PM session activated → PM outputs dev instructions → granite extracts → Dev session activated → Dev does work → granite summarizes → PM evaluates → repeat until PM signals done → result returned

### Technical Approach

- **Persistent subprocess**: `subprocess.Popen(cmd, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=no_api_key_env)` — both sessions stay alive for the full loop
- **Turn completion**: Read stdout line-by-line; `json.loads(line)["type"] == "result"` signals turn complete. 120s silence → granite steering probe.
- **No AgentSDK**: Raw subprocess, no `claude_agent_sdk` import anywhere in the new files
- **No `-p` flag**: Sessions run in interactive mode with `--input-format stream-json`
- **Operator events**: Granite's `handle_operator_event` is called for every non-`tool_use`/`tool_result`/`result` line. Pattern-matches `?` + numbered options (multiple choice), rating strings (feedback), empty output > 30s (hang).
- **Granite tool dispatch**: `ollama.chat()` returns `tool_calls`; the loop executes the indicated tool function and appends the `role: "tool"` result to granite's message history before the next `chat()` call.
- **Trace log**: Each turn appended to `logs/granite_poc_trace.jsonl` — `{turn, role, input_summary, output_summary, operator_events, duration_s}`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ClaudeSession.read_until_result()` must handle `subprocess.TimeoutExpired`, `json.JSONDecodeError` (malformed stream-json line), and `BrokenPipeError` (process died mid-read) — each logs and triggers granite's restart path, never propagates to the caller
- [ ] `GraniteRouter.chat()` wraps ollama calls in try/except — if granite itself fails, the loop raises `GraniteRoutingError` (explicit, not silent)

### Empty/Invalid Input Handling
- [ ] `ClaudeSession.send_message("")` raises `ValueError` before writing to stdin — empty prompts cause Claude Code to hang waiting
- [ ] `GraniteRouter.handle_pm_output([])` (empty events list) returns a safe probe string rather than passing empty context to granite

### Error State Rendering
- [ ] Crash during Dev turn: `ClaudeSession` catches process exit, logs, calls `restart()`, informs granite with operator event `{"type": "crash", "session": "dev"}` — granite decides whether to retry or escalate to PM
- [ ] The trace log always emits an entry even on failure — the PoC must produce evidence of what happened, not a silent crash

## Test Impact

No existing tests affected — this is a greenfield experiment. New files are standalone and do not modify any existing module. The four new files have no callers in production code.

New tests to create:
- `tests/unit/test_claude_session.py` — unit tests for stream parsing, turn completion detection, timeout probe, restart behavior (mock subprocess)
- `tests/unit/test_granite_router.py` — unit tests for PM/Dev output handling, operator event detection, tool dispatch (mock ollama)
- `tests/integration/test_granite_agent_loop.py` — marked `@pytest.mark.slow` — runs actual granite4.1:3b against mock ClaudeSession fixtures

## Rabbit Holes

- **Wiring into production AgentSession lifecycle** — explicitly out of scope; any attempt to "just hook it in" before the PoC validates will cost more than it saves
- **Handling every possible Claude Code edge case** — the PoC needs the three main ones (multiple choice, feedback rating, hang); exotic cases (permission prompts, large file diffs) are follow-on
- **Optimizing granite latency** — granite4.1:3b is fast enough locally; don't spend time on batching or async ollama until the architecture is validated
- **Multi-task parallelism** — the PoC is single-loop; parallel task orchestration is a separate concern

## Risks

### Risk 1: `--input-format stream-json` input envelope schema
**Impact:** ClaudeSession.send_message() sends incorrectly formatted JSON; Claude Code session never processes the message
**Mitigation:** Spike with a one-line test before implementing the full loop: `echo '{"prompt": "hello"}' | claude --input-format stream-json --output-format stream-json 2>&1 | head -5`; adjust envelope format based on what Claude Code actually accepts

### Risk 2: Opus session context blowout
**Impact:** PM (Opus) accumulates too much context across many turns — Dev's full stream-json (including all tool calls) passed to PM every turn is expensive and may hit context limits
**Mitigation:** Granite's `summarize_for_pm` extracts only the result text and key observations — never forwards raw tool call streams to PM

### Risk 3: granite4.1:3b routing quality
**Impact:** Granite misroutes — passes garbage to Dev, fails to detect completion, or loops indefinitely
**Mitigation:** Hard cap of 10 PM→Dev turns in `GraniteAgentLoop`; if cap is hit, loop exits with trace and a `"max_turns_reached"` status for human review

### Risk 4: Max subscription rate limits
**Impact:** Two simultaneous Claude Code sessions may hit concurrent session limits on Max plan
**Mitigation:** Sessions are sequential (PM turn → granite → Dev turn → granite → PM), not concurrent. Only one session is active at a time.

## Race Conditions

No concurrency concerns — the loop is fully sequential: PM turn completes before Dev turn starts. `ClaudeSession.read_until_result()` blocks until the `result` event or timeout. No shared mutable state between sessions.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG #1486]` Production replacement of `sdk_client.py` and `session_executor.py` — the PoC must succeed and be evaluated before any production wiring begins
- `[EXTERNAL]` Max subscription concurrent session limit verification — requires testing on the actual subscription plan; the PoC will surface this if it's a real problem

## Update System

No update system changes required — the PoC files are purely additive and not deployed to any machine via the update system. `ollama` must be available on whatever machine runs the script, but granite4.1:3b is already present on the dev machine.

## Agent Integration

No agent integration required at PoC stage — `scripts/granite_poc.py` is a developer script, not a bridge-facing tool. A follow-on issue will wire the validated architecture into the worker if the PoC succeeds.

## Documentation

- [ ] Create `docs/features/granite-agent-loop.md` describing the architecture, data flow, and operator event taxonomy — written after the PoC run produces evidence
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `python scripts/granite_poc.py "write a hello world Python file"` runs end-to-end and produces a committed Python file without human intervention
- [ ] `logs/granite_poc_trace.jsonl` contains at least 2 PM→Dev turns with correct `{"type": "result"}` detection
- [ ] At least one operator event (multiple-choice, feedback, or hang) is detected and handled by granite during the run
- [ ] Both PM (Opus) and Dev (Sonnet) subprocesses have `ANTHROPIC_API_KEY=""` in their env — `grep -v "ANTHROPIC_API_KEY" /proc/$(pgrep -f granite_poc)/environ` passes (or equivalent macOS check)
- [ ] Written assessment file `docs/plans/granite-agent-loop-poc-results.md` produced after the run, covering: latency per turn, context window pressure, turn-completion reliability, operator-event frequency
- [ ] Tests pass: `pytest tests/unit/test_claude_session.py tests/unit/test_granite_router.py -x -q`

## Team Orchestration

### Team Members

- **Builder (granite-poc)**
  - Name: poc-builder
  - Role: Implement all four new files and the unit test suite
  - Agent Type: builder
  - Resume: true

- **Validator (granite-poc)**
  - Name: poc-validator
  - Role: Run the PoC end-to-end, verify acceptance criteria, produce assessment doc
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement ClaudeSession
- **Task ID**: build-claude-session
- **Depends On**: none
- **Validates**: `tests/unit/test_claude_session.py` (create)
- **Informed By**: spike-1 (persistent subprocess, no per-turn respawn), spike-3 (verify input envelope format)
- **Assigned To**: poc-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/claude_session.py` with `ClaudeSession` class
- Subprocess cmd: `claude --input-format stream-json --output-format stream-json --model <model> --permission-mode bypassPermissions`
- Env: inherit os.environ, set `ANTHROPIC_API_KEY=""`
- `send_message(text: str)`: writes JSON envelope to stdin, flush
- `read_until_result(timeout: int = 120) -> list[dict]`: reads stdout line-by-line, parses JSON, returns all events up to and including `{"type": "result"}`; on timeout returns events accumulated so far with a synthetic `{"type": "timeout"}` appended
- `restart()`: kill subprocess, respawn fresh, re-prime with original system context
- Verify `--input-format stream-json` input envelope via `echo '{"prompt":"hello"}' | claude --input-format stream-json --output-format stream-json` before implementing send_message

### 2. Implement GraniteRouter
- **Task ID**: build-granite-router
- **Depends On**: none
- **Validates**: `tests/unit/test_granite_router.py` (create)
- **Informed By**: spike-2 (granite4.1:3b supports tool calling via ollama Python lib)
- **Assigned To**: poc-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/granite_router.py` with `GraniteRouter` class
- `from ollama import chat` — no other imports from ollama
- Define operator tools as Python functions with docstrings: `extract_dev_prompt(pm_result: str) -> str`, `summarize_for_pm(dev_events: list) -> str`, `handle_choice(question: str, options: list) -> str`, `probe_session(reason: str) -> str`, `signal_done(result_summary: str) -> str`
- `route(pm_events: list[dict] | None, dev_events: list[dict] | None) -> RouterDecision` — calls `chat()` with full history + tools, dispatches tool call, returns `RouterDecision(action, payload)` where action ∈ `{send_to_dev, send_to_pm, probe, restart, done}`
- Maintain `self.messages: list[dict]` history across calls

### 3. Implement GraniteAgentLoop and PoC script
- **Task ID**: build-agent-loop
- **Depends On**: build-claude-session, build-granite-router
- **Validates**: `scripts/granite_poc.py` runs without import error
- **Assigned To**: poc-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `agent/granite_agent_loop.py` with `GraniteAgentLoop` class
- `run(task: str, max_turns: int = 10) -> LoopResult`: main loop
- Start PMSession and DevSession as persistent subprocesses on entry
- Turn loop: granite.route(None, None) → get initial PM prompt → PMSession.send_message → read_until_result → granite.route(pm_events, None) → get dev prompt → DevSession.send_message → read_until_result → granite.route(None, dev_events) → repeat
- Append each turn to `logs/granite_poc_trace.jsonl`
- Exit loop on `signal_done` action or `max_turns` hit
- Create `scripts/granite_poc.py`: `if __name__ == "__main__": import sys; from agent.granite_agent_loop import GraniteAgentLoop; result = GraniteAgentLoop().run(sys.argv[1]); print(result)`

### 4. Run the PoC end-to-end
- **Task ID**: validate-poc-run
- **Depends On**: build-agent-loop
- **Assigned To**: poc-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python scripts/granite_poc.py "write a hello world Python file named hello_poc.py and commit it"`
- Verify `hello_poc.py` exists and contains valid Python
- Verify `logs/granite_poc_trace.jsonl` has ≥ 2 turns
- Verify `ANTHROPIC_API_KEY` is absent from session subprocess envs
- Check unit tests: `pytest tests/unit/test_claude_session.py tests/unit/test_granite_router.py -x -q`
- Write `docs/plans/granite-agent-loop-poc-results.md` with: turn count, latency per turn, operator events observed, any failures, overall verdict (proceed to production replacement / needs rework / abandon)

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-poc-run
- **Assigned To**: poc-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/granite-agent-loop.md` using findings from the PoC run
- Add entry to `docs/features/README.md` index

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `pytest tests/unit/test_claude_session.py tests/unit/test_granite_router.py -x -q` | exit code 0 |
| PoC trace exists | `test -s logs/granite_poc_trace.jsonl` | exit code 0 |
| No API key in new files | `grep -r "ANTHROPIC_API_KEY" agent/granite_router.py agent/claude_session.py agent/granite_agent_loop.py` | exit code 1 |
| No claude-agent-sdk import | `grep -r "claude_agent_sdk\|ClaudeAgent" agent/granite_*.py agent/claude_session.py` | exit code 1 |
| Results doc exists | `test -f docs/plans/granite-agent-loop-poc-results.md` | exit code 0 |
| Lint clean | `python -m ruff check agent/granite_router.py agent/claude_session.py agent/granite_agent_loop.py scripts/granite_poc.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **`--input-format stream-json` envelope format**: The exact JSON structure for user messages written to stdin is undocumented beyond the CLI flags table (GitHub issue #24594). Implementation must verify this before completing ClaudeSession — the spike recommended `echo '{"prompt":"hello"}' | claude ...` as a one-liner check.
2. **Concurrent session limit on Max**: Does Max plan enforce a single active session? The PoC runs sessions sequentially (one active at a time) which should be fine, but this has not been tested.
