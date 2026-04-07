# Coaching Loop

> **Deprecated**: The coaching loop has been replaced by the [Observer Agent](observer-agent.md) (issue #309, PR #321). The classifier (`classify_output()`), coach (`build_coaching_message()`), and routing logic (`classify_routing_decision()`) have been removed from the routing path. Stage detection is now handled deterministically by `bridge/stage_detector.py`. This document is retained for historical reference.

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
| **Tier 1c** | SDLC stage progress coaching | Explicit next-stage instructions for SDLC pipelines via `_build_sdlc_stage_coaching()`. Fires when `sdlc_stage_progress` is provided and has remaining stages. |
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

The auto-continue system uses a two-path routing strategy. SDLC jobs use pipeline stage progress as the primary signal. Non-SDLC jobs (casual chat, Q&A) use the LLM classifier.

An SDLC job is identified by `AgentSession.is_sdlc_job()`, which checks two signals in order:
1. **Primary**: `classification_type == "sdlc"` -- set at input routing time by `tools/classifier.py`
2. **Fallback**: `[stage]` entries in `AgentSession.history` -- for sessions that have stage progress from `session_progress` calls

The `classification_type` check is the authoritative signal because it is set once at classification time and propagated through auto-continue via `_enqueue_continuation()`. The history fallback catches sessions where stage entries exist but `classification_type` was not set.

```
Agent Output
    |
    v
Is SDLC job? (classification_type == "sdlc" OR [stage] entries in history)
    |
    +-- YES: Stage-Aware Path
    |     |
    |     +-- Has failed stage? --> Deliver to user immediately
    |     +-- Stages remaining?
    |     |     +-- Open questions detected? --> Deliver to user (pause for input)
    |     |     +-- Error prose detected?    --> Fall through to Classifier Path
    |     |     +-- Otherwise                --> Auto-continue (skip classifier)
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
          |-- Tier 1c: SDLC stage progress coaching (if sdlc_stage_progress has remaining stages)
          |-- Tier 2:  skill-aware coaching (plan criteria or skill evidence hints)
          +-- Tier 3:  plain "continue"
          |
          v
    [System Coach] message sent to agent
```

### Stage-Aware Auto-Continue Decision Matrix

| Pipeline state | Output classification | Action |
|---|---|---|
| Stages remaining, open questions detected | (skipped) | Deliver to user (pause for human input) |
| Stages remaining, no error prose, no open questions | (skipped) | Auto-continue |
| Stages remaining, error prose detected | ERROR/BLOCKER (heuristic) | Fall through to classifier |
| All stages done | Completion | Deliver to user |
| All stages done | Status (no evidence) | Coach + continue |
| Any stage failed | Error/blocker | Deliver to user |
| No stages (non-SDLC) | Question | Deliver to user |
| No stages (non-SDLC) | Completion (Q&A answer) | Deliver to user |
| No stages (non-SDLC) | Status + planning language | Auto-continue |
| No stages (non-SDLC) | Status + substantive content | Deliver to user |

### Open Question Gate

Before auto-continuing on the stage-aware path during the **PLAN stage**, the system checks for `## Open Questions` sections in the agent output using `_extract_open_questions()` from `bridge/summarizer.py`. If substantive questions are found, the output falls through to the classifier/deliver path instead of auto-continuing. This ensures the human can answer design decisions before BUILD proceeds.

The gate is scoped to the PLAN stage only (determined via `agent_session.get_stage_progress()`). During BUILD, TEST, REVIEW, and DOCS stages, the gate is bypassed to avoid false positives from quoted plan content in status reports.

The gate works at two levels (defense in depth):
1. **`agent/job_queue.py`**: During PLAN stage, extracts open questions from the output. If found, falls through to deliver path. During other stages, skips extraction entirely.
2. **`bridge/summarizer.py`**: When summarizing, `summarize_response()` detects `## Open Questions` sections and populates the `expectations` field with extracted questions. LLM-detected expectations take priority.

The `_extract_open_questions()` function extracts list items (numbered or bulleted) from `## Open Questions` sections. It skips resolved/answered sections (headings like `## Open Questions (Resolved)`). It handles edge cases: empty sections, placeholder text (TBD, TODO, N/A), whitespace-only sections, and malformed markdown. Only substantive items are treated as questions.

### Stage-Aware Error Guard

Before auto-continuing on the stage-aware path, a lightweight heuristic check (`_classify_with_heuristics(msg[:500])`) scans the first 500 characters for error/blocker patterns. If detected, the output falls through to the full classifier instead of being silently re-enqueued. This catches error prose (e.g., "Error: test suite timeout") that would otherwise be missed because stage history says "still in progress."

### Non-SDLC Planning Language Guard

For non-SDLC jobs (casual chat, Q&A), status updates only auto-continue if the output contains **planning language** -- phrases indicating the agent is sharing its approach before executing work (e.g., "I'll check...", "Let me investigate...", "First I need to..."). Substantive answers and informational content are delivered immediately to the user.

This guard (`_is_planning_language()` in `agent/job_queue.py`) checks the first 500 characters for planning prefixes. It was introduced to fix the cross-wire bug (#232) where a Q&A answer was misclassified as a status update and auto-continued, causing it to resume the wrong session context.

### Q&A Completion Classification (Path B)

The classifier prompt defines two paths to COMPLETION:

- **Path A (SDLC/work completion)**: Requires evidence -- test output, numbers, URLs, commit hashes
- **Path B (Conversational/Q&A completion)**: No evidence needed. When the user asked a question and the agent answered with factual, substantive content, the answer itself is the deliverable

Path B was added to prevent informational answers (e.g., "The summarizer works by...") from being classified as STATUS_UPDATE simply because they lack test output or URLs. See few-shot examples in `CLASSIFIER_SYSTEM_PROMPT` in `bridge/summarizer.py`.

### Auto-Continue Caps

- **Non-SDLC jobs**: `MAX_AUTO_CONTINUES = 3` (classifier + planning language guard are the primary signals; counter prevents runaway loops)
- **SDLC jobs**: `MAX_AUTO_CONTINUES_SDLC = 10` (stage progress is the real termination signal; counter is a safety net)

Both caps are defined in `agent/job_queue.py`. The effective cap is selected based on `AgentSession.is_sdlc_job()`.

### Session Reuse (Single Source of Truth)

When `_enqueue_continuation()` fires for auto-continue, it **reuses the existing `AgentSession`** instead of creating a new one. This eliminates the duplicate-session problem where metadata was lost across auto-continue boundaries.

**How it works:** The function looks up the existing session by `session_id`, extracts all fields via `_extract_job_fields()`, deletes the old record, and recreates it with updated `status` ("pending"), `message_text` (coaching message), `auto_continue_count`, and `priority` ("high"). All other fields -- including `classification_type`, `history`, link URLs, `context_summary`, and `expectations` -- are preserved automatically.

This follows the same delete-and-recreate pattern used by `_pop_job()` to work around Popoto's `KeyField` index corruption (where `on_save()` adds to the new index set but never removes from the old one).

**Fallback:** If no session is found for the `session_id` (edge case), `_enqueue_continuation` falls back to calling `enqueue_job()` with explicit `classification_type` propagation.

**Fresh session reads:** The `send_to_chat` closure re-reads the `AgentSession` from Redis before evaluating `is_sdlc_job()`, `has_remaining_stages()`, and `has_failed_stage()`. This ensures routing decisions use data written by `session_progress.py` in the agent subprocess, not the stale in-memory copy captured at job start.

The `tools/classifier.py` module supports four classification types: `bug`, `feature`, `chore`, and `sdlc`. The `sdlc` type is used for messages that reference the SDLC pipeline (e.g., "SDLC issue 274", "/sdlc", "run the pipeline for issue #42").

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
| `bridge/summarizer.py` | LLM classifier, heuristic fallback, confidence gate, approval gate patterns, open question extraction, audit log |
| `bridge/coach.py` | Tiered coaching resolution via `build_coaching_message()` |
| `agent/job_queue.py` | Auto-continue wiring, `SendToChatResult` state, stage-aware routing + open question gate + error guard, WorkflowState resolution |
| `models/agent_session.py` | `AgentSession` with `is_sdlc_job()`, `has_remaining_stages()`, `has_failed_stage()` helpers |
| `agent/sdk_client.py` | Session cleanup on SDK errors (marks sessions as `failed`) |
| `monitoring/session_watchdog.py` | Stale session detection with unique constraint handling |
| `logs/classification_audit.jsonl` | JSONL audit log for classification decisions (auto-rotated at 10MB) |
| `tests/test_coach.py` | Coach module tests (skill detection, criteria extraction, coaching tiers) |
| `tests/test_summarizer.py` | Classifier tests including approval gates, confidence gate, audit log |
| `tests/test_auto_continue.py` | Auto-continue duplicate suppression tests |
| `tests/test_stage_aware_auto_continue.py` | Stage-aware decision matrix tests (32 tests) |
| `tests/test_enqueue_continuation.py` | `_enqueue_continuation` tests (coaching source, parameters, plan resolution) |
| `tests/test_cross_wire_fixes.py` | Cross-wire bug fix tests: classifier Q&A, session isolation, planning language guard |
| `tests/test_open_question_gate.py` | Open question extraction and stage-aware gate behavior tests (28 tests) |

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

### SDLC Stage Progress Coaching (Tier 1c)

When the auto-continue system fires for an SDLC job with remaining pipeline stages, `_enqueue_continuation()` in `agent/job_queue.py` reads the session's stage progress via `AgentSession.get_stage_progress()` and passes it to `build_coaching_message()` as the `sdlc_stage_progress` parameter.

The `_build_sdlc_stage_coaching()` function in `bridge/coach.py` then:

1. Identifies which stages are completed, in progress, and pending
2. Finds the first pending stage in pipeline order
3. Maps the stage name to a `/do-*` skill via `STAGE_TO_SKILL`
4. Builds a directive coaching message that explicitly tells the agent to invoke the next skill

Example output:
```
[System Coach] The SDLC pipeline has completed: ISSUE, PLAN.
The next stage is BUILD. Return to the SDLC pipeline and invoke
`/do-build` to continue. Do NOT investigate logs, check system
status, or start other work -- proceed directly to `/do-build`.
```

The `STAGE_TO_SKILL` mapping covers all actionable stages:

| Stage | Skill |
|-------|-------|
| PLAN | `/do-plan` |
| BUILD | `/do-build` |
| TEST | `/do-test` |
| REVIEW | `/do-pr-review` |
| DOCS | `/do-docs` |

The ISSUE stage has no skill mapping since it is a manual step.

**Fall-through behavior**: When `sdlc_stage_progress` is `None`, empty, or has no remaining stages (all completed), the function returns `None` and the coach falls through to Tier 2 (skill-aware coaching) or Tier 3 (plain continue).

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
