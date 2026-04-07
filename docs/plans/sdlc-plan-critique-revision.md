---
status: Planning
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/779
last_comment_id:
---

# SDLC Skill Gaps: Propagation Check, Shallow Critique Findings, No Revision Pass

## Problem

Three related gaps were identified working through a real plan-to-build cycle on issue #778 (fix-worker-concurrency). Each gap is a skill file (Markdown) edit only — no code changes required.

**Current behavior:**

1. `do-plan` runs spikes, updates Technical Approach, then writes tasks with no step to verify task steps match the updated approach. In the #778 plan, Task 1 still said "msgpack-encoded" after spike-2 confirmed `json.dumps()` was correct.

2. `do-plan-critique` finding format has `SEVERITY / LOCATION / FINDING / SUGGESTION` only. CONCERN-severity findings arrive with reviewer-depth suggestions that require the builder to re-investigate implementation details. In #778, three of five concerns had non-obvious guards: a dict-miss guard, a `CancelledError` guard on `t.exception()`, and the reason `asyncio.to_thread()` was needed.

3. The SDLC router sends `READY TO BUILD` directly to `/do-build` even when concerns exist. The builder receives the critique table as a to-do list rather than a clean, already-revised plan.

**Desired outcome:**

1. A propagation check step after all tasks are written catches task steps that contradict the current Technical Approach before the plan is committed.
2. Each CONCERN or BLOCKER finding includes an **Implementation Note** — the specific guard condition, call signature, or gotcha that makes the fix unambiguous.
3. After `READY TO BUILD (with concerns)`, a plan revision pass applies all concern findings to the plan text before build starts.

## Prior Art

- **PR #472** (Add CRITIQUE stage to SDLC pipeline between PLAN and BUILD) — Added critique as a gate. Did not address finding depth or post-critique revision path.
- **PR #802** (fix(sdlc): enforce CRITIQUE and REVIEW gates in PM persona) — Enforced critique gate completion. Did not address finding format or concern routing.
- **Issue #463** (Add CRITIQUE stage + fix critic hallucinations) — Introduced SOURCE_FILES context injection in v1.1.0. Hallucination fix only, no finding-depth change.

No prior attempts addressed propagation check, Implementation Note field, or concern-triggered revision pass.

## Spike Results

No spikes needed. All three changes are Markdown edits to skill instruction files. File paths are confirmed from recon. No code is modified.

## Data Flow

This plan modifies three instruction files that the SDLC pipeline reads at runtime:

1. **`do-plan/SKILL.md`** — Adds propagation check step at end of Phase 2 (after tasks written, before commit). Builder reads this during plan creation.
2. **`do-plan-critique/SKILL.md`** — Adds Implementation Note field to finding format. Critic agents read this when generating findings.
3. **`sdlc/SKILL.md`** — Adds `READY TO BUILD (with concerns)` row to dispatch table. PM session reads this to determine next stage.
4. **`do-plan/PLAN_TEMPLATE.md`** — Adds Implementation Note column to `## Critique Results` table. Plan documents inherit this on creation.

No runtime data flow changes. These are instruction changes that affect agent behavior when reading skill files.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: Critique finding format gains one field (Implementation Note). Existing findings without the field remain valid — the field is required for new CONCERN/BLOCKER findings only.
- **Coupling**: None — all changes are to standalone instruction files
- **Data ownership**: No change
- **Reversibility**: Trivial — revert the three Markdown file edits

## Appetite

Small — three targeted Markdown edits plus one template column addition. No code changes. Estimated 30-60 minutes of builder time.

## Solution

### Change 1: `do-plan/SKILL.md` — Propagation Check

Add a **Phase 2.6: Propagation Check** step after all task steps are written and before committing. The check instructs the builder to:

1. Re-read the **Technical Approach** section (or spike findings if no Technical Approach section exists)
2. Scan each task bullet for encoding choices, library selections, function names, and pattern names
3. Flag any task bullet that contradicts or predates spike findings
4. Update divergent task steps before committing

The step should include a concrete example: if spike-2 found `json.dumps()` and a task bullet still says "msgpack-encoded", update the task.

### Change 2: `do-plan-critique/SKILL.md` — Implementation Note Field

Extend the critic finding format to include a fifth field for CONCERN and BLOCKER severity:

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference in the plan
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
IMPLEMENTATION NOTE: [CONCERN/BLOCKER only] The specific guard condition, call signature,
  or gotcha that makes this implementable without re-investigation. If you cannot write
  this note, the finding is not yet specific enough.
```

NITs are exempt. Update the critic instructions and the Step 5 report format to include Implementation Note in the Blockers and Concerns sections.

Also update the `## Critique Results` table template in `PLAN_TEMPLATE.md` to include an `Implementation Note` column.

### Change 3: `sdlc/SKILL.md` — Concern-Triggered Revision Pass

Update dispatch table Row 4 to distinguish two sub-cases:

| # | State | Dispatch | Note |
|---|-------|----------|------|
| 4a | Plan critiqued (READY TO BUILD, zero concerns) | `/do-build` | No revision needed |
| 4b | Plan critiqued (READY TO BUILD, concerns present) | `/do-plan {slug}` with directive to apply concern findings | Revision pass before build |

After the revision pass completes, the next SDLC invocation finds Row 4a and dispatches `/do-build`.

Alternatively (simpler), update the Outcome Contract table in `do-plan-critique/SKILL.md`:

| Verdict | SDLC Action |
|---------|-------------|
| READY TO BUILD (no concerns) | Proceed to `/do-build` |
| READY TO BUILD (with concerns) | Return to `/do-plan` to apply concern findings, then build |
| NEEDS REVISION | Return to `/do-plan` with findings |
| MAJOR REWORK | Return to issue discussion |

