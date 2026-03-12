---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/272
last_comment_id:
---

# PM (Project Manager) Channels

## Problem

Valor currently only operates as a developer. Every Telegram group maps to a source code repo and routes work through the SDLC pipeline (plan, build, test, review, merge). There is no way to use Valor as a project manager -- tracking status, triaging issues, drafting communications, managing priorities -- without it trying to write code.

**Current behavior:**
A message in any Telegram group is routed to the project's source repo via `working_directory` in `projects.json`. The classifier decides whether it is "sdlc" (code work) or "question" (Q&A in that repo). There is no concept of a PM-mode channel that routes to the work vault instead.

**Desired outcome:**
"PM: Cuttlefish" and similar Telegram groups route to the corresponding work-vault project folder (e.g., `~/work-vault/Cuttlefish/`), load PM-specific instructions from that folder's `CLAUDE.md`, skip SDLC classification entirely, and let Valor operate as a project manager scoped to a single project.

## Prior Art

No prior issues found related to this work. This is greenfield functionality.

## Data Flow

1. **Entry point**: Telegram message arrives in "PM: Cuttlefish" group
2. **bridge/routing.py `find_project_for_chat()`**: Matches group name to project config in `GROUP_TO_PROJECT` map
3. **bridge/telegram_bridge.py**: Extracts `working_directory` from the matched project config
4. **agent/sdk_client.py `get_agent_response_sdk()`**: Calls `classify_work_request()` to decide sdlc vs question routing
5. **agent/sdk_client.py**: If sdlc, overrides `working_dir` to `AI_REPO_ROOT` and injects `/sdlc` directive. Otherwise uses project's `working_directory`.
6. **agent/sdk_client.py `ValorAgent`**: Spawns Claude Code with `cwd=working_dir` and system prompt from `SOUL.md`
7. **Output**: Agent response sent back through Telegram

For PM channels, the flow should be:
1. Same entry point
2. Same group-to-project matching, but the matched project has `"mode": "pm"` and `working_directory` pointing to the work vault
3. `classify_work_request()` is **skipped** -- PM channels always use "question" classification (never SDLC)
4. Agent spawns with `cwd=~/work-vault/Cuttlefish/` and reads that folder's `CLAUDE.md` for PM instructions
5. No worker safety rails about branches, no SDLC pipeline

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on which projects to support initially)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. The work vault already exists at `~/work-vault/` with project folders for Cuttlefish, SATSOL, Monday Flowers, PsyOptimal, and Royop.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Work vault exists | `test -d ~/work-vault/Cuttlefish` | Verify target directory |

## Solution

### Key Elements

- **Project mode field in `projects.json`**: A `"mode": "pm"` field on project entries that signals non-dev behavior
- **PM project entries**: New entries in `projects.json` for each PM channel (e.g., `pm-cuttlefish`) with `working_directory` pointing to the work-vault folder
- **Classification bypass**: When a project has `mode: "pm"`, skip `classify_work_request()` and always use `"question"` classification
- **System prompt override**: PM-mode agents load system prompt from the work-vault folder's `CLAUDE.md` instead of (or in addition to) the standard `SOUL.md`
- **Worker rules bypass**: PM-mode agents do not receive the `WORKER_RULES` (branch safety rails, SDLC directives)

### Flow

**Message in "PM: Cuttlefish"** → routing matches `pm-cuttlefish` project → mode=pm detected → skip SDLC classification → spawn agent with `cwd=~/work-vault/Cuttlefish/` → agent reads local `CLAUDE.md` for PM instructions → responds as project manager

### Technical Approach

