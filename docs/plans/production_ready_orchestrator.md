---
status: Ready
type: chore
appetite: Medium
owner: Tom
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/70
---

# Production-Ready Podcast Orchestrator

## Problem

The podcast orchestrator (`apps/podcast/agent/orchestrator.py`) uses a monolithic Anthropic agentic loop: one Claude session with `max_iterations=50` that decides which tool to call next. This is the wrong abstraction for a deterministic 12-phase pipeline.

**Current behavior:**
- One opaque `run_episode()` function does everything — can't inspect which step is stuck.
- `max_iterations` is a band-aid for a loop that shouldn't exist. If step 7 fails, you have to re-run the entire loop from scratch.
- Can't be enqueued as a background task — no `@task` decorator.
- No concurrency protection — two calls race on the same workflow state.
- Hard-coded model string.

**Desired outcome:**
- Each of the 12 workflow phases is its own `@task`. Completing one enqueues the next.
- At any point you can inspect which step is stuck and unstick just that piece.
- `produce_episode(episode_id)` is the entry point that kicks off the pipeline.
- Concurrent calls for the same step are rejected via database-level locking.

## Appetite

**Size:** Medium

**Team:** Solo dev + 1 review round

**Interactions:**
- PM check-ins: 1 (scope alignment on prompt generation approach)
- Review rounds: 1

Replaces `orchestrator.py` with a `tasks.py` module. Touches `workflow.py` service lightly. No new models or migrations.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Task-per-step architecture**: Each workflow phase is a `@task`-decorated function that calls the service layer and enqueues its successor(s)
- **Dependency tree**: Steps that can run in parallel do (e.g., GPT + Gemini research). Steps that depend on prior output wait.
- **Prompt generation**: Research steps generate their own prompts from episode artifacts, replacing the AI orchestrator's prompt-crafting role
- **Concurrency guard**: `select_for_update` per step to prevent duplicate work
- **Settings-based model**: `settings.PODCAST_DEFAULT_MODEL` for any AI steps that need it

### Dependency Tree

(See also: `docs/reference/podcast-workflow-diagram.md`)

```
produce_episode(episode_id)
  └→ step_setup
       └→ step_perplexity_research
            └→ step_question_discovery
                 ├→ step_gpt_research ──────┐
                 └→ step_gemini_research ────┤ (parallel)
                                             └→ step_research_digests (one per p2-* artifact)
                                                  └→ step_cross_validation
                                                       └→ step_master_briefing
                                                            └→ [Quality Gate: Wave 1]
                                                                 └→ step_synthesis
                                                                      └→ step_episode_planning
                                                                           └→ [Quality Gate: Wave 2]
                                                                                └→ step_audio_generation
                                                                                     └→ step_transcribe_audio
                                                                                          └→ step_generate_chapters
                                                                                               ├→ step_cover_art ────┐
                                                                                               ├→ step_metadata ─────┤ (parallel)
                                                                                               └→ step_companions ───┘
                                                                                                                     └→ step_publish
```

Note: Phase 10 (Audio Processing) is sequential — chapters need transcript. Phase 11 (Publishing Assets) fans out in parallel.

### Flow

**Caller** → `produce_episode.enqueue(episode_id=42)` → **TaskResult** → Worker runs `step_setup` → on success, enqueues `step_perplexity_research` → ... → each step enqueues its successor(s) → `step_publish` marks episode complete

### Technical Approach

#### 1. New `apps/podcast/tasks.py` module

One `@task`-decorated function per workflow step. Each follows the same pattern:

```python
from django.tasks import task

@task
def step_perplexity_research(episode_id: int):
    _acquire_step_lock(episode_id, "Perplexity Research")
    try:
        prompt = _craft_research_prompt(episode_id, "perplexity")
        research.run_perplexity_research(episode_id, prompt=prompt)
        workflow.advance_step(episode_id, "Perplexity Research")
        # Enqueue next step
        step_question_discovery.enqueue(episode_id=episode_id)
    except Exception as exc:
        workflow.fail_step(episode_id, "Perplexity Research", str(exc))
        raise
```

