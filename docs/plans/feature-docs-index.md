---
status: In Progress
type: chore
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/56
---

# Feature Documentation Index

## Problem

There are 13 feature docs in `docs/features/` but nothing links to them. A new Claude session has no way to discover what features exist unless it happens to glob the directory. The make-plan skill template tells the documentarian to "Add entry to documentation index" but no such index exists. CLAUDE.md, `/prime`, and `/add-feature` don't mention the directory either.

**Current behavior:**
Feature docs are written as part of the plan workflow but never discovered or read by future sessions. Context that should inform future work is buried.

**Desired outcome:**
Feature docs are indexed in `docs/features/README.md`, discoverable via `CLAUDE.md` and `docs/README.md`, and the make-plan template explicitly references the index file.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

Pure documentation changes — no code, no tests. The issue body is practically a complete spec.

## Solution

### Key Elements

- **Feature index file**: `docs/features/README.md` with a table of all 13 existing features
- **Discovery links**: One-line references in `CLAUDE.md` and `docs/README.md` pointing to the index
- **Template clarification**: Make-plan skill and documentarian agent updated to reference the specific index file
- **Backfill**: All 13 existing feature docs added to the index

### Flow

**Discovery flow after fix:**
New session loads CLAUDE.md → sees `docs/features/README.md` in See Also → reads index if needed → drills into specific feature doc

**Documentation flow after fix:**
Plan ships → documentarian creates `docs/features/{name}.md` → adds row to `docs/features/README.md` index table

### Technical Approach

1. **Create `docs/features/README.md`** — Markdown table with columns: Feature (link), Description, Status. One row per existing feature doc.

2. **Update `docs/README.md`** — Add a Features section to the Documentation Index:
   ```markdown
   ### Features

   | Document | Description |
   |----------|-------------|
   | [Feature Index](features/README.md) | All implemented features with documentation |
   ```

3. **Update `CLAUDE.md`** — Add one line to the See Also table:
   ```markdown
   | `docs/features/README.md` | Feature index — look up how things work |
   ```

4. **Update `.claude/skills/make-plan/SKILL.md`** — Change the Documentation section's index reference from:
   ```markdown
   - [ ] Add entry to documentation index
   ```
   to:
   ```markdown
   - [ ] Add entry to `docs/features/README.md` index table
   ```

5. **Update `.claude/agents/documentarian.md`** — Add concrete indexing instruction under Discovery & Navigation:
   > When creating feature documentation, always add an entry to `docs/features/README.md`. The index is a markdown table with columns: Feature (link), Description (one line), Status.

## Rabbit Holes

- Don't auto-generate the index from frontmatter — manual curation is fine for 13 docs and the overhead of a build script isn't worth it
- Don't load feature docs into session context automatically — lazy loading via the index is the right pattern
- Don't change the feature doc format or add frontmatter schemas

## Risks

### Risk 1: Index gets stale
**Impact:** New features ship without updating the index, defeating the purpose
**Mitigation:** The make-plan template and documentarian agent both explicitly reference `docs/features/README.md`. The documentarian's quality checklist includes index updates.

## No-Gos (Out of Scope)

- Auto-generating the index from frontmatter or file system
- Loading feature docs into session context automatically
- Changing the feature doc format or frontmatter schema
- External documentation sites (Sphinx, MkDocs, etc.)
- Adding feature docs to the `/prime` onboarding flow

## Update System

No update system changes required. This is purely documentation within the repository. Changes propagate via normal git pull during updates.

## Agent Integration

No agent integration required — this is pure documentation with no code or tool changes.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/README.md` as the feature index
- [ ] Add entry to `docs/features/README.md` index table (self-referential — the index itself is documented)

### Inline Documentation
- [ ] No code changes — no inline docs needed

## Success Criteria

- [ ] `docs/features/README.md` exists with a table listing all 13 existing feature docs
- [ ] Each row has: linked feature name, one-line description, status
- [ ] `docs/README.md` has a Features section linking to the index
- [ ] `CLAUDE.md` See Also table includes `docs/features/README.md`
- [ ] `.claude/skills/make-plan/SKILL.md` references `docs/features/README.md` explicitly
- [ ] `.claude/agents/documentarian.md` has concrete indexing instructions
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (index)**
  - Name: index-builder
  - Role: Create the feature index and update all reference points
  - Agent Type: documentarian
  - Resume: true

- **Validator (index)**
  - Name: index-validator
  - Role: Verify all links work and all features are indexed
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create feature index and update references
- **Task ID**: build-index
- **Depends On**: none
- **Assigned To**: index-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Read all 13 files in `docs/features/*.md` to extract feature names and descriptions
- Create `docs/features/README.md` with the index table
- Add Features section to `docs/README.md`
- Add `docs/features/README.md` to CLAUDE.md See Also table
- Update `.claude/skills/make-plan/SKILL.md` documentation section to reference `docs/features/README.md`
- Update `.claude/agents/documentarian.md` with concrete indexing instructions

### 2. Validate index completeness
- **Task ID**: validate-index
- **Depends On**: build-index
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `docs/features/README.md` has exactly 13 feature entries
- Verify each entry links to an existing file
- Verify `docs/README.md` links to the index
- Verify `CLAUDE.md` See Also table includes the index
- Verify make-plan SKILL.md references the specific file
- Verify documentarian agent has indexing instructions
- Run all validation commands

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-index
- **Assigned To**: index-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `test -f docs/features/README.md` - verify index exists
- `grep -c '\[.*\](.*\.md)' docs/features/README.md` - count linked entries (should be 13)
- `grep 'features/README.md' docs/README.md` - verify docs index links to feature index
- `grep 'features/README.md' CLAUDE.md` - verify CLAUDE.md references the index
- `grep 'docs/features/README.md' .claude/skills/make-plan/SKILL.md` - verify make-plan template updated
- `grep 'docs/features/README.md' .claude/agents/documentarian.md` - verify agent updated