- Add `"mode"` field to project config schema (values: `"dev"` (default) or `"pm"`)
- Add PM project entries to `projects.json` for each project (Cuttlefish, SATSOL, Monday Flowers, PsyOptimal, Royop)
- In `agent/sdk_client.py get_agent_response_sdk()`: check `project.get("mode") == "pm"` and if so, force `classification = "question"` (bypass `classify_work_request()`)
- In `agent/sdk_client.py get_agent_response_sdk()`: when mode is "pm", do not inject SDLC directives, do not prepend `WORKER_RULES`
- In `agent/sdk_client.py ValorAgent.__init__()` or `load_system_prompt()`: when mode is "pm", load system prompt from the project's `working_directory/CLAUDE.md` if it exists, falling back to SOUL.md
- Create a `CLAUDE.md` in each work-vault project folder with PM-mode instructions (what to do, what not to do, linked repos for read-only status checks)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `classify_work_request` bypass path is a simple conditional; no exception handlers added
- [ ] If mode field is missing from project config, default to "dev" -- no crash

### Empty/Invalid Input Handling
- [ ] If `mode` field has an unexpected value (not "dev" or "pm"), treat as "dev" (existing behavior)
- [ ] If PM working directory does not exist, log warning and fall back to ai/ repo root (existing behavior in `validate_workspace`)

### Error State Rendering
- [ ] PM channels render errors the same as dev channels -- no special error handling needed
- [ ] If work-vault `CLAUDE.md` is missing, agent still works with default SOUL.md prompt

## Rabbit Holes

- **Building a full PM tool suite** -- Resist creating dedicated PM tools (sprint boards, Gantt charts, etc). The agent already has file read/write, GitHub, and Google Workspace access. Let the `CLAUDE.md` instructions guide PM behavior without new tooling.
- **Cross-project visibility** -- Do not add a way for PM channels to see other projects' data. Each PM channel is scoped to one project folder. Cross-project dashboards are a separate feature.
- **PM-specific SDLC pipeline** -- Do not create a "PM pipeline" with stages. PM work is conversational Q&A, not pipeline-driven.
- **Automatic project linking** -- Do not auto-link PM channels to Dev channels. Keep them independent. The PM `CLAUDE.md` can reference the source repo path for read-only status checks.

## Risks

### Risk 1: Accidental code changes from PM channels
**Impact:** PM channel agent could modify source code if instructions are unclear
**Mitigation:** PM `CLAUDE.md` files explicitly instruct "do not write code" and the agent spawns in the work-vault directory, not the source repo. The work vault contains only docs/notes.

### Risk 2: Config file conflicts across machines
**Impact:** `config/projects.json` already has local path overrides per machine; adding PM entries adds more machine-specific paths
**Mitigation:** Use the existing pattern -- `projects.json` is `.gitignore`d or has local overrides. Work vault paths follow the same `~/work-vault/` convention on all machines.

## Race Conditions

No race conditions identified. The routing change is stateless -- it reads the project config synchronously at message routing time. No shared mutable state is introduced.

## No-Gos (Out of Scope)

- No new MCP tools or tool servers for PM functionality
- No cross-project dashboards or aggregation views
- No PM-specific summarization or reporting (use existing agent capabilities)
- No changes to the Observer Agent or auto-continue logic for PM channels
- No Notion/Linear integration changes specifically for PM mode (existing integrations still apply)

## Update System

The update script needs to propagate the new `projects.json` entries. However, `projects.json` already contains machine-specific paths and is typically configured per-machine.

- The update skill does NOT need code changes -- `projects.json` is already excluded from update propagation
- Each machine operator adds PM entries to their local `projects.json` manually
- The work-vault `CLAUDE.md` files should be committed to the work-vault repo (if it is version-controlled) or documented in setup instructions

## Agent Integration

No agent integration required -- this is a bridge/routing-internal change. The agent itself does not need new tools or MCP servers. The PM behavior is driven entirely by:
1. Routing to the work-vault directory
2. The `CLAUDE.md` in that directory providing PM instructions
3. Skipping SDLC classification so the agent does not try to plan/build/test

## Documentation

- [ ] Create `docs/features/pm-channels.md` describing PM channel setup and behavior
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Document `projects.json` `mode` field in inline comments or existing config docs
- [ ] Create template `CLAUDE.md` for work-vault project folders

## Success Criteria

