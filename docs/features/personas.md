# Personas

> **Business context:** See [Persona Teamwork](~/work-vault/AI Valor Engels System/Personas/Persona Teamwork.md) in the work vault for the original leadership structure and role definitions (Pullman, Wells, Verne).

Configurable persona system using composable segments and overlays. Identity data lives in structured JSON (`config/identity.json`), behavioral content is split into three composable segments (`config/personas/segments/`), and role-specific overlays define per-persona behavior.

## How It Works

### Segment + Overlay Architecture

The persona system splits content between the repo (shared) and private storage (iCloud-synced):

**In repo** (`config/personas/segments/` and `config/identity.json`):
```
config/
  identity.json                  # Structured identity data (name, email, timezone, org)
  personas/
    segments/
      manifest.json              # Universal segment order (all personas render all segments)
      identity.md                # Who I Am, values, voice, communication style
      work-patterns.md           # Autonomy, permissions, escalation, philosophy, memory
      tools.md                   # MCP servers, dev tools, browser automation, CLI tools
```

**Private** (`~/Desktop/Valor/personas/` and `~/Desktop/Valor/identity.json`):
```
~/Desktop/Valor/
  identity.json                  # Per-instance identity overrides (shallow merge)
  personas/
    developer.md                 # Full system access, autonomous execution, self-management
    project-manager.md           # Triage, routing, GitHub management, communications
    teammate.md                  # Casual conversation, Q&A, helpful and encouraging
```

Segments stay in the repo because they contain general-purpose identity and behavioral content (not private). Identity data is structured JSON, queryable by code, with per-instance overrides via `~/Desktop/Valor/identity.json`. Overlay files contain role-specific strategic context and capabilities, so they live in `~/Desktop/Valor/` (iCloud-synced, not committed to git).

At load time, `load_persona_prompt(persona)` reads `config/identity.json`, assembles all 3 segments from `config/personas/segments/` per `manifest.json`, injects identity fields via `{{identity.*}}` marker substitution, and concatenates the named overlay (`{persona}.md`) from `~/Desktop/Valor/personas/`. The result is a complete system prompt.

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
| `developer` | `~/Desktop/Valor/personas/developer.md` | Full developer with system access, git operations, SDLC pipeline | Dev: groups, AgentSDK subprocesses |
| `project-manager` | `~/Desktop/Valor/personas/project-manager.md` (private) or `config/personas/project-manager.md` (in-repo fallback) | Triage, routing, SDLC gate enforcement, GitHub management | PM: groups, bridge messaging |
| `teammate` | `~/Desktop/Valor/personas/teammate.md` | Casual Q&A, brainstorming, knowledge sharing | DMs, team chats |

## Configuration

### projects.json

Project configuration lives at `~/Desktop/Valor/projects.json` (iCloud-synced, private). Persona selection is configured per-group and for DMs:

```json
{
  "personas": {
    "developer": {"name": "Valor"},
    "project-manager": {"name": "Valor"},
    "teammate": {"name": "Valor"}
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

### File Locations

| File | Location | Why |
|------|----------|-----|
| `identity.json` | `config/identity.json` (in repo) | Structured identity data, shared defaults |
| `identity.json` | `~/Desktop/Valor/identity.json` (iCloud) | Per-instance identity overrides |
| Segments | `config/personas/segments/` (in repo) | Composable behavioral content, shared |
| `project-manager.md` | `config/personas/project-manager.md` (in repo) | In-repo fallback with hard CRITIQUE/REVIEW gate rules -- loaded when private overlay is absent |
| Overlay files | `~/Desktop/Valor/personas/` (iCloud) | Private strategic context (preferred over in-repo fallbacks) |
| `projects.json` | `~/Desktop/Valor/projects.json` (iCloud) | Contains chat IDs, machine names |
| `projects.example.json` | `config/projects.example.json` (in repo) | Sanitized schema for new setups |

## Adding a New Persona

1. Create `~/Desktop/Valor/personas/{persona-name}.md` with role-specific instructions
2. Add the persona entry to `~/Desktop/Valor/projects.json` under `personas`
3. Reference it in the appropriate group or DM config
4. The segment content is automatically assembled and prepended -- no need to duplicate shared content

## Fallback Behavior

| Scenario | Fallback |
|----------|----------|
| Overlay in `~/Desktop/Valor/personas/` missing | Falls back to `config/personas/{persona}.md` (in-repo) |
| Both overlay locations missing | Raises `FileNotFoundError` (no silent fallback) |
| Segment file missing | Raises `FileNotFoundError` with segment path for debugging |
| Unknown persona name | Falls back to `developer` persona with warning |
| Identity config missing | Raises `FileNotFoundError` (identity.json is required) |

## API

```python
from agent.sdk_client import load_identity, load_persona_prompt, load_system_prompt, load_pm_system_prompt

# Load identity data
identity = load_identity()                    # dict with name, email, timezone, etc.

# Load specific persona
prompt = load_persona_prompt("developer")     # segments + developer overlay
prompt = load_persona_prompt("teammate")      # segments + teammate overlay

# System prompt wrappers
prompt = load_system_prompt()                 # developer persona + WORKER_RULES
prompt = load_pm_system_prompt("/path")       # PM persona + work-vault CLAUDE.md
```

`load_pm_system_prompt()` is invoked from `agent/session_executor.py` for PM sessions (issue #1148). The result is passed to `get_response_via_harness(system_prompt=...)` and appended to `claude -p`'s default prompt via `--append-system-prompt`. See `docs/features/harness-abstraction.md#pm-persona-injection-append-system-prompt-issue-1148`.

## Related

- `config/identity.json` -- Structured identity data (name, email, timezone, org)
- `config/personas/segments/` -- Composable prompt segments (identity, work-patterns, tools)
- `docs/features/config-architecture.md` -- Unified config system
- `docs/features/pm-channels.md` -- PM mode channel routing
- `agent/sdk_client.py` -- `load_identity()`, `load_persona_prompt()`, `_resolve_persona()`
- `tests/unit/test_persona_loading.py` -- Test coverage
