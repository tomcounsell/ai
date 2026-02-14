---
status: Ready
type: feature
appetite: Small
owner: Tom
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/71
---

# Named AI Tool for Research Prompt Generation

## Problem

The podcast task pipeline (`tasks.py`) uses a private `_craft_research_prompt()` function that produces generic, template-based prompts for research tools. Each template is a static string with the episode context appended.

**Current behavior:**

The Perplexity prompt is always: *"Conduct comprehensive academic and peer-reviewed research on the following topic. Focus on systematic reviews, meta-analyses..."* regardless of whether the topic is about microplastics, monetary policy, or sleep science. The GPT and Gemini prompts similarly ignore the episode's specific content, gaps discovered in earlier phases, and the question-discovery artifact's structured recommendations.

**Desired outcome:**

An AI-crafted prompt that reads the episode's accumulated artifacts (brief, question-discovery with its structured tool recommendations) and produces a research prompt specifically tailored to:
- The episode's unique subject matter and framing
- The specific gaps and questions identified by question discovery
- The target research tool's strengths (academic for Perplexity, industry for GPT-Researcher, policy for Gemini)
- The tool recommendations already present in the `QuestionDiscovery` output

Additionally, the tool creates empty placeholder artifacts for targeted research sources, enabling the existing `post_save` fan-in signal to work without hardcoded artifact title sets.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This follows an established Named AI Tool pattern with 8 existing examples. The service-layer wiring pattern is identical to `discover_questions` in `analysis.py`.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('ANTHROPIC_API_KEY')"` | PydanticAI agent needs Anthropic API access |
| Task pipeline exists | `python -c "from apps.podcast.tasks import step_gpt_research"` | Issue #70 task pipeline must be merged |

## Solution

### Key Elements

- **Named AI Tool** (`craft_research_prompt.py`): Single-file PydanticAI module that generates tailored research prompts
- **Service wrapper** (`analysis.py`): DB-aware function that reads artifacts, calls the tool, creates placeholders, and returns prompts
- **Task integration** (`tasks.py`): Replace `_craft_research_prompt()` with calls to the new service function

### Flow

The tool is called at two points in the pipeline — once per research phase — because the available context differs:

1. **Phase 2** (`step_perplexity_research`): AI crafts Perplexity prompt from `p1-brief` only
2. **Phase 4** (`step_question_discovery` end): AI crafts GPT + Gemini prompts from `p1-brief` + `question-discovery`, creates placeholders, logs prompts to workflow history

**Phase 2:** task step → `analysis.craft_research_prompt(episode_id, "perplexity")` → reads p1-brief → AI generates tailored prompt → logs prompt to `EpisodeWorkflow.history` → returns prompt string → task passes to `research.run_perplexity_research()`

**Phase 4:** task step → `analysis.craft_targeted_research_prompts(episode_id)` → reads p1-brief + question-discovery → AI generates GPT + Gemini prompts in one call → creates empty p2-chatgpt/p2-gemini placeholders → logs prompts to `EpisodeWorkflow.history` → task fans out to parallel research steps which read prompts from history

### Technical Approach

