---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/432
last_comment_id: IC_kwDOEYGa0870G1VV
---

# Configurable Personas

## Problem

The system has one monolithic `config/SOUL.md` (511 lines) used for all interactions. But the bridge now routes messages through 4 distinct personas (developer, project-manager, team-member, qa) based on which Telegram group the message arrives from. The persona config in `projects.json` already defines soul file paths (`config/personas/developer.md`, etc.) but these files don't exist yet — the bridge still loads the single `SOUL.md` for everything.

**Current behavior:**
- `agent/sdk_client.py` loads `config/SOUL.md` as the system prompt for all sessions
- A PM group message gets the same "full system access, YOLO mode" prompt as a dev group
- A team chat Q&A gets the same autonomous execution instructions as a feature build
- The `personas` section in `projects.json` has `soul` paths that point to nonexistent files

**Desired outcome:**
- `config/SOUL.md` split into shared base + per-persona overlays
- Bridge passes the persona name (from group config) to the SDK client
- SDK client loads the correct persona soul file
- Each persona gets appropriate instructions (dev: full access, PM: read-only + GitHub, team-member: Q&A only, qa: Q&A only)

## Prior Art

- **Issue #432**: This issue — defines Category A (persona) vs B (brand) vs C (docs) separation
- **Issue #189**: "More Souls" — broader vision for multiple personas from souls.directory
- **PR #438**: Config consolidation — established `~/Desktop/Valor/` pattern and `projects.json` persona section
- **`docs/references/valor-name-references.md`**: Full audit of 200+ "Valor" references sorted by category

## Data Flow

1. **Telegram message arrives** → bridge determines chat group name
2. **`bridge/routing.py`** → `find_project_for_chat()` returns project config
3. **NEW: `get_group_persona()`** → extracts persona name from group config dict
4. **Bridge passes persona to SDK** → `sdk_client.py` receives persona name alongside project
5. **`sdk_client.py` loads soul file** → reads `config/personas/{persona}.md` instead of `config/SOUL.md`
6. **System prompt assembled** → WORKER_RULES + persona soul + principal context + criteria

## Architectural Impact

- **New files**: 4 persona soul files in `config/personas/`
- **Interface changes**: `sdk_client.py` functions gain a `persona` parameter
- **Coupling**: Decreases — persona behavior decoupled from monolithic SOUL.md
- **Data ownership**: `projects.json` owns persona selection, `config/personas/*.md` owns persona content
- **Reversibility**: High — fall back to SOUL.md if persona file missing

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (review persona soul file content)
- Review rounds: 1

The code changes are small (~5 files). The bulk of work is splitting SOUL.md content into the right personas.

## Prerequisites

No prerequisites — uses existing config infrastructure.

## Solution

### Key Elements

- **Shared base**: `config/personas/_base.md` — identity, values, communication style (shared across all personas)
- **Per-persona overlays**: `config/personas/{developer,project-manager,team-member,qa}.md` — each imports the base and adds persona-specific instructions
- **Persona routing**: Bridge extracts persona from group config and passes to SDK client
- **SDK loading**: `sdk_client.py` loads persona soul file instead of `SOUL.md`

### Flow

**Message arrives** → routing finds project + group → persona name extracted → passed to SDK → SDK loads `config/personas/{persona}.md` → assembled into system prompt

### Technical Approach

**Split SOUL.md into layers:**

| Layer | File | Content |
|-------|------|---------|
| Base | `config/personas/_base.md` | Identity, values, communication style, tools reference, wisdom |
| Developer | `config/personas/developer.md` | Full system access, SDLC pipeline, autonomous execution, git operations |
| PM | `config/personas/project-manager.md` | Read-only code, GitHub issue/PR management, communications drafting |
| Team member | `config/personas/team-member.md` | Q&A only, concise answers, no proactive actions |
| QA | `config/personas/qa.md` | Q&A about the ai repo, read-only, no code changes |

**Bridge changes:**
- `bridge/routing.py` already has `get_group_persona()` (added today) — just needs to be called from the bridge
- `bridge/telegram_bridge.py` passes persona name when calling SDK

**SDK changes:**
- `agent/sdk_client.py`: `load_system_prompt()` and `load_pm_prompt()` unified into `load_prompt(persona)` that reads from `config/personas/{persona}.md`
- Fallback: if persona file missing, load `config/SOUL.md` (backward compat)
- DMs: use `"qa"` persona (from `dms.persona` in projects.json)

**What stays as SOUL.md:**
- `config/SOUL.md` remains as legacy fallback only — not loaded in normal operation
- Eventually moved to `~/Desktop/Valor/SOUL.md` (per config consolidation plan)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Missing persona file → fall back to SOUL.md with warning log
- [ ] Missing `_base.md` → fail loudly (base is required)
- [ ] Invalid persona name in config → fall back to developer persona with warning

### Empty/Invalid Input Handling
- [ ] Empty persona name from routing → default to "developer"
- [ ] DM with no project match → use "qa" persona per dms config

### Error State Rendering
- [ ] Log which persona was loaded at session start for debugging

## Test Impact

- [ ] `tests/unit/test_sdk_client.py` — UPDATE: mock persona file loading instead of SOUL.md loading
- [ ] `tests/unit/test_config_consolidation.py` — UPDATE: add persona file existence checks

## Rabbit Holes

