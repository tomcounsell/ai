---
status: docs_complete
type: enhancement
appetite: Small
owner: Valor
created: 2026-04-13
tracking: https://github.com/tomcounsell/ai/issues/928
last_comment_id:
---

# PM Dev-Session Briefing Quality: Structured Dispatch Template

## Problem

**Current behavior:**
The PM session dispatches dev sessions with a minimal 5-field prompt (stage, skill, issue URL, current state, acceptance criteria). The dev agent arrives cold and must re-derive all context from scratch -- re-fetching the issue, re-reading related files, re-discovering constraints.

**Observed evidence** -- session `0_1776056811242` (PLAN stage for issue #927):

Six specific failures:
1. **No recon summary** -- PM read the issue in full but forwarded none of it. Dev re-fetched from scratch.
2. **Prior stage context skipped** -- PM didn't check `sdlc-stage-comment` markers in issue comments.
3. **No architectural pointers** -- issue touched specific files; PM passed nothing. Dev explored blindly.
4. **No constraints forwarded** -- CLAUDE.md rules (plan-on-main, required sections) were not included.
5. **`--model` flag omitted** -- PLAN stage calls for Opus per the dispatch table. PM passed no `--model` flag.
6. **Brittle venv resolution** -- PM retried `valor_session create` 5 times with `source .venv/bin/activate` subshells instead of using `python -m tools.valor_session create` directly.

**Desired outcome:**
The PM builds a structured briefing for each dev session using context it already holds, so the dev agent starts informed rather than cold.

## Prior Art

- **PR #909**: Added `--model` flag and stage-to-model dispatch table to `config/personas/project-manager.md` (merged).
- **Issue #791**: Prior PM quality issue -- stage skipping.
- **Issue #846**: Prior PM quality issue -- routing gaps.
- The current "Dispatch Message Format" section (lines 121-159 of `config/personas/project-manager.md`) was written as a reaction to over-verbose briefings but swung too far toward minimalism.

## Architectural Impact

- **Interface changes**: None. The `valor_session create` CLI interface is unchanged. Only the PM persona's guidance on what to put in `--message` changes.
- **Coupling**: No change. The PM persona is advisory text, not executable code.
- **Data ownership**: No change.
- **Reversibility**: High -- this is a prompt change in a persona document. Fully reversible with a git revert.

## Appetite

**Size:** Small (single file change, ~100 lines of persona text)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully defined)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PM persona exists | `test -f config/personas/project-manager.md` | File to modify |
| PR #909 merged | `git log --oneline --all --grep="stage.*model dispatch"` | Dispatch table must already be in place |

## Solution

### Key Elements

- **Part 1: Replace the "Dispatch Message Format" section** -- Replace the current minimalist guidance (lines 121-159) with a structured briefing template that includes Problem Summary, Key Files, Prior Stage Findings, Constraints, Current State, and Acceptance Criteria fields.
- **Part 2: Add `--model` as required in the template** -- The `valor_session create` example must show `--model` as a required field with a cross-reference to the dispatch table.
- **Part 3: Add invocation note** -- Add a callout that `python -m tools.valor_session create` is the correct invocation from project root, never `source .venv/bin/activate` in a subshell.
- **Part 4: Add a calibration example** -- Include a fully rendered PLAN-stage briefing so the PM has a concrete reference for expected verbosity.

### Flow

All four parts are a single atomic change to `config/personas/project-manager.md`. One commit.

### Technical Approach

**Replace the "Dispatch Message Format" section** (lines 121-159 of `config/personas/project-manager.md`) with the following structure:

#### New Briefing Template

