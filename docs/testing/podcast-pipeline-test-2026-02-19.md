# Podcast Production Pipeline - End-to-End Test Report
**Date:** 2026-02-19
**Episode:** Test Episode: AI Infrastructure Evolution (ID: 2629)
**Status:** INCOMPLETE - Multiple blocking issues found

## Executive Summary
The podcast production pipeline was tested for the first time end-to-end. The pipeline started successfully but encountered multiple issues preventing complete autonomous execution. The test revealed architectural problems, missing API keys, race conditions, and lack of error recovery mechanisms.

## Test Setup
- **Episode Created:** ID 2629, "Test Episode: AI Infrastructure Evolution"
- **Podcast:** Test Podcast (test-podcast)
- **Command Used:** `python manage.py start_episode --podcast test-podcast --episode test-ai-infrastructure-evolution`
- **Environment:** Local development (ImmediateBackend for tasks)

## Pipeline Progress

### ✅ Successful Steps
1. **Setup (Phase 1)** - PASSED
   - EpisodeWorkflow created successfully
   - p1-brief artifact created from episode description
   - Working directory created at `apps/podcast/pending-episodes/test-podcast/test-ai-infrastructure-evolution/`
   - Files created: research/p1-brief.md, logs/prompts.md, sources.md
   - Episode status changed to `in_progress`

2. **Prompt Crafting** - PASSED (when tested manually)
   - `craft_research_prompt(episode_id, 'perplexity')` works correctly
   - Generated 2,952 character research prompt
   - Uses Claude Sonnet via PydanticAI
   - Artifact created: prompt-perplexity

3. **Question Discovery (Phase 3)** - PASSED (when tested manually)
   - `discover_questions(episode_id)` works correctly
   - Generated 11,069 character question discovery document
   - Properly analyzed mock research and identified gaps
   - Artifact created: question-discovery

### ❌ Failed Steps

#### 1. CRITICAL: Race Condition in Task Enqueueing
**Phase:** Perplexity Research (Phase 2)
**Error Message:**
```
ValueError: Step 'Perplexity Research' already running for episode 2629
```

**Stack Trace:**
```
File "apps/podcast/tasks.py", line 127, in step_perplexity_research
    _acquire_step_lock(episode_id, "Perplexity Research")
File "apps/podcast/tasks.py", line 61, in _acquire_step_lock
    raise ValueError(
        f"Step '{expected_step}' already running "
        f"for episode {episode_id}"
    )
```

**Root Cause:**
- The `produce_episode` task calls `setup.setup_episode(episode_id)`
- This creates the workflow and marks Perplexity Research as "started"
- Then `produce_episode` immediately enqueues `step_perplexity_research.enqueue()`
- With ImmediateBackend, the task runs synchronously
- `_acquire_step_lock` checks history and finds status="started" already exists
- Raises ValueError thinking it's a duplicate

**Problem Analysis:**
The lock checking logic in `_acquire_step_lock` (lines 56-64) looks for ANY history entry with status="started" for the current step. This doesn't distinguish between:
1. A legitimately running task (should block)
2. The FIRST time a task is being run (should proceed)

The check is:
```python
for entry in reversed(wf.history):
    if entry["step"] == expected_step and entry["status"] == "started":
        raise ValueError(...)
```

This means the very first run will always fail because `workflow.advance_step()` already created a history entry with status="started".

**Impact:** BLOCKING - Pipeline cannot progress past Setup without manual intervention

---

#### 2. BLOCKING: Missing Perplexity API Key
**Phase:** Perplexity Research (Phase 2)
**Required:** `PERPLEXITY_API_KEY` environment variable
**Status:** Not set

**Impact:** Even if the race condition is fixed, Perplexity research will fail due to missing API key.

**Code Location:** `apps/podcast/tools/perplexity_deep_research.py:54-71`

**Workaround Used:** Created mock p2-perplexity artifact to continue testing downstream steps.

---

#### 3. BLOCKING: Missing Together AI API Key
**Phase:** Targeted Research (Phase 4)
**Required:** `TOGETHER_API_KEY` environment variable
**Status:** Not set

**Impact:** One of four parallel research tasks (step_together_research) will fail.

**Notes:** The pipeline has 4 parallel research tasks in Phase 4:
- ✓ GPT-Researcher (has OPENAI_API_KEY + TAVILY_API_KEY)
- ✓ Gemini (has GEMINI_API_KEY)
- ✗ Together AI (missing TOGETHER_API_KEY)
- ✓ Claude Deep Research (has ANTHROPIC_API_KEY)

---

## Architectural Issues Discovered

### 1. Workflow Lock Logic is Flawed
The `_acquire_step_lock` function doesn't properly handle the initial run of a step. It should check if the step is ACTIVELY running (e.g., by checking workflow.status AND current_step), not just if a history entry exists with status="started".

