# Open Deep Research Review — Implementation Plan

**Issue:** [#77](https://github.com/yudame/cuttlefish/issues/77)
**Status:** Plan

## Executive Summary

Together AI's [Open Deep Research](https://github.com/togethercomputer/open_deep_research) is a Streamlit-based agentic research tool using multi-hop reasoning with self-reflection loops. After reviewing the architecture against our podcast research pipeline (Phases 2-6), there are two patterns worth adopting and several things to skip.

## Their Architecture (Summary)

- **Stack:** Streamlit UI, LangGraph agent orchestration, Together AI inference API
- **Core loop:** Plan queries → search (Tavily/SerpAPI) → evaluate results → reflect on quality → iterate (up to N hops)
- **Self-reflection:** After each search hop, the agent evaluates whether it has enough information or needs to refine the query and search again
- **Citation:** Inline source tracking through the search → evaluate cycle
- **Models:** Together AI models (DeepSeek, Llama, etc.) via their inference API

## What to Adopt

### 1. Iterative Query Refinement (High Value)

**What they do:** Instead of a single research query, they run a plan→search→evaluate→refine loop. If results are weak, the agent reformulates the query and searches again.

**Our gap:** Our pipeline is linear — `craft_research_prompt` generates one prompt per tool, fires it, and moves on. If Perplexity returns shallow results on a subtopic, we don't retry with a better query.

**Implementation:**

Add an optional retry loop to `research.py` research functions:

```
services/research.py:
  run_perplexity_research(episode_id, prompt, max_retries=2)
    1. Run research with initial prompt
    2. Call digest_research on result
    3. If digest shows gaps/low confidence findings → craft refined prompt
    4. Retry with refined prompt, merge results
    5. Save final merged artifact
```

**Files to modify:**
- `apps/podcast/services/research.py` — Add retry loop to `run_perplexity_research` (start with Perplexity only, it's fastest)
- `apps/podcast/services/prompts/refine_research_query.md` — New prompt template for query refinement
- `apps/podcast/services/refine_query.py` — New Named AI Tool: takes a digest + original prompt, outputs refined prompt

**Estimated effort:** 1-2 days

### 2. Research Quality Scoring per Source (Medium Value)

**What they do:** Self-reflection step grades each search result before including it in the final output.

**Our gap:** Our cross-validation catches contradictions *across* sources, but doesn't score individual source quality *before* they enter the pipeline. Low-quality sources waste downstream tokens.

**Implementation:**

Add a quality pre-filter in the digest step:

```
services/digest_research.py:
  Add quality_score field to ResearchDigest output
  Add source_reliability assessment per finding
  Flag findings below threshold for exclusion from briefing
```

**Files to modify:**
- `apps/podcast/services/digest_research.py` — Add quality scoring to output model
- `apps/podcast/services/prompts/digest_research.md` — Update prompt to include quality assessment
- `apps/podcast/services/analysis.py` — `write_briefing` can filter low-quality findings

**Estimated effort:** 0.5 day

## What to Skip

### Multi-hop Agent Orchestration (LangGraph)
Their LangGraph-based agent loop is tightly coupled to their Streamlit UI and Together AI models. Our pipeline already has a clean service layer with `@task` orchestration. Adopting LangGraph would add complexity without clear benefit — we'd be replacing our working Django task pipeline with a second orchestration framework.

### Together AI Models
Their models (DeepSeek-R1, Llama) are optimized for their inference API. We already use Anthropic (Sonnet/Opus) for analysis and OpenAI for GPT-Researcher. Adding another LLM provider for marginal gains isn't worth the integration cost right now. Revisit if Together releases a model that clearly outperforms on research tasks.

### Tavily/SerpAPI Search Integration
They use these as search backends. Our tools (Perplexity, Gemini, GPT-Researcher) already have built-in web search. Adding raw search APIs would duplicate functionality.

### Streamlit UI
Irrelevant — we use Django + HTMX.

## Implementation Plan

### Phase 1: Query Refinement (Priority)

| Step | Task | Files |
|------|------|-------|
| 1 | Create `refine_query.py` Named AI Tool | `apps/podcast/services/refine_query.py` |
| 2 | Create prompt template | `apps/podcast/services/prompts/refine_research_query.md` |
| 3 | Add retry loop to `run_perplexity_research` | `apps/podcast/services/research.py` |
| 4 | Add `max_retries` param to task pipeline | `apps/podcast/tasks.py` |
| 5 | Test with a real episode | Manual validation |

### Phase 2: Quality Scoring (After Phase 1 ships)

| Step | Task | Files |
|------|------|-------|
| 1 | Add quality fields to `ResearchDigest` model | `apps/podcast/services/digest_research.py` |
| 2 | Update digest prompt | `apps/podcast/services/prompts/digest_research.md` |
| 3 | Add quality filtering to `write_briefing` | `apps/podcast/services/analysis.py` |
| 4 | Test quality gate with existing episode data | Manual validation |

## Success Criteria

- [ ] Perplexity research retries at least once when initial results have gaps
- [ ] Refined queries produce measurably better coverage on subtopics
- [ ] Research digests include quality scores per finding
- [ ] Low-quality findings are flagged (not silently dropped) in briefing

## Not In Scope (per issue)

- Full Together AI integration as a research provider
- Replacing existing research tools
- LangGraph adoption
