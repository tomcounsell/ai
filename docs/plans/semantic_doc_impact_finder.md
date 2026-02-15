---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-15
tracking: https://github.com/tomcounsell/ai/issues/109
---

# Semantic Doc Impact Finder for /update-docs

## Problem

`/update-docs` finds affected documentation via lexical matching — regex for file paths, function names, class names, and keywords (`scripts/scan_related_docs.py`). This catches direct references but misses conceptual coupling.

**Current behavior:**
- Agent A reads the diff, Agent B inventories docs, then triage cross-references using keyword matching
- `scan_related_docs.py` finds docs that mention `worktree_manager` when `worktree_manager.py` changes
- BUT: renaming how "sessions are scoped" won't find `docs/features/session-isolation.md` if the doc never mentions the changed function names
- Semantic relationships (rate limiting → self-healing, thread IDs → session isolation) are invisible to grep

**Desired outcome:**
- A third signal (Agent C) uses embedding similarity + LLM reranking to find conceptually related docs
- Step 2 triage merges all three signals (lexical grep + agent triage + semantic search) — union means nothing slips through
- Cost is pennies per run (local cosine similarity is free, Haiku reranking is ~5-15 calls)

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Embedding API access | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('OPENAI_API_KEY') or dotenv_values('.env').get('VOYAGE_API_KEY') or dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Generate embeddings for doc chunks |
| Anthropic API key | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | LLM reranking with Haiku |

## Solution

### Key Elements

- **Doc chunker**: Splits markdown files on `##` headings into individually embeddable sections
- **Embedding index**: Pre-computed embeddings for all doc sections, stored as flat JSON with content hashes for cache invalidation
- **Semantic search**: Query embedding from change summary → cosine similarity → top-N candidates
- **LLM reranker**: Haiku scores each candidate chunk against the change summary (0-10) with explanation
- **Integration**: Slots into `/update-docs` Step 1 as Agent C alongside existing Agents A and B

### Flow

**Change arrives** → [Agent C receives change summary] → [Query embedding computed] → [Cosine similarity against doc index] → [Top-N candidates to Haiku reranker] → [Scored + explained results merged into Step 2 triage]

### Technical Approach

**1. Indexer** (`tools/doc_impact_finder.py::index_docs()`)

Walk all documentation locations:
- `docs/**/*.md`
- `CLAUDE.md`
- `.claude/skills/*/SKILL.md`
- `.claude/commands/*.md`
- `config/SOUL.md`

For each file:
- Split on `##` headings (each section = one chunk)
- Compute content hash (SHA-256 of chunk text)
- If hash matches existing index entry, skip re-embedding
- Otherwise, embed via API and store

Storage: `data/doc_embeddings.json`
```json
{
  "version": 1,
  "model": "text-embedding-3-small",
  "chunks": [
    {
      "path": "docs/features/session-isolation.md",
      "section": "## Tier 1 (thread-scoped)",
      "content_hash": "abc123...",
      "embedding": [0.012, -0.034, ...],
      "content_preview": "First 200 chars..."
    }
  ]
}
```

At this repo's scale (~50-80 doc sections), full re-index is seconds and costs <$0.01.

**2. Finder** (`tools/doc_impact_finder.py::find_affected_docs()`)

```python
def find_affected_docs(change_summary: str, top_n: int = 15) -> list[AffectedDoc]:
    """Two-stage: embedding recall then LLM reranking."""
```

Stage 1 — Embedding recall:
- Embed the change summary
- Cosine similarity against all indexed chunks
- Take top-N (default 15) — generous threshold favoring recall over precision

Stage 2 — LLM reranking:
- For each candidate chunk, send to Haiku:
  - Change summary
  - Doc section content
  - Question: "Does this documentation section need updating given this change? Score 0-10 and explain."
- Filter to score >= 5
- Return sorted by score with path, section, relevance, and reason

**3. Integration with `/update-docs`**

In Step 1, add Agent C as a third parallel agent:

```
Agent A — Change Explorer (reads the diff)        [existing]
Agent B — Documentation Inventory (lists all docs) [existing]
Agent C — Semantic Impact Finder (vector search + rerank) [NEW]
```