#### 2. Concurrency guard per step

```python
def _acquire_step_lock(episode_id: int, expected_step: str):
    """Verify the workflow is at the expected step and not already running."""
    with transaction.atomic():
        wf = EpisodeWorkflow.objects.select_for_update().get(episode_id=episode_id)
        if wf.status == "running" and wf.current_step == expected_step:
            # Already being worked on by another worker
            raise ValueError(f"Step '{expected_step}' already running for episode {episode_id}")
        if wf.current_step != expected_step:
            raise ValueError(
                f"Episode {episode_id} is at step '{wf.current_step}', "
                f"not '{expected_step}'"
            )
```

Lock scope is narrow — just the status check, not the entire step execution.

#### 3. Signal-driven fan-in via `post_save` on `EpisodeArtifact`

Parallel steps (targeted research, publishing assets) don't use explicit counting. Instead, a `post_save` signal on `EpisodeArtifact` checks whether the episode's expected artifacts are all populated, and enqueues the next step if so.

**How it works:**

1. **The prompt generator creates empty placeholder artifacts** for each research source it writes a prompt for (e.g., `p2-chatgpt`, `p2-gemini`, `p2-grok` — all with empty `content`). The thing that expects a result creates the place to put the result. This is handled by the research prompt Named AI Tool (#71).
2. Each research task fills in its artifact. Humans fill theirs via the web UI.
3. On every `EpisodeArtifact.post_save`, check: does this episode have pending empty artifacts for the current workflow step? If all are now populated → enqueue the next step.

```python
# apps/podcast/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.podcast.models import EpisodeArtifact

@receiver(post_save, sender=EpisodeArtifact)
def check_workflow_progression(sender, instance, **kwargs):
    """When an artifact is saved, check if the episode is ready to advance."""
    episode = instance.episode
    try:
        wf = episode.workflow
    except EpisodeWorkflow.DoesNotExist:
        return

    if wf.status not in ("running", "paused_for_human"):
        return

    # Check if all expected artifacts for the current step have content
    expected = EpisodeArtifact.objects.filter(
        episode=episode,
        title__startswith=_prefix_for_step(wf.current_step),
    )
    if expected.exists() and not expected.filter(content="").exists():
        _enqueue_next_step(episode.id, wf.current_step)
```

**This pattern applies to:**
- **Phase 4 (Targeted Research):** Empty `p2-chatgpt`, `p2-gemini`, `p2-grok` created during question discovery. GPT/Gemini tasks fill theirs; human fills `p2-grok` via web UI. Last save triggers digests.
- **Phase 11 (Publishing Assets):** Same pattern for metadata, companions, cover art artifacts.

**Benefits:**
- No explicit fan-in counting in task functions
- Human contributions through the web UI trigger progression automatically
- Each task only cares about its own artifact — the signal handles coordination

#### 4. Research prompt generation

Replace the AI orchestrator's prompt-crafting with a `_craft_research_prompt(episode_id, research_type)` utility:

- **"perplexity"**: Reads p1-brief, applies academic research template (peer-reviewed studies, meta-analyses, effect sizes, citations)
- **"gpt"**: Reads question-discovery artifact, extracts industry/technical questions
- **"gemini"**: Reads question-discovery artifact, extracts policy/strategic questions

This can be template-based initially. If more sophistication is needed later, it becomes a Named AI Tool.

#### 5. Entry point

```python
@task
def produce_episode(episode_id: int):
    """Kick off the 12-phase podcast production pipeline."""
    setup.setup_episode(episode_id)
    workflow.advance_step(episode_id, "Setup")
    step_perplexity_research.enqueue(episode_id=episode_id)
```

Callers use: `produce_episode.enqueue(episode_id=42)`

#### 6. Settings

```python
# settings/base.py
PODCAST_DEFAULT_MODEL = "claude-sonnet-4-20250514"
```

Used by Named AI Tools that need a model reference. Not used by the task pipeline directly (the pipeline calls service functions, which call Named AI Tools, which have their own model selection).

#### 7. Human-in-the-loop pauses

When a step encounters a condition requiring human input (e.g., manual research needed) or a quality gate fails, the pipeline calls `workflow.pause_for_human()` and does NOT enqueue the next step. `pause_for_human` is the default for quality issues. The human investigates, fixes the issue, and resumes — either by saving an artifact (which triggers the `post_save` signal) or by manually enqueuing the next step.

#### 8. Delete `orchestrator.py`, `tools.py`, `system_prompt.md`

The agentic loop, tool schemas, and system prompt are replaced entirely by the task pipeline. These files are no longer needed.

## Rabbit Holes

- **Async support** — Django's `@task` expects synchronous callables. Don't convert to async.
- **Retry logic** — Don't add automatic retry. A failed step should be investigated. The workflow `fail_step` records the error; a human decides whether to retry.
- **Dynamic step ordering** — Don't try to make the dependency tree configurable. The 12 phases are fixed. If the pipeline changes, update the code.
- **Sophisticated prompt generation** — Start with templates. Don't build a Named AI Tool for prompt crafting unless templates prove insufficient.
- **Preserving the agentic orchestrator as an alternative mode** — Just delete it. If we need AI-driven orchestration later, we can build it fresh on top of the task pipeline.

## Risks

### Risk 1: Race condition on `post_save` signal
**Impact:** If two artifacts save near-simultaneously, both `post_save` signals fire and both see "all artifacts populated" — enqueuing the next step twice.
**Mitigation:** The `_enqueue_next_step` helper acquires `select_for_update` on the workflow inside `transaction.atomic()`. First signal advances the step; second sees the workflow already advanced and skips.

### Risk 2: Research prompt quality without AI crafting
**Impact:** Template-based prompts may produce less targeted research than the Claude-crafted prompts.
**Mitigation:** The question-discovery step (which IS AI-powered) already identifies the right questions. Template prompts just frame those questions appropriately for each research tool. We can iterate on prompt quality.

### Risk 3: Worker timeout on long-running steps
**Impact:** Audio generation takes 5-30 minutes. The worker process may timeout.
**Mitigation:** Django task workers are designed for long-running tasks. The `db_worker` has no default timeout. If needed, `PODCAST_TASK_TIMEOUT` can be added to settings.

## No-Gos (Out of Scope)

- No new models or migrations (signal is on existing `EpisodeArtifact` model)
- No changes to the core service functions in `apps/podcast/services/` (they're called as-is)
- No async conversion
- No changes to `publish_episode` management command
- No automatic retry/backoff
- No Named AI Tool for prompt generation (see #71)

## Update System

No update system changes required — this is an internal code change with no new dependencies or config files.

## Agent Integration

No agent integration required — the task pipeline calls the same service functions the agent tools called. The service layer is unchanged.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/podcast-services.md` to document task pipeline and `produce_episode.enqueue()` usage
- [ ] Add `PODCAST_DEFAULT_MODEL` to settings documentation
- [ ] Update CLAUDE.md podcast section to reflect task-based architecture

### Inline Documentation
- [ ] Docstrings on each `@task` function in `tasks.py`

## Success Criteria

- [ ] `produce_episode.enqueue(episode_id=42)` kicks off the full pipeline
- [ ] Each workflow phase is a separate `@task` function
- [ ] Completing a step automatically enqueues the next step(s)
- [ ] `post_save` signal on `EpisodeArtifact` handles fan-in for parallel steps
- [ ] Question discovery creates empty placeholder artifacts for expected research sources
- [ ] Saving a research artifact (automated or via web UI) triggers progression
- [ ] Concurrent signals on the same step don't double-enqueue (select_for_update guard)
- [ ] Quality gates pause the pipeline when they fail
- [ ] `orchestrator.py`, `tools.py`, `system_prompt.md` deleted
- [ ] `PODCAST_DEFAULT_MODEL` in `settings/base.py`
- [ ] All existing tests pass
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (tasks)**
  - Name: tasks-builder
  - Role: Create `apps/podcast/tasks.py` with all step functions, prompt utility, and entry point
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete old orchestrator files, update settings, update imports
  - Agent Type: builder
  - Resume: true

- **Validator (pipeline)**
  - Name: pipeline-validator
  - Role: Verify task pipeline, dependency tree, concurrency guards
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs and CLAUDE.md
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create `apps/podcast/tasks.py` with all step functions
- **Task ID**: build-tasks
- **Depends On**: none
- **Assigned To**: tasks-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/tasks.py`
- Implement `produce_episode` entry point as `@task`
- Implement one `@task` per workflow step (~15 functions: 12 phases, some split into sub-steps for parallelism + digest step)
- Implement `_acquire_step_lock` concurrency guard
- Implement `_craft_research_prompt` template-based prompt utility (placeholder until #71)
- Sequential steps: lock check → call service function → advance workflow → enqueue next
- Parallel steps (targeted research, publishing assets): each task fills its artifact only — signal handles fan-in

### 1b. Create `apps/podcast/signals.py` with `post_save` fan-in
- **Task ID**: build-signals
- **Depends On**: none
- **Assigned To**: tasks-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
- Create `apps/podcast/signals.py`
- Implement `check_workflow_progression` signal handler on `EpisodeArtifact.post_save`
- Use `select_for_update` to prevent double-enqueue on concurrent saves
- Register signal in `apps/podcast/apps.py` `ready()` method
- Note: empty placeholder artifacts are created by the research prompt generator (#71), not by this signal

### 2. Add `PODCAST_DEFAULT_MODEL` setting
- **Task ID**: build-setting
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true (with task 1)
- Add `PODCAST_DEFAULT_MODEL = "claude-sonnet-4-20250514"` to `settings/base.py`

### 3. Delete old orchestrator files and update management command
- **Task ID**: build-cleanup
- **Depends On**: build-tasks
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `apps/podcast/agent/orchestrator.py`
- Delete `apps/podcast/agent/tools.py`
- Delete `apps/podcast/agent/system_prompt.md`
- Update `apps/podcast/agent/__init__.py` if it imports from deleted files
- Update `start_episode` management command to call `produce_episode.enqueue()` after setup
- Remove any imports of `run_episode` from other modules

### 4. Validate pipeline
- **Task ID**: validate-pipeline
- **Depends On**: build-cleanup, build-setting
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 12 step functions exist with `@task` decorator
- Verify dependency tree: each step enqueues correct successor(s)
- Verify `_acquire_step_lock` uses `select_for_update`
- Verify fan-out/fan-in logic for parallel steps
- Verify quality gates pause pipeline on failure
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v`

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-pipeline
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/podcast-services.md` with task pipeline architecture
- Update CLAUDE.md podcast orchestrator section
- Remove references to `orchestrator.py` and agentic loop

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: pipeline-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `DJANGO_SETTINGS_MODULE=settings pytest`
- Verify all success criteria met (including documentation)

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` — podcast tests pass
- `DJANGO_SETTINGS_MODULE=settings pytest -v` — full test suite passes
- `python -c "from apps.podcast.tasks import produce_episode; print(hasattr(produce_episode, 'enqueue'))"` — confirms @task decorator
- `python -c "from django.conf import settings; print(settings.PODCAST_DEFAULT_MODEL)"` — confirms setting exists
- `test ! -f apps/podcast/agent/orchestrator.py` — confirms old orchestrator deleted

---

## Open Questions

None — all resolved during planning.
