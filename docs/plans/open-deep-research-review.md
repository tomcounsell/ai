# Together Open Deep Research — 5th Automated p2-* Source

**Issue:** [#77](https://github.com/yudame/cuttlefish/issues/77)
**Status:** Done

## Context

We're building toward 5 independent automated research tools, each producing a unique `p2-*` artifact for maximum diversity before cross-validation:

| Slot | Tool | Focus | Status |
|------|------|-------|--------|
| `p2-perplexity` | Perplexity `sonar-deep-research` | Academic/peer-reviewed | ✅ Automated |
| `p2-chatgpt` | GPT-Researcher (multi-agent) | Industry/technical | ✅ Automated |
| `p2-gemini` | Gemini Deep Research | Policy/regulatory | ✅ Automated |
| `p2-claude` | Claude Opus + web search (#78) | Comprehensive synthesis | 📋 Planned |
| `p2-together` | Together Open Deep Research (#77) | Iterative multi-hop | 📋 This plan |

Each tool uses a different LLM provider, search strategy, and reasoning approach — maximizing the diversity of research that feeds into cross-validation and briefing.

## What Together Open Deep Research Provides

An open-source Python package with a simple callable interface:

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

**Unique value vs. other tools:**
- **Iterative search refinement** — loops until coverage is sufficient (our other tools fire once and return)
- **Source quality filtering** — LLM grades each source before synthesis
- **Open-source** — we can tune models, budget, and behavior
- **Different model family** — Together hosts DeepSeek, Llama, Qwen — adds genuine diversity to research perspectives

## Dependencies

**New API keys required:**
- `TOGETHER_API_KEY` — Together AI inference
- `TAVILY_API_KEY` — Web search backend

**Python dependencies (heavy):**
- `together-open-deep-research` (the package itself)
- Transitive: `litellm`, `tavily-python`, `langgraph`, `langchain`, `langchain-together`, `smolagents`, `gradio`, `pandoc`

**Mitigation for dependency weight:** Install as an optional dependency group so it doesn't bloat the base install:
```toml
[project.optional-dependencies]
together-research = ["together-open-deep-research"]
```

## Implementation Plan

### Step 1: Create the tool script

**File:** `apps/podcast/tools/together_deep_research.py`

Following the same pattern as other research tools:

```python
def run_together_research(
    prompt: str,
    budget: int = 6,
    planning_model: str = "deepseek-ai/DeepSeek-R1",
    answer_model: str = "deepseek-ai/DeepSeek-R1",
    max_queries: int = -1,
    max_sources: int = -1,
    timeout: int = 900,
    verbose: bool = False,
) -> tuple[str | None, dict]:
    """Run Together Open Deep Research and return (content, metadata).

    Returns:
        Tuple of (report_text, metadata_dict).
    """
    from together_open_deep_research import DeepResearcher

    researcher = DeepResearcher(
        budget=budget,
        max_queries=max_queries,
        max_sources=max_sources,
        planning_model=planning_model,
        answer_model=answer_model,
    )
    answer = researcher(prompt)

    metadata = {
        "budget": budget,
        "planning_model": planning_model,
        "answer_model": answer_model,
    }
    return answer, metadata
```

**Key features:**
- CLI mode for standalone testing: `python together_deep_research.py "prompt"`
- Configurable iteration budget (controls depth vs. speed tradeoff)
- Model selection (can swap between DeepSeek-R1, Llama, Qwen)
- Timeout handling (iterative loop can run long)

### Step 2: Wire into the service layer

**File:** `apps/podcast/services/research.py`

Add `run_together_research()` following the existing pattern:

```python
def run_together_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Together Open Deep Research and save results as p2-together."""
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    from apps.podcast.tools.together_deep_research import (
        run_together_research as _together,
    )

    content_text, metadata = _together(prompt=full_prompt, verbose=False)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-together",
        defaults={
            "content": content_text or "",
            "description": "Together Open Deep Research multi-hop output.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )
    return artifact
```

### Step 3: Add task pipeline step

**File:** `apps/podcast/tasks.py`

Add `step_together_research` task, parallel with GPT-Researcher, Gemini, and Claude in Phase 4:

```python
@task
def step_together_research(episode_id: int) -> None:
    """Phase 4: Run Together deep research (parallel)."""
    episode = Episode.objects.get(pk=episode_id)

    prompt_artifact = EpisodeArtifact.objects.filter(
        episode=episode, title="prompt-together"
    ).first()

    prompt = prompt_artifact.content if prompt_artifact else _get_episode_context(episode)

    research.run_together_research(episode_id, prompt)
```

Wire into the existing Phase 4 fan-in signal alongside the other targeted research tasks.

### Step 4: Add prompt crafting

**File:** `apps/podcast/services/craft_research_prompt.py`

Add `"together"` as a valid `research_type`. The Together prompt should emphasize:
- **Exploratory breadth** — leverage the iterative search to cover adjacent topics the other tools might miss
- **Contrarian perspectives** — find dissenting views and alternative framings
- **Emerging/recent developments** — the multi-hop search is good at chasing recent threads

**File:** `apps/podcast/services/prompts/craft_research_prompt.md`

Add Together-specific guidance:

> **Together Prompts** (exploratory multi-hop):
> - Request broad exploration of adjacent and emerging subtopics
> - Ask for contrarian or minority viewpoints with supporting evidence
> - Emphasize recent developments and evolving consensus
> - Request identification of under-reported angles and novel connections

### Step 5: Update workflow progress tracking

**File:** `apps/podcast/services/workflow_progress.py`

Add `p2-together` to the Phase 4 targeted sources list:

```python
targeted_sources = [
    ("Grok research", "p2-grok"),
    ("ChatGPT research", "p2-chatgpt"),
    ("Gemini research", "p2-gemini"),
    ("Claude research", "p2-claude"),
    ("Together research", "p2-together"),  # NEW
    ("Manual research", "p2-manual"),
]
```

### Step 6: Add dependency

**File:** `pyproject.toml`

```toml
[project.optional-dependencies]
together-research = ["together-open-deep-research"]
```

Install with: `uv sync --extra together-research`

### Step 7: Add env vars

**File:** `.env.example`

```
TOGETHER_API_KEY=your_key
TAVILY_API_KEY=your_key
```

## Files Changed (Summary)

| File | Change |
|------|--------|
| `apps/podcast/tools/together_deep_research.py` | **New** — standalone tool script |
| `apps/podcast/services/research.py` | Add `run_together_research()` |
| `apps/podcast/services/craft_research_prompt.py` | Add `"together"` research type |
| `apps/podcast/services/prompts/craft_research_prompt.md` | Add Together prompt guidance |
| `apps/podcast/tasks.py` | Add `step_together_research`, wire into Phase 4 |
| `apps/podcast/services/analysis.py` | Generate `prompt-together` in targeted prompts |
| `apps/podcast/services/workflow_progress.py` | Add `p2-together` to Phase 4 tracking |
| `pyproject.toml` | Add optional `together-research` dependency group |
| `.env.example` | Add `TOGETHER_API_KEY`, `TAVILY_API_KEY` |
| `.claude/skills/new-podcast-episode.md` | Add Together to automated research list |

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Heavy transitive deps (LangChain etc.) | Certain | Medium | Optional dep group; isolate from base install |
| Speed unknown (iterative loop) | Medium | Low | Set `budget=6` default; cap at 15 min timeout |
| Together AI model quality vs. our stack | Medium | Low | DeepSeek-R1 is strong for reasoning; diversity is the goal, not parity |
| 2 new API keys to manage | Certain | Low | One-time setup; both providers have free tiers for testing |
| Package stability (v0.1.0) | Medium | Medium | Pin version; wrap in try/except with graceful fallback to skip |

## Relationship to Other Issues

- **Issue #78** (Claude Opus p2-claude) — Sibling tool. Together and Claude are independent p2-* sources; neither depends on the other.
- Together with #78, these bring the total automated research tools to 5, eliminating all manual research steps.

## Success Criteria

- [ ] `together_deep_research.py` tool works standalone via CLI
- [ ] `run_together_research()` service creates `p2-together` artifact
- [ ] Task pipeline runs `step_together_research` in parallel with other Phase 4 tools
- [ ] 5 automated p2-* sources running end-to-end in episode production
- [ ] Cross-validation handles 5 sources correctly
