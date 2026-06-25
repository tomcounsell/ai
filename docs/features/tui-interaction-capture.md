# TUI Interaction Capture

Captures human-in-the-loop interaction patterns from local Claude Code TUI sessions and distills one retrievable subconscious-memory observation per session. Pillar 3 of epic #1536.

## What It Does

When a developer drives Claude Code at the terminal, the decisions they make are invisible to the autonomous agent personas: which slash commands they ran, when they injected a steering message mid-run, and how many tools they approved. This feature records those interaction signals during a session and, at session end, composes a compact natural-language "pattern" Memory tagged `tui-interaction`. That Memory becomes part of the subconscious-memory substrate and is recallable via the standard `memory_search` / `memory_get` MCP path.

**Scope:** local Claude Code TUI sessions only. Bridge-driven eng/granite sessions are explicitly out of scope because there is no human in the TUI for those sessions.

**This pillar is capture-and-store only.** Auto-emulation behavior (teaching autonomous sessions to mimic captured patterns) is a future pillar of epic #1536.

## Capture Surface

Two existing hooks instrument the session:

| Hook | Fires | Action |
|------|-------|--------|
| `UserPromptSubmit` | Every user prompt | `capture_prompt_event(session_id, prompt, cwd)` |
| `Stop` | Session end | `_run_tui_interaction_capture` helper calls `summarize_and_store(session_id, project_key)` |

Both operations are fail-silent: every code path is wrapped in `try/except Exception`. A capture failure never blocks a hook or the TUI.

## Interaction Event Types

The capture module emits two new telemetry event types that ride the existing `record_telemetry_event` JSONL recorder without modifying it. A third signal type (tool approvals) is tallied at summarize time from pre-existing `tool_use` events rather than requiring a new event.

### `slash_command`

Fires whenever the user submits a prompt that begins with `/`. The command name is the token immediately after `/` up to the first whitespace (e.g. `/do-test --quick` records `do-test`). Slash commands are always signal; no triviality gate applies.

```json
{"type": "slash_command", "command": "do-test"}
```

### `human_steering`

Fires when the user submits a non-slash prompt that passes all four gates (see Gating below). Captures the ordinal position within the session so that patterns like "first steer at turn 4" can be observed.

```json
{"type": "human_steering", "ordinal": 4, "snippet": "please also fix the failing test in..."}
```

### Tool approvals (tallied at summarize time)

Pre-existing `tool_use` events in the JSONL timeline are counted during `summarize_and_store`. No new event type is emitted; `post_tool_use.py` is deliberately untouched. Tool **rejections** are not captured: `PostToolUse` only fires for approved tools, so rejections are invisible to this feature (see Documented Gaps).

## Gating

### Slash commands

No gate. Every `/...` prompt is recorded.

### Human steering prompts

Four gates apply in order:

1. `strip_private` the prompt text (removes `<private>` tagged content).
2. If the cleaned, lowercased text is in the trivial-patterns list (`"yes"`, `"continue"`, `"ok"`, `"lgtm"`, etc.) skip it.
3. If the cleaned text is shorter than `_MIN_STEERING_LENGTH` (50 characters) skip it.
4. Derive the ordinal by counting prior `slash_command` and `human_steering` events already in the timeline. If the ordinal is 0, the prompt is the session's initial instruction and is skipped (not a mid-run steer).

Only prompts that pass all four gates become `human_steering` events.

### Snippet privacy

Stored snippets are capped at 120 characters (`_MAX_SNIPPET_LENGTH`). Content is run through `strip_private` before storage. The distilled Memory content is capped at 500 characters (`_MAX_CONTENT_LENGTH`).

## Data Flow

