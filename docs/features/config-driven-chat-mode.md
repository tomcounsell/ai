# Config-Driven Chat Mode

## Overview

Chat mode resolution determines how the system handles messages from each Telegram group: whether to spawn an Eng session (full permissions, handles both SDLC work and conversational responses) or treat the group as a passive teammate listener. Previously, this was inferred solely from chat title prefixes (the single `Eng:` prefix today). Config-driven chat mode adds an explicit `persona` field in `projects.json` group configuration, giving operators direct control over per-group routing without relying on naming conventions.

## Config Schema

The `persona` field lives inside the `telegram.groups` dictionary of each project in `projects.json`. Groups can be configured as either a simple list (legacy) or a dictionary with per-group settings (new):

```json
{
  "projects": {
    "my-project": {
      "telegram": {
        "groups": {
          "Eng: MyProject": {"persona": "engineer"},
          "Team: MyProject": {"persona": "teammate"}
        },
        "mention_triggers": ["@valor", "valor"]
      }
    }
  }
}
```

### Persona Values

| Persona | Resolved Persona | Session Type | Behavior |
|---------|-----------------|--------------|----------|
| `"engineer"` | `PersonaType.ENGINEER` | Eng session | Full permissions, engineer persona, handles both SDLC work and conversational responses |
| `"teammate"` | `PersonaType.TEAMMATE` | Teammate session | Passive listener -- only responds on @mention or reply-to-Valor |
| `"customer-service"` | `PersonaType.CUSTOMER_SERVICE` | Teammate session | Action-oriented, no code writes; used by the email-spawned customer-service override |

`PersonaType` is defined in `config/enums.py` (`ENGINEER`, `TEAMMATE`, `CUSTOMER_SERVICE`). The group-config mapping is handled by `resolve_persona()` in `bridge/routing.py`, which returns a `PersonaType` directly. Note that `customer-service` is not selected via the Telegram `groups` persona field today -- it is resolved by `agent/sdk_client.py` from an `email.persona` override for email-transport teammate sessions.

## Mode Resolution Order

The `resolve_persona()` function in `bridge/routing.py` uses the following priority chain:

1. **DMs** -- use the project's `telegram.dm_persona` if configured (parsed as a `PersonaType`), otherwise default to `PersonaType.TEAMMATE` (direct teammate mode, no SDLC overhead)
2. **Config persona** -- if the project has a `telegram.groups` dictionary entry matching the chat title with a valid `persona` field, return the corresponding `PersonaType`
3. **Title prefix fallback** -- if no persona is configured, the `"Eng:"` prefix resolves to `PersonaType.ENGINEER`
4. **None (unconfigured)** -- no persona determined; caller falls through to existing behavior (respond_to_all/mention logic for response decisions)

This layered approach ensures full backward compatibility: existing groups that rely on title prefixes continue working without any configuration changes.

## Passive Listener Behavior (Teammate Groups)

When a group resolves to `PersonaType.TEAMMATE` (via `"teammate"` persona config), the system behaves as a passive listener:

- **Messages are stored** in Redis as usual (TelegramMessage records)
- **No automatic response** -- the system stays completely silent
- **No LLM classification** -- skips the `classify_needs_response` call entirely
- **@mention triggers response** -- if a message contains a configured mention trigger (e.g., `@valor`), the system responds
- **Reply-to-Valor triggers response** -- replying to a previous Valor message continues the conversation

This is useful for groups where the agent should observe and learn from conversations without interrupting, only engaging when explicitly addressed.

## Integration Points

### Bridge (`bridge/telegram_bridge.py`)

The bridge calls `resolve_persona()` when determining session type for a new session:

- If persona is `PersonaType.ENGINEER` (or unconfigured) -> creates an Eng session (`session_type="eng"`). The Eng session handles both SDLC work and conversational responses with full permissions.
- If persona is `PersonaType.TEAMMATE` -> creates a Teammate session (`session_type="teammate"`). Handles informational queries directly, restricted writes.

### SDK Client (`agent/sdk_client.py`)

`resolve_persona_and_access` in `agent/sdk_client.py` maps the resolved `session_type` to a `(PersonaType, AccessLevel, persona_override)` tuple:

- `SessionType.ENG` -> `(PersonaType.ENGINEER, AccessLevel.WORKER, None)` — full permissions, engineer persona
- `SessionType.TEAMMATE` (default) -> `(PersonaType.TEAMMATE, AccessLevel.TEAMMATE, None)` — conversational, no rails
- `SessionType.TEAMMATE` with `transport == "email"` and a project `email.persona` override -> parses that override (e.g. `customer-service` -> `PersonaType.CUSTOMER_SERVICE`)

For teammate routing, the response-decision path in `bridge/routing.py::should_respond_async()` calls `resolve_persona()` directly: a `PersonaType.TEAMMATE` group short-circuits to mention/reply-only without invoking the `classify_needs_response` LLM classifier.

### Response Decision (`bridge/routing.py::should_respond_async()`)

The async response decision uses `resolve_persona()` to handle teammate groups:

- If persona is `PersonaType.TEAMMATE` -> only respond on @mention or reply-to-Valor; skip LLM classification entirely
- Other personas -> fall through to existing response logic (respond_to_all, respond_to_unaddressed, etc.)

## Backward Compatibility

- **List-format groups**: If `telegram.groups` is a list (legacy format), `resolve_persona()` skips the persona lookup and falls through to title prefix matching
- **Dict without persona**: If a group entry is a dict but has no `persona` key, the function falls through to title prefix matching
- **No groups config**: Projects without a `telegram.groups` section use title prefix matching as before

## Key Files

| File | Role |
|------|------|
| `bridge/routing.py` | `resolve_persona()`, passive listener logic in `should_respond_async()` |
| `bridge/telegram_bridge.py` | Session type derivation from resolved persona |
| `agent/sdk_client.py` | Classifier bypass for config-determined personas |
| `tests/unit/test_config_driven_routing.py` | Teammate passive listener, backward compatibility, session type derivation |
| `tests/unit/test_routing_mode.py` | `resolve_persona()` unit tests for all resolution paths |
