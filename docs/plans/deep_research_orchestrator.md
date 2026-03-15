---
status: Ready
type: feature
appetite: Medium
owner: Tom
created: 2025-06-15
tracking: https://github.com/yudame/cuttlefish/issues/78
---

# Deep Research Orchestrator

## Problem

The podcast pipeline needs a `p2-claude` research source that matches the quality of Claude's deep research feature on claude.ai. A single API call with `WebSearchTool` produces shallow, single-pass research — fundamentally different from the multi-agent orchestration that powers claude.ai's deep research.

**What claude.ai deep research actually is:**
- `launch_extended_search_task` — a separate agentic system, NOT the chat model doing web searches
- Lead agent breaks the research command into subtasks
- Subagents run in parallel, each with independent web search (10 results per query, no pagination)
- Lead agent synthesizes subagent results into a comprehensive report
- Interface is simple: `command` (free text) in, comprehensive report out
- claude.ai also gives subagents `web_fetch` to deep-dive into promising URLs after searching

**Current behavior:**
Manual copy/paste from claude.ai deep research. Only non-automated research source.

**Desired outcome:**
An API-powered deep research orchestrator that replicates the multi-agent pattern, producing research quality comparable to claude.ai's deep research. Runs as `p2-claude` in the podcast pipeline alongside Perplexity, GPT-Researcher, Gemini, Together, and MiroFish.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1-2 (validate orchestration approach, review output quality)
- Review rounds: 1

This is more complex than a standard Named AI Tool — it's a multi-agent orchestration system. But the patterns exist in the codebase (PydanticAI agents, task pipeline, fan-in signals), and the interface is simple.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('ANTHROPIC_API_KEY')"` | Anthropic API access |
| `pydantic-ai` with WebSearchTool | `python -c "from pydantic_ai import Agent, WebSearchTool"` | Web search for subagents |

## Solution

### Key Elements

- **Planner agent** (Opus): Takes a research command, breaks it into 3-5 subtasks with distinct research angles
- **Researcher subagents** (Sonnet + WebSearchTool + custom web_fetch): Each subagent researches one subtask independently, returns structured findings with sources
- **Synthesizer agent** (Opus): Merges all subagent findings into a comprehensive research report
- **Service function** (`research.run_claude_research()`): DB adapter that calls the orchestrator and persists the `p2-claude` artifact
- **Task step** (`step_claude_research`): `@task` function that runs in parallel with GPT-Researcher and Gemini in Phase 4

### Flow

**Research prompt** → Opus planner breaks into subtasks → 3-5 Sonnet subagents run sequentially (each with WebSearchTool + web_fetch) → subagent results collected → Opus synthesizes into comprehensive report → saved as `p2-claude` artifact

### Technical Approach

**Review first:** `docs/PYDANTIC_AI_INTEGRATION.md` for canonical Agent setup and patterns.

#### Stage 1: Planning (Opus)

A PydanticAI Agent (Opus) that takes the research command and outputs a structured research plan — a list of subtasks, each with a focused search directive and suggested domain filters.

```python
# apps/podcast/services/claude_deep_research/planner.py

class ResearchSubtask(BaseModel):
    focus: str                       # what this subagent should investigate
    search_strategy: str             # suggested search approach
    key_questions: list[str]         # 3-5 specific questions to answer
    allowed_domains: list[str] = []  # optional domain hints (e.g. [".edu", "scholar.google.com"])

class ResearchPlan(BaseModel):
    subtasks: list[ResearchSubtask]  # 3-5 subtasks
    synthesis_guidance: str          # how to merge results

planner_agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=ResearchPlan,
    system_prompt=_PLANNER_PROMPT,
    defer_model_check=True,
)
```

#### Stage 2: Parallel Research (Sonnet x N)

Each subtask is dispatched to a Sonnet agent with two tools:

1. **`WebSearchTool`** (built-in) — Anthropic's server-side web search. Returns 10 results per query. `max_uses=10` caps cost at $0.10/subagent. `allowed_domains` from the planner can be passed through for focused searching.

