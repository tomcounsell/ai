# PM (Project Manager) Channels

Project manager mode allows Telegram groups to route to work-vault project folders instead of source code repositories, enabling Valor to operate as a project manager rather than a developer.

## How It Works

### Project Mode Field

Each project in `config/projects.json` supports an optional `"mode"` field:

- `"dev"` (default): Standard developer mode with SDLC classification, WORKER_RULES, and branch safety rails
- `"pm"`: Project manager mode that skips SDLC classification and uses work-vault CLAUDE.md for instructions

If the mode field is missing, the project defaults to `"dev"`. Unknown mode values are treated as `"dev"`.

### Routing Flow

**Dev mode (existing behavior):**
1. Message arrives in Telegram group
2. `find_project_for_chat()` matches group to project config
3. `classify_work_request()` determines `sdlc` vs `question` routing
4. SDLC requests go to ai/ repo orchestrator; questions go to project working directory
5. Agent spawns with WORKER_RULES + SOUL.md system prompt

**PM mode (new behavior):**
1. Message arrives in Telegram group (e.g., "PM: Cuttlefish")
2. `find_project_for_chat()` matches group to PM project config
3. `classify_work_request()` is **skipped** -- classification forced to `"question"`
4. Agent spawns with `cwd` set to the work-vault directory (e.g., `~/work-vault/Cuttlefish/`)
5. System prompt uses SOUL.md (persona) + work-vault `CLAUDE.md` (PM instructions), no WORKER_RULES

### System Prompt Composition

PM mode uses `load_pm_system_prompt()` which:
- Loads SOUL.md for Valor's persona and communication style
- Appends the project-specific `CLAUDE.md` from the work-vault directory if it exists
- Does NOT include WORKER_RULES (no branch safety rails needed for PM work)
- Falls back to SOUL.md only if no project CLAUDE.md exists

### What PM Mode Skips

- SDLC classification (`classify_work_request()`)
- WORKER_RULES injection (branch safety rails)
- SDLC directives (no `/sdlc` invocation)
- Target repo context injection

## Configuration

### projects.json Entry

```json
{
  "pm-cuttlefish": {
    "name": "PM: Cuttlefish",
    "description": "Project management for Cuttlefish",
    "mode": "pm",
    "working_directory": "~/work-vault/Cuttlefish/",
    "telegram": {
      "groups": ["PM: Cuttlefish"],
      "respond_to_unaddressed": true,
      "respond_to_dms": false
    },
    "context": {
      "description": "Project management for Cuttlefish."
    }
  }
}
```

### Work-Vault CLAUDE.md

Each work-vault project folder can contain a `CLAUDE.md` with PM-specific instructions. This file tells the agent how to behave as a project manager for that specific project.

Template structure:
- Project overview and goals
- Read-only source repo reference for status checks
- PM-specific do's and don'ts (no code writing)
- Linked resources (issue trackers, documents, etc.)

## Available PM Channels

| Channel | Work Vault Path | Project |
|---------|----------------|---------|
| PM: Cuttlefish | `~/work-vault/Cuttlefish/` | AI tools and MCP Servers |
| PM: SATSOL | `~/work-vault/SATSOL/` | SATSOL |
| PM: Monday Flowers | `~/work-vault/Monday Flowers/` | Monday Flowers |
| PM: PsyOptimal | `~/work-vault/PsyOptimal/` | Mental health platform |
| PM: Royop | `~/work-vault/Royop/` | Royop |

## Key Files

- `agent/sdk_client.py`: PM mode detection, classification bypass, `load_pm_system_prompt()`
- `config/projects.json`: PM project entries with `"mode": "pm"`
- `tests/unit/test_pm_channels.py`: Unit tests for PM mode behavior

## Design Decisions

1. **SOUL.md preserved in PM mode**: The persona (Valor's attitude/style) is valuable even when operating as a PM. Only WORKER_RULES are stripped.
2. **Classification bypass, not a new classification**: PM mode forces `"question"` rather than adding a third classification type, keeping the routing logic simple.
3. **No new tools**: PM behavior is driven entirely by routing and instructions, not new MCP tools or servers.
4. **Work-vault CLAUDE.md is optional**: If a project folder lacks CLAUDE.md, the agent still works with just the SOUL.md persona.
