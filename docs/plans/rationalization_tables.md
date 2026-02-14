---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-14
tracking: https://github.com/tomcounsell/ai/issues/102
---

# Rationalization Tables for Discipline-Enforcing Prompts

## Problem

Agents rationalize skipping process. They use plausible-sounding excuses to avoid discipline — rubber-stamping reviews, writing vague plans, committing untested code, claiming "it's obvious" instead of verifying.

**Current behavior:**
- Code reviewer can say "looks good" without actually running code or checking edge cases
- Validator can trust builder's self-reported output instead of independently verifying
- Plan-maker can produce vague success criteria, skip risk analysis, or write grab-bag plans
- Builder can make large unfocused commits, skip linting "just this once", or bundle unrelated changes

**Desired outcome:**
- Each discipline-enforcing prompt includes a rationalization table that explicitly closes the most common loopholes
- Tables are built from real agent behavior (not hypothetical) — patterns observed in session logs and validator failures
- Agents encounter their own excuse reflected back at them before they can use it

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (prompt engineering, no alignment needed)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Code reviewer rationalization table**: Counters for rubber-stamping, skipping test review, "the CI will catch it"
- **Validator rationalization table**: Counters for trusting builder output, skipping independent verification, "the tests pass so it's fine"
- **Plan-maker rationalization table**: Counters for vague scope, kitchen-sink plans, skipping risks
- **Builder commit discipline table**: Counters for large commits, skipping lint, bundling unrelated changes

### Flow

No user-facing flow — this is internal agent prompt hardening.

**Prompt loaded** → [Agent encounters rationalization situation] → [Sees own excuse in the table] → [Follows the "Reality" column instead]

### Technical Approach

Four prompt updates, all additive. No code changes.

**Relationship to #101 and #103:** Issues #101 (TDD enforcement) and #103 (verification-before-completion) already include rationalization tables for their specific domains. This issue covers the remaining gaps — code review, validation, plan-making, and commit discipline. Where a prompt is shared (e.g., builder.md), this work adds tables that don't overlap with #101/#103 content.

**1. Code Reviewer** (`.claude/agents/code-reviewer.md`)

Add a "Common Rationalizations" section after the review checklist:

| Rationalization | Reality |
|---|---|
| "Looks good to me" | Did you actually run the code? "Looks good" is not a review. |
| "The CI will catch any issues" | CI catches syntax, not logic. Review the logic. |
| "It's just a small change" | Small changes cause production outages. Review proportionally. |
| "The author is experienced" | Experience doesn't prevent bugs. Review the code, not the author. |
| "I don't have context on this area" | Then read the code until you do. That's the job. |
| "Tests pass, so it's fine" | Tests prove what's tested. Review what's NOT tested. |
| "It matches the existing pattern" | The existing pattern might be wrong. Evaluate independently. |
| "I'll flag it in the next PR" | No you won't. Flag it now or it never gets flagged. |

**2. Validator** (`.claude/agents/validator.md`)

Add a "Common Rationalizations" section after the validation checks:

| Rationalization | Reality |
|---|---|
| "The builder said tests pass" | Run them yourself. Builder reports are not evidence. |
| "The code looks correct" | Looking is not running. Execute the verification commands. |
| "It's a prompt-only change, nothing to test" | Run `ruff check` and `black --check` anyway. And read the prompt for coherence. |
| "The builder's report was detailed" | Detail is not accuracy. Verify independently. |
| "I checked the important parts" | Check ALL the criteria. Partial validation is no validation. |
| "This is the same pattern as last time" | Last time might have been wrong. Validate fresh. |

**3. Plan-Maker Skill** (`.claude/skills/make-plan/SKILL.md`)

Add a "Common Rationalizations" section in the "Anti-Patterns to Avoid" area:

| Rationalization | Reality |
|---|---|
| "The requirements are clear enough" | If success criteria aren't measurable, they're not clear. |
| "We can figure out the details during implementation" | That's what the plan is FOR. Figure them out now. |
| "This risk is unlikely" | Unlikely risks cause the worst surprises. Document and mitigate. |
| "It's all related, so one plan is fine" | Multiple features = multiple plans. Split the grab-bag. |
| "The appetite is flexible" | No. Appetite is a hard constraint. Cut scope, not budget. |
| "We don't need a rabbit holes section" | You always need one. Name what you won't build. |
| "Success criteria are obvious" | If they're obvious, writing them down takes 30 seconds. Do it. |
| "No-gos aren't necessary for a small plan" | Small plans need boundaries MORE because scope creep kills them faster. |

**4. Builder Commit Discipline** (`.claude/agents/builder.md`)

