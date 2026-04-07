# Claude Code Memory Integration

Hook-based memory integration that extends the subconscious memory system to Claude Code CLI sessions. User prompts are ingested as memories, tool calls trigger memory recall with thought injection, and session transcripts are extracted for observations on session stop.

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
        |                         Bloom dedup --> Memory.safe_save(importance=6.0)
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
                              |
                              v
                        Read transcript --> Haiku extraction
                              |                  |
                              v                  v
                        Outcome detection   Categorized observations
                        (injected thoughts)  saved as Memory records
                              |
                              v
                        Sidecar cleanup
```

## How It Works

### Ingestion (UserPromptSubmit Hook)

The `user_prompt_submit.py` hook fires on every user prompt in Claude Code. It passes the prompt to `memory_bridge.ingest()`, which:

1. Rejects prompts shorter than 50 characters
2. Rejects trivial patterns ("yes", "continue", "ok", "lgtm", etc.)
3. Checks the bloom filter for duplicate content
4. Saves qualifying prompts as Memory records with importance 6.0 (same as Telegram human messages)

Registered in `.claude/settings.json` with a 15-second timeout, running after the calendar prompt hook.

### Recall (PostToolUse Hook)

The `post_tool_use.py` hook calls `memory_bridge.recall()` after its existing SDLC state tracking. The recall system uses a file-based sliding window since hooks run as stateless processes:

1. Each tool call is appended to a JSON sidecar file at `data/sessions/{session_id}/memory_buffer.json`
2. The buffer is capped at 9 entries (BUFFER_SIZE)
3. Every 3rd tool call (WINDOW_SIZE), keywords are extracted from the buffer
4. Keywords are checked against the Memory bloom filter
5. On bloom hits, ContextAssembler queries Redis for relevant memories
6. Up to 3 matching memories are formatted as `<thought>` blocks and returned via the hook's `additionalContext` response field
7. Injected thought IDs are tracked in the sidecar for later outcome detection

The PostToolUse hook has a 5-second timeout. Memory operations (Redis-only) complete in under 15ms.

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
5. Runs `detect_outcomes_async()` to strengthen/weaken memories based on bigram overlap with the transcript
6. Cleans up all sidecar files for the session

The Stop hook has a 10-second timeout. Haiku extraction typically completes in 2-3 seconds.

## AgentSession Lifecycle Tracking

Local Claude Code sessions create AgentSession records in Redis, providing dashboard observability on par with Telegram-originated sessions. The lifecycle is managed across three hooks using a sidecar file to share the AgentSession `agent_session_id`:

1. **UserPromptSubmit hook**: On the first prompt of a session, creates an AgentSession via `AgentSession.create_local(session_type=..., ...)` with `status="running"` and `session_id=f"local-{claude_session_id}"`. The hook reads the `SESSION_TYPE` environment variable injected by `sdk_client.py` when spawning subprocesses, so the record stores the actual persona (`teammate`, `pm`, or `dev`); if `SESSION_TYPE` is absent (standalone CLI use), it defaults to `dev`. The `agent_session_id` is persisted to `data/sessions/{session_id}/agent_session.json`.
2. **PostToolUse hook**: On every tool call, reads `agent_session_id` from the sidecar and updates `updated_at` timestamp and increments `tool_call_count` on the AgentSession record.
3. **Stop hook**: Reads `agent_session_id` from the sidecar, sets `completed_at`, and marks status as `completed` (or `failed` if `stop_reason` is "error" or "crash").

The dashboard at `localhost:8500` picks up local sessions automatically via `AgentSession.query` -- no dashboard code changes were needed. Local sessions appear alongside Telegram sessions with correct status, timestamps, and project key.

The `AgentSession.create_local(...)` call requires only `session_id`, `project_key`, and `working_dir`. The `session_type` defaults to `"dev"` but is overridden by the `SESSION_TYPE` env var when set. Local sessions omit all Telegram-specific fields (no `chat_id` or `parent_chat_session_id`).

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

Every memory record is stored under a `project_key` partition that scopes it to a specific project. The `_get_project_key(cwd)` function in `memory_bridge.py` resolves the key using the following priority chain:

1. **`VALOR_PROJECT_KEY` env var** — if set, always wins (highest priority). Injected by `sdk_client.py` when spawning Dev sessions so CI and automated flows use the correct partition.
2. **`projects.json` cwd match** — reads `~/Desktop/Valor/projects.json`, iterates all `projects[key].working_directory` entries, and returns the first key whose path is a prefix of `cwd`. This handles multi-project machines automatically.
3. **`Path(cwd).name` fallback** — if `projects.json` is missing or no entry matches, the basename of the current working directory is used as the project key (e.g., `~/src/ai` → `"ai"`).
4. **`DEFAULT_PROJECT_KEY`** — final fallback when `cwd` is None or empty. Comes from `config/memory_defaults.py` (currently `"default"`). This value is intentionally not `"dm"` — that key is semantically reserved for Telegram direct messages and must not be used as a fallback for non-DM contexts.

The `cwd` value flows through every public hook entry point:

| Hook entry point | Where cwd comes from | How it's passed |
|-----------------|----------------------|-----------------|
| `recall(session_id, tool_name, tool_input, cwd)` | `hook_input.get("cwd")` in `post_tool_use.py` | Keyword argument |
| `ingest(content, cwd)` | `hook_input.get("cwd")` in `user_prompt_submit.py` | Keyword argument |
| `extract(session_id, transcript_path, cwd)` | `hook_input.get("cwd")` in `stop.py` | Keyword argument |
| `post_merge_extract(pr_number, pr_title, diff_summary, cwd)` | `hook_input.get("cwd")` in `stop.py` | Keyword argument |

All four functions default to `cwd=None` for backwards compatibility. Callers that do not pass `cwd` fall through to `DEFAULT_PROJECT_KEY`.

### Migration Note

All Memory records created between 2026-03-24 and 2026-04-07 have `project_key="dm"` due to a bug where hooks called `_get_project_key()` with no `cwd` argument, falling through to the then-default `DEFAULT_PROJECT_KEY="dm"`. This is fixed in PR #820.

To re-key existing mislabeled records, run the migration script:

```bash
# Preview what would be migrated (safe, no changes)
python scripts/migrate_memory_project_key.py --dry-run

# Apply the migration
python scripts/migrate_memory_project_key.py --apply
```

The script:
- Scans all `Memory:*:dm:*` Redis keys
- Classifies each record: genuine Telegram DMs (source=human AND agent_id=dm) are preserved; all others are re-keyed to `"valor"`
- Renames keys atomically via Redis RENAME, updates hash fields, and rebuilds Popoto indexes
- Is idempotent — safe to run multiple times

See `scripts/migrate_memory_project_key.py` for full documentation.

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
| Extraction trigger | `run_post_session_extraction()` in messenger | `extract()` called from Stop hook |
| Ingestion | `Memory.safe_save()` in bridge | `ingest()` called from UserPromptSubmit hook |
| Deja vu signals | `check_and_inject()` emits vague recognition and novel territory thoughts | `recall()` emits identical signals |
| Post-merge learning | `extract_post_merge_learning()` in merge stage | `post_merge_extract()` triggered from Stop hook on `gh pr merge` detection |
| Session tracking | AgentSession created by bridge handler (`AgentSession.create(session_type=...)`) | AgentSession created by UserPromptSubmit hook (`AgentSession.create_local(session_type=SESSION_TYPE env var or "dev", ...)`) |
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