Both files need updating to be consistent.

## No-Gos (Out of Scope)

- Changing critic agent prompts or adding new critics — only the finding format changes
- Automated validation that Implementation Notes are present (enforcement is the critic's responsibility per instruction)
- Changing how NITs are handled — NITs remain advisory only
- Modifying `pipeline_graph.py` or any Python code — skill file edits only
- Changing the six-critic war-room structure

## Test Impact

No existing tests affected — the test suite does not test skill instruction files (Markdown). These are behavior instructions for agent sessions, not code under test. All changes are to `.claude/skills/` Markdown files.

## Update System

No update system changes required — this feature is purely internal to skill files. No new dependencies, no config propagation, no migration steps. The changes take effect immediately when agents read the updated skill files.

## Agent Integration

No agent integration changes required. The agent reads skill files directly via the Claude Code file system. No MCP server changes, no `.mcp.json` changes, no bridge changes. Updated instructions are visible to agents immediately after commit.

## Documentation

- [ ] Update `docs/features/sdlc-pipeline.md` to document the `READY TO BUILD (with concerns)` → revision → build path
- [ ] Add a note to `docs/features/README.md` if the sdlc-pipeline feature doc entry needs updating

## Success Criteria

- [ ] `do-plan/SKILL.md` includes a Phase 2.6 propagation check step that explicitly cross-references task steps against Technical Approach
- [ ] `do-plan-critique/SKILL.md` critic finding format includes `IMPLEMENTATION NOTE` field required for CONCERN and BLOCKER severity
- [ ] `do-plan-critique/SKILL.md` Step 5 report format includes Implementation Note in Blockers and Concerns sections
- [ ] `sdlc/SKILL.md` dispatch table Row 4 (or equivalent Outcome Contract) distinguishes zero-concern vs. with-concerns `READY TO BUILD` verdicts
- [ ] `do-plan/PLAN_TEMPLATE.md` `## Critique Results` table includes an Implementation Note column
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-files)**
  - Name: skill-builder
  - Role: Edit the three skill files and plan template per the solution spec
  - Agent Type: builder
  - Resume: true

- **Validator (skill-files)**
  - Name: skill-validator
  - Role: Verify all four acceptance criteria are met by reading the updated files
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 — Core:
- `builder` — General implementation
- `validator` — Read-only verification

## Step by Step Tasks

### 1. Edit `do-plan/SKILL.md` — Add Propagation Check
- **Task ID**: build-propagation-check
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: skill-builder
- **Agent Type**: builder
- Add Phase 2.6 section after Phase 2 task-writing and before Phase 2.7 (Sync Issue Comments)
- Section heading: `### Phase 2.6: Propagation Check`
- Instructions: re-read Technical Approach (or spike findings), scan each task bullet for encoding choices, library selections, function names, and pattern names; flag and update any bullet that contradicts current findings before committing
- Include a concrete example (msgpack vs json.dumps pattern)

### 2. Edit `do-plan-critique/SKILL.md` — Add Implementation Note Field
- **Task ID**: build-impl-note-field
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: skill-builder
- **Agent Type**: builder
- Add `IMPLEMENTATION NOTE` field to critic finding format (after SUGGESTION, required for CONCERN/BLOCKER, exempt for NIT)
- Update Step 5 report format: Blockers and Concerns sections each gain an `**Implementation Note**:` bullet
- Update Outcome Contract table to distinguish `READY TO BUILD (no concerns)` vs `READY TO BUILD (with concerns)`

### 3. Edit `sdlc/SKILL.md` — Update Dispatch Table Row 4
- **Task ID**: build-sdlc-routing
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: skill-builder
- **Agent Type**: builder
- Split Row 4 into Row 4a (zero concerns → do-build) and Row 4b (concerns present → do-plan revision pass)
- Add a note explaining that after revision pass, next SDLC invocation routes to Row 4a

### 4. Edit `do-plan/PLAN_TEMPLATE.md` — Add Implementation Note Column
- **Task ID**: build-template-column
- **Depends On**: none
- **Parallel**: true
- **Assigned To**: skill-builder
- **Agent Type**: builder
- Update `## Critique Results` table header to add `Implementation Note` column
- Example row: `| CONCERN | [agent-type] | [The concern raised] | [How/whether addressed] | [Guard condition or gotcha] |`

### 5. Validate All Changes
- **Task ID**: validate-all
- **Depends On**: build-propagation-check, build-impl-note-field, build-sdlc-routing, build-template-column
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Read `do-plan/SKILL.md` and confirm Phase 2.6 propagation check exists with concrete example
- Read `do-plan-critique/SKILL.md` and confirm Implementation Note field in finding format and Step 5 report
- Read `sdlc/SKILL.md` and confirm Row 4a / Row 4b split or equivalent
- Read `do-plan/PLAN_TEMPLATE.md` and confirm Critique Results table has Implementation Note column
- Verify all four acceptance criteria from the issue are met

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-pipeline.md` with the new revision pass path
- Check `docs/features/README.md` for any entry that needs updating

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Propagation check present | `grep -c "Propagation Check" .claude/skills/do-plan/SKILL.md` | output > 0 |
| Implementation Note present | `grep -c "IMPLEMENTATION NOTE" .claude/skills/do-plan-critique/SKILL.md` | output > 0 |
| Row 4b routing present | `grep -c "with concerns" .claude/skills/sdlc/SKILL.md` | output > 0 |
| Template column added | `grep -c "Implementation Note" .claude/skills/do-plan/PLAN_TEMPLATE.md` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. All acceptance criteria are clear from the issue. Recon confirmed all three files exist and the precise gaps. No human input needed before build.
