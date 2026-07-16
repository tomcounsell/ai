# Harness Abstraction

**Status**: Shipped (Phases 1-5 of issue #780, PRs #868 and #902)

## Overview

All session types (Eng, Teammate) now execute via the CLI harness (`claude -p --output-format stream-json`). The original `DEV_SESSION_HARNESS` environment variable is preserved for historical compatibility, but all session types are unconditionally routed to `get_response_via_harness()`. The SDK path (`ValorAgent`, `get_agent_response_sdk()`) was deleted wholesale in #2000 — see [HarnessAdapter Seam](harness-adapter.md).

**Phase 6 (end-to-end validation)** is complete — all session types run through the CLI harness in production.

## How It Works

### Harness Selection

All session types are unconditionally routed to `get_response_via_harness()` in `agent/agent_session_queue.py`. The `DEV_SESSION_HARNESS` environment variable is preserved but no longer gates routing for Eng or Teammate sessions — they always run via the CLI harness.

| Session Type | Execution Path |
|-------------|---------------|
| Eng | `get_response_via_harness()` — CLI harness, always |
| Teammate | `get_response_via_harness()` — CLI harness, always |

### Routing Path

```
Worker receives pending AgentSession (any session_type)
    |
    v
get_response_via_harness()   [all types: Eng, Teammate]
    |
    v
claude -p subprocess
    |
    v
stream-json stdout parsing
    |
    v
Final result string returned
```

### Streaming and Batching

`get_response_via_harness()` in `agent/sdk_client.py` spawns `claude -p --output-format stream-json` as an async subprocess and parses stdout line-by-line:

- **`content_block_start` events**: Resets the internal text buffer — only the current block is retained, never a concatenation across blocks or turns
- **`content_block_delta` events**: Text chunks accumulate into the current block buffer
- **`result` event**: Contains the final response text and a `session_id`. The session_id is persisted on the `AgentSession.claude_session_uuid` field and reused on the next turn via `--resume` (see [Harness Session Continuity](harness-session-continuity.md))
- **Interruption (no `result` event)**: If the subprocess is killed before emitting `result` (e.g. crash or SIGTERM), the function returns `""` and logs at `ERROR` level. `BackgroundTask` skips the send on empty string — nothing reaches Telegram
- **Error handling**: Malformed JSON lines are skipped; non-zero exit codes are logged with stderr

The function returns the final result text only — it has no streaming callback parameter.

### Session Continuity and Context Budget (issues #958, #976)

`get_response_via_harness()` uses two complementary mechanisms to keep the subprocess argv bounded:

1. **Native `--resume` continuity (#976)** — On the first turn the captured `session_id` is persisted to `AgentSession.claude_session_uuid`. On subsequent turns the harness looks up the prior UUID and spawns `claude -p --resume <uuid> ... [raw_new_message]`; the binary loads prior context from its on-disk session file and the positional argv is just the new user message. Context overflow becomes structurally impossible for resumed turns. See [Harness Session Continuity](harness-session-continuity.md) for the two argv shapes, the stale-UUID fallback, and observability log lines.
2. **Context budget cap (#958)** — `_apply_context_budget(message)` runs unconditionally before every spawn (first and resumed turns alike). If the assembled input exceeds `HARNESS_MAX_INPUT_CHARS` (100,000 characters), the oldest context is trimmed from the start of the string. The `MESSAGE:` boundary is preserved unconditionally — the steering message is never truncated. A `[CONTEXT TRIMMED]` marker is prepended so the agent is aware context was omitted.

The budget remains in place even on resumed turns because a single new user message can still carry a forwarded transcript or pasted log that alone exceeds the chunk limit. Together the two mechanisms eliminated the `Separator is found, but chunk is longer than limit` crashes that previously affected long Telegram threads. The cap is a module-level constant in `agent/sdk_client.py` and can be raised by editing it without other code changes.

### Streaming Chunk Suppression

`get_response_via_harness()` does not accept a streaming callback. Intermediate `content_block_delta` chunks are accumulated internally and never forwarded to any output transport mid-session. This applies equally to all transports — Telegram (`TelegramRelayOutputHandler`) and email (`EmailOutputHandler`) — no transport receives real-time streaming output.

Forwarding streaming chunks would bypass the nudge loop and cause mid-sentence message fragments to appear as discrete messages. The final result is delivered by `BackgroundTask` through the nudge loop, ensuring complete, coherent messages reach the user regardless of session type (Eng or Teammate).

### Startup Health Check

When `DEV_SESSION_HARNESS` is set to a non-`sdk` value, the worker runs `verify_harness_health()` at startup:

1. Checks the binary exists on `PATH` via `shutil.which()`
2. Runs a minimal test command (`claude -p --output-format stream-json "test"`)
3. Verifies a `system` init event appears in the output
4. Logs a warning if `apiKeySource` indicates API key billing (the point of the CLI harness is to use subscription billing)
5. On failure: logs a warning but does not block startup -- the health check is advisory only (there is no SDK fallback path since #2000)

### Security and Session Context

- `ANTHROPIC_API_KEY` is explicitly stripped from the subprocess environment to prevent the CLI from using API billing when a subscription is available
- `AGENT_SESSION_ID` and `CLAUDE_CODE_TASK_LIST_ID` are passed to all session types for session isolation
- `VALOR_PARENT_SESSION_ID` is injected for Eng and Teammate sessions, enabling child subprocesses (spawned via `valor_session create --parent` or the Agent tool) to link their `AgentSession` records back to the parent session in `user_prompt_submit.py`.

### Session Environment Injection (issue #1148)

The harness path mirrors the env contract that the now-deleted SDK-era `ValorAgent.env` builder provided (removed in #2000). `agent/session_executor.py` constructs `_harness_env` with the following keys, scoped by session type:

| Env Var | Scope | Source | Consumer |
|---------|-------|--------|----------|
| `AGENT_SESSION_ID` | All typed sessions | `session.agent_session_id` | Hooks (`pre_tool_use.py`, `user_prompt_submit.py`); session isolation |
| `CLAUDE_CODE_TASK_LIST_ID` | All typed sessions | Tier 1 thread-derived or Tier 2 slug | Task list isolation per `docs/features/session-isolation.md` |
| `SESSION_TYPE` | All typed sessions | `session.session_type` (`eng`/`teammate`) | `agent/hooks/pre_tool_use.py::_is_teammate_session()` — drives the Teammate Bash allowlist + write restrictions |
| `VALOR_PARENT_SESSION_ID` | Eng, Teammate | `session.agent_session_id` | Child subprocess linkage (`user_prompt_submit.py`) |
| `TELEGRAM_CHAT_ID` | Eng, Teammate (when `chat_id` set) | `session.chat_id` | `tools/send_message.py` for agent-side message sends |
| `SENTRY_AUTH_TOKEN` | Eng, Teammate | `agent/sdk_client.py::_resolve_sentry_auth_token()` | `sentry-cli` (no manual export needed) |

Sentry token resolution cascade: `SENTRY_PERSONAL_TOKEN` env var → `SENTRY_AUTH_TOKEN` env var → `~/Desktop/Valor/.env` file read. Under `VALOR_LAUNCHD=1` the file read is skipped (macOS TCC blocks `open()` on `~/Desktop` files under launchd).

### Engineer persona injection — `--append-system-prompt` (issue #1148)

> **Granite PTY sessions (all bridge-originated sessions) no longer use this path.** As of issue #1692, persona is delivered to the granite PTY container via prime commands (`.claude/commands/granite/prime-*-role.md`) at PTY startup — `--append-system-prompt` is not set at all. The description below applies to the headless `claude -p` path only (direct `get_response_via_harness()` callers, drafter turns, non-bridge tool invocations).

Eng sessions append the engineer persona to `claude -p`'s default system prompt via `--append-system-prompt`:

1. The executor calls `agent.sdk_client.load_eng_system_prompt(working_dir)` (the WORKER-access branch in `agent/session_executor.py`) — returns the composed prompt (WORKER_RULES + engineer persona + principal/completion sections) plus the project-specific work-vault `CLAUDE.md` if present.
2. The result is passed as `system_prompt=` to `get_response_via_harness()`.
3. `get_response_via_harness()` injects `["--exclude-dynamic-system-prompt-sections", "--append-system-prompt", <text>]` into the harness argv after `--model` and before the positional message.

`--append-system-prompt` (not `--system-prompt`) preserves Claude Code's default tool-handling protocol — the engineer persona is additive guidance. Defensive 512KB cap avoids macOS `ARG_MAX` overflows; oversized prompts are dropped with a `logger.warning` and the session continues without the persona (degraded but functional).

Failure modes:
- Persona load raises (e.g. missing persona file on a fresh machine): logs `[eng-persona-missing]` and proceeds with `system_prompt=None`. Session runs without SDLC orchestration rules — visible to the dashboard via the structured log prefix.
- Drafter call sites in `agent/session_completion.py` (Pass 1 + Pass 2 of `_deliver_pipeline_completion`) MUST keep `system_prompt=None`. Tainting drafter turns with engineer orchestration corrupts the user-facing summary. Enforced by `tests/unit/test_session_completion.py::test_drafter_calls_omit_system_prompt` and the AST/anchor guards alongside it.

Teammate sessions do not take the WORKER persona-loader branch; they keep the default Claude Code protocol (their persona overlay, when set, is loaded via the non-WORKER path).

### Prompt Cache Stabilization — `--exclude-dynamic-system-prompt-sections` (issue #1227)

Eng sessions include a large system prompt (~74K chars) via `--append-system-prompt`. Prior to issue #1227, every eng session paid a 15–20 minute cold-start TTFT because Anthropic's server-side prompt cache could not reuse the prefix — the default system prompt includes per-machine dynamic sections (cwd, env info, memory paths, git status) that differ between machines and sessions.

**Fix:** `get_response_via_harness()` now injects `--exclude-dynamic-system-prompt-sections` alongside `--append-system-prompt` for every session that carries a system prompt. This flag (built into the `claude` CLI) moves the dynamic sections into the first user message, leaving the system-prompt prefix stable across consecutive sessions on the same machine in the same `working_directory`.

**Result:** The second eng session within a 5-minute window (Anthropic's cache TTL) hits the server-side cache and completes its first turn in < 90 seconds instead of 15–20 minutes. Cache hits are logged via `cache_read_input_tokens` in `logs/cold_start_metrics.jsonl`.

**Ordering invariant:** `--exclude-dynamic-system-prompt-sections` must precede `--append-system-prompt` in the argv. `get_response_via_harness()` enforces this ordering. Tests in `TestGetResponseViaHarnessSystemPrompt::test_exclude_dynamic_sections_present_when_system_prompt` assert the ordering contract.

### TTFT Measurement — `agent/cold_start_metrics.py` (issue #1227)

Every first-turn harness invocation (no `--resume`) writes a JSONL entry to `logs/cold_start_metrics.jsonl` via `agent.cold_start_metrics.record_ttft()`. The entry captures:
- Spawn → first-stdout-byte elapsed time (`ttft_seconds`)
- Session metadata: `session_id`, `session_type`, `working_dir`, `prompt_chars`, `model`
- Cache hit indicator: `cache_read_input_tokens` (from `result` event's usage dict)

All writes are best-effort: any failure is silently swallowed. The instrumentation MUST NOT block the worker or the user. See `agent/cold_start_metrics.py` for the full schema and `docs/features/pm-channels.md#cold-start-ttft-mitigation-issue-1227` for the measurement commands (the eng-session system-prompt wiring is documented there).

## Harness Command Registry

New harnesses are added to `_HARNESS_COMMANDS` in `agent/sdk_client.py`:

```python
_HARNESS_COMMANDS = {
    "claude-cli": ["claude", "-p", "--output-format", "stream-json", ...],
    "opencode": ["opencode", "--non-interactive"],
}
```

Setting `DEV_SESSION_HARNESS=opencode` routes sessions to the opencode binary with no other code changes.

## Configuration

```bash
# In .env or shell environment
DEV_SESSION_HARNESS=sdk          # Historical default name; SDK path no longer exercised
DEV_SESSION_HARNESS=claude-cli   # Use claude -p CLI harness
```

No changes to the `AgentSession` model are needed. Harness selection is purely a worker-level configuration concern.

## Key Files

| File | What changed |
|------|-------------|
| `agent/sdk_client.py` | `get_response_via_harness()`, `verify_harness_health()`, `_HARNESS_COMMANDS` registry, `_apply_context_budget()`, `HARNESS_MAX_INPUT_CHARS`; `_get_prior_session_uuid()` / `_store_claude_session_uuid()` / `_UUID_PATTERN` for `--resume` continuity (#976); `build_harness_turn_input(skip_prefix=...)` for the two argv shapes |
| `agent/agent_session_queue.py` | Routing logic in `_execute_agent_session()`: all session types go through the CLI harness; looks up prior UUID via `_get_prior_session_uuid()` and passes both full-context and minimal message forms to `get_response_via_harness()` so the stale-UUID fallback can retry without `--resume` |
| `worker/__main__.py` | Startup health check when harness is non-`sdk` |
| `agent/__init__.py` | Exports `get_response_via_harness` and `verify_harness_health` |
| `tests/unit/test_harness_streaming.py` | Unit tests covering NDJSON parsing, text accumulation, error paths, health checks (isolation scope only — no send_cb) |
| `tests/integration/test_harness_no_op_contract.py` | Integration test asserting the no-op delivery contract: output handler called exactly once (final result), never during streaming |

## Completion Handling

After `get_response_via_harness()` returns, the worker calls `complete_transcript()`, which runs `_finalize_parent_sync()` synchronously. Under the unified eng-session model that is the whole completion story — there is no second post-completion handler call. The old two-call sequence (`complete_transcript()` then a separate `_handle_dev_session_completion()`) and its associated ordering invariant (issue #987) are gone: the dedicated dev-completion handler (`_handle_dev_session_completion`) and the PM-continuation creator (`_create_continuation_pm`) were deleted when the PM and Dev roles merged into one `eng` session that both orchestrates and executes SDLC work in-process.

## Engineer persona drift guard

The engineer persona overlay is loaded by `load_persona_prompt("engineer")` and runs through a set of startup drift guards in `agent/sdk_client.py` (around lines 940–980). These greps fire `logger.warning` if the per-machine overlay at `~/Desktop/Valor/personas/engineer.md` has fallen out of sync with the in-repo template:

- Missing `CRITIQUE` gate rules.
- Missing the bucket-#3 workflow-announcement clause (`"Unless you directly instruct me to skip"`).
- Stale `subagent_type="dev-session"` dispatch strings — eng sessions are now created via `python -m tools.valor_session create --role eng`, not the Agent tool, so any lingering dev-session dispatch in the overlay is flagged for removal.
- Missing the `Mode 3` parallel-orchestrator playbook.

The `/update` drift check that compares the in-repo template against the private overlay is `scripts/update/persona_drift.py` (`check_pm_persona_drift`, name retained for historical reasons), targeting `config/personas/engineer.md` vs `~/Desktop/Valor/personas/engineer.md`.

## Hook Cleanup (Phase 5)

PR #902 simplified hook infrastructure as part of the harness migration:

- **`agent/hooks/session_registry.py`** deleted (250 lines) — UUID-to-bridge-session mapping no longer needed; hooks now use `AGENT_SESSION_ID` env var directly
- **`subagent_stop.py`** stripped to logging only — `_register_dev_session_completion`, `_record_stage_on_parent`, `_post_stage_comment_on_completion` deleted; SDLC tracking moved to worker post-completion handler. Subsequently deleted entirely in issue #1024 once the broader SDK path was confirmed fully unreachable.
- **`pre_tool_use.py`** — `_maybe_start_pipeline_stage` and Agent tool interception deleted; `_handle_skill_tool_start()` now uses `AGENT_SESSION_ID` env var instead of `session_registry.resolve()`
- **`dev-session` entry** deleted from `agent_definitions.py` — dev sessions are created as AgentSession records, not via the Agent tool
- `agent/pipeline_state.py` and `agent/pipeline_graph.py` are now shims that re-export from `agent/pipeline_state.py` and `agent/pipeline_graph.py` (canonical locations after Phase 3 move)

## Pending Work (Phase 6)

- **Phase 6**: End-to-end validation — production validation with `DEV_SESSION_HARNESS=claude-cli` running a full SDLC pipeline end-to-end
