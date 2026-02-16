# Automated Claude Deep Research Tool (p2-claude)

**Issue:** [#78](https://github.com/yudame/cuttlefish/issues/78)
**Status:** Plan

## Summary

Replace the manual "paste Claude deep research results" step with an automated `p2-claude` research tool using Claude Opus + web search via the Anthropic API. This runs alongside Perplexity, GPT-Researcher, and Gemini as an independent p2-* source — not a replacement for any of them.

## Current State

The `p2-claude` artifact is currently produced manually:
1. User copies a research prompt from the workflow
2. User pastes it into claude.ai and runs deep research
3. User copies the output back and pastes it via `add_manual_research()`

This is the only research step that isn't automated. All others (`p2-perplexity`, `p2-chatgpt`, `p2-gemini`) run via API.

## Proposed Tool

**Claude Opus 4 + web search tool** via the Anthropic Messages API.

```python
# apps/podcast/tools/claude_deep_research.py

import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-6",
    max_tokens=16384,
    messages=[
        {"role": "user", "content": research_prompt}
    ],
    tools=[{
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 20,
    }],
)
```

**Why Opus + web search:**
- Opus is the strongest model for comprehensive synthesis — matches/exceeds what users get from claude.ai deep research
- Web search tool gives real-time access to current sources, citations with URLs
- We already have `ANTHROPIC_API_KEY` — no new API keys
- `anthropic` package already in dependencies — no new deps
- Fits our existing tool pattern: `function(prompt) -> (content, metadata)`

**Pricing:**
- Opus 4.6: $15/M input, $75/M output tokens
- Web search: $10 per 1,000 searches
- Estimated per call: $2-8 depending on output length and number of searches
- With 20 max searches: ~$0.20 search cost + token costs

## Implementation Plan

### Step 1: Create the tool script

**File:** `apps/podcast/tools/claude_deep_research.py`

Following the exact pattern of `perplexity_deep_research.py`:

```python
def run_claude_research(
    prompt: str,
    system_message: str = "",
    model: str = "claude-opus-4-6",
    max_searches: int = 20,
    max_tokens: int = 16384,
    timeout: int = 600,
    verbose: bool = False,
) -> tuple[str | None, dict]:
    """Run Claude deep research with web search and return (content, metadata).

    Returns:
        Tuple of (report_text, metadata_dict) where metadata includes
        citations, token usage, search count, and cost estimate.
    """
```

**Key features:**
- Extracts final text from response content blocks
- Collects all citations (url, title, cited_text) into metadata
- Tracks token usage and web search count for cost estimation
- Handles `pause_turn` stop reason by continuing the conversation
- CLI mode for standalone testing: `python claude_deep_research.py "prompt"`
- Streaming support for progress visibility

### Step 2: Wire into the service layer

**File:** `apps/podcast/services/research.py`

Add `run_claude_research()` following the exact pattern of `run_perplexity_research()`:

```python
def run_claude_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Call Claude Opus with web search and save results as p2-claude."""
    episode = Episode.objects.get(pk=episode_id)
    context = _get_episode_context(episode)

    full_prompt = (
        f"Episode: {episode.title}\n\n"
        f"Context:\n{context}\n\n"
        f"Research query:\n{prompt}"
    )

    from apps.podcast.tools.claude_deep_research import (
        run_claude_research as _claude,
    )

    content_text, metadata = _claude(prompt=full_prompt, verbose=False)

    artifact, created = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-claude",
        defaults={
            "content": content_text or "",
            "description": "Claude Opus deep research with web search.",
            "workflow_context": "Research Gathering",
            "metadata": metadata,
        },
    )
    return artifact
```

### Step 3: Add task pipeline step

**File:** `apps/podcast/tasks.py`

Add `step_claude_research` task, parallel with GPT-Researcher and Gemini in Phase 4:

```python
@task
def step_claude_research(episode_id: int) -> None:
    """Phase 4: Run Claude deep research (parallel)."""
    episode = Episode.objects.get(pk=episode_id)

    # Read the crafted prompt (or fall back to question-discovery context)
    prompt_artifact = EpisodeArtifact.objects.filter(
        episode=episode, title="prompt-claude"
    ).first()

    prompt = prompt_artifact.content if prompt_artifact else _get_episode_context(episode)

    research.run_claude_research(episode_id, prompt)
```

Wire into the existing fan-in signal so it completes alongside `step_gpt_research` and `step_gemini_research`.

### Step 4: Add prompt crafting

**File:** `apps/podcast/services/craft_research_prompt.py`

Add `"claude"` as a valid `research_type` in `craft_research_prompt()`. The Claude prompt should emphasize comprehensive synthesis — combining academic rigor with practical analysis, since Opus excels at long-form reasoning.

**File:** `apps/podcast/services/prompts/craft_research_prompt.md`

Add Claude-specific guidance:

> **Claude Prompts** (comprehensive synthesis):
> - Request multi-perspective analysis combining academic, industry, and policy viewpoints
> - Ask for critical evaluation of competing claims with evidence weighting
> - Emphasize narrative coherence and identification of non-obvious connections
> - Request explicit uncertainty flagging where evidence is weak

### Step 5: Update workflow progress tracking

**File:** `apps/podcast/services/workflow_progress.py`

The `p2-claude` artifact is already tracked in Phase 4 targeted sources (line 144). No changes needed — it will automatically show as complete when the artifact is created by the automated tool instead of manual paste.

### Step 6: Update targeted research prompt crafting

**File:** `apps/podcast/services/analysis.py`

Update `craft_targeted_research_prompts()` to also generate a `prompt-claude` artifact, or create a separate function. The fan-in signal needs to account for the new parallel step.

**File:** `apps/podcast/tasks.py`

Update the Phase 4 orchestration to enqueue `step_claude_research` alongside the existing targeted research tasks.

## Files Changed (Summary)

| File | Change |
|------|--------|
| `apps/podcast/tools/claude_deep_research.py` | **New** — standalone tool script |
| `apps/podcast/services/research.py` | Add `run_claude_research()` |
| `apps/podcast/services/craft_research_prompt.py` | Add `"claude"` research type |
| `apps/podcast/services/prompts/craft_research_prompt.md` | Add Claude prompt guidance |
| `apps/podcast/tasks.py` | Add `step_claude_research` task, wire into Phase 4 fan-in |
| `apps/podcast/services/analysis.py` | Generate `prompt-claude` in targeted prompts |
| `.claude/skills/new-podcast-episode.md` | Remove manual Claude research instructions |

## What This Does NOT Change

- Perplexity, GPT-Researcher, and Gemini continue running as-is
- Cross-validation, briefing, synthesis phases unchanged
- No new dependencies or API keys
- No model changes — existing pipeline uses the same `ANTHROPIC_API_KEY`

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Opus cost per call ($2-8) | Certain | Low | Budget ~$5/episode for this tool; quality justifies it |
| Web search rate limits | Low | Medium | `max_uses=20` caps searches; handle `too_many_requests` gracefully |
| `pause_turn` handling complexity | Medium | Low | Implement continuation loop; similar to Gemini's polling pattern |
| Output quality vs. claude.ai deep research | Low | Low | Same model (Opus), same web access; API may actually be more controllable via system prompts |

## Success Criteria

- [ ] `claude_deep_research.py` tool works standalone via CLI
- [ ] `run_claude_research()` service creates `p2-claude` artifact with citations
- [ ] Task pipeline runs `step_claude_research` in parallel with GPT-Researcher and Gemini
- [ ] Manual Claude research step removed from workflow skill
- [ ] Episode produced end-to-end with automated `p2-claude` source
