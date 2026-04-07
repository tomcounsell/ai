---
status: Complete
type: chore
appetite: Small
owner: Valor Engels
created: 2026-02-27
tracking: https://github.com/valorengels/ai/issues/196
---

# PR #195 Post-Merge Tech Debt Cleanup

## Problem

Post-merge code review of PR #195 (SDLC user-level hooks + tech debt cleanup) identified 6 tech debt items. Five are low-severity nits in hooks and update code; one (test coverage for `_enqueue_continuation`) has already been addressed in a prior commit.

**Current behavior:**
1. `sync_claude_dirs` silently drops the `removed` counter from `sync_user_hooks` results
2. Quality gate detection in `validate_sdlc_on_stop.py` uses loose substring matching (`"black" in text` matches "blacker")
3. Hook deduplication in `_merge_hook_settings` checks only `command` string, ignoring `matcher` changes
4. `/tmp/sdlc_reminder_*` flag files accumulate indefinitely
5. `sys.path.insert(0, ...)` usage in hook scripts is undocumented regarding the standalone-only assumption

**Desired outcome:**
All five remaining items fixed. Code is cleaner, more correct, and better documented.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (straightforward fixes)
- Review rounds: 1 (code review)

These are five mechanical fixes with clear specifications from the issue. No design decisions needed.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Counter fix (hardlinks.py)**: Add missing `result.removed += hook_result.removed` line
- **Word-boundary matching (validate_sdlc_on_stop.py)**: Use regex `\b` word boundaries or check for specific command patterns instead of substring containment
- **Matcher-aware dedup (_merge_hook_settings)**: Compare both `command` and `matcher` fields; replace block when command matches but matcher differs
- **Temp file cleanup (sdlc_reminder.py)**: Add age-based cleanup of stale `/tmp/sdlc_reminder_*` files
- **sys.path.insert documentation**: Add inline comments in hook scripts noting the standalone-script assumption

### Flow

No user-facing flow changes. These are all internal code quality improvements.

### Technical Approach

1. **hardlinks.py line 115**: Add `result.removed += hook_result.removed` after the existing counter aggregations
2. **validate_sdlc_on_stop.py line 120-122**: Replace `cmd_name not in transcript_text` with regex word-boundary matching using `re.search(r'\b' + cmd_name + r'\b', transcript_text)`
3. **hardlinks.py _merge_hook_settings**: When `command` matches an existing hook, also compare `matcher`; if matcher differs, update the existing block's matcher in-place
4. **sdlc_reminder.py**: Add a `cleanup_stale_reminders()` function that removes `/tmp/sdlc_reminder_*` files older than 24 hours, called at the start of `main()`
5. **Hook scripts**: Add a brief comment above each `sys.path.insert` call: `# Standalone script — sys.path mutation is safe (never imported as library)`

## Rabbit Holes

- Redesigning the hook settings format or migration strategy -- just fix the dedup
- Building a comprehensive temp file management system -- simple age-based cleanup is sufficient
- Refactoring all hooks to avoid sys.path.insert entirely -- out of scope, they are standalone scripts

## Risks

### Risk 1: Regex word-boundary matching could miss valid tool invocations
**Impact:** False negatives in quality gate detection (wrongly flagging missing commands)
**Mitigation:** Test with realistic transcript content including tool invocations like `ruff check .`, `black --check`, `pytest tests/`

### Risk 2: Matcher update logic could corrupt existing settings.json
**Impact:** User hooks could be broken
**Mitigation:** Only update the matcher within an existing block when command matches; never remove blocks

## No-Gos (Out of Scope)

- Item 6 from issue #196 (`_enqueue_continuation` test coverage) -- already addressed, 383 lines of tests exist in `tests/test_enqueue_continuation.py`
- Redesigning the hook system architecture
- Changing the `/tmp` location for flag files to something persistent
- Adding a daydream-based cleanup job (simple inline cleanup is sufficient)

## Update System

The hardlinks.py changes affect the update system directly (`scripts/update/hardlinks.py`). The fix to `_merge_hook_settings` will propagate correctly on next update since it only changes dedup behavior -- existing correct hooks remain unchanged, and hooks with stale matchers will be updated.

No new dependencies or config files. No migration steps needed.

## Agent Integration

No agent integration required -- these are all internal code quality fixes to hooks and the update system. No MCP server changes, no bridge changes, no new tools.

## Documentation

