# Replace Research Tools with OpenAI Deep Research API

**Issue:** [#78](https://github.com/yudame/cuttlefish/issues/78)
**Status:** Plan

## Summary

Replace our three independent research tools (Perplexity, GPT-Researcher, Gemini) with OpenAI's Deep Research API (`o3-deep-research`). This consolidates the research pipeline from 3 tools with 3 API keys and 3 different integration patterns into one unified tool that handles multi-hop reasoning, web search, and citation tracking natively.

## Current State

| Tool | Role | Speed | Integration | Issues |
|------|------|-------|-------------|--------|
| Perplexity (`sonar-deep-research`) | Academic/peer-reviewed | 30-120s | REST sync/async | Solid, but limited to Perplexity's search index |
| GPT-Researcher | Industry/technical | 6-20 min | Local multi-agent framework | Slow, fragile, requires Tavily key, no metadata output |
| Gemini (`deep-research-pro-preview`) | Policy/regulatory | 3-10 min | REST polling | Preview API, subject to change |

**Pain points:**
- 3 different API patterns to maintain
- 3 API keys to manage (Perplexity, OpenAI+Tavily, Google)
- GPT-Researcher is the weakest link — slow, no structured metadata, depends on third-party library stability
- No unified quality scoring across tools
- Targeted research prompts must be crafted differently per tool

## Proposed State

Replace all three with `o3-deep-research` via the OpenAI Responses API:

| Slot | Model | Role | Prompt Strategy |
|------|-------|------|-----------------|
| `p2-primary` | `o3-deep-research` | Broad academic + industry research | System prompt emphasizes peer-reviewed sources, RCTs, meta-analyses, industry reports |
| `p2-targeted-1` | `o3-deep-research` | Gap-filling from question discovery | Prompt includes gaps/contradictions from question-discovery artifact |
| `p2-targeted-2` | `o3-deep-research` | Policy/regulatory focus | System prompt emphasizes government docs, regulatory frameworks, comparative policy |

Same 3-source cross-validation pattern, but all powered by one API. The differentiation comes from prompt engineering, not tool selection.

## Why o3-deep-research

- **Native multi-hop reasoning**: Plans queries, evaluates results, iterates automatically — no need to build our own retry loop (addresses issue #77 Phase 1)
- **Built-in web search + code interpreter**: Searches the open web, can analyze data programmatically
- **Inline citations with annotations**: Every claim links to source URL with character offsets — much richer than what we get from Perplexity
- **Single API pattern**: Standard OpenAI Responses API with `background=True` for async
- **MCP support**: Can connect to private knowledge sources if needed later

## Pricing

- **Input:** $10/M tokens
- **Output:** $40/M tokens
- **Estimated cost per research call:** $1-5 depending on depth (comparable to current Perplexity + GPT-Researcher combined)
- **3 calls per episode:** ~$3-15 total research cost per episode

Perplexity current cost is ~$0.01-0.05 per call. This is significantly more expensive, but quality and automation justify it given the use case.

## Implementation Plan

### Phase 1: New Research Tool (Replace GPT-Researcher first)

GPT-Researcher is the weakest tool — replace it first as proof of concept.

| Step | Task | Files |
|------|------|-------|
| 1 | Create `openai_deep_research.py` tool | `apps/podcast/tools/openai_deep_research.py` |
| 2 | Implement sync and async (background) modes | Same file |
| 3 | Extract citations as structured metadata | Same file |
| 4 | Add cost tracking (input/output tokens) | Same file |
| 5 | Wire into `research.py` as `run_openai_research()` | `apps/podcast/services/research.py` |
| 6 | Update task pipeline to use new tool for `p2-chatgpt` slot | `apps/podcast/tasks.py` |
| 7 | Test with a real episode | Manual validation |

**Tool API pattern** (follows existing conventions):

```python
# apps/podcast/tools/openai_deep_research.py

def run_deep_research(
    prompt: str,
    system_message: str = "",
    model: str = "o3-deep-research",
    background: bool = True,
    timeout: int = 600,
    verbose: bool = False,
) -> tuple[str | None, dict]:
    """Run OpenAI Deep Research and return (content, metadata).

    Returns:
        Tuple of (report_text, metadata_dict) where metadata includes
        citations, token usage, and cost estimate.
    """
```

### Phase 2: Replace Gemini

| Step | Task | Files |
|------|------|-------|
| 1 | Update `run_gemini_research` to call `openai_deep_research` | `apps/podcast/services/research.py` |
| 2 | Create policy-focused system prompt | `apps/podcast/services/prompts/deep_research_policy.md` |
| 3 | Update `craft_research_prompt.py` — Gemini prompt style → Deep Research style | `apps/podcast/services/craft_research_prompt.py` |
| 4 | Update task pipeline | `apps/podcast/tasks.py` |
| 5 | Rename artifact from `p2-gemini` to `p2-policy` | Migration needed |

### Phase 3: Replace Perplexity

| Step | Task | Files |
|------|------|-------|
| 1 | Update `run_perplexity_research` to call `openai_deep_research` | `apps/podcast/services/research.py` |
| 2 | Create academic-focused system prompt | `apps/podcast/services/prompts/deep_research_academic.md` |
| 3 | Update `craft_research_prompt.py` — Perplexity prompt style → Deep Research style | `apps/podcast/services/craft_research_prompt.py` |
| 4 | Update task pipeline | `apps/podcast/tasks.py` |
| 5 | Rename artifact from `p2-perplexity` to `p2-academic` | Migration needed |

### Phase 4: Cleanup

| Step | Task | Files |
|------|------|-------|
| 1 | Remove `gpt_researcher_run.py` | `apps/podcast/tools/gpt_researcher_run.py` |
| 2 | Remove `gemini_deep_research.py` | `apps/podcast/tools/gemini_deep_research.py` |
| 3 | Remove `perplexity_deep_research.py` | `apps/podcast/tools/perplexity_deep_research.py` |
| 4 | Remove `gpt-researcher` from dependencies | `pyproject.toml` |
| 5 | Update craft_research_prompt to use unified prompt strategy | `apps/podcast/services/craft_research_prompt.py` |
| 6 | Update skill files that reference old tools | `.claude/skills/` |
| 7 | Update CLAUDE.md references | `CLAUDE.md` |
| 8 | Remove unused env vars from `.env.example` | `PERPLEXITY_API_KEY`, `GOOGLE_AI_API_KEY`, `TAVILY_API_KEY` |

## Artifact Naming Migration

| Current | New | Rationale |
|---------|-----|-----------|
| `p2-perplexity` | `p2-academic` | Named by research focus, not tool |
| `p2-chatgpt` | `p2-industry` | Named by research focus, not tool |
| `p2-gemini` | `p2-policy` | Named by research focus, not tool |
| `prompt-perplexity` | `prompt-academic` | Consistent with above |
| `prompt-gpt` | `prompt-industry` | Consistent with above |
| `prompt-gemini` | `prompt-policy` | Consistent with above |

This renaming is optional but recommended — it decouples artifact identity from implementation details. Can be done in Phase 4 or deferred.

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Higher cost per episode | High | Low | Budget ~$15/episode; quality gain justifies it |
| o3-deep-research quality varies by topic | Medium | Medium | Keep Perplexity as fallback for Phase 3; test thoroughly |
| API latency (minutes per call) | High | Low | Already handle this — Gemini and GPT-Researcher are slow too |
| Single provider dependency | Medium | Medium | OpenAI is our most stable provider; can add fallback later |
| Citation format differs from current tools | Low | Low | Extract to common format in `openai_deep_research.py` |

## Success Criteria

- [ ] GPT-Researcher replaced with `o3-deep-research` (Phase 1)
- [ ] Episode research quality maintained or improved (manual review)
- [ ] Research pipeline runs end-to-end with new tool
- [ ] Gemini replaced (Phase 2)
- [ ] Perplexity replaced (Phase 3)
- [ ] Old tools and dependencies removed (Phase 4)
- [ ] Single API key (`OPENAI_API_KEY`) handles all research

## Dependencies

- `OPENAI_API_KEY` with access to `o3-deep-research` model
- `openai` Python package (already in dependencies)
- No new packages needed
