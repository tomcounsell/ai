# Coaching Loop

The coaching loop prevents the auto-continue system from blindly sending "continue" when the agent pauses. Instead, it generates context-aware coaching messages that guide the agent toward productive next steps.

## Architecture

The classifier and coach work together in a unified pipeline. The classifier (an LLM call in `bridge/summarizer.py`) categorizes agent output and, when it detects a status update, also generates a coaching message in the same pass. The coach (`bridge/coach.py`) then selects the best available coaching message from a tiered fallback chain.

### Merged Classifier-Coach Design

The classifier prompt asks the LLM to do two things simultaneously:

1. **Classify** the agent output as `question`, `status`, `completion`, `blocker`, or `error`
2. **Generate a coaching message** (for `status` classifications) explaining what the agent should do next

This eliminates the need for a separate hedging-detection pass. The LLM natively understands when an agent is hedging, stalling, or missing evidence, and produces a targeted coaching message as part of its classification response.

The `ClassificationResult` dataclass carries both pieces:

```python
@dataclass
class ClassificationResult:
    output_type: OutputType
    confidence: float
    reason: str
    coaching_message: str | None = None  # LLM-generated coaching, if applicable
    was_rejected_completion: bool = False  # True when coaching_message present on status
```

## Coaching Tiers

The `build_coaching_message()` function in `bridge/coach.py` resolves coaching through a tiered fallback chain:

| Tier | Source | Description |
|------|--------|-------------|
| **Tier 1** | LLM coaching | Uses `classification.coaching_message` from the merged classifier pass. Prefixed with `[System Coach]`. |
| **Tier 1b** | Heuristic rejection coaching | Static templates from `_build_heuristic_rejection_coaching()` when no LLM coaching is available but `was_rejected_completion=True`. |
| **Tier 2** | Skill-aware coaching | Matches the agent output against known skills/commands and suggests relevant evidence. |
| **Tier 3** | Plain continue | Falls back to a simple "continue" message when no richer coaching is available. |

The first tier that produces a non-None result wins.

### Skill Detection

Detection uses the `SKILL_DETECTORS` mapping in `bridge/coach.py`. Currently supports four SDLC skills:

| Trigger | Phase | Evidence Hint |
|---------|-------|---------------|
| `/do-plan` | plan | Finalized plan doc with all required sections |
| `/do-build` | build | Passing tests, commit hashes, and a PR link |
| `/do-test` | test | Test output with pass/fail counts and failure details |
| `/do-docs` | document | Created/updated doc file paths and index entry |

Detection works two ways (OR logic):
1. Message text contains the skill's trigger pattern
2. WorkflowState.phase matches the skill's phase name

To add a future skill, add an entry to `SKILL_DETECTORS` — the coach picks it up automatically.

## Flow

The auto-continue system uses a two-path routing strategy. SDLC jobs (those with `[stage]` entries in `AgentSession.history`) use pipeline stage progress as the primary signal. Non-SDLC jobs (casual chat, Q&A) use the LLM classifier.

```
Agent Output
    |
    v
Is SDLC job? (check AgentSession.history for [stage] entries)
    |
    +-- YES: Stage-Aware Path
    |     |
    |     +-- Has failed stage? --> Deliver to user immediately
    |     +-- Stages remaining? --> Auto-continue (skip classifier)
    |     +-- All stages done?  --> Fall through to Classifier Path
    |
    +-- NO: Classifier Path
          |
          v
    Classifier (LLM) --- produces {type, confidence, reason, coaching_message}
          |
          v
    build_coaching_message()
          |-- Tier 1:  coaching_message from classifier (if present)
          |-- Tier 1b: heuristic rejection templates (if was_rejected but no LLM coaching)
          |-- Tier 2:  skill-aware coaching (plan criteria or skill evidence hints)
          +-- Tier 3:  plain "continue"
          |
          v
    [System Coach] message sent to agent
```

### Stage-Aware Auto-Continue Decision Matrix

