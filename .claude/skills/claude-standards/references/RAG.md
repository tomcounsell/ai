# RAG Reference

Guidance for building retrieval-augmented generation pipelines — feeding a subset of a large corpus into the model's prompt instead of dumping the whole corpus. Read when the corpus is too big, too varied, or too dynamic to fit in every prompt. For prompting techniques that make retrieved context useful once it's in the prompt, see [`PROMPT_ENGINEERING.md`](PROMPT_ENGINEERING.md). For measuring retrieval quality, see [`EVALS.md`](EVALS.md). For tool-based retrieval alternatives (e.g., web search), see [`TOOL_USE.md`](TOOL_USE.md).

---

## When to reach for RAG

The simplest approach — paste the whole document into the prompt — works until it doesn't. RAG becomes the right answer when:

- The corpus exceeds the model's context window (one huge doc, or many docs).
- The corpus changes faster than you want to refine the prompt.
- Cost or latency of processing the full corpus per request is unacceptable.
- Long-context performance on your specific task is noticeably worse than targeted-context performance. Models attend less reliably to the middle of long inputs; a focused 2K-token context often beats a sprawling 100K-token one.

RAG adds complexity: a chunking strategy, an index, a retrieval step, and the eval harness to tell whether retrieval is working. If pasting the whole document works and stays cheap, skip RAG.

---

## The pipeline, end to end

A minimal RAG system has two phases:

**Pre-processing (offline):**

1. Chunk the source documents.
2. Embed each chunk (and optionally index for lexical search).
3. Store chunks + vectors + metadata in an index.

**Query time (online):**

4. Embed the user query.
5. Retrieve the top-K most similar chunks.
6. Assemble a prompt that includes the user query and the retrieved chunks.
7. Call the model.

Every subsequent technique in this doc improves one of those steps: chunking (step 1), retrieval (step 5), or context quality (step 6).

---

## Chunking strategies

Chunks are the retrieval unit. Quality in, quality out — a good query against badly-chunked content still retrieves junk.

