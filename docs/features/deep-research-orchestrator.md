# Deep Research Orchestrator

Multi-agent research pipeline that replicates claude.ai's deep research feature using the Anthropic API.

## Architecture

```
Research Command
      |
[Stage 1: Planner (Opus)]
      |
ResearchPlan (3-5 subtasks)
      |
[Stage 2: Researchers (Sonnet x N)]  -- WebSearchTool + fetch_page
      |
list[SubagentFindings]
      |
[Stage 3: Synthesizer (Opus)]
      |
DeepResearchReport
```

## Usage

```python
from apps.podcast.services.claude_deep_research import deep_research

report = deep_research("Investigate the impact of AI on healthcare labor markets")
print(report.content)           # 3000-6000 word markdown report
print(report.sources_cited)     # URLs from all subagents
print(report.key_findings)      # Top-level findings
print(report.confidence_assessment)
print(report.gaps_remaining)
```

## Pipeline Integration

Runs as `p2-claude` in the podcast pipeline's Phase 4 (Targeted Research), alongside GPT-Researcher and Gemini.

- **Service function:** `research.run_claude_research(episode_id, prompt)`
- **Task step:** `step_claude_research` (parallel sub-step, fan-in via signal)
- **Prompt crafting:** `craft_targeted_research_prompts()` generates `prompt-claude` artifact

## Agents

| Agent | Model | Tools | Purpose |
|-------|-------|-------|---------|
| Planner | Opus 4.6 | None | Break command into 3-5 subtasks |
| Researcher (x3-5) | Sonnet 4.5 | `WebSearchTool(max_uses=10)` + `fetch_page` | Search + deep-dive |
| Synthesizer | Opus 4.6 | None | Merge findings into report |

## Cost

Estimated $6-11 per research run:
- Opus planner: ~$1-2
- Sonnet subagents (x4): ~$2-4
- Web search: ~$0.40
- Opus synthesizer: ~$3-5

## File Structure

```
apps/podcast/services/claude_deep_research/
    __init__.py          # Package init, public API re-exports
    orchestrate.py       # deep_research() -- ties all stages together
    planner.py           # Stage 1: Opus planner
    researcher.py        # Stage 2: Sonnet researchers with WebSearchTool
    synthesizer.py       # Stage 3: Opus synthesizer
    prompts/
        planner.md       # Planner system prompt
        researcher.md    # Researcher system prompt
        synthesizer.md   # Synthesizer system prompt
```
