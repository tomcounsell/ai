---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/368
last_comment_id:
---

# Adopt TELOS-Style Principal Context for Strategic Agent Alignment

## Problem

The agent system (Valor) has strong identity context (`SOUL.md`) and behavioral rules (`CLAUDE.md`) but no structured representation of the supervisor's operating context. When making autonomous judgment calls — prioritizing between projects, scoping features, deciding whether to escalate — the agent has no documented basis for "what does Tom actually care about."

**Current behavior:**
`config/PRINCIPAL.md` exists as a draft but is not read by any component. It contains unresolved `<!-- TOM: -->` placeholder comments. No workflow reads principal context when making triage, scoping, or escalation decisions.

**Desired outcome:**
Key decision-making workflows (Observer Agent, do-plan skill, reflections report) read principal context as part of their preamble. Valor can answer "which project should I prioritize" using `PRINCIPAL.md` as the source of truth. There is a defined process for keeping the file current.

## Prior Art

No prior issues found related to this work. This is greenfield — the PRINCIPAL.md file was drafted as part of issue #368 itself.

## Data Flow

1. **Entry point**: `config/PRINCIPAL.md` is read from disk at decision time
2. **Observer Agent** (`bridge/observer.py`): Principal context injected into `OBSERVER_SYSTEM_PROMPT` context when making STEER vs DELIVER decisions — particularly for prioritization and escalation
3. **Worker system prompt** (`agent/sdk_client.py`): `load_system_prompt()` appends principal context summary alongside SOUL.md, giving workers strategic context for scoping decisions
4. **do-plan skill** (`.claude/skills/do-plan/SKILL.md`): References `config/PRINCIPAL.md` in its preamble so plan appetite and scoping decisions are grounded in portfolio priorities
5. **Reflections report** (`scripts/reflections.py`): Reads principal context to decide what findings to surface vs suppress based on project priority

## Architectural Impact

- **New dependencies**: None — reads a markdown file from disk, no new libraries
- **Interface changes**: `load_system_prompt()` in `agent/sdk_client.py` grows to include principal context; Observer system prompt grows
- **Coupling**: Slightly increases coupling between config layer and decision-making components, but this is intentional — all components need the same strategic context
- **Data ownership**: `config/PRINCIPAL.md` is human-authored and human-maintained; the system only reads it
- **Reversibility**: Fully reversible — removing the reads reverts to current behavior

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (resolve the `<!-- TOM: -->` placeholders, validate priority order)
- Review rounds: 1 (code review of injection points)

The main work is wiring principal context into existing prompts. The harder part is resolving the placeholder comments, which requires supervisor input.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `config/PRINCIPAL.md` exists | `test -f config/PRINCIPAL.md` | Principal context file |

## Solution

### Key Elements

- **Principal context loader**: Utility function that reads `config/PRINCIPAL.md` and extracts relevant sections
- **Observer injection**: Observer system prompt includes project priorities and escalation context from PRINCIPAL.md
- **Worker prompt injection**: `load_system_prompt()` appends a concise principal context block after SOUL.md
- **do-plan reference**: Plan skill preamble references PRINCIPAL.md for appetite/scoping decisions
- **Staleness check**: A reflections step that flags when PRINCIPAL.md hasn't been updated in 90+ days

### Flow

**Message arrives** → Worker receives system prompt (SOUL.md + PRINCIPAL.md summary) → Worker makes scoping decisions grounded in priorities → Observer uses principal context for STEER/DELIVER decisions → Reflections periodically checks staleness

### Technical Approach

- Add `load_principal_context()` function in `agent/sdk_client.py` alongside existing `load_system_prompt()`
- Inject a condensed principal summary (mission + goals + project priorities) into the worker system prompt — keep it short to preserve context window
- Inject the full project priorities table into the Observer system prompt since it makes triage decisions
- Add a `PRINCIPAL.md` reference line to `.claude/skills/do-plan/SKILL.md` in the scoping preamble
- Add a staleness check to `scripts/reflections.py` that flags if `config/PRINCIPAL.md` modification date is > 90 days old
- Resolve or remove all `<!-- TOM: -->` placeholder comments (requires supervisor input)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `load_principal_context()` must handle missing file gracefully (log warning, return empty string) — same pattern as SOUL.md loading
- [ ] If PRINCIPAL.md is malformed or empty, the system continues without principal context

### Empty/Invalid Input Handling
- [ ] Empty PRINCIPAL.md returns empty string, does not crash prompt construction
- [ ] Missing file returns empty string with warning log

### Error State Rendering
- [ ] No user-visible output from this feature — all injection is internal to prompts

## Rabbit Holes

- **Auto-updating PRINCIPAL.md from session signals** — explicitly out of scope per the issue. This is a static authored file.
- **Full TELOS/PAI pack system** — we don't need packaging, distribution, or TypeScript infrastructure
- **Per-project principal configs** — one file covers the whole portfolio. Per-project configs are over-engineering.
- **Complex staleness detection** — a simple file modification date check is sufficient. No need for content diffing or semantic analysis.

## Risks

### Risk 1: Context window bloat
**Impact:** Adding principal context to every worker prompt increases token usage and could push complex tasks over context limits
**Mitigation:** Inject only a condensed summary (mission + active project priorities table) into worker prompts. Full context only goes to Observer, which has a dedicated short-lived session.

