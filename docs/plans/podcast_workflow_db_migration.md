---
status: Planning
type: feature
appetite: Large
owner: Tom
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/60
---

# Podcast Workflow: File-Based → Database-Backed Service Layer

## Problem

The podcast episode production workflow (12 phases, 7 sub-agents, 4 external APIs) runs entirely as a Claude Code CLI session on a developer's local machine. All intermediate artifacts live as files in `apps/podcast/pending-episodes/`. This means:

**Current behavior:**
- An episode can only be produced from a local dev environment with Claude Code installed
- State lives in the CLI conversation context — if the session ends, progress context is lost
- All 20+ intermediate files (research, briefings, reports, plans) are local-only until `publish_episode` imports them at the end
- Long-running API calls (GPT-Researcher: 6-20 min, Gemini: 3-10 min, NotebookLM: 5-15 min) block the CLI session
- No way for a non-developer or remote agent to participate in episode production
- Binary files (audio, cover art) sit on local disk, unreachable by prod

**Desired outcome:**
- A service layer in `apps/podcast/services/` with functions that have clear inputs and outputs
- Every function reads from and writes to the database (Episode, EpisodeArtifact) and S3 (binary files)
- An Anthropic Agent SDK session running on the prod webserver can orchestrate the full workflow
- Human input (manual research, quality gate approvals) can arrive asynchronously via the web UI
- The existing CLI workflow can gradually adopt the same service functions
- Workflow state is persistent and inspectable in the database

## Appetite

**Size:** Large

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 2-3 (service API shape, Agent SDK integration approach, infra decisions)
- Review rounds: 2+ (service layer review, agent integration review)

The service layer is fundamentally plumbing — transforming file I/O into DB I/O. The real complexity is in the Agent SDK orchestration, background task management, and ensuring the 7 sub-agent prompts produce equivalent quality when called as API functions.

## Prerequisites

### Infrastructure modules (separate issues — must be completed first)

These provide abstract interfaces that the service layer calls. Each hides its implementation details (which cloud provider, which task runner, which LLM) behind a clean API.