- **Size-based (fixed character/token length).** Split into ~N-character pieces. Simple, works on any document. Trade-off: cuts words and sentences mid-stream. Add **overlap** (each chunk includes ~10–20% of the previous chunk's tail) to preserve context across boundaries at the cost of some duplication.
- **Structure-based (headers, paragraphs, sections).** Split on explicit markers: markdown headers, HTML sections, paragraph breaks. Right when the source has reliable structure. Wrong when structure is inconsistent (user-generated content, OCR output).
- **Semantic-based (NLP-driven grouping).** Group consecutive sentences by semantic similarity — keep topically-related sentences together. Best retrieval quality in principle; highest implementation cost; fragile on noisy text.

No universal winner. Default to size-based with overlap if you don't know the corpus; graduate to structure-based when you control the input format.

Chunk size is a knob. Too small and chunks lack context. Too big and retrieval is coarse (you fetch a whole section when you wanted a paragraph). 500–1500 tokens is a reasonable default, tuned against eval results.

---

## Embeddings and semantic search

An embedding is a vector representation of a chunk's meaning. Two chunks with related meanings have similar vectors; two unrelated chunks don't. Retrieval compares the query's embedding to chunk embeddings and returns the closest.

Key concepts:

- **Cosine similarity** — angle between vectors, range -1 to 1, higher is more similar. The standard metric.
- **Cosine distance** — `1 - cosine_similarity`, lower is closer. Some APIs return one, some the other; check before sorting.
- **Normalization** — vectors scaled to length 1 so similarity comparisons are apples-to-apples. Most embedding APIs normalize automatically.

Same embedding model on both sides. The query embedding and the chunk embeddings must come from the same model, or the comparison is meaningless. Switching embedding models invalidates the entire index — pick one and commit.

---

## Lexical search (BM25)

Semantic search is strong at "same meaning, different words" and weak at exact-term matching. A query for "CVE-2024-12345" should prefer documents literally containing that string; semantic search may rank a paragraph about "recent security vulnerabilities" higher even without the specific CVE.

BM25 (Best Match 25) is the standard lexical algorithm:

1. Tokenize the query (strip punctuation, split on spaces).
2. Count each term's frequency across the corpus — common words ("the", "a") get low weight, rare words get high weight.
3. Rank documents by how often they contain the high-weight terms.

BM25 is fast, interpretable, no training step. It fails where semantic search succeeds (paraphrases, synonyms) and succeeds where semantic search fails (exact names, identifiers, jargon).

---

## Hybrid retrieval

Run semantic and lexical search in parallel, then merge the results. Each method covers the other's weakness.

**Reciprocal Rank Fusion (RRF)** is the standard merging technique:

```
score(doc) = sum over each method of: 1 / (rank_in_method + constant)
```

A document that appears high in both lists beats one that ranks first in only one. The constant (typically ~60) dampens the difference between top ranks so no single method dominates.

RRF is a rank combiner, not a score predictor — it doesn't care about raw similarity numbers from either method. That makes it robust to scale mismatches between systems.

Implementation pattern: a `Retriever` class owns both indexes, calls `search()` on each, merges with RRF, returns the fused top-K.

---

## Reranking

Retrieval is fast and approximate. Reranking is slower and more accurate. After you've retrieved the top 20–50 candidates from hybrid search, pass them through an LLM (or cross-encoder) that scores *relevance to the query* directly, then keep the top 3–10.

The reranker sees the query and candidate chunks together, unlike embedding search which compares vectors computed independently. This lets it catch nuance — that "ENG team" in the query means "engineering team" in the docs, not "England team."

Implementation pattern:

1. Hybrid search returns top 30 chunks (by ID).
2. Construct a reranking prompt: user query plus chunk IDs and contents.
3. Ask the LLM to return the top N chunk IDs in decreasing relevance, as JSON (use prefill+stop for a clean response).
4. Use the reranked chunks as context.

Tradeoffs: reranking adds a second LLM call per query (latency + cost). Use when initial retrieval is the bottleneck — which you can only tell by running evals on your actual queries.

---

## Contextual retrieval

A chunk ripped from its document loses context. "The methodology section describes a phased rollout" makes sense inside the original doc but is opaque on its own — methodology of what? rollout of what?

Contextual retrieval fixes this by prepending a short, generated context to each chunk *before* indexing:

1. For each chunk, send chunk + source document to an LLM with a prompt: "Summarize in 1–2 sentences how this chunk relates to the whole document."
2. Concatenate the generated context with the chunk text.
3. Index the contextualized chunk (both embeddings and BM25).

The extra context makes the chunk retrievable by queries that don't use the chunk's literal words but do use the document's broader vocabulary.

For large source documents that don't fit in one prompt, use a subset: the first few chunks (abstract/summary) plus the chunks immediately before the target (local context). Skip the middle.

Cost: one extra LLM call per chunk at index time. Doesn't affect query-time latency.

---

## Citations

Once the model answers from retrieved chunks, users benefit from knowing *which* chunks produced the answer. The Citations feature attaches source attribution to generated content:

- For PDFs: `citation_page_location` with document index, title, start/end page, cited text.
- For plain text: `citation_char_location` with character positions.

Enable with `"citations": {"enabled": true}` on the request and provide a `title` for each source document. The response content becomes a list of text blocks, some carrying a `citations` array pointing to source material.

UI application: render cited spans as clickable/hoverable, popping up the source document, page, and exact quoted text. This shifts the user from "trust the model" to "verify against the source" — a significant reliability improvement for document-grounded Q&A.

Citations pair naturally with RAG: the model is already reasoning from retrieved chunks, so attributing its claims to those chunks is cheap and honest.

---

## Evaluating a RAG pipeline

Most RAG failures are retrieval failures, not generation failures. Before optimizing the prompt, check whether the right chunks are being retrieved.

- **Retrieval eval** — for a labeled set of (query, correct-chunk-IDs) pairs, measure recall@K. If the right chunk isn't in the top 10, no amount of prompt engineering downstream will help.
- **Generation eval** — given the correct chunks in context, does the model produce the right answer? This is a normal prompt eval (see `EVALS.md`) with the retrieved chunks as input.
- **End-to-end eval** — the real workload: given a user query, does the full pipeline produce the right answer?

Split these evals. A single end-to-end number tells you things are broken; it doesn't tell you whether the fix lives in the chunker, retriever, reranker, or prompt.
