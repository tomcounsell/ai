# Subconscious Memory

Automatic memory injection and extraction system that gives agents persistent context across sessions. Human instructions and agent observations are stored as Memory records in Redis, surfaced as `<thought>` blocks during tool calls, and reinforced by outcome detection.

Memories carry structured metadata (category, file paths, tags) from extraction, track effectiveness via dismissal counting with importance decay, and use multi-query decomposition for broader retrieval coverage.

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
        |                            _cluster_keywords()
  Haiku Extraction  <---+             (multi-query split)
  JSON -> metadata       |                   |
  importance=AGENT (1.0) |           ContextAssembler x N
        |                |            (per cluster query)
        v                |                   |
  Outcome Detection -----+                   v
  (bigram overlap)                    <thought> blocks
        |                            via additionalContext
        v
  ObservationProtocol
  (confidence adjustment)
        |
        v
  _persist_outcome_metadata()
  (dismissal tracking + decay)
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
5. If positive and >5 unique keywords: `_cluster_keywords()` splits keywords into topical clusters (max 3 clusters of ~3-5 keywords each); otherwise uses a single query
6. `ContextAssembler.assemble()` runs per cluster, results are merged and deduplicated by `memory_id`
7. Results are formatted as `<thought>content</thought>` blocks (max 3)
8. Returned via `additionalContext` in the hook response
9. Injected thoughts are tracked for later outcome detection
10. Latency is monitored with a 15ms warning threshold for multi-query paths

### Flow 3: Post-Session Extraction

After a session completes in `agent/messenger.py`:

1. `run_post_session_extraction()` is called after `BackgroundTask._result` is set
2. Haiku extracts novel observations as structured JSON (category, observation text, file_paths, tags), with a line-based fallback parser for robustness
3. Each observation is saved as Memory with categorized importance (corrections/decisions at 4.0, patterns/surprises at 1.0) and structured metadata attached via `DictField`
4. Outcome detection compares injected thoughts against response using bigram overlap
5. `ObservationProtocol.on_context_used()` strengthens acted-on memories and weakens dismissed ones
6. `_persist_outcome_metadata()` runs after ObservationProtocol, updating `dismissal_count` and `last_outcome` in each memory's metadata. When a memory reaches the dismissal threshold (3 consecutive dismissals), its importance is decayed by 0.7x (floor: 0.2). Acting on a memory resets the dismissal counter

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
2. Knowledge document companions: 3.0 (Flow 6 indexer)
3. Enhanced extraction corrections/decisions: 4.0 (Flow 3 categorized)
4. Human Telegram messages: 6.0 (Flow 1)
5. Agent-identified architectural decisions: 7.0 (Flow 5 intentional)
6. Human-directed saves (corrections, explicit requests): 8.0 (Flow 5 intentional)

### Flow 6: Knowledge Document Ingestion

The knowledge document integration system indexes work-vault files as companion memories. See [Knowledge Document Integration](knowledge-document-integration.md) for full details.

1. `KnowledgeWatcher` (bridge thread) detects file changes in `~/work-vault/` via watchdog
2. `index_file()` reads content, resolves project scope, upserts `KnowledgeDocument` (Redis + filesystem)
3. Haiku summarizes the document content (fallback: first 500 chars)
4. Companion Memory records are created with `source="knowledge"`, `importance=3.0`, and a `reference` JSON pointer to the source file
5. Companion memories enter the bloom filter and surface as `<thought>` blocks during related work
6. The agent reads the full file on demand using the reference pointer

**Importance tier:** Knowledge memories sit at 3.0 -- above agent observations (1.0) but below human messages (6.0). Large documents (>2000 words) are split by top-level headings, producing one companion memory per section.

**Reference pointer format:**
```json
{"tool": "read_file", "params": {"file_path": "/path/to/doc.md"}}
```

### Flow 7: Post-Merge Learning Extraction

After a PR merges, `extract_post_merge_learning()` in `agent/memory_extraction.py` distills the single most important project-level takeaway from the PR title, body, and diff summary. The learning is saved as a Memory with importance 7.0 and structured metadata (category, tags, file_paths) matching the post-session extraction schema. This captures architectural decisions and conventions established by shipped code.

The extraction prompt requests structured JSON output. If Haiku returns valid JSON, the observation, category, tags, and file_paths are parsed and passed as metadata to `Memory.safe_save()`. If Haiku returns non-JSON (plain text), the text is saved with a default metadata of `{"category": "decision"}`. This ensures all memory creation paths produce consistent metadata.

The function is designed to be called from the SDLC merge stage or a post-merge script. It returns None gracefully if no meaningful takeaway is found or if the API call fails.

## Claude Code Integration

The memory system also runs in Claude Code CLI sessions via hooks. See [Claude Code Memory](claude-code-memory.md) for full details.

