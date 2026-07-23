# Claude Code Memory Integration

Hook-based memory integration that extends the subconscious memory system to Claude Code CLI sessions. User prompts are ingested as memories, tool calls trigger memory recall with compact stub injection, and session transcripts are extracted for observations on session stop. Full memory bodies are pulled on demand via the `memory_get` and `memory_search` MCP tools (see [Progressive Disclosure](#progressive-disclosure-stub-injection-and-memory-mcp-tools) below).

## Architecture

```
Claude Code CLI Session
        |
        +-- UserPromptSubmit hook --> memory_bridge.ingest()
        |                               |
        |                               v
        |                         Quality filter (length, trivial patterns)
        |                               |
        |                               v
        |                         Bloom dedup --> Memory.safe_save(provisional, importance=PROVISIONAL_INGEST_IMPORTANCE)
        |
        |   (then, same hook)
        |
        +-- UserPromptSubmit hook --> memory_bridge.prefetch()
        |                               |
        |                               v
        |                         Strip PM boilerplate, gates (length, trivial)
        |                               |
        |                               v
        |                         _recall_with_query(prompt) --> <thought> blocks
        |                                                        via hookSpecificOutput
        |                                                        (additionalContext)
        |
        +-- PostToolUse hook --> memory_bridge.recall()
        |                           |
        |                           v
        |                     File-based sliding window (JSON sidecar)
        |                           |
        |                     Every 3 tool calls:
        |                           v
        |                     Keyword extraction --> Bloom pre-check
        |                           |                    |
        |                     [bloom miss]          [bloom hit]
        |                           |                    |
        |                     Deja vu signals      ContextAssembler query
        |                                                |
        |                                                v
        |                                         <thought> blocks
        |                                         via additionalContext
        |
        +-- Stop hook --> memory_bridge.extract()
        |                     |
        |                     v
        |               Read transcript --> Haiku extraction
        |                     |                  |
        |                     v                  v
        |               Outcome detection   Categorized observations
        |               (injected thoughts)  saved as Memory records
        |                     |
        |                     v
        |               Sidecar cleanup
        |
        +-- Stop hook --> tui_interaction_capture.summarize_and_store()
                              |
                              v
                        Distill the session's TUI interaction shape into one
                        `pattern` Memory tagged `tui-interaction`
                        (see docs/features/tui-interaction-capture.md)
```

The Stop hook also drives **TUI interaction capture** (Pillar 3 of #1536): a
separate, fail-silent `summarize_and_store()` call distills the session's
human-in-the-loop interaction shape — slash-command sequence, mid-run steering,
tool-approval tally, idle-gap interrupts — into one retrievable `pattern` Memory.
This is an *interaction-shape* observation, distinct from the *content*
observations Haiku extraction produces. See
[`tui-interaction-capture.md`](tui-interaction-capture.md).

## How It Works

### Ingestion (UserPromptSubmit Hook)

The `user_prompt_submit.py` hook fires on every user prompt in Claude Code. It passes the prompt to `memory_bridge.ingest()`, which:

1. Rejects prompts shorter than 50 characters
2. Rejects trivial patterns ("yes", "continue", "ok", "lgtm", etc.)
3. Checks the bloom filter for duplicate content
4. Saves qualifying prompts as **provisional** Memory records at `PROVISIONAL_INGEST_IMPORTANCE` (no LLM call, so the hook's deadline is never at risk) -- unlike the Telegram path, which still saves verbatim at flat `importance=6.0`. A standing reflection distills the provisional record into a fact with content-derived importance out of band. See [Distilled Human Ingest](subconscious-memory.md#distilled-human-ingest-phase-3) for the full design.

Registered in `.claude/settings.json` with a 15-second timeout, running after the calendar prompt hook.

### First-turn Prefetch (UserPromptSubmit Hook)

After `ingest()`, the same `user_prompt_submit.py` hook calls `memory_bridge.prefetch(session_id, prompt, cwd)` (added in issue [#1180](https://github.com/tomcounsell/ai/issues/1180)). Unlike `recall()` -- which buffers tool calls and queries every `WINDOW_SIZE=3` -- prefetch runs immediately so the agent receives memory thoughts on the very first turn, before any tool fires.

The hook emits the result as a `hookSpecificOutput` JSON object on stdout. The `additionalContext` carries one or more compact stubs — the same format the PostToolUse `recall()` path uses — not full memory bodies (see [Progressive Disclosure](#progressive-disclosure-stub-injection-and-memory-mcp-tools)):

```json
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "<thought id=\"mem_xyz\">[decision] one-line title</thought>"}}
```

Claude Code prepends `additionalContext` to the agent's first system message.

**Behavior:**

1. Strips `FROM:`/`SCOPE:`/`MESSAGE:` boilerplate from worker-spawned PM/Teammate prompts so BM25 ranks against the user payload, not the routing template.
2. Applies the same `MIN_PROMPT_LENGTH` and `TRIVIAL_PATTERNS` gates as `ingest()`.
3. Runs a single `_recall_with_query(prompt, project_key, exclude_ids)` call -- no clustering, since the prompt is already a coherent query.
4. Suppresses the deja vu fallback (`bloom_check_emit_dejavu=False`) -- novel-territory thoughts on the user-visible first turn are pure noise (issue [#627](https://github.com/tomcounsell/ai/issues/627)).
5. Times the call; logs a warning when elapsed exceeds `PREFETCH_LATENCY_WARN_MS = 200` (in `config/memory_defaults.py`).
6. Appends surfaced memory IDs to the shared sidecar `injected[]` list, preserving the `count` and `buffer` fields owned by `recall()`.

**De-dup contract:** subsequent PostToolUse `recall()` cycles read the same sidecar and skip any memory ID already in `injected[]`. The SDK-side `agent/memory_hook.check_and_inject()` (which fires inside worker-spawned subprocesses) accepts an optional `claude_uuid` parameter and reads the same sidecar via `_load_hooks_sidecar_injected_ids()`. The watchdog hook in `agent/health_check.py` passes Claude Code's `input_data["session_id"]` as `claude_uuid` so SDK-side recall never re-surfaces a prefetched memory.

**`claude_uuid="unknown"` guard:** when `input_data["session_id"]` is absent, Claude Code defaults `claude_uuid` to the literal `"unknown"`. The SDK-side loader skips sidecar reads when `claude_uuid` is empty or equals `"unknown"`, preventing every malformed-payload session from sharing `data/sessions/unknown/memory_buffer.json`.

**Failure mode:** silent. `prefetch()` returns `None` on any error; the hook prints nothing and prompt submission continues unaffected.

### Recall (PostToolUse Hook)

The `post_tool_use.py` hook calls `memory_bridge.recall()` after its existing SDLC state tracking. The recall system uses a file-based sliding window since hooks run as stateless processes:

1. Each tool call is appended to a JSON sidecar file at `data/sessions/{session_id}/memory_buffer.json`
2. The buffer is capped at 9 entries (BUFFER_SIZE)
3. Every 3rd tool call (WINDOW_SIZE), keywords are extracted from the buffer
4. Keywords are checked against the Memory bloom filter; the gate requires at least `BLOOM_MIN_HITS = 2` distinct token hits before BM25 + RRF runs (the `bloom_hits == 0` deja-vu / novel-territory branch is preserved)
5. On bloom-gate pass, ContextAssembler queries Redis for relevant memories with `min_rrf_score=RRF_MIN_SCORE` so post-fusion records below the relevance floor are dropped before hydration
6. Up to 3 matching memories are formatted as compact stub blocks `<thought id="mem_xyz">[category] title</thought>` and returned via the hook's `additionalContext` response field. The agent calls `memory_get(memory_id)` (MCP tool) to pull the full body when a stub looks worth reading.
7. Injected memory IDs **with their full content** are recorded in the sidecar's `injected[]` for later outcome detection — bigram-overlap detection needs the full string to distinguish acted / used / dismissed

The PostToolUse hook has a 5-second timeout. Memory operations (Redis-only) complete in under 15ms. See [Subconscious Memory: Relevance Threshold](subconscious-memory.md#relevance-threshold) for the calibration math behind `RRF_MIN_SCORE` and `BLOOM_MIN_HITS`.

### Deja Vu Signals

When recall produces ambiguous results, the system emits contextual signals instead of silence:

- **Vague recognition**: When 3+ bloom filter hits occur but ContextAssembler returns no records above the surfacing threshold, the hook injects: `<thought>I have encountered something related to [topic] before, but the details are unclear.</thought>`
- **Novel territory**: When 7+ unique keywords produce zero bloom hits, the hook injects: `<thought>This is new territory -- I should pay attention to what works here.</thought>`

These thresholds are controlled by `DEJA_VU_BLOOM_HIT_THRESHOLD` and `NOVEL_TERRITORY_KEYWORD_THRESHOLD` in `memory_bridge.py`.

### Extraction (Stop Hook)

The `stop.py` hook calls `memory_bridge.extract()` after backing up the session transcript. Extraction:

1. Reads the session transcript from the path provided in hook input
2. Truncates to 8000 characters for the Haiku API call
3. Runs `extract_observations_async()` to save categorized observations (corrections, decisions, patterns, surprises)
4. Reads injected thought IDs from the sidecar file
5. Runs `detect_outcomes_async()` to classify each injected memory as `"acted"` (drove response), `"used"` (consumed but did not drive response), or `"dismissed"` (no relationship). LLM judgment (Haiku) is primary; bigram overlap is the fallback when the LLM call fails
6. Cleans up all sidecar files for the session

The Stop hook has a 10-second timeout. Haiku extraction typically completes in 2-3 seconds.

## Progressive Disclosure (Stub Injection and Memory MCP Tools)

Recall (PostToolUse `recall()`) and prefetch (UserPromptSubmit `prefetch()`) emit compact stubs into the agent's context, not full memory bodies. The agent reads category + title to decide whether the memory is worth pulling, then calls the `memory_get` MCP tool for the full content of any stub it actually wants.

**Stub format** (from `_format_stub_blocks` in `memory_bridge.py`):

| When | Format |
|------|--------|
| `record.title` populated | `<thought id="{memory_id}">[{category}] {title}</thought>` |
| `record.title` empty (race or Ollama down) | `<thought id="{memory_id}">[{category}]</thought>` |

**Sidecar `injected[]` keeps full content.** Even though the agent sees a stub, the sidecar at `data/sessions/{session_id}/memory_buffer.json` stores `(memory_id, full_content)` for every injected memory. The Stop hook's `detect_outcomes_async()` reads this list to run bigram-overlap outcome detection against the agent's response — stubs alone would collapse the act / used / dismissed signal that drives `dismissal_count`, `outcome_history`, and importance decay.

**Token reduction:** the contract is `≥5×` smaller per injection, asserted in `tests/integration/test_memory_stub_injection.py::test_stub_format_at_least_5x_token_reduction` via `tiktoken.get_encoding("cl100k_base")`.

### `memory_get` and `memory_search` MCP tools

`mcp_servers/memory_server.py` is a stdio FastMCP server that Claude Code spawns per session via the entry registered in `~/.claude.json` under `mcpServers.memory`:

```python
memory_get(memory_id: str) -> dict
# {memory_id, content, title, category, tags, importance, source}
# or {"error": "memory not found: <id>"} / {"error": "memory_id required"}

memory_search(query: str, category=None, tag=None, limit=5) -> dict
# {"results": [{"id", "category", "title", "score"}, ...], "error": None}
# limit is clamped to [1, 50]; empty query returns empty results without raising.
```

`memory_search` returns more stubs (id / category / title / score). The agent calls `memory_get(id)` to pull the body for any stub it actually wants. The cold-start budget is asserted in `tests/integration/test_memory_mcp_server.py::test_cold_start_latency`; module imports happen lazily inside tool bodies to keep startup fast.

**Decision rule for the agent:**

- **`memory_get(memory_id)`** when you already have a stub ID (from `additionalContext` injection or a prior `memory_search` result) and the title suggests the body is worth reading.
- **`memory_search(query, ...)`** when you're hunting for memories the auto-injection didn't surface — e.g. mid-task you realize you need prior context on a topic the keyword filter missed.
- Avoid pulling bodies "just in case" — the token-reduction win comes from selective fetches.

### Title generation

The stub label comes from a `Memory.title` field populated asynchronously by a local LLM. `tools/memory_search/title_generator.generate_title_async(memory_id, content)` spawns a daemon thread that calls Ollama via `settings.models.ollama_generation_model` (default `gemma4:31b-cloud`; env `MODELS__OLLAMA_GENERATION_MODEL`) with a 5s timeout and writes back the normalized title via `record.save()`. The function returns synchronously — writers never block. A defensive `<private>`-strip runs inside `_do_generate` before the HTTP call.

Title generation is wired at every Memory writer call site in this hook path (no model-layer hook):

- `.claude/hooks/hook_utils/memory_bridge.py::ingest()` after `Memory.safe_save` — content already passes through `strip_private`.
- See [Subconscious Memory: Title generation](subconscious-memory.md#title-generation) for the full list of 7 writer call sites and the design rationale.

**Graceful degradation:** if Ollama is unreachable or the configured generation model is unavailable (cloud or local), no title is written and stubs render as `[category]` only on next recall. The agent can still call `memory_get(memory_id)` to pull the full body — recall function is unaffected. `scripts/update/run.py` Step 4.8 pings Ollama on every `/update` and logs a non-fatal warning if the generation model is unavailable.

### MCP registration in `~/.claude.json`

`scripts/update/mcp_memory.py` writes (and self-heals) the entry under `mcpServers.memory` on every `/update` invocation. Acquires `fcntl.flock` (`LOCK_EX` for write, `LOCK_SH` for `--verify`), backs up to `~/.claude.json.bak`, writes to `.tmp`, then atomically renames. Idempotent — if the existing entry already matches, no rewrite happens. PYTHONPATH is resolved per-machine via `git rev-parse --show-toplevel`.

For one-time setup or troubleshooting:

```bash
# Verify (read-only) — reports drift but does not write
python scripts/update/run.py --verify

# Repair (writes if missing or drifted)
python scripts/update/run.py --full

# Backfill titles for pre-existing records
python scripts/backfill_memory_titles.py --dry-run
python scripts/backfill_memory_titles.py
```

## AgentSession Lifecycle Tracking

Worker-spawned Claude Code sessions create AgentSession records in Redis, providing dashboard observability on par with Telegram-originated sessions. The lifecycle is managed across three hooks using a sidecar file to share the AgentSession `agent_session_id`:

> **Note:** Direct CLI sessions (developer running `claude` at the terminal) do **not** create AgentSession records. The UserPromptSubmit hook gates creation on the presence of `SESSION_TYPE` or `VALOR_PARENT_SESSION_ID` environment variables, which are only set by `sdk_client.py` for worker-spawned sessions (issue [#1001](https://github.com/tomcounsell/ai/issues/1001)). Memory extraction, transcript backup, and all other hook functionality continue to work normally for direct CLI sessions — they simply have no AgentSession record.

1. **UserPromptSubmit hook**: On the first prompt of a session, the hook decides between two paths:
   - **Worker-spawned subprocess (attach path, issue [#1157](https://github.com/tomcounsell/ai/issues/1157)):** if `AGENT_SESSION_ID` or `VALOR_SESSION_ID` resolves to a live (non-terminal) AgentSession, the hook writes that session's `agent_session_id` into the sidecar and returns. **No new record is created.** The worker has already created the authoritative AgentSession record before spawning the subprocess, and the env vars communicate "I already own you." Before this guard landed, the hook would mint a `local-*` phantom twin for every worker-spawned PM/Teammate subprocess, causing `wait-for-children` to terminate instantly on a self-referential phantom child.
   - **Direct-CLI fallback (create path):** if neither env var resolves, the hook falls through to the existing gate and calls `AgentSession.create_local(session_type=..., ...)` with `status="running"` and `session_id=f"local-{claude_session_id}"`. This path is reserved for direct-CLI users (developer running `claude` at the terminal with `SESSION_TYPE=dev` exported). The `SESSION_TYPE` env var determines the persona; `VALOR_PARENT_SESSION_ID` (if set) links to a parent.
   - **Terminal-session safety (preserves [#1113](https://github.com/tomcounsell/ai/issues/1113)):** if the resolved env-var target is in a terminal state (killed/completed/failed/abandoned/cancelled), the hook falls through to the create path rather than re-activating. Terminal sessions are operator-resume-only.
2. **PostToolUse hook**: On every tool call, reads `agent_session_id` from the sidecar. Primary lookup via `AgentSession.get_by_id()` (fast path for attached worker sessions); falls back to `query.filter(session_id=f"local-{claude_session_id}")` reconstruction for direct-CLI sessions. Updates `updated_at` timestamp and increments `tool_call_count` on the resolved record.
3. **Stop hook**: Reads `agent_session_id` from the sidecar. Same primary/fallback lookup pattern as PostToolUse. Sets `completed_at` and marks status as `completed` (or `failed` if `stop_reason` is "error" or "crash") on the resolved record.

The dashboard at `localhost:8500` picks up local sessions automatically via `AgentSession.query` -- no dashboard code changes were needed. Local sessions appear alongside Telegram sessions with correct status, timestamps, and project key.

The `AgentSession.create_local(...)` call requires only `session_id`, `project_key`, and `working_dir`. The `session_type` defaults to `"dev"` but is overridden by the `SESSION_TYPE` env var when set. Local sessions omit all Telegram-specific fields (no `chat_id` or `parent_agent_session_id`).

> **Attach-vs-create is strict prevention.** After issue #1157, worker-spawned subprocesses produce exactly ONE AgentSession row (the worker-created one). The UserPromptSubmit hook attaches via env vars; no phantom `local-*` twin is ever written to Redis. The `create_local()` method body is untouched — only the call-site precondition changed.

## State Management

Hooks are stateless processes -- each invocation starts fresh. State is persisted to JSON sidecar files using atomic writes (tmp file + rename):

| File | Location | Contents |
|------|----------|----------|
| Memory buffer | `data/sessions/{session_id}/memory_buffer.json` | Tool call count, rolling buffer (last 9 calls), injected thought IDs |
| Agent session | `data/sessions/{session_id}/agent_session.json` | `agent_session_id` for cross-hook lifecycle tracking, `merge_detected` flag for post-merge learning |

The memory buffer sidecar structure:
```json
{
  "count": 12,
  "buffer": [
    {"tool_name": "Read", "tool_input": {"file_path": "..."}},
    ...
  ],
  "injected": [
    {"memory_id": "abc123", "content": "..."},
    ...
  ]
}
```

The agent session sidecar structure:
```json
{
  "agent_session_id": "abc123",
  "merge_detected": true,
  "merged_pr_number": "560"
}
```

Sidecar files are cleaned up by the Stop hook after extraction. Cross-session contention is impossible because sidecar directories are session-scoped and Claude Code runs hooks sequentially within a session.

## Key Files

| File | Purpose |
|------|---------|
| `.claude/hooks/hook_utils/memory_bridge.py` | Bridge module: recall, ingest, extract, sidecar management, agent session sidecar helpers |
| `.claude/hooks/user_prompt_submit.py` | UserPromptSubmit hook for prompt ingestion and AgentSession creation |
| `.claude/hooks/post_tool_use.py` | PostToolUse hook with memory recall, SDLC state tracking, and AgentSession activity updates |
| `.claude/hooks/stop.py` | Stop hook with extraction, AgentSession completion, sidecar cleanup, and post-merge learning |
| `models/agent_session.py` | AgentSession model; `create_local()` factory for local CLI sessions (accepts `session_type` kwarg, defaults to `"dev"`) |
| `.claude/settings.json` | Hook registration (UserPromptSubmit entry) |

## Project Key Resolution

Every memory record is partitioned by `project_key` so that memories from one project (e.g. `~/src/ai`) do not surface as thoughts in a different project's sessions (e.g. `~/src/some-other-repo`).

All four public functions in `memory_bridge.py` accept a `cwd` parameter:

| Hook function | Called from | cwd source |
|---------------|-------------|------------|
| `recall(session_id, tool_name, tool_input, cwd)` | `post_tool_use.py` | `hook_input["cwd"]` |
| `ingest(content, cwd)` | `user_prompt_submit.py` | `hook_input["cwd"]` |
| `prefetch(session_id, prompt, cwd)` | `user_prompt_submit.py` | `hook_input["cwd"]` |
| `extract(session_id, transcript_path, cwd)` | `stop.py` | `hook_input["cwd"]` (read once, passed to both calls) |
| `post_merge_extract(pr_number, cwd)` | `stop.py` | same `cwd` read above |

`_get_project_key(cwd)` resolves the key using this priority order:

1. `VALOR_PROJECT_KEY` environment variable (explicit override)
2. Match `cwd` against `working_directory` entries in `~/Desktop/Valor/projects.json`
3. Derive from the directory basename (`Path(cwd).name`)
4. Fall through to `config.memory_defaults.DEFAULT_PROJECT_KEY` (value: `"default"`)

The fallback value `"default"` is a neutral sentinel. It was previously `"dm"`, which caused all hook-created memories to be mislabeled as Telegram DM-sourced. The change to `"default"` prevents silent cross-partition contamination when `cwd` is unavailable.

### One-time Migration

If you have existing Memory records under the obsolete `project_key="default"` or `project_key="dm"` partitions, run the migration script:

```bash
# Preview -- no writes
python scripts/migrate_memory_project_key.py

# Apply
python scripts/migrate_memory_project_key.py --apply
```

The script handles BOTH legacy buckets:

- `project_key="default"` — all records (legacy SDK-spawned-session writes that fell through to `config/memory_defaults.DEFAULT_PROJECT_KEY`).
- `project_key="dm"` — only mislabeled hook-source records. Genuine Telegram DM records (identified by `source="human"` AND `agent_id="dm"`) stay under `"dm"`.

All migrated records are re-keyed to `"valor"` (the canonical project_key for `~/src/ai`) via Popoto's supported `save(migrate_key=True)` path — no raw Redis. The migration is idempotent and safe to run while the bridge is running. After the issue #1171 plist deploy, this script was run once on the canonical machine: 222 records (218 default + 4 mislabeled dm) re-tagged to `valor`.

## Configuration

Constants in `memory_bridge.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `WINDOW_SIZE` | 3 | Tool calls between recall queries |
| `BUFFER_SIZE` | 9 | Max recent tool calls in sidecar |
| `MAX_THOUGHTS` | 3 | Max thought blocks per recall cycle |
| `MIN_PROMPT_LENGTH` | 50 | Minimum prompt length for ingestion |
| `DEJA_VU_BLOOM_HIT_THRESHOLD` | 3 | Bloom hits needed for vague recognition signal |
| `NOVEL_TERRITORY_KEYWORD_THRESHOLD` | 7 | Keywords with zero bloom hits needed for novel territory signal |

These mirror the values in `config/memory_defaults.py` used by the Telegram agent path.

## Error Handling

All memory operations fail silently. The bridge module wraps every public function in try/except:

- `recall()` returns None on any error -- PostToolUse continues without thought injection
- `ingest()` returns False on any error -- prompt submission proceeds normally
- `extract()` catches all exceptions -- session stop completes normally
- Corrupt sidecar files (invalid JSON) reset to empty state instead of crashing
- Redis unavailability causes all operations to skip gracefully
- All failures are logged at WARNING level to stderr

## Relationship to Agent Memory

This is a parallel path to the Telegram agent memory system, not a replacement:

| Aspect | Agent (Telegram) | Hooks (Claude Code) |
|--------|------------------|---------------------|
| State management | In-memory dicts | JSON sidecar files |
| Entry point | `agent/memory_hook.py` | `.claude/hooks/hook_utils/memory_bridge.py` |
| Recall trigger | `check_and_inject()` in health check | `recall()` called from PostToolUse hook |
| Extraction trigger | `_schedule_post_session_extraction()` in session_executor (fire-and-forget after `complete_transcript`; hotfix #1055) | `extract()` called from Stop hook |
| Ingestion | `Memory.safe_save()` in bridge | `ingest()` called from UserPromptSubmit hook |
| Deja vu signals | `check_and_inject()` emits vague recognition and novel territory thoughts | `recall()` emits identical signals |
| Post-merge learning | `extract_post_merge_learning()` in merge stage | `post_merge_extract()` triggered from Stop hook on `gh pr merge` detection |
| Session tracking | AgentSession created by bridge handler (`AgentSession.create(session_type=...)`) | AgentSession attached or created by UserPromptSubmit hook. Worker-spawned subprocesses **attach** to the worker's existing record via `AGENT_SESSION_ID` / `VALOR_SESSION_ID` env vars — no new record (issue #1157). Direct-CLI subprocesses fall through to `AgentSession.create_local(session_type=SESSION_TYPE env var, ...)` only if `SESSION_TYPE` or `VALOR_PARENT_SESSION_ID` is set; otherwise no AgentSession is created. |
| Category re-ranking | `_apply_category_weights()` in `check_and_inject()` | `_apply_category_weights()` imported from `agent.memory_hook` in `recall()` |
| Shared code | `extract_topic_keywords()`, `_apply_category_weights()`, `extract_observations_async()`, `detect_outcomes_async()` | Same functions imported from `agent/` |

Both paths write to the same Redis Memory model. Memories created in Claude Code sessions are visible to Telegram agent sessions and vice versa. Deja vu thresholds and category recall weights are shared via `config/memory_defaults.py`.

## Tracking

- Issue: [#519](https://github.com/tomcounsell/ai/issues/519)
- PR: [#525](https://github.com/tomcounsell/ai/pull/525)
- Prerequisite: [Subconscious Memory](subconscious-memory.md) (PR #515)
- Related: [Memory Search Tool](memory-search-tool.md) (issue #518)
- Observability and parity: [#552](https://github.com/tomcounsell/ai/issues/552) (PR [#560](https://github.com/tomcounsell/ai/pull/560)) -- AgentSession lifecycle tracking for local sessions, deja vu parity, post-merge learning
- Project key isolation fix: [#811](https://github.com/tomcounsell/ai/issues/811) (PR [#820](https://github.com/tomcounsell/ai/pull/820)) -- cwd threading through hook entry points, DEFAULT_PROJECT_KEY changed from "dm" to "default", migration script
- Progressive disclosure + memory MCP server: [#1178](https://github.com/tomcounsell/ai/issues/1178) (PR [#1255](https://github.com/tomcounsell/ai/pull/1255)) -- compact stub injection in `recall()` / `prefetch()`, sidecar full-content invariant, `memory_get` / `memory_search` MCP tools (`mcp_servers/memory_server.py`), idempotent `~/.claude.json` registration via `scripts/update/mcp_memory.py`, async title generator wired at all 7 writer call sites
