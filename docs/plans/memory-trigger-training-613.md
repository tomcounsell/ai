---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-31
tracking: https://github.com/tomcounsell/ai/issues/613
last_comment_id:
---

# Memory Trigger Training: Outcome Tracking, Routine Compression, Stress Testing

## Problem

The memory system surfaces contextually relevant memories as `<thought>` blocks but has no meaningful feedback loop to learn which memories actually influence agent behavior. The current outcome detection in `agent/memory_extraction.py` uses bigram overlap (`_extract_bigrams` at line 333) -- a blunt heuristic that cannot distinguish coincidental keyword echoes from genuine influence.

**Current outcome flow:**
1. `_extract_bigrams()` pulls unigrams/bigrams from injected thoughts and response text
2. Non-empty set intersection -> "acted", empty -> "dismissed"
3. `ObservationProtocol.on_context_used()` adjusts confidence scores
4. `_persist_outcome_metadata()` tracks `dismissal_count` and `last_outcome` in the Memory metadata dict

**What is missing:**
- No semantic or causal analysis of whether a memory shaped the agent's decision
- No per-memory outcome history -- only `last_outcome` (a single string) and `dismissal_count` (resets on any "acted")
- Retrieval is recomputed from scratch every time, even for recurring prompt patterns
- No testing of retrieval quality under degraded conditions (Redis latency, ambiguous queries, bloom filter saturation)

## Workstream 1: Trigger Tracking (initial deliverable)

Upgrade outcome detection to measure whether surfaced memories actually influenced agent decisions, and persist outcome history for longitudinal analysis.

### Step 1.1: Replace bigram overlap with LLM-judged outcome detection

**Files modified:** `agent/memory_extraction.py`, `.claude/hooks/hook_utils/memory_bridge.py`

Replace the `_extract_bigrams` + set-intersection approach in `detect_outcomes_async()` (line 399) with a lightweight Haiku call that judges influence. The existing pipeline already calls Haiku for extraction (`extract_observations_async`), so the infrastructure is in place.

**Implementation:**
- Add a new function `_judge_outcomes_llm(injected_thoughts, response_text) -> dict[str, dict]` that sends a single Haiku request with all injected thoughts and the response text
- The prompt asks Haiku to return a JSON array: for each thought, classify as `"acted"` (response was influenced by this memory), `"echoed"` (keywords overlap but no causal link), or `"dismissed"` (no relationship)
- Include a `reasoning` field (one sentence) explaining the judgment
- Map `"echoed"` to `"dismissed"` for ObservationProtocol compatibility (echoed is noise, not signal)
- Keep `_extract_bigrams` as a zero-cost fallback when API calls fail or are rate-limited
- The Haiku call should be bounded: truncate response to 4000 chars, truncate each thought to 500 chars, cap at 5 thoughts per batch

**Cost estimate:** One additional Haiku call per session (~$0.001). Acceptable given the existing extraction call.

**Parity:** The Claude Code hooks path (`memory_bridge.py`) calls the same `detect_outcomes_async` function from the Stop hook, so this change automatically applies to both paths.

### Step 1.2: Add outcome history to Memory metadata

**Files modified:** `agent/memory_extraction.py`, `models/memory.py` (docstring only), `config/memory_defaults.py`

Currently `_persist_outcome_metadata()` stores only `last_outcome` and `dismissal_count`. Add an `outcome_history` list that tracks the last N outcomes with timestamps.

**Implementation:**
- In `_persist_outcome_metadata()`, append to `metadata["outcome_history"]` a dict: `{"outcome": "acted"|"dismissed", "reasoning": "...", "ts": unix_timestamp}`
- Cap the list at `MAX_OUTCOME_HISTORY` entries (default: 10) -- oldest entries are dropped
- Add `MAX_OUTCOME_HISTORY = 10` to `config/memory_defaults.py`
- Update the Memory model docstring to document the new metadata key
- Compute a derived `act_rate` for downstream use: `acted_count / total_count` from the history

**Migration:** No schema change needed. DictField accepts arbitrary keys. Old memories without `outcome_history` get an empty list on first outcome detection (natural backfill).

### Step 1.3: Surface outcome data in memory_search CLI

**Files modified:** `tools/memory_search/` (the inspect/search commands)

Expose the new outcome history so developers can audit which memories are effective.

**Implementation:**
- In the `inspect` subcommand output, display `outcome_history` as a formatted table (date, outcome, reasoning)
- In the `search` subcommand, add an `--act-rate` flag that filters results to memories with act_rate above a threshold
- Add a `stats` subcommand that shows aggregate outcome statistics: total memories with history, average act_rate, top 5 most-acted-on memories

### Step 1.4: Tests for outcome detection upgrade

**Files modified:** `tests/unit/test_memory_extraction.py` (new test cases)

- Test `_judge_outcomes_llm` with mocked Anthropic client: verify JSON parsing, verify fallback to bigram on API failure
- Test `_persist_outcome_metadata` with outcome_history: verify append behavior, verify cap at MAX_OUTCOME_HISTORY, verify act_rate computation
- Test that "echoed" maps to "dismissed" for ObservationProtocol
- Test backward compatibility: old memories without outcome_history get it initialized on first outcome