### Inline Documentation
- [ ] Add `# Standalone script` comments above `sys.path.insert` calls in hook scripts (item 5)
- [ ] Update docstring for `_merge_hook_settings` to mention matcher-aware dedup

No feature documentation changes needed -- these are internal fixes with no user-facing impact.

## Success Criteria

- [ ] `result.removed` counter is aggregated in `sync_claude_dirs` (hardlinks.py)
- [ ] Quality gate detection uses word-boundary matching, not substring containment
- [ ] Hook dedup compares both `command` and `matcher` fields
- [ ] Stale `/tmp/sdlc_reminder_*` files are cleaned up (files > 24h old)
- [ ] `sys.path.insert` calls have standalone-script comments
- [ ] Tests pass (`/do-test`)
- [ ] `ruff check .` and `black --check .` pass

## Team Orchestration

### Team Members

- **Builder (tech-debt-fixes)**
  - Name: debt-fixer
  - Role: Apply all five fixes across hardlinks.py, validate_sdlc_on_stop.py, sdlc_reminder.py, and hook scripts
  - Agent Type: builder
  - Resume: true

- **Validator (verify-fixes)**
  - Name: debt-validator
  - Role: Verify all success criteria are met
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix removed counter in hardlinks.py
- **Task ID**: build-removed-counter
- **Depends On**: none
- **Assigned To**: debt-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `result.removed += hook_result.removed` at line 115 of `scripts/update/hardlinks.py`

### 2. Fix word-boundary matching in validate_sdlc_on_stop.py
- **Task ID**: build-word-boundary
- **Depends On**: none
- **Assigned To**: debt-fixer
- **Agent Type**: builder
- **Parallel**: true
- Replace substring check with `re.search(r'\b' + cmd_name + r'\b', transcript_text)` in `.claude/hooks/sdlc/validate_sdlc_on_stop.py`
- Also fix in `.claude/hooks/validators/validate_sdlc_on_stop.py` if the same pattern exists

### 3. Fix matcher-aware dedup in _merge_hook_settings
- **Task ID**: build-matcher-dedup
- **Depends On**: none
- **Assigned To**: debt-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `_merge_hook_settings` in `scripts/update/hardlinks.py` to compare matcher field alongside command
- When command matches but matcher differs, update the existing block's matcher

### 4. Add temp file cleanup to sdlc_reminder.py
- **Task ID**: build-tmp-cleanup
- **Depends On**: none
- **Assigned To**: debt-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `cleanup_stale_reminders()` function that removes `/tmp/sdlc_reminder_*` files older than 24 hours
- Call it at the start of `main()`

### 5. Add sys.path.insert comments in hook scripts
- **Task ID**: build-syspath-comments
- **Depends On**: none
- **Assigned To**: debt-fixer
- **Agent Type**: builder
- **Parallel**: true
- Add `# Standalone script — sys.path mutation is safe (never imported as library)` above each `sys.path.insert` in:
  - `.claude/hooks/sdlc/sdlc_reminder.py`
  - `.claude/hooks/sdlc/validate_sdlc_on_stop.py`
  - `.claude/hooks/sdlc/sdlc_context.py`
  - `.claude/hooks/sdlc/validate_commit_message.py`
  - `.claude/hooks/validators/validate_sdlc_on_stop.py`
  - `.claude/hooks/pre_tool_use.py`
  - `.claude/hooks/stop.py`
  - `.claude/hooks/post_tool_use.py`
  - `.claude/hooks/subagent_stop.py`

### 6. Validate all fixes
- **Task ID**: validate-all
- **Depends On**: build-removed-counter, build-word-boundary, build-matcher-dedup, build-tmp-cleanup, build-syspath-comments
- **Assigned To**: debt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/` and verify pass
- Run `ruff check .` and verify pass
- Run `black --check .` and verify pass
- Verify all success criteria met

## Validation Commands

- `grep -n "result.removed" scripts/update/hardlinks.py` - confirms removed counter aggregation
- `grep -n "\\\\b" .claude/hooks/sdlc/validate_sdlc_on_stop.py` - confirms word-boundary matching
- `grep -n "matcher" scripts/update/hardlinks.py | grep -i "compare\|match\|differ"` - confirms matcher-aware dedup
- `grep -n "cleanup_stale" .claude/hooks/sdlc/sdlc_reminder.py` - confirms temp cleanup
- `grep -rn "Standalone script" .claude/hooks/` - confirms sys.path comments
- `pytest tests/` - all tests pass
- `ruff check .` - no lint issues
- `black --check .` - formatting OK