Agent C prompt:
```
Run the semantic doc impact finder against this change:

Change summary: {agent_a_output_or_change_description}

1. Ensure the doc index is current: python -c "from tools.doc_impact_finder import index_docs; index_docs()"
2. Find affected docs: python -c "from tools.doc_impact_finder import find_affected_docs; import json; print(json.dumps([d.dict() for d in find_affected_docs('''<change_summary>''')]))"

Report the results as a ranked list with paths, sections, and reasons.
```

Step 2 triage merges:
- Agent A's key terms + Agent B's inventory → existing lexical cross-reference
- Agent C's semantic results → additional candidates with relevance scores
- Union of all three → final affected docs list

**4. Embedding provider choice**

Priority order:
1. **OpenAI `text-embedding-3-small`** — cheapest ($0.02/M tokens), 1536 dims, excellent quality
2. **Voyage `voyage-3-lite`** — good alternative if already using Voyage
3. **Anthropic** — if available via their embeddings API

Use whatever key is available in `.env`. Fall back gracefully — if no embedding API key, skip Agent C and log a warning (the cascade still works with Agents A+B alone).

## Rabbit Holes

- **Vector database**: Don't use Pinecone, Weaviate, Chroma, etc. Flat JSON + numpy cosine similarity is perfect for <100 chunks. Adding a DB adds dependency and complexity for zero benefit at this scale.
- **sqlite-vec**: The issue mentions this as an option. Don't bother — the overhead of setting up SQLite extensions isn't worth it for <100 vectors. A flat JSON file with numpy is simpler and just as fast.
- **Real-time indexing via git hooks**: Don't. Index on-demand when `/update-docs` runs. Post-commit hooks are fragile and this operation is fast enough to run every time.
- **Embedding the code changes too**: Don't embed diffs. The change summary (natural language from Agent A) is a much better query than raw diff text.
- **Fine-tuning embeddings**: Off-the-shelf embeddings are excellent for doc-to-doc similarity. No fine-tuning needed.

## Risks

### Risk 1: Embedding API latency
**Impact:** Adds seconds to `/update-docs` if re-indexing many chunks
**Mitigation:** Content hashing means only changed chunks get re-embedded. At <100 chunks, even full re-index is fast. Agent C runs in parallel with A and B, so latency is hidden.

### Risk 2: False positives from semantic search
**Impact:** Haiku reranker gets fed irrelevant chunks, wasting API calls
**Mitigation:** The reranker is explicitly designed to filter false positives — that's its job. At 15 candidates max, the cost is ~$0.01 in Haiku calls. Acceptable.

### Risk 3: No embedding API key available
**Impact:** Agent C can't run
**Mitigation:** Graceful degradation — if no embedding API key, the finder returns an empty list and logs a warning. The cascade continues with Agents A+B only. Document in setup instructions.

## No-Gos (Out of Scope)

- External vector databases (Pinecone, Weaviate, Chroma)
- Replacing the existing lexical scanner (`scan_related_docs.py`) — this complements it
- Re-architecting `/update-docs` beyond adding Agent C
- Embedding code files or diffs (only docs are indexed)
- Automatic re-indexing via git hooks

## Update System

- New dependency: `numpy` (for cosine similarity) — add to requirements
- New optional dependency: `openai` or `voyageai` (for embeddings) — check what's already installed
- New data file: `data/doc_embeddings.json` — created on first run, gitignored (machine-local cache)
- Add `OPENAI_API_KEY` or `VOYAGE_API_KEY` to `.env.example` as optional
- Update script should install new deps if any

## Agent Integration

No new MCP server needed. The finder is a Python tool in `tools/doc_impact_finder.py` that agents invoke via `python -c` or `python -m`. It integrates into `/update-docs` through the existing agent orchestration pattern (Agent C is spawned by the cascade orchestrator just like Agents A and B).

Changes needed:
- Add `tools/doc_impact_finder.py` — the new tool
- Update `.claude/commands/update-docs.md` — add Agent C to Step 1
- No changes to `.mcp.json` or bridge

## Documentation

- [ ] Create `docs/features/semantic-doc-impact-finder.md` describing the tool
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `.claude/commands/update-docs.md` with Agent C instructions
- [ ] Add embedding API key to setup documentation

## Success Criteria

