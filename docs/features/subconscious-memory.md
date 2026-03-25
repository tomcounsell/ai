# Subconscious Memory

Automatic memory injection and extraction system that gives agents persistent context across sessions. Human instructions and agent observations are stored as Memory records in Redis, surfaced as `<thought>` blocks during tool calls, and reinforced by outcome detection.

## Architecture

```
Human Message (Telegram)                    Agent Session
        |                                        |
        v                                        v
  Memory.save()                          PostToolUse Hook
  importance=HUMAN (6.0)                       |
        |                                      v
        v                               ExistenceFilter
  Redis (Memory model)  <----  bloom    (O(1) check)
        ^                 check              |
        |                               ContextAssembler
  Haiku Extraction  <---+                    |
  importance=AGENT (1.0) |                   v
        |                |            <thought> blocks
        v                |            via additionalContext
  Outcome Detection -----+
  (bigram overlap)
        |
        v
  ObservationProtocol
  (confidence adjustment)
```

## Data Flows

### Flow 1: Human Message Ingestion

Telegram messages are saved as Memory records immediately on receipt in `bridge/telegram_bridge.py`:

1. Message arrives via Telethon event handler
2. `store_message()` saves to TelegramMessage (existing behavior)
3. `Memory.safe_save()` creates a Memory record with `InteractionWeight.HUMAN` (6.0) importance
4. ExistenceFilter bloom index is updated automatically on save
5. Memory is immediately available for future ContextAssembler queries

Empty text, bot messages, and media-only messages are skipped.

### Flow 2: Thought Injection

The PostToolUse hook in `agent/health_check.py` checks for relevant memories on every tool call:

1. `check_and_inject()` in `agent/memory_hook.py` is called
2. Tool call is added to a rolling buffer (last 9 calls, 3 windows)
3. Every 3rd call, topic keywords are extracted from the buffer
4. `ExistenceFilter.might_exist()` does an O(1) bloom check
5. If positive: `ContextAssembler.assemble()` retrieves top memories (~5-10ms)
6. Results are formatted as `<thought>content</thought>` blocks (max 3)
7. Returned via `additionalContext` in the hook response
8. Injected thoughts are tracked for later outcome detection

### Flow 3: Post-Session Extraction

After a session completes in `agent/messenger.py`:

1. `run_post_session_extraction()` is called after `BackgroundTask._result` is set
2. Haiku extracts novel observations (decisions, surprises, corrections, patterns)
3. Each observation is saved as Memory with `InteractionWeight.AGENT` (1.0) importance
4. Outcome detection compares injected thoughts against response using bigram overlap
5. `ObservationProtocol.on_context_used()` strengthens acted-on memories and weakens dismissed ones

### Flow 4: System Prompt Priming

`config/personas/_base.md` includes a `## Subconscious Memory` section that tells the agent to treat `<thought>` blocks as background context without referencing them explicitly.

### Flow 5: Intentional Saves

Agents can deliberately persist high-level concepts using `python -m tools.memory_search save "content"`. Unlike passive extraction (Flow 3), intentional saves are for concepts the agent recognizes as important in the moment. Instructions in `config/personas/_base.md` (the `## Intentional Memory` section) guide the agent on when to save.

**Trigger categories and importance levels:**

| Trigger | Importance | Source | Example |
|---------|-----------|--------|---------|
| User correction | 8.0 | `human` | User clarifies how a system actually works |
| Explicit "remember this" | 8.0 | `human` | User asks the agent to remember a fact or rule |
| Architectural decision | 7.0 | `agent` | Design choice made during planning or building |

**When NOT to save:**
- Implementation details (file paths, function signatures) -- those belong in code comments
- Temporary work context (current branch, PR number) -- those belong in issue comments
- Facts already in CLAUDE.md or project docs -- avoid duplication
- Routine observations -- the passive extraction system (Flow 3) handles those

**Importance tier hierarchy** (lower to higher):
1. Generic agent observations: 1.0 (Flow 3 default for patterns/surprises)
2. Enhanced extraction corrections/decisions: 4.0 (Flow 3 categorized)
3. Human Telegram messages: 6.0 (Flow 1)
4. Agent-identified architectural decisions: 7.0 (Flow 5 intentional)
5. Human-directed saves (corrections, explicit requests): 8.0 (Flow 5 intentional)

### Flow 6: Post-Merge Learning Extraction

After a PR merges, `extract_post_merge_learning()` in `agent/memory_extraction.py` distills the single most important project-level takeaway from the PR title, body, and diff summary. The learning is saved as a Memory with importance 7.0. This captures architectural decisions and conventions established by shipped code.

