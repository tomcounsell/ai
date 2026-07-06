# Personas

> **Business context:** See [Persona Teamwork](~/work-vault/AI Valor Engels System/Personas/Persona Teamwork.md) in the work vault for the original leadership structure and role definitions (Pullman, Wells, Verne).

Configurable persona system using composable segments and overlays. Identity data lives in structured JSON (`config/identity.json`), behavioral content is split into three composable segments (`config/personas/segments/`), and role-specific overlays define per-persona behavior.

There are three personas: **engineer** (the default for development work — orchestrates and executes the SDLC pipeline), **teammate** (conversational Q&A in DMs and team chats), and **customer-service** (action-oriented, no code writes, used for email-spawned sessions). A persona decides voice and identity; the orthogonal `AccessLevel` (see `config/enums.py`) decides which safety preamble and appendices wrap it.

## How It Works

### Segment + Overlay Architecture

The persona system splits content between the repo (shared) and private storage (iCloud-synced):

**In repo** (`config/personas/segments/`, `config/personas/*.md`, and `config/identity.json`):
```
config/
  identity.json                  # Structured identity data (name, email, timezone, org)
  personas/
    engineer.md                  # In-repo engineer overlay (SDLC orchestration + execution)
    customer-service.md          # In-repo customer-service overlay (email-spawned sessions)
    segments/
      manifest.json              # Universal segment order (all personas render all segments)
      identity.md                # Who I Am, values, voice, communication style
      work-patterns.md           # Autonomy, permissions, escalation, philosophy, memory
      tools.md                   # MCP servers, dev tools, browser automation, CLI tools
```

As of issue #1692, `teammate.md` is now also tracked in the repo at
`config/personas/teammate.md` and is no longer vault-only.

**Private** (`~/Desktop/Valor/personas/` and `~/Desktop/Valor/identity.json`):
```
~/Desktop/Valor/
  identity.json                  # Per-instance identity overrides (shallow merge)
  personas/
    engineer.md                  # Full system access, SDLC pipeline, autonomous execution
    teammate.md                  # Casual conversation, Q&A, helpful and encouraging
    customer-service.md          # Professional, action-oriented, no code writes
```

Segments stay in the repo because they contain general-purpose identity and behavioral content (not private). Identity data is structured JSON, queryable by code, with per-instance overrides via `~/Desktop/Valor/identity.json`. Overlay files contain role-specific strategic context and capabilities, so the private copies live in `~/Desktop/Valor/` (iCloud-synced, preferred over the in-repo copies).

At load time, `load_persona_prompt(persona)` reads `config/identity.json`, assembles all 3 segments from `config/personas/segments/` per `manifest.json`, injects identity fields via `{{identity.*}}` marker substitution, and concatenates the named overlay (`{persona}.md`). The result is a complete persona prompt. Overlay resolution checks `~/Desktop/Valor/personas/{persona}.md` first, then falls back to `config/personas/{persona}.md` (`_resolve_overlay_path`, `agent/sdk_client.py:878`).

### Persona Selection

Two resolution sites cover different transports.

**Harness path (the live one)** — `agent/session_executor.py` derives the `(persona, access_level)` pair for every harness invocation via `_resolve_compose_args()` (`agent/sdk_client.py:1118`). The mapping:

1. `session_type=ENG` → `(engineer, WORKER)` (source = `session_type=eng`).
2. `transport=email` with `project.email.persona` set → that persona, with `teammate` as the fallback (source = `project.email.persona` or `email-default`). The email override lives only in `_resolve_compose_args`.
3. `session_type=TEAMMATE` (Telegram DM / teammate) → `(teammate, TEAMMATE)` (source = `session_type=teammate`).
4. Unknown session type → resolved via `_resolve_persona(project, chat_title, is_dm)` (`agent/sdk_client.py:2084`), which defaults to `engineer` for groups and `teammate` for DMs.

The chosen name is logged at INFO BEFORE any disk read (`agent/session_executor.py:1678`):

```
agent.session_executor INFO [<cid|project>] Resolved persona for session=<sid>: <name|<none>> (source=<source>)
```