- **UserPromptSubmit hook** ingests qualifying user prompts (same importance=6.0 as Telegram messages) and creates an AgentSession record for dashboard observability
- **PostToolUse hook** runs memory recall with a file-based sliding window (JSON sidecar files replace in-memory state since hooks are stateless processes) and updates AgentSession activity tracking
- **Stop hook** runs Haiku extraction and outcome detection on the session transcript, completes the AgentSession lifecycle, and triggers post-merge learning extraction when applicable
- **Deja vu signals** provide vague recognition or novel territory cues when recall results are ambiguous (shared thresholds with SDK agent path via `config/memory_defaults.py`)
- **Post-merge learning** is triggered from the Stop hook when `gh pr merge` is detected during the session, calling `extract_post_merge_learning()` with PR metadata fetched via `gh` CLI
- Bridge module: `.claude/hooks/hook_utils/memory_bridge.py`

Both paths (Telegram agent and Claude Code hooks) write to the same Redis Memory model. Memories are shared across all session types. All memory capabilities (ingestion, recall, deja vu signals, extraction, outcome detection, post-merge learning, multi-query decomposition) now have feature parity across both paths.

## Category-Weighted Recall

After ContextAssembler returns scored results, `_apply_category_weights()` re-ranks them by multiplying each record's score by a category-specific weight before sorting. This ensures that corrections and decisions -- higher-signal memory types -- surface preferentially over patterns and surprises when scores are similar.

**Weight table** (from `CATEGORY_RECALL_WEIGHTS` in `config/memory_defaults.py`):

| Category | Weight | Effect |
|----------|--------|--------|
| `correction` | 1.5 | Boosted -- past mistakes should be top of mind |
| `decision` | 1.3 | Boosted -- architectural choices are high-value context |
| `pattern` | 1.0 | Neutral -- general observations keep existing rank |
| `surprise` | 1.0 | Neutral |
| `default` | 1.0 | Fallback for records with missing or unknown category |

**Mechanism:**
1. ContextAssembler returns scored records (relevance + confidence weighted)
2. `_apply_category_weights(records)` reads `metadata.category` from each record
3. Effective score = `record.score * category_weight`
4. Records are re-sorted by effective score descending
5. Top `MAX_THOUGHTS` records are formatted as `<thought>` blocks

**Fail-safe:** If metadata is None, not a dict, or missing the category key, the default weight (1.0) is used. If `CATEGORY_RECALL_WEIGHTS` cannot be imported, records are returned in their original order.

Both `check_and_inject()` (SDK/Telegram path) and `recall()` (Claude Code hooks path) apply the same re-ranking. The bridge imports `_apply_category_weights` from `agent.memory_hook`.

## Structured Metadata

Memory records carry an optional `metadata` DictField with structured data from extraction and outcome tracking. Old records without metadata return `{}` (no migration needed).

**Schema:**

| Key | Type | Source | Description |
|-----|------|--------|-------------|
| `category` | `str` | Extraction | One of `"correction"`, `"decision"`, `"pattern"`, `"surprise"` |
| `file_paths` | `list[str]` | Extraction | File paths referenced in the observation |
| `tags` | `list[str]` | Extraction | Domain tags (1-3 short keywords) |
| `tool_names` | `list[str]` | Extraction | Tool names from the session context |
| `dismissal_count` | `int` | Outcome tracking | Consecutive dismissals before last reset |
| `last_outcome` | `str` | Outcome tracking | `"acted"` or `"dismissed"` |

Additionally, the Memory model has a top-level `reference` StringField (not inside metadata) for actionable pointers. Knowledge-sourced memories use this to store a JSON tool call pointing to the source file. See [Knowledge Document Integration](knowledge-document-integration.md).

**Querying by metadata:** The `memory_search` CLI supports post-retrieval filtering:

```bash
python -m tools.memory_search search "query" --category correction
python -m tools.memory_search search "query" --tag redis
```

Metadata filtering happens after ContextAssembler returns results (ContextAssembler does not support field-level filtering). The `inspect` command displays metadata when present.

## Dismissal Tracking

Chronically dismissed memories have their importance decayed to reduce noise. This supplements the confidence-based ObservationProtocol adjustment with a direct importance penalty.

**Mechanism:**
1. After each session, `_persist_outcome_metadata()` runs (after ObservationProtocol to avoid conflicting saves)
2. For "dismissed" outcomes: `dismissal_count` increments in metadata
3. When `dismissal_count` reaches `DISMISSAL_DECAY_THRESHOLD` (3): importance is multiplied by `DISMISSAL_IMPORTANCE_DECAY` (0.7), and the counter resets
4. For "acted" outcomes: `dismissal_count` resets to 0
5. Importance never drops below `MIN_IMPORTANCE_FLOOR` (0.2)

