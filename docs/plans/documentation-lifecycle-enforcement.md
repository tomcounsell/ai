---
status: Ready
type: feature
appetite: Medium: 3-5 days
owner: Valor
created: 2026-02-09
tracking: https://github.com/tomcounsell/ai/issues/69
---

# Documentation Lifecycle Enforcement

## Problem

The `/make-plan` and `/build` workflows don't enforce that documentation actually gets created or updated when features ship.

**Current behavior:**
- `/make-plan` hook validates that `## Documentation` heading exists, but not that it contains actionable tasks
- `/build` executes whatever tasks are in the plan but has no post-build validation that docs were created
- Completed plans sit in `docs/plans/` indefinitely instead of migrating to `docs/features/`
- No mechanism identifies related docs that need updates when a feature changes existing behavior
- Documentation can become stale or orphaned without detection

**Desired outcome:**
- Plans with empty or invalid Documentation sections are rejected
- Builds cannot complete (PR blocked) until documentation changes are verified
- Completed plans are automatically deleted after migrating content to feature docs
- Related documentation is auto-updated with GitHub issues created for human review of discrepancies
- Documentation count can go up OR down (feature removal = doc removal)

## Appetite

**Time budget:** Medium: 3-5 days

**Team size:** Solo

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Plan Documentation Validator**: Ensures `## Documentation` section contains actionable tasks with target paths
- **Build Completion Gate**: Validates docs were actually created/modified before PR can merge
- **Documentation Cascade Command** (`/update-docs`): Post-build parallel discovery + triage of all docs affected by a change, with targeted surgical edits (adapted from [cuttlefish's update-docs](https://github.com/tomcounsell/cuttlefish/blob/main/.claude/commands/update-docs.md))
- **Plan Migration & Cleanup**: Deletes completed plans after verifying feature docs exist

### Flow

**Plan created** → [Doc section validated] → **Plan ready** → [Build executes] → **Build complete** → [Doc gate validates] → **PR ready** → [/update-docs cascade runs] → [Targeted edits to affected docs] → [Issues for conflicts needing human review] → **Plan deleted** → **Done**

### Technical Approach

- New hook validators in `.claude/hooks/validators/`
- New `/update-docs` command (`.claude/commands/update-docs.md`) using parallel agent discovery pattern
- Enhanced `/build` command with doc gate step and post-build cascade trigger
- GitHub CLI for issue creation on conflicts
- Diff-based detection: compare doc state before/after build

### Documentation Cascade Pattern (from cuttlefish)

The `/update-docs` command runs after a build completes. It uses two parallel agents:

**Agent A — Explore the change:**
- Read the PR diff (`gh pr diff --name-only`) and changed files
- Extract: what was added/changed, key API surface, deviations from plan

**Agent B — Inventory all documentation:**
Search every doc location that could be affected:

| Location | What lives there |
|----------|-----------------|
| `CLAUDE.md` | Primary project guidance, architecture, rules |
| `docs/features/*.md` | Feature documentation |
| `docs/plans/*.md` | Plans that may reference this as prerequisite |
| `.claude/skills/` | Workflow skills that may reference tools or patterns |
| `.claude/commands/` | Slash commands |
| `config/SOUL.md` | Agent identity and behavior |

**Triage — Cross-reference change against docs:**
For each doc, ask:
- Does it **reference** the area that changed?
- Does it **depend on** the change?
- Does it **teach a pattern** this change modifies?
- Does it **orchestrate a workflow** using the changed components?

Skip unrelated files. Make surgical, targeted edits — not rewrites. Read before edit. Document what IS, not what WAS.

## Rabbit Holes

- **Semantic doc analysis**: Don't try to understand if docs are "good" — just verify they exist and were touched. The cascade makes targeted edits, not quality judgments.
- **Cross-repo doc updates**: Only handle docs within this repo
- **Doc generation from code**: Don't auto-generate docs from docstrings — agents write docs with human review
- **Full doc rewrites**: The cascade makes surgical edits. If a doc needs a major rewrite, create an issue instead.
- **Complex conflict resolution**: For discrepancies needing human judgment, create an issue — don't try to merge conflicting content

## Risks

### Risk 1: False positives blocking builds
**Impact:** Legitimate builds blocked because validator is too strict
**Mitigation:** Validator checks for file modification, not content quality. No override allowed - if docs need updating, update them.

### Risk 2: Orphaned GitHub issues
**Impact:** Too many discrepancy issues created, ignored by humans
**Mitigation:** Only create issues for HIGH and MED-HIGH confidence discrepancies. Include clear context in issue body.

### Risk 3: Accidental plan deletion
**Impact:** Plan deleted before feature doc properly captures content
**Mitigation:** Migration script validates feature doc exists and contains minimum sections before deleting plan.

## No-Gos (Out of Scope)

- Doc quality scoring or linting
- Automated doc generation from code
- Cross-repository documentation updates
- Version history for docs (git handles this)
- Doc preview/rendering validation

## Update System

No update system changes required — this feature is purely internal to the planning and build workflows.

## Agent Integration

No agent integration required — this is a hook/script enhancement to existing Claude Code workflows. The validators run as CLI tools invoked by hooks, not as MCP-exposed tools.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/documentation-lifecycle.md` describing the enforcement system
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings for new validator scripts
- [ ] Usage examples in script headers

## Success Criteria

- [ ] Plans without actionable Documentation section are rejected by hook
- [ ] `/build` fails if no docs were created/modified (unless plan explicitly states "no docs needed")
- [ ] `/update-docs` cascade discovers and triages affected docs using parallel agents
- [ ] `/update-docs` makes targeted surgical edits to affected docs (not rewrites)
- [ ] GitHub issues created for conflicts needing human review
- [ ] Completed plans are deleted after successful build with feature doc verified
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (validators)**
  - Name: validator-builder
  - Role: Create hook validator scripts
  - Agent Type: tool-developer
  - Resume: true

- **Builder (scripts)**
  - Name: script-builder
  - Role: Create migration and scanner scripts
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Wire validators into make-plan and build commands
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: system-validator
  - Role: Verify complete system works end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create plan documentation validator
- **Task ID**: build-plan-validator
- **Depends On**: none
- **Assigned To**: validator-builder
- **Agent Type**: tool-developer
- **Parallel**: true
- Create `.claude/hooks/validators/validate_documentation_section.py`
- Validate `## Documentation` section contains at least one `- [ ]` task
- Validate section references a target path (e.g., `docs/features/`)
- Exit 0 on success, exit 2 on failure with clear error message

### 2. Create doc change validator
- **Task ID**: build-doc-change-validator
- **Depends On**: none
- **Assigned To**: validator-builder
- **Agent Type**: tool-developer
- **Parallel**: true
- Create `scripts/validate_docs_changed.py`
- Accept plan path as argument
- Extract expected doc paths from plan's Documentation section
- Compare git diff to verify docs were modified (added, changed, OR removed)
- For feature removals: docs must be updated to reflect current state (no "deprecated" markers or historical notes)
- Support explicit "No documentation changes needed" in plan (rare - most changes affect docs)
- Exit 0 on success, exit 1 on failure (no override mechanism)

### 3. Create /update-docs cascade command
- **Task ID**: build-update-docs-command
- **Depends On**: none
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/commands/update-docs.md` adapted from cuttlefish's pattern
- Command accepts PR number, commit SHA, or change description as input
- Step 1: Launch two parallel agents — Agent A explores the change (diff, changed files, API surface), Agent B inventories all docs in the repo
- Step 2: Cross-reference using triage questions (references, dependencies, patterns, workflows)
- Step 3: Create task list of affected docs, ordered by dependency (foundational first)
- Step 4: Make targeted surgical edits — read before edit, preserve existing structure
- Step 5: Verify only intended files touched, create issues for conflicts needing human review
- Principles: match actual API (not plan), cross-reference don't duplicate, document what IS not what WAS

### 4. Create plan migration script
- **Task ID**: build-migration-script
- **Depends On**: none
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/migrate_completed_plan.py`
- Accept plan path as argument
- Verify feature doc exists at path specified in plan
- Verify feature doc contains minimum sections (# Title, content)
- Verify `docs/features/README.md` contains entry for feature
- Delete the plan file
- Update tracking issue to closed state

### 6. Integrate plan validator hook
- **Task ID**: build-hook-integration
- **Depends On**: build-plan-validator
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/make-plan/SKILL.md` hooks section
- Add `validate_documentation_section.py` to Stop hooks
- Test that plans without proper Documentation section are rejected

### 6. Integrate doc gate into build
- **Task ID**: build-gate-integration
- **Depends On**: build-doc-change-validator, build-update-docs-command, build-migration-script
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/commands/build.md`
- Add doc validation step after final validation task — PR blocked if docs not created/modified
- Add `/update-docs` cascade as post-build step (runs after PR is ready, before merge)
- Add plan deletion step on success (after feature doc verified)
- The cascade handles related doc discovery, triage, targeted edits, and issue creation for conflicts

### 7. Validate complete system
- **Task ID**: validate-system
- **Depends On**: build-hook-integration, build-gate-integration
- **Assigned To**: system-validator
- **Agent Type**: validator
- **Parallel**: false
- Test: Plan without Documentation section is rejected
- Test: Plan with empty Documentation section is rejected
- Test: Build without doc changes fails validation
- Test: Build with doc changes passes validation
- Test: Related docs scanner finds references
- Test: Completed plan is deleted after successful build
- Run all validation commands

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-system
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/documentation-lifecycle.md`
- Add entry to `docs/features/README.md` index
- Include usage examples and troubleshooting

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: system-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python .claude/hooks/validators/validate_documentation_section.py --plan-directory docs/plans --test` - Test plan validator
- `python scripts/validate_docs_changed.py docs/plans/test-plan.md --dry-run` - Test doc change validator
- `python scripts/migrate_completed_plan.py docs/plans/test-plan.md --dry-run` - Test migration script
- `cat .claude/commands/update-docs.md` - Verify cascade command exists with parallel discovery pattern
- `ruff check .claude/hooks/validators/ scripts/` - Lint new code
- `black --check .claude/hooks/validators/ scripts/` - Format check

