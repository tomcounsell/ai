# Config-Driven Chat Mode

## Overview

Chat mode resolution determines how the system handles messages from each Telegram group: whether to spawn a DevSession (full permissions), a ChatSession (PM orchestration), or treat the group as a passive Q&A listener. Previously, this was inferred solely from chat title prefixes (`Dev:`, `PM:`). Config-driven chat mode adds an explicit `persona` field in `projects.json` group configuration, giving operators direct control over per-group routing without relying on naming conventions.

## Config Schema

The `persona` field lives inside the `telegram.groups` dictionary of each project in `projects.json`. Groups can be configured as either a simple list (legacy) or a dictionary with per-group settings (new):

```json
{
  "projects": {
    "my-project": {
      "telegram": {
        "groups": {
          "Dev: MyProject": {"persona": "developer"},
          "PM: MyProject": {"persona": "project-manager"},
          "Team: MyProject": {"persona": "teammate"}
        },
        "mention_triggers": ["@valor", "valor"]
      }
    }
  }
}
```

### Persona Values

| Persona | Resolved Mode | Session Type | Behavior |
|---------|--------------|--------------|----------|
| `"developer"` | `"dev"` | DevSession | Full permissions, dev persona, direct execution |
| `"project-manager"` | `"pm"` | ChatSession | PM persona, SDLC orchestration, spawns DevSessions |
| `"teammate"` | `"qa"` | ChatSession | Passive listener -- only responds on @mention or reply-to-Valor |

The mapping is defined in `PERSONA_TO_MODE` in `bridge/routing.py`.

## Mode Resolution Order

The `resolve_chat_mode()` function in `bridge/routing.py` uses the following priority chain:

1. **DMs** -- always resolve to `"qa"` mode (direct Q&A, no SDLC overhead)
2. **Config persona** -- if the project has a `telegram.groups` dictionary entry matching the chat title with a valid `persona` field, map it to a mode via `PERSONA_TO_MODE`
3. **Title prefix fallback** -- if no persona is configured, `"Dev:"` prefix resolves to `"dev"`, `"PM:"` prefix resolves to `"pm"`
4. **None (unconfigured)** -- no mode determined; caller falls through to existing behavior (intent classifier for ChatSessions, respond_to_all/mention logic for response decisions)

This layered approach ensures full backward compatibility: existing groups that rely on title prefixes continue working without any configuration changes.

## Passive Listener Behavior (Q&A Groups)

When a group resolves to `"qa"` mode (via `"teammate"` persona), the system behaves as a passive listener:

- **Messages are stored** in Redis as usual (TelegramMessage records)
- **No automatic response** -- the system stays completely silent
- **No Ollama classification** -- skips the `classify_needs_response` call entirely
- **@mention triggers response** -- if a message contains a configured mention trigger (e.g., `@valor`), the system responds
- **Reply-to-Valor triggers response** -- replying to a previous Valor message continues the conversation

This is useful for groups where the agent should observe and learn from conversations without interrupting, only engaging when explicitly addressed.

## Integration Points

### Bridge (`bridge/telegram_bridge.py`)

The bridge calls `resolve_chat_mode()` when determining session type for a new job:

- If mode is `"dev"` -> creates a DevSession (session_type="dev")
- Everything else -> creates a ChatSession (session_type="chat")

### SDK Client (`agent/sdk_client.py`)

The SDK client calls `resolve_chat_mode()` inside `get_agent_response_sdk()` when routing ChatSession intent:

- If mode is `"qa"` -> skips the Haiku intent classifier, sets `qa_mode=True` directly (reducing latency and cost)
- If mode is `"pm"` or `"dev"` -> skips the classifier, uses the known mode
- If mode is `None` -> falls through to the existing intent classifier

### Response Decision (`bridge/routing.py::should_respond_async()`)

The async response decision uses `resolve_chat_mode()` to handle Q&A groups:

- If mode is `"qa"` -> only respond on @mention or reply-to-Valor; skip Ollama classification entirely
- Other modes -> fall through to existing response logic (respond_to_all, respond_to_unaddressed, etc.)

## Backward Compatibility

- **List-format groups**: If `telegram.groups` is a list (legacy format), `resolve_chat_mode()` skips the persona lookup and falls through to title prefix matching
- **Dict without persona**: If a group entry is a dict but has no `persona` key, the function falls through to title prefix matching
- **No groups config**: Projects without a `telegram.groups` section use title prefix matching as before

## Key Files

| File | Role |
|------|------|
| `bridge/routing.py` | `resolve_chat_mode()`, `PERSONA_TO_MODE`, passive listener logic in `should_respond_async()` |
| `bridge/telegram_bridge.py` | Session type derivation from resolved mode |
| `agent/sdk_client.py` | Classifier bypass for config-determined modes |
| `tests/unit/test_config_driven_routing.py` | Q&A passive listener, backward compatibility, session type derivation |
| `tests/unit/test_routing_mode.py` | `resolve_chat_mode()` unit tests for all resolution paths |
