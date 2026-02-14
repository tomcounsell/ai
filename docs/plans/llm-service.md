---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/63
---

# Named AI Tools: PydanticAI-Powered Podcast Services

## Problem

The podcast workflow has 8 AI tasks currently run as Claude Code sub-agents (research digest, question discovery, cross-validation, briefing writer, synthesis writer, episode planner, metadata writer, chapter generation). Each sub-agent gets a prompt, can take multiple turns, and returns structured output.

These need to move into the Django codebase so the workflow can run programmatically — from a management command, a Celery task, or an Agent SDK orchestrator. But the right abstraction is NOT a generic `call_llm(prompt) -> str` wrapper — that loses multi-turn capability, locks us to one model provider, and creates a grab-bag function that means nothing.

**Current behavior:**
Human runs Claude Code, invokes `/podcast-episode`, and Claude Code spawns sub-agents for each AI task. Works, but requires a human at the keyboard and ties us to Claude Code's runtime.

**Desired outcome:**
Named, modular AI tools that can be called programmatically. Each tool is self-contained, model-agnostic, individually testable, and independently updatable. Prompts are extracted verbatim from the current sub-agent skill definitions.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM alignment on tool convention.

**Interactions:**
- PM check-ins: 1 (convention review — completed)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `pydantic-ai` installed | `python -c "import pydantic_ai"` | Agent framework |
| `ANTHROPIC_API_KEY` set | `python -c "from django.conf import settings; assert settings.ANTHROPIC_API_KEY"` | Default model provider |

## Solution

### Architecture

```
Orchestrator (future — separate issue)
│
├── Named PydanticAI tools ← THIS ISSUE
│   ├── digest_research()
│   ├── discover_questions()
│   ├── cross_validate()
│   ├── write_briefing()
│   ├── write_synthesis()
│   ├── plan_episode()
│   ├── write_metadata()
│   └── generate_chapters()
│
├── API research tools (already exist as scripts)
│   ├── perplexity_deep_research.py
│   ├── gemini_deep_research.py
│   └── gpt_researcher_run.py
│
└── Processing tools (no LLM)
    ├── audio processing (FFmpeg + Whisper)
    └── cover art generation
```

This issue builds the named tools only. Orchestration (how tools get called in sequence) is a separate concern — see follow-up issues.

### Tool Convention

Each tool is a self-contained Python module in `apps/podcast/services/`. No base class, no shared framework — PydanticAI is already concise enough. The convention is documented, not codified.

**Example: `apps/podcast/services/generate_chapters.py`**

```python
"""Generate chapter markers from a podcast transcript."""

import logging

from pydantic import BaseModel
from pydantic_ai import Agent

logger = logging.getLogger(__name__)


# --- Output schema ---

class Chapter(BaseModel):
    title: str
    start_time: str  # "MM:SS"
    summary: str


class ChapterList(BaseModel):
    chapters: list[Chapter]


# --- Agent ---

agent = Agent(
    "anthropic:claude-sonnet-4-5-20250929",
    result_type=ChapterList,
    system_prompt=(
        "You are a podcast editor. Given a transcript with timestamps, "
        "identify 10-15 natural topic transitions and generate chapter markers. "
        "Each chapter should have a concise, descriptive title."
    ),
)


# --- Public interface ---

def generate_chapters(transcript: str, episode_title: str) -> ChapterList:
    """Generate chapter markers from a transcript.

    Args:
        transcript: Full episode transcript with timestamps.
        episode_title: Title of the episode for context.

    Returns:
        ChapterList with 10-15 chapters.
    """
    result = agent.run_sync(
        f"Episode: {episode_title}\n\nTranscript:\n{transcript}"
    )
    logger.info(
        "generate_chapters: model=%s tokens_in=%d tokens_out=%d",
        result.usage().model_name,
        result.usage().request_tokens,
        result.usage().response_tokens,
    )
    return result.data
```

