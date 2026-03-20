# Personas

Configurable persona system that replaces the monolithic `config/SOUL.md` with layered base + overlay files. Each persona defines role-specific behavior on top of a shared identity.

## How It Works

### Base + Overlay Architecture

```
config/personas/
  _base.md           # Shared identity, values, communication style, tools, philosophy
  developer.md       # Full system access, autonomous execution, self-management
  project-manager.md # Triage, routing, GitHub management, communications
  teammate.md        # Casual conversation, Q&A, helpful and encouraging
```

At load time, `load_persona_prompt(persona)` reads `_base.md` and concatenates the named overlay (`{persona}.md`). The result is a complete system prompt.

### Persona Selection

The bridge resolves which persona to use based on:

1. **DMs**: Uses `dm_persona` from project config (default: `"teammate"`)
2. **PM mode projects**: Always `"project-manager"`
3. **Group chats**: Looks up `persona` field in `telegram.groups[chat_title]` config
4. **Default**: `"developer"`

Resolution is handled by `_resolve_persona()` in `agent/sdk_client.py`.

### System Prompt Composition

Each mode wraps the persona prompt differently:

| Mode | Prompt Structure |
|------|-----------------|
| Developer (default) | `WORKER_RULES` + `---` + persona prompt + principal context + completion criteria |
| PM mode | persona prompt + work-vault `CLAUDE.md` (no WORKER_RULES) |
| Teammate (DMs) | persona prompt only (no WORKER_RULES) |

## Available Personas

| Persona | File | Role | Used By |
|---------|------|------|---------|
| `developer` | `config/personas/developer.md` | Full developer with system access, git operations, SDLC pipeline | Dev: groups, AgentSDK subprocesses |
| `project-manager` | `config/personas/project-manager.md` | Triage, routing, Observer duties, GitHub management | PM: groups, bridge messaging |
| `teammate` | `config/personas/teammate.md` | Casual Q&A, brainstorming, knowledge sharing | DMs, team chats |

## Configuration

### projects.json

Persona selection is configured per-group and for DMs in `config/projects.json`:

```json
{
  "personas": {
    "developer": {"name": "Valor", "soul": "config/personas/developer.md"},
    "project-manager": {"name": "Valor", "soul": "config/personas/project-manager.md"},
    "teammate": {"name": "Valor", "soul": "config/personas/teammate.md"}
  },
  "projects": {
    "valor": {
      "telegram": {
        "groups": {
          "Dev: Valor": {"chat_id": -123, "persona": "developer"},
          "PM: Valor": {"chat_id": -456, "persona": "project-manager"}
        },
        "dm_persona": "teammate"
      }
    }
  }
}
```

## Adding a New Persona

1. Create `config/personas/{persona-name}.md` with role-specific instructions
2. Add the persona entry to `config/projects.json` under `personas`
3. Reference it in the appropriate group or DM config
4. The `_base.md` content is automatically prepended -- no need to duplicate shared content

## Fallback Behavior

| Scenario | Fallback |
|----------|----------|
| Persona overlay file missing | Falls back to `config/SOUL.md` with warning log |
| `_base.md` missing | Raises `FileNotFoundError` (base is required) |
| Unknown persona name | Falls back to `developer` persona with warning |
| Entire persona system missing | `load_system_prompt()` catches `FileNotFoundError` and falls back to `SOUL.md` |

## API

```python
from agent.sdk_client import load_persona_prompt, load_system_prompt

# Load specific persona
prompt = load_persona_prompt("developer")     # base + developer overlay
prompt = load_persona_prompt("teammate")      # base + teammate overlay

# Legacy wrappers (still work)
prompt = load_system_prompt()                 # developer persona + WORKER_RULES
prompt = load_pm_system_prompt("/path")       # PM persona + work-vault CLAUDE.md
```

## Related

- `config/SOUL.md` -- Original monolithic prompt (used as fallback when persona files are missing)
- `docs/features/config-architecture.md` -- Unified config system
- `docs/features/pm-channels.md` -- PM mode channel routing
- `agent/sdk_client.py` -- `load_persona_prompt()`, `_resolve_persona()`
- `tests/unit/test_persona_loading.py` -- Test coverage