| Pipeline state | Output classification | Action |
|---|---|---|
| Stages remaining, no error prose | (skipped) | Auto-continue |
| Stages remaining, error prose detected | ERROR/BLOCKER (heuristic) | Fall through to classifier |
| All stages done | Completion | Deliver to user |
| All stages done | Status (no evidence) | Coach + continue |
| Any stage failed | Error/blocker | Deliver to user |
| No stages (non-SDLC) | Question | Deliver to user |
| No stages (non-SDLC) | Status | Auto-continue (existing behavior) |

### Stage-Aware Error Guard

Before auto-continuing on the stage-aware path, a lightweight heuristic check (`_classify_with_heuristics(msg[:500])`) scans the first 500 characters for error/blocker patterns. If detected, the output falls through to the full classifier instead of being silently re-enqueued. This catches error prose (e.g., "Error: test suite timeout") that would otherwise be missed because stage history says "still in progress."

### Auto-Continue Caps

- **Non-SDLC jobs**: `MAX_AUTO_CONTINUES = 3` (classifier is the primary signal; counter prevents runaway loops)
- **SDLC jobs**: `MAX_AUTO_CONTINUES_SDLC = 10` (stage progress is the real termination signal; counter is a safety net)

Both caps are defined in `agent/job_queue.py`. The effective cap is selected based on `AgentSession.is_sdlc_job()`.

## Error-Classified Output Bypass (Crash Guard)

When the SDK crashes or returns an error, the output classifier labels it as `ERROR`. Error-classified outputs **skip auto-continue entirely** and are sent directly to chat. This prevents the system from endlessly re-enqueuing continuation jobs for a session that will keep crashing.

### Crash Guard Flow

1. SDK session crashes or returns an error message
2. Output classifier labels the result as `OutputType.ERROR`
3. The `send_to_chat` callback in `agent/job_queue.py` checks the classification
4. Because the type is `ERROR`, auto-continue logic is bypassed completely
5. The error message is sent to chat so the user sees what happened
6. Session cleanup in `agent/sdk_client.py` marks the session as `failed` in Redis
7. The session watchdog (`monitoring/session_watchdog.py`) handles any stale sessions left behind by crashes by catching `popoto.exceptions.ModelException` (e.g. unique constraint violations from duplicate session IDs)

### Why This Matters

Without this guard, an SDK crash would produce output classified as a status update (since error messages are short and lack completion evidence). The auto-continue system would then re-enqueue the job, which would crash again, creating an infinite crash loop consuming resources and flooding logs.

### Related Guards

- **Session cleanup on SDK error**: `agent/sdk_client.py` marks sessions as `failed` in the `except` block, preventing the watchdog from trying to interact with dead sessions
- **ModelException handling**: `monitoring/session_watchdog.py` catches `popoto.exceptions.ModelException` from stale sessions (unique constraint violations, corrupted state) and marks them as `failed` to break the watchdog loop

## Auto-Continue State Management

The `SendToChatResult` dataclass (`agent/job_queue.py`) tracks mutable state across the `send_to_chat()` closure and outer `_execute_job()` scope:

```python
@dataclass
class SendToChatResult:
    completion_sent: bool = False   # Gate: suppress further outputs after completion
    defer_reaction: bool = False    # Skip reaction when continuation job enqueued
    auto_continue_count: int = 0    # Persisted across re-enqueued jobs
```

This replaces the previous `nonlocal` closure variables (`_defer_reaction`, `_completion_sent`) which were fragile — if an exception occurred between setting one flag and another, state could become inconsistent.

## Key Files

| File | Purpose |
|------|---------|
| `bridge/summarizer.py` | LLM classifier, heuristic fallback, confidence gate, approval gate patterns, audit log |
| `bridge/coach.py` | Tiered coaching resolution via `build_coaching_message()` |
| `agent/job_queue.py` | Auto-continue wiring, `SendToChatResult` state, stage-aware routing + error guard, WorkflowState resolution |
| `models/agent_session.py` | `AgentSession` with `is_sdlc_job()`, `has_remaining_stages()`, `has_failed_stage()` helpers |
| `agent/sdk_client.py` | Session cleanup on SDK errors (marks sessions as `failed`) |
| `monitoring/session_watchdog.py` | Stale session detection with unique constraint handling |
| `logs/classification_audit.jsonl` | JSONL audit log for classification decisions (auto-rotated at 10MB) |
| `tests/test_coach.py` | Coach module tests (skill detection, criteria extraction, coaching tiers) |
| `tests/test_summarizer.py` | Classifier tests including approval gates, confidence gate, audit log |
| `tests/test_auto_continue.py` | Auto-continue duplicate suppression tests |
| `tests/test_stage_aware_auto_continue.py` | Stage-aware decision matrix tests (32 tests) |
| `tests/test_enqueue_continuation.py` | `_enqueue_continuation` tests (coaching source, parameters, plan resolution) |

