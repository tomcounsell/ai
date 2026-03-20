---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/432
also_closes: https://github.com/tomcounsell/ai/issues/395
last_comment_id: IC_kwDOEYGa0870G1VV
---

# Configurable Personas

## Problem

The system has one monolithic `config/SOUL.md` (511 lines) used for all interactions. But the bridge now routes messages through 4 distinct personas (developer, project-manager, teammate) based on which Telegram group the message arrives from. The persona config in `projects.json` already defines soul file paths (`config/personas/developer.md`, etc.) but these files don't exist yet — the bridge still loads the single `SOUL.md` for everything.

**Current behavior:**
- `agent/sdk_client.py` loads `config/SOUL.md` as the system prompt for all sessions
- The bridge itself and the coding subprocess get the same monolithic prompt
- A team chat Q&A gets the same autonomous execution instructions as a feature build
- The `personas` section in `projects.json` has `soul` paths that point to nonexistent files

**Desired outcome:**
- `config/SOUL.md` split into shared base + per-persona overlays
- The PM persona handles all Telegram messaging (bridge/Observer) — it's the single communication layer
- The developer persona is loaded when the PM spins up AgentSDK subprocesses for coding work
- The teammate persona handles DMs and team chats (casual Q&A)
- Chat group prefix (Dev: vs PM:) determines what work the PM dispatches, not which persona receives the message

## Prior Art

- **Issue #432**: This issue — defines Category A (persona) vs B (brand) vs C (docs) separation
- **Issue #189**: "More Souls" — broader vision for multiple personas from souls.directory
- **PR #438**: Config consolidation — established `~/Desktop/Valor/` pattern and `projects.json` persona section
- **`docs/references/valor-name-references.md`**: Full audit of 200+ "Valor" references sorted by category

## Data Flow

1. **Telegram message arrives** → bridge determines chat group name
2. **`bridge/routing.py`** → `find_project_for_chat()` returns project config
3. **Bridge (PM persona) handles message** → always loaded with PM persona soul
4. **If coding work needed** → PM spins up AgentSDK subprocess with **developer** persona
5. **If DM or team chat** → bridge uses **teammate** persona
6. **System prompt assembled** → base + persona overlay + principal context + criteria

The key insight: the bridge IS the PM. It doesn't "select" a persona per message — it always runs as PM. The persona selection only matters for:
- AgentSDK subprocesses (developer persona for coding)
- DMs and team chats (teammate persona)

## Architectural Impact

- **New files**: 3 persona soul files + 1 base in `config/personas/`
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

- **Shared base**: `config/personas/_base.md` — identity, values, communication style, strategic context
- **Three persona overlays**: developer, project-manager, teammate — role-specific instructions on top of the base
- **Bridge = PM persona**: the bridge always runs as PM. It dispatches coding work to developer persona via AgentSDK.
- **Unified loader**: `load_prompt(persona)` replaces both `load_system_prompt()` and `load_pm_prompt()`

### Architecture

```
Telegram (all groups)
      ↓
  Bridge (PM persona) ← always loaded with project-manager soul
      ├── Dev: X group → dispatches AgentSDK subprocess (developer persona)
      ├── PM: X group → PM handles directly (issue/PR mgmt, comms)
      ├── Team chat → teammate persona (casual Q&A, mention-only)
      └── DMs → teammate persona (casual Q&A)
```

### Technical Approach

**Split SOUL.md into layers:**

| Layer | File | Content |
|-------|------|---------|
| Base | `config/personas/_base.md` | Identity, values, communication style, tools, strategic context, wisdom |
| Developer | `config/personas/developer.md` | Full system access, SDLC pipeline, autonomous execution, git operations |
| PM | `config/personas/project-manager.md` | Triage, routing, Observer duties, GitHub management, communications |
| Teammate | `config/personas/teammate.md` | Casual conversation, Q&A, light and helpful |

