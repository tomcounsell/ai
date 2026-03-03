# SDLC-First Routing

Automatic classification and routing of incoming work requests to determine whether they should be processed as SDLC pipeline work (from the ai/ repo) or as conversational responses (from the target project directory).

## Problem

Previously, all agent sessions ran from a single working directory regardless of whether the request was development work or a simple question. This meant:

1. SDLC pipeline commands couldn't access the orchestrator skills and dispatch logic in `ai/`
2. Conversational questions loaded unnecessary SDLC context
3. No automated way to distinguish "build this feature" from "what does this function do?"

## Solution

A two-stage routing system: fast-path pattern matching for obvious cases, LLM classification for ambiguous requests.

### Classification (`bridge/routing.py`)

`classify_work_request(message, project_slug)` returns `"sdlc"` or `"question"`:

1. **Fast paths** (no LLM call needed):
   - Slash commands (`/sdlc`, `/do-build`, `/do-plan`, etc.) → `sdlc`
   - Short messages under 20 chars without action verbs → `question`

2. **LLM classification** (for everything else):
   - Primary: Ollama (llama3.2, fast, local, free)
   - Fallback: Anthropic Haiku (when Ollama unavailable)
   - Prompt asks for single-word `sdlc` or `question` response
   - Any classification failure defaults to `question` (safe fallback)

### Orchestrator Routing (`agent/sdk_client.py`)

Based on classification result:

| Classification | Working Directory | Behavior |
|---|---|---|
| `sdlc` | `ai/` repo root | Full SDLC pipeline access, TARGET_REPO context injected |
| `question` | Target project dir | Direct project context, no SDLC overhead |

For SDLC-routed requests, a `TARGET_REPO` context block is injected into the system prompt so the agent knows which project to dispatch workers to.

### System Prompt Ordering

The system prompt was reordered to prioritize SDLC workflow instructions:

1. SDLC workflow rules (MUST language, negative examples)
2. SOUL.md (persona and values)
3. Project-specific context

This ensures the agent defaults to SDLC pipeline behavior for work requests rather than acting directly.

## Lazy Singleton Client

The Anthropic client used for Haiku fallback classification is instantiated lazily via `_get_anthropic_client()` to avoid per-call overhead. The classify function itself is imported lazily inside `get_agent_response_sdk()` to prevent circular imports between `agent/` and `bridge/` modules.

## Files

| File | Purpose |
|------|---------|
| `bridge/routing.py` | `classify_work_request()`, `_classify_work_request_llm()`, `_get_anthropic_client()` |
| `agent/sdk_client.py` | Orchestrator routing logic, system prompt ordering, TARGET_REPO injection |
| `config/SOUL.md` | Professional Standards section (SDLC-first defaults) |
| `tests/test_work_request_classifier.py` | 44 tests covering fast paths, LLM classification, narration stripping |

## Related

- [SDLC Enforcement](sdlc-enforcement.md) -- Quality gates and pipeline stage model
- [Summarizer Format](summarizer-format.md) -- Process narration stripping added alongside routing
- [Coaching Loop](coaching-loop.md) -- Output classification (distinct from input classification)
