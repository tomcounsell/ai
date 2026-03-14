---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-14
tracking: https://github.com/yudame/cuttlefish/issues/169
last_comment_id:
---

# Integrate MiroFish Swarm Intelligence into Podcast Research Pipeline

## Problem

The podcast production workflow runs 5 research tools during the Research Gathering phase (Perplexity, GPT-Researcher, Gemini, Together, Claude). All of them return **factual web-sourced summaries** — search results, article synthesis, and document analysis.

**Current behavior:**
No research source generates perspective-oriented outputs: stakeholder reaction modeling, prediction generation, counter-argument stress-testing, or audience reception simulation. The host must do all of that mental modeling manually before recording.

**Desired outcome:**
A 6th research source — MiroFish swarm intelligence — that produces a `p2-mirofish` artifact containing multi-agent simulation results: predicted stakeholder reactions, evidence-based forecasts, counter-arguments, and audience reception modeling. This artifact flows into Cross-Validation, Synthesis, and Episode Planning like any other `p2-*` artifact.

## Prior Art

- **[PR #80](https://github.com/yudame/cuttlefish/pull/80)**: Add Together Open Deep Research as 5th automated p2-* source — established the latest pattern for adding a new research tool with graceful degradation
- **[PR #109](https://github.com/yudame/cuttlefish/pull/109)**: Improve Gemini error detection, close Claude research issue — shows error handling and skip-artifact patterns
- **[Issue #78](https://github.com/yudame/cuttlefish/issues/78)**: Build deep research orchestrator (replicate claude.ai deep research via API) — the Claude multi-agent research pipeline, architecturally the closest precedent to MiroFish's multi-agent approach
- **[PR #67](https://github.com/yudame/cuttlefish/pull/67)**: Add 8 named PydanticAI tools for podcast services — the tool registration pattern

No prior attempts to integrate MiroFish or any swarm intelligence system.

## Data Flow

1. **Entry point**: `run_mirofish_research(episode_id, prompt)` called during Research Gathering phase
2. **Context assembly**: `_get_episode_context(episode)` fetches episode title + best available context (question-discovery artifact > p1-brief > description)
3. **Prompt construction**: Episode context + research query combined into a MiroFish-specific prompt that emphasizes perspective simulation over factual search
4. **MiroFish API call**: HTTP POST to the MiroFish backend API (`/api/predict` or equivalent) with the constructed prompt, agent configuration, and simulation parameters
5. **Response parsing**: Extract prediction report, agent dialogues, and key findings from the MiroFish JSON response
6. **Artifact persistence**: `EpisodeArtifact.objects.update_or_create()` saves results as `p2-mirofish` with `workflow_context="Research Gathering"`
7. **Downstream consumption**: Cross-Validation, Synthesis, and Episode Planning services read `p2-mirofish` alongside other `p2-*` artifacts

## Architectural Impact

- **New dependencies**: `httpx` (already likely in the project) for async-capable HTTP calls to the MiroFish backend API. MiroFish itself runs as a separate service (Docker or source deployment) — it is NOT a Python library dependency.
- **Interface changes**: None. The new function follows the exact same `(episode_id, prompt) -> EpisodeArtifact` contract as the 5 existing research tools.
- **Coupling**: Minimal — MiroFish is accessed via HTTP API, same as Perplexity/Gemini. No shared state, no Django model changes.
- **Data ownership**: MiroFish service owns its simulation state. Cuttlefish only stores the final report text in `EpisodeArtifact.content` and structured metadata in `EpisodeArtifact.metadata`.
- **Reversibility**: Trivial — remove `run_mirofish_research()`, remove tool wrapper, remove call from orchestrator. No schema changes involved.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (confirm MiroFish deployment approach)
- Review rounds: 1 (code review)

The integration pattern is well-established (5 prior research tools). The main new work is: (a) standing up MiroFish as a service, (b) discovering its API contract, and (c) crafting the perspective-oriented prompt template.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| MiroFish service running | `curl -sf http://localhost:5001/health 2>/dev/null && echo OK` | MiroFish backend must be accessible |
| `MIROFISH_API_URL` env var | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('MIROFISH_API_URL')"` | MiroFish backend URL |
| `MIROFISH_LLM_API_KEY` (or MiroFish's own `.env`) | MiroFish configured with LLM provider key | MiroFish needs an LLM key to run simulations |

## Solution

### Key Elements

- **MiroFish tool wrapper** (`apps/podcast/tools/mirofish_research.py`): HTTP client that calls the MiroFish backend API, sends simulation requests, and parses responses into text + metadata
- **Research service function** (`run_mirofish_research` in `apps/podcast/services/research.py`): Follows the existing `(episode_id, prompt) -> EpisodeArtifact` contract with graceful degradation
- **Perspective prompt template**: A prompt design that steers MiroFish toward its unique strengths — stakeholder modeling, prediction generation, counter-arguments — rather than duplicating factual search
- **Workflow wiring**: Hook into Research Gathering phase so MiroFish runs alongside existing tools

### Flow

**Research Gathering step** → Workflow orchestrator calls `run_mirofish_research(episode_id, prompt)` → Tool wrapper sends HTTP request to MiroFish API → MiroFish runs multi-agent simulation → Response parsed into report text → Saved as `p2-mirofish` EpisodeArtifact → Available to Cross-Validation and Synthesis

### Technical Approach

- MiroFish runs as a Docker container (or local process) alongside the Django app — it is a **sidecar service**, not an embedded library
- Communication is via HTTP API (likely Flask/FastAPI at `localhost:5001`)
- The tool wrapper (`apps/podcast/tools/mirofish_research.py`) handles:
  - Health checks before calling
  - Request construction with simulation parameters
  - Response parsing (extract report text, agent interactions, predictions)
  - Timeout handling (MiroFish simulations may take minutes)
- The research service function wraps this in the standard graceful-skip pattern
- The prompt template should request: "Given this episode topic, simulate a panel of diverse stakeholders reacting to the key claims. Generate predictions, counter-arguments, and identify blind spots the host should address."

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_mirofish_research` must catch `httpx.HTTPError`, `httpx.TimeoutException`, `ConnectionError` and produce `[SKIPPED]` artifacts — never raise into the pipeline
- [ ] Tool wrapper must log warnings (not errors) when MiroFish is unavailable, matching the pattern in `run_perplexity_research`
- [ ] Test that a `[SKIPPED]` artifact is created when MiroFish returns empty/None content

### Empty/Invalid Input Handling
- [ ] Empty episode description → still produces a meaningful prompt (falls back to title-only)
- [ ] MiroFish returns empty response body → `[SKIPPED]` artifact with metadata reason
- [ ] MiroFish returns malformed JSON → `[SKIPPED]` artifact with parse error in metadata

### Error State Rendering
- [ ] `[SKIPPED]` artifacts display correctly in the episode artifact viewer
- [ ] Downstream services (Cross-Validation, Synthesis) handle missing `p2-mirofish` gracefully (they already handle missing sources)

## Rabbit Holes

- **Building a custom MiroFish fork**: Use the upstream repo as-is. Don't customize the MiroFish codebase itself — treat it as a black-box service.
- **Real-time streaming of agent dialogues**: MiroFish may support streaming simulation updates. Ignore this — store the final report only. Streaming is a v2 concern.
- **Zep memory integration**: MiroFish uses Zep for agent memory. Don't try to pre-populate or manage Zep — let MiroFish handle its own memory layer.
- **Fine-tuning simulation parameters per episode genre**: Start with a single default agent configuration. Per-genre tuning is a separate feature.

## Risks

### Risk 1: MiroFish API is undocumented
**Impact:** Integration may require reverse-engineering the API endpoints from the MiroFish frontend code.
**Mitigation:** Read `backend/app/` source code to identify Flask/FastAPI routes. The frontend JS will also reveal API calls. Worst case, wrap the CLI or use the web UI via browser automation — but HTTP API is strongly preferred.

### Risk 2: MiroFish simulation latency
**Impact:** Swarm simulations may take 2-10 minutes, significantly longer than other research tools.
**Mitigation:** Research Gathering already runs tools with independent error handling. A slow MiroFish won't block other tools. Add a generous timeout (10 minutes) and skip if exceeded.

### Risk 3: MiroFish service reliability
**Impact:** Self-hosted service may crash, run out of memory, or hit LLM rate limits.
**Mitigation:** Standard graceful-skip pattern — if MiroFish is down, create a `[SKIPPED]` artifact and continue. No pipeline disruption.

## Race Conditions

No race conditions identified. `run_mirofish_research` is a synchronous function making a blocking HTTP call, same as the other research tools. The EpisodeArtifact `update_or_create` uses Django's built-in row-level locking via `unique_together = [("episode", "title")]`.

## No-Gos (Out of Scope)

- **MiroFish deployment automation**: Standing up MiroFish in production (Docker Compose, cloud hosting) is a separate ops task. This plan covers the integration code only.
- **Custom agent personalities per podcast**: v1 uses a single default simulation config. Custom agent archetypes per show/episode come later.
- **Streaming simulation updates to the UI**: Final report only. No WebSocket/SSE integration.
- **MiroFish as a PydanticAI tool**: The existing 8 PydanticAI tools pattern (`apps/podcast/tools/main.py`) is for agent-callable tools. MiroFish research runs as part of the automated workflow, not as an on-demand agent tool. Adding it to the PydanticAI tool registry is a separate consideration.
- **Replacing existing research tools**: MiroFish supplements, not replaces, the factual research tools.

## Update System

No update system changes required — this is a Cuttlefish application feature, not a Valor system feature. MiroFish runs as an independent service. The only deployment consideration is ensuring MiroFish is running on the host machine, which is an ops concern outside the update skill's scope.

## Agent Integration

No agent integration required — MiroFish research is triggered automatically by the podcast workflow orchestrator during the Research Gathering phase. It is not invoked conversationally via Telegram or MCP tools. The agent does not need to call `run_mirofish_research` directly.

If future work adds on-demand MiroFish queries (e.g., "simulate audience reaction to X"), that would require a new PydanticAI tool registration — but that's out of scope for this plan.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/mirofish-research.md` describing the integration, configuration, and prompt design
- [ ] Update `docs/plans/deep_research_orchestrator.md` to reference MiroFish as a 6th research source

### Inline Documentation
- [ ] Docstrings on `run_mirofish_research()` and the tool wrapper matching the quality of existing research functions
- [ ] Comment explaining why the prompt template differs from other tools (perspective vs. factual)

## Success Criteria

- [ ] `run_mirofish_research(episode_id, prompt)` exists in `apps/podcast/services/research.py` with the same interface as `run_perplexity_research`
- [ ] `apps/podcast/tools/mirofish_research.py` exists with HTTP client for MiroFish API
- [ ] Running the Research Gathering phase produces a `p2-mirofish` EpisodeArtifact
- [ ] When MiroFish is unavailable, a `[SKIPPED]` artifact is created and the pipeline continues
- [ ] The MiroFish prompt template emphasizes perspective simulation (not factual search)
- [ ] Downstream services (Cross-Validation, Synthesis) can consume `p2-mirofish` without changes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (mirofish-tool)**
  - Name: tool-builder
  - Role: Create the MiroFish HTTP client wrapper and research service function
  - Agent Type: builder
  - Resume: true

- **Builder (prompt-design)**
  - Name: prompt-builder
  - Role: Design the perspective-oriented prompt template for MiroFish
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify the full data flow from workflow trigger to artifact creation
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation and update existing docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Discover MiroFish API Contract
- **Task ID**: research-api
- **Depends On**: none
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: true
- Clone MiroFish repo, read `backend/app/` to identify API endpoints
- Document the request/response format for prediction/simulation endpoints
- Identify health check endpoint

### 2. Build MiroFish Tool Wrapper
- **Task ID**: build-tool
- **Depends On**: research-api
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/tools/mirofish_research.py` with HTTP client
- Implement `run_mirofish_simulation(prompt, config) -> (content_text, metadata)`
- Handle timeouts, connection errors, malformed responses
- Add health check function

### 3. Build Research Service Function
- **Task ID**: build-service
- **Depends On**: build-tool
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `run_mirofish_research(episode_id, prompt) -> EpisodeArtifact` to `apps/podcast/services/research.py`
- Follow the exact pattern of `run_perplexity_research` (context assembly, graceful skip, artifact creation)
- Use artifact title `p2-mirofish`, workflow_context `"Research Gathering"`

### 4. Design Perspective Prompt Template
- **Task ID**: build-prompt
- **Depends On**: research-api
- **Assigned To**: prompt-builder
- **Agent Type**: builder
- **Parallel**: true
- Create a prompt template that steers MiroFish toward: stakeholder reactions, predictions, counter-arguments, audience modeling
- Avoid overlap with factual research tools — emphasize "what would people think/do/say" not "what are the facts"
- Store template in `apps/podcast/services/prompts/` or inline in the service function

### 5. Wire into Workflow Orchestrator
- **Task ID**: build-wiring
- **Depends On**: build-service
- **Assigned To**: tool-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `run_mirofish_research` call to the Research Gathering phase in the workflow orchestrator
- Ensure it runs alongside (not blocking) other research tools

### 6. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-wiring, build-prompt
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `p2-mirofish` artifact is created during Research Gathering
- Verify graceful skip when MiroFish is unavailable
- Verify downstream services can read the artifact
- Run linting and format checks

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/mirofish-research.md`
- Update research pipeline documentation
- Add docstrings to new functions

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tool wrapper exists | `python -c "from apps.podcast.tools.mirofish_research import run_mirofish_simulation"` | exit code 0 |
| Service function exists | `python -c "from apps.podcast.services.research import run_mirofish_research"` | exit code 0 |
| Feature docs exist | `test -f docs/features/mirofish-research.md` | exit code 0 |

---

## Open Questions

1. **MiroFish hosting**: Should MiroFish run as a Docker sidecar alongside the Django app on the same host, or as a separate service (e.g., on Render)? This affects the `MIROFISH_API_URL` configuration and latency profile.

2. **LLM provider for MiroFish**: MiroFish defaults to Aliyun DashScope (Qwen models). Should we reconfigure it to use the same Anthropic/OpenAI keys Cuttlefish already has, or keep MiroFish on its own LLM provider? Using shared keys simplifies ops but adds API cost to the same account.

3. **Zep dependency**: MiroFish uses [Zep](https://www.getzep.com/) for agent memory. This is an additional service to deploy and manage. Is the memory layer essential for podcast research (where each simulation is independent), or can we disable it?