**Suggested Fix:**
```python
def _acquire_step_lock(episode_id: int, expected_step: str) -> None:
    with transaction.atomic():
        wf = EpisodeWorkflow.objects.select_for_update().get(episode_id=episode_id)

        # Only block if BOTH conditions are true:
        # 1. Workflow is in 'running' status
        # 2. Current step matches expected step
        # 3. Most recent history entry is 'started' (not 'completed')
        if wf.status == "running" and wf.current_step == expected_step:
            # Check if this is a retry vs duplicate
            latest_entry = None
            for entry in reversed(wf.history):
                if entry["step"] == expected_step:
                    latest_entry = entry
                    break

            if latest_entry and latest_entry["status"] == "started" and latest_entry.get("completed_at"):
                # Step was started and completed - this is a duplicate
                raise ValueError(f"Step '{expected_step}' already completed for episode {episode_id}")
            elif latest_entry and latest_entry["status"] == "started" and not latest_entry.get("completed_at"):
                # Step is actively running - this is legitimate blocking
                raise ValueError(f"Step '{expected_step}' already running for episode {episode_id}")

        if wf.current_step != expected_step:
            raise ValueError(
                f"Episode {episode_id} is at step '{wf.current_step}', "
                f"not '{expected_step}'"
            )
```

### 2. No Error Recovery Mechanism
When a task fails, the workflow is marked as "failed" but there's no automatic retry or graceful degradation. For example:
- If Perplexity research fails, why not continue with GPT-Researcher?
- If one parallel research task fails, should we block the entire pipeline?

### 3. ImmediateBackend Exposes Race Conditions
Using ImmediateBackend (synchronous task execution) reveals race conditions that might be hidden in production with async DatabaseBackend. This is actually GOOD for testing, but indicates the code assumes async execution.

### 4. Workflow State vs History Inconsistency
The workflow has both:
- `status` field (pending/running/paused/failed/complete)
- `history` list with per-step status entries

These can get out of sync. For example:
- After Setup completes, workflow.status = "running"
- Perplexity Research entry in history has status="started"
- But then the task fails before it actually runs
- Now workflow.status = "running" but the last history entry is "started" with no "completed_at"

### 5. Missing API Key Validation at Start
The pipeline doesn't validate that required API keys exist before starting. It should fail fast at Setup with a clear error message listing missing keys.

---

## Database State After Test

### Episode
- **ID:** 2629
- **Status:** in_progress
- **Title:** Test Episode: AI Infrastructure Evolution

### EpisodeWorkflow
- **Current Step:** Question Discovery
- **Status:** running
- **Blocked On:** (empty)
- **History:** 3 entries
  1. Setup: completed
  2. Perplexity Research: started (no completed_at)
  3. Question Discovery: started (no completed_at)

### EpisodeArtifacts
1. **p1-brief**
   - Workflow Context: Setup
   - Content: 151 chars
   - Status: ✓ Valid

2. **prompt-perplexity**
   - Workflow Context: Research Gathering
   - Content: 2,952 chars
   - Status: ✓ Valid

3. **p2-perplexity** (MOCK)
   - Workflow Context: Research Gathering
   - Content: 1,306 chars
   - Status: ⚠️ Mock data for testing

4. **question-discovery**
   - Workflow Context: (not set)
   - Content: 11,069 chars
   - Status: ✓ Valid

---

## Files Created

### Working Directory
`/Users/valorengels/src/cuttlefish/apps/podcast/pending-episodes/test-podcast/test-ai-infrastructure-evolution/`

**Structure:**
```
.
├── research/
│   ├── documents/  (empty)
│   └── p1-brief.md (151 chars)
├── logs/
│   └── prompts.md (metadata)
├── tmp/  (empty)
└── sources.md (template)
```

---

## API Keys Status

| Service | Environment Variable | Status | Usage |
|---------|---------------------|--------|-------|
| Anthropic Claude | `ANTHROPIC_API_KEY` | ✓ SET | Prompt crafting, question discovery, Claude research |
| OpenAI | `OPENAI_API_KEY` | ✓ SET | GPT-Researcher |
| Tavily | `TAVILY_API_KEY` | ✓ SET | GPT-Researcher search |
| Google Gemini | `GEMINI_API_KEY` | ✓ SET | Gemini research |
| **Perplexity** | **`PERPLEXITY_API_KEY`** | **✗ MISSING** | **Perplexity Deep Research** |
| **Together AI** | **`TOGETHER_API_KEY`** | **✗ MISSING** | **Together research** |
| OpenRouter | `OPENROUTER_API_KEY` | ✓ SET | (usage TBD) |

---

## Issues That WILL Block Further Progress (Untested)

