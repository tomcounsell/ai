# PM (Project Manager) Channels

Project manager mode allows Telegram groups to route to work-vault project folders instead of source code repositories, enabling Valor to operate as a project manager rather than a developer.

## How It Works

### Project Mode Field

Each project in `~/Desktop/Valor/projects.json` supports an optional `"mode"` field:

- `"dev"` (default): Standard developer mode with SDLC classification, WORKER_RULES, and branch safety rails
- `"pm"`: Project manager mode that skips SDLC classification and uses work-vault CLAUDE.md for instructions

If the mode field is missing, the project defaults to `"dev"`. Unknown mode values are treated as `"dev"`.

### Routing Flow

**Dev mode (existing behavior):**
1. Message arrives in Telegram group
2. `find_project_for_chat()` matches group to project config
3. `classify_work_request()` determines `sdlc` vs `question` routing
4. SDLC requests go to ai/ repo orchestrator; questions go to project working directory
5. Agent spawns with WORKER_RULES + persona segments system prompt

**PM mode (new behavior):**
1. Message arrives in Telegram group (e.g., "PM: Cuttlefish")
2. `find_project_for_chat()` matches group to PM project config
3. `classify_work_request()` is **skipped** -- classification forced to `"question"`
4. Agent spawns with `cwd` set to the work-vault directory (e.g., `~/work-vault/Cuttlefish/`)
5. System prompt uses persona segments + work-vault `CLAUDE.md` (PM instructions), no WORKER_RULES

### System Prompt Composition

PM mode uses `load_pm_system_prompt()` which:
- Loads the project-manager persona (base + PM overlay via `load_persona_prompt("project-manager")`)
- Appends the project-specific `CLAUDE.md` from the work-vault directory if it exists
- Does NOT include WORKER_RULES (no branch safety rails needed for PM work)
- Raises `FileNotFoundError` if persona segments are missing (no silent fallback)

The composed prompt is wired into `claude -p` via `--append-system-prompt` (issue #1148). `agent/session_executor.py` calls `load_pm_system_prompt(working_dir)` for PM sessions and passes the result through `get_response_via_harness(system_prompt=...)`. The harness appends it to Claude Code's default system prompt so the PM persona is additive guidance rather than a full replacement — see `docs/features/harness-abstraction.md#pm-persona-injection-append-system-prompt-issue-1148` for the argv-level details.

Prior to #1148, the harness path silently dropped the persona entirely (the `_harness_env` did not carry `SESSION_TYPE` and the `claude -p` argv did not carry `--append-system-prompt`). PM sessions ran without their orchestration rules and the `_is_pm_session()` hook gate was bypassed. The fix restores parity with the SDK-era `ValorAgent.system_prompt` path.

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
- `~/Desktop/Valor/projects.json`: PM project entries with `"mode": "pm"`
- `tests/unit/test_pm_channels.py`: Unit tests for PM mode behavior

## Design Decisions

1. **Persona preserved in PM mode**: The base persona (Valor's identity/style) is valuable even when operating as a PM. PM mode uses the `project-manager` overlay instead of `developer`, and WORKER_RULES are stripped.
2. **Classification bypass, not a new classification**: PM mode forces `"question"` rather than adding a third classification type, keeping the routing logic simple.
3. **No new tools**: PM behavior is driven entirely by routing and instructions, not new MCP tools or servers.
4. **Work-vault CLAUDE.md is optional**: If a project folder lacks CLAUDE.md, the agent still works with just the persona segments.
