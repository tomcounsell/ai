# Agent Memory Landscape — External Approaches Worth Learning From

> Reference doc compiled 2026-03-21 from systematic HN research (99 posts, Jan-Mar 2026).
> Context: Our planned system (issues #393, #394) already covers decay, confidence, co-occurrence,
> write gating, prediction tracking, episode→pattern crystallization, persona/project partitioning,
> and token-budget context assembly via Popoto ORM primitives.
> This doc captures ideas from the field that go **beyond** what we've planned.

---

## 1. Vector-Seeded Graph Traversal (from GibRAM)

**Source:** [GibRAM](https://github.com/gibram-io/gibram) — ephemeral in-memory GraphRAG runtime (Go)

**The pattern:** A 4-phase query pipeline that combines vector search with graph exploration:
1. HNSW vector search finds seed entities by embedding similarity
2. BFS graph expansion from seeds (configurable K hops, default 2)
3. Relationship collection between all discovered entities (maintaining graph coherence)
4. Composite scoring — direct matches get vector similarity, graph-discovered entities get inverse hop distance (`1.0 / (1 + hop_count)`)

**Why it matters:** Our ContextAssembler treats memory as flat ranked items. This pattern would let CoOccurrenceField links be *traversed* at retrieval time — surfacing contextually related memories that pure scoring misses. "Vector search finds the entry point; graph traversal finds the neighborhood."

**Applicability:** Could enhance ContextAssembler with a lightweight BFS expansion step over CoOccurrenceField edges before final token-budget assembly.

**Also from GibRAM:** Leiden community detection for automatic memory clustering into hierarchical topic groups. Heavier lift, but enables "zoom out" queries ("what topics exist?").

---

## 2. Entity-Page Crystallization (from NERDs)

**Source:** [NERDs paper](https://doi.org/10.36227/techrxiv.177156112.28008669/v1) — "Thinking Like a NERD: Entity-Centered Memory for LLM Agents" (Feb 2026)

**The pattern:** Maintain living natural-language documents (Wikipedia-style) per entity. A "NERD-Writer" agent processes information chunk-by-chunk, appending to and revising the appropriate entity page. A "NERD-Actor" retrieves entity pages at query time and follows hyperlinks between entities.

**Key property:** Query cost is O(1) regardless of history length — bounded by entity page size, not corpus size.

**Why it matters:** Our system is strong on temporal/behavioral queries ("what happened?", "what works?") but lacks a first-class entity concept. Adding entity-summary crystallization to the Reflections pipeline would give us NERDs' flat-scaling retrieval for "who is X?" and "how do X and Y relate?" queries.

**Applicability:** Add an entity crystallization Reflections task that maintains per-entity prose summaries. ContextAssembler can retrieve these as a complementary layer alongside episode/pattern records.

**Gotchas to avoid:**
- Entity coreference/dedup is unsolved in NERDs — need dedup from day one
- No entity rename/merge tooling — causes fragmented knowledge
- Weak on non-entity queries (counting, temporal ordering) — don't sacrifice our episode strengths

---

## 3. Deterministic Scoring Without LLM Calls (from Anchor Engine)

**Source:** [Anchor Engine](https://github.com/RSBalchII/anchor-engine-node) — deterministic semantic memory (<3GB RAM)

**The pattern:** The STAR scoring equation combines three signals with no LLM inference:
```
W(q,a) = |T(q) ∩ T(a)| * γ^d(q,a) * e^(-λΔt) * (1 - H(h_q, h_a)/64)
```
- **Semantic gravity:** shared tags, damped by graph hop distance (γ = 0.85)
- **Temporal decay:** exponential recency (~115-min half-life)
- **Structural similarity:** 64-bit SimHash fingerprint proximity

Same query + same data = identical results every time. No embedding drift.

**Why it matters:** Validates that our DecayingSortedField + CoOccurrenceField can serve as a complete retrieval system without vector embeddings. The three STAR components map directly to our primitives.

**Also from Anchor Engine:**
- **Pointer-only storage:** Database stores metadata/scores/indices; content lives on disk. Index is disposable and rebuildable from source. Applicable to keeping Redis lean.
- **Provenance receipts:** Every retrieval returns *why* it matched (shared tags, hop distance, temporal score, source location). Our ContextAssembler should emit similar explanations.

---

## 4. LLM at Write, Never at Read (from Mnemora)

**Source:** [Mnemora](https://news.ycombinator.com/item?id=47260077) — serverless memory DB, no LLM in CRUD path

**The pattern:** Push all LLM intelligence to write time (embedding generation, fact extraction, entity resolution). All reads are pure database queries — DynamoDB lookups at sub-10ms for working memory, pgvector for semantic search. Result: sub-10ms reads where competitors get 200-500ms.

**Four memory types with appropriate storage:**
| Type | Storage | Latency |
|------|---------|---------|
| Working memory | Key-value (DynamoDB) | <10ms |
| Semantic memory | pgvector + embeddings | ~50ms |
| Episodic memory | Append-only logs (S3) | varies |
| Procedural memory | Rules/definitions | <10ms |

**Why it matters:** Reinforces that our Reflections pipeline (background crystallization) is where LLM intelligence belongs. ORM primitives should be pure data operations that never call an LLM. This should be an explicit architectural principle.

**Design principle:** "The retrieval path is computation, not inference."

---

## 5. Proactive Context Pre-Assembly (from memU)

**Source:** [memU](https://github.com/NevaMind-AI/memU) — file-based agent memory framework

**The pattern:** A background "memU Bot" runs alongside the main agent with two retrieval modes:
- **Reactive:** Standard embedding-based lookup (sub-second)
- **Proactive:** LLM predicts intent from conversation trajectory, pre-fetches relevant context, evolves queries as conversation develops

The memory system anticipates what context will be needed next, rather than waiting for explicit queries.

**Why it matters:** Our StreamConsumer is already positioned for background processing, but currently focused on crystallization. Adding intent prediction (watching the conversation stream to pre-assemble likely-needed context) would reduce latency at retrieval time.

**Also from memU:** "LLM reads structured Markdown instead of embedding search" — for time-sensitive facts, configs, and multi-step reasoning, having the LLM directly read structured files outperforms vector similarity. Validates our CLAUDE.md / memory file approach over pure RAG.

---

## 6. Hierarchical Compression with Aggressive Pruning (from HAM + KVzip + TTT)

**Sources:**
- [HAM](https://news.ycombinator.com/item?id=47176537) — Hierarchical Agent Memory (Claude Code-specific)
- [KVzip](https://arxiv.org/abs/2505.23416) — KV cache compaction (NeurIPS 2025)
- [TTT-E2E](https://developer.nvidia.com/blog/reimagining-llm-memory-using-context-as-training-data-unlocks-models-that-learn-at-test-time/) — Nvidia test-time training

**The convergent pattern:** Every successful approach compresses aggressively at multiple levels:

| Level | Technique | Compression |
|-------|-----------|-------------|
| File system | HAM: directory-scoped context files | 80% token reduction (4-12K → 800-2.4K) |
| KV cache | KVzip: importance-scored pair pruning | 3-4x reduction, minimal accuracy loss |
| Application | Multi-level summarization (L0 per-exchange → L1 lessons) | ~70% fewer tokens |
| Model weights | TTT-E2E: encode context into weights, discard cache | Constant latency regardless of context length |

**Why it matters:** Our CyclicEpisode → ProceduralPattern → ContextAssembler pipeline is already a three-stage compression hierarchy. Three additions inspired by this:

1. **Importance-scored context selection** in ContextAssembler using a "reconstruction loss" heuristic (can the agent still reason correctly without this chunk?) rather than pure recency
2. **Aggressive source-episode GC** after successful pattern crystallization — once episodes are "compressed into" a ProceduralPattern, discard the originals (inspired by TTT's "encode then discard")
3. **Progressive tool-result stripping** — tool outputs are only useful for a few turns, then should be aggressively summarized

---

## 7. Semantic Annotations for Memory Records (from HADS)

**Source:** [HADS convention](https://github.com/catcam/hads) — discussed in HN "Markdown-based context" thread

**The pattern:** Four semantic block types in plain Markdown:
- `[SPEC]` — authoritative facts (ground truth)
- `[NOTE]` — contextual information (helpful but not binding)
- `[BUG]` — known failures (negative knowledge)
- `[?]` — unverified claims (low confidence)

Reported ~70% reduction in per-query token load. Small models (7B) handle it well because "the tags remove the structural reasoning problem entirely."

**Why it matters:** Maps naturally to our ConfidenceField. We could tag memory records with semantic type (spec/note/bug/unverified) that guides both retrieval priority and how the agent treats the information. A `[SPEC]` memory would have high base confidence; a `[?]` memory would decay faster.

---

## 8. Contradiction Detection (from HN Discussion)

**Source:** HN discussion on Rowboat (205 pts, 56 comments)

**The pattern:** Multiple LLM "personas" independently review the same content, then a reconciliation step flags disagreements. Contradictions surface when independent reviewers reach different conclusions from the same data.

**Why it matters:** Our system has no contradiction detection planned. As memories accumulate, contradictory facts will appear (especially across projects or over time). A periodic Reflections sub-task that scans for contradictions — or a WriteFilter that checks new memories against existing ones — would prevent the knowledge base from containing "revenue increased" and "revenue decreased" simultaneously.

**Related insight from Anchor Engine:** Use timestamps as truth arbitrator — when contradictory information exists, most recent wins. Simple but effective as a default.

---

## 9. Correction-as-Signal (from Mem0 Critique)

**Source:** [HN discussion](https://news.ycombinator.com/item?id=46891715) — "Mem0 stores memories but doesn't learn user patterns"

**The anti-pattern (what Mem0 gets wrong):**
- Treats user corrections as generic conversation content
- Extracts atomic facts via LLM but never synthesizes behavioral patterns
- No decay, no confidence evolution, no temporal reasoning
- Each `add()` call is independent — no background cross-memory analysis

**The pattern to adopt:** User corrections and overrides are the highest-signal data for preference learning. Build explicit tracking of when the user overrides, corrects, or rejects agent output. These events should feed directly into confidence adjustments on existing memories.

**Why it matters:** Our ConfidenceField + PredictionLedger are well-positioned for this. The specific addition: when the PredictionLedger records a miss (agent predicted X, user corrected to Y), that should directly weaken confidence on the pattern that produced X and strengthen/create patterns aligned with Y.

---

## 10. Negative Pattern Tracking (from "Operational Memory" Discussion)

**Source:** [HN discussion](https://news.ycombinator.com/item?id=47462910) — "Is operational memory a missing layer?"

**The pattern:** Explicitly track failure modes and anti-patterns alongside successful patterns:
- Tool-specific quirks discovered during execution
- Expensive-to-rediscover failure modes
- Environment-specific gotchas

**Why it matters:** Our ProceduralPattern crystallization focuses on what works. Adding negative patterns ("this approach always fails in this context") prevents the agent from re-discovering known dead ends. The PredictionLedger already tracks misses — crystallizing persistent misses into negative ProceduralPatterns is a natural extension.

---

## Summary: What Our System Already Covers vs. What's Novel

### Already covered by issues #393/#394:
- Temporal decay (DecayingSortedField) — validated by Anchor Engine, HAM, KVzip
- Confidence scoring (ConfidenceField) — validated by Mem0 critique
- Co-occurrence tracking (CoOccurrenceField) — validated by "memory is broken" article
- Write gating (WriteFilter) — validated by Mnemora's approach
- Background crystallization (Reflections + StreamConsumer) — validated by memU, NERDs, TTT
- Token-budget assembly (ContextAssembler) — validated by HAM, KVzip
- Behavioral episode capture (CyclicEpisode) — validated by operational memory concept
- Pattern extraction (ProceduralPattern) — validated by Mem0 critique, NERDs

### Novel ideas worth adopting:
1. **Graph traversal at retrieval** — BFS over CoOccurrenceField edges (#1)
2. **Entity-page crystallization** — living per-entity summaries in Reflections (#2)
3. **Provenance receipts** — ContextAssembler explains *why* each item was included (#3)
4. **Proactive context pre-assembly** — predict needed context before query (#5)
5. **Source-episode GC** — discard episodes after pattern crystallization (#6)
6. **Semantic type tags** — [SPEC]/[NOTE]/[BUG]/[?] on memory records (#7)
7. **Contradiction detection** — periodic Reflections sub-task (#8)
8. **Correction-as-signal** — PredictionLedger misses weaken source patterns (#9)
9. **Negative pattern tracking** — crystallize persistent failures (#10)

### Explicit design principles validated:
- "LLM at write, never at read" — push intelligence to Reflections, keep ORM ops pure computation
- "Compress aggressively, preserve selectively" — multi-level hierarchy is the winning pattern
- "Graph-as-index, not graph-as-context" — use structure to locate, then inject selectively
