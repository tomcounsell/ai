---
status: Building
type: chore
appetite: Small
owner: Tom Counsell
created: 2026-05-09
tracking: https://github.com/tomcounsell/ai/issues/1360
last_comment_id: null
---

# Agent Definitions Cleanup — Remove Dead `get_definition()` and Stale `dev-session.md`

## Problem

The `agent/agent_definitions.py` surface carries residue from the Phase 5 dev-session removal. Three coupled items have drifted from the actual runtime contract:

**Current behavior:**

1. **Dead `get_definition()` at `agent/agent_definitions.py:124-145`.** Zero call sites in the codebase. Its docstring promises an "actionable error" when a stale PM persona invokes `Agent(subagent_type="dev-session")`, but the function is never called — the SDK's subagent resolution path never re-enters this Python module after `agents=` is forwarded to `ClaudeAgentOptions`. The actual stale-persona detection lives at `agent/sdk_client.py:940-948` (overlay grep at PM session startup).

2. **Stale `.claude/agents/dev-session.md` is worse than residue.** The file is hardlinked (link count = 2) to `~/.claude/agents/dev-session.md` by `scripts/update/hardlinks.py:106-110`, AND `agent/sdk_client.py:1611` sets `setting_sources=["user","local","project"]` — so the SDK loads `.claude/agents/*.md` from disk in addition to the programmatic registry. A stale PM persona calling `Agent(subagent_type="dev-session")` would resolve and dispatch successfully with `tools: ['*']`, exactly the dual-execution path Phase 5 was supposed to eliminate.

3. **Doc-to-code drift.** Multiple feature/architecture docs (`pm-dev-session-architecture.md` lines 406, 502, 509; `harness-abstraction.md:198`) still describe `get_definition()` as the enforcement mechanism. Plan `sdk_graceful_agent_fallback.md` (lines 64, 74, 83, 170) describes try/except `FileNotFoundError`; the shipped code at `agent/agent_definitions.py:49` uses `if not path.exists():` — covered by parallel issue #1350, not in scope here.

**Desired outcome:**

- `get_definition()` deleted (~22 lines).
- `.claude/agents/dev-session.md` deleted from repo. The hardlink in `~/.claude/agents/` is removed by the next `hardlinks.py::_cleanup_stale_commands` run.
- After deletion, a stale `Agent(subagent_type="dev-session")` dispatch fails fast with an SDK "unknown subagent" error — exactly the actionable signal the unbuilt `get_definition()` error path was supposed to provide.
- Doc references replaced with accurate descriptions of `agent/sdk_client.py:940-948`.
- Lock-in test added so a future re-add via skill template / merge conflict is caught.

## Freshness Check

**Baseline commit:** `53a64e7c5abbf5675ed79f3e42d2ce08a1331b9b`
**Issue filed at:** 2026-05-09T13:16:18Z
**Disposition:** **Unchanged**

**File:line references re-verified:**
- `agent/agent_definitions.py:124-145` — `get_definition()` exists exactly at these lines. Verified.
- `agent/agent_definitions.py:49` — `if not path.exists():` confirmed. Verified.
- `agent/sdk_client.py:940-948` — stale-persona detection (overlay grep + warning) confirmed. Verified at lines 940-948.
- `agent/sdk_client.py:1611` — `setting_sources=["user","local","project"]` confirmed. Verified.
- `scripts/update/hardlinks.py:104-110` — agent-sync block confirmed. Verified.
- `.claude/agents/dev-session.md` — exists, 2747 bytes, link count = 2 (`stat -f "%l"` returns 2). Verified.
- `docs/features/pm-dev-session-architecture.md` lines 406, 502, 509 — all three references to `get_definition()` confirmed. Verified.
- `docs/features/harness-abstraction.md:198` — reference to `get_definition()` confirmed. Verified.
- `docs/plans/sdk_graceful_agent_fallback.md` lines 64, 74, 83, 170 — try/except `FileNotFoundError` references confirmed. Verified.
- `tests/unit/test_agent_definitions.py` — exists, 132 lines. Verified.

