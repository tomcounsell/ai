# Issue Classification

Automatic classification of incoming messages as `bug`, `feature`, or `chore`, with immutability enforcement and reclassification support.

## Overview

When a Telegram message arrives, the bridge automatically classifies it using Claude Haiku before a plan is created. This classification is stored in session metadata and pre-populated in the plan template, reducing manual effort during the planning phase.

## Components

### Auto-Classification (Bridge)

The bridge's `classify_and_update_reaction()` function in `bridge/telegram_bridge.py` runs two parallel classification tasks:

1. **Intent classification** (existing) - determines the processing emoji reaction via Ollama
2. **Work type classification** (new) - calls `classify_request_async()` from `tools/classifier.py` to determine bug/feature/chore

The work type result is stored in a mutable dict and passed to `enqueue_job()` as `classification_type`. Both classifications run as a single `asyncio.create_task()`, keeping message intake non-blocking.

If classification fails, the field stays `null` and the user specifies the type manually during planning.

### Session Metadata Fields

**AgentSession** (`models/sessions.py`):
- `classification_type` - The classified type: `bug`, `feature`, or `chore` (nullable)
- `classification_confidence` - Confidence score from 0.0 to 1.0 (nullable)

**RedisJob** (`agent/job_queue.py`):
- `classification_type` - Passthrough field so the worker can store the classification on the AgentSession

Both fields are nullable and backward-compatible with existing sessions/jobs.

### Do-Plan Pre-Population

The `/do-plan` skill (`.claude/skills/do-plan/SKILL.md`) checks for `classification_type` in the session context when creating a new plan. If available, it pre-populates the `type:` field in the frontmatter template. The user can override this during drafting.

### Type Immutability Hook

`.claude/hooks/validators/validate_type_immutability.py` prevents changing the `type:` field once a plan's status has moved past `Planning`.

**Locked statuses:** `Ready`, `In Progress`, `Complete`

The hook:
1. Reads the current file and the git HEAD version
2. Extracts `status:` and `type:` from both versions' frontmatter
3. If the HEAD version has a locked status and the type changed, exits with code 2 (blocking the save)

This is registered as a Stop hook in the do-plan skill.

### Reclassify Skill

`.claude/skills/reclassify/SKILL.md` provides a `/reclassify <type>` command for changing a plan's type during the Planning phase.

**Usage:** `/reclassify bug` or `/reclassify feature` or `/reclassify chore`

**Rules:**
- Only accepts `bug`, `feature`, or `chore`
- Plan status must be `Planning` - rejects if `Ready` or beyond
- Updates the frontmatter `type:` field and commits the change

## Data Flow

```
Telegram message
  -> bridge handler
  -> classify_and_update_reaction() [asyncio.create_task]
     -> classify_request_async(clean_text)  [Haiku API]
     -> store in classification_result dict
  -> enqueue_job(classification_type=...)
  -> _execute_job() stores on AgentSession
  -> /do-plan reads classification_type from session context
  -> pre-populates type: in plan frontmatter
```

## Related Files

| File | Purpose |
|------|---------|
| `bridge/telegram_bridge.py` | Auto-classification integration point |
| `tools/classifier.py` | Classification engine (Haiku API) |
| `models/sessions.py` | AgentSession with classification fields |
| `agent/job_queue.py` | RedisJob with classification passthrough |
| `.claude/hooks/validators/validate_type_immutability.py` | Immutability enforcement |
| `.claude/skills/reclassify/SKILL.md` | Reclassification during Planning |
| `.claude/skills/do-plan/SKILL.md` | Pre-population of type field |