The function is designed to be called from the SDLC merge stage or a post-merge script. It returns None gracefully if no meaningful takeaway is found or if the API call fails.

## Claude Code Integration

The memory system also runs in Claude Code CLI sessions via hooks. See [Claude Code Memory](claude-code-memory.md) for full details.

- **UserPromptSubmit hook** ingests qualifying user prompts (same importance=6.0 as Telegram messages)
- **PostToolUse hook** runs memory recall with a file-based sliding window (JSON sidecar files replace in-memory state since hooks are stateless processes)
- **Stop hook** runs Haiku extraction and outcome detection on the session transcript
- **Deja vu signals** provide vague recognition or novel territory cues when recall results are ambiguous
- Bridge module: `.claude/hooks/hook_utils/memory_bridge.py`

Both paths (Telegram agent and Claude Code hooks) write to the same Redis Memory model. Memories are shared across all session types.

## Key Files

| File | Purpose |
|------|---------|
| `models/memory.py` | Memory model (Level 3 popoto: decay, confidence, write filter, access tracker, bloom) |
| `config/memory_defaults.py` | Tuned Defaults overrides for popoto constants |
| `agent/memory_hook.py` | PostToolUse thought injection with sliding window rate limiting (Telegram agent path) |
| `agent/memory_extraction.py` | Post-session Haiku extraction (categorized), bigram outcome detection, post-merge learning extraction |
| `agent/health_check.py` | Integration point: `watchdog_hook()` calls `check_and_inject()` |
| `agent/messenger.py` | Integration point: `_run_work()` calls `run_post_session_extraction()` |
| `bridge/telegram_bridge.py` | Integration point: `Memory.safe_save()` after `store_message()` |
| `.claude/hooks/hook_utils/memory_bridge.py` | Claude Code hook memory bridge (recall, ingest, extract) |
| `.claude/hooks/user_prompt_submit.py` | Claude Code prompt ingestion hook |
| `config/personas/_base.md` | Thought priming instruction for agents |

## Configuration

All tuning constants are in `config/memory_defaults.py`. Call `apply_defaults()` before defining the Memory model (this happens automatically on import).

| Constant | Default | Description |
|----------|---------|-------------|
| `MEMORY_DECAY_RATE` | 0.3 | How fast memories fade (lower = slower). Effective lifetime ~ importance^2 days |
| `MEMORY_WF_MIN_THRESHOLD` | 0.15 | Minimum importance to persist (below this: silently dropped) |
| `MEMORY_WF_PRIORITY_THRESHOLD` | 0.7 | Above this: tagged as priority for preferential retrieval |
| `MEMORY_INITIAL_CONFIDENCE` | 0.5 | Starting confidence (neutral) |
| `MEMORY_ACTED_SIGNAL` | 0.85 | Confidence boost when agent acts on a memory |
| `MEMORY_CONTRADICTED_SIGNAL` | 0.15 | Confidence penalty when agent contradicts a memory |
| `MEMORY_SURFACING_THRESHOLD` | 0.4 | Minimum score for ContextAssembler to surface a memory |
| `MAX_THOUGHTS_PER_INJECTION` | 3 | Maximum thought blocks per injection event |
| `INJECTION_WINDOW_SIZE` | 3 | Tool calls per sliding window |
| `INJECTION_BUFFER_SIZE` | 9 | Total tool calls in rolling buffer |

## Error Handling

All memory operations are wrapped in try/except with logging. The memory system is designed to fail silently:

- `Memory.safe_save()` returns None on any error
- `check_and_inject()` returns None on any error
- `run_post_session_extraction()` catches all exceptions
- Memory failures never crash the bridge, agent, or session
- All failures are logged at WARNING level for debugging

## Reversibility

The memory system has high reversibility:

1. Remove `Memory.safe_save()` call from `bridge/telegram_bridge.py`
2. Remove memory hook integration from `agent/health_check.py`
3. Remove extraction hook from `agent/messenger.py`
4. Delete `models/memory.py`, `config/memory_defaults.py`, `agent/memory_hook.py`, `agent/memory_extraction.py`
5. Remove Memory from `models/__init__.py`
6. Flush Redis keys: `redis-cli KEYS "*Memory*" | xargs redis-cli DEL`

No schema migrations are involved. Redis keys can be flushed without side effects.

## Tracking

- Issue: [#514](https://github.com/tomcounsell/ai/issues/514)
- Intentional saves: [#521](https://github.com/tomcounsell/ai/issues/521) (PR [#524](https://github.com/tomcounsell/ai/pull/524))
- Prior art: Issue #394 (original agent memory integration layer)
- Downstream: Issue #395 (multi-persona memory partitioning), Issue #393 (behavioral episode memory)
