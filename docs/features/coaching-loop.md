# Coaching Loop: Context-Aware Auto-Continue Messages

## Overview

When the output classifier downgrades a completion to STATUS_UPDATE (due to hedging or missing evidence), the auto-continue system now sends targeted coaching messages instead of bare "continue". This helps agents self-correct by telling them exactly what evidence to include.

## How It Works

### Three-Tier Coaching

The coaching system (`bridge/coach.py`) builds messages based on context:

1. **Rejection coaching** (highest priority) - When `was_rejected_completion=True`, the agent gets specific guidance about including concrete evidence (test counts, commit hashes, exit codes).

2. **Skill-aware coaching** - When a `/do-build`, `/do-plan`, `/do-test`, or `/do-docs` skill is active, coaching references the plan's success criteria.

3. **Plain continue** (fallback) - Genuine status updates still get "continue" with no change.

### Duplicate Message Fix

The system also fixes a critical bug where `BackgroundTask._run_work()` would re-send SDK results through `send_to_chat` after auto-continue already handled them. Setting `_completion_sent=True` in the auto-continue path prevents this.

## Key Files

| File | Purpose |
|------|---------|
| `bridge/coach.py` | Coaching message builder with three tiers |
| `bridge/summarizer.py` | `ClassificationResult.was_rejected_completion` flag |
| `agent/job_queue.py` | Auto-continue wiring and duplicate suppression |
| `tests/test_coach.py` | Coach module tests |
| `tests/test_auto_continue.py` | Auto-continue duplicate suppression tests |

## Tuning Guide

### Coaching Message Templates

All coaching messages are static templates in `bridge/coach.py`. To adjust:

- **Rejection coaching**: Edit `_build_rejection_coaching()` - change the guidance text
- **Skill coaching**: Edit `_build_skill_coaching()` - change how success criteria are presented
- **Generic skill coaching**: Edit `_build_generic_skill_coaching()`

### Hedging Detection Patterns

In `bridge/summarizer.py`, the `_parse_classification_response()` function checks the classifier's reason for hedging patterns:

```python
hedging_patterns = ["hedg", "no evidence", "no proof", "without verification",
                    "unverified", "not verified", "no test", "no command output"]
```

Add patterns here to catch more rejection reasons.

### Plan Success Criteria Extraction

`_extract_success_criteria()` uses regex to find `## Success Criteria` in plan docs. It returns the section content between that heading and the next `##` heading.

## Coaching Message Prefix

All coaching messages (except plain "continue") are prefixed with `[System Coach]` so the agent can distinguish coaching from user messages.