**Convention rules:**
- File name = tool name (snake_case)
- One public function per module, same name as the file
- Pydantic output model defined in the same file
- PydanticAI Agent defined at module level (not inside the function)
- Model choice is the tool's decision — each tool picks what's appropriate
- Logging includes model name and token usage
- No shared base class — each tool is fully self-contained
- Prompt is inline or loaded from `apps/podcast/services/prompts/{tool_name}.md` if long
- Prompts extracted verbatim from existing `.claude/skills/` sub-agent definitions — don't rewrite, just convert the input/output interface

### Tools Inventory

| Tool | Input | Output | Default Model | Complexity |
|------|-------|--------|---------------|------------|
| `digest_research` | Research file text | Structured digest with key findings, gaps, sources | Sonnet | Simple |
| `discover_questions` | Research digest | Gap analysis with follow-up questions by category | Sonnet | Simple |
| `cross_validate` | Multiple research digests | Verification matrix (verified / single-source / conflicting) | Sonnet | Moderate |
| `write_briefing` | All digests + cross-validation | Master briefing organized by topic | Sonnet | Moderate |
| `write_synthesis` | Briefing | Narrative report (5,000-8,000 words) | Opus | Complex |
| `plan_episode` | Report + briefing | Episode structure with sections, modes, transitions | Opus | Complex |
| `write_metadata` | Report + transcript + chapters | Publishing metadata, descriptions, keywords | Sonnet | Simple |
| `generate_chapters` | Transcript | Chapter markers with titles and timestamps | Sonnet | Simple |

### Technical Approach

1. **Create tool modules in `apps/podcast/services/`** — one file per tool, following the convention above. Flat directory alongside existing `workflow_progress.py`.

2. **Extract prompts from `.claude/skills/`** — each sub-agent skill file contains the prompt used today. Extract verbatim into the tool's system prompt (inline) or into `apps/podcast/services/prompts/{tool_name}.md` (if long). Don't rewrite — only change the input/output interface to match PydanticAI.

3. **Tests in `apps/podcast/tests/test_ai_tools/`** — one test file per tool, mocked PydanticAI responses.

### File Changes

| File | Action | Description |
|------|--------|-------------|
| `apps/podcast/services/digest_research.py` | Create | Research digest tool |
| `apps/podcast/services/discover_questions.py` | Create | Question discovery tool |
| `apps/podcast/services/cross_validate.py` | Create | Cross-validation tool |
| `apps/podcast/services/write_briefing.py` | Create | Briefing writer tool |
| `apps/podcast/services/write_synthesis.py` | Create | Synthesis/report writer tool |
| `apps/podcast/services/plan_episode.py` | Create | Episode planner tool |
| `apps/podcast/services/write_metadata.py` | Create | Metadata generator tool |
| `apps/podcast/services/generate_chapters.py` | Create | Chapter marker generator |
| `apps/podcast/services/prompts/` | Create | Prompt templates for tools with long prompts |
| `apps/podcast/tests/test_ai_tools/` | Create | Test files for each tool |

## Rabbit Holes

- **Don't build a base class or framework** — PydanticAI is already concise. A `BaseTool` class adds indirection without value. If a pattern emerges after building 8 tools, extract it then.
- **Don't add streaming** — These tools run in background tasks, not interactive sessions.
- **Don't persist token usage to DB yet** — Logging is sufficient. Add a `TokenUsage` model when we actually need cost reports.
- **Don't build the orchestrator** — That's a separate issue. Build tools first; they're useful standalone.
- **Don't rewrite prompts** — Extract verbatim from skill definitions. Prompt quality audit is a separate follow-up issue.

## Risks

### Risk 1: PydanticAI Agent API changes
**Impact:** PydanticAI is pre-2.0. Breaking changes could affect all tools.
**Mitigation:** Each tool is self-contained, so updates are isolated. Pin `pydantic-ai>=1.0.0,<2.0.0` (already done). If the API changes, update tools individually.

### Risk 2: Prompt extraction loses context
**Impact:** Sub-agent skill definitions may include instructions that only make sense in the Claude Code context (e.g., "use the Read tool to..."). Extracting verbatim could include irrelevant instructions.
**Mitigation:** Strip Claude Code-specific tool references during extraction, but keep all domain instructions intact. The follow-up prompt audit issue will catch quality regressions.

## No-Gos

