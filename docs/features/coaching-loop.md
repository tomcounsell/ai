# Coaching Loop: Context-Aware Auto-Continue Messages

## Overview

When the output classifier downgrades a completion to STATUS_UPDATE (due to hedging or missing evidence), the auto-continue system sends targeted coaching messages instead of bare "continue". The coach is explanatory and supportive — it tells the agent what it needs to confirm next time it stops, rather than barking commands.

**Philosophy:** The coach is here to help, not to be a supervisor. When uncertain about context, it degrades gracefully to plain "continue" rather than risk misdirecting the agent. It is better to say little or nothing than to accidentally coach in the wrong direction.

## How It Works

### Three-Tier Coaching

The coaching system (`bridge/coach.py`) builds messages based on context:

1. **Rejection coaching** (highest priority) — When `was_rejected_completion=True`, the agent gets an explanation of why its completion wasn't accepted and what evidence to include next time.

2. **Skill-aware coaching** — When an SDLC skill is active:
   - If a plan file has a `## Success Criteria` section that can be parsed with certainty, the criteria are quoted verbatim
   - If the plan file exists but criteria can't be cleanly extracted, the coach points the agent to the file path (never guesses)
   - If no plan file but a skill is detected from the message text, the coach uses the skill's `evidence_hint` from `SKILL_DETECTORS`

3. **Plain continue** (fallback) — Genuine status updates with no dev skill context get "continue" unchanged.

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

### Duplicate Message Fix

The system also fixes a bug where `BackgroundTask._run_work()` would re-send SDK results through `send_to_chat` after auto-continue already handled them. Setting `_completion_sent=True` in the auto-continue path prevents this.

## Error-Classified Output Bypass (Crash Guard)

When the SDK crashes or returns an error, the output classifier labels it as `ERROR`. Error-classified outputs **skip auto-continue entirely** and are sent directly to chat. This prevents the system from endlessly re-enqueuing continuation jobs for a session that will keep crashing.

### Flow

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

## Key Files

| File | Purpose |
|------|---------|
| `bridge/coach.py` | Coaching message builder with three tiers and `SKILL_DETECTORS` mapping |
| `bridge/summarizer.py` | `ClassificationResult.was_rejected_completion` flag |
| `agent/job_queue.py` | Auto-continue wiring, WorkflowState resolution, duplicate suppression |
| `tests/test_coach.py` | Coach module tests (skill detection, criteria extraction, coaching tiers) |
| `agent/sdk_client.py` | Session cleanup on SDK errors (marks sessions as `failed`) |
| `monitoring/session_watchdog.py` | Stale session detection with unique constraint handling |
| `tests/test_auto_continue.py` | Auto-continue duplicate suppression tests |

## Tuning Guide

### Coaching Message Templates

All coaching messages are static templates in `bridge/coach.py`. To adjust:

- **Rejection coaching**: Edit `_build_rejection_coaching()` — explain what happened and what to include
- **Skill coaching with criteria**: Edit `_build_skill_coaching_with_criteria()` — how quoted criteria are presented
- **Skill coaching with file pointer**: Edit `_build_skill_coaching_with_file_pointer()` — fallback when criteria can't be parsed
- **Generic skill coaching**: Edit `_build_generic_skill_coaching()` — uses `evidence_hint` from `SKILL_DETECTORS`

### Hedging Detection Patterns

In `bridge/summarizer.py`, the `_parse_classification_response()` function checks the classifier's reason for hedging patterns:

```python
hedging_patterns = ["hedg", "no evidence", "no proof", "without verification",
                    "unverified", "not verified", "no test", "no command output"]
```

Add patterns here to catch more rejection reasons.

### Plan Success Criteria Extraction

`_extract_success_criteria()` uses regex to find `## Success Criteria` in plan docs. Returns the section content only when parsed with certainty. If the file is missing, malformed, or the section can't be cleanly extracted, it returns `None` and the caller falls back to pointing the agent at the file path.

## Coaching Message Prefix

All coaching messages (except plain "continue") are prefixed with `[System Coach]` so the agent can distinguish coaching from user messages.