Outcome detection naturally backfills metadata on pre-existing records as a side effect -- this is intentional and distinct from explicit bulk backfill (which is not done).

## Multi-Query Decomposition

When the keyword buffer produces more than 5 unique keywords, `_cluster_keywords()` splits them into topical clusters for broader retrieval coverage.

**Mechanism:**
1. `_cluster_keywords(keywords, max_clusters=3)` divides the list into chunks of ~3-5 keywords
2. Each cluster is queried separately via `ContextAssembler.assemble()`
3. Results are merged and deduplicated by `memory_id`
4. Tiny trailing clusters (<2 keywords) are merged into the previous cluster
5. For <=5 keywords, a single query is used (no decomposition)

Both `check_and_inject()` (SDK/Telegram path) and `recall()` (Claude Code hooks path) use this same logic. The bridge imports `_cluster_keywords` from `agent.memory_hook`.

A latency guard logs a WARNING if multi-query retrieval exceeds 15ms.

## Parity Requirement

The memory system MUST work equally across all agent session types — SDK/Telegram sessions and local Claude Code CLI sessions. Any memory capability added to one path must be implemented in the other. The shared Redis Memory model ensures data-level parity; the gaps below are at the integration layer.

### Current Gaps (to be closed)

| Capability | Claude Code | SDK/Agent | Action |
|-----------|-------------|-----------|--------|
| Prompt ingestion (auto-save user input) | Yes (UserPromptSubmit hook) | No (Telegram messages only) | Add ingestion hook or equivalent to SDK path |

## Key Files

| File | Purpose |
|------|---------|
| `models/memory.py` | Memory model (Level 3 popoto: decay, confidence, write filter, access tracker, bloom, DictField metadata, reference pointer) |
| `config/memory_defaults.py` | Tuned Defaults overrides for popoto constants and dismissal tracking thresholds |
| `agent/memory_hook.py` | PostToolUse thought injection with sliding window rate limiting, multi-query decomposition via `_cluster_keywords()` (Telegram agent path) |
| `agent/memory_extraction.py` | Post-session JSON extraction with line-based fallback, bigram outcome detection, dismissal tracking via `_persist_outcome_metadata()`, post-merge learning extraction |
| `agent/health_check.py` | Integration point: `watchdog_hook()` calls `check_and_inject()` |
| `agent/messenger.py` | Integration point: `_run_work()` calls `run_post_session_extraction()` |
| `bridge/telegram_bridge.py` | Integration point: `Memory.safe_save()` after `store_message()` |
| `.claude/hooks/hook_utils/memory_bridge.py` | Claude Code hook memory bridge (recall, ingest, extract, agent session sidecar helpers, post-merge extract) |
| `.claude/hooks/user_prompt_submit.py` | Claude Code prompt ingestion hook and AgentSession creation |
| `.claude/hooks/post_tool_use.py` | Claude Code PostToolUse hook with memory recall and AgentSession activity tracking |
| `.claude/hooks/stop.py` | Claude Code Stop hook with extraction, AgentSession completion, and post-merge learning |
| `models/knowledge_document.py` | KnowledgeDocument model for indexed work-vault files |
| `tools/knowledge/indexer.py` | Knowledge indexer pipeline (index, delete, full scan, companion memories) |
| `tools/knowledge/scope_resolver.py` | File path to project scope resolution via projects.json |
| `bridge/knowledge_watcher.py` | Filesystem watcher for work-vault changes (watchdog + debounce) |
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
| `DEJA_VU_BLOOM_HIT_THRESHOLD` | 3 | Bloom hits needed for "vague recognition" thought (shared across both paths) |
| `NOVEL_TERRITORY_KEYWORD_THRESHOLD` | 7 | Unique keywords with zero bloom hits needed for "novel territory" thought |
| `DISMISSAL_DECAY_THRESHOLD` | 3 | Consecutive dismissals before importance decays |
| `DISMISSAL_IMPORTANCE_DECAY` | 0.7 | Importance multiplier on threshold breach |
| `MIN_IMPORTANCE_FLOOR` | 0.2 | Minimum importance after decay (never drops below this) |
| `CATEGORY_RECALL_WEIGHTS` | `{correction: 1.5, decision: 1.3, pattern: 1.0, surprise: 1.0, default: 1.0}` | Post-query re-ranking multipliers by category |

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
- Retrieval enhancement: [#583](https://github.com/tomcounsell/ai/issues/583) (PR [#584](https://github.com/tomcounsell/ai/pull/584)) -- structured metadata, dismissal tracking, multi-query decomposition
- Metadata-aware recall: [#586](https://github.com/tomcounsell/ai/issues/586) -- category-weighted recall re-ranking, post-merge metadata parity, retrieval recipes
- Downstream: Issue #395 (multi-persona memory partitioning), Issue #393 (behavioral episode memory)