```markdown
## Dispatch Message Format

The `--message` passed to each dev session is a structured briefing. The dev agent has no
other context -- what the PM writes here is what it knows.

### Required Fields

Every dispatch message MUST include these fields:

    Stage: <STAGE_NAME>
    Required skill: /do-<skill>
    Issue: <GitHub issue URL>

    ## Problem Summary
    <2-3 sentences from the issue -- what's broken, what the desired outcome is.
     Use the issue body you already fetched, do not make the dev agent re-fetch it.>

    ## Key Files / Entry Points
    <3-5 files the dev agent should read first. Derived from the PM's issue analysis
     and any recon done during earlier stages.>

    ## Prior Stage Findings
    <Paste sdlc-stage-comment content from issue comments, or "None -- this is the first stage."
     Check: gh api repos/{owner}/{repo}/issues/{number}/comments | grep sdlc-stage-comment>

    ## Constraints
    <Relevant rules from CLAUDE.md, plan section requirements, branch rules, scope limits.
     Only constraints the skill cannot derive on its own.>

    ## Current State
    <What's already done: existing plan doc path, open PR number, test results, etc.
     "No plan doc exists. No PR open. Starting from scratch." is valid for first stages.>

    ## Acceptance Criteria
    <What done looks like for THIS stage. Be specific but don't restate the skill's
     own output format.>

### What NOT to Include

- Instructions the skill already contains ("run ruff", "open a PR", "commit on main")
- Generic acceptance criteria that restate the skill's built-in behavior
- Full issue body copy-paste -- summarize, don't dump

### Invocation

Always invoke via module path from the project root. Never use subshell activate.

    python -m tools.valor_session create \
      --role dev \
      --model <opus|sonnet> \
      --slug {slug} \
      --parent "$AGENT_SESSION_ID" \
      --message "<briefing>"

The `--model` flag is REQUIRED. Refer to the Stage-to-Model Dispatch Table above for
which model to use per stage.

### Calibration Example: PLAN Stage

    Stage: PLAN
    Required skill: /do-plan
    Issue: https://github.com/tomcounsell/ai/issues/928

    ## Problem Summary
    The PM session dispatches dev sessions with a minimal 5-field briefing, so dev agents
    arrive cold and must re-derive context from scratch. Six specific failures were observed:
    no recon summary forwarded, prior stage context skipped, no architectural pointers,
    no constraints, --model flag omitted, and brittle venv resolution.

    ## Key Files / Entry Points
    - config/personas/project-manager.md -- PRIMARY file to change (contains dispatch template)
    - tools/valor_session.py -- create subcommand, shows --model flag implementation
    - docs/plans/ -- output directory for the plan doc
    - PR #909 -- added --model flag and stage-to-model dispatch table (already merged)

    ## Prior Stage Findings
    None -- this is the first stage.

    ## Constraints
    - Plan doc must be committed on MAIN branch (not a feature branch)
    - Plan must include all four required sections: Documentation, Update System,
      Agent Integration, Test Impact
    - In scope: config/personas/project-manager.md only -- no worker changes

    ## Current State
    No plan doc exists. No PR open. Starting from scratch.

    ## Acceptance Criteria
    Plan doc exists at docs/plans/pm-dev-session-briefing.md with expanded briefing template,
    --model shown as required, correct invocation note, and example briefing included.
```

#### Preserve Existing Sections

The following sections remain UNCHANGED:
- Hard Rules (Rule 1, Rule 2)
- SDLC Stage Sequence
- Stage Artifact Verification
- Available Tools
- Escalation Policy
- Stage-to-Model Dispatch Table (lines 96-119)
- Hard-PATCH Resume Decision Rules
- Worktree Isolation
- Multi-Issue Fan-out
- Anomaly Response

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] PM persona is a markdown file -- no exception paths. The only failure mode is the PM ignoring the template, which is advisory and not enforceable.

### Empty/Invalid Input Handling
- [x] Verify the template handles "first stage" case (Prior Stage Findings = "None")
- [x] Verify the template handles "no PR yet" case (Current State references no PR)

### Error State Rendering
- [x] N/A -- this is a persona document, not executable code

## Test Impact

No existing tests affected -- this change modifies a persona markdown file (`config/personas/project-manager.md`), not executable code. No test files reference or parse this file programmatically.

## Rabbit Holes

- **Automated validation that the PM fills in each field** -- Out of scope. The template is advisory. Enforcement would require parsing the `--message` content in `valor_session.py`, which adds complexity for marginal benefit. The PM is an LLM that will follow clear instructions.
- **Changing how dev sessions receive context** -- Out of scope. The `--message` field is the sole context channel by design. Adding a sidecar context file or shared state would be a larger architectural change.
- **Modifying the worker or bridge** -- Out of scope. This is purely a PM persona change.
- **Per-stage templates** -- Tempting but unnecessary. One template with stage-agnostic fields covers all cases. The calibration example shows how to fill it for PLAN; other stages follow the same structure.

## Risks

### Risk 1: Template is too verbose, PM sessions consume more context window
**Impact:** PM sessions use more tokens per dispatch, reducing headroom for other reasoning.
**Mitigation:** The template adds ~200 tokens per dispatch. PM sessions have large context windows. The time saved by dev agents not re-deriving context far outweighs the marginal token cost.

### Risk 2: PM ignores the template and falls back to minimal briefings
**Impact:** No improvement -- same as current state.
**Mitigation:** The template is clearly structured with labeled fields. LLMs follow structured templates reliably. The calibration example provides a concrete reference. If the PM consistently ignores it, a follow-up issue can add lightweight validation.

## Race Conditions

None -- this is a persona document change with no concurrent access concerns.

## No-Gos (Out of Scope)

- Changes to `tools/valor_session.py` or the `create` subcommand
- Changes to the worker or bridge code
- Automated enforcement/validation of briefing completeness
- Per-stage specialized templates
- Changes to existing gate rules (Rule 1, Rule 2)
- Changes to the Stage-to-Model Dispatch Table content (only cross-referencing it)

## Update System

No update system changes required -- this modifies a persona markdown file that is pulled via `git pull` during the standard update process. No new dependencies, no config files, no migration steps.

## Agent Integration

No agent integration required -- this is a persona document change. The PM session reads `config/personas/project-manager.md` as part of its system prompt assembly. No MCP server changes, no `.mcp.json` changes, no bridge imports needed.

## Documentation

