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

`classify_work_request(message, project_slug)` returns `"sdlc"`, `"collaboration"`, `"other"`, `"question"`, or `"passthrough"`:

1. **Fast paths** (no LLM call needed):
   - Slash commands (`/sdlc`, `/do-build`, `/do-plan`, etc.) → `sdlc`
   - PR/issue references (`PR 478`, `issue #463`, `#471`) → `sdlc`
   - Short acknowledgments (`continue`, `ok`, `yes`) → `passthrough`

2. **LLM classification** (for everything else):
   - Primary: Ollama (gemma4:e2b, fast, local, free)
   - Fallback: Anthropic Haiku (when Ollama unavailable)
   - Prompt asks for single-word `sdlc`, `collaboration`, `other`, or `question` response
   - Default for ambiguous messages: `collaboration` (cheaper when wrong than SDLC)
   - Any classification failure defaults to `question` (safe fallback)

### Orchestrator Routing (`agent/sdk_client.py`)

Based on classification result:

| Classification | Working Directory | Behavior |
|---|---|---|
| `sdlc` | `ai/` repo root | Full SDLC pipeline access, TARGET_REPO context injected |
| `collaboration` | `ai/` repo root | PM direct-action mode (handles with available tools, no dev-session) |
| `other` | `ai/` repo root | PM uses judgment (may handle directly or spawn dev-session) |
| `question` | Target project dir | Direct project context, no SDLC overhead |

The SDK client reads `classification_type` from the `AgentSession` stored by the bridge. If not set (e.g., async classifier lost the race with session pickup), a synchronous fast-path regex checks the message for PR/issue references before falling back to `"question"`. This prevents messages like "Complete PR 478" from being misrouted.

For SDLC-routed requests, a `TARGET_REPO` context block is injected into the system prompt so the agent knows which project to dispatch workers to. The subprocess environment includes `GH_REPO=org/repo` so all `gh` CLI commands automatically target the correct repository. A `GITHUB: org/repo` line in the prompt context serves as a secondary safety net.

### System Prompt Ordering

The system prompt was reordered to prioritize SDLC workflow instructions:

1. SDLC workflow rules (MUST language, negative examples)
2. SOUL.md (persona and values)
3. Project-specific context

This ensures the agent defaults to SDLC pipeline behavior for work requests rather than acting directly.

## Lazy Singleton Client

The Anthropic client used for Haiku fallback classification is instantiated lazily via `_get_anthropic_client()` to avoid per-call overhead. The classify function itself is imported lazily inside `get_agent_response_sdk()` to prevent circular imports between `agent/` and `bridge/` modules.

## Cross-Repo `gh` Resolution

When SDLC is invoked for a non-ai project (e.g., popoto), the worker runs with `cwd=ai/` (the orchestrator repo). All `gh` commands (issue view, pr list, etc.) resolve against the cwd repo by default, which causes cross-project SDLC work to silently target the wrong repository.

### Primary Mechanism: `GH_REPO` Environment Variable

The `gh` CLI supports a `GH_REPO` environment variable that automatically applies to all commands in the subprocess. This is the deterministic fix -- it requires no LLM cooperation.

When `get_agent_response_sdk()` detects a cross-repo SDLC request (classification is "sdlc", project key is not "valor", and project mode is not "pm"), it:

1. Extracts `github.org` and `github.repo` from the project config in `~/Desktop/Valor/projects.json`
2. Passes `gh_repo="org/repo"` to `ValorAgent.__init__()`
3. `ValorAgent._create_options()` sets `env["GH_REPO"] = self.gh_repo` in the subprocess environment

All `gh` commands in the subprocess then automatically target the correct repository without needing explicit `--repo` flags.

```python
# In get_agent_response_sdk():
is_cross_repo = project_key != "valor"
if project_mode != "pm" and classification == "sdlc" and is_cross_repo:
    _github_config = project.get("github", {})
    _gh_org = _github_config.get("org", "")
    _gh_name = _github_config.get("repo", "")
    if _gh_org and _gh_name:
        _gh_repo = f"{_gh_org}/{_gh_name}"

agent = ValorAgent(..., gh_repo=_gh_repo)
```

### Safety Net: `GITHUB:` Context Line and `--repo` Instructions

As a belt-and-suspenders fallback, the SDK client also injects a `GITHUB: org/repo` line into the enriched prompt text. Skills include instructions to parse this line and add `--repo` flags to `gh` commands. This is a secondary mechanism -- the `GH_REPO` env var is the primary fix.

Skills with `--repo` instructions: `/sdlc`, `/do-issue`, `/do-plan`, `/do-pr-review`, `/do-docs`, `/do-patch`.

### Verification

After fetching an issue, the SDLC skill verifies the issue URL matches the expected project. This catches misconfiguration early rather than silently operating on the wrong issue.

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
- [Chat Dev Session Architecture](pm-dev-session-architecture.md) -- Session routing and orchestration
- [PM Routing: Collaboration](pm-routing-collaboration.md) -- Four-way classification extending this two-way system