**Bridge changes:**
- Bridge loads PM persona at startup (base + project-manager overlay)
- `bridge/telegram_bridge.py` uses PM persona for its own reasoning/summarization
- When spawning AgentSDK coding subprocesses, passes `persona="developer"`
- DMs and team chats use `persona="teammate"`

**SDK changes:**
- `agent/sdk_client.py`: unify `load_system_prompt()` and `load_pm_prompt()` into `load_prompt(persona)`
- `load_prompt(persona)` reads `config/personas/_base.md` + `config/personas/{persona}.md`, concatenates
- Fallback: if persona file missing, load `config/SOUL.md`

**Strategic context (from #395 TELOS framework):**

The base file carries shared strategic context (from the drafted PRINCIPAL.md in #368):
- Mission, priority order (Valor > PsyOPTIMAL > Popoto > Templates > Others)
- Key beliefs and strategies
- Accumulated lessons

Each persona overlay adds role-specific judgment:
- Developer: scoping decisions, architecture tradeoffs, effort calibration
- PM: triage and routing, work prioritization, escalation decisions
- Teammate: what to answer vs defer, casual helpfulness

**What stays as SOUL.md:**
- `config/SOUL.md` remains as legacy fallback only — not loaded in normal operation
- Eventually moved to `~/Desktop/Valor/SOUL.md`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Missing persona file → fall back to SOUL.md with warning log
- [ ] Missing `_base.md` → fail loudly (base is required)
- [ ] Invalid persona name in config → fall back to developer persona with warning

### Empty/Invalid Input Handling
- [ ] Empty persona name from routing → default to "project-manager" (bridge default)
- [ ] DM with no project match → use "teammate" persona per dms config

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
- Memory partitioning by persona (#395 scope — AgentSession.persona field, per-persona memory decay)
- Persona staleness detection / `/update-persona` command (#395 scope)
- CTO / Chief of Staff personas (#395 scope — only build the 3 core personas now)
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
- [ ] `config/personas/teammate.md` exists with casual conversation instructions
- [ ] `agent/sdk_client.py` has unified `load_prompt(persona)` replacing both `load_system_prompt()` and `load_pm_prompt()`
- [ ] Bridge loads PM persona at startup for its own reasoning
- [ ] AgentSDK coding subprocesses use developer persona
- [ ] DMs and team chats use teammate persona
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
- Create `config/personas/_base.md` with: Identity, Values, Communication Style, Tools reference, Strategic context (from PRINCIPAL.md draft), Wisdom
- Create `config/personas/developer.md` with: Full System Access, Autonomous Execution, SDLC, Self-Management, Daily Operations
- Create `config/personas/project-manager.md` with: Triage/routing, Observer duties, GitHub management, communications, escalation decisions
- Create `config/personas/teammate.md` with: Casual conversation, Q&A, light and helpful, encouraging

### 2. Wire persona through bridge and SDK
- **Task ID**: build-wiring
- **Depends On**: build-split
- **Assigned To**: bridge-wirer
- **Agent Type**: builder
- **Parallel**: false
- Update `agent/sdk_client.py`: unify `load_system_prompt()` and `load_pm_prompt()` into `load_prompt(persona)`
- `load_prompt(persona)` reads `_base.md` + `{persona}.md`, concatenates, falls back to `SOUL.md`
- Bridge loads PM persona at startup for its own messaging/summarization
- AgentSDK coding subprocesses get developer persona
- DMs and team chats get teammate persona

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
| All personas exist | `ls config/personas/developer.md config/personas/project-manager.md config/personas/teammate.md` | exit code 0 |
| SOUL.md still exists | `test -f config/SOUL.md` | exit code 0 |

---

## Resolved Questions

1. **Base + persona concatenation at load time.** `_base.md` is prepended to each persona file. Valor is the same name and same base persona but acting in different roles. DRY wins.

2. **Unify into `load_prompt(persona)`.** Kill `load_pm_prompt()`. One function, one path. Build for simplicity and scale.

3. **Merge team-member and qa into one "teammate" persona.** Light and simple — casual conversation on top of the base persona. Not a separate "Q&A mode" with rigid rules.
