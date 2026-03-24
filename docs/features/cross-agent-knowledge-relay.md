# Cross-Agent Knowledge Relay

Persistent findings from parallel sub-agent work. When ChatSession orchestrates work through sequential DevSessions (BUILD, TEST, REVIEW, etc.), each stage's discoveries are extracted, persisted, and injected into subsequent stages.

## How It Works

### Finding Model (`models/finding.py`)

A Popoto model storing structured technical discoveries scoped to work items (slugs):

- **slug**: Work item scope (partition key)
- **project_key**: Project partition
- **session_id**: Which DevSession produced the finding
- **stage**: SDLC stage (BUILD, TEST, REVIEW, etc.)
- **category**: file_examined, pattern_found, decision_made, artifact_produced, dependency_discovered
- **content**: Finding text (max 500 chars)
- **file_paths**: Comma-separated relevant file paths
- **importance**: Numeric score (1.0-10.0)

Popoto primitives used:
- `DecayingSortedField` partitioned by slug for natural decay of inactive work items
- `ConfidenceField` for deduplication reinforcement
- `WriteFilterMixin` to gate trivial findings
- `AccessTrackerMixin` to track which findings get reused
- `ExistenceFilter` (bloom) for O(1) topic pre-checks
- `CoOccurrenceField` linking findings from the same extraction batch

### Extraction (`agent/finding_extraction.py`)

When a DevSession completes, the SubagentStop hook (`agent/hooks/subagent_stop.py`) calls `extract_findings_from_output()`:

1. Sends the subagent's output to Haiku with a structured extraction prompt
2. Haiku returns a JSON array of findings with category, content, file_paths, importance
3. Each finding is deduplicated against existing findings for the same slug
4. New findings are saved via `Finding.safe_save()`
5. Findings from the same batch are co-associated via `CoOccurrenceField`

### Deduplication

When extracting new findings:

1. Check `Finding.bloom.might_exist(content)` for O(1) pre-filter
2. If bloom says "maybe exists," run full content similarity check (exact/substring match)
3. If duplicate found: reinforce existing finding's confidence, refresh access tracker
4. If no duplicate: save as new finding

### Query (`agent/finding_query.py`)

`query_findings(slug, topics, limit)` retrieves findings using manual composite scoring:

| Factor | Weight | Source |
|--------|--------|--------|
| Recency | 0.4 | DecayingSortedField (importance * decay) |
| Confidence | 0.3 | ConfidenceField |
| Access frequency | 0.2 | AccessTrackerMixin |
| Topic relevance | 0.1 | Keyword matching against content/file_paths |

ExistenceFilter pre-check: if topics are provided and no bloom hits, the full query is skipped entirely (O(1) short-circuit).

### Injection

Two injection paths ensure sub-agents receive prior findings:

**Path A: Pre-dispatch injection** (`agent/hooks/pre_tool_use.py`)
When ChatSession dispatches a DevSession via the Agent tool, the PreToolUse hook queries prior findings for the current slug and appends a "Prior Findings" section to the prompt. Budget: up to 2000 tokens.

**Path B: On-demand injection** (`agent/memory_hook.py`)
The PostToolUse hook's memory injection also checks findings when the current session has a slug. Relevant findings are injected as `<thought>` blocks alongside memory thoughts.

### Decay

Findings decay naturally via DecayingSortedField:
- Active work items: findings stay hot because they are accessed frequently
- Completed work items: findings fade over days/weeks as they are no longer accessed
- No explicit cleanup job needed

## Files

| File | Purpose |
|------|---------|
| `models/finding.py` | Finding Popoto model |
| `agent/finding_extraction.py` | Haiku-based extraction + deduplication |
| `agent/finding_query.py` | Composite scoring query + formatting |
| `agent/memory_hook.py` | PostToolUse finding injection (extended) |
| `agent/hooks/subagent_stop.py` | SubagentStop extraction trigger (extended) |
| `agent/hooks/pre_tool_use.py` | Pre-dispatch prompt injection (extended) |

## Error Handling

All finding operations fail silently:
- Extraction failures: logged, dev-session completion still recorded
- Query failures: return empty list
- Injection failures: no `<thought>` blocks added
- Redis unavailable: `Finding.safe_save()` returns None
- Haiku API failure: extraction skipped
- Bloom corruption: falls through to full query (slower but correct)

## Related

- [Subconscious Memory](subconscious-memory.md) -- General observations/instructions stored as Memory records. Finding is distinct: work-item-scoped technical discoveries for cross-agent relay.
- [Pipeline State Machine](pipeline-state-machine.md) -- Tracks which SDLC stage is in progress; findings are tagged with the current stage.
