# Standardized Enums

## Overview

All magic strings for session types, persona identifiers, and classification types are centralized as Python `StrEnum` members in `config/enums.py`. This replaces scattered string literals across the codebase with type-safe enum imports.

`StrEnum` inherits from `str`, so enum members compare equal to their string values (`SessionType.CHAT == "chat"` is `True`). No Redis data migration is needed -- existing string values in Redis match enum members transparently.

## Enum Types

### SessionType

Discriminator for AgentSession: chat (PM) or dev (developer).

| Member | Value | Usage |
|--------|-------|-------|
| `SessionType.CHAT` | `"chat"` | ChatSession -- PM persona, orchestration |
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
if session.session_type == SessionType.CHAT:
    ...

# Assignments
_session_type = SessionType.DEV

# Persona resolution
persona = resolve_persona(project, chat_title, is_dm)
if persona == PersonaType.TEAMMATE:
    session.session_mode = PersonaType.TEAMMATE
```

## Backward Compatibility

- `SESSION_TYPE_CHAT` and `SESSION_TYPE_DEV` constants in `models/agent_session.py` are now aliases to `SessionType.CHAT` and `SessionType.DEV`
- The `session_mode` field on AgentSession stores `PersonaType.TEAMMATE` for teammate sessions. The legacy `qa_mode` boolean and `ChatMode` enum have been removed entirely
- Environment variables remain string-typed (`SESSION_TYPE` env var still contains `"chat"` or `"dev"`), and `StrEnum` members compare equal to those strings
- The `"passthrough"` return value from `classify_work_request()` is not part of `ClassificationType` -- it is a routing-specific value distinct from intent classification

## Dashboard Changes

The sessions table column previously labeled "Type" is now "Persona" with display values: "Developer", "PM", "Teammate". The `_resolve_session_type()` function was renamed to `_resolve_persona_display()` in `ui/data/sdlc.py`. Both the SDLC and sessions tables use matching badge colors (blue for Developer, purple for PM, green for Teammate).

## Files Modified

| File | Changes |
|------|---------|
| `config/enums.py` | Enum definitions (SessionType, PersonaType, ClassificationType) |
| `models/agent_session.py` | Enum imports, session_mode field |
| `bridge/telegram_bridge.py` | SessionType for session_type assignment |
| `bridge/routing.py` | PersonaType, ClassificationType, `resolve_persona()` |
| `bridge/summarizer.py` | session_mode checks with PersonaType.TEAMMATE |
| `agent/sdk_client.py` | SessionType, PersonaType for comparisons |
| `agent/agent_session_queue.py` | SessionType defaults, session_mode reads |
| `agent/hooks/pre_tool_use.py` | SessionType for env var comparison |
| `tools/job_scheduler.py` | SessionType for choices and defaults |
| `ui/data/sdlc.py` | Renamed display function, unified badge colors |
| `ui/data/machine.py` | PersonaType for sort ordering |
| `ui/templates/_partials/sessions_table.html` | Column rename, badge updates |