### Risk 2: Unresolved placeholders cause confusion
**Impact:** If `<!-- TOM: -->` comments remain, the agent has incomplete strategic context and may make wrong prioritization calls
**Mitigation:** Plan includes a task to resolve all placeholders with supervisor input before marking the file as load-bearing.

## Race Conditions

No race conditions identified — all operations are synchronous file reads at prompt construction time. PRINCIPAL.md is human-edited and read-only from the system's perspective.

## No-Gos (Out of Scope)

- Not adding memory or learning loops to PRINCIPAL.md — it's a static authored file
- Not replacing SOUL.md — PRINCIPAL.md is about the supervisor's context, SOUL.md is about Valor's identity
- Not building a UI or slash command for editing PRINCIPAL.md — it's a markdown file edited directly
- Not implementing per-project priority overrides — the global priority table is sufficient
- Not auto-classifying incoming work against PRINCIPAL.md goals — that's a future enhancement

## Update System

The update script (`scripts/remote-update.sh`) pulls the repo, which includes `config/PRINCIPAL.md`. No changes needed to the update process since:
- `config/PRINCIPAL.md` is already in the repo
- No new dependencies are added
- The code changes (prompt injection) are pulled automatically

One consideration: `config/PRINCIPAL.md` may contain machine-specific content if different machines serve different principals. For now, this is not the case — both machines serve Tom. If this changes, PRINCIPAL.md would need to be added to `.gitignore` alongside `config/projects.json`. No action needed now.

## Agent Integration

No agent integration required — this is a bridge-internal and prompt-internal change. The functionality modifies system prompts and skill preambles, not MCP tools or exposed capabilities. The agent benefits from the injected context automatically without needing new tools.

## Documentation

- [ ] Create `docs/features/principal-context.md` describing the feature, what PRINCIPAL.md contains, how it's injected, and how to keep it current
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline comments in `sdk_client.py` explaining the principal context loading

## Success Criteria

- [ ] `config/PRINCIPAL.md` has no unresolved `<!-- TOM: -->` placeholders
- [ ] `load_principal_context()` exists in `agent/sdk_client.py` and is called by `load_system_prompt()`
- [ ] Observer Agent system prompt includes project priorities from PRINCIPAL.md
- [ ] do-plan skill references PRINCIPAL.md in its scoping preamble
- [ ] Reflections script includes a staleness check for PRINCIPAL.md (90-day threshold)
- [ ] Missing or empty PRINCIPAL.md degrades gracefully (warning log, no crash)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (principal-injection)**
  - Name: principal-builder
  - Role: Wire principal context into sdk_client, observer, do-plan skill, reflections
  - Agent Type: builder
  - Resume: true

- **Validator (principal-injection)**
  - Name: principal-validator
  - Role: Verify all injection points work, graceful degradation, no context bloat
  - Agent Type: validator
  - Resume: true

- **Documentarian (principal-docs)**
  - Name: principal-docs
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Resolve PRINCIPAL.md placeholders
- **Task ID**: resolve-placeholders
- **Depends On**: none
- **Assigned To**: (requires supervisor input)
- **Agent Type**: n/a (human task)
- **Parallel**: true
- Review and resolve all `<!-- TOM: -->` placeholder comments in `config/PRINCIPAL.md`
- Confirm or correct the inferred project priority order

### 2. Build principal context loader and prompt injection
- **Task ID**: build-injection
- **Depends On**: none (can proceed with current PRINCIPAL.md content)
- **Assigned To**: principal-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `load_principal_context()` to `agent/sdk_client.py`
- Modify `load_system_prompt()` to append condensed principal summary
- Inject project priorities into `OBSERVER_SYSTEM_PROMPT` in `bridge/observer.py`
- Add PRINCIPAL.md reference to `.claude/skills/do-plan/SKILL.md` scoping section
- Add staleness check to `scripts/reflections.py`

### 3. Validate injection points
- **Task ID**: validate-injection
- **Depends On**: build-injection
- **Assigned To**: principal-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `load_principal_context()` handles missing file gracefully
- Verify condensed summary is under 500 tokens
- Verify Observer prompt includes project priorities
- Verify do-plan skill references PRINCIPAL.md
- Verify reflections staleness check works

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-injection
- **Assigned To**: principal-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/principal-context.md`
- Add entry to `docs/features/README.md` index table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature, resolve-placeholders
- **Assigned To**: principal-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests
- Verify all success criteria met
- Confirm no `<!-- TOM: -->` placeholders remain

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| PRINCIPAL.md exists | `test -f config/PRINCIPAL.md` | exit code 0 |
| No unresolved placeholders | `grep -c '<!-- TOM:' config/PRINCIPAL.md` | exit code 1 |
| Principal loader exists | `grep -c 'load_principal_context' agent/sdk_client.py` | output > 0 |
| Observer uses principal | `grep -c 'principal' bridge/observer.py` | output > 0 |

---

## Open Questions

1. **Placeholder resolution**: The `<!-- TOM: -->` comments in `config/PRINCIPAL.md` ask about revenue targets, risk tolerance on AI spending, project priority order, and biggest bottleneck. These need supervisor answers before the file is fully load-bearing. Can you provide answers to these four placeholder questions?

2. **Priority order confirmation**: The inferred priority order is `Valor > PsyOPTIMAL > Popoto > Templates > Others`. Is this correct, or should it be adjusted?

3. **Staleness threshold**: The plan proposes 90 days as the threshold for flagging PRINCIPAL.md as stale. Is that reasonable, or would you prefer a different cadence (e.g., monthly)?
