# Standardized Enums

## Overview

All magic strings for session types, persona identifiers, classification types, and chat modes are centralized as Python `StrEnum` members in `config/enums.py`. This replaces scattered string literals across the codebase with type-safe enum imports.

`StrEnum` inherits from `str`, so enum members compare equal to their string values (`SessionType.CHAT == "chat"` is `True`). No Redis data migration is needed -- existing string values in Redis match enum members transparently.

## Enum Types

### SessionType

Discriminator for AgentSession: chat (PM) or dev (developer).

| Member | Value | Usage |
|--------|-------|-------|
| `SessionType.CHAT` | `"chat"` | ChatSession -- PM persona, orchestration |
| `SessionType.DEV` | `"dev"` | DevSession -- Dev persona, full permissions |

### PersonaType

Persona identifiers from projects.json group configuration.

| Member | Value | Usage |
|--------|-------|-------|
| `PersonaType.DEVELOPER` | `"developer"` | Developer persona |
| `PersonaType.PROJECT_MANAGER` | `"project-manager"` | PM persona |
| `PersonaType.TEAMMATE` | `"teammate"` | Teammate/Q&A persona |

### ClassificationType

Intent classification results from the work request classifier.

| Member | Value | Usage |
|--------|-------|-------|
| `ClassificationType.SDLC` | `"sdlc"` | Work request routed to SDLC pipeline |
| `ClassificationType.QUESTION` | `"question"` | Informational query, direct response |

### ChatMode

Resolved chat mode from config, title prefix, or DM detection.

| Member | Value | Usage |
|--------|-------|-------|
| `ChatMode.QA` | `"qa"` | Q&A mode (DMs, teammate persona) |
| `ChatMode.PM` | `"pm"` | PM mode (project-manager persona) |
| `ChatMode.DEV` | `"dev"` | Dev mode (developer persona) |

## Import Pattern

```python
from config.enums import SessionType, PersonaType, ClassificationType, ChatMode

# Comparisons
if session.session_type == SessionType.CHAT:
    ...

# Assignments
_session_type = SessionType.DEV

# Dict keys
PERSONA_TO_MODE = {
    PersonaType.TEAMMATE: ChatMode.QA,
    PersonaType.PROJECT_MANAGER: ChatMode.PM,
    PersonaType.DEVELOPER: ChatMode.DEV,
}
```

## Backward Compatibility

- `SESSION_TYPE_CHAT` and `SESSION_TYPE_DEV` constants in `models/agent_session.py` are now aliases to `SessionType.CHAT` and `SessionType.DEV`
- The `qa_mode` boolean field on AgentSession is replaced by `session_mode` (stores `ChatMode.QA` or `None`). A backward-compatible `qa_mode` property reads `session_mode` first, then falls back to the legacy `_qa_mode_legacy` field for pre-migration Redis sessions
- Environment variables remain string-typed (`SESSION_TYPE` env var still contains `"chat"` or `"dev"`), and `StrEnum` members compare equal to those strings
- The `"passthrough"` return value from `classify_work_request()` is not part of `ClassificationType` -- it is a routing-specific value distinct from intent classification

## Dashboard Changes

The sessions table column previously labeled "Type" is now "Persona" with capitalized display values: "Dev", "PM", "Q&A". The `_resolve_session_type()` function was renamed to `_resolve_persona_display()` in `ui/data/sdlc.py`.

## Files Modified

| File | Changes |
|------|---------|
| `config/enums.py` | New: enum definitions |
| `models/agent_session.py` | Enum imports, session_mode field, qa_mode property |
| `bridge/telegram_bridge.py` | SessionType for session_type assignment |
| `bridge/routing.py` | PersonaType, ChatMode, ClassificationType |
| `bridge/summarizer.py` | session_mode checks with qa_mode fallback |
| `agent/sdk_client.py` | SessionType, ChatMode for comparisons |
| `agent/job_queue.py` | SessionType defaults, session_mode reads |
| `agent/hooks/pre_tool_use.py` | SessionType for env var comparison |
| `tools/job_scheduler.py` | SessionType for choices and defaults |
| `ui/data/sdlc.py` | Renamed display function |
| `ui/data/machine.py` | PersonaType for sort ordering |
| `ui/templates/_partials/sessions_table.html` | Column rename, badge updates |