```
UserPromptSubmit hook
        |
        +-- capture_prompt_event(session_id, prompt, cwd=cwd)
                |
                +-- Is prompt a slash command?
                |       Yes → record {"type": "slash_command", "command": ...}
                |
                +-- Is it substantive steering?
                        strip_private → trivial gate → length gate → ordinal gate
                        Pass → record {"type": "human_steering", "ordinal": N, "snippet": ...}
                        Fail → no-op

    [session runs; telemetry JSONL accumulates events across all types]

Stop hook
        |
        +-- _run_tui_interaction_capture(session_id, cwd)
                |
                +-- resolve project_key from cwd (same logic as memory_bridge)
                |
                +-- summarize_and_store(session_id, project_key)
                        |
                        +-- read_session_timeline(session_id)    [JSONL]
                        |
                        +-- tally: slash_commands, steering_ordinals, tool_count, idle_gaps
                        |
                        +-- skip write if NO slash_commands AND NO steering_ordinals
                        |
                        +-- _compose_pattern_string(...)
                        |   → "In a 47-event session, human ran /do-plan → /do-build,
                        |      steered once at turn 4, approved 12 tools."
                        |
                        +-- Memory.safe_save(
                                agent_id="tui-{session_id}",
                                project_key=project_key,
                                content=pattern_str,
                                importance=1.0,
                                source=SOURCE_HUMAN,
                                metadata={"category": "pattern", "tags": ["tui-interaction"]}
                            )
```

## Memory Record Shape

| Field | Value |
|-------|-------|
| `agent_id` | `tui-{session_id}` |
| `project_key` | resolved from `cwd` (same `_get_project_key` logic as `memory_bridge.py`) |
| `content` | compact natural-language pattern string, capped at 500 chars |
| `importance` | 1.0 |
| `source` | `SOURCE_HUMAN` |
| `metadata.category` | `"pattern"` |
| `metadata.tags` | `["tui-interaction"]` |

The `tui-{session_id}` namespace separates these records from the Haiku content observations the Stop hook also writes. Both write to the same Redis Memory model and are recallable via standard memory tooling.

### Recalling interaction patterns

```bash
# Search by tag
python -m tools.memory_search search "slash command" --tag tui-interaction

# Search by category
python -m tools.memory_search search "do-plan do-build" --category pattern
```

MCP tools (available inside Claude Code sessions):

```
memory_search(query="tui interaction patterns", tag="tui-interaction")
memory_get(memory_id)
```

## Pattern String Format

`_compose_pattern_string` assembles a single sentence from whichever signals are present:

```
"In a {N}-event session, human ran /do-plan → /do-build → /do-test, steered once at turn 4, approved 12 tools, 1 idle-gap interrupt of 90s."
```

Parts are omitted when the corresponding data is absent. If no slash commands and no steering are present, `summarize_and_store` skips the Memory write entirely rather than saving a noise record.

## Documented Gaps

These are known, accepted limitations:

**Tool rejections are invisible.** `PostToolUse` fires only after an approved tool call completes. If the user rejects a tool request, no hook fires and the rejection is not captured. Capturing rejections would require a `ToolInput`-level hook that does not currently exist.

**True ESC-interrupts are not captured.** When a user presses ESC to interrupt an in-progress agent turn, Claude Code does not fire a hook event. The telemetry recorder approximates interruption via `idle_gap` events (periods of silence exceeding 60 seconds), which are tallied in the pattern string. This is an approximation, not a reliable interrupt signal.

## Fail-Silent Guarantee

Both `capture_prompt_event` and `summarize_and_store` wrap their entire bodies in `try/except Exception` and log at DEBUG. Neither function ever raises. A Redis outage, a missing session timeline, or a Memory model failure all resolve to silent no-ops. The TUI and the hook pipeline are never blocked.

## Key Files

| File | Purpose |
|------|---------|
| `agent/tui_interaction_capture.py` | Module with `capture_prompt_event` and `summarize_and_store` public functions |
| `.claude/hooks/user_prompt_submit.py` | Calls `capture_prompt_event` after memory ingest |
| `.claude/hooks/stop.py` | `_run_tui_interaction_capture` helper calls `summarize_and_store` at session end |
| `agent/session_telemetry.py` | JSONL recorder; provides `record_telemetry_event` and `read_session_timeline` |

## Related

- [Subconscious Memory](subconscious-memory.md) — the Memory model this feature writes to; recall, category weights, and consolidation
- [Session Telemetry](session-telemetry.md) — the JSONL recorder this feature extends with two new event types
- [Claude Code Memory](claude-code-memory.md) — the broader hook pipeline in which this feature participates
- [Stall Advisory Classifier](stall-advisory-classifier.md) — Pillar 1 of #1536; also reads session telemetry
- [Crash-signature auto-resume](crash-signature-auto-resume.md) — Pillar 2 of #1536

## Tracking

- Issue: [#1540](https://github.com/tomcounsell/ai/issues/1540) (Pillar 3 of epic #1536)
- Plan: `docs/plans/tui-interaction-capture.md`
