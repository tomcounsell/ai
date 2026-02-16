# Open Deep Research as a p2-* Research Source

**Issue:** [#77](https://github.com/yudame/cuttlefish/issues/77)
**Status:** Plan

## Context

Our podcast pipeline runs 3 independent deep research tools in parallel, each producing a `p2-*.md` artifact:

| Slot | Tool | Focus | Speed |
|------|------|-------|-------|
| `p2-perplexity` | Perplexity `sonar-deep-research` | Academic/peer-reviewed | 30-120s |
| `p2-chatgpt` | GPT-Researcher (local multi-agent) | Industry/technical | 6-20 min |
| `p2-gemini` | Gemini `deep-research-pro-preview` | Policy/regulatory | 3-10 min |

Together's [Open Deep Research](https://github.com/togethercomputer/open_deep_research) is a candidate to add as a 4th source or replace one of the existing three.

## What Together Open Deep Research Does

An open-source Python package (`together-open-deep-research`) with a callable `DeepResearcher` class:

```python
from together_open_deep_research import DeepResearcher

researcher = DeepResearcher(
    budget=6,              # number of search iterations
    max_queries=-1,        # unlimited queries per iteration
    max_sources=-1,        # unlimited sources
    planning_model="...",  # Together AI model for planning
    answer_model="...",    # Together AI model for synthesis
)
answer = researcher("your research topic")  # returns markdown string
```

**Architecture:** Multi-hop agentic loop:
1. Generate clarifying questions → refine topic
2. Generate search queries
3. Search via Tavily → evaluate results
4. Check completeness → if gaps, generate more queries and search again
5. Filter low-quality sources
6. Synthesize final answer with citations

**Key differentiator:** Iterative search refinement. Our current tools fire one query and return. This tool loops until it decides it has enough coverage.

**Dependencies:** `litellm`, `tavily-python`, `langgraph`, `langchain`, `langchain-together`, `smolagents`, `gradio`, `together>=1.3.5`, plus system `pandoc`.

## Assessment

### Strengths
- **Iterative search** — automatically refines queries when initial results are weak
- **Source filtering** — LLM grades each source before including it
- **Completeness check** — explicitly evaluates whether research is sufficient
- **Programmable** — clean `DeepResearcher(topic) -> str` interface fits our pattern

### Concerns
- **Heavy dependencies** — pulls in LangGraph, LangChain, smolagents, Gradio. That's a lot of framework for one research tool
- **Together AI lock-in** — requires `TOGETHER_API_KEY` and `TAVILY_API_KEY` (2 new API keys)
- **Overlaps with issue #78** — if we're replacing tools with `o3-deep-research` (issue #78), adding Together as a 4th tool first may be throwaway work
- **Speed unknown** — no benchmarks on how long the iterative loop takes; could be slower than GPT-Researcher's 6-20 min
- **Quality unknown** — Together's models (DeepSeek, Llama) vs. our current stack (GPT-5.2, Gemini, Perplexity) — unclear if quality matches

### Verdict

**Don't add as a new tool. Steal the patterns instead.**

The valuable parts are the *techniques* (iterative search, completeness checking, source filtering), not the package itself. The dependency footprint is too heavy and the API key sprawl isn't worth it — especially given issue #78 plans to consolidate onto `o3-deep-research`.

## What to Steal

### 1. Iterative Completeness Check

**Their pattern:** After each search round, an LLM evaluates whether the collected results adequately cover the research topic. If not, it generates refined queries targeting the gaps.

**Where to apply:** Build this into whichever tool wins the #78 consolidation. Specifically, the `o3-deep-research` tool already does multi-hop reasoning natively, but we should add an explicit completeness check at the service layer:

```python
# In research.py, after getting results:
def _check_completeness(result_text: str, original_prompt: str) -> bool:
    """Quick LLM check: does the result adequately cover the prompt?"""
    ...
```

**File:** `apps/podcast/services/research.py`

### 2. Source Quality Pre-filter

**Their pattern:** `filter_results()` uses an LLM to grade each source for relevance and quality before it enters the synthesis step. Low-quality sources are dropped.

**Where to apply:** Add to `digest_research.py` — our digest step already summarizes each research source but doesn't grade quality. Adding a quality score per finding lets `write_briefing` filter before synthesis.

**File:** `apps/podcast/services/digest_research.py`, `apps/podcast/services/prompts/digest_research.md`

### 3. Query Generation Strategy

**Their pattern:** Instead of one monolithic research prompt, they generate multiple targeted search queries from the topic, then run them in parallel.

**Where to apply:** Our `craft_research_prompt.py` already generates per-tool prompts. The improvement would be generating 3-5 sub-queries per tool instead of 1, then merging results. This is most relevant for the `o3-deep-research` integration in #78 — we could pass multiple queries as structured input.

**File:** `apps/podcast/services/craft_research_prompt.py`

## Implementation Plan

These improvements are **additive to issue #78**, not standalone work. They should be implemented as part of the `o3-deep-research` integration:

| Priority | Pattern | Apply When | Files |
|----------|---------|------------|-------|
| 1 | Completeness check | Phase 1 of #78 (new tool) | `services/research.py` |
| 2 | Source quality pre-filter | Phase 1 of #78 (digest update) | `services/digest_research.py`, `prompts/digest_research.md` |
| 3 | Multi-query generation | Phase 3 of #78 (prompt crafting) | `services/craft_research_prompt.py` |

**No new dependencies needed.** These patterns are implemented with our existing PydanticAI Named AI Tools (Sonnet for quick checks, same as current pipeline).

## Success Criteria

- [ ] Research results include explicit completeness evaluation
- [ ] Research digests include quality score per finding
- [ ] Briefing step can filter out low-quality findings
- [ ] Craft research prompt can generate sub-queries (not just one prompt)

## Relationship to Other Issues

- **Issue #78** (Replace research tools with OpenAI Deep Research API) — This issue's patterns feed into #78's implementation. The techniques above should be built into the `o3-deep-research` integration, not as a separate tool.
- This issue becomes "research complete, patterns identified" once this plan is approved. Actual implementation happens in #78.
