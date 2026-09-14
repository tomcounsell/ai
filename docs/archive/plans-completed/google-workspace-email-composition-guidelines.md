---
status: Ready
type: chore
appetite: Small
owner: valorengels
created: 2026-04-11
tracking: https://github.com/tomcounsell/ai/issues/899
last_comment_id:
---

# Google Workspace Email Composition Guidelines

## Problem

The `google-workspace` skill's Gmail section has no guidance for composing outbound email on behalf of the user. This gap caused an agent to offer to "jump on a call" in a reply — inappropriate because the agent cannot participate in voice or video calls. Two additional gaps exist: no rule explicitly requiring draft-over-send for email, and no guidance on honest product representation in outreach.

**Current behavior:**
The Gmail section covers search, threading, label management, and attachment downloads. When an agent drafts or replies to email, it applies general writing instincts with no domain-specific constraints.

**Desired outcome:**
A `### Composing on Behalf of the User` subsection is added to the `## 📧 Gmail & Chat Guidelines` section of `google-workspace/SKILL.md`, covering three constraints: async CTAs only, draft-first, and honest representation.

## Freshness Check

**Baseline commit:** `30242bc331579d42847f379cbfc22ec7f2d49df1`
**Issue filed at:** 2026-04-11T07:52:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills/google-workspace/SKILL.md:128–168` (Gmail & Chat Guidelines section) — no `### Composing` subsection exists, confirmed

**Cited sibling issues/PRs re-checked:**
- #40 — "Add allowed-tools to google-workspace" — closed and merged; no conflict with this change

**Commits on main since issue was filed (touching referenced files):**
- None

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** No drift. Issue claims hold exactly. Safe to proceed.

## Prior Art

- **Issue #40**: [Skill] Add allowed-tools to google-workspace — Added `allowed-tools` frontmatter to scope the skill to specific Google API tools. Closed. No conflict with this change.

No prior issues or PRs attempted to add email composition guidelines.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It is a purely additive edit to a skill `.md` file.

## Solution

### Key Elements

- **New subsection**: `### Composing on Behalf of the User` added within the existing `## 📧 Gmail & Chat Guidelines` section of `.claude/skills/google-workspace/SKILL.md`
- **Async CTAs rule**: Prohibit offering calls, meetings, or any synchronous communication
- **Draft-first rule**: All outbound composition uses `gmail_create_draft`; never call a send tool without explicit user instruction
- **Honest representation rule**: When composing outreach about the user's product, represent it accurately — if automated, say so

### Flow

Agent receives email composition request → Applies `### Composing on Behalf of the User` constraints → Produces draft via `gmail_create_draft` → User reviews and sends manually

### Technical Approach

Single additive edit to `.claude/skills/google-workspace/SKILL.md`:

- Insert the new subsection after the existing `### Downloading Attachments` subsection and before the `## 📄 Docs, Sheets, and Slides` section header
- The subsection uses the same bullet-list style as the rest of the Gmail section
- No changes to any other file

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope — this is a documentation-only change.

### Empty/Invalid Input Handling
Not applicable — no code is being modified.

### Error State Rendering
Not applicable — no code is being modified.

## Test Impact

No existing tests affected — this is a purely additive edit to a behavioral skill guide (`.md` file). Skill files are not covered by automated tests; their effect is behavioral and validated through use.

## Rabbit Holes

- **Extending to other skill files** (LinkedIn, Telegram) — each skill file has its own context and should be addressed independently. Scope is Gmail only.
- **Adding a `validate_skill_file.py` linter** — would be useful but is a separate project; not worth blocking this small change.
- **Rewriting the entire Gmail section** — the existing structure is fine; we need one new subsection, nothing more.

## Risks

### Risk 1: Subsection placement breaks skill readability
**Impact:** Minor — the skill file is loaded as context; misplaced headers reduce clarity but don't break functionality.
**Mitigation:** Insert after `### Downloading Attachments`, before `## 📄 Docs, Sheets, and Slides` — a natural boundary.

## Race Conditions

No race conditions identified — all operations are synchronous single-file edits with no concurrency concerns.

## No-Gos (Out of Scope)

- Adding voice/identity guidelines to other skill files (LinkedIn, Telegram)
- Modifying the general write-operation safety rule in Core Principles
- Adding automation to enforce the draft-first rule at the MCP tool level

## Update System

No update system changes required — this feature is purely a documentation edit to a skill file that is part of the repo checkout. The update script (`scripts/remote-update.sh`) already pulls the latest repo changes, so the updated SKILL.md will propagate automatically on the next `/update` run.

## Agent Integration

No agent integration required — the google-workspace skill file (`.claude/skills/google-workspace/SKILL.md`) is a behavioral guide loaded into Claude's context. No MCP server changes, no `.mcp.json` changes, and no bridge changes are needed. The updated guidance takes effect whenever the skill is loaded.

## Documentation

- [ ] Update `.claude/skills/google-workspace/SKILL.md` with the new `### Composing on Behalf of the User` subsection (this is the primary deliverable — the skill file IS the documentation)

No separate `docs/features/` entry is needed — skill behavioral guides are self-contained in `.claude/skills/`.

## Success Criteria

- [ ] `.claude/skills/google-workspace/SKILL.md` Gmail section contains a `### Composing on Behalf of the User` subsection
- [ ] The subsection explicitly prohibits offering calls, meetings, or any synchronous communication
- [ ] The subsection states that composition defaults to draft via `gmail_create_draft` — no send without explicit user instruction
- [ ] The subsection includes guidance on honest product representation in outreach
- [ ] The edit is purely additive — no existing content removed or restructured

## Team Orchestration

### Team Members

- **Builder (skill-edit)**
  - Name: skill-editor
  - Role: Add `### Composing on Behalf of the User` subsection to the Gmail section of `.claude/skills/google-workspace/SKILL.md`
  - Agent Type: builder
  - Resume: true

- **Validator (skill-edit)**
  - Name: skill-validator
  - Role: Verify the new subsection is present, correctly placed, and covers all three rules
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add Composing subsection to SKILL.md
- **Task ID**: build-skill-edit
- **Depends On**: none
- **Validates**: manual grep check for subsection heading
- **Assigned To**: skill-editor
- **Agent Type**: builder
- **Parallel**: false
- Edit `.claude/skills/google-workspace/SKILL.md`
- Insert `### Composing on Behalf of the User` after `### Downloading Attachments` subsection
- Add three rules: async CTAs only, draft-first (`gmail_create_draft`), honest representation
- Keep bullet-list style consistent with rest of Gmail section

### 2. Validate the edit
- **Task ID**: validate-skill-edit
- **Depends On**: build-skill-edit
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm subsection heading exists in SKILL.md
- Confirm all three rules are present and clearly stated
- Confirm no existing content was removed or restructured
- Report pass/fail

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Subsection exists | `grep -n "Composing on Behalf" .claude/skills/google-workspace/SKILL.md` | output contains match |
| Async CTA rule present | `grep -n "async\|synchronous\|call" .claude/skills/google-workspace/SKILL.md` | output contains match |
| Draft-first rule present | `grep -n "gmail_create_draft" .claude/skills/google-workspace/SKILL.md` | output contains match |
| Honest representation present | `grep -n "honest\|automated\|represent" .claude/skills/google-workspace/SKILL.md` | output contains match |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — scope is fully defined. The plan is ready for build.