2. **`web_fetch`** (custom function tool) — Fetches full page content from a URL. This replicates what claude.ai's `web_fetch` tool gives the research agent: the ability to search first, then deep-dive into promising URLs for detailed content. Implemented as a simple `httpx.get()` wrapper with HTML-to-text conversion.

```python
# apps/podcast/services/claude_deep_research/researcher.py
import httpx
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic_ai.builtin_tools import WebSearchUserLocation

class SubagentFindings(BaseModel):
    focus: str
    findings: str              # detailed research text
    sources: list[str]         # URLs cited
    key_data_points: list[str] # specific facts, stats, quotes
    confidence: str            # high/medium/low
    gaps_identified: list[str] # what couldn't be found

def _create_researcher_agent(
    max_searches: int = 10,
    allowed_domains: list[str] | None = None,
) -> Agent:
    """Create a researcher agent with configured WebSearchTool.

    Separate function because allowed_domains may vary per subtask
    based on the planner's output.
    """
    web_search = WebSearchTool(
        max_uses=max_searches,
        allowed_domains=allowed_domains if allowed_domains else None,
    )

    agent = Agent(
        "anthropic:claude-sonnet-4-6",
        output_type=SubagentFindings,
        system_prompt=_RESEARCHER_PROMPT,
        builtin_tools=[web_search],
        defer_model_check=True,
    )

    @agent.tool
    def fetch_page(ctx: RunContext, url: str) -> str:
        """Fetch the full text content of a web page.

        Use this after web_search to get detailed content from
        a promising URL. Returns plain text extracted from HTML.
        """
        try:
            resp = httpx.get(url, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            # Basic HTML to text — strip tags, keep content
            from html2text import html2text
            text = html2text(resp.text)
            # Truncate to avoid context window pressure
            return text[:8000]
        except Exception as e:
            return f"Failed to fetch {url}: {e}"

    return agent
```

**Known risk: `pause_turn` bug.** PydanticAI has a known edge-case where Anthropic's `pause_turn` stop reason (emitted when a search takes too long) can cause HTTP 400 errors when structured `output_type` is used. Mitigation: wrap each subagent `run_sync()` call in a try/except that catches this specific error and retries once without `output_type`, falling back to parsing the text response manually.

#### Stage 3: Synthesis (Opus)

The lead agent receives all subagent findings and synthesizes them into a comprehensive research report — resolving contradictions, identifying consensus, flagging gaps.

```python
# apps/podcast/services/claude_deep_research/synthesizer.py

class DeepResearchReport(BaseModel):
    content: str               # full research report (markdown, 3000-6000 words)
    sources_cited: list[str]   # all URLs from all subagents
    key_findings: list[str]    # top-level findings
    confidence_assessment: str # overall research quality
    gaps_remaining: list[str]  # what wasn't covered

synthesizer_agent = Agent(
    "anthropic:claude-opus-4-6",
    output_type=DeepResearchReport,
    system_prompt=_SYNTHESIZER_PROMPT,
    defer_model_check=True,
)
```

#### Orchestrator Function

One public function ties the three stages together:

```python
# apps/podcast/services/claude_deep_research/orchestrate.py

def deep_research(command: str) -> DeepResearchReport:
    """Replicate claude.ai deep research via multi-agent orchestration.

    Stage 1: Opus plans subtasks
    Stage 2: Sonnet subagents research sequentially (each with WebSearchTool + web_fetch)
    Stage 3: Opus synthesizes into comprehensive report
    """
    plan = planner_agent.run_sync(command)

    findings = []
    for subtask in plan.output.subtasks:
        researcher = _create_researcher_agent(
            max_searches=10,
            allowed_domains=subtask.allowed_domains or None,
        )
        try:
            result = researcher.run_sync(
                f"Research focus: {subtask.focus}\n\n"
                f"Key questions:\n" +
                "\n".join(f"- {q}" for q in subtask.key_questions) +
                f"\n\nSearch strategy: {subtask.search_strategy}"
            )
            findings.append(result.output)
        except Exception as exc:
            logger.warning(
                "Subagent failed for subtask '%s': %s", subtask.focus, exc
            )
            # Continue with remaining subtasks

    if not findings:
        raise RuntimeError("All subagents failed — no findings to synthesize")

    synthesis_input = _format_findings_for_synthesis(plan.output, findings)
    report = synthesizer_agent.run_sync(synthesis_input)
    return report.output
```

