# Standardized Enums

## Overview

All magic strings for session types, persona identifiers, and classification types are centralized as Python `StrEnum` members in `config/enums.py`. This replaces scattered string literals across the codebase with type-safe enum imports.

`StrEnum` inherits from `str`, so enum members compare equal to their string values (`SessionType.PM == "pm"` is `True`).

## Enum Types

### SessionType

Discriminator for AgentSession: pm, teammate, or dev.

| Member | Value | Usage |
|--------|-------|-------|
| `SessionType.PM` | `"pm"` | PM session -- PM persona, orchestration, read-only |
| `SessionType.TEAMMATE` | `"teammate"` | Teammate session -- conversational, informational queries |
| `SessionType.DEV` | `"dev"` | DevSession -- Dev persona, full permissions |

### PersonaType

Persona identifiers from projects.json group configuration. This is the sole enum for persona identification -- there is no separate ChatMode enum.

| Member | Value | Usage |
|--------|-------|-------|
| `PersonaType.DEVELOPER` | `"developer"` | Developer persona |
| `PersonaType.PROJECT_MANAGER` | `"project-manager"` | PM persona |
| `PersonaType.TEAMMATE` | `"teammate"` | Teammate persona (informational queries, conversational) |

### ClassificationType

Intent classification results from the work request classifier.

| Member | Value | Usage |
|--------|-------|-------|
| `ClassificationType.SDLC` | `"sdlc"` | Work request routed to SDLC pipeline |
| `ClassificationType.QUESTION` | `"question"` | Informational query, direct response |

## Import Pattern

```python
from config.enums import SessionType, PersonaType, ClassificationType

# Comparisons
if session.session_type == SessionType.PM:
    ...

# Assignments
_session_type = SessionType.DEV

# Persona resolution
persona = resolve_persona(project, chat_title, is_dm)
if persona == PersonaType.TEAMMATE:
    _session_type = SessionType.TEAMMATE
```

## Backward Compatibility

- `SESSION_TYPE_PM` and `SESSION_TYPE_DEV` constants in `models/agent_session.py` are aliases to `SessionType.PM` and `SessionType.DEV`
- The `session_mode` field on AgentSession stores `PersonaType.TEAMMATE` for teammate sessions as a legacy fallback. With `SessionType.TEAMMATE` as a first-class enum value, new code checks `session_type` directly
- Environment variables remain string-typed (`SESSION_TYPE` env var contains `"pm"`, `"teammate"`, or `"dev"`), and `StrEnum` members compare equal to those strings
- The `"passthrough"` return value from `classify_work_request()` is not part of `ClassificationType` -- it is a routing-specific value distinct from intent classification
- A Redis key migration script (`scripts/migrate_session_type_chat_to_pm.py`) handles renaming existing `:chat:` key segments to `:pm:` or `:teammate:`

## Dashboard Changes

The sessions table column previously labeled "Type" is now "Persona" with display values: "dev", "PM", "Teammate". The `_resolve_session_type()` function was renamed to `_resolve_persona_display()` in `ui/data/sdlc.py`. Both the SDLC and sessions tables use matching badge colors (blue for dev, purple for PM, green for Teammate).

## Files Modified

| File | Changes |
|------|---------|
| `config/enums.py` | Enum definitions (SessionType with PM/TEAMMATE/DEV, PersonaType, ClassificationType) |
| `models/agent_session.py` | Enum imports, factory methods (create_pm, create_teammate), properties (is_pm, is_teammate) |
| `bridge/telegram_bridge.py` | SessionType routing: PM, TEAMMATE, or DEV based on persona |
| `bridge/routing.py` | PersonaType, ClassificationType, `resolve_persona()` |
| `bridge/summarizer.py` | session_mode checks with PersonaType.TEAMMATE |
| `agent/sdk_client.py` | SessionType.PM and SessionType.TEAMMATE for routing |
| `agent/agent_session_queue.py` | SessionType.PM defaults, TEAMMATE detection |
| `agent/hooks/pre_tool_use.py` | SessionType.PM for env var comparison |
| `tools/agent_session_scheduler.py` | SessionType choices include PM, TEAMMATE, DEV |
| `ui/data/sdlc.py` | Display function handles "pm", "teammate", "dev" |
| `scripts/migrate_session_type_chat_to_pm.py` | Redis key migration: `:chat:` -> `:pm:` or `:teammate:` |
