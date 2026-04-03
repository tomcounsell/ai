# Issue Classification

Automatic classification of incoming messages as `bug`, `feature`, or `chore`, with immutability enforcement and reclassification support.

## Overview

When a Telegram message arrives, the bridge automatically classifies it using Claude Haiku before a plan is created. This classification is stored in session metadata and pre-populated in the plan template, reducing manual effort during the planning phase.

## Components

### Auto-Classification (Bridge)

The bridge's message handler in `bridge/telegram_bridge.py` runs classification as a background task:

1. **Emoji reaction** -- selected via embedding cosine similarity in `tools/emoji_embedding.py` (under 50ms, separate from classification)
2. **Work type classification** -- calls `classify_request_async()` from `tools/classifier.py` to determine bug/feature/chore

The work type result is stored in a mutable dict and passed to `enqueue_agent_session()` as `classification_type`. Classification runs as an `asyncio.create_task()`, keeping message intake non-blocking.

If classification fails, the field stays `null` and the user specifies the type manually during planning.

### Synchronous Fast-Path for PR/Issue References

Messages containing PR or issue references (e.g., "Complete PR 478", "fix issue #463", "#471") are always SDLC work. A synchronous regex check runs **before** `enqueue_agent_session()` to set `classification_result["type"] = "sdlc"` immediately, bypassing the async classifier race condition. This fast-path uses the same regex as `classify_work_request()` in `bridge/routing.py`. A matching fallback exists in `agent/sdk_client.py` for belt-and-suspenders defense.

### Classification Inheritance on Reply-to-Resume

When a user resumes a session by replying to a previous message (`is_reply_to_valor`), the async classifier may not have completed before `enqueue_agent_session()` is called, leaving `classification_type` as `None`. To prevent this race condition, the bridge inherits the classification from the original session:

1. If `is_reply_to_valor` and `classification_result` has no `type` yet, the bridge queries the existing `AgentSession` by `session_id`
2. If found, the original session's `classification_type` is copied into the mutable `classification_result` dict
3. If the async classifier completes first (populating `classification_result` before the inheritance check), the inherited value is never set

This ensures reply-to-resume messages always carry the correct classification, preventing misrouting of SDLC sessions.

### Session Metadata Fields

**AgentSession** (`models/agent_session.py`):
- `classification_type` - The classified type: `bug`, `feature`, or `chore` (nullable)
- `classification_confidence` - Confidence score from 0.0 to 1.0 (nullable)

The unified `AgentSession` model carries classification fields through the full lifecycle — from enqueue through completion — eliminating the previous need for a separate passthrough field.

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
  -> synchronous fast-path: PR/issue regex → set "sdlc" immediately
  -> if reply-to-resume and classification_result empty:
     -> inherit classification_type from existing AgentSession
  -> enqueue_agent_session(classification_type=...)
  -> _execute_agent_session() stores on AgentSession
  -> /do-plan reads classification_type from session context
  -> pre-populates type: in plan frontmatter
```

## Related Files

| File | Purpose |
|------|---------|
| `bridge/telegram_bridge.py` | Auto-classification integration point |
| `tools/classifier.py` | Classification engine (Haiku API) |
| `models/agent_session.py` | AgentSession with classification fields |
| `agent/agent_session_queue.py` | Job queue using AgentSession model |
| `.claude/hooks/validators/validate_type_immutability.py` | Immutability enforcement |
| `.claude/skills/reclassify/SKILL.md` | Reclassification during Planning |
| `.claude/skills/do-plan/SKILL.md` | Pre-population of type field |