- No generic `call_llm()` function — every tool has a name and purpose
- No provider abstraction layer — PydanticAI already handles this
- No shared base class for tools
- No async (sync `run_sync()` is fine for background tasks)
- No conversation memory between tools (each call is independent)
- No orchestrator in this issue — separate concern

## Update System

No update system changes required — this is internal to the cuttlefish Django app.

## Agent Integration

No agent integration in this issue. The tools are Python modules callable from anywhere. How they get wired into an orchestrator (Agent SDK, management command, Celery) is a follow-up issue.

## Documentation

### Inline Documentation
- [ ] Each tool module has a docstring explaining inputs, outputs, and model choice
- [ ] Prompt template files have a header comment explaining what the tool does

### Convention Documentation
- [ ] Tool convention documented in `docs/AI_CONVENTIONS.md` (add "Named AI Tools" section)

## Team Orchestration

### Team Members

- **Builder (tools)**
  - Name: tool-builder
  - Role: Implement named PydanticAI tools following the convention
  - Agent Type: builder
  - Resume: true

- **Validator (tools)**
  - Name: tool-validator
  - Role: Verify each tool is self-contained, testable, and follows convention
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create tool convention example
- **Task ID**: build-convention
- **Depends On**: none
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/services/generate_chapters.py` as the reference implementation
- Extract prompt from existing chapter generation logic in `.claude/skills/`
- Create `apps/podcast/tests/test_ai_tools/test_generate_chapters.py` with mocked agent
- Create `apps/podcast/services/prompts/` directory

### 2. Build simple tools (batch 1)
- **Task ID**: build-simple-tools
- **Depends On**: build-convention
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- `digest_research.py` — extract prompt from podcast-research-digest skill
- `discover_questions.py` — extract prompt from podcast-question-discovery skill
- `write_metadata.py` — extract prompt from podcast-metadata-writer skill
- Tests for each tool

### 3. Build moderate tools (batch 2)
- **Task ID**: build-moderate-tools
- **Depends On**: build-convention
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- `cross_validate.py` — extract prompt from podcast-cross-validator skill
- `write_briefing.py` — extract prompt from podcast-briefing-writer skill
- Prompt templates in `prompts/` for these tools
- Tests for each tool

### 4. Build complex tools (batch 3)
- **Task ID**: build-complex-tools
- **Depends On**: build-convention
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- `write_synthesis.py` — extract prompt from podcast-synthesis-writer skill
- `plan_episode.py` — extract prompt from podcast-episode-planner skill
- Prompt templates in `prompts/` for these tools
- Tests for each tool

### 5. Validate all tools
- **Task ID**: validate-tools
- **Depends On**: build-simple-tools, build-moderate-tools, build-complex-tools
- **Assigned To**: tool-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify each tool follows the convention (no base class, own agent, own output model)
- Verify each tool is independently importable and testable
- Verify model choices are appropriate per tool
- Verify prompts were extracted (not rewritten)
- Run all tests

### 6. Update documentation
- **Task ID**: document-convention
- **Depends On**: validate-tools
- **Assigned To**: tool-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add "Named AI Tools" section to `docs/AI_CONVENTIONS.md`
- Document the tool convention with the generate_chapters example

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: document-convention
- **Assigned To**: tool-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all 8 tools importable
- Verify documentation is accurate
- Generate final report

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_ai_tools/ -v` — all tool tests pass
- `python -c "from apps.podcast.services.generate_chapters import generate_chapters"` — tools importable
- `python -c "from apps.podcast.services.write_metadata import write_metadata"` — tools importable
- `python -c "from apps.podcast.services.write_synthesis import write_synthesis"` — tools importable

## Success Criteria

- [ ] All 8 named tools created in `apps/podcast/services/`
- [ ] Each tool has its own PydanticAI Agent, output model, and public function
- [ ] Each tool is model-agnostic (model specified in PydanticAI format, swappable)
- [ ] Prompts extracted from existing `.claude/skills/` sub-agent definitions
- [ ] Each tool individually testable with mocked model responses
- [ ] All tests pass
- [ ] Convention documented in `docs/AI_CONVENTIONS.md`