### Architecture decision: LLM vs embedding-based similarity

The issue suggests embedding-based semantic similarity as an alternative. The LLM-judge approach is preferred for Workstream 1 because:

1. **No new infrastructure** -- embeddings would require an embedding model, vector storage, and similarity computation. The Haiku call reuses existing API infrastructure.
2. **Richer signal** -- the LLM can distinguish "acted on" from "echoed keywords" with reasoning, which embeddings cannot.
3. **Debuggable** -- the reasoning field makes it possible to audit why a judgment was made.
4. **Cheap enough** -- one Haiku call per session is negligible cost.

Embeddings may be revisited in Workstream 2 if pattern detection needs fast similarity lookups at scale.

## Workstream 2: Routine Compression (follow-on)

Learn common prompt-to-memory associations so the system can shortcut full retrieval for recurring patterns. Depends on Workstream 1 outcome data to identify which memories are consistently useful for which prompt types.

### Sketch

- Add a frequency counter to `agent/memory_hook.py` that tracks `(keyword_cluster_hash -> set[memory_id])` pairs in Redis
- When the same cluster hash appears N times (configurable, default 5) with >80% overlap in retrieved memory IDs, cache the result set
- Cache entries expire on: (a) TTL (e.g., 24h), (b) any constituent memory's content or importance changing, (c) new memories being created with overlapping bloom fingerprints
- On cache hit, bypass `retrieve_memories()` entirely -- inject cached thoughts directly
- Track cache hit/miss rates in logging for tuning

**Key files:** `agent/memory_hook.py`, `agent/memory_retrieval.py`, `config/memory_defaults.py`

**Separate issue recommended** -- this is a distinct feature with its own acceptance criteria.

## Workstream 3: Stress-Tested Reliability (follow-on, parallel)

Ensure memory recall stays useful when conditions degrade. Independent of Workstreams 1 and 2.

### Sketch

- **Redis degradation tests:** Mock Redis connections with injected latency (50ms, 200ms, timeout). Verify `check_and_inject()` returns None gracefully within time budget. Verify `Memory.safe_save()` does not block.
- **Ambiguous query tests:** Create memories with overlapping content (e.g., "Redis connection pooling" and "Redis connection timeout"). Query with ambiguous terms. Use AI judge to evaluate whether results are relevant.
- **Bloom filter edge cases:** Populate ExistenceFilter near capacity (100k entries). Verify false positive rate stays within `error_rate=0.01`. Test with content that produces similar fingerprints.
- **Performance benchmarks:** Establish baseline latency for `retrieve_memories()` with 100, 1000, 10000 memories. Assert no regression beyond 2x baseline.

**Key files:** `tests/integration/` (new test files), `models/memory.py`, `agent/memory_hook.py`

**Relationship to #516:** The memory-test-suite plan (`docs/plans/memory-test-suite-516.md`) covers integration testing broadly. Workstream 3 focuses specifically on degradation and edge cases. The two are complementary -- Workstream 3 tests should live alongside the #516 test suite.

**Separate issue recommended** -- this is independent infrastructure work.

## Acceptance Criteria (Workstream 1 only)

- [ ] `detect_outcomes_async()` uses Haiku LLM judgment instead of bigram overlap as primary signal
- [ ] Bigram overlap is retained as fallback when Haiku call fails
- [ ] `outcome_history` list is persisted in Memory metadata (capped at 10 entries)
- [ ] Each outcome entry includes `outcome`, `reasoning`, and `ts` fields
- [ ] `memory_search inspect` displays outcome history
- [ ] Unit tests cover LLM judge parsing, fallback behavior, and history persistence
- [ ] Both agent paths (SDK/Telegram and Claude Code hooks) use the upgraded detection

## Key Files

| File | Role in this plan |
|------|-------------------|
| `agent/memory_extraction.py` | Primary: replace bigram detection, add outcome history persistence |
| `agent/memory_hook.py` | Read-only reference: injection pipeline, keyword extraction |
| `agent/memory_retrieval.py` | Read-only reference: BM25 + RRF fusion retrieval |
| `models/memory.py` | Docstring update for new metadata keys |
| `config/memory_defaults.py` | Add MAX_OUTCOME_HISTORY constant |
| `.claude/hooks/hook_utils/memory_bridge.py` | Parity check: uses same detect_outcomes_async |
| `tools/memory_search/` | Surface outcome data in CLI |
| `tests/unit/test_memory_extraction.py` | New test cases for LLM judge and outcome history |
| `docs/features/subconscious-memory.md` | Update outcome detection docs after implementation |

## Risks

1. **Haiku latency in outcome detection path** -- Detection runs post-session (not in the hot path), so latency is acceptable. The 4000-char truncation bounds token cost.
2. **LLM judgment accuracy** -- May over-attribute "acted" for on-topic responses. The "echoed" category mitigates this. Outcome history allows post-hoc analysis of judgment quality.
3. **Metadata size growth** -- 10 outcome entries per memory at ~200 bytes each = ~2KB. Negligible for msgpack-encoded DictField.