**Named AI Tool** (`apps/podcast/services/craft_research_prompt.py`):
- Pydantic output model: `ResearchPrompt` with field `prompt: str` (single prompt per research type)
- Second output model: `TargetedResearchPrompts` with fields `gpt_prompt: str`, `gemini_prompt: str` (for the Phase 4 batch call)
- Model: Sonnet (fast, focused task — prompt crafting doesn't need Opus-level reasoning)
- System prompt in `apps/podcast/services/prompts/craft_research_prompt.md`
- Two public functions:
  - `craft_research_prompt(episode_brief: str, episode_topic: str, episode_title: str, research_type: str) -> ResearchPrompt` — single prompt (used for Perplexity in Phase 2)
  - `craft_targeted_prompts(episode_brief: str, question_discovery: str, episode_topic: str, episode_title: str) -> TargetedResearchPrompts` — GPT + Gemini prompts in one call (used in Phase 4)

**System prompt guidance** — instruct the agent to:
- For Perplexity: focus on academic/peer-reviewed angles specific to the topic; identify the most relevant types of studies, key researchers, and institutional sources
- For GPT-Researcher: extract the industry/technical questions from question-discovery; frame for multi-agent web research across case studies, market reports, and expert analysis
- For Gemini: extract the policy/regulatory questions from question-discovery; frame for government docs, regulatory frameworks, and strategic analysis
- Reference the `recommended_tools` from question-discovery to route specific questions to the right prompt

**Service wrappers** (in `analysis.py`):

`craft_research_prompt(episode_id, research_type)`:
- Reads `p1-brief` artifact
- Calls the single-prompt Named AI Tool function
- Logs the generated prompt to `EpisodeWorkflow.history` as `{"step": "Craft Prompt", "research_type": "perplexity", "prompt": "...", "crafted_at": "..."}`
- Returns the prompt string

`craft_targeted_research_prompts(episode_id)`:
- Reads `p1-brief` and `question-discovery` artifacts
- Calls the batch Named AI Tool function
- Creates empty placeholder artifacts (`p2-chatgpt`, `p2-gemini`) via `update_or_create` with `content=""`
- Logs both prompts to `EpisodeWorkflow.history` as `{"step": "Craft Targeted Prompts", "gpt_prompt": "...", "gemini_prompt": "...", "crafted_at": "..."}`
- Returns `TargetedResearchPrompts` (task steps read from the return value or from history)

**Placeholder creation principle**: The thing that expects a result creates the place to put it. By creating placeholders here, the signal's fan-in check becomes data-driven rather than relying on a hardcoded set.

**Prompt logging**: Generated prompts are appended to `EpisodeWorkflow.history` (JSONField). This provides an audit trail without creating additional artifacts. The history already tracks step transitions; prompt entries are a natural extension.

**Task integration** (`tasks.py`):
- Remove `_craft_research_prompt()` private function
- In `step_perplexity_research`: call `analysis.craft_research_prompt(episode_id, "perplexity")` to get the AI-crafted prompt
- In `step_question_discovery`: after question discovery completes, call `analysis.craft_targeted_research_prompts(episode_id)` to generate GPT/Gemini prompts and create placeholders
- In `step_gpt_research` and `step_gemini_research`: read the pre-generated prompts from `EpisodeWorkflow.history` (find the entry with `step == "Craft Targeted Prompts"`)

**Signal cleanup** (`signals.py`):
- `_check_targeted_research_complete()` can query placeholder artifacts dynamically instead of checking against `{"p2-chatgpt", "p2-gemini"}`. Check all `p2-*` artifacts (excluding `p2-perplexity` which is from Phase 2) and verify they all have content.

## Rabbit Holes

- **Three separate AI calls**: The Perplexity prompt must be a separate call (different context — no question-discovery yet), but GPT + Gemini should be one call since they share the same context.
- **Dynamic tool selection**: Don't try to dynamically decide which research tools to use based on AI analysis. The pipeline uses a fixed set (Perplexity, GPT-Researcher, Gemini). Keep it deterministic.
- **Prompt versioning**: Don't build infrastructure for A/B testing prompts or tracking which prompt version produced which research. That's a separate concern.

## Risks

### Risk 1: AI-generated prompts may be worse than templates for some topics
**Impact:** Lower quality research output
**Mitigation:** The system prompt for the tool can include the current template text as a baseline, ensuring the AI always produces something at least as specific as the template. Log token usage to monitor costs.

### Risk 2: Extra latency from AI calls before research
**Impact:** Adds ~5-10 seconds per call (two calls total: one in Phase 2, one in Phase 4)
**Mitigation:** Negligible compared to research tool execution times (3-20 minutes each).

## No-Gos (Out of Scope)

- Dynamic research tool selection (always use the fixed three tools)
- Prompt quality evaluation or scoring
- Multi-turn prompt refinement
- Changes to the research service functions themselves (`research.py`)

## Update System

No update system changes required — this is a Django service-layer addition with no external dependencies or configuration changes.

## Agent Integration

No agent integration required — this tool is called internally by the task pipeline, not exposed via MCP.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/podcast-services.md` — add `craft_research_prompts` to the analysis service section
- [ ] Update `CLAUDE.md` — add `craft_research_prompt.py` to the Named AI Tools table

### Inline Documentation
- [ ] Docstrings on the public function and Pydantic models (following existing pattern)

## Success Criteria

- [ ] `craft_research_prompt.py` exists as a Named AI Tool following the established pattern
- [ ] System prompt in `apps/podcast/services/prompts/craft_research_prompt.md`
- [ ] `analysis.craft_research_prompt(episode_id, research_type)` generates Perplexity prompt in Phase 2
- [ ] `analysis.craft_targeted_research_prompts(episode_id)` generates GPT/Gemini prompts + placeholders in Phase 4
- [ ] Generated prompts logged to `EpisodeWorkflow.history` JSONField
- [ ] `_craft_research_prompt()` removed from `tasks.py`
- [ ] Task steps use the new service functions for prompt generation
- [ ] `step_gpt_research`/`step_gemini_research` read prompts from workflow history
- [ ] Empty placeholder artifacts created before targeted research fan-out
- [ ] Signal fan-in still works correctly with placeholder pattern
- [ ] Tests pass: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v`
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (prompt-tool)**
  - Name: prompt-tool-builder
  - Role: Implement the Named AI Tool, service wrapper, and task integration
  - Agent Type: builder
  - Resume: true

- **Validator (prompt-tool)**
  - Name: prompt-tool-validator
  - Role: Verify implementation matches pattern and tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Named AI Tool
- **Task ID**: build-named-tool
- **Depends On**: none
- **Assigned To**: prompt-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/services/craft_research_prompt.py` with `ResearchPrompt` and `TargetedResearchPrompts` output models
- Two public functions: `craft_research_prompt()` (single) and `craft_targeted_prompts()` (batch GPT+Gemini)
- Create `apps/podcast/services/prompts/craft_research_prompt.md` system prompt
- Follow pattern from existing tools (e.g., `discover_questions.py`, `write_briefing.py`)

### 2. Add service wrappers in analysis.py
- **Task ID**: build-service-wrapper
- **Depends On**: build-named-tool
- **Assigned To**: prompt-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `craft_research_prompt(episode_id, research_type)` — reads p1-brief, calls tool, logs prompt to `EpisodeWorkflow.history`
- Add `craft_targeted_research_prompts(episode_id)` — reads p1-brief + question-discovery, calls tool, creates empty `p2-chatgpt`/`p2-gemini` placeholders, logs prompts to `EpisodeWorkflow.history`

### 3. Integrate with task pipeline
- **Task ID**: build-task-integration
- **Depends On**: build-service-wrapper
- **Assigned To**: prompt-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `_craft_research_prompt()` from `tasks.py`
- In `step_perplexity_research`: call `analysis.craft_research_prompt(episode_id, "perplexity")` for AI-crafted prompt
- At end of `step_question_discovery`: call `analysis.craft_targeted_research_prompts(episode_id)` to generate prompts + placeholders
- Update `step_gpt_research` and `step_gemini_research` to read prompts from `EpisodeWorkflow.history`

### 4. Update signal fan-in
- **Task ID**: build-signal-update
- **Depends On**: build-task-integration
- **Assigned To**: prompt-tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_check_targeted_research_complete()` to check placeholder artifacts dynamically
- Remove hardcoded `_TARGETED_RESEARCH_TITLES` set (or make it the default fallback)

### 5. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-signal-update
- **Assigned To**: prompt-tool-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify Named AI Tool follows pattern (one file, one function, one agent, one output model)
- Verify service wrapper creates placeholders correctly
- Verify task pipeline no longer has `_craft_research_prompt()`
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v`
- Verify signal fan-in works with new placeholder pattern

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: prompt-tool-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/podcast-services.md`
- Update `CLAUDE.md` Named AI Tools table
- Update `docs/AI_CONVENTIONS.md` if the tool count reference needs updating

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` — all podcast tests pass
- `python -c "from apps.podcast.services.craft_research_prompt import craft_research_prompt"` — tool importable
- `grep -r "_craft_research_prompt" apps/podcast/tasks.py` — should return no results (removed)

## Resolved Questions

1. **Perplexity prompt**: AI-crafted (not template). Called in Phase 2 with p1-brief only. Question-discovery context is not available yet, but the AI still produces a better prompt than a static template by reasoning about the specific topic.

2. **Prompt storage**: Logged to `EpisodeWorkflow.history` JSONField. No new artifact type needed. History already tracks step transitions; prompt entries are a natural extension and provide an audit trail.
