---
status: In Progress
type: feature
appetite: Small
owner: Valor
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/966
last_comment_id:
---

# Time-Range Memory Views & Hierarchical Consolidation

## Problem

All Memory records carry timestamps (via AccessTrackerMixin and DecayingSortedField relevance scores) but there is no time-sliced view (day, week, month) of what was observed, corrected, or decided. Humans cannot answer "what did we learn this week?" without running ad-hoc queries. The existing consolidation pipeline (memory-dedup) produces no browsable artifact.

## Design Decision

**On-demand reconstitution** via a new `timeline` CLI command, not materialized files.

Rationale:
- Records already carry timestamps via the `relevance` DecayingSortedField (which embeds creation time in its decay score) and `last_accessed` from AccessTrackerMixin
- Materialized daily-note files (like OpenClaw's `memory/YYYY-MM-DD.md`) go stale the moment consolidation runs and re-supersedes records
- Once #965 (embedding-enabled recall) ships, semantic timeline queries become far more powerful than static files
- The agent is the primary consumer; humans use the CLI for spot-checks

**Implementation:**
1. Add a `timeline` subcommand to `tools/memory_search` CLI that filters memories by time range
2. Add a `timeline()` function to the `tools/memory_search` Python API for programmatic use
3. Use the DecayingSortedField sorted set (which stores creation-time-based scores) to efficiently retrieve records within a time window via ZRANGEBYSCORE
4. Support grouping output by day/category for human readability

## Prior Art

- `tools/memory_search/cli.py` — existing CLI with `search`, `save`, `inspect`, `forget`, `status` commands
- `scripts/memory_consolidation.py` — existing nightly dedup reflection
- `agent/memory_retrieval.py` — BM25 + RRF fusion retrieval with `get_relevance_ranked()`
- OpenClaw `memory/YYYY-MM-DD.md` — materialized daily notes (rejected approach for this project)

## Data Flow

### Timeline Query

1. User runs `python -m tools.memory_search timeline --since "7 days ago"` (or `--from 2026-04-01 --to 2026-04-15`)
2. `timeline()` calls `get_memories_in_time_range()` which reads the DecayingSortedField sorted set via ZRANGEBYSCORE with min/max score bounds
3. Hydrates Memory instances from matched Redis keys
4. Groups by day and optionally by category
5. Renders human-readable or JSON output

### Score-to-Timestamp Mapping

The DecayingSortedField stores scores that combine importance and creation time. The score decays over time from the base_score_field ("importance"). By examining the sorted set scores, we can derive approximate creation times. However, for reliable time-range queries, we use the score range as a proxy: higher scores = more recent (at same importance level).

For the initial implementation, we use a simpler approach: iterate all records for the project and filter by `last_accessed` timestamp from AccessTrackerMixin (which is set on creation and each access). This is correct for small-to-medium datasets (<10k records). If performance becomes an issue, we can add a dedicated DateTimeField.

## Architectural Impact

- **Modified module:** `tools/memory_search/__init__.py` — add `timeline()` function
- **Modified module:** `tools/memory_search/cli.py` — add `timeline` subcommand
- **New utility:** `agent/memory_retrieval.py` — add `get_memories_in_time_range()` helper
- **Interface changes:** New public function `timeline()` in tools/memory_search
- **Coupling:** No new coupling — uses existing Memory model and retrieval infrastructure
- **Reversibility:** Fully reversible — additive changes only, no schema migrations

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- Review rounds: 1

Additive feature with no breaking changes. The timeline function is a read-only query over existing data.

## Prerequisites

None. Works with existing Memory model and data.

## Tasks

- [ ] Add `timeline()` function to `tools/memory_search/__init__.py`
- [ ] Add `get_memories_in_time_range()` to `agent/memory_retrieval.py`
- [ ] Add `timeline` subcommand to `tools/memory_search/cli.py`
- [ ] Write tests for timeline functionality
- [ ] Verify all existing tests still pass

## No-Gos

- No materialized daily-note files (go stale after consolidation)
- No schema migrations or new model fields (use existing timestamp data)
- No changes to the consolidation pipeline (separate concern)
- No embedding/vector search dependency (that is #965)

## Failure Path Test Strategy

- Empty project key returns empty timeline
- Invalid date range returns helpful error
- Time range with no matching records returns empty list
- Superseded records are filtered out (same as retrieval)

## Test Impact

No existing tests affected — this is a greenfield feature adding new functions and CLI commands to an existing module. Existing test_memory_search.py tests are unmodified.

## Documentation

- [ ] Add `timeline` command to CLAUDE.md quick reference table
- [ ] Update `docs/features/subconscious-memory.md` with timeline query documentation

## Update System

No update system changes required — this feature is purely internal to the memory search tool. No new dependencies or config files.

## Agent Integration

No agent integration required — the timeline function is exposed through the existing `tools/memory_search` CLI module which is already available to agents. No MCP server changes needed.

## Rabbit Holes

- **Exact creation timestamps:** The Memory model has no explicit `created_at` field. Using `last_accessed` as a proxy works for "when was this record last touched" but not "when was it originally created." Adding a DateTimeField is deferred until there is a concrete need beyond this feature.
- **Hierarchical summaries:** Daily -> weekly -> monthly digest consolidation is a separate pipeline concern. This feature provides the query surface; hierarchical summarization can be layered on top later via a reflection task.
- **Performance at scale:** Iterating all records per project is fine for <10k records. If the dataset grows significantly, a dedicated sorted set index by creation time would be the right optimization.
