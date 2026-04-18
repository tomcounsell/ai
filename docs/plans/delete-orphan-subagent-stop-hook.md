---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1024
last_comment_id:
---

# Delete Orphan SubagentStop Hook

## Problem

`agent/hooks/subagent_stop.py` is an SDK-only hook that no longer fires in production. After the CLI harness migration (issue #780, documented in `docs/features/harness-abstraction.md`), all session types — PM, Teammate, and Dev — route unconditionally through `get_response_via_harness()`. The SDK path (`ValorAgent.query()` / `get_agent_response_sdk()`) is defined but has zero production callers; SDK hooks registered via `HookMatcher` therefore never fire.

Keeping the hook around violates CLAUDE.md §1 (NO LEGACY CODE TOLERANCE) and creates a maintenance tax: every audit (`audit-hooks`, `integration-audit`) re-reads the file, every contributor has to reason about whether it runs, and future refactors have to preserve a code path that no real session exercises.

**Current behavior:**

- `agent/hooks/subagent_stop.py` defines `subagent_stop_hook()` and `_extract_outcome_summary()`.
- `agent/hooks/__init__.py:17` imports it; `build_hooks_config()` at `agent/hooks/__init__.py:37` registers it under the `"SubagentStop"` HookMatcher key.
- `build_hooks_config()` is consumed only at `agent/sdk_client.py:1120` inside `ValorAgent._create_options()`.
- `ValorAgent` is instantiated and queried only by `get_agent_response_sdk()` at `agent/sdk_client.py:2525–2539`. That function has **no production callers** anywhere in `agent/`, `bridge/`, or `worker/` (verified by `grep -rn "get_agent_response_sdk\b" agent/ bridge/ worker/` returning only the definition at line 1953 and a docstring reference at 1885).
- SDLC stage tracking and GitHub stage comments — the hook's original job — were moved to the worker post-completion handler `_handle_dev_session_completion()` in `agent/agent_session_queue.py:3583` as part of the Phase 5 hook cleanup documented in `docs/features/harness-abstraction.md`.
- `tests/unit/test_subagent_stop_hook.py` exists and imports `subagent_stop_hook` and `_extract_outcome_summary` directly. Phase 5 already stripped it to only basic logging tests, but the whole file now tests dead code.

**Desired outcome:**

- `agent/hooks/subagent_stop.py` is deleted.
- `agent/hooks/__init__.py` no longer imports or registers `subagent_stop_hook`; the `"SubagentStop"` key is removed from the dict returned by `build_hooks_config()`.
- `tests/unit/test_subagent_stop_hook.py` is deleted.
- Remaining `subagent_stop` references in docstrings/comments/fixtures are updated or removed.
- `build_hooks_config()` itself is **kept** — four other hooks (PreToolUse, PostToolUse, Stop, PreCompact) still register through it. Evaluation of whether the whole SDK path (including `build_hooks_config`) should be removed is out of scope for this issue and tracked by `docs/plans/cli_harness_full_migration.md`.
- No behavioral change at runtime. Before this PR: the hook is dead code that never fires. After this PR: the hook is gone and still never fires. No session type observes a difference.

## Freshness Check

**Baseline commit:** `350df702d0648a4036913ba60b6cb551bc6ef7c0`
**Issue filed at:** 2026-04-17T08:42:41Z (approximately 30 hours before plan time)
**Disposition:** Minor drift

**File:line references re-verified:**

- `agent/hooks/__init__.py:17` — import of `subagent_stop_hook` — **still holds exactly**
- `agent/hooks/__init__.py:37` — `"SubagentStop": [HookMatcher(matcher="", hooks=[subagent_stop_hook])]` in `build_hooks_config()` — **still holds exactly**
- `agent/sdk_client.py:1110` (issue said this was the consumer of `build_hooks_config`) — **drifted**: line 1110 is now the `should_continue = prior_uuid is not None` assignment. The actual `hooks=build_hooks_config()` call is at line **1120**. The claim still holds — `build_hooks_config` is consumed at exactly one place inside `ValorAgent._create_options()` — but cite the new line.
- `agent/agent_session_queue.py:4148` (issue said this proves SDK path unreachable) — **drifted**: line 4148 is `chat_state.defer_reaction = True`. The harness-abstraction fact still holds — `docs/features/harness-abstraction.md` is shipped and the routing table in `_execute_agent_session()` unconditionally calls `get_response_via_harness()` — but the specific line number cited in the issue is incorrect. The load-bearing evidence is the absence of any caller for `get_agent_response_sdk()`, which the Phase 1 grep below confirms.
- `agent/hooks/subagent_stop.py:3–6` (docstring acknowledging SDK-only) — **still holds exactly**

**Cited sibling issues/PRs re-checked:**

- #1022 (parent investigation — PM orchestration audit open questions) — **still open**. Q1 in its body is the question this issue was spun off from; recon in comment 4266556940 matches current code.
- PR #912 (issue claims it moved Dev sessions from SDK to CLI harness) — **does not exist in this repo**. `gh pr view 912` returns `Could not resolve to a PullRequest`. The actual migration is documented in `docs/plans/cli_harness_full_migration.md` and `docs/features/harness-abstraction.md` (shipped). `docs/features/harness-abstraction.md` mentions PRs #868 and #902 as the real migration PRs. This is a citation error in the issue body; the underlying fact (migration happened, SDK path orphaned) is correct.

**Commits on main since issue was filed (touching referenced files):**

- `350df702 feat(health): two-tier no-progress detector (#1036) (#1039)` — irrelevant (health detector, unrelated to hooks)
- `29f8b450 Collapse session concurrency: single MAX_CONCURRENT_SESSIONS=8 cap (#1029)` — irrelevant (concurrency limits in queue, unrelated to hooks)
- `405eedd0 fix: skip iCloud .env read under launchd for Sentry token injection` — irrelevant (env loader)

No commits touched `agent/hooks/`, `agent/sdk_client.py` hook registration, or the tests in scope.

**Active plans in `docs/plans/` overlapping this area:**

- `cli_harness_full_migration.md` (status: Shipped, `revision_applied: true`) — the broader migration plan that proposes deleting the entire SDK execution path including `get_agent_response_sdk()`, the import in `agent_session_queue.py`, and the `agent/__init__.py` export. **Status is "Shipped" but Phase 6 validation is marked pending** and the function body still exists in code. This plan's goal is strictly narrower: delete only the SubagentStop hook and its registration, not the larger SDK path. The two plans do not conflict — this one is a safe subset. If `cli_harness_full_migration.md` phase 6 lands first and deletes `get_agent_response_sdk()` entirely, the whole `agent/hooks/__init__.py` and `build_hooks_config()` chain becomes removable in a follow-up.

**Notes:**

- Line-number drift in the issue body is cosmetic (3-line shift at `sdk_client.py`, stale reference at `agent_session_queue.py`). The plan uses the corrected pointers.
- The PR #912 reference in the issue body is a citation error. The harness migration happened and is documented; the premise stands.

## Prior Art

- **Issue #1022** — "PM orchestration audit open questions" (open). This issue is Q1 of #1022. Recon was performed under that investigation and is reproduced in the issue body.
- **`docs/plans/cli_harness_full_migration.md`** (Shipped, Phase 6 pending) — the broader migration. Explicitly addresses `get_agent_response_sdk()` deletion. Quoted in that plan (line 155 of `docs/features/harness-abstraction.md`): "`subagent_stop.py` stripped to logging only — `_register_dev_session_completion`, `_record_stage_on_parent`, `_post_stage_comment_on_completion` deleted; SDLC tracking moved to worker post-completion handler." So Phase 5 of that plan already took the hook down to a husk. This current plan is the final step — delete the husk itself now that it serves no purpose.
- **Issue #638** — "Document and test parent-child session hook lifecycle (round-trip gap)" (closed 2026-04-03). Closed after the hook lifecycle was re-examined. Not a blocker — its concerns are covered by the worker post-completion handler.
- **Issue #630** — "Create /audit-hooks skill" (closed 2026-04-03). Created the hook audit skill that would flag this orphan if run today. Validates the problem statement: hook auditing considers `subagent_stop.py` part of the inventory. Deleting it removes one row from that inventory.

No prior attempts to delete this specific hook. No failed fixes to analyze.

## Research

No relevant external findings — this is a pure internal code deletion task with no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

This section would normally trace data through components. For this plan it is vestigial:

1. **Entry point (hypothetical):** A subagent completion event in the SDK path.
2. **Would-be handler:** `subagent_stop_hook()` fires, logs completion, returns `{}`.
3. **Reality:** Step 1 never occurs. No session type routes to the SDK, so the hook is never triggered.

After deletion: the control-flow graph is identical, just with one fewer unreachable node.

## Architectural Impact

- **New dependencies:** None.
- **Interface changes:** `build_hooks_config()` returns a dict with 4 keys instead of 5 (the `"SubagentStop"` key disappears). Nothing reads that key from outside `ClaudeAgentOptions` (which is only used by the orphaned SDK path).
- **Coupling:** Decreases. Removes one import edge from `agent/hooks/__init__.py` to `agent/hooks/subagent_stop.py`.
- **Data ownership:** Unchanged.
- **Reversibility:** Trivial to revert — `git revert` the PR restores the file and the import. No data migration.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is fully defined by the issue's acceptance criteria)
- Review rounds: 1 (code review to confirm no missed references)

This is a deletion-only chore. The investigation is complete; the only remaining uncertainty is whether any caller was missed, and that is resolved by a deterministic grep sweep at build time.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Pre-deletion grep sweep:** Before deleting anything, run `grep -rn "subagent_stop_hook\|SubagentStop\|agent\.hooks\.subagent_stop" agent/ bridge/ worker/ tests/ scripts/ tools/ docs/ --include="*.py" --include="*.md"` and confirm the result set matches the inventory in the Technical Approach below. Any unexpected reference must be triaged before deletion proceeds.
- **File deletion:** `agent/hooks/subagent_stop.py`, `tests/unit/test_subagent_stop_hook.py`.
- **Registration removal:** Line 17 import and line 37 dict entry in `agent/hooks/__init__.py`. Update the `SubagentStop: Fires when a subagent finishes.` docstring line (line 30) to remove the stale reference.
- **Incidental reference cleanup:** Remove or update the two in-code references that mention `subagent_stop` as a known consumer:
  - `tools/sdlc_stage_marker.py:7` (docstring comment `"(pre_tool_use/subagent_stop) remain the primary marker path"`) — update to remove `subagent_stop`.
  - `agent/hooks/pre_tool_use.py:314` (docstring comment `"subagent_stop can later find and complete it"`) — update to reference `_handle_dev_session_completion` instead, which is the actual current consumer.
  - `tests/integration/test_stage_comment.py:77` — the string `"agent/hooks/subagent_stop.py"` appears as sample filename input to `format_stage_comment()`. Replace with a different sample path (e.g., `"agent/hooks/pre_tool_use.py"`) so the test doesn't assert against a file that no longer exists.
- **Doc cleanup (active/live docs only):** `docs/features/harness-abstraction.md:155` mentions the Phase 5 strip. That entry is historical and accurate; do not edit it. Plan docs under `docs/plans/` (including `docs/plans/completed/`) are historical artifacts — do not edit. The grep sweep is to confirm no active docs (like `docs/features/hooks-best-practices.md`, `docs/features/pipeline-state-machine.md`, etc.) still describe the hook as active; if any do, update them.
- **`build_hooks_config()` retained:** Keep the function and the four other hook registrations. Broader cleanup (removing `build_hooks_config` entirely when the SDK path is deleted) is out of scope — tracked by `cli_harness_full_migration.md`.

### Flow

There is no user-facing flow. This is an internal code deletion.

**Pre-build state** → Run grep sweep → Confirm inventory matches → Delete files → Remove import + dict entry → Update 3 incidental references → Run tests → PR → **Post-build state**

### Technical Approach

Deterministic deletion in this order:

1. **Pre-deletion grep sweep (blocker gate):** Run this exact command and verify output:
   ```
   grep -rn "subagent_stop_hook\|SubagentStop\|agent\.hooks\.subagent_stop" agent/ bridge/ worker/ tests/ scripts/ tools/ docs/ --include="*.py" --include="*.md"
   ```
   Expected inventory (everything else must be absent):
   - `agent/hooks/__init__.py:17` (import)
   - `agent/hooks/__init__.py:37` (registration, plus line 30 docstring)
   - `agent/hooks/subagent_stop.py` (whole file — will be deleted)
   - `tests/unit/test_subagent_stop_hook.py` (whole file — will be deleted)
   - `tools/sdlc_stage_marker.py:7` (docstring comment)
   - `agent/hooks/pre_tool_use.py:314` (docstring comment)
   - `tests/integration/test_stage_comment.py:77` (sample filename in fixture)
   - Occurrences under `docs/` — triage: update active docs, leave historical plan docs untouched.
   If any other reference surfaces, **stop and triage**. A surprise reference means either a new caller landed after plan time (check `git log` on that file) or the recon missed something — either way, do not delete until it's understood.

2. **Delete `agent/hooks/subagent_stop.py`** via `git rm`.

3. **Edit `agent/hooks/__init__.py`:** remove line 17 import, remove line 37 dict entry, update line 30 docstring to drop the `SubagentStop` bullet. The function body and remaining four registrations stay.

4. **Delete `tests/unit/test_subagent_stop_hook.py`** via `git rm`.

5. **Edit `tools/sdlc_stage_marker.py:7` and `agent/hooks/pre_tool_use.py:314`:** update the docstring comments to remove the stale `subagent_stop` reference. The `pre_tool_use.py` comment should redirect the reader to `_handle_dev_session_completion` as the current completion consumer.

6. **Edit `tests/integration/test_stage_comment.py:77`:** replace the sample filename with an existing path. The assertion is purely cosmetic (checks that files appear in the formatted comment) — any valid path works.

7. **Run the full test suite:** `pytest tests/ -x -q`. All tests must pass. Lint: `python -m ruff check .` and `python -m ruff format --check .` must pass.

8. **Doc audit:** grep `docs/features/` for any active doc that describes `subagent_stop` as if it runs. Update to past-tense/removed, or link to the harness migration for context. Historical plan docs (`docs/plans/*.md` and `docs/plans/completed/*.md`) are preserved as-is.

**Not doing:**
- Removing `build_hooks_config()` or the other four hook registrations. Tracked elsewhere.
- Removing `get_agent_response_sdk()` or the ValorAgent class. Tracked elsewhere.
- Removing `.claude/hooks/subagent_stop.py` — **that is a completely separate Claude Code harness hook (settings.json-driven standalone script), not the SDK hook. It is live and must stay.**

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `agent/hooks/subagent_stop.py` contains no `except Exception: pass` blocks to preserve — the file is being deleted entirely.
- [ ] `agent/hooks/__init__.py` contains no exception handlers; the edit is a pure import and dict-entry removal.
- [x] No exception handlers in the scope of this work.

### Empty/Invalid Input Handling

- [x] No new or modified functions receive user input. The only code change is an import removal and a dict-key removal. The three incidental edits (docstring comment and sample fixture string) do not process input.

### Error State Rendering

- [x] No user-visible output is touched. This change has no runtime effect on any session — the hook it removes never fires today.

## Test Impact

- [ ] `tests/unit/test_subagent_stop_hook.py` — **DELETE**: tests a hook that is being removed. Every test in the file imports `subagent_stop_hook` or `_extract_outcome_summary` from the deleted module; there is no partial keep.
- [ ] `tests/integration/test_stage_comment.py::TestStageCommentFormat::test_format_stage_comment_is_valid_markdown` — **UPDATE**: line 77 passes the string `"agent/hooks/subagent_stop.py"` as a sample file path into `format_stage_comment()`. Change to any existing file path (e.g., `"agent/hooks/pre_tool_use.py"`). The assertion body is path-agnostic (it only checks that the formatter emits the `### Files Modified` section), so the update is trivial.
- [ ] Any tests that currently mock `agent.hooks.subagent_stop.*` — audit via `grep -rn "agent\.hooks\.subagent_stop\|subagent_stop_hook" tests/`. Expected zero matches outside `tests/unit/test_subagent_stop_hook.py` itself. If matches exist, each one is either a DELETE (covers the removed hook exclusively) or UPDATE (the test has a broader scope and mocked the hook incidentally — remove the patch).

## Rabbit Holes

- **Do not** audit or migrate `build_hooks_config()` upstream. That is the scope of `cli_harness_full_migration.md` and will require analyzing four other hooks (PreToolUse, PostToolUse, Stop, PreCompact) plus the Haiku classifier and cross-repo env injection that live in `get_agent_response_sdk()`. This is a weeks-scale refactor, not a chore.
- **Do not** delete `get_agent_response_sdk()` or `ValorAgent`. Same scope note as above.
- **Do not** touch `.claude/hooks/subagent_stop.py`. Despite the identical filename, that is a Claude Code CLI harness hook (invoked by `settings.json`) and is live and required.
- **Do not** rewrite the Phase 5 historical notes in `docs/features/harness-abstraction.md`. The hook being stripped then fully deleted now is the correct history; preserving the timeline in docs is valuable.
- **Do not** re-investigate whether the SDK path runs for any session type. The grep sweep at step 1 of Technical Approach is the authoritative check. If it returns zero callers for `get_agent_response_sdk`, the hook is orphan, full stop.

## Risks

### Risk 1: A caller for `get_agent_response_sdk()` lands on main between plan time and build time
**Impact:** If a new caller appears, `ValorAgent._create_options()` runs again, `build_hooks_config()` returns a dict missing the `SubagentStop` key, and the SDK passes that dict to `ClaudeAgentOptions`. The SDK is permissive about missing hook keys (it simply doesn't register a matcher), so there is no crash — the hook just doesn't fire. Net behavior: identical to what happens today, since the hook already doesn't do anything meaningful beyond logging.
**Mitigation:** The pre-deletion grep sweep at build time is the gate. If a caller appears, the sweep will reveal it and the builder can triage (most likely outcome: confirm the caller doesn't depend on SubagentStop behavior, proceed; worst case: pause and scope-expand).

### Risk 2: A doc reference to `subagent_stop` is missed, and a reader later assumes the hook is still live
**Impact:** Confusion and wasted time during later audits or debugging. Not a runtime risk.
**Mitigation:** Grep `docs/features/` (active feature docs) as part of step 8. Accept that historical plan docs (`docs/plans/*.md` and `docs/plans/completed/*.md`) will retain references — that is intentional historical preservation.

### Risk 3: The test fixture update at `tests/integration/test_stage_comment.py:77` accidentally selects a path that will also be deleted in a sibling PR
**Impact:** Flaky test if the replacement path is also removed.
**Mitigation:** Use `agent/hooks/pre_tool_use.py` as the replacement — it is actively used by all session types (not SDK-dependent) and is not scheduled for deletion in any active plan.

## Race Conditions

No race conditions identified — this is a synchronous, single-threaded deletion of dead code. No concurrent access patterns, no shared state, no async boundaries touched.

## No-Gos (Out of Scope)

- Deletion of `build_hooks_config()`, `ValorAgent`, or `get_agent_response_sdk()`. Those are tracked by `docs/plans/cli_harness_full_migration.md`.
- Removal of the other four hooks (PreToolUse, PostToolUse, Stop, PreCompact).
- Any change to `.claude/hooks/subagent_stop.py` (separate Claude Code CLI harness hook, live).
- Rewriting historical plan docs. Historical notes in `docs/features/harness-abstraction.md` Phase 5 section remain untouched.
- Behavioral changes to session lifecycle, SDLC stage tracking, or GitHub stage commenting. Those all live in `_handle_dev_session_completion()` in `agent/agent_session_queue.py` and are untouched.
- Verification that `get_agent_response_sdk()` has no callers in unrelated repositories or downstream forks. Scope is this repo only.

## Update System

No update system changes required. This is a pure deletion of unused Python files — no new dependencies, no new config files, no secrets, no launchd schedules, no deployment-topology changes. The next `/update` run will simply pick up the deletion via `git pull` with no migration steps.

## Agent Integration

No agent integration required. This is a bridge-internal change — actually, an agent-internal change — that removes code the agent never reached. No new tools, no new MCP servers, no `.mcp.json` edits. The Telegram bridge, worker, and all session harnesses are unaffected.

## Documentation

### Feature Documentation

- [ ] Update `docs/features/harness-abstraction.md` if needed: the Phase 5 section at line 155 states `subagent_stop.py` was "stripped to logging only". After this PR, that note is still accurate as historical context but should be followed by a note like "Subsequently deleted entirely in issue #1024 once the broader SDK path was confirmed fully unreachable." Add this as a sentence in the Phase 5 section, not a new section. If on re-read the existing sentence is already historical enough, leave it.
- [ ] Grep `docs/features/` for any other active doc that describes `subagent_stop` as live. If found, update each to reflect deletion. Audit targets include `docs/features/hooks-best-practices.md`, `docs/features/pipeline-state-machine.md`, `docs/features/sdlc-stage-handoff.md`, `docs/features/sdlc-pipeline-integrity.md` — all surfaced by the Phase 2 grep.
- [ ] No entry needed in `docs/features/README.md` index — this deletion doesn't add or rename any feature.

### External Documentation Site

No external documentation site (Sphinx/RTD/MkDocs) is used in this repo. Skip.

### Inline Documentation

- [ ] Update docstring at `agent/hooks/__init__.py:30` (the `SubagentStop: Fires when a subagent finishes.` bullet) to remove the stale entry.
- [ ] Update docstring comment at `tools/sdlc_stage_marker.py:7` to remove the `subagent_stop` reference.
- [ ] Update docstring comment at `agent/hooks/pre_tool_use.py:314` to replace `subagent_stop` with `_handle_dev_session_completion`.

## Success Criteria

- [ ] `agent/hooks/subagent_stop.py` does not exist on the feature branch
- [ ] `grep -rn "subagent_stop_hook" agent/ bridge/ worker/ tests/` returns zero matches (empty output, exit code 1)
- [ ] `grep -rn "from agent.hooks.subagent_stop" .` returns zero matches in production code (worktree files under `.claude/worktrees/` and `.worktrees/` are ignored — those are isolated session workspaces)
- [ ] `agent/hooks/__init__.py` has 4 keys in the dict returned by `build_hooks_config()` (PreToolUse, PostToolUse, Stop, PreCompact) — verify with a one-liner: `python -c "from agent.hooks import build_hooks_config; cfg = build_hooks_config(); assert set(cfg.keys()) == {'PreToolUse', 'PostToolUse', 'Stop', 'PreCompact'}, cfg.keys()"`
- [ ] `tests/unit/test_subagent_stop_hook.py` does not exist
- [ ] `pytest tests/unit/` passes (no broken imports from deleted module)
- [ ] `pytest tests/integration/test_stage_comment.py` passes (fixture updated to use a live file path)
- [ ] `python -m ruff check .` and `python -m ruff format --check .` both exit 0
- [ ] Active docs under `docs/features/` do not describe `subagent_stop` as live; historical plan docs under `docs/plans/` preserved untouched
- [ ] PR opened with `Closes #1024` in the body
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (deletion)**
  - Name: `hook-deletion-builder`
  - Role: Execute the pre-deletion grep sweep, delete the hook file and its test, remove the import/registration, update the three incidental references, run tests and lint
  - Agent Type: `builder`
  - Resume: true

- **Validator (grep/import/test)**
  - Name: `hook-deletion-validator`
  - Role: Re-run grep sweep to confirm zero stale references, import `build_hooks_config()` to confirm 4-key result, run full unit and integration test suite
  - Agent Type: `validator`
  - Resume: true

### Available Agent Types

See template. Only `builder` and `validator` are needed here.

## Step by Step Tasks

### 1. Pre-deletion grep sweep and deletion
- **Task ID**: build-deletion
- **Depends On**: none
- **Validates**: `tests/unit/` (all must pass after deletion), `tests/integration/test_stage_comment.py`
- **Informed By**: Phase 2 grep in plan (inventory of 7 references + 2 file-scale deletions)
- **Assigned To**: `hook-deletion-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- Run the inventory grep: `grep -rn "subagent_stop_hook\|SubagentStop\|agent\.hooks\.subagent_stop" agent/ bridge/ worker/ tests/ scripts/ tools/ docs/ --include="*.py" --include="*.md"`. Confirm each match is in the expected inventory (Technical Approach step 1). If any surprise match surfaces, stop and raise as a blocker — do not proceed with deletion.
- `git rm agent/hooks/subagent_stop.py`
- `git rm tests/unit/test_subagent_stop_hook.py`
- Edit `agent/hooks/__init__.py`: remove line 17 import (`from agent.hooks.subagent_stop import subagent_stop_hook`), remove the `"SubagentStop"` dict entry in `build_hooks_config()`, update the docstring bullet at line 30 to drop the `SubagentStop` reference.
- Edit `tools/sdlc_stage_marker.py:7`: update the docstring comment to remove `subagent_stop` from the hook path list.
- Edit `agent/hooks/pre_tool_use.py:314`: update the docstring comment to reference `_handle_dev_session_completion` instead of `subagent_stop`.
- Edit `tests/integration/test_stage_comment.py:77`: replace `"agent/hooks/subagent_stop.py"` with `"agent/hooks/pre_tool_use.py"`.
- Grep active docs: `grep -rln "subagent_stop_hook\|agent/hooks/subagent_stop" docs/features/`. For each match, update the doc to reflect that the hook is deleted (past tense or removed mention entirely). Leave `docs/plans/` untouched.
- Run `pytest tests/unit/ tests/integration/test_stage_comment.py -x -q`. All must pass.
- Run `python -m ruff check .` and `python -m ruff format --check .`. Both must exit 0.
- Commit with message `chore(#1024): delete orphan SubagentStop hook`.

### 2. Validation
- **Task ID**: validate-deletion
- **Depends On**: build-deletion
- **Assigned To**: `hook-deletion-validator`
- **Agent Type**: `validator`
- **Parallel**: false
- Re-run the grep sweep command and confirm the inventory shrunk to match the success criteria (zero `subagent_stop_hook` matches in `agent/ bridge/ worker/ tests/`).
- Run `python -c "from agent.hooks import build_hooks_config; cfg = build_hooks_config(); assert set(cfg.keys()) == {'PreToolUse', 'PostToolUse', 'Stop', 'PreCompact'}, cfg.keys()"`. Must exit 0.
- Run `pytest tests/ -x -q`. Must exit 0.
- Run `python -m ruff check .` and `python -m ruff format --check .`. Both must exit 0.
- Report pass/fail status. If fail, hand back to builder with the specific failing check.

### 3. Documentation
- **Task ID**: document-deletion
- **Depends On**: validate-deletion
- **Assigned To**: `hook-deletion-builder` (no documentarian specialist needed — the doc work is small and inline)
- **Agent Type**: `builder`
- **Parallel**: false
- Inside `docs/features/harness-abstraction.md`, in the Phase 5 section (around line 150), append a sentence noting that the `subagent_stop.py` file was subsequently deleted in issue #1024 once the broader SDK path was confirmed fully unreachable. Do not rewrite earlier phases.
- Re-grep `docs/features/` to confirm no active doc still describes `subagent_stop` as a live registered hook. Historical references are fine as long as they are phrased as historical.
- Commit doc update with message `docs(#1024): note deletion of orphan SubagentStop hook`.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: document-deletion
- **Assigned To**: `hook-deletion-validator`
- **Agent Type**: `validator`
- **Parallel**: false
- Run all commands in the Verification table below. All must pass.
- Verify every Success Criteria checkbox can be ticked.
- Generate final report. Ready for PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hook file deleted | `test ! -f agent/hooks/subagent_stop.py && echo ok` | output contains ok |
| Test file deleted | `test ! -f tests/unit/test_subagent_stop_hook.py && echo ok` | output contains ok |
| No stale hook refs in prod code | `grep -rn "subagent_stop_hook" agent/ bridge/ worker/ tests/` | exit code 1 |
| 4-key hooks config | `python -c "from agent.hooks import build_hooks_config; assert set(build_hooks_config().keys()) == {'PreToolUse', 'PostToolUse', 'Stop', 'PreCompact'}"` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. Should the Phase 5 note in `docs/features/harness-abstraction.md` be amended with a forward reference to this issue (#1024), or left as the authoritative "stripped to logging only" snapshot? Recommendation: add the amendment — it gives future readers a pointer to the final deletion and preserves the timeline.
2. Are there any downstream forks or private branches that depend on `subagent_stop_hook` that we should coordinate with before deletion? Scope assumption: no. The issue body indicates this is internal orphan code with no external consumers. Confirming with the owner before merge is cheap insurance.
