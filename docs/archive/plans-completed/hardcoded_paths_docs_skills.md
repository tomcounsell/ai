---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/445
last_comment_id:
---

# Scan and refactor hardcoded /Users/valorengels paths in docs and skills

## Problem

PR #438 eliminated hardcoded `/Users/valorengels` paths from production code, scripts, and plists. However, ~35 remaining references persist in documentation and skill files.

**Current behavior:**
Docs and skills reference `/Users/valorengels/src/ai` and `/Users/valorengels/src` as absolute paths, making them incorrect on other machines and misleading as canonical examples.

**Desired outcome:**
Zero hardcoded `/Users/valorengels` paths in any `.md` file (excluding `docs/plans/` which may reference the issue). All replaced with `~/src/ai`, `~/src`, or `$HOME`-relative paths as contextually appropriate.

## Prior Art

- **PR #438**: Config consolidation: eliminate hardcoded paths, unify settings -- Addressed production code/scripts/plists. This issue covers the remaining docs/skills.
- **PR #382**: Patch tech debt: hardcoded paths and deprecated APIs -- Earlier pass at hardcoded paths in code.
- **Issue #416**: Config consolidation (parent issue) -- Closed after PR #438 merged, but docs were out of scope.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Find-and-replace in main worktree**: Replace all `/Users/valorengels/src/ai` with `~/src/ai`, `/Users/valorengels/src` with `~/src` in docs and skill files
- **Context-sensitive replacement**: Some paths in shell command examples should use `~/src/ai`, while paths in explanatory prose may need `~/src` or just the relative form

### Technical Approach

Replacement rules (applied in order to avoid partial matches):
1. `/Users/valorengels/src/ai` -> `~/src/ai` (most specific first)
2. `/Users/valorengels/src` -> `~/src` (catch remaining)
3. Manual review of edge cases (e.g., plist XML examples in setup.md should use `$HOME` since `~` does not expand in plist XML)

### Files in Scope (main worktree only, ignore `.worktrees/`)

**Skills (4 files):**
- `config/SOUL.md` (5 refs)
- `config/personas/_base.md` (1 ref)
- `.claude/skills/checking-system-logs/SKILL.md` (8 refs)
- `.claude/skills/do-build/PR_AND_CLEANUP.md` (2 refs)
- `.claude/skills/do-build/SKILL.md` (1 ref)
- `.claude/skills/sdlc/SKILL.md` (1 ref)

**Docs (10 files):**
- `docs/guides/setup.md` (6 refs)
- `docs/features/workspace-safety-invariants.md` (3 refs)
- `docs/features/worktree-sdk-compatibility.md` (1 ref)
- `docs/features/scale-job-queue-with-popoto-and-worktrees.md` (1 ref)
- `docs/features/reflections.md` (1 ref)
- `docs/features/deployment.md` (2 refs)
- `docs/guides/upgrade-workflow.md` (1 ref)
- `docs/guides/tool-rebuild-requirements.md` (1 ref)
- `tools/README.md` (1 ref)
- `tools/telegram_history/README.md` (1 ref)

**Root:**
- `CLAUDE.md` (1 ref)

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- this is pure documentation editing.

### Empty/Invalid Input Handling
- Not applicable -- no code changes.

### Error State Rendering
- Not applicable -- no code changes.

## Test Impact

No existing tests affected -- this is a documentation-only change with no code modifications. No tests reference hardcoded user paths.

## Rabbit Holes

- Do not touch `.worktrees/` files -- they are copies of the main tree and will be updated when worktrees are recreated
- Do not modify `docs/plans/` files -- they may legitimately reference the issue context
- Do not attempt to make paths dynamically resolved in markdown -- just use `~` convention

## Risks

### Risk 1: plist examples in setup.md need $HOME not ~
**Impact:** Setup instructions would be wrong if ~ is used in plist XML
**Mitigation:** Use `$HOME` expansion syntax in plist XML examples, `~` everywhere else

## Race Conditions

No race conditions identified -- all operations are documentation edits with no concurrency concerns.

## No-Gos (Out of Scope)

- Production code changes (already handled by PR #438)
- `.worktrees/` directory files
- `docs/plans/` directory files
- Changing how paths are resolved at runtime

## Update System

No update system changes required -- this is purely a documentation cleanup.

## Agent Integration

No agent integration required -- no code, tools, or MCP servers are affected.

## Documentation

- [ ] No new feature docs needed -- this IS the documentation cleanup
- [ ] Verify CLAUDE.md gws path is correctly updated

## Success Criteria

- [ ] `grep -rn '/Users/valorengels' --include='*.md' . | grep -v .git/ | grep -v docs/plans/ | grep -v .worktrees/` returns 0 results
- [ ] All replacements are contextually correct (~/src/ai, ~/src, or $HOME as appropriate)
- [ ] plist XML examples use $HOME/src/ai not ~/src/ai
- [ ] Tests pass (`/do-test`)
- [ ] PR opened linking to issue #445

## Team Orchestration

### Team Members

- **Builder (docs-cleanup)**
  - Name: docs-builder
  - Role: Find and replace hardcoded paths across all docs and skill files
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Replace hardcoded paths in skill files
- **Task ID**: build-skills
- **Depends On**: none
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `/Users/valorengels/src/ai` with `~/src/ai` in config/SOUL.md, config/personas/_base.md, .claude/skills/**/*.md
- Replace `/Users/valorengels/src` with `~/src` for remaining refs

### 2. Replace hardcoded paths in doc files
- **Task ID**: build-docs
- **Depends On**: none
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace paths in all docs/**/*.md files
- Use `$HOME/src/ai` for plist XML examples in setup.md
- Replace `/Users/valorengels/src` with `~/src` for remaining refs

### 3. Replace hardcoded paths in root and tools files
- **Task ID**: build-root
- **Depends On**: none
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Update CLAUDE.md gws path
- Update tools/README.md and tools/telegram_history/README.md

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-skills, build-docs, build-root
- **Assigned To**: docs-builder
- **Agent Type**: validator
- **Parallel**: false
- Run scan command to verify 0 results
- Manual review of plist examples for $HOME usage

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| No hardcoded paths | `grep -rn '/Users/valorengels' --include='*.md' . \| grep -v .git/ \| grep -v docs/plans/ \| grep -v .worktrees/` | exit code 1 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None -- scope is fully defined by the grep scan and replacement rules are straightforward.