- [ ] `projects.json` has a `"mode": "pm"` field supported on project entries
- [ ] PM channel entries exist for Cuttlefish, SATSOL, Monday Flowers, PsyOptimal, Royop
- [ ] Messages in PM channels route to the corresponding work-vault folder
- [ ] PM channels bypass SDLC classification (never trigger `/sdlc`)
- [ ] PM channel agents load `CLAUDE.md` from the work-vault project folder
- [ ] PM channel agents do not receive WORKER_RULES or branch safety rails
- [ ] Dev channels continue working exactly as before (no regression)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (routing)**
  - Name: routing-builder
  - Role: Implement mode field in config, classification bypass, system prompt override
  - Agent Type: builder
  - Resume: true

- **Builder (config)**
  - Name: config-builder
  - Role: Create PM project entries and work-vault CLAUDE.md files
  - Agent Type: builder
  - Resume: true

- **Validator (routing)**
  - Name: routing-validator
  - Role: Verify PM routing bypasses SDLC, dev channels unchanged
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add mode field support to routing and agent
- **Task ID**: build-routing
- **Depends On**: none
- **Assigned To**: routing-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `mode` field support to `config/projects.json` schema (default: `"dev"`)
- In `agent/sdk_client.py get_agent_response_sdk()`: when `project.get("mode") == "pm"`, skip `classify_work_request()` and force `classification = "question"`
- In `agent/sdk_client.py get_agent_response_sdk()`: when mode is `"pm"`, do not inject SDLC directives or `WORKER_RULES` into the enriched message
- In `agent/sdk_client.py`: when mode is `"pm"`, attempt to load system prompt from `{working_directory}/CLAUDE.md` if it exists, appending to (or replacing) SOUL.md content

### 2. Create PM project entries and vault CLAUDE.md files
- **Task ID**: build-config
- **Depends On**: none
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Add PM entries to `config/projects.json` for: Cuttlefish, SATSOL, Monday Flowers, PsyOptimal, Royop
- Each entry: `"mode": "pm"`, `"telegram": {"groups": ["PM: {Name}"], ...}`, `"working_directory": "~/work-vault/{Name}/"`
- Create `CLAUDE.md` in each work-vault project folder with PM-mode instructions: focus on project management, no code writing, linked source repo for read-only status

### 3. Validate routing
- **Task ID**: validate-routing
- **Depends On**: build-routing, build-config
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `find_project_for_chat("PM: Cuttlefish")` returns the PM config
- Verify mode="pm" projects skip `classify_work_request()`
- Verify mode="dev" (or absent) projects still call `classify_work_request()` as before
- Verify PM agent does not receive WORKER_RULES in system prompt

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routing
- **Assigned To**: routing-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-channels.md`
- Add entry to `docs/features/README.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: routing-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| PM config exists | `python -c "import json; c=json.load(open('config/projects.json')); assert any(p.get('mode')=='pm' for p in c['projects'].values())"` | exit code 0 |
| Mode field defaults to dev | `python -c "import json; c=json.load(open('config/projects.json')); assert c['projects']['valor'].get('mode', 'dev') == 'dev'"` | exit code 0 |

---

## Open Questions

1. **Work vault path convention**: The work vault is at `~/work-vault/` on the current machine. Is this the same path on all machines, or do some use `~/src/work-vault/`? This affects the `projects.json` entries.

2. **PM channel Telegram groups**: Should the PM channels be created as new Telegram groups (e.g., "PM: Cuttlefish") that don't exist yet, or are they already created and waiting for routing?

3. **Read-only source repo access**: Should PM channels have read-only access to the linked source repo (e.g., for checking git log, open PRs, test results)? If so, how -- via GitHub CLI from the work-vault working directory, or by including the source repo path in the PM `CLAUDE.md`?

4. **System prompt composition**: For PM channels, should the agent get SOUL.md (persona) + work-vault CLAUDE.md (PM instructions), or should the work-vault CLAUDE.md completely replace SOUL.md? The persona (Valor's attitude/style) seems valuable even in PM mode.