See [bridge-worker-architecture.md](bridge-worker-architecture.md) and [email-bridge.md](email-bridge.md#persona-resolution-for-email-spawned-sessions) for the full rule.

**Bridge routing path** — `bridge/routing.py::resolve_persona()` maps an incoming Telegram message to a `PersonaType` for the bridge's chat-mode decision (deliver vs. @mention-only vs. silent). Resolution order (`bridge/routing.py:339`):

1. DMs → per-project `telegram.dm_persona` if configured, else `PersonaType.TEAMMATE`.
2. Group `persona` field in `projects.json` → that `PersonaType` directly.
3. Title prefix `Eng:` → `PersonaType.ENGINEER` (`is_team_chat()` treats any title WITHOUT the `Eng:` prefix as a mention-only team chat, `bridge/routing.py:327`).
4. Otherwise `None` (unconfigured — caller falls through to existing classifier behavior).

**SDK path** — `_resolve_persona()` in `agent/sdk_client.py` is the legacy resolver retained for non-harness callers (e.g. drafter call sites). It is not the live persona-selection path for agent sessions; the harness path above is.

### System Prompt Composition

`compose_system_prompt(persona, access_level, ...)` (`agent/sdk_client.py:1014`) is the single composer. It assembles the persona prompt (identity + segments + overlay) and then wraps it according to the `AccessLevel`:

| Access level | Persona (today) | Prompt structure |
|--------------|-----------------|------------------|
| `WORKER` | engineer | `WORKER_RULES` + `---` + persona prompt + principal context + completion criteria (+ work-vault `CLAUDE.md` when a `working_directory` is provided and the file exists) |
| `TEAMMATE` | teammate | persona prompt only (no rails, no appendices) |
| `CUSTOMER_SERVICE` | customer-service | persona prompt only (no rails, no appendices) |

`AccessLevel` is prompt-only. Runtime tool restrictions are enforced separately by `agent/hooks/pre_tool_use.py`, keyed on `SessionType`.

## Available Personas

| Persona | In-repo overlay | Private overlay | Role | Used by |
|---------|-----------------|-----------------|------|---------|
| `engineer` | `config/personas/engineer.md` | `~/Desktop/Valor/personas/engineer.md` | Full system access, git operations, SDLC pipeline orchestration and execution | Eng sessions; `Eng:` groups; default for unknown group session types |
| `teammate` | (none) | `~/Desktop/Valor/personas/teammate.md` | Casual Q&A, brainstorming, knowledge sharing | DMs, team chats (non-`Eng:` groups) |
| `customer-service` | `config/personas/customer-service.md` | `~/Desktop/Valor/personas/customer-service.md` | Professional, action-oriented, no code writes | Email-spawned sessions when `project.email.persona` is set |

## Configuration

### projects.json

Project configuration lives at `~/Desktop/Valor/projects.json` (iCloud-synced, private). Persona selection is configured per-group and for DMs:

```json
{
  "personas": {
    "engineer": {"name": "Valor"},
    "teammate": {"name": "Valor"},
    "customer-service": {"name": "Valor"}
  },
  "projects": {
    "valor": {
      "telegram": {
        "groups": {
          "Eng: Valor": {"chat_id": -123, "persona": "engineer"}
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
| `engineer.md` | `config/personas/engineer.md` (in repo) | In-repo engineer overlay with CRITIQUE/REVIEW gate rules — loaded when private overlay is absent |
| `customer-service.md` | `config/personas/customer-service.md` (in repo) | In-repo customer-service overlay |
| Overlay files | `~/Desktop/Valor/personas/` (iCloud) | Private strategic context (preferred over in-repo overlays) |
| `projects.json` | `~/Desktop/Valor/projects.json` (iCloud) | Contains chat IDs, machine names |
| `projects.example.json` | `config/projects.example.json` (in repo) | Sanitized schema for new setups |

### Engineer Overlay Drift Guards

`load_persona_prompt` (`agent/sdk_client.py:909`) emits WARN logs when the loaded **engineer** overlay is missing load-bearing sections. These guard against overlay drift on bridge machines where the private overlay (`~/Desktop/Valor/personas/engineer.md`, iCloud-synced) could fall out of sync with the in-repo template (`config/personas/engineer.md`). The checks warn when the overlay is missing:

- the `CRITIQUE` gate rules (pipeline integrity);
- the workflow-announcement clause `"Unless you directly instruct me to skip"` (so coding/automation/config requests are surfaced before being implemented);
- the `Mode 3` parallel-orchestrator playbook (multi-issue fan-out);
- the `merge_authorized` stale-baseline bypass section.

It also warns if the overlay still contains the removed `subagent_type="dev-session"` Agent-dispatch pattern (eng sessions are now created via `python -m tools.valor_session create --role eng`).

If you see these warnings at session startup, sync the private overlay on that machine from `config/personas/engineer.md`.

## Adding a New Persona

1. Add a `PersonaType` member to `config/enums.py` (and an `AccessLevel` mapping in `_access_level_for_persona`, `agent/sdk_client.py:1177`, if the new persona should not use `WORKER` rails).
2. Create `~/Desktop/Valor/personas/{persona-name}.md` (and optionally an in-repo `config/personas/{persona-name}.md` fallback) with role-specific instructions.
3. Add the persona entry to `~/Desktop/Valor/projects.json` under `personas`.
4. Reference it in the appropriate group `persona` field or `dm_persona`.
5. The segment content is automatically assembled and prepended — no need to duplicate shared content.

## Fallback Behavior

| Scenario | Fallback |
|----------|----------|
| Overlay in `~/Desktop/Valor/personas/` missing | Falls back to `config/personas/{persona}.md` (in-repo) |
| Known persona (`engineer`/`teammate`/`customer-service`) with both overlay locations missing | Raises `FileNotFoundError` (no silent fallback) |
| Unknown persona name | Falls back to `engineer` (warns), provided the engineer overlay exists |
| Segment file missing | Raises `FileNotFoundError` with segment path for debugging |
| Identity config missing | Raises `FileNotFoundError` (identity.json is required) |

> Note: because there is no in-repo `config/personas/teammate.md`, the `teammate` persona requires the private `~/Desktop/Valor/personas/teammate.md` to exist — it is NOT served from base segments alone. If that file is absent, `load_persona_prompt("teammate")` raises `FileNotFoundError`.

## API

```python
from agent.sdk_client import (
    load_identity,
    load_persona_prompt,
    compose_system_prompt,
    load_system_prompt,
    load_eng_system_prompt,
)
from config.enums import PersonaType, AccessLevel

# Load identity data
identity = load_identity()                          # dict with name, email, timezone, etc.

# Load a specific persona (segments + overlay, no rails)
prompt = load_persona_prompt("engineer")            # segments + engineer overlay
prompt = load_persona_prompt("teammate")            # segments + teammate overlay
prompt = load_persona_prompt("customer-service")    # segments + customer-service overlay

# Single composer (preferred for new code)
prompt = compose_system_prompt(PersonaType.ENGINEER, AccessLevel.WORKER)
prompt = compose_system_prompt(PersonaType.TEAMMATE, AccessLevel.TEAMMATE)

# Thin backward-compatible wrappers over compose_system_prompt
prompt = load_system_prompt()                       # engineer persona + WORKER_RULES
prompt = load_eng_system_prompt("/path")            # engineer persona + WORKER_RULES + work-vault CLAUDE.md
```

`load_eng_system_prompt()` is invoked for the direct `claude -p` path (`get_response_via_harness`) outside the role-runner. Every bridge-originated session (PM, Dev, Teammate) instead receives its persona via role prime commands (`.claude/commands/roles/prime-*-role.md`) run at turn 1 — no `--append-system-prompt` is set on that path. See [Headless Session Runner](headless-session-runner.md).

## Related

- `config/enums.py` — `PersonaType` and `AccessLevel` definitions
- `config/identity.json` — Structured identity data (name, email, timezone, org)
- `config/personas/segments/` — Composable prompt segments (identity, work-patterns, tools)
- `docs/features/config-architecture.md` — Unified config system
- `agent/sdk_client.py` — `load_identity()`, `load_persona_prompt()`, `compose_system_prompt()`, `_resolve_compose_args()`, `_resolve_persona()`
- `bridge/routing.py` — `resolve_persona()`, `is_team_chat()`
- `tests/unit/test_persona_loading.py`, `tests/unit/test_compose_system_prompt.py` — Test coverage