### Phase 4: Targeted Research
- Together AI research will fail (missing API key)
- Need to verify fan-in signal logic works correctly
- Signal-based coordination might have race conditions

### Phase 5-6: Cross-Validation & Master Briefing
- Depends on having multiple p2-* artifacts
- Untested with mock data

### Phase 7: Synthesis
- Uses Claude Opus for long-form narrative generation
- May hit token limits with large research corpus
- Untested

### Phase 8: Episode Planning
- Generates NotebookLM-compatible content plan
- Quality gate: checks for content_plan artifact
- Untested

### Phase 9: Audio Generation
- Requires NotebookLM Enterprise API access
- Environment variable: `NOTEBOOKLM_API_KEY` (not checked)
- Tool: `apps/podcast/tools/notebooklm_api.py`
- Untested

### Phase 10: Audio Processing
- Transcription: Uses local Whisper model
- Chapter generation: Uses Claude to analyze transcript
- Storage: Requires Supabase configuration
- Untested

### Phase 11: Publishing Assets
- Cover art generation
- Metadata generation
- Companion resources
- All untested

### Phase 12: Publish
- Final RSS feed update
- Requires fully configured storage backend
- Untested

---

## Recommended Next Steps

### 1. Fix Critical Race Condition (P0)
- Rewrite `_acquire_step_lock` logic
- Add tests for race conditions
- Verify with both ImmediateBackend and DatabaseBackend

### 2. Add API Key Validation (P0)
- Create `validate_api_keys()` function in setup.py
- Check all required keys before starting pipeline
- Provide clear error messages with instructions

### 3. Implement Graceful Degradation (P1)
- Make Perplexity research optional (skip if no API key)
- Allow pipeline to continue if ≥2 research sources succeed
- Add configuration for required vs optional steps

### 4. Add Error Recovery (P1)
- Implement retry logic for failed tasks
- Add manual "Skip Step" option in UI
- Create fallback strategies for each phase

### 5. Complete Integration Testing (P1)
- Set up test environment with all API keys
- Run full pipeline with small test episode
- Document actual vs expected behavior at each phase

### 6. Fix Workflow State Management (P2)
- Ensure status and history stay in sync
- Add validation to detect inconsistencies
- Consider using Django signals to keep them coupled

### 7. Add Monitoring & Observability (P2)
- Log entry/exit of each step with timing
- Track token usage and costs per step
- Add Sentry error reporting for task failures

---

## Questions for Tom

1. **API Keys:** Do we need to purchase Perplexity and Together AI API access, or should these be optional?

2. **Error Recovery:** What's the expected behavior when a research step fails? Continue with remaining sources or halt entirely?

3. **Workflow Lock:** Is the current lock logic a known issue, or is this the first time it's been tested end-to-end?

4. **Testing Strategy:** Should we set up a CI pipeline with mock services to test the full workflow, or is manual testing sufficient?

5. **NotebookLM:** Do we have NotebookLM Enterprise API access configured? The audio generation step will fail without it.

6. **Storage:** Is Supabase configured and tested for audio file uploads?

---

## Files to Review

### Critical Code Files
1. `/Users/valorengels/src/cuttlefish/apps/podcast/tasks.py` - Task definitions and lock logic
2. `/Users/valorengels/src/cuttlefish/apps/podcast/services/workflow.py` - Workflow state management
3. `/Users/valorengels/src/cuttlefish/apps/podcast/services/setup.py` - Initial setup logic
4. `/Users/valorengels/src/cuttlefish/apps/podcast/signals.py` - Fan-in coordination

### Tools That Need Testing
1. `/Users/valorengels/src/cuttlefish/apps/podcast/tools/notebooklm_api.py` - Audio generation
2. `/Users/valorengels/src/cuttlefish/apps/podcast/tools/perplexity_deep_research.py` - Perplexity research
3. `/Users/valorengels/src/cuttlefish/apps/podcast/tools/transcribe_only.py` - Whisper transcription
4. `/Users/valorengels/src/cuttlefish/apps/podcast/services/audio.py` - Audio processing

---

## Conclusion

The podcast production pipeline has a solid foundation but is not yet production-ready. The main issues are:

1. **Race condition preventing any episode from completing Phase 2** (CRITICAL)
2. **Missing API keys for 2/6 research providers** (BLOCKING)
3. **No error recovery or graceful degradation** (MAJOR)
4. **Untested phases 5-12** (UNKNOWN RISK)

The good news:
- Core AI services (Claude, OpenAI, Gemini) work correctly
- Database-backed artifact system works well
- File system organization is clean and logical
- Named AI Tools pattern is implemented correctly

Estimated time to production-ready:
- Fix race condition: 2-4 hours
- Add API key validation: 1-2 hours
- Test remaining phases: 8-16 hours (depends on API access)
- Error recovery: 4-8 hours
- Total: **2-4 days** of focused development