- **Rewriting SOUL.md content**: The split should be mechanical — move sections, don't rewrite prose. Content improvements are separate work.
- **Dynamic persona switching mid-conversation**: Out of scope. Persona is set at session start.
- **Persona inheritance/composition system**: Just use string concatenation of base + overlay. No template engine.
- **Moving SOUL.md to ~/Desktop/Valor/**: Separate issue, do after this ships.

## Risks

### Risk 1: Soul file content gets stale across personas
**Impact:** Personas diverge, shared updates need to be applied to multiple files
**Mitigation:** Base file holds all shared content. Per-persona files are small overlays (permissions, mode-specific instructions only).

### Risk 2: Bridge restart needed after persona file edits
**Impact:** Can't hot-reload persona content
**Mitigation:** Acceptable — bridge restarts are fast (~3s). Same as current SOUL.md behavior.

## Race Conditions

No race conditions identified. Persona is resolved once at message receipt time and passed through the pipeline synchronously.

## No-Gos (Out of Scope)

- Rewriting persona content (just split existing SOUL.md)
- Moving SOUL.md to ~/Desktop/Valor/ (tracked separately)
- Dynamic persona switching within a session
- Category B (brand) or C (docs) name changes from issue #432
- Implementing actual permission enforcement (read-only for PM) — that's enforcement logic, this is prompt configuration
- souls.directory integration (#189) — separate work after this ships

## Update System

The update script should ensure `config/personas/` directory exists after pull. No other changes — persona files are checked into git.

## Agent Integration

No agent integration required — this changes the system prompt loaded by `sdk_client.py`, which is bridge-internal. No MCP server changes needed.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/personas.md` describing the persona system
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/config-architecture.md` to reference persona files

### Inline Documentation
- [ ] Docstring on `load_prompt(persona)` in `sdk_client.py`

## Success Criteria

- [ ] `config/personas/_base.md` exists with shared identity/values/tools content
- [ ] `config/personas/developer.md` exists with full-access developer instructions
- [ ] `config/personas/project-manager.md` exists with read-only + GitHub instructions
- [ ] `config/personas/team-member.md` exists with Q&A-only instructions
- [ ] `config/personas/qa.md` exists with Q&A-only instructions
- [ ] `agent/sdk_client.py` loads persona-specific soul file based on persona parameter
- [ ] Bridge passes persona name from group config to SDK client
- [ ] DMs use "qa" persona
- [ ] Missing persona file falls back to SOUL.md with warning
- [ ] `config/SOUL.md` remains as fallback but is not loaded in normal operation
- [ ] Tests pass
- [ ] Documentation updated

## Team Orchestration

### Team Members

- **Builder (persona-split)**
  - Name: soul-splitter
  - Role: Split SOUL.md into base + 4 persona files
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-wiring)**
  - Name: bridge-wirer
  - Role: Wire persona routing through bridge to SDK client
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: persona-validator
  - Role: Verify each persona loads correctly and contains appropriate content
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: persona-docs
  - Role: Create personas.md feature doc
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Split SOUL.md into persona files
- **Task ID**: build-split
- **Depends On**: none
- **Assigned To**: soul-splitter
- **Agent Type**: builder
- **Parallel**: true
- Create `config/personas/_base.md` with: Identity, Values, Communication Style, Tools reference, Wisdom
- Create `config/personas/developer.md` with: base import + Full System Access, Autonomous Execution, SDLC, Self-Management, Daily Operations
- Create `config/personas/project-manager.md` with: base import + Read-only instructions, GitHub management, communications focus
- Create `config/personas/team-member.md` with: base import + Mention-only, Q&A focus, concise answers
- Create `config/personas/qa.md` with: base import + Q&A only, ai repo context, read-only

### 2. Wire persona through bridge and SDK
- **Task ID**: build-wiring
- **Depends On**: build-split
- **Assigned To**: bridge-wirer
- **Agent Type**: builder
- **Parallel**: false
- Update `bridge/telegram_bridge.py` to call `get_group_persona()` and pass result to SDK
- Update `agent/sdk_client.py`: replace `load_system_prompt()`/`load_pm_prompt()` with `load_prompt(persona)`
- `load_prompt()` reads `config/personas/{persona}.md`, prepends `_base.md`, falls back to `SOUL.md`
- DM sessions use `"qa"` persona from `projects.json` dms config

### 3. Validate
- **Task ID**: validate-personas
- **Depends On**: build-wiring
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 5 persona files exist and are non-empty
- Verify `load_prompt("developer")` returns content including base + developer sections
- Verify `load_prompt("nonexistent")` falls back to SOUL.md
- Run `pytest tests/unit/ -x -q`
- Run `ruff check . && ruff format --check .`

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-personas
- **Assigned To**: persona-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/personas.md`
- Add entry to `docs/features/README.md`
- Update `docs/features/config-architecture.md`

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: persona-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Base persona exists | `test -f config/personas/_base.md` | exit code 0 |
| All personas exist | `ls config/personas/developer.md config/personas/project-manager.md config/personas/team-member.md config/personas/qa.md` | exit code 0 |
| SOUL.md still exists | `test -f config/SOUL.md` | exit code 0 |

---

## Open Questions

1. Should the base file be literally prepended to each persona file at load time (concatenation), or should each persona file include the base content directly (self-contained)? Concatenation keeps files DRY but means persona files read oddly in isolation. Self-contained means duplication but each file is a complete prompt.

2. Should we keep `load_pm_prompt()` as a separate function or fully unify into `load_prompt(persona)`? The PM prompt currently has different WORKER_RULES — should PM rules also be persona-specific?

3. For the `team-member` and `qa` personas, how minimal should the soul be? Just "answer questions concisely about the codebase" or should it still include Valor's identity/values?
