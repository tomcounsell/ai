# Podcast Service Layer API

> **Business context:** See [Podcasting](~/work-vault/Cuttlefish/Podcasting.md) in the work vault for product overview, workflow summary, and key integrations.

The podcast service layer (`apps/podcast/services/`) provides a database-backed API for the 12-phase episode production workflow. Every function takes an `episode_id`, reads state from the database, performs its operation (often delegating to a Named AI Tool or external API), and writes results back to `Episode`, `EpisodeArtifact`, or `EpisodeWorkflow` records.

## Architecture

```
Management Commands / Task Pipeline
           |
           v
   Service Layer (this module)
      |              |
      v              v
Named AI Tools    External Tools
(PydanticAI)      (CLI wrappers)
      |              |
      v              v
   Database (Episode, EpisodeArtifact, EpisodeWorkflow)
```

**Service layer responsibilities:**
- Load episode data from the database
- Delegate to Named AI Tools or external tool wrappers
- Format AI output into readable markdown
- Persist results via `update_or_create` (idempotent)
- Track workflow state transitions

**Services do NOT:**
- Contain AI prompts or model logic (that lives in Named AI Tools and `services/prompts/`)
- Manage filesystem paths or working directories
- Handle HTTP requests or responses

## Models

### Podcast
Core podcast record. Key fields for navigation and access control:

| Field | Type | Purpose |
|-------|------|---------|
| `owner` | FK to User (nullable) | Enables private podcast access for the owning user |
| `is_public` | BooleanField | Controls visibility in list views and feed access |
| `spotify_url` | URLField (blank) | External Spotify link, displayed on detail pages |
| `apple_podcasts_url` | URLField (blank) | External Apple Podcasts link, displayed on detail pages |

**View access control:** `PodcastListView` shows `is_public=True` podcasts plus the authenticated user's private podcasts. Detail views (`PodcastDetailView`, `EpisodeDetailView`, etc.) use `_get_accessible_podcast()` which returns 404 for private podcasts unless `request.user == podcast.owner`. `PodcastFeedView` allows authenticated owners to access private feeds without `?token=`.

### Episode
Core episode record. Services read and write these fields:

| Field | Written By |
|-------|-----------|
| `status` | `setup_episode`, `publish_episode` |
| `description` | `write_episode_metadata` |
| `report_text` | `synthesize_report` |
| `sources_text` | `synthesize_report` |
| `show_notes` | `write_episode_metadata` |
| `audio_url` | `local_audio_worker` (via API callback) |
| `audio_file_size_bytes` | `local_audio_worker` (via API callback) |
| `transcript` | `transcribe_audio` |
| `chapters` | `generate_episode_chapters` |
| `cover_image_url` | `generate_cover_art` (stub) |
| `companion_resources` | `generate_companions` |
| `published_at` | `publish_episode` |

### EpisodeArtifact
Versioned content artifacts produced during the workflow. Each has `episode` (FK), `title` (unique per episode), `content`, `description`, `workflow_context`, and `metadata` (JSONField).

| Artifact Title | Created By | Phase |
|---------------|-----------|-------|
| `p1-brief` | `setup_episode` | 1 |
| `p2-perplexity` | `run_perplexity_research` | 2 |
| `p2-chatgpt` | `run_gpt_researcher` | 4 |
| `p2-gemini` | `run_gemini_research` | 4 |
| `p2-together` | `run_together_research` | 4 |
| `p2-claude` | `run_claude_research` | 4 |
| `p2-grok` | `run_grok_research` | 4 |
| `p2-mirofish` | `run_mirofish_research` | 4 |
| `p2-{source}` | `add_manual_research` | 4 |
| `question-discovery` | `discover_questions` | 3 |
| `digest-{source}` | `create_research_digest` | 4-5 |
| `cross-validation` | `cross_validate` | 5 |
| `p3-briefing` | `write_briefing` | 6 |
| `content_plan` | `plan_episode_content` | 8 |
| `metadata` | `write_episode_metadata` | 11 |
| `companion-summary` | `generate_companions` | 11 |
| `companion-checklist` | `generate_companions` | 11 |
| `companion-frameworks` | `generate_companions` | 11 |

### EpisodeWorkflow
One-to-one with Episode. Tracks production state.

