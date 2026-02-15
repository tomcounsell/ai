---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-02-15
tracking: https://github.com/tomcounsell/ai/issues/117
---

# Repo Cleanup: Delete Obsolete Files and Directories

## Problem

The repo accumulated directories and files from earlier use cases (essays, SOPs, analytics dashboard, MCP catalog, image generation). These are dead code — zero imports, no references, never called. They add noise to searches, inflate context for agents reading the codebase, and make the repo look unmaintained.

## Appetite

**Size:** Small — deletion is straightforward, the audit is done.

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites.

## Solution

### Phase 1: Delete clearly obsolete directories

| Target | Reason |
|---|---|
| `essays/` | Research essays from Jun 2025. Zero imports. |
| `sops/` | SOP templates from pre-skills era. Replaced by `.claude/skills/`. Zero imports. |
| `generated_images/` | Temp image artifacts. Not git-tracked. |
| `analytics/` | Metrics dashboard. Never integrated — zero external callers. |
| `mcp_catalog/` | MCP server selection. Self-referencing only. Replaced by `.mcp.json` + skills. |
| `agents/` (top-level) | Workflow state persistence. Only README + .gitkeep tracked. State files gitignored. |
| `.zed/` | Zed editor config. No longer using Zed. |

### Phase 2: Delete obsolete files

| Target | Reason |
|---|---|
| `.env.backup` | Stale env backup from Aug 2025. Should never be in repo. |
| `.env.template` | Superseded by `.env.example` (which is current). |
| `.gitattributes` | Check if trivial/empty, delete if so. |

### Phase 3: Audit and clean borderline modules

**`intent/`** — Has one lazy import in `bridge/response.py`. Check if that code path is actually reachable. If not, delete both the module and the import.

**`.github/workflows/`** — Check contents. Delete if CI is unused. Keep if it has value.

### Phase 4: Review stale plans

Check `docs/plans/*.md` for plans tracking issues that are closed or abandoned. Delete plans that will never be built. Candidates:
- `social-media-announcements.md` — likely abandoned
- `telegram-message-tools.md` — may be superseded
- `telegram_desktop_control.md` — likely abandoned
- `fix_test_failures.md` — may be stale
- `update-popoto.md` — check if popoto work is done
- `sdk_modernization.md` — check if SDK migration is done

### Phase 5: Update references

After deleting, grep for any remaining references to deleted modules/dirs and clean them up:
- Import statements
- CLAUDE.md references
- Documentation mentions
- Config references
- Test files that import deleted modules

## Rabbit Holes

- **Archiving instead of deleting**: Git history preserves everything. Delete, don't move to an `archive/` folder.
- **Refactoring during cleanup**: Resist. This is a deletion PR, not a refactor. If something needs restructuring, create a separate issue.
- **Auditing every file**: The audit is done (issue #117). Trust it. Execute the deletions.

## Risks

### Risk 1: Deleting something still referenced
**Impact:** Import errors, broken functionality
**Mitigation:** Phase 5 greps for references. Tests run after deletion. The intent module gets special attention since it has one known import.

## No-Gos (Out of Scope)

- Restructuring remaining directories
- Renaming or moving active code
- Adding new features
- Updating dependencies
- Refactoring the bridge or agent code

## Update System

No update system changes required — we're only deleting obsolete code.

## Agent Integration

No agent integration required — no new tools or MCP changes.

## Documentation

- [ ] Update CLAUDE.md if it references any deleted directories
- [ ] Update README.md if it references any deleted directories
- [ ] Update `docs/features/README.md` if applicable

## Success Criteria

- [ ] All listed obsolete directories deleted
- [ ] All listed obsolete files deleted
- [ ] `intent/` module resolved (deleted or kept with justification)
- [ ] Stale plans reviewed and deleted where appropriate
- [ ] No remaining import references to deleted modules
- [ ] Existing tests still pass
- [ ] Ruff/Black clean

## Team Orchestration

### Team Members

- **Cleaner**
  - Name: repo-cleaner
  - Role: Delete files and clean up references
  - Agent Type: builder
  - Resume: true

- **Verifier**
  - Name: cleanup-verifier
  - Role: Verify nothing broke
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete obsolete directories and files
- **Task ID**: delete-obsolete
- **Depends On**: none
- **Assigned To**: repo-cleaner
- **Agent Type**: builder
- **Parallel**: false
- Delete directories: `essays/`, `sops/`, `generated_images/`, `analytics/`, `mcp_catalog/`, `agents/` (top-level, preserve `.claude/agents/`), `.zed/`
- Delete files: `.env.backup`, `.env.template`
- Check `.gitattributes` — delete if trivial
- Check `.github/workflows/` — delete if unused
- Commit: "Delete obsolete directories and files"

### 2. Audit and remove intent module
- **Task ID**: audit-intent
- **Depends On**: none
- **Assigned To**: repo-cleaner
- **Agent Type**: builder
- **Parallel**: true
- Check if `bridge/response.py`'s `from intent import classify_intent` is actually called in any reachable code path
- If dead: delete `intent/` directory and remove the import
- If alive: keep it, document why
- Commit: "Remove dead intent module" or "Keep intent module (documented)"

### 3. Review and delete stale plans
- **Task ID**: clean-stale-plans
- **Depends On**: none
- **Assigned To**: repo-cleaner
- **Agent Type**: builder
- **Parallel**: true
- For each plan in `docs/plans/`, check its tracking issue status (open/closed)
- Delete plans whose tracking issues are closed or whose features were abandoned
- Commit: "Remove stale plan documents"

### 4. Clean up references
- **Task ID**: clean-references
- **Depends On**: delete-obsolete, audit-intent
- **Assigned To**: repo-cleaner
- **Agent Type**: builder
- **Parallel**: false
- Grep for any remaining references to deleted modules/directories
- Update CLAUDE.md, README.md, docs if they mention deleted items
- Remove any test files that only test deleted modules
- Commit: "Clean up references to deleted modules"

### 5. Validate cleanup
- **Task ID**: validate-cleanup
- **Depends On**: delete-obsolete, audit-intent, clean-stale-plans, clean-references
- **Assigned To**: cleanup-verifier
- **Agent Type**: validator
- **Parallel**: false
- Verify deleted directories no longer exist
- Run `pytest tests/ -v` — confirm no import errors from deletions
- Run `ruff check . && black --check .`
- Grep for any orphaned references to deleted modules
- Verify CLAUDE.md and README.md are clean

## Validation Commands

- `ls essays/ sops/ generated_images/ analytics/ mcp_catalog/ agents/ .zed/ 2>&1` — should all fail
- `ls .env.backup .env.template 2>&1` — should fail
- `grep -r "from intent\|from analytics\|from mcp_catalog\|from sops\|from essays" --include="*.py" . 2>/dev/null` — should return nothing
- `pytest tests/ -v` — tests pass
- `ruff check .` — clean
- `black --check .` — clean
