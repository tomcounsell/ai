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
| `/do-test` | test | Test output with pass/fail counts and coverage |
| `/do-docs` | document | Created/updated doc file paths and index entry |

Detection works two ways (OR logic):
1. Message text contains the skill's trigger pattern
2. WorkflowState.phase matches the skill's phase name

To add a future skill, add an entry to `SKILL_DETECTORS` — the coach picks it up automatically.

## Flow

```
Agent Output
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

## Error-Classified Output Bypass (Crash Guard)

When the SDK crashes or returns an error, the output classifier labels it as `ERROR`. Error-classified outputs **skip auto-continue entirely** and are sent directly to chat. This prevents the system from endlessly re-enqueuing continuation jobs for a session that will keep crashing.

### Crash Guard Flow

1. SDK session crashes or returns an error message
2. Output classifier labels the result as `OutputType.ERROR`
3. The `send_to_chat` callback in `agent/job_queue.py` checks the classification
4. Because the type is `ERROR`, auto-continue logic is bypassed completely
5. The error message is sent to chat so the user sees what happened
6. Session cleanup in `agent/sdk_client.py` marks the session as `failed` in Redis
7. The session watchdog (`monitoring/session_watchdog.py`) handles any stale sessions left behind by crashes

### Related Guards

- **Session cleanup on SDK error**: `agent/sdk_client.py` marks sessions as `failed` in the `except` block
- **Unique constraint handling**: `monitoring/session_watchdog.py` catches stale session errors and marks them as `failed`

## Key Files

| File | Purpose |
|------|---------|
| `bridge/summarizer.py` | LLM classifier that produces `ClassificationResult` with optional `coaching_message` |
| `bridge/coach.py` | Tiered coaching resolution via `build_coaching_message()` |
| `agent/job_queue.py` | Auto-continue wiring, WorkflowState resolution, duplicate suppression |
| `agent/sdk_client.py` | Session cleanup on SDK errors (marks sessions as `failed`) |
| `monitoring/session_watchdog.py` | Stale session detection with unique constraint handling |
| `tests/test_coach.py` | Coach module tests (skill detection, criteria extraction, coaching tiers) |
| `tests/test_summarizer.py` | Classifier tests including coaching_message extraction |
| `tests/test_auto_continue.py` | Auto-continue duplicate suppression tests |

## Tuning Guide

### Coaching via Classifier Prompt

The classifier prompt in `CLASSIFIER_SYSTEM_PROMPT` (`bridge/summarizer.py`) includes few-shot examples showing what coaching messages to generate. To adjust coaching quality, edit the examples in the prompt.

### Heuristic Fallback

The `_build_heuristic_rejection_coaching()` function in `bridge/coach.py` provides a static template used when the LLM classifier doesn't provide a `coaching_message`. This covers cases like permission errors, auth failures, and rate limiting.

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