**Cost estimate per run:**
- Opus planner: ~$1-2 (planning is short)
- 4x Sonnet subagents with web search: ~$2-4 ($0.50-1 each)
- Web search: ~$0.40 (4 subagents × 10 max searches × $0.01)
- web_fetch calls: free (just HTTP requests, no API cost)
- Opus synthesizer: ~$3-5 (long synthesis from all findings)
- **Total: ~$6-11 per research run**

### Tooling Reference

Summary of the tools each agent gets, informed by reverse-engineering claude.ai's deep research:

| Agent | Model | Tools | Purpose |
|-------|-------|-------|---------|
| Planner | Opus 4.6 | None | Break command into subtasks |
| Researcher (×3-5) | Sonnet 4.5 | `WebSearchTool(max_uses=10)` + custom `fetch_page` | Search + deep-dive |
| Synthesizer | Opus 4.6 | None | Merge findings into report |

**WebSearchTool specifics (Anthropic provider):**
- $0.01 per search, charged on top of token costs
- Returns 10 results per query (fixed, no pagination)
- `max_uses` caps searches per agent run (Anthropic-only parameter)
- `allowed_domains` / `blocked_domains` supported but mutually exclusive
- Search results count as input tokens at standard rates
- `user_location` available but not needed for research use cases

**Custom `fetch_page` tool:**
- Simple `httpx.get()` + `html2text` conversion
- 15-second timeout, follows redirects
- Truncates to 8,000 chars to manage context window
- Allows subagents to deep-dive into URLs found via search
- No API cost — just HTTP requests

### File Structure

```
apps/podcast/services/claude_deep_research/
    __init__.py
    orchestrate.py      # public function: deep_research()
    planner.py          # Stage 1: Opus plans subtasks
    researcher.py       # Stage 2: Sonnet subagents with WebSearchTool + fetch_page
    synthesizer.py      # Stage 3: Opus synthesizes report
    prompts/
        planner.md      # system prompt for planning agent
        researcher.md   # system prompt for research subagents
        synthesizer.md  # system prompt for synthesis agent
```

This is a **package** (subdirectory with `__init__.py`) rather than a single file because it contains three distinct agents. This follows the Named AI Tool spirit (each agent in its own file with its own prompt) while acknowledging that these agents form a pipeline.

### Dependency: `html2text`

The `fetch_page` tool needs HTML-to-text conversion. Add `html2text` as a dependency:
```bash
uv add html2text
```

This is a lightweight, well-maintained package (~50KB) with no transitive dependencies.

## Rabbit Holes

- **Async parallel execution**: Don't build async subagent dispatch. Use sequential `run_sync()` calls for now. The total runtime (3-5 minutes) is acceptable for a background task. Async adds complexity without meaningful benefit in a `@task` pipeline.
- **Adaptive subagent count**: Don't dynamically adjust the number of subagents based on topic complexity. The planner outputs 3-5 subtasks; run them all. Optimize later if needed.
- **Subagent-to-subagent communication**: Don't let subagents share findings mid-research. Keep them isolated (like claude.ai's architecture). The synthesis stage handles merging.
- **Retry/fallback logic**: Don't build elaborate retry mechanisms for failed subagents. If one fails, log it and synthesize from the others. The report will note the gap. (Exception: the `pause_turn` bug gets one retry.)
- **Caching/deduplication**: Don't cache subagent results across episodes. Each research run is unique to the episode topic.
- **Safety filtering**: Copyright compliance and harmful content filtering are implemented one level up, not in this tool. See #81.
- **Image search**: claude.ai has `image_search` but we don't need images for research text. Skip it.

## Risks

### Risk 1: Cost per run ($6-11)
**Impact:** Higher episode production cost than single-call approach
**Mitigation:** Quality justifies cost. Still cheaper than a human researcher. Can reduce subagent count to 3 for cost-sensitive runs.