**Cited sibling issues/PRs re-checked:**
- #1352 (parent investigation) — closed, served its purpose surfacing the three signals.
- #1350 (widen fallback for malformed YAML / OS errors) — open, parallel and independent. Footer cross-reference only.
- #1351 (cited in #1352 footer) — not directly relevant to this cleanup.

**Commits on main since issue was filed (touching referenced files):** none (issue filed today, no relevant commits in last 24 hours).

**Active plans in `docs/plans/` overlapping this area:** none. `sdk_graceful_agent_fallback.md` and `agentsession-harness-abstraction.md` are historical records of completed Phase 5 work; they reference `get_definition()` but are not active plans needing coordination.

**Additional drift discovered during freshness check (not enumerated in issue body):**
- `.claude/skills-global/add-feature/SKILL.md:102` lists `dev-session.md` as an example agent file.
- `docs/research/claude-code-feature-swot.md:92` references `dev-session.md` in an agent-types tree.
- `docs/guides/claude-code-feature-swot.md:92` is an apparent duplicate of the research doc, same reference.
- `docs/plans/agentsession-harness-abstraction.md:89` describes the `get_definition()` runtime guard that this plan is removing.
- `agent/agent_definitions.py:148-152` has an inline comment about `_EXPECTED_AGENT_FILES` excluding `dev-session.md` "Phase 5 cleanup" — comment can be simplified post-deletion (the file's existence outside `_EXPECTED_AGENT_FILES` is no longer notable once the file itself is gone).
- `tests/unit/test_agent_definitions.py:54-55, 102` have Phase 5 explanatory comments — still accurate, no changes needed.

These additions are folded into the Solution and Step by Step Tasks sections. The issue body's "Files to touch" list expands by 4 entries (skill, two swot docs, one inline code comment).

## Prior Art

- **Investigation #1352** (closed): "Reliability risk: agent_definitions — dead code and stale dev-session residue after Phase 5 cleanup" — the source of this plan. All three signals were surfaced and verified there.
- **Issue #1353** (closed via PR #1353, "fix(worker): call validate_agent_files() at startup"): wired `validate_agent_files()` into worker startup. Touches the same module but a different concern (file-existence audit, not the dead-code cleanup).
- **Issue #1350** (open): "Widen agent-definition fallback to cover malformed YAML and OS errors" — parallel work, not blocking. Footer cross-reference only.
- **Phase 5 dev-session removal** (historical, multiple PRs): removed dev-session from `_EXPECTED_AGENT_FILES` and from `get_agent_definitions()`. Did NOT remove `.claude/agents/dev-session.md` from disk (the gap this cleanup closes) and did NOT remove `get_definition()` (the dead code this cleanup removes).

No prior failed fixes — this is the first cleanup pass on these specific signals.

## Research

No relevant external findings — this is purely internal cleanup (deleting dead code, updating docs, adding a lock-in test). No external libraries, APIs, or ecosystem patterns involved.

## Data Flow

Skipped — change is isolated to a single Python function deletion plus doc/file deletions. No data-flow concerns.

## Architectural Impact

- **New dependencies:** none.
- **Interface changes:** `get_definition()` is removed from `agent.agent_definitions`'s public surface. Verified zero callers exist in the repo via exhaustive grep.
- **Coupling:** decreases. Removes a function whose contract (catch stale dev-session dispatches) is unenforceable and was duplicating concerns of `agent/sdk_client.py:940-948`.
- **Data ownership:** unchanged.
- **Reversibility:** trivial (`git revert`). Pure deletion of dead code; no runtime behavior changes for working call paths.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1 (standard PR review)

This is a deletion-and-doc-update plan with a lock-in test. No design decisions, no architectural shifts, no external dependencies.

## Prerequisites

No prerequisites — this work has no external dependencies. Standard repo checkout suffices.

## Solution

### Key Elements

- **Code deletion**: Remove `get_definition()` from `agent/agent_definitions.py`.
- **File deletion**: Remove `.claude/agents/dev-session.md` from the repo. The hardlink at `~/.claude/agents/dev-session.md` is cleaned up automatically by the next `hardlinks.py::_cleanup_stale_commands` run.
- **Doc updates**: Replace `get_definition()` references in three doc files (`pm-dev-session-architecture.md`, `harness-abstraction.md`, the add-feature skill), and update the two SWOT docs that list `dev-session.md` as an example agent file.
- **Plan footer**: Append a one-line cross-reference to #1350 in `docs/plans/sdk_graceful_agent_fallback.md` so future readers find the active follow-up work.
- **Lock-in test**: Add `test_dev_session_md_not_in_repo` asserting `(_AGENTS_DIR / "dev-session.md").exists() is False`. Catches accidental re-adds via skill template, hardlink misconfiguration, or merge conflict resolution.
- **Comment simplification**: Update the inline comment at `agent/agent_definitions.py:148-152` so it stops mentioning the now-deleted `dev-session.md` file. Keep the `_EXPECTED_AGENT_FILES` block; just rewrite the explanatory note to describe what's expected, not what was deliberately excluded.

### Flow

This is a deletion + lock-in flow, not a user flow. Sequence:

1. Run zero-callers grep one more time to re-verify before deletion.
2. Delete `get_definition()`.
3. Delete `.claude/agents/dev-session.md`.
4. Update doc references (5 files).
5. Append plan footer cross-reference to #1350.
6. Add lock-in test.
7. Run `pytest tests/unit/test_agent_definitions.py` — must pass including the new lock-in test.
8. Run the verification one-liner: `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); assert 'dev-session' not in d"`.
9. Run `python -m ruff format .` and `python -m ruff check .`.
10. Open PR.

### Technical Approach

- **Why deletion is safe**: the function has zero callers (verified by exhaustive grep), and the SDK never re-enters this Python module for subagent resolution after `agents=get_agent_definitions()` is forwarded to `ClaudeAgentOptions`. The actionable error in `get_definition`'s docstring is unreachable.
- **Why deletion improves the runtime contract**: post-deletion, a stale `Agent(subagent_type="dev-session")` dispatch fails fast with the SDK's "unknown subagent" error. That signal (a) actually reaches the user, and (b) is exactly what the unbuilt `get_definition` error path was supposed to provide. The "no unenforced contract" outcome is strictly better than the current "documented contract that no code enforces" state.
- **Why the hardlink concern is real, not theoretical**: `agent/sdk_client.py:1611` sets `setting_sources=["user","local","project"]`, so the SDK reads `.claude/agents/*.md` files from disk into its registry. A stale persona could resolve `dev-session` from disk (with `tools: ['*']`) even though `get_agent_definitions()` doesn't include it. Deleting the file closes that gap.
- **Why the inline comment at `agent_definitions.py:148-152` should be simplified, not left alone**: the current comment explains why `dev-session.md` is intentionally excluded from `_EXPECTED_AGENT_FILES`. After the file is deleted, that explanation becomes noise — there is no exclusion, the file simply does not exist. Replacing the comment with a forward-looking "files referenced by `get_agent_definitions()`; checked at bridge startup" keeps the docstring useful without preserving Phase 5 archaeology.
- **Why historical plans get a footer cross-reference, not a body rewrite**: `sdk_graceful_agent_fallback.md` and `agentsession-harness-abstraction.md` are records of completed work. Their bodies describe the state at plan time. Rewriting body text to match shipped code would erase useful history. Appending a "See also: #1350 for active follow-up" footer to `sdk_graceful_agent_fallback.md` gives readers the pointer they need without falsifying the historical record.
- **Why the SWOT docs are in scope**: they list `dev-session.md` as an example "Builder" agent. After deletion, that's misleading. Update them to remove the line — the rest of the tree is still accurate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [x] No new `except Exception: pass` blocks introduced. Existing handlers in `_parse_agent_markdown` (covered by `tests/unit/test_agent_definitions.py::TestParseAgentMarkdown::test_missing_file_logs_warning`) are unchanged.

### Empty/Invalid Input Handling
- [x] No new functions added; no new input-handling logic. The lock-in test asserts a static repo invariant (file does not exist) — no input variation applies.

### Error State Rendering
- [x] No user-visible output changes. The user-visible behavior change for stale dev-session dispatches (SDK "unknown subagent" error) is delegated to the SDK and is not under this plan's control. We rely on the SDK's existing error rendering.

## Test Impact

- [x] `tests/unit/test_agent_definitions.py` — UPDATE: add `test_dev_session_md_not_in_repo` asserting `(_AGENTS_DIR / "dev-session.md").exists() is False`. No changes to existing tests; the existing assertions (`"dev-session" not in defs`, `len(missing) == 3`, etc.) remain accurate post-cleanup.
- [x] `tests/unit/test_agent_definitions.py::TestGetAgentDefinitions::test_returns_all_agents_when_files_exist` — comment at line 54-55 references "Phase 5 note: dev-session was removed from get_agent_definitions()". Comment stays accurate post-cleanup; no edit needed.
- [x] `tests/unit/test_agent_definitions.py::TestValidateAgentFiles::test_detects_missing_files` — comment at line 102 references "Phase 5: dev-session.md removed from `_EXPECTED_AGENT_FILES`." Still accurate; no edit needed.

No other existing tests touch `get_definition` (it has zero callers including tests) or `dev-session.md`. The lock-in test is the only test surface change.

## Rabbit Holes

- **Don't widen the `_parse_agent_markdown` fallback** to cover malformed YAML / OS errors. That's #1350. Stay narrow.
- **Don't audit the other 31 unregistered agent files in `.claude/agents/`.** They are intentionally shared general-purpose subagents, confirmed via `scripts/update/hardlinks.py:104-110`. The audit in #1352 already cleared them.
- **Don't rewrite plan body lines in `sdk_graceful_agent_fallback.md`** (lines 64, 74, 83, 170). Historical record. Footer cross-reference only.
- **Don't change `setting_sources` SDK config or hardlink propagation.** Both are correct as designed; the SDK reading `.claude/agents/*.md` from disk is intended behavior for general-purpose subagents.
- **Don't migrate or refactor `validate_agent_files()`.** Its contract (check expected files exist at startup) is independent and was wired by #1353.

## Risks

### Risk 1: Hidden runtime caller of `get_definition()` exists outside the repo
**Impact:** A stale persona, external integration, or skill template calls `get_definition()` and crashes with `AttributeError` post-deletion.
**Mitigation:** Pre-deletion grep already returned zero callers across the repo. The function is not exported in `__all__` or any package `__init__.py`. External callers (if any) would have to import it directly, which is a contract they own. Re-run the grep one more time at build time as a final check.

### Risk 2: A future skill template re-adds `.claude/agents/dev-session.md`
**Impact:** Stale file reappears, re-introducing the dual-execution path.
**Mitigation:** The new lock-in test (`test_dev_session_md_not_in_repo`) catches re-adds. CI failure makes the regression visible immediately.

### Risk 3: Hardlink at `~/.claude/agents/dev-session.md` lingers on machines that don't run `/update` soon after
**Impact:** Stale file remains in user-scope agents directory until the next update sync runs `_cleanup_stale_commands`. During that window, a stale PM persona dispatch could still resolve dev-session from the user-scope file.
**Mitigation:** Acceptable. The window is short (next update cycle, typically same-day). Stale PM personas are themselves rare (warning logged at PM session startup per `sdk_client.py:940-948`). The combination of (a) needing a stale persona AND (b) being in the small window before the next update is a low-probability edge case. **Per critique C2:** worktree checkouts have an independent inode from main; the verification "file does not exist" only confirms worktree state, and the user-scope hardlink at `~/.claude/agents/dev-session.md` will keep resolving via `setting_sources` until `/update` runs on each machine. The PR body MUST explicitly tell reviewers and the author: "After merge, run `/update` (or `python scripts/update/run.py`) on each machine so `_cleanup_stale_commands` reaps the user-scope copy."

### Risk 4: Inline comment rewrite at `agent_definitions.py:148-152` introduces a typo or removes useful context
**Impact:** Future readers lose the Phase 5 archaeology that explained why `dev-session.md` was excluded.
**Mitigation:** The deletion of `dev-session.md` itself makes the archaeology moot. The new comment focuses on what `_EXPECTED_AGENT_FILES` is for (startup file-existence audit), which is the genuinely useful information. Keep the comment short and forward-looking.

## Race Conditions

No race conditions identified — all operations are synchronous, single-threaded edits to source files. The deletion of `.claude/agents/dev-session.md` does not race with hardlink cleanup because `_cleanup_stale_commands` runs idempotently on subsequent `/update` invocations and has no dependency on this PR's commit timing.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #1350] Widening the `_parse_agent_markdown` fallback to cover malformed YAML / OS errors is filed as #1350.

(All other items from the issue's "Out of scope" section are codified in **Rabbit Holes** above — they are deliberate non-goals, not deferred work, and don't need separate-slug tracking.)

## Update System

No update system changes required — this feature is purely internal repo cleanup. The existing `scripts/update/hardlinks.py::_cleanup_stale_commands` mechanism already handles removal of the hardlinked agent file from `~/.claude/agents/` when it disappears from the repo. No new dependencies, no new config, no migration needed.

## Agent Integration

No agent integration required — this is a bridge-internal change (deletion of dead Python code, deletion of a stale .md file, doc updates, lock-in test). No new MCP server, no new CLI entry point, no new bridge import path. The bridge and worker continue to use the unchanged `get_agent_definitions()` and `validate_agent_files()` surface.

## Documentation

### Feature Documentation
- [x] Update `docs/features/pm-dev-session-architecture.md` — replace `get_definition()` references at lines 406, 502, 509 with descriptions of the actual stale-persona detection at `agent/sdk_client.py:940-948` (overlay grep at PM session startup; logs `WARNING: PM persona overlay still contains Agent tool dispatch instructions`).
- [x] Update `docs/features/harness-abstraction.md:198` — same replacement pattern.

### Plan Cross-Reference
- [x] Append a single-line footer to `docs/plans/sdk_graceful_agent_fallback.md` referencing #1350 (active follow-up widening the fallback to malformed YAML / OS errors).

### Skill / Research Doc Updates
- [x] Update `.claude/skills-global/add-feature/SKILL.md:102` — remove `dev-session.md` from the example list. Replace with `code-reviewer.md` (the third remaining agent in `_EXPECTED_AGENT_FILES`) so the skill still has three concrete examples.
- [x] Update `docs/research/claude-code-feature-swot.md:92` — remove `dev-session.md` from the Builders subtree (Phase 5 already removed the runtime; the doc just hadn't caught up).
- [x] Update `docs/guides/claude-code-feature-swot.md:92` — same removal (this file appears to be a sibling of the research doc; verify both during edit).

### Inline Documentation
- [x] Simplify the inline comment at `agent/agent_definitions.py:148-152` — drop the "Phase 5 cleanup" archaeology, keep a concise statement of what `_EXPECTED_AGENT_FILES` is for.

[No `docs/features/README.md` index update needed — these are existing docs being corrected, not new feature docs being introduced.]

## Success Criteria

- [x] `agent/agent_definitions.py::get_definition` deleted (~22 lines including docstring).
- [x] `.claude/agents/dev-session.md` deleted from the repo.
- [x] Inline comment at `agent/agent_definitions.py:148-152` simplified to drop dev-session archaeology.
- [x] `docs/features/pm-dev-session-architecture.md` lines 406, 502, 509 updated to describe `agent/sdk_client.py:940-948`.
- [x] `docs/features/harness-abstraction.md:198` updated similarly.
- [x] `docs/plans/sdk_graceful_agent_fallback.md` gains a footer cross-link to #1350.
- [x] `.claude/skills-global/add-feature/SKILL.md:102` no longer references `dev-session.md`.
- [x] `docs/research/claude-code-feature-swot.md:92` and `docs/guides/claude-code-feature-swot.md:92` no longer reference `dev-session.md`.
- [x] `tests/unit/test_agent_definitions.py` adds `test_dev_session_md_not_in_repo` asserting `(_AGENTS_DIR / "dev-session.md").exists() is False`.
- [x] `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); assert 'dev-session' not in d"` passes.
- [x] Tests pass (`pytest tests/unit/test_agent_definitions.py -v`).
- [x] Lint and format clean (`python -m ruff check .`, `python -m ruff format --check .`).
- [x] Documentation updated (`/do-docs`).

## Team Orchestration

This is a Small-appetite plan; the builder handles all tasks sequentially. No parallel work, no specialist agents needed.

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Execute deletions, doc updates, and add lock-in test
  - Agent Type: builder
  - Resume: true

- **Validator (cleanup)**
  - Name: cleanup-validator
  - Role: Verify success criteria met
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard core tier: `builder`, `validator`. No specialists required.

## Step by Step Tasks

### 1. Pre-deletion sanity check
- **Task ID**: pre-check
- **Depends On**: none
- **Validates**: Zero callers of `get_definition` confirmed at build time
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Run `grep -rn "get_definition" "$(git rev-parse --show-toplevel)" --include="*.py" 2>/dev/null | grep -v "/\.venv/"`. Confirm only the definition itself appears. (Worktree-portable: uses `git rev-parse --show-toplevel` so the grep works whether run from main checkout or a worktree.)
- Run `grep -rn "dev-session.md" /Users/tomcounsell/src/ai/ --include="*.py" --include="*.md" 2>/dev/null | grep -v ".worktrees" | grep -v "/.venv/" | grep -v "/.git/"`. Confirm the matches are exactly: `add-feature/SKILL.md:102`, `agent_definitions.py:150` (inline comment), `claude-code-feature-swot.md` (research and guides copies), and `agentsession-harness-abstraction.md:89` (historical plan, leave alone). Any new match means new drift to address before deletion.

### 2. Delete dead code
- **Task ID**: delete-dead-code
- **Depends On**: pre-check
- **Validates**: `tests/unit/test_agent_definitions.py` (existing tests still pass)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `get_definition()` (lines 124-145 inclusive of docstring) from `agent/agent_definitions.py`.
- Simplify the inline comment block at lines 148-152. Replace with: `# Agent files referenced by get_agent_definitions(). Used by validate_agent_files()` / `# to check that all expected files exist on disk at process startup (worker and bridge).` (Updated per critique C1: PR #1353 wired the validator into both `worker/__main__.py:546-548` and `bridge/telegram_bridge.py:1051-1053`.)
- Delete the file `.claude/agents/dev-session.md` (use `git rm`).

### 3. Update feature docs
- **Task ID**: update-feature-docs
- **Depends On**: delete-dead-code
- **Validates**: Manual diff inspection; no `get_definition` references remain in `docs/features/`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `docs/features/pm-dev-session-architecture.md` lines 406, 502, 509. Replace the `get_definition()` references with descriptions of `agent/sdk_client.py:940-948` (overlay grep at PM session startup; logs warning if PM persona still contains `subagent_type="dev-session"`). Per critique N1, treat each line as a separate edit: line 406 is a Key Components table row → drop the row entirely (the function is gone; no replacement role exists in this module). Line 502 is prose → rewrite to describe the actual stale-persona detection at `sdk_client.py:940-948`. Line 509 is a Key Files table row whose Purpose column needs the `get_definition()` clause dropped — keep the row, simplify the description.
- Edit `docs/features/harness-abstraction.md:198` similarly: rewrite to describe `sdk_client.py:940-948` instead of `get_definition()`.

### 4. Append plan footer cross-reference
- **Task ID**: plan-footer
- **Depends On**: delete-dead-code
- **Validates**: Footer present at end of `docs/plans/sdk_graceful_agent_fallback.md`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Append a footer to `docs/plans/sdk_graceful_agent_fallback.md`:

  ```
  ---

  ## See Also

  - **#1350** — Active follow-up: widen the fallback in `_parse_agent_markdown()` to cover malformed YAML and OS errors (this plan documents only the original `FileNotFoundError` case; shipped code uses `path.exists()`, functionally equivalent for the missing-file case).
  ```

- Do NOT rewrite the plan body lines (64, 74, 83, 170) that mention `try/except FileNotFoundError`. Historical record.

### 5. Update skills and SWOT docs
- **Task ID**: update-skills-swot
- **Depends On**: delete-dead-code
- **Validates**: No `dev-session.md` references remain in `.claude/skills-global/` or `docs/research/` or `docs/guides/`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Edit `.claude/skills-global/add-feature/SKILL.md:102`. Replace `dev-session.md — SDK-driven session agent` with `code-reviewer.md — read-only code review` so the example list still has three concrete agents.
- Edit `docs/research/claude-code-feature-swot.md:92`. Remove the `dev-session.md` line from the Builders subtree.
- Edit `docs/guides/claude-code-feature-swot.md:92`. Same removal.

### 6. Add lock-in test
- **Task ID**: lock-in-test
- **Depends On**: delete-dead-code
- **Validates**: New test passes; running `pytest tests/unit/test_agent_definitions.py -k "test_dev_session_md_not_in_repo" -v` shows it as PASSED
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a new test to `tests/unit/test_agent_definitions.py` at the end of the existing `TestAgentFilesCICheck` class (or a new class if structure preferred):

  ```python
  def test_dev_session_md_not_in_repo(self):
      """Phase 5 follow-up cleanup (#1360): dev-session.md must not be in the repo.

      The file was deleted because (a) get_agent_definitions() does not
      reference it, and (b) the SDK loads .claude/agents/*.md from disk
      via setting_sources, which would let a stale Agent(subagent_type=
      "dev-session") dispatch resolve to it. Deletion makes stale
      dispatches fail-fast with an SDK 'unknown subagent' error.
      """
      assert not (_AGENTS_DIR / "dev-session.md").exists(), (
          ".claude/agents/dev-session.md must not exist "
          "(Phase 5 follow-up cleanup, #1360). "
          "If a skill template or merge re-added it, delete it again."
      )
  ```

### 7. Run verification
- **Task ID**: verify
- **Depends On**: update-feature-docs, plan-footer, update-skills-swot, lock-in-test
- **Validates**: All success criteria met
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); assert 'dev-session' not in d"` — must exit 0.
- Run `pytest tests/unit/test_agent_definitions.py -v` — must pass with all tests including the new lock-in test.
- Run `python -m ruff check .` — must pass.
- Run `python -m ruff format --check .` — must pass.
- Run `grep -rn "get_definition" /Users/tomcounsell/src/ai/ --include="*.py" 2>/dev/null | grep -v ".worktrees" | grep -v "/.venv/"` — must return zero matches.
- Run `grep -rn "dev-session.md" /Users/tomcounsell/src/ai/ --include="*.py" --include="*.md" 2>/dev/null | grep -v ".worktrees" | grep -v "/.venv/" | grep -v "/.git/"` — must return only the historical plan reference (`agentsession-harness-abstraction.md:89`) and possibly the new lock-in test's docstring/comment. No live code references.
- Verify `.claude/agents/dev-session.md` does not exist: `[ ! -e .claude/agents/dev-session.md ]` exits 0.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_agent_definitions.py -v` | exit code 0 |
| Lock-in test present | `grep -c "test_dev_session_md_not_in_repo" tests/unit/test_agent_definitions.py` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| `get_definition` removed | `grep -rn "def get_definition" agent/ --include="*.py"` | exit code 1 |
| `dev-session.md` removed | `test ! -e .claude/agents/dev-session.md` | exit code 0 |
| Module imports cleanly | `python -c "from agent.agent_definitions import get_agent_definitions; d = get_agent_definitions(); assert 'dev-session' not in d"` | exit code 0 |
| No `get_definition` in docs/features | `grep -rn "get_definition" docs/features/` | exit code 1 |
| Plan footer present | `grep -c "#1350" docs/plans/sdk_graceful_agent_fallback.md` | output > 0 |
| SWOT docs updated | `grep -c "dev-session.md" docs/research/claude-code-feature-swot.md docs/guides/claude-code-feature-swot.md` | output contains 0 |

## Critique Results

**Verdict:** READY TO BUILD (with concerns) — 0 blockers, 4 concerns, 3 nits. Concerns C1-C4 addressed below; nits noted but not all applied.

### C1 — `_EXPECTED_AGENT_FILES` comment must mention worker startup
- **Resolution applied:** Inline-comment replacement text updated to `# Agent files referenced by get_agent_definitions(). Used by validate_agent_files()` / `# to check that all expected files exist on disk at process startup (worker and bridge).`
- **Why:** PR #1353 (`5c2375b6`) wired `validate_agent_files()` into `worker/__main__.py:546-548` in addition to `bridge/telegram_bridge.py:1051-1053`. Saying "bridge startup" alone understates the worker's role.

### C2 — Worktree inode is independent; PR body must instruct post-merge `/update`
- **Resolution applied:** Risk 3 mitigation extended; PR body will explicitly tell reviewers/author to run `/update` (or `python scripts/update/run.py`) on each machine after merge so `_cleanup_stale_commands` reaps the user-scope hardlink at `~/.claude/agents/dev-session.md`. Without that step, `setting_sources` will keep resolving the stale file on the author's machine indefinitely.
- **Why:** Worktree checkouts materialize fresh inodes; the `link count = 2` claim only holds in main checkout. The verification step "file does not exist" only confirms worktree state.

### C3 — Pre-check grep must be worktree-portable
- **Resolution applied:** Task 1 grep replaced with `grep -rn "get_definition" "$(git rev-parse --show-toplevel)" --include="*.py" 2>/dev/null | grep -v "/\.venv/"`. Drops the absolute path and the `.worktrees` filter that would have excluded the cleanup-builder's own checkout.
- **Why:** Absolute path `/Users/tomcounsell/src/ai/` is non-portable across machines, and the `.worktrees` filter excludes the very worktree the builder is running in.

### C4 — Task 3 marked `Parallel: true` for consistency with tasks 4 and 5
- **Resolution applied:** All three doc-edit tasks (3, 4, 5) now mark `Parallel: true`. They edit disjoint files and depend only on `delete-dead-code`. No I/O contention.

### Nits (advisory)
- **N1** (line-509 is a Key Files table row, not prose): partially applied — Task 3 will treat each of lines 406, 502, 509 as separate edits with line-appropriate framing. Documented in this section; builder follows.
- **N2** (lock-in test docstring conflates Phase 5 with #1360): applied — docstring will say "Phase 5 follow-up cleanup (#1360)".
- **N3** (verification grep "possibly the new lock-in test's docstring" is permissive): not applied — the historical-plan reference and the new test's docstring/comment are both expected. Verification table holds; Task 7 wording is acceptable as advisory.

---

---

## Open Questions

None — the issue is well-scoped, the recon was thorough, and the freshness check turned up only minor additional drift (the SWOT docs and skill file) that's been folded into the plan. No supervisor input needed before critique.