## Tuning Guide

### Coaching via Classifier Prompt

The classifier prompt in `CLASSIFIER_SYSTEM_PROMPT` (`bridge/summarizer.py`) includes few-shot examples showing what coaching messages to generate. To adjust coaching quality, edit the examples in the prompt.

### Heuristic Fallback

The `_build_heuristic_rejection_coaching()` function in `bridge/coach.py` provides a static template used when the LLM classifier doesn't provide a `coaching_message`. This covers cases like permission errors, auth failures, and rate limiting.

### Classification Safety Net

The heuristic classifier (`_classify_with_heuristics`) has three layers of safety:

1. **Conservative default**: When no pattern matches, defaults to `QUESTION` at the confidence threshold (0.80). This shows the message to the user rather than silently auto-continuing.

2. **Approval gate patterns**: Detects permission-seeking language ("shall I proceed", "awaiting approval", "when approved", etc.) and classifies as `QUESTION` at 0.85 confidence. Added to both the heuristic patterns and the `CLASSIFIER_SYSTEM_PROMPT` examples.

3. **Confidence gate**: `_apply_heuristic_confidence_gate()` applies the same `CLASSIFICATION_CONFIDENCE_THRESHOLD` (0.80) to heuristic results that the LLM path uses. Below-threshold heuristic results become `QUESTION`. This closes the asymmetry where heuristic results at low confidence would have been returned as-is while LLM results would have been converted to QUESTION.

### Classification Audit Log

Every `classify_output()` call appends a JSONL entry to `logs/classification_audit.jsonl`:

```json
{"ts": "2026-02-27T14:00:00+00:00", "text_preview": "first 200 chars...", "result": "status_update", "confidence": 0.92, "reason": "...", "source": "llm"}
```

| Field | Description |
|-------|-------------|
| `ts` | ISO 8601 timestamp (UTC) |
| `text_preview` | First 200 characters of the classified text |
| `result` | Classification output type value |
| `confidence` | Confidence score (0-1), rounded to 3 decimals |
| `reason` | Classifier reasoning |
| `source` | `"llm"`, `"heuristic"`, or `"empty"` |

**Rotation**: When the file exceeds 10MB, it's renamed to `.jsonl.1` (simple single-rotation). Single writer, no locking needed.

**Non-fatal**: Write failures are logged at DEBUG level and do not affect classification behavior.

### Skill-Specific Coaching

- **With plan criteria**: Edit `_build_skill_coaching_with_criteria()` — how quoted criteria are presented
- **With file pointer**: Edit `_build_skill_coaching_with_file_pointer()` — fallback when criteria can't be parsed
- **Generic skill**: Edit `_build_generic_skill_coaching()` — uses `evidence_hint` from `SKILL_DETECTORS`

### Plan Success Criteria Extraction

`_extract_success_criteria()` uses regex to find `## Success Criteria` in plan docs. Returns the section content only when parsed with certainty. If the file is missing, malformed, or the section can't be cleanly extracted, it returns `None` and the caller falls back to pointing the agent at the file path.

## Coaching Message Prefix

All coaching messages (except plain "continue") are prefixed with `[System Coach]` so the agent can distinguish coaching from user messages.

## Design Rationale

Merging classification and coaching into a single LLM pass provides two benefits:

1. **Fewer API calls** — one call instead of two (classify then coach separately)
2. **Better coherence** — the same LLM context that decides "this is a status update" also explains what the agent should do about it, avoiding information loss from piping a classification label into a separate coaching prompt

The previous approach used regex-based hedging pattern detection (matching keywords like "hedg", "no evidence", "no proof" in the classifier's reason text) followed by static coaching templates. The merged design replaces the brittle pattern matching with native LLM understanding while keeping heuristic fallbacks for reliability.