### Risk 2: Total runtime (3-5 minutes sequential)
**Impact:** Longer than other research sources
**Mitigation:** Runs as a background `@task` in parallel with GPT-Researcher and Gemini. Wall clock time doesn't matter — throughput does.

### Risk 3: Opus planner generates poor subtasks
**Impact:** Subagents research irrelevant angles, wasting tokens
**Mitigation:** Strong planner system prompt with examples. The synthesis stage can still produce a good report from imperfect subtask coverage.

### Risk 4: `pause_turn` bug in PydanticAI
**Impact:** Anthropic's `pause_turn` stop reason (emitted when web search takes too long) can cause HTTP 400 errors when structured `output_type` is used with WebSearchTool.
**Mitigation:** Wrap subagent calls in try/except. On this specific error, retry once. If it persists, skip the subagent and log the failure.

### Risk 5: Context window pressure in synthesis stage
**Impact:** 4-5 subagent reports may be large when including full page fetches
**Mitigation:** `fetch_page` truncates to 8,000 chars. `SubagentFindings` fields are bounded. Worst case: truncate findings to key data points before synthesis.

## No-Gos (Out of Scope)

- Not replacing Perplexity, GPT-Researcher, or Gemini — this runs alongside them
- Not building a generic "deep research as a service" — scoped to the podcast pipeline
- Not adding Google Drive search (claude.ai has this but we don't need it)
- Not building a UI for research progress — this runs as a background task
- Not modifying the fan-in signal — it already handles arbitrary `p2-*` artifacts
- Not implementing safety/copyright filtering — that's #81, applied one level up
- Not adding image search — research output is text only

## Update System

No update system changes required — new service-layer package, one new lightweight dependency (`html2text`).

## Agent Integration

No agent integration required — runs within the automated task pipeline.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/deep-research-orchestrator.md` describing the multi-agent architecture
- [ ] Update `docs/features/podcast-services.md` Named AI Tools table
- [ ] Update CLAUDE.md Named AI Tools table

### Inline Documentation
- [ ] Docstrings on all three agents and the orchestrator function
- [ ] Architecture diagram in the package `__init__.py`

## Success Criteria

- [ ] `deep_research()` orchestrator produces comprehensive research with sources from multiple subagents
- [ ] Subagents use both WebSearchTool (discover) and fetch_page (deep-dive) effectively
- [ ] Output quality is comparable to claude.ai deep research (manual comparison on 2-3 test topics)
- [ ] `run_claude_research()` service creates `p2-claude` artifact with content and metadata
- [ ] `step_claude_research` task runs in parallel with GPT-Researcher and Gemini in Phase 4
- [ ] Fan-in signal correctly waits for `p2-claude` before advancing
- [ ] Total cost per run stays under $15
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (deep-research-planner)**
  - Name: planner-builder
  - Role: Implement the Opus planner agent and prompt
  - Agent Type: builder
  - Resume: true

- **Builder (deep-research-subagent)**
  - Name: subagent-builder
  - Role: Implement the Sonnet researcher agent with WebSearchTool + fetch_page
  - Agent Type: builder
  - Resume: true

- **Builder (deep-research-orchestrator)**
  - Name: orchestrator-builder
  - Role: Implement the orchestrator, synthesizer, and pipeline integration
  - Agent Type: builder
  - Resume: true

- **Validator (deep-research)**
  - Name: deep-research-validator
  - Role: Verify multi-agent pipeline, output quality, and pipeline integration
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add html2text dependency
- **Task ID**: add-dependency
- **Depends On**: none
- **Assigned To**: orchestrator-builder
- **Agent Type**: builder
- **Parallel**: true
- Run `uv add html2text`
- Verify import works: `python -c "from html2text import html2text"`

### 2. Create planner agent
- **Task ID**: build-planner
- **Depends On**: none
- **Assigned To**: planner-builder
- **Agent Type**: builder
- **Parallel**: true
- **Review first:** `docs/PYDANTIC_AI_INTEGRATION.md`
- Create `apps/podcast/services/claude_deep_research/planner.py`:
  - Output model: `ResearchPlan` with list of `ResearchSubtask` (including `allowed_domains`)
  - Agent: `anthropic:claude-opus-4-6`, no tools (planning only)
  - Function: `plan_research(command: str) -> ResearchPlan`
- Create `apps/podcast/services/claude_deep_research/prompts/planner.md`:
  - Instruct agent to break research command into 3-5 focused subtasks
  - Each subtask should cover a distinct angle (academic, industry, policy, case studies, emerging trends)
  - Include domain hints where appropriate (e.g. `.edu` for academic, `.gov` for policy)
  - Include synthesis guidance for how to merge results

### 3. Create researcher subagent
- **Task ID**: build-researcher
- **Depends On**: add-dependency
- **Assigned To**: subagent-builder
- **Agent Type**: builder
- **Parallel**: true
- **Review first:** `docs/PYDANTIC_AI_INTEGRATION.md`
- Create `apps/podcast/services/claude_deep_research/researcher.py`:
  - Output model: `SubagentFindings` with findings, sources, data points, confidence, gaps
  - Factory function: `_create_researcher_agent(max_searches, allowed_domains)` returns configured Agent
  - Built-in tool: `WebSearchTool(max_uses=10, allowed_domains=...)` — configurable per subtask
  - Custom tool: `fetch_page(url) -> str` — `httpx.get()` + `html2text`, 15s timeout, 8000 char truncation
  - Public function: `research_subtask(subtask: ResearchSubtask) -> SubagentFindings`
  - Error handling: catch `pause_turn` HTTP 400 and retry once
- Create `apps/podcast/services/claude_deep_research/prompts/researcher.md`:
  - Instruct agent to conduct thorough web research on the assigned focus area
  - Use `web_search` first to discover relevant sources, then `fetch_page` to deep-dive into the most promising URLs
  - Cite all sources with URLs
  - Flag confidence levels and gaps honestly
  - Return concrete data points, not summaries

### 4. Create synthesizer agent
- **Task ID**: build-synthesizer
- **Depends On**: none
- **Assigned To**: orchestrator-builder
- **Agent Type**: builder
- **Parallel**: true
- **Review first:** `docs/PYDANTIC_AI_INTEGRATION.md`
- Create `apps/podcast/services/claude_deep_research/synthesizer.py`:
  - Output model: `DeepResearchReport` with content, sources, findings, confidence, gaps
  - Agent: `anthropic:claude-opus-4-6`, no tools (synthesis only)
  - Function: `synthesize_findings(plan: ResearchPlan, findings: list[SubagentFindings]) -> DeepResearchReport`
- Create `apps/podcast/services/claude_deep_research/prompts/synthesizer.md`:
  - Instruct agent to merge subagent findings into a cohesive report
  - Resolve contradictions, identify consensus
  - Produce 3,000-6,000 word comprehensive research report
  - Cite sources from all subagents
  - Note gaps and confidence levels

### 5. Create orchestrator
- **Task ID**: build-orchestrator
- **Depends On**: build-planner, build-researcher, build-synthesizer
- **Assigned To**: orchestrator-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/services/claude_deep_research/__init__.py` with architecture docstring
- Create `apps/podcast/services/claude_deep_research/orchestrate.py`:
  - Public function: `deep_research(command: str) -> DeepResearchReport`
  - Calls planner → iterates subtasks through researcher (passing `allowed_domains`) → calls synthesizer
  - Logs total token usage and cost estimate across all stages
  - Handles individual subagent failures gracefully (log and continue)
  - Raises `RuntimeError` only if ALL subagents fail

### 6. Update prompt crafting
- **Task ID**: build-prompt-crafting
- **Depends On**: none
- **Assigned To**: orchestrator-builder
- **Agent Type**: builder
- **Parallel**: true
- Add Claude section to `apps/podcast/services/prompts/craft_research_prompt.md`
- Add `claude_prompt: str` field to `TargetedResearchPrompts` in `craft_research_prompt.py`
- Update `craft_targeted_research_prompts()` in `analysis.py` to save `prompt-claude` artifact and create empty `p2-claude` placeholder

### 7. Wire service layer and task pipeline
- **Task ID**: build-pipeline
- **Depends On**: build-orchestrator, build-prompt-crafting
- **Assigned To**: orchestrator-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `run_claude_research()` to `apps/podcast/services/research.py`:
  - Call `deep_research()` orchestrator
  - Format `DeepResearchReport` to markdown
  - Persist as `p2-claude` artifact with `metadata=result.model_dump()`
- Add `step_claude_research` task to `apps/podcast/tasks.py` (parallel sub-step pattern)
- Update `step_question_discovery` to enqueue `step_claude_research` alongside GPT and Gemini
- Remove manual Claude research instructions from `.claude/skills/new-podcast-episode.md`

### 8. Write tests
- **Task ID**: build-tests
- **Depends On**: build-pipeline
- **Assigned To**: orchestrator-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test planner agent with mocked agent
- Test researcher subagent with mocked agent (verify both WebSearchTool and fetch_page are registered)
- Test synthesizer agent with mocked agent
- Test orchestrator end-to-end with all agents mocked
- Test `pause_turn` error handling and retry logic
- Test service function creates artifact correctly

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: orchestrator-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/deep-research-orchestrator.md`
- Update `docs/features/podcast-services.md`
- Update CLAUDE.md

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: deep-research-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Run orchestrator on 1-2 test topics and compare output quality to claude.ai deep research
- Verify pipeline integration (fan-in, workflow progress)
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v -k "deep_research"` - orchestrator tests
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` - full podcast test suite
- `uv run pre-commit run --all-files` - code quality checks

## Files Changed (Summary)

| File | Change |
|------|--------|
| `pyproject.toml` | Add `html2text` dependency |
| `apps/podcast/services/claude_deep_research/__init__.py` | **New** — package init with architecture docstring |
| `apps/podcast/services/claude_deep_research/orchestrate.py` | **New** — public `deep_research()` function |
| `apps/podcast/services/claude_deep_research/planner.py` | **New** — Opus planner agent |
| `apps/podcast/services/claude_deep_research/researcher.py` | **New** — Sonnet researcher with WebSearchTool + fetch_page |
| `apps/podcast/services/claude_deep_research/synthesizer.py` | **New** — Opus synthesis agent |
| `apps/podcast/services/claude_deep_research/prompts/planner.md` | **New** — planner system prompt |
| `apps/podcast/services/claude_deep_research/prompts/researcher.md` | **New** — researcher system prompt |
| `apps/podcast/services/claude_deep_research/prompts/synthesizer.md` | **New** — synthesizer system prompt |
| `apps/podcast/services/research.py` | Add `run_claude_research()` |
| `apps/podcast/services/craft_research_prompt.py` | Add `claude_prompt` to `TargetedResearchPrompts` |
| `apps/podcast/services/prompts/craft_research_prompt.md` | Add Claude prompt guidance section |
| `apps/podcast/services/analysis.py` | Generate `prompt-claude` + `p2-claude` placeholder |
| `apps/podcast/tasks.py` | Add `step_claude_research`, wire into Phase 4 fan-out |
| `.claude/skills/new-podcast-episode.md` | Remove manual Claude research instructions |
| `apps/podcast/tests/test_ai_tools/test_claude_deep_research.py` | **New** — tests |

## What This Does NOT Change

- Perplexity, GPT-Researcher, and Gemini continue running as-is
- Cross-validation, briefing, synthesis phases unchanged
- Fan-in signal unchanged (already handles any number of `p2-*` artifacts)
- No new API keys (`ANTHROPIC_API_KEY` already configured)
- Safety/copyright filtering is separate (#81)

## Resolved Questions

1. **Sequential subagents.** Subagents run sequentially via `run_sync()`. Simpler, and the total runtime (3-5 min) is acceptable for a background task.
2. **Sonnet for subagents.** Sonnet 4.5 — smarter research justifies the cost over Haiku.
3. **`claude_deep_research/` package.** Subdirectory package, not a single file. Three distinct agents warrant separate files.
