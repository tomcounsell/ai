---
status: Building
type: feature
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/500
last_comment_id:
---

# Cross-Agent Knowledge Relay: Persistent Findings from Parallel Work

## Problem

When ChatSession orchestrates work through sub-agents (DevSession stages, parallel research, etc.), each sub-agent's detailed findings disappear when it completes. The parent agent gets a compressed summary, but:

1. Future sub-agents cannot search prior findings -- a DevSession running the TEST stage cannot see what the BUILD-stage DevSession discovered about the codebase
2. Cross-agent learning is lost -- parallel research agents may discover overlapping insights with no structured way to persist and retrieve them
3. Parent context is thin -- the orchestrating ChatSession gets a compressed result, not the detailed reasoning or artifacts

## Dependencies

**Builds on PR #515** (Subconscious Memory) which shipped the core Popoto primitives: DecayingSortedField, ConfidenceField, WriteFilterMixin, AccessTrackerMixin, ExistenceFilter, CoOccurrenceField, CompositeScoreQuery, ContextAssembler. All primitives are available as ORM-level building blocks.

**Already available (used by this plan):**
- `Memory` model with DecayingSortedField, ConfidenceField, WriteFilterMixin, AccessTrackerMixin, ExistenceFilter (shipped in PR #515)
- `SubagentStop` hook that fires on dev-session completion and records stage state
- `memory_hook.py` PostToolUse injection via `<thought>` blocks and additionalContext
- `AgentSession` model with session_id, session_type, project_key, slug, parent_chat_session_id

## Prior Art

- **PR #515**: Subconscious Memory -- established Memory model, ExistenceFilter bloom, ContextAssembler injection, PostToolUse thought injection pattern
- **SubagentStop hook** (`agent/hooks/subagent_stop.py`): fires on dev-session completion, already extracts outcome summary
- **memory_hook.py**: PostToolUse thought injection, sliding window topic extraction, `<thought>` block formatting

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (design review after plan)
- Review rounds: 1-2

Builds on PR #515. Estimated build effort is 2-3 sessions.

## Prerequisites

- PR #515 (Subconscious Memory) -- all Popoto primitives available (DecayingSortedField, ConfidenceField, WriteFilterMixin, AccessTrackerMixin, ExistenceFilter, CoOccurrenceField, CompositeScoreQuery, ContextAssembler)
- Memory model established with bloom filter injection pipeline

## Solution

### Model: Finding

A new Popoto model `Finding` in `models/finding.py` that stores structured records of sub-agent discoveries. This is distinct from `Memory` (which stores human instructions and agent observations at a general level) -- `Finding` stores work-item-scoped technical discoveries.

```
Finding
  finding_id    = AutoKeyField()
  slug          = KeyField()           # work item scope (e.g., "cross-agent-knowledge-relay")
  project_key   = KeyField()           # project partition
  session_id    = KeyField()           # which DevSession produced this
  stage         = StringField()        # SDLC stage (BUILD, TEST, REVIEW, etc.)
  category      = StringField()        # "file_examined", "pattern_found", "decision_made", "artifact_produced", "dependency_discovered"
  content       = StringField()        # the finding text (max ~500 chars)
  file_paths    = StringField()        # comma-separated file paths (for path-based queries)
  importance    = FloatField(default=3.0)

  relevance     = DecayingSortedField(base_score_field="importance", partition_by="slug")
  confidence    = ConfidenceField(initial_confidence=0.5)
  bloom         = ExistenceFilter(error_rate=0.01, capacity=50_000, fingerprint_fn=...)
  associations  = CoOccurrenceField(partition_by="slug")  # links related findings
```

**Key design decisions:**
- Partitioned by `slug` (not project_key) so findings are scoped to a single work item
- DecayingSortedField on `slug` partition means findings from completed/stale work items fade naturally
- CoOccurrenceField links findings that appear in the same query context (e.g., "auth bug" findings from BUILD + TEST strengthen each other)
- WriteFilterMixin gates trivial findings (importance below threshold silently dropped)
- AccessTrackerMixin tracks which findings get reused by future agents

### Extraction: SubagentStop Hook Enhancement

Enhance `agent/hooks/subagent_stop.py` to extract and persist findings when a DevSession completes.

**Flow:**
1. SubagentStop hook fires (existing behavior)
2. New function `_extract_and_persist_findings()` is called
3. Sends the subagent's output/result to a Haiku LLM call with a structured extraction prompt
4. Haiku returns a list of findings with category, content, file_paths, and importance
5. Each finding is saved via `Finding.safe_save()` with the parent session's slug, project_key, and the dev-session's stage
6. CoOccurrenceField is updated: findings from the same extraction batch are co-associated

**Extraction prompt asks Haiku to identify:**
- Files examined and what was learned about them
- Patterns found in the codebase
- Decisions made and their rationale
- Artifacts produced (PRs, commits, test files)
- Dependencies discovered

**Graceful degradation:** If Haiku call fails, extraction is skipped silently. If slug is not set on the parent session, extraction is skipped (findings only apply to planned work items with slugs).

### Query: Finding Retrieval for Sub-Agents

New module `agent/finding_query.py` that sub-agents use to retrieve prior findings.

**CompositeScoreQuery** combines:
- Recency (DecayingSortedField score) -- weight 0.4
- Confidence (ConfidenceField) -- weight 0.3
- Access frequency (AccessTrackerMixin) -- weight 0.2
- Co-occurrence with current topic (CoOccurrenceField) -- weight 0.1

**Query interface:**
```python
def query_findings(slug: str, topics: list[str], limit: int = 10) -> list[Finding]:
    """Retrieve top findings for a work item, ranked by composite score."""
```

**ExistenceFilter pre-check:** Before running the full CompositeScoreQuery, check `Finding.bloom.might_exist(topic)` for each topic. If no bloom hits, skip the query entirely (O(1) short-circuit).

### Injection: Context Assembly for Sub-Agents

Two injection paths:

**Path A: Pre-dispatch injection (ChatSession -> DevSession prompt)**
When the PM dispatches a DevSession via the Agent tool, the pre_tool_use hook (or the SDLC skill itself) queries prior findings for the current slug and appends a "Prior Findings" section to the dev-session prompt. Uses ContextAssembler to stay within token budget.

**Path B: On-demand injection (DevSession PostToolUse)**
Extend the existing `memory_hook.py` pattern to also check findings. When the PostToolUse hook fires and the current session has a slug, check Finding.bloom for topic relevance and inject relevant findings as `<thought>` blocks alongside existing Memory thoughts.

**Token budget:** ContextAssembler manages this. Pre-dispatch injection gets a larger budget (up to ~2000 tokens of prior findings). On-demand injection shares the existing thought injection budget.

### Deduplication

When `_extract_and_persist_findings()` processes a new batch:

1. For each candidate finding, check `Finding.bloom.might_exist(content)`
2. If bloom says "maybe exists," run a full content similarity check (exact substring match on content field within the same slug)
3. If a duplicate is found: reinforce the existing finding's confidence via `ConfidenceField.update()` and refresh its AccessTracker, then discard the new record
4. If no duplicate: save as new finding

This ensures that when BUILD and TEST both discover "auth module uses JWT with RS256," the finding consolidates rather than duplicates.

### Decay

Findings decay naturally via DecayingSortedField:
- Active work items: findings stay hot because they are accessed frequently (AccessTracker refreshes decay score)
- Completed work items: findings fade over days/weeks as they are no longer accessed
- No explicit cleanup job needed -- DecayingSortedField handles this at query time

### Key Elements Summary

| Goal | Implementation |
|------|---------------|
| Findings persistence | Finding model + SubagentStop extraction via Haiku |
| Cross-agent search | CompositeScoreQuery on slug partition + ExistenceFilter pre-check |
| Context injection | Pre-dispatch prompt augmentation + PostToolUse thought injection |
| Natural decay | DecayingSortedField + AccessTracker refresh on access |
| Deduplication | Bloom pre-check + content similarity + ConfidenceField reinforcement |

## Success Criteria

- [ ] `Finding` model defined in `models/finding.py` with DecayingSortedField, ConfidenceField, WriteFilterMixin, AccessTrackerMixin, ExistenceFilter, and CoOccurrenceField
- [ ] SubagentStop hook extracts findings via Haiku on dev-session completion and persists them as Finding records scoped by slug
- [ ] `query_findings(slug, topics, limit)` retrieves top findings using CompositeScoreQuery with ExistenceFilter pre-check
- [ ] Pre-dispatch injection augments dev-session prompts with prior findings from the same slug (via ContextAssembler, within token budget)
- [ ] PostToolUse injection extends memory_hook to also inject relevant findings as `<thought>` blocks
- [ ] Deduplication: duplicate findings consolidate via ConfidenceField reinforcement instead of creating new records
- [ ] Natural decay: findings from inactive slugs fade via DecayingSortedField; accessed findings stay hot via AccessTracker refresh
- [ ] All extraction/query/injection failures are caught silently -- finding system never crashes the agent
- [ ] Unit tests cover Finding model, extraction, query, injection, and deduplication
- [ ] Integration test verifies end-to-end relay: extract from one session, inject into another
- [ ] Integration tests use real Redis (not mocks) for the full extract-store-query-inject pipeline
- [ ] At least one value measurement test proves findings from stage N appear in stage N+1 context
- [ ] Feature documentation at `docs/features/cross-agent-knowledge-relay.md`

## No-Gos

- No new orchestration primitives (wave scheduling, conditional branching)
- No changes to the Agent tool or sub-agent spawning mechanics
- No real-time streaming between concurrent agents (findings are persisted on completion)
- No cross-project finding leakage (slug-partitioned)
- No new vocabulary -- uses existing concepts (sessions, slugs, findings)
- No modification of the existing Memory model -- Finding is a separate model for a separate purpose

## Update System

No update system changes required. This feature is purely internal to the agent's memory layer. The Finding model uses Redis via Popoto (same as Memory), so no new infrastructure dependencies. The `scripts/remote-update.sh` and update skill need no modifications -- a standard `git pull && pip install -e .` picks up the new model and hook changes.

## Agent Integration

The agent gains access to prior findings through two automatic paths (no new MCP server needed):

- **SubagentStop hook** (`agent/hooks/subagent_stop.py`): Enhanced to extract findings on dev-session completion. This is bridge-internal code that fires automatically -- no MCP wrapping needed.
- **PostToolUse hook** (`agent/hooks/post_tool_use.py` -> `agent/memory_hook.py`): Enhanced to inject findings as `<thought>` blocks. Also automatic -- no MCP wrapping needed.
- **Pre-dispatch injection**: The SDLC skills (`.claude/commands/do-build.md`, etc.) or the `pre_tool_use` hook augments the dev-session prompt with prior findings. Internal to the hook/skill layer.

No changes to `.mcp.json` or `mcp_servers/` directory. No new tools exposed to the agent. The finding system is fully automatic -- extraction on completion, injection on dispatch and during tool use.

**Integration test:** A test that creates a mock dev-session completion, verifies findings are extracted and persisted, then verifies a subsequent mock dev-session receives those findings via the injection path.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_extract_and_persist_findings()` wraps Haiku call in try/except -- test with mock API failure
- [ ] `Finding.safe_save()` follows Memory.safe_save() pattern -- test with Redis connection error
- [ ] `query_findings()` handles empty slug, missing bloom index, CompositeScoreQuery failure
- [ ] Deduplication handles bloom false positives gracefully (content mismatch after bloom hit)

### Empty/Invalid Input Handling
- [ ] SubagentStop with no slug on parent session -- extraction skipped silently
- [ ] SubagentStop with empty/None output from dev-session -- extraction skipped
- [ ] Query with no findings for slug -- returns empty list, no error
- [ ] Injection with zero findings -- no `<thought>` blocks added, no error
- [ ] Finding with empty content -- filtered by WriteFilterMixin

### Degradation Scenarios
- [ ] Haiku extraction timeout -- findings not persisted, dev-session completion still recorded
- [ ] Redis unavailable during save -- Finding.safe_save() returns None, no crash
- [ ] Bloom index corrupted -- falls through to full query (slower but correct)

## Test Impact

No existing tests affected -- this is a greenfield feature that adds a new model (Finding), new extraction logic, and new injection paths. Existing test files are not modified:

- `tests/unit/test_subagent_stop_hook.py` -- existing tests remain valid; new tests are added alongside for the finding extraction path
- `tests/unit/test_memory_hook.py` -- existing tests remain valid; new tests are added for finding injection
- `tests/unit/test_memory_model.py` -- not modified; Finding model gets its own test file

New test files:
- `tests/unit/test_finding_model.py` -- Finding model CRUD, WriteFilter gating, bloom checks
- `tests/unit/test_finding_extraction.py` -- SubagentStop finding extraction with mock Haiku
- `tests/unit/test_finding_query.py` -- CompositeScoreQuery, ExistenceFilter pre-check, dedup
- `tests/unit/test_finding_injection.py` -- Pre-dispatch and PostToolUse injection paths
- `tests/integration/test_finding_relay.py` -- End-to-end: extract from one session, inject into another

## Rabbit Holes

- **Semantic similarity search**: Do not build embedding-based search. ExistenceFilter bloom + keyword matching + CoOccurrenceField is sufficient. Embeddings add latency and infrastructure complexity.
- **Cross-slug findings**: Do not propagate findings across work items. Each slug is isolated. If a pattern is truly general, it belongs in Memory (subconscious), not Finding.
- **Finding UI**: Do not build a web UI for browsing findings. This is a machine-to-machine system. Debugging can use Redis CLI or a simple script.
- **Finding export/sync**: Do not add cross-machine sync for findings. Findings are ephemeral by design (they decay). Patterns that survive should crystallize into ProceduralPatterns (#393).

## Integration & Value Measurement

The current test suite (57 tests) relies entirely on mocks. This section defines real Redis integration tests and value measurement tests that prove the pipeline works end-to-end.

### Real Redis Integration Tests

All tests below use actual Redis (no mocks for Redis or Popoto). Test file: `tests/integration/test_finding_relay.py` (replace existing mock-based content).

- [ ] **Finding round-trip**: `Finding.save()` to Redis, then retrieve by slug -- verify all fields persist correctly
- [ ] **Query round-trip**: Save multiple findings with different importance/stages, call `query_findings(slug, topics)`, verify ranked results come back from real Redis
- [ ] **Injection format**: Query findings from Redis, pass through `format_findings_for_injection()`, verify output contains correct `<thought>` block structure
- [ ] **Full pipeline**: SubagentStop extraction (with mock Haiku, real Redis) saves Finding records, then `query_findings()` retrieves them, then injection formats them -- complete extract-store-query-inject cycle
- [ ] **Deduplication round-trip**: Save a finding to Redis, then run extraction with a duplicate content string, verify the existing finding's confidence is reinforced (not a new record created)
- [ ] **Bloom filter with real data**: Save findings, verify `Finding.bloom.might_exist()` returns True for saved content and (probabilistically) False for unseen content

### Value Measurement Tests

These tests prove that findings from one SDLC stage actually appear in the next stage's context. Test file: `tests/integration/test_finding_value.py` (new file).

- [ ] **Cross-stage relay**: Create findings tagged `stage=BUILD` for a slug, then simulate a TEST-stage dev-session dispatch -- verify the pre-dispatch prompt includes BUILD-stage findings
- [ ] **PostToolUse injection**: Create findings for a slug in Redis, configure a session with that slug, trigger PostToolUse hook -- verify `additionalContext` contains finding `<thought>` blocks
- [ ] **Before/after comparison**: Run `query_findings()` for a slug with zero prior findings (returns empty), then save findings and re-query -- assert the "with" case returns actionable context (non-empty list with content matching saved findings)
- [ ] **Injection count**: Save N findings across BUILD and REVIEW stages, query from TEST stage context -- verify the count of injected findings matches expectations (respects limit parameter and token budget)
- [ ] **Relevance filtering**: Save findings with varying importance scores, query with a topic list -- verify higher-importance findings rank first and low-importance findings are excluded by WriteFilterMixin

### Mock Test Disposition

- [ ] `tests/unit/test_finding_injection.py` -- KEEP mock tests for Haiku API calls and error handling paths; REMOVE mock Redis tests (replaced by real Redis integration tests above)
- [ ] `tests/unit/test_finding_relay.py` -- DELETE entirely (replaced by `tests/integration/test_finding_relay.py` with real Redis)
- [ ] `tests/unit/test_finding_model.py` -- KEEP for model schema validation; REMOVE any mock Redis CRUD tests (covered by integration tests)
- [ ] `tests/unit/test_finding_extraction.py` -- KEEP mock Haiku extraction tests (external API); REMOVE mock Redis persistence tests
- [ ] `tests/unit/test_finding_query.py` -- KEEP mock tests for CompositeScoreQuery logic; ADD real Redis variants in integration suite

## Documentation

- [ ] Create `docs/features/cross-agent-knowledge-relay.md` describing the finding model, extraction flow, query interface, injection paths, and decay behavior
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/subconscious-memory.md` with a "Related: Cross-Agent Knowledge Relay" section explaining how Finding differs from Memory

## Implementation Order

1. **Finding model** (`models/finding.py`) -- model definition with all Popoto primitives
2. **Finding extraction** (`agent/finding_extraction.py`) -- Haiku-based extraction from dev-session output
3. **SubagentStop integration** -- wire extraction into `agent/hooks/subagent_stop.py`
4. **Finding query** (`agent/finding_query.py`) -- CompositeScoreQuery + ExistenceFilter
5. **Injection: PostToolUse** -- extend `agent/memory_hook.py` to also inject findings
6. **Injection: pre-dispatch** -- augment dev-session prompts with prior findings in pre_tool_use hook
7. **Deduplication** -- bloom pre-check + content similarity in extraction path
8. **Tests** -- unit and integration tests per Test Impact section
9. **Documentation** -- per Documentation section