| Module | Issue | Interface | Purpose |
|--------|-------|-----------|---------|
| File Storage Service | [#61](https://github.com/yudame/cuttlefish/issues/61) | `store_file(key, content) → url` | Binary files (audio, images) without knowing S3 vs R2 vs local |
| Background Task Service | [#62](https://github.com/yudame/cuttlefish/issues/62) | `enqueue(fn, *args) → task_id` | Long-running ops without knowing Celery vs Django-Q2 |
| LLM Service | [#63](https://github.com/yudame/cuttlefish/issues/63) | `call_llm(prompt, context) → str` | AI calls with retry/budget without knowing Anthropic vs OpenAI details |

### Environment (API keys for external services)

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "assert __import__('os').environ.get('ANTHROPIC_API_KEY')"` | Agent SDK + sub-agent LLM calls |
| `OPENAI_API_KEY` | `python -c "assert __import__('os').environ.get('OPENAI_API_KEY')"` | Whisper API transcription |
| `PERPLEXITY_API_KEY` | `python -c "assert __import__('os').environ.get('PERPLEXITY_API_KEY')"` | Phase 2 research |
| Google Cloud credentials | `python -c "assert __import__('os').environ.get('GOOGLE_AI_API_KEY') or __import__('os').environ.get('GOOGLE_APPLICATION_CREDENTIALS')"` | NotebookLM Enterprise API + Gemini |

## Solution

### Key Elements

- **Service Layer** (`apps/podcast/services/`): Pure Python functions for every workflow step. Each takes an `episode_id` and reads/writes DB. No filesystem assumptions.
- **Workflow State Model**: An `EpisodeWorkflow` model tracking current step, status, blocked-on, and history. Replaces conversation context as state store.
- **Infrastructure Abstraction**: Services call `store_file()`, `enqueue()`, and `call_llm()` from the prerequisite modules (#61, #62, #63). The service layer never knows which storage provider, task runner, or LLM provider is behind these calls.
- **Agent SDK Integration**: An orchestrator agent with tools that map 1:1 to service functions. Sub-agent prompts extracted from current `.claude/skills/` files into prompt templates.
- **Text in DB, binaries in storage**: Text artifacts stay in `EpisodeArtifact.content`. Binary files (audio, cover art) go through the File Storage Service, which returns stable URLs.

### Architecture Layers

```
┌─────────────────────────────────────────────────────────┐
│  Orchestration Layer (chooses what to do next)           │
│  ┌──────────────┐  ┌──────────────────────────────────┐ │
│  │ Claude Code   │  │ Anthropic Agent SDK (prod)       │ │
│  │ CLI (dev)     │  │ + tools mapped to services       │ │
│  └──────┬───────┘  └──────────────┬───────────────────┘ │
│         │                         │                      │
│─────────┼─────────────────────────┼──────────────────────│
│         ▼                         ▼                      │
│  Service Layer (does the work)                           │
│  apps/podcast/services/                                  │
│  ┌──────────┐ ┌───────────┐ ┌──────────┐ ┌───────────┐ │
│  │ research │ │ synthesis │ │  audio   │ │ publishing│ │
│  │ services │ │ services  │ │ services │ │ services  │ │
│  └────┬─────┘ └─────┬─────┘ └────┬─────┘ └─────┬─────┘ │
│       │             │            │              │        │
│───────┼─────────────┼────────────┼──────────────┼────────│
│       ▼             ▼            ▼              ▼        │
│  Data Layer                                              │
│  ┌──────────────────────────┐  ┌────────────────────┐   │
│  │ Episode / EpisodeArtifact│  │ File Storage (#61) │   │
│  │ EpisodeWorkflow          │  │ (audio, images)    │   │
│  └──────────────────────────┘  └────────────────────┘   │
│  ┌──────────────────────────┐  ┌────────────────────┐   │
│  │ Background Tasks (#62)   │  │ LLM Service (#63)  │   │
│  │ (long-running ops)       │  │ (all AI calls)     │   │
│  └──────────────────────────┘  └────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

### Flow

**Agent starts episode** → Creates Episode (draft) → EpisodeWorkflow (step=Setup) → **Service: setup** populates p1-brief artifact →
**Service: run_perplexity** → saves p2-perplexity artifact → **Service: question_discovery** (Claude API call) → saves gap analysis artifact →
**Services: run_gpt_researcher + run_gemini** (parallel background tasks) → each saves p2-* artifact →
**Workflow pauses** (optional: waiting for human to paste Claude/Grok research) → human adds artifacts via UI → agent resumes →
**Service: cross_validate** → saves cross-validation artifact → **Service: write_briefing** → saves p3-briefing artifact →
**Quality gate: Wave 1** → agent or human approves → **Service: synthesize_report** → saves report_text on Episode →
**Service: plan_episode** → saves content_plan artifact → **Quality gate: Wave 2** →
**Service: generate_audio** (NotebookLM API, background task) → uploads MP3 to S3, saves URL →
**Service: transcribe_audio** (OpenAI Whisper API) → saves transcript on Episode →
**Service: generate_chapters** (Claude API) → saves chapters on Episode →
**Services: generate_cover_art + write_metadata + generate_companions** (parallel) → save artifacts + S3 →
**Service: publish_episode** → sets published_at, generates feed entry → **Done**

### Technical Approach

#### 1. EpisodeWorkflow Model

New model in `apps/podcast/models/`:

```python
class EpisodeWorkflow(Timestampable):
    episode = models.OneToOneField(Episode, on_delete=CASCADE, related_name="workflow")
    current_step = models.CharField(max_length=100)  # e.g. "Research Gathering"
    status = models.CharField(...)  # running, paused_for_human, paused_at_gate, failed, complete
    blocked_on = models.CharField(max_length=200, blank=True)  # what input is needed
    history = models.JSONField(default=list)  # [{step, status, started_at, completed_at, error}]
    agent_session_id = models.CharField(max_length=100, blank=True)  # Anthropic session tracking
```

This replaces conversation context. The agent reads `current_step` to know where it is, `status` to know whether to proceed or wait, and `history` for context.

#### 2. Service Function Pattern

Every service function follows the same contract:

```python
# apps/podcast/services/research.py

def run_perplexity_research(episode_id: int, prompt: str) -> EpisodeArtifact:
    """Run Perplexity deep research and save results as an artifact.

    Inputs: episode_id, research prompt text
    Outputs: EpisodeArtifact with title="p2-perplexity", content=research markdown
    Side effects: Updates EpisodeWorkflow history
    External APIs: Perplexity sonar-deep-research
    """
    episode = Episode.objects.get(id=episode_id)
    # ... call Perplexity API ...
    artifact, _ = EpisodeArtifact.objects.update_or_create(
        episode=episode,
        title="p2-perplexity",
        defaults={
            "content": result_markdown,
            "description": "Academic foundation research via Perplexity",
            "workflow_context": "Research Gathering",
            "metadata": {"citations": [...], "cost": ...},
        },
    )
    return artifact
```

No filesystem paths anywhere. Input is an episode ID + parameters. Output is a model instance.

#### 3. Sub-Agent → LLM Service Pattern

The 7 sub-agents (digest, question-discovery, cross-validator, briefing-writer, synthesis-writer, episode-planner, metadata-writer) each become a service function that:

1. Reads input artifacts from `EpisodeArtifact.objects.filter(episode_id=...)`
2. Calls `call_llm_with_prompt_template()` from the LLM Service (#63) with artifact content as context
3. Saves the result as a new `EpisodeArtifact`

```python
# apps/podcast/services/synthesis.py
from apps.ai.services.llm import call_llm_with_prompt_template

def synthesize_report(episode_id: int) -> str:
    """Create report by calling LLM with briefing + research artifacts.

    Reads: p3-briefing artifact, all p2-* artifacts
    Writes: Episode.report_text
    """
    episode = Episode.objects.get(id=episode_id)
    briefing = episode.artifacts.get(title="p3-briefing")
    research = episode.artifacts.filter(title__startswith="p2-")

    result = call_llm_with_prompt_template(
        "synthesis_writer",
        context={"briefing": briefing.content, "research": [r.content for r in research]},
    )

    episode.report_text = result
    episode.save(update_fields=["report_text", "modified_at"])
    return episode.report_text
```

#### 4. Binary Files via Storage Service

Audio and cover art go through the File Storage Service (#61). The service layer never knows the underlying provider:

```python
# apps/podcast/services/audio.py
from apps.common.services.storage import store_file

def save_audio(episode_id: int, audio_bytes: bytes, filename: str) -> str:
    """Store audio and update Episode.audio_url."""
    episode = Episode.objects.get(id=episode_id)
    key = f"podcast/{episode.podcast.slug}/{episode.slug}/{filename}"
    url = store_file(key, audio_bytes, content_type="audio/mpeg")
    episode.audio_url = url
    episode.audio_file_size_bytes = len(audio_bytes)
    episode.save(update_fields=["audio_url", "audio_file_size_bytes", "modified_at"])
    return url
```

#### 5. Background Tasks via Task Service

Long-running operations submitted through the Background Task Service (#62):

```python
# apps/podcast/services/research.py
from apps.common.services.tasks import enqueue

def run_perplexity_research_async(episode_id: int, prompt: str) -> str:
    """Submit Perplexity research as a background task. Returns task_id."""
    return enqueue(run_perplexity_research, episode_id, prompt)
```

The service functions themselves are synchronous and testable. The `enqueue()` wrapper is only used by the orchestrator when it needs non-blocking execution.

#### 6. Agent SDK Integration

The Anthropic Agent SDK agent gets tools that map to service functions:

```python
# apps/podcast/agent/tools.py — exposed to the Agent SDK

tools = [
    {"name": "setup_episode", "fn": services.setup.setup_episode},
    {"name": "run_perplexity", "fn": services.research.run_perplexity_research},
    {"name": "run_gpt_researcher", "fn": services.research.run_gpt_researcher},
    {"name": "run_gemini", "fn": services.research.run_gemini_research},
    {"name": "discover_questions", "fn": services.analysis.discover_questions},
    {"name": "cross_validate", "fn": services.analysis.cross_validate},
    {"name": "write_briefing", "fn": services.analysis.write_briefing},
    {"name": "synthesize_report", "fn": services.synthesis.synthesize_report},
    {"name": "plan_episode", "fn": services.synthesis.plan_episode},
    {"name": "generate_audio", "fn": services.audio.generate_audio},
    {"name": "transcribe_audio", "fn": services.audio.transcribe_audio},
    {"name": "generate_chapters", "fn": services.audio.generate_chapters},
    {"name": "generate_cover_art", "fn": services.publishing.generate_cover_art},
    {"name": "write_metadata", "fn": services.publishing.write_metadata},
    {"name": "generate_companions", "fn": services.publishing.generate_companions},
    {"name": "publish_episode", "fn": services.publishing.publish_episode},
    {"name": "check_workflow_status", "fn": services.workflow.get_status},
    {"name": "check_quality_gate", "fn": services.workflow.check_quality_gate},
]
```

The agent's system prompt describes the workflow phases and quality gates. The agent reads `EpisodeWorkflow.current_step` and decides which tool to call next.

### Service Module Layout

```
apps/podcast/services/
├── __init__.py
├── workflow.py          # EpisodeWorkflow state management
├── setup.py             # Phase 1: Episode setup
├── research.py          # Phases 2-4: Perplexity, GPT-Researcher, Gemini
├── analysis.py          # Phases 3, 5-6: Question discovery, cross-validation, briefing
├── synthesis.py         # Phases 7-8: Report synthesis, episode planning
├── audio.py             # Phases 9-10: NotebookLM generation, Whisper transcription, chapters
├── publishing.py        # Phase 11: Cover art, metadata, companions, feed
└── prompts/             # Extracted prompt templates for sub-agent API calls
    ├── question_discovery.md
    ├── cross_validator.md
    ├── briefing_writer.md
    ├── synthesis_writer.md
    ├── episode_planner.md
    └── metadata_writer.md
```

### Phase-to-Service Mapping

| Workflow Step | Service Function | Inputs (from DB) | Outputs (to DB) | External API | Duration |
|---|---|---|---|---|---|
| Setup | `setup.setup_episode()` | Episode.title, .description | p1-brief artifact, workflow record | None | <1s |
| Research Gathering (Perplexity) | `research.run_perplexity()` | p1-brief artifact content | p2-perplexity artifact | Perplexity | 30-120s |
| Question Discovery | `analysis.discover_questions()` | p2-perplexity artifact | question-discovery artifact | Anthropic | ~30s |
| Research Gathering (GPT-Researcher) | `research.run_gpt_researcher()` | question-discovery artifact | p2-chatgpt artifact | OpenAI + Tavily | 6-20 min |
| Research Gathering (Gemini) | `research.run_gemini()` | question-discovery artifact | p2-gemini artifact | Google Gemini | 3-10 min |
| Cross-Validation | `analysis.cross_validate()` | All p2-* artifacts | cross-validation artifact | Anthropic | ~60s |
| Master Briefing | `analysis.write_briefing()` | cross-validation + p2-* artifacts | p3-briefing artifact, sources artifact | Anthropic | ~60s |
| Synthesis | `synthesis.synthesize_report()` | p3-briefing + p2-* artifacts | Episode.report_text | Anthropic | ~60s |
| Episode Planning | `synthesis.plan_episode()` | report_text, p3-briefing, sources | content_plan artifact | Anthropic | ~60s |
| Audio Generation | `audio.generate_audio()` | 5 source artifacts/fields | Episode.audio_url (S3) | NotebookLM Enterprise | 5-15 min |
| Transcription | `audio.transcribe_audio()` | Episode.audio_url (S3) | Episode.transcript | OpenAI Whisper | 30-120s |
| Chapter Generation | `audio.generate_chapters()` | Episode.transcript | Episode.chapters | Anthropic | ~30s |
| Cover Art | `publishing.generate_cover_art()` | report_text, Episode.title | Episode.cover_image_url (S3) | OpenRouter/Gemini | 30-60s |
| Metadata | `publishing.write_metadata()` | report, transcript, chapters, sources | metadata artifact | Anthropic | ~30s |
| Companion Resources | `publishing.generate_companions()` | report_text, sources | companion-* artifacts | Anthropic | ~30s |
| Publish | `publishing.publish_episode()` | All fields populated | Episode.published_at set | None | <1s |

### What Changes vs. Stays

| Current | After |
|---------|-------|
| `apps/podcast/tools/*.py` (CLI scripts) | `apps/podcast/services/*.py` (importable functions). Tools stay for CLI backward compat but call services internally. |
| File I/O in `pending-episodes/` | DB reads/writes via `Episode` + `EpisodeArtifact` |
| Local Whisper transcription | OpenAI Whisper API |
| Local ffmpeg chapter embedding | Claude API generates chapter JSON; no embedding needed (chapters served via Podcasting 2.0 JSON in feed) |
| `.claude/skills/*.md` sub-agent prompts | `apps/podcast/services/prompts/*.md` templates called via Anthropic API |
| Conversation context as state | `EpisodeWorkflow` model |
| `start_episode` management command | `services.setup.setup_episode()` |
| `publish_episode` management command | `services.publishing.publish_episode()` |

## Rabbit Holes

- **Don't build the web UI now** — The data layer supports human-in-the-loop (EpisodeWorkflow.status="paused_for_human"), but building the actual approval screens, file upload forms, and notification system is a separate plan. Artifacts can be added via Django admin for now.
- **Don't build a full state machine library** — The EpisodeWorkflow model with a history JSONField is enough. Don't import Temporal, Django-FSM, or Dramatiq. The workflow is linear with optional pauses — a simple model field is sufficient.
- **Don't refactor the quality scorecard** — The 10-dimension quality scoring is a nice-to-have post-production tool. It doesn't need to be in the service layer for the workflow to function.
- **Don't try to run GPT-Researcher on the server yet** — It has heavy dependencies (Playwright, headless browser) and uses significant memory. For now, wrap it as a service function that works locally. Replace with a simpler alternative (OpenAI with web search, or a second Perplexity call) for server execution. This decision can be revisited.
- **Don't over-engineer the prompt template system** — Simple markdown files loaded with `Path.read_text()` are fine. Don't build a Jinja/template engine for prompts.
- **Don't migrate git-based publishing** — The feed is already served dynamically by Django. Phase 12 (git commit/push) becomes unnecessary once audio is on S3. Just skip it.

## Risks

### Risk 1: Sub-agent prompt quality degradation
**Impact:** The 7 sub-agents currently run as Claude Code Task agents with rich conversation context (the full `.claude/skills/*.md` file as system prompt, plus the entire prior conversation). Extracting prompts and calling the API directly may produce lower quality output.
**Mitigation:** Extract prompts carefully, preserving all the Wave 1/2 quality requirements. Test each extracted prompt against the same inputs the CLI workflow uses. Compare outputs. Iterate on prompts before shipping.

### Risk 2: Token budget for large contexts
**Impact:** Some sub-agent calls process 50-100KB of input (e.g., synthesis-writer reads all p2-* research files + the briefing). This approaches context limits on some models.
**Mitigation:** Use the digest pattern already in the workflow — compress each p2-* file into a 3-5KB digest before feeding to synthesis. This is what the current workflow does. The service layer preserves this.

### Risk 3: Background task reliability
**Impact:** Long-running tasks (GPT-Researcher: 20 min, NotebookLM: 15 min) could fail silently if the worker crashes or times out.
**Mitigation:** EpisodeWorkflow tracks task status. Service functions update workflow history on start/success/fail. The Background Task Service (#62) owns retry logic. The agent checks workflow status before proceeding.

### Risk 4: File storage cost
**Impact:** Audio files are 20-80MB each. Hosting, bandwidth, and storage costs add up.
**Mitigation:** The File Storage Service (#61) decides the provider. This plan doesn't care — it just calls `store_file()`. Provider choice (R2, S3, etc.) is an infra decision made in that issue.

### Risk 5: Anthropic Agent SDK maturity
**Impact:** The Agent SDK is newer and may have limitations or breaking changes.
**Mitigation:** The service layer is SDK-agnostic — it's plain Python functions. If the Agent SDK doesn't work well, the tools can be exposed via any other mechanism (PydanticAI, custom loop, Django management command). The service layer is the stable foundation.

## No-Gos (Out of Scope)

- Web UI for human-in-the-loop (quality gates, manual research upload, approval screens) — separate plan
- Notification system (email/Telegram when human input needed) — separate plan
- Running GPT-Researcher on the server — use as local-only or replace for now
- Migrating episode audio from research.yuda.me to S3 (historical episodes) — separate backfill
- Git-based publishing (Phase 12) — already obsolete with dynamic feed
- Cover art image processing with Pillow/fonts on server — use API-only generation or upload pre-made

## Update System

No update system changes required — this is internal to the cuttlefish Django app. Deployment to Render picks up the new services automatically. Infrastructure environment variables (S3 credentials, Redis URL, etc.) are managed by the prerequisite issues (#61, #62).

## Agent Integration

This plan IS the agent integration. The service layer is specifically designed so that:
- Each service function becomes an Agent SDK tool
- The agent orchestrator reads EpisodeWorkflow state to decide next actions
- Tools are exposed in `apps/podcast/agent/tools.py`
- The agent's system prompt lives in `apps/podcast/agent/system_prompt.md`

No MCP server changes needed — the Agent SDK runs server-side, not through MCP.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/podcast-services.md` describing the service layer API
- [ ] Update CLAUDE.md podcast section to reference services

### Inline Documentation
- [ ] Docstrings on every service function (inputs, outputs, side effects, external APIs)
- [ ] Module-level docstrings in each service file

## Success Criteria

- [ ] `EpisodeWorkflow` model created with migration
- [ ] All 16 service functions implemented and importable
- [ ] Each service function reads from DB and writes to DB (no filesystem paths)
- [ ] Services use `store_file()` (#61), `enqueue()` (#62), `call_llm()` (#63) — never direct provider calls
- [ ] All 6 sub-agent prompt templates extracted and tested
- [ ] Agent tool definitions exist mapping to service functions
- [ ] `start_episode` management command updated to call `services.setup.setup_episode()`
- [ ] `publish_episode` management command updated to call `services.publishing.publish_episode()`
- [ ] `workflow_progress.py` reads from `EpisodeWorkflow` model
- [ ] At least one end-to-end episode produced using the service layer (can be via CLI calling services with sync backends)
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create EpisodeWorkflow model, migrations, and workflow state management
  - Agent Type: database-architect
  - Resume: true

- **Builder (services-research)**
  - Name: research-services-builder
  - Role: Implement setup, research, and analysis service functions
  - Agent Type: builder
  - Resume: true

- **Builder (services-synthesis)**
  - Name: synthesis-services-builder
  - Role: Implement synthesis, audio, and publishing service functions
  - Agent Type: builder
  - Resume: true

- **Builder (prompts)**
  - Name: prompt-extractor
  - Role: Extract sub-agent prompts from .claude/skills/ into service prompt templates
  - Agent Type: builder
  - Resume: true

- **Builder (agent-integration)**
  - Name: agent-builder
  - Role: Create Agent SDK tool definitions and orchestrator system prompt
  - Agent Type: agent-architect
  - Resume: true

- **Validator (services)**
  - Name: services-validator
  - Role: Verify all services work end-to-end with DB I/O
  - Agent Type: validator
  - Resume: true

- **Builder (docs)**
  - Name: docs-builder
  - Role: Update documentation, CLAUDE.md, management commands
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create EpisodeWorkflow model
- **Task ID**: build-workflow-model
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: database-architect
- **Parallel**: true
- Create `apps/podcast/models/episode_workflow.py` with fields: episode (OneToOne), current_step, status, blocked_on, history (JSONField), agent_session_id
- Status choices: pending, running, paused_for_human, paused_at_gate, failed, complete
- Add to `apps/podcast/models/__init__.py`
- Create migration
- Add to admin

### 2. Extract sub-agent prompt templates
- **Task ID**: build-prompts
- **Depends On**: none
- **Assigned To**: prompt-extractor
- **Agent Type**: builder
- **Parallel**: true (with build-workflow-model)
- Read each of the 7 sub-agent skill files in `.claude/skills/`
- Extract the system prompt / instructions portion into `apps/podcast/services/prompts/`
- Create: question_discovery.md, cross_validator.md, briefing_writer.md, synthesis_writer.md, episode_planner.md, metadata_writer.md, research_digest.md
- Each template should include input format instructions and output format requirements
- Preserve all Wave 1/2 quality gate requirements in the relevant prompts

### 3. Implement research services
- **Task ID**: build-services-research
- **Depends On**: build-workflow-model
- **Assigned To**: research-services-builder
- **Agent Type**: builder
- **Parallel**: true (with build-services-synthesis, build-prompts)
- Create `apps/podcast/services/setup.py`: setup_episode(episode_id) → creates p1-brief artifact + EpisodeWorkflow
- Create `apps/podcast/services/research.py`:
  - run_perplexity_research(episode_id, prompt) → p2-perplexity artifact
  - run_gpt_researcher(episode_id, prompt) → p2-chatgpt artifact
  - run_gemini_research(episode_id, prompt) → p2-gemini artifact
  - add_manual_research(episode_id, title, content) → p2-{title} artifact (for human-pasted research)
- Create `apps/podcast/services/analysis.py`:
  - discover_questions(episode_id) → question-discovery artifact
  - create_research_digest(episode_id, artifact_title) → digest artifact
  - cross_validate(episode_id) → cross-validation artifact
  - write_briefing(episode_id) → p3-briefing artifact + sources artifact

### 4. Implement synthesis + audio + publishing services
- **Task ID**: build-services-synthesis
- **Depends On**: build-workflow-model
- **Assigned To**: synthesis-services-builder
- **Agent Type**: builder
- **Parallel**: true (with build-services-research)
- Create `apps/podcast/services/synthesis.py`:
  - synthesize_report(episode_id) → Episode.report_text
  - plan_episode(episode_id) → content_plan artifact
- Create `apps/podcast/services/audio.py`:
  - generate_audio(episode_id) → uploads MP3 to S3, sets Episode.audio_url + file_size + duration
  - transcribe_audio(episode_id) → Episode.transcript (via OpenAI Whisper API)
  - generate_chapters(episode_id) → Episode.chapters (via Claude API analyzing transcript)
- Create `apps/podcast/services/publishing.py`:
  - generate_cover_art(episode_id) → uploads to S3, sets Episode.cover_image_url
  - write_metadata(episode_id) → metadata artifact
  - generate_companions(episode_id) → companion-summary, companion-checklist, companion-frameworks artifacts
  - publish_episode(episode_id) → sets Episode.status="complete", published_at=now

### 5. Create workflow state management service
- **Task ID**: build-workflow-service
- **Depends On**: build-workflow-model
- **Assigned To**: model-builder
- **Agent Type**: database-architect
- **Parallel**: true (with build-services-*)
- Create `apps/podcast/services/workflow.py`:
  - get_status(episode_id) → current step, status, what's complete, what's next
  - advance_step(episode_id, completed_step) → updates current_step, appends to history
  - pause_for_human(episode_id, reason) → sets status=paused_for_human
  - resume_workflow(episode_id) → sets status=running
  - check_quality_gate(episode_id, gate_name) → returns pass/fail with details
  - fail_step(episode_id, step, error) → sets status=failed, records error
- Update `apps/podcast/services/workflow_progress.py` to read from EpisodeWorkflow model

### 6. Validate services
- **Task ID**: validate-services
- **Depends On**: build-services-research, build-services-synthesis, build-workflow-service, build-prompts
- **Assigned To**: services-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every service function is importable and has correct signature
- Verify every service reads from DB (Episode/EpisodeArtifact) not filesystem
- Verify every service writes to DB or S3, not filesystem
- Verify prompt templates exist for all 7 sub-agent services
- Run any existing tests, ensure no regressions

### 7. Create Agent SDK tool definitions
- **Task ID**: build-agent
- **Depends On**: validate-services
- **Assigned To**: agent-builder
- **Agent Type**: agent-architect
- **Parallel**: true (with build-command-bridge)
- Create `apps/podcast/agent/` package
- Create `apps/podcast/agent/tools.py` mapping service functions to Agent SDK tool schemas
- Create `apps/podcast/agent/system_prompt.md` describing the workflow, quality gates, and decision logic
- Create `apps/podcast/agent/orchestrator.py` that initializes an Anthropic Agent SDK session with tools

### 8. Update management commands to use services
- **Task ID**: build-command-bridge
- **Depends On**: validate-services
- **Assigned To**: research-services-builder
- **Agent Type**: builder
- **Parallel**: true (with build-agent)
- Update `start_episode` to call `services.setup.setup_episode()`
- Update `publish_episode` to call `services.publishing.publish_episode()`
- Keep backward compatibility: commands still accept directory paths but services work from DB
- Update `_episode_import_utils.py` to work with service layer where possible

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: build-agent, build-command-bridge
- **Assigned To**: docs-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/podcast-services.md` describing the service API
- Update CLAUDE.md podcast section with service layer info
- Update `docs/reference/podcast-workflow-diagram.md` to show DB-backed flow

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: services-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria
- Generate final report

## Validation Commands

- `python -c "from apps.podcast.models import EpisodeWorkflow"` — Model importable
- `python -c "from apps.podcast.services import setup, research, analysis, synthesis, audio, publishing, workflow"` — All services importable
- `ls apps/podcast/services/prompts/*.md | wc -l` — At least 6 prompt templates exist
- `python -c "from apps.podcast.agent.tools import tools; assert len(tools) >= 15"` — Agent tools defined
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` — Tests pass

---

## Resolved Questions

1. ~~Background task runner choice~~ → Decided by #62 (Background Task Service). This plan just calls `enqueue()`.
2. ~~S3 provider~~ → Decided by #61 (File Storage Service). This plan just calls `store_file()`.
3. ~~Prompt template management~~ → Files in `apps/podcast/services/prompts/*.md`. Version-controlled, simple.
4. ~~Existing tools/ scripts~~ → Keep as thin CLI wrappers that call services internally. Gradual migration.

## Open Questions

1. **GPT-Researcher replacement for server**: On prod, GPT-Researcher can't run (needs Playwright + lots of RAM). Options: (a) Use OpenAI's built-in web search tool in the responses API, (b) Run a second Perplexity call with an industry-focused prompt, (c) Skip it entirely (3 research sources instead of 4). Preference?