Add a "Commit Discipline" section with rationalization table (separate from the TDD table in #101 and the verification table in #103):

| Rationalization | Reality |
|---|---|
| "I'll split this commit later" | No you won't. Split it now. |
| "These changes are related" | Related is not atomic. One logical change per commit. |
| "Lint can wait until the end" | Lint failures compound. Fix them per-file, not per-project. |
| "This is just cleanup, no need for a message" | Every commit needs a clear message. "cleanup" is not a message. |
| "I'll push after the next change too" | Push now. Unpushed work is lost work. |
| "Skipping format check just this once" | There is no "just this once." Run `black --check`. |

## Rabbit Holes

- **Mining session logs for rationalizations**: The issue suggests reviewing actual agent logs. This is a good practice but shouldn't block this work. Start with the tables above (derived from observed patterns and superpowers' approach), then iterate based on real log evidence in future updates.
- **Automated rationalization detection**: Don't try to build a system that detects rationalizations in agent output programmatically. The LLM reading its own prompt is the detection mechanism.
- **Exhaustive tables**: Don't try to cover every possible excuse. Focus on the most common and most damaging rationalizations. The tables can grow over time.

## Risks

### Risk 1: Tables get ignored by the agent
**Impact:** Agent rationalizes past the rationalization table (meta-rationalization)
**Mitigation:** The tables are positioned near the action points in each prompt, not in an appendix. They use direct, confrontational language ("No you won't", "That's the job") that's harder to dismiss. Over time, session log review reveals which rationalizations still slip through, and we add counters.

### Risk 2: Tables make prompts too long
**Impact:** Important instructions get pushed out of the agent's attention window
**Mitigation:** Each table is ~8-10 rows. At ~2 lines per row, that's ~20 lines per prompt. The prompts are already well within context limits. Monitor for attention degradation if prompts grow significantly.

## No-Gos (Out of Scope)

- TDD-specific rationalizations (covered by #101)
- Verification-before-completion rationalizations (covered by #103)
- Automated rationalization detection in agent output
- Session log mining for new rationalizations (future iteration)
- Changes to agent code or behavior — purely prompt content

## Update System

No update system changes required — this is purely prompt engineering on agent and skill definitions.

## Agent Integration

No agent integration required — changes are to agent prompts (`.claude/agents/code-reviewer.md`, `.claude/agents/validator.md`, `.claude/agents/builder.md`) and the make-plan skill (`.claude/skills/make-plan/SKILL.md`), all loaded natively by Claude Code.

## Documentation

- [ ] Inline documentation: the rationalization tables themselves are self-documenting
- [ ] Add entry to `docs/features/README.md` index if a feature doc is created

## Success Criteria

- [ ] Code reviewer prompt includes rationalization table (8 entries)
- [ ] Validator prompt includes rationalization table (6 entries)
- [ ] Plan-maker skill includes rationalization table (8 entries)
- [ ] Builder prompt includes commit discipline rationalization table (6 entries)
- [ ] Tables positioned near relevant action points, not in appendices
- [ ] No overlap with #101 (TDD) or #103 (verification) rationalization tables
- [ ] All existing tests pass after prompt changes

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-engineer
  - Role: Add rationalization tables to code-reviewer, validator, plan-maker, and builder prompts
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: prompt-validator
  - Role: Verify all rationalization tables are present and correctly positioned
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add rationalization table to code reviewer
- **Task ID**: build-reviewer-table
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/agents/code-reviewer.md`
- Add "Common Rationalizations" section after the Review Checklist
- Include 8-entry table covering rubber-stamping, skipping verification, trusting CI
- Ensure table uses direct, confrontational language

### 2. Add rationalization table to validator
- **Task ID**: build-validator-table
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/agents/validator.md`
- Add "Common Rationalizations" section after Validation Checks
- Include 6-entry table covering trusting builder output, skipping independent verification
- Ensure no overlap with #103 (verification-before-completion) content

### 3. Add rationalization table to plan-maker skill
- **Task ID**: build-planmaker-table
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/skills/make-plan/SKILL.md`
- Add "Common Rationalizations" section near the "Anti-Patterns to Avoid" area
- Include 8-entry table covering vague scope, grab-bags, skipping risks/boundaries
- Position so agent sees it when writing plans, not just as reference material

### 4. Add commit discipline table to builder
- **Task ID**: build-commit-table
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read `.claude/agents/builder.md`
- Add "Commit Discipline" section with rationalization table after the SDLC Workflow section
- Include 6-entry table covering large commits, skipping lint/format, vague messages
- Ensure no overlap with #101 (TDD) or #103 (verification) content

### 5. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-reviewer-table, build-validator-table, build-planmaker-table, build-commit-table
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify code reviewer has rationalization table with 8 entries
- Verify validator has rationalization table with 6 entries
- Verify plan-maker skill has rationalization table with 8 entries
- Verify builder has commit discipline table with 6 entries
- Verify no overlap with #101/#103 rationalization tables
- Verify tables are positioned near action points, not appendices
- Run `pytest tests/ -v` to ensure nothing broke
- Run `ruff check . && black --check .`

## Validation Commands

- `cat .claude/agents/code-reviewer.md` - Verify rationalization table present
- `cat .claude/agents/validator.md` - Verify rationalization table present
- `cat .claude/skills/make-plan/SKILL.md` - Verify rationalization table present
- `cat .claude/agents/builder.md` - Verify commit discipline table present
- `pytest tests/ -v` - Ensure existing tests still pass
- `ruff check .` - Linting
- `black --check .` - Formatting