- [ ] `tools/doc_impact_finder.py` exists with `index_docs()` and `find_affected_docs()` functions
- [ ] `index_docs()` chunks all markdown docs on `##` headings and embeds them
- [ ] Content hashing avoids re-embedding unchanged chunks
- [ ] `find_affected_docs(summary)` returns ranked `AffectedDoc` results with paths, sections, relevance scores, and reasons
- [ ] Semantic search catches conceptual matches that grep misses (test: "changed session scoping" → finds session-isolation.md)
- [ ] Haiku reranker filters false positives (score < 5 excluded)
- [ ] Graceful degradation when no embedding API key is available
- [ ] `/update-docs` Step 1 launches Agent C in parallel with A and B
- [ ] Step 2 triage merges all three agent signals
- [ ] `data/doc_embeddings.json` is gitignored
- [ ] All existing tests pass
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (tool)**
  - Name: finder-builder
  - Role: Implement `tools/doc_impact_finder.py` with indexer, chunker, embedding, and reranker
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Wire Agent C into `/update-docs` cascade
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: finder-validator
  - Role: Verify semantic search finds conceptual matches, reranker filters noise, graceful degradation works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Build the doc impact finder tool
- **Task ID**: build-finder
- **Depends On**: none
- **Assigned To**: finder-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/doc_impact_finder.py` with:
  - `AffectedDoc` Pydantic model (path, relevance, sections, reason)
  - `chunk_markdown(content, file_path)` — split on `##` headings, return list of chunks with metadata
  - `index_docs()` — walk all doc locations, chunk, hash, embed unchanged chunks, save to `data/doc_embeddings.json`
  - `load_index()` — read the flat JSON index
  - `find_affected_docs(change_summary, top_n=15)` — two-stage: embedding recall + Haiku reranking
  - `cosine_similarity(a, b)` — numpy dot product
  - Embedding provider detection: check for `OPENAI_API_KEY`, `VOYAGE_API_KEY`, or `ANTHROPIC_API_KEY` in env
  - Graceful degradation: return empty list + warning if no embedding API key
- Add `data/doc_embeddings.json` to `.gitignore`
- Add tests in `tests/test_doc_impact_finder.py`:
  - Test chunking splits on `##` headings correctly
  - Test content hash caching (unchanged chunks not re-embedded)
  - Test graceful degradation with no API key
  - Test cosine similarity math

### 2. Integrate Agent C into /update-docs
- **Task ID**: build-integration
- **Depends On**: build-finder
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/commands/update-docs.md`:
  - Step 1 adds Agent C — Semantic Impact Finder
  - Agent C runs in parallel with existing Agents A and B
  - Agent C prompt: run `index_docs()` then `find_affected_docs()` with change summary
  - Step 2 triage instructions updated to merge three signals (lexical + inventory + semantic)
- Ensure Agent C failure doesn't block the cascade (wrap in try/catch, log warning)

### 3. Validate
- **Task ID**: validate-finder
- **Depends On**: build-integration
- **Assigned To**: finder-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `tools/doc_impact_finder.py` exists and imports cleanly
- Verify `index_docs()` produces `data/doc_embeddings.json` with chunked entries
- Verify `find_affected_docs("changed session scoping")` returns session-related docs
- Verify graceful degradation: unset all embedding API keys, confirm empty results + warning
- Verify `/update-docs` command references Agent C
- Run `pytest tests/test_doc_impact_finder.py -v`
- Run `ruff check . && black --check .`

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-finder
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/semantic-doc-impact-finder.md`
- Add entry to `docs/features/README.md` index
- Update setup docs with optional embedding API key

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: finder-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria met
- Verify documentation exists and is indexed
- Run `pytest tests/ -v`

## Validation Commands

- `python -c "from tools.doc_impact_finder import index_docs, find_affected_docs; print('imports ok')"` - Tool imports cleanly
- `python -c "from tools.doc_impact_finder import index_docs; index_docs()"` - Indexing runs without error
- `test -f data/doc_embeddings.json` - Index file created
- `grep -q doc_embeddings .gitignore` - Index file is gitignored
- `grep -q 'Agent C' .claude/commands/update-docs.md` - Integration in cascade
- `pytest tests/test_doc_impact_finder.py -v` - Unit tests pass
- `pytest tests/ -v` - All tests pass
- `ruff check .` - Linting
- `black --check .` - Formatting