- [x] The plan document itself (`docs/plans/pm-dev-session-briefing.md`) serves as documentation
- [x] Update `docs/features/README.md` index table if a feature doc for PM dispatch quality doesn't exist
- [x] No separate feature doc needed -- the persona file IS the documentation

## Success Criteria

- [x] `config/personas/project-manager.md` "Dispatch Message Format" section replaced with structured briefing template
- [x] Template includes all six fields: Problem Summary, Key Files, Prior Stage Findings, Constraints, Current State, Acceptance Criteria
- [x] `--model` shown as required in the invocation example with cross-reference to dispatch table
- [x] Invocation note clarifies `python -m tools.valor_session create` (no subshell activate)
- [x] Calibration example for PLAN stage included
- [x] Existing gate rules, escalation policy, and dispatch table UNCHANGED
- [x] Lint clean: `python -m ruff check config/` passes (markdown file, should be no-op)

## Team Orchestration

### Team Members

- **Builder (template-update)**
  - Name: template-builder
  - Role: Replace the Dispatch Message Format section in config/personas/project-manager.md
  - Agent Type: builder
  - Resume: false

- **Validator (verify-template)**
  - Name: template-validator
  - Role: Verify the template is correct, existing sections unchanged, and format is clean
  - Agent Type: validator
  - Resume: false

## Step by Step Tasks

### 1. Replace Dispatch Message Format section
- **Task ID**: build-template
- **Depends On**: none
- **Validates**: `grep -c "Problem Summary" config/personas/project-manager.md` returns >= 1
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: false
- Read `config/personas/project-manager.md` in full
- Replace lines 121-159 (the "Dispatch Message Format" section through the `---` separator) with the new structured briefing template from the Technical Approach above
- Add the invocation note with `--model` as required
- Add the calibration example for PLAN stage
- Ensure no other sections are modified
- Commit on main: "Expand PM dispatch briefing template with structured fields, --model requirement, and PLAN example"

### 2. Validate template
- **Task ID**: validate-template
- **Depends On**: build-template
- **Assigned To**: template-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify "Dispatch Message Format" section contains: Problem Summary, Key Files, Prior Stage Findings, Constraints, Current State, Acceptance Criteria
- Verify `--model` appears as required in invocation example
- Verify "python -m tools.valor_session create" appears (no subshell activate)
- Verify calibration example is present
- Verify Hard Rules (Rule 1, Rule 2) are unchanged
- Verify Stage-to-Model Dispatch Table is unchanged
- Verify Escalation Policy is unchanged

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Template has Problem Summary | `grep -c "Problem Summary" config/personas/project-manager.md` | >= 2 (template + example) |
| Template has Key Files | `grep -c "Key Files" config/personas/project-manager.md` | >= 2 |
| Template has Prior Stage Findings | `grep -c "Prior Stage Findings" config/personas/project-manager.md` | >= 2 |
| Template has Constraints | `grep -c "Constraints" config/personas/project-manager.md` | >= 1 |
| --model is required | `grep -c "model.*REQUIRED\|REQUIRED.*model" config/personas/project-manager.md` | >= 1 |
| No subshell activate | `grep -c "source.*activate" config/personas/project-manager.md` | 0 |
| Rule 1 unchanged | `grep -c "CRITIQUE is Mandatory" config/personas/project-manager.md` | 1 |
| Rule 2 unchanged | `grep -c "REVIEW is Mandatory" config/personas/project-manager.md` | 1 |
| Calibration example present | `grep -c "Calibration Example" config/personas/project-manager.md` | >= 1 |

## Critique Results

| Severity | Critic(s) | Finding | Addressed |
|----------|-----------|---------|-----------|
| CONCERN | Skeptic, Operator, Archaeologist | No compliance/observability mechanism — plan assumes LLMs follow templates but provides no self-check or detection | Add Pre-Dispatch Self-Check subsection to persona; add post-merge smoke test to Success Criteria |
| CONCERN | Adversary | Calibration example omits `--model opus` from invocation block, defeating one of the six stated fixes | Expand calibration example invocation to show `--model opus` with comment |
| CONCERN | Adversary, Archaeologist | Freeform fields allow semantic emptiness (PM writes "See issue" or "TBD") — structurally compliant but contextually hollow | Add inline ❌/✅ anti-patterns within each Required Field description |
| CONCERN | Simplifier | Calibration example Key Files section lists `tools/valor_session.py` (out of scope per No-Gos) and `PR #909` (not a file) | Remove out-of-scope and non-file entries from example Key Files list |
| CONCERN | Operator | Verification greps are not section-scoped — a match in calibration example or leftover fragment false-positives the check | Rewrite all verification commands with section-scoped sed/grep pattern |
| NIT | Skeptic | Calibration example uses this plan's own issue — hypothetical ideal, not real trace | Mark as "(DRAFT - replace with first real post-merge briefing)" |
| NIT | Simplifier | "What NOT to Include" section adds cognitive load — exclusions are implied by Required Fields | Consider removing or collapsing into a single sentence |

---

## Open Questions

None -- scope is fully defined by the issue and constrained to a single file change.