| Field | Type | Purpose |
|-------|------|---------|
| `current_step` | CharField | Name of the active workflow step |
| `status` | CharField | `pending`, `running`, `paused_for_human`, `paused_at_gate`, `failed`, `complete` |
| `blocked_on` | CharField | Description of what's blocking progress |
| `history` | JSONField | List of `{step, status, started_at, completed_at, error}` dicts |
| `agent_session_id` | CharField | Orchestrator session metadata |

## Service Modules

### `setup.py` -- Episode Initialization

```python
setup_episode(episode_id: int) -> EpisodeArtifact
```

Creates the `p1-brief` artifact from `Episode.description` and initializes an `EpisodeWorkflow` record. Transitions draft episodes to `in_progress` status.

---

### `research.py` -- External Research Tools

```python
run_perplexity_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls Perplexity Deep Research (sonar-deep-research model). Saves result as `p2-perplexity` artifact with extracted metadata (citations, URLs).

```python
run_gpt_researcher(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls GPT-Researcher multi-agent system via `asyncio.run`. Saves result as `p2-chatgpt` artifact.

```python
run_gemini_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls Gemini Deep Research via the Interactions API. Saves result as `p2-gemini` artifact. Catches `GeminiQuotaError` (HTTP 429) separately from other failures, creating skip artifacts with specific `reason` metadata (`quota_exceeded` vs `api_error_or_empty`). Gracefully degrades when `GEMINI_API_KEY` is missing or quota is exceeded.

```python
run_together_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls Open Deep Research (LangGraph multi-hop, auto-detects LLM provider). Saves result as `p2-together` artifact.

```python
run_claude_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls the multi-agent deep research orchestrator (`claude_deep_research.deep_research`). Plans subtasks, runs Sonnet researchers, synthesizes findings. Saves result as `p2-claude` artifact with structured metadata (sources, key findings, confidence assessment). Gracefully degrades on any failure (validation errors, API issues) by creating a skip artifact.

```python
run_grok_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls xAI Grok API (grok-3 model) via the OpenAI-compatible chat completions endpoint. Specializes in real-time research with current events, practitioner perspectives, and social/regional insights. Saves result as `p2-grok` artifact with token usage metadata. Gracefully degrades when `GROK_API_KEY` is missing, creating a skip artifact.

```python
run_mirofish_research(episode_id: int, prompt: str) -> EpisodeArtifact
```
Calls the MiroFish swarm intelligence service via HTTP API for perspective-oriented simulation. Produces stakeholder reaction modeling, evidence-based predictions, counter-arguments, and audience reception analysis. Saves result as `p2-mirofish` artifact. Gracefully degrades when `MIROFISH_API_URL` is not set or the service is unreachable, creating a skip artifact. Requires the MiroFish sidecar service to be running.

```python
add_file_research(episode_id: int, title: str, file_path: str | Path) -> EpisodeArtifact
```
Parses a document file (PDF, DOCX, ODT) using `parse_document()` and stores the extracted text as a `p2-{title}` artifact. If parsing fails, creates an artifact with empty content and `parse_failed: true` metadata so the pipeline can continue gracefully. Delegates to `add_manual_research()` for storage after successful extraction.

```python
add_manual_research(episode_id: int, title: str, content: str) -> EpisodeArtifact
```
Stores human-pasted research as `p2-{title}` artifact. Used for expert interviews, supplementary sources, or any manual research.

All research functions use `_get_episode_context()` to build prompts from the best available context (prefers `question-discovery` artifact, falls back to `p1-brief`, then `Episode.description`).

---

### Document Parser Utility (`apps/common/utilities/document_parser.py`)

```python
parse_document(path: Path | str) -> str
```
Extracts plain text from a document file using kreuzberg's `extract_file_sync()` API. Supports PDF, DOCX, ODT, TXT, and MD formats. Kreuzberg auto-detects the file format from the extension and delegates to the appropriate extractor (pdfium for PDFs, python-docx for DOCX, etc.). Plain text formats (.txt, .md) are read directly without kreuzberg.

Raises `FileNotFoundError` for missing files and `ValueError` for unsupported extensions. Returns an empty string if kreuzberg extraction fails (e.g., corrupt files), logging the error for diagnostics.

The backfill command (`_episode_import_utils.py`) uses `parse_document()` to extract text from PDF files during episode import, storing the extracted text in the `content` field alongside the URL instead of creating empty-content URL-only artifacts.

---

### `analysis.py` -- Research Analysis

```python
discover_questions(episode_id: int) -> EpisodeArtifact
```
Analyzes the first available `p2-*` artifact to identify knowledge gaps, contradictions, and follow-up questions. Delegates to the `discover_questions` Named AI Tool. Saves result as `question-discovery` artifact.

```python
create_research_digest(episode_id: int, artifact_title: str) -> EpisodeArtifact
```
Digests a single `p2-*` artifact into a structured summary. Delegates to the `digest_research` Named AI Tool. Saves result as `digest-{source}` artifact (e.g., `digest-perplexity` from `p2-perplexity`).

```python
cross_validate(episode_id: int) -> EpisodeArtifact
```
Cross-validates findings across all `p2-*` artifacts. Identifies verified claims (2+ sources), single-source claims, and conflicting claims. Delegates to the `cross_validate` Named AI Tool. Saves result as `cross-validation` artifact.

```python
write_briefing(episode_id: int) -> EpisodeArtifact
```
Creates the master research briefing from `cross-validation` and `digest-*` (or `p2-*`) artifacts. Delegates to the `write_briefing` Named AI Tool. Saves result as `p3-briefing` artifact with verified findings, story bank, counterpoints, and source inventory.

```python
craft_research_prompt(episode_id: int, research_type: str) -> EpisodeArtifact
```
Reads the `p1-brief` artifact, delegates to the `craft_research_prompt` Named AI Tool to generate a topic-specific research prompt, and saves the result as a `prompt-{research_type}` artifact.

```python
craft_targeted_research_prompts(episode_id: int) -> dict[str, EpisodeArtifact]
```
Reads `p1-brief` and `question-discovery` artifacts, delegates to the `craft_research_prompt` Named AI Tool for batch prompt generation, saves prompts as `prompt-gpt`, `prompt-gemini`, `prompt-together`, `prompt-claude`, `prompt-grok`, and `prompt-mirofish` artifacts, and creates empty `p2-chatgpt`, `p2-gemini`, `p2-together`, `p2-claude`, `p2-grok`, and `p2-mirofish` placeholder artifacts for fan-in.

---

### `synthesis.py` -- Report and Planning

```python
synthesize_report(episode_id: int) -> str
```
Reads `p3-briefing` and all `p2-*` artifacts, delegates to the `write_synthesis` Named AI Tool, and saves the narrative report (5,000-8,000 words) to `Episode.report_text`. Also populates `Episode.sources_text` with cited sources. Returns the report text.

```python
plan_episode_content(episode_id: int) -> EpisodeArtifact
```
Reads `Episode.report_text` and `p3-briefing`, delegates to the `plan_episode` Named AI Tool, and saves the structured episode plan as `content_plan` artifact. The plan includes structure map, counterpoint moments, toolkit selections, and NotebookLM guidance.

---

### `audio.py` -- Audio Pipeline

Audio generation is handled by the `local_audio_worker` management command using `notebooklm-py`. See `apps/podcast/management/commands/local_audio_worker.py`.

```python
transcribe_audio(episode_id: int) -> str
```
Downloads audio from `Episode.audio_url`, sends to OpenAI Whisper API, and saves the transcript to `Episode.transcript`. Returns the transcript text.

```python
generate_episode_chapters(episode_id: int) -> str
```
Reads `Episode.transcript`, delegates to the `generate_chapters` Named AI Tool, and saves chapter markers as JSON to `Episode.chapters`. Returns the chapters JSON string.

---

### `publishing.py` -- Publishing Assets

```python
generate_cover_art(episode_id: int) -> str | None
```
Generates AI cover art via OpenRouter (Gemini), applies Yudame Research branding overlay, and uploads to Supabase storage. Uses `generate_cover_image()` from `tools/generate_cover.py` for image generation and `apply_branding()` from `tools/add_logo_watermark.py` for logo/text overlay. Saves the URL to `Episode.cover_image_url` and creates a `cover-art` artifact with generation metadata. Gracefully degrades when `OPENROUTER_API_KEY` is missing (creates a `[SKIPPED]` artifact and returns `None`).

```python
write_episode_metadata(episode_id: int) -> EpisodeArtifact
```
Reads `Episode.report_text`, `transcript`, and `chapters`. Delegates to the `write_metadata` Named AI Tool. Saves structured metadata as `metadata` artifact and updates `Episode.description` and `Episode.show_notes`.

```python
generate_companions(episode_id: int) -> list[EpisodeArtifact]
```
Reads `Episode.report_text`, extracts content elements using functions from `tools/generate_companion_resources.py`, and generates three companion documents: `companion-summary`, `companion-checklist`, `companion-frameworks`. Also saves to `Episode.companion_resources` JSONField.

```python
publish_episode(episode_id: int) -> Episode
```
Sets `Episode.status` to `"complete"` and calls `Episode.publish()` (Publishable mixin) to set `published_at`. Returns the updated Episode.

---

### `workflow.py` -- Workflow State Management

The 12 workflow steps:
```
Setup, Perplexity Research, Question Discovery, Targeted Research,
Cross-Validation, Master Briefing, Synthesis, Episode Planning,
Audio Generation, Audio Processing, Publishing Assets, Publish
```

```python
get_status(episode_id: int) -> dict
```
Returns `{current_step, status, blocked_on, completed_steps, next_step, history}`. Returns `status='not_started'` if no EpisodeWorkflow exists.

```python
advance_step(episode_id: int, completed_step: str) -> EpisodeWorkflow
```
Marks `completed_step` as done (with timestamp) and moves `current_step` to the next step. Sets `status='complete'` when the final step finishes.

```python
pause_for_human(episode_id: int, reason: str) -> EpisodeWorkflow
```
Sets `status='paused_for_human'` and records the blocking reason in `blocked_on`.

```python
resume_workflow(episode_id: int) -> EpisodeWorkflow
```
Clears `blocked_on` and sets `status='running'`.

```python
check_quality_gate(episode_id: int, gate_name: str) -> dict
```
Returns `{passed: bool, details: str}`. Supported gates:
- `wave_1` -- after Master Briefing: checks `p3-briefing` artifact exists with 200+ words
- `wave_2` -- after Episode Planning: checks `content_plan` artifact exists

```python
fail_step(episode_id: int, step: str, error: str) -> EpisodeWorkflow
```
Sets `status='failed'` and records the error in the step's history entry. Multiple calls for the same step accumulate errors (separated by `\n---\n`) rather than overwriting.

```python
fail_research_source(episode_id: int, artifact_title: str, error: str) -> EpisodeArtifact
```
Records a per-source research failure by writing `[FAILED: error]` to the artifact's content and storing error details in `metadata`. Does NOT change workflow status — the fan-in signal evaluates aggregate state. Used by parallel research sub-tasks instead of `fail_step()`.

---

### `workflow_progress.py` -- Progress Computation

```python
compute_workflow_progress(episode: Episode, artifact_titles: list[str], artifact_contents: dict[str, str] | None = None) -> list[Phase]
```
Maps episode fields and artifact titles to 12 `Phase` dataclass objects. Each Phase has a list of `SubStep` items. For Phase 4, `SubStep` includes `status` (pending/running/complete/failed/skipped) and `error` fields derived from artifact content via `_resolve_substep_status()`. Used for dashboard display and progress tracking.

```python
get_workflow_summary(episode_id: int) -> dict
```
Combines `compute_workflow_progress` with `EpisodeWorkflow` state into a single dict with `phases` (list of phase dicts), `overall_progress` (0.0-1.0), and `workflow` (persisted state or None).

## Prompt Templates

Nine prompt templates live in `apps/podcast/services/prompts/` and are used by the Named AI Tools:

| Template | Used By |
|----------|---------|
| `craft_research_prompt.md` | `craft_research_prompt.py` |
| `cross_validate.md` | `cross_validate.py` |
| `discover_questions.md` | `discover_questions.py` |
| `generate_chapters.md` | `generate_chapters.py` |
| `plan_episode.md` | `plan_episode.py` |
| `research_digest.md` | `digest_research.py` |
| `write_briefing.md` | `write_briefing.py` |
| `write_metadata.md` | `write_metadata.py` |
| `write_synthesis.md` | `write_synthesis.py` |

## Task Pipeline

The podcast production pipeline (`apps/podcast/tasks.py`) uses Django 6.0's `@task` framework to execute each workflow phase as an independent background task. Each task calls the service layer, advances the workflow, and enqueues its successor(s).

### Entry Point

```python
from apps.podcast.tasks import produce_episode

result = produce_episode.enqueue(episode_id=42)
```

### Task Functions

| Task | Service Call | Next Task |
|------|------------|-----------|
| `produce_episode` | `setup.setup_episode` | `step_perplexity_research` |
| `step_perplexity_research` | `research.run_perplexity_research` | `step_question_discovery` |
| `step_question_discovery` | auto-retries `research.run_perplexity_research` if no usable p2-* artifacts exist, then `analysis.discover_questions` | `step_gpt_research` + `step_gemini_research` + `step_together_research` + `step_claude_research` + `step_grok_research` + `step_mirofish_research` (parallel) |
| `step_gpt_research` | `research.run_gpt_researcher` | _(signal fan-in)_ |
| `step_gemini_research` | `research.run_gemini_research` | _(signal fan-in)_ |
| `step_together_research` | `research.run_together_research` | _(signal fan-in)_ |
| `step_claude_research` | `research.run_claude_research` | _(signal fan-in)_ |
| `step_grok_research` | `research.run_grok_research` | _(signal fan-in)_ |
| `step_mirofish_research` | `research.run_mirofish_research` | _(signal fan-in)_ |
| `step_research_digests` | `analysis.create_research_digest` (per artifact) | `step_cross_validation` |
| `step_cross_validation` | `analysis.cross_validate` | `step_master_briefing` |
| `step_master_briefing` | `analysis.write_briefing` | Quality Gate Wave 1 → `step_synthesis` |
| `step_synthesis` | `synthesis.synthesize_report` | `step_episode_planning` |
| `step_episode_planning` | `synthesis.plan_episode_content` | Quality Gate Wave 2 → `step_audio_generation` |
| `step_audio_generation` | `workflow.pause_for_human` (local_audio_worker) | `step_transcribe_audio` |
| `step_transcribe_audio` | `audio.transcribe_audio` | `step_generate_chapters` |
| `step_generate_chapters` | `audio.generate_episode_chapters` | `step_cover_art` + `step_metadata` + `step_companions` (parallel) |
| `step_cover_art` | `publishing.generate_cover_art` | _(signal fan-in)_ |
| `step_metadata` | `publishing.write_episode_metadata` | _(signal fan-in)_ |
| `step_companions` | `publishing.generate_companions` | _(signal fan-in)_ |
| `step_publish` | `publishing.publish_episode` | _(complete)_ |

### Concurrency Guard

Each sequential step acquires a `select_for_update` lock to prevent duplicate execution:

```python
def _acquire_step_lock(episode_id, expected_step):
    with transaction.atomic():
        wf = EpisodeWorkflow.objects.select_for_update().get(episode_id=episode_id)
        if wf.status == "running" and wf.current_step == expected_step:
            raise ValueError(f"Step '{expected_step}' already running")
```

### Fan-In Signal (`apps/podcast/signals.py`)

Parallel steps (Targeted Research and Publishing Assets) use a `post_save` signal on `EpisodeArtifact` for fan-in coordination. The signal uses `select_for_update` inside `_try_enqueue_next_step()` to prevent double-enqueue.

**Targeted Research fan-in** uses threshold-based advancement: all p2-* artifacts must have non-empty content (task resolved), and at least one must have real content (not `[FAILED:]` or `[SKIPPED:]`). This allows the pipeline to continue when some research sources fail while others succeed. Failed sources write `[FAILED: error]` to their artifact via `fail_research_source()`, which triggers the signal without halting the workflow.

### Quality Gates

- **Wave 1** (after Master Briefing): Checks `p3-briefing` artifact has 200+ words. Pauses for human review on failure.
- **Wave 2** (after Episode Planning): Checks `content_plan` artifact exists. Pauses for human review on failure.

### Settings

```python
# settings/base.py
PODCAST_DEFAULT_MODEL = "claude-sonnet-4-20250514"
```
