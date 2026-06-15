# Standardized Enums

## Overview

All magic strings for session types, persona identifiers, access levels, and classification types are centralized as Python `StrEnum` members in `config/enums.py`. This replaces scattered string literals across the codebase with type-safe enum imports.

`StrEnum` inherits from `str`, so enum members compare equal to their string values (`SessionType.ENG == "eng"` is `True`).

## Enum Types

### SessionType

Discriminator for AgentSession: eng, teammate, or granite.

| Member | Value | Usage |
|--------|-------|-------|
| `SessionType.ENG` | `"eng"` | Eng session -- engineer persona, full permissions; handles both SDLC work and conversational responses |
| `SessionType.TEAMMATE` | `"teammate"` | Teammate session -- conversational, informational queries |
| `SessionType.GRANITE` | `"granite"` | Direct invocations of the standalone `valor-granite-loop` CLI (`tools/granite_interactive_tui_poc/cli.py`); labels CLI-originated sessions so they are not misclassified as bridge-originated. Bridge sessions that run through the granite PTY container are typed `ENG`. |

### PersonaType

Persona identifiers from projects.json group configuration. This is the sole enum for persona identification -- there is no separate ChatMode enum.

| Member | Value | Usage |
|--------|-------|-------|
| `PersonaType.ENGINEER` | `"engineer"` | Engineer persona |
| `PersonaType.TEAMMATE` | `"teammate"` | Teammate persona (informational queries, conversational) |
| `PersonaType.CUSTOMER_SERVICE` | `"customer-service"` | Customer-service persona (email-spawned, action-oriented, no code writes) |

### AccessLevel

Prompt-rails layer applied on top of a persona. Orthogonal to `SessionType` (which decides queueing, child-session shape, output handler) and to `PersonaType` (which decides voice and identity). `AccessLevel` decides which safety preamble and appendices wrap the persona when `compose_system_prompt` assembles the final agent system prompt. It is **prompt-only** -- runtime tool restrictions are enforced separately by `agent/hooks/pre_tool_use.py` keyed on `SessionType`.

| Member | Value | Usage |
|--------|-------|-------|
| `AccessLevel.WORKER` | `"worker"` | Full permissions; prepends `WORKER_RULES` (safety rails) and appends principal context plus completion criteria. Maps to `SessionType.ENG` today. |
| `AccessLevel.TEAMMATE` | `"teammate"` | Conversational, no rails. Maps to `SessionType.TEAMMATE` with the teammate persona today. |
| `AccessLevel.CUSTOMER_SERVICE` | `"customer-service"` | Action-oriented, no code writes, no rails. Used by the email-spawned customer-service persona override today. |

### ClassificationType

Intent classification results from the work request classifier.

| Member | Value | Usage |
|--------|-------|-------|
| `ClassificationType.SDLC` | `"sdlc"` | Work request that could result in code changes or a PR |
| `ClassificationType.COLLABORATION` | `"collaboration"` | Direct task the agent can handle without a dev-session |
| `ClassificationType.OTHER` | `"other"` | Ambiguous task; the agent uses judgment |
| `ClassificationType.QUESTION` | `"question"` | Informational query, explanation, or opinion request |

## Import Pattern

```python
from config.enums import SessionType, PersonaType, AccessLevel, ClassificationType

# Comparisons
if session.session_type == SessionType.ENG:
    ...

# Assignments
_session_type = SessionType.ENG

# Persona resolution
persona = resolve_persona(project, chat_title, is_dm)
if persona == PersonaType.TEAMMATE:
    _session_type = SessionType.TEAMMATE
```

## Convenience Aliases

- `SESSION_TYPE_ENG` and `SESSION_TYPE_TEAMMATE` constants in `models/agent_session.py` alias `SessionType.ENG` and `SessionType.TEAMMATE` for internal use by the model's factory methods and properties. New code should import directly from `config.enums`.
- The `session_mode` field on AgentSession stores `PersonaType.TEAMMATE` for teammate sessions as a legacy fallback. With `SessionType.TEAMMATE` as a first-class enum value, new code checks `session_type` directly.
- Environment variables remain string-typed (the `SESSION_TYPE` env var contains `"eng"`, `"teammate"`, or `"granite"`), and `StrEnum` members compare equal to those strings.
- The `"passthrough"` return value from `classify_work_request()` is not part of `ClassificationType` -- it is a routing-specific value distinct from intent classification.
- A Redis key migration script (`scripts/migrate_session_type_pm_to_eng.py`) renames existing `:pm:`/`:dev:` key segments to `:eng:`.

## Dashboard Display

`session_type` is the sole discriminator for the dashboard sessions table. The `_resolve_persona_display()` function in `ui/data/sdlc.py` maps `session_type="eng"` to "Engineer" and `session_type="teammate"` to "Teammate".

## Files Modified

| File | Changes |
|------|---------|
| `config/enums.py` | Enum definitions (SessionType with ENG/TEAMMATE/GRANITE, PersonaType, AccessLevel, ClassificationType) |
| `models/agent_session.py` | Enum imports, `SESSION_TYPE_ENG`/`SESSION_TYPE_TEAMMATE` aliases, factory methods (`create_eng`, `create_teammate`), properties (`is_eng`, `is_teammate`) |
| `bridge/telegram_bridge.py` | SessionType routing: ENG or TEAMMATE based on persona |
| `bridge/routing.py` | PersonaType, ClassificationType, `resolve_persona()` (DMs -> TEAMMATE, `Eng:`-prefixed titles -> ENGINEER) |
| `bridge/message_drafter.py` | session_mode checks with PersonaType.TEAMMATE |
| `agent/sdk_client.py` | SessionType.ENG and SessionType.TEAMMATE for persona/access mapping and routing |
| `agent/agent_session_queue.py` | SessionType.ENG default, TEAMMATE detection |
| `agent/hooks/pre_tool_use.py` | Compares the `SESSION_TYPE` env var to `SessionType.TEAMMATE` for tool restrictions |
| `tools/agent_session_scheduler.py` | SessionType choices include ENG and TEAMMATE; defaults to ENG |
| `ui/data/sdlc.py` | `_resolve_persona_display()` maps "eng" -> "Engineer", "teammate" -> "Teammate" |
| `scripts/migrate_session_type_pm_to_eng.py` | Redis key migration: `:pm:`/`:dev:` -> `:eng:` |
