---
status: Planning
type: chore
appetite: Small
owner: Tom
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/65
---

# Audit AI Tool Prompts Post-Conversion

## Problem

The Named AI Tools (#63) converted 8 Claude Code sub-agents into PydanticAI agents. Each tool has a detailed `.md` prompt file in `apps/podcast/services/prompts/`, but 4 of 8 tools never load their prompt file — they use a short inline string instead, losing substantial quality guidance.

**Current behavior:**
- 4 tools (`digest_research`, `discover_questions`, `generate_chapters`, `write_metadata`) use 1-3 sentence inline prompts and ignore their 51-94 line `.md` prompt files
- 4 tools (`cross_validate`, `write_briefing`, `write_synthesis`, `plan_episode`) correctly load from `.md` files but those files are comparatively thin (17-26 lines) and could benefit from enrichment
- No Claude Code-specific references found (that cleanup was done correctly)

**Desired outcome:**
- All 8 tools load prompts from `.md` files (consistent pattern)
- Prompt quality guidance is comprehensive: editorial principles, count constraints, source type handling, evidence hierarchy
- Structural redundancy with Pydantic output schemas is trimmed

## Appetite

**Size:** Small

**Team:** Solo dev, no review.

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This is a straightforward consistency fix — no architectural decisions, no new code patterns. The `.md` files already exist and are well-written.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Prompt loading consistency**: Switch 4 inline-prompt tools to load from their existing `.md` files
- **Prompt enrichment**: Add quality guidance to the 4 thin `.md` files already in use
- **Redundancy trimming**: Remove instructions that merely describe output fields already defined by Pydantic schemas

### Flow

Inline prompt tools → Load from `.md` file → Richer guidance reaches model → Better structured output quality

### Technical Approach

- Apply the `Path(__file__).parent / "prompts" / "name.md"` pattern (already used by 4 tools) to the remaining 4
- Enrich the 4 thin prompts with source type awareness, evidence hierarchy, editorial principles from original sub-agent definitions
- In all 8 prompts, keep HOW-to-fill guidance, remove WHAT-fields-exist guidance (schema handles that)

## Detailed Changes

### Python files (4 files — add `.md` loading)

Each needs: `from pathlib import Path`, `_PROMPT_FILE`/`_SYSTEM_PROMPT` constants, replace inline `system_prompt=(...)`.

| File | Prompt file to load |
|------|-------------------|
| `apps/podcast/services/digest_research.py` | `prompts/research_digest.md` |
| `apps/podcast/services/discover_questions.py` | `prompts/discover_questions.md` |
| `apps/podcast/services/generate_chapters.py` | `prompts/generate_chapters.md` |
| `apps/podcast/services/write_metadata.py` | `prompts/write_metadata.md` |

### Prompt files (4 files — enrich thin prompts)

| File | Current | Add |
|------|---------|-----|
| `prompts/cross_validate.md` | 17 lines | Source type awareness (Grok = opinion not evidence), near-miss stats handling, confidence tiering detail |
| `prompts/write_briefing.md` | 19 lines | Practical implementation parameters, story bank criteria (memorability/resonance), evidence hierarchy |
| `prompts/write_synthesis.md` | 23 lines | Narrative techniques, uncertainty framing, Foundation/Evidence/Application structure |
| `prompts/plan_episode.md` | 26 lines | Mode definitions, counterpoint quality criteria, NotebookLM two-host tips |

### Prompt files (4 files — already comprehensive, trim redundancy)

| File | Lines | Action |
|------|-------|--------|
| `prompts/research_digest.md` | 77 | Trim "Output Format" section (schema handles it), keep Source Type Awareness table |
| `prompts/discover_questions.md` | 94 | Trim "Output Format" section, keep Guiding Principles and tool strength table |
| `prompts/generate_chapters.md` | 51 | Trim "Output Format" section, keep Title Style and Transition Detection guidance |
| `prompts/write_metadata.md` | 87 | Trim "Output Format" section, keep Quality Standards and CTA guidance |

## Rabbit Holes

- Rewriting prompts from scratch — the existing `.md` files are well-written, just need loading and minor enrichment
- Running every tool on real episode data as a quality test — valuable but separate from the code fix; can be done later
- Optimizing prompts for token efficiency — premature optimization, prompts are already concise

## Risks

### Risk 1: Longer prompts increase token usage
**Impact:** Marginal cost increase per tool invocation
**Mitigation:** The `.md` files are 50-90 lines, adding ~500-1000 tokens to system prompt. Negligible vs the research input text.

## No-Gos (Out of Scope)

- Changing Pydantic output schemas
- Changing model selection (Sonnet vs Opus)
- Running quality regression tests against real episodes (separate task)
- Modifying the service layer functions or their signatures
- Touching the original `.claude/agents/` or `.claude/skills/` files

## Update System

No update system changes required — this is an internal prompt quality improvement.

## Agent Integration

No agent integration required — these are internal service layer components.

## Documentation

No documentation changes needed — the tools' public interfaces don't change.

## Success Criteria

- [ ] All 8 tools load system prompts from `.md` files (zero inline prompts)
- [ ] All 8 prompt files include quality guidance not expressible by schemas
- [ ] No structural redundancy (no "return a list of X" when schema already defines `list[X]`)
- [ ] `python -c "from apps.podcast.services import digest_research, discover_questions, cross_validate, write_briefing, write_synthesis, plan_episode, write_metadata, generate_chapters"` succeeds
- [ ] `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` passes

## Validation Commands

- `python -c "from apps.podcast.services import digest_research, discover_questions, cross_validate, write_briefing, write_synthesis, plan_episode, write_metadata, generate_chapters"` — all 8 tools import cleanly
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` — existing tests pass
