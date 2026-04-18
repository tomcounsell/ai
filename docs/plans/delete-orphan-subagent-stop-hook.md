---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-04-18
tracking: https://github.com/tomcounsell/ai/issues/1024
last_comment_id:
revision_applied: true
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
   grep -rn "subagent_stop_hook\|SubagentStop\|agent\.hooks\.subagent_stop" agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/ docs/ --include="*.py" --include="*.md"
   ```
   Expected inventory (everything else must be absent):
   - `agent/hooks/__init__.py:17` (import)
   - `agent/hooks/__init__.py:37` (registration, plus line 30 docstring)
   - `agent/hooks/subagent_stop.py` (whole file — will be deleted)
   - `tests/unit/test_subagent_stop_hook.py` (whole file — will be deleted)
   - `tools/sdlc_stage_marker.py:7` (docstring comment)
   - `agent/hooks/pre_tool_use.py:314` (docstring comment)
   - `tests/integration/test_stage_comment.py:77` (sample filename in fixture)
   - `reflections/auditing.py:377` — **LEAVE UNCHANGED**. This check iterates `.claude/settings.json` hooks (the live CLI-harness `.claude/hooks/subagent_stop.py`), NOT the SDK hook being deleted. Touching it would break the `|| true` audit rule for the live harness hook. Pre-declared as a KEEP so it does not trip the surprise-match gate.
   - Occurrences under `docs/` — triage: update active docs (enumerated in the Documentation section), leave historical plan docs untouched.
   If any other reference surfaces, **stop and triage**. A surprise reference means either a new caller landed after plan time (check `git log` on that file) or the recon missed something — either way, do not delete until it's understood.

   **Caller-count gate (Risk 1 mitigation):** In addition to the inventory grep, run:
   ```
   grep -rn "get_agent_response_sdk\|build_hooks_config\|ValorAgent(" agent/ bridge/ worker/
   ```
   Expected matches (anything else = STOP and escalate, do not proceed with deletion):
   - `agent/sdk_client.py:1120` — `hooks=build_hooks_config()` inside `ValorAgent._create_options()`
   - `agent/sdk_client.py:1885` — docstring reference to `get_agent_response_sdk`
   - `agent/sdk_client.py:1953` — `def get_agent_response_sdk(...)` definition
   - `agent/sdk_client.py` — `class ValorAgent` and its internal `ValorAgent()` instantiation inside `get_agent_response_sdk()`
   - `agent/__init__.py` — re-export of `get_agent_response_sdk`
   Any match outside this inventory means a new SDK-path caller has landed; escalate rather than silently dropping their logging.

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
- [ ] `tests/integration/test_stage_comment.py::TestStageCommentFormat::test_format_stage_comment_is_valid_markdown` — **UPDATE (HYGIENE, not correctness)**: line 77 passes the string `"agent/hooks/subagent_stop.py"` as a sample file path into `format_stage_comment()`. The assertion only checks `"### Files Modified" in body` — `format_stage_comment()` formats markdown only and never touches the filesystem, so the test passes today regardless of whether the file exists. Update is a dangling-reference cleanup for future readers, not a correctness fix. Replace with `"agent/hooks/pre_tool_use.py"` if the edit is cheap; skip if a conflict arises.
- [x] **Mock audit complete**: `grep -rn "agent\.hooks\.subagent_stop\|subagent_stop_hook" tests/` at critique time returned zero matches outside `tests/unit/test_subagent_stop_hook.py` itself. No other test mocks or imports the hook — no incidental UPDATE work required.

## Rabbit Holes

- **Do not** audit or migrate `build_hooks_config()` upstream. That is the scope of `cli_harness_full_migration.md` and will require analyzing four other hooks (PreToolUse, PostToolUse, Stop, PreCompact) plus the Haiku classifier and cross-repo env injection that live in `get_agent_response_sdk()`. This is a weeks-scale refactor, not a chore.
- **Do not** delete `get_agent_response_sdk()` or `ValorAgent`. Same scope note as above.
- **Do not** touch `.claude/hooks/subagent_stop.py`. Despite the identical filename, that is a Claude Code CLI harness hook (invoked by `settings.json`) and is live and required.
- **Do not** rewrite the Phase 5 historical notes in `docs/features/harness-abstraction.md`. The hook being stripped then fully deleted now is the correct history; preserving the timeline in docs is valuable.
- **Do not** re-investigate whether the SDK path runs for any session type. The grep sweep at step 1 of Technical Approach is the authoritative check. If it returns zero callers for `get_agent_response_sdk`, the hook is orphan, full stop.

## Risks

### Risk 1: A caller for `get_agent_response_sdk()` lands on main between plan time and build time
**Impact:** A new SDK-path caller would silently lose the SubagentStop logging that was previously emitted. No crash — the SDK is permissive about missing hook keys — but a regression in observability that a downstream reader may rely on. The caller wouldn't know the logging is gone until they look for it and can't find it.
**Mitigation:** Explicit caller-count gate at build time. Run `grep -rn "get_agent_response_sdk\|build_hooks_config\|ValorAgent(" agent/ bridge/ worker/` and compare against the known inventory baked into Technical Approach step 1 (the 5 expected matches under `agent/sdk_client.py` and `agent/__init__.py`). **If the result set differs in any way — new file, new line, new matching symbol — stop and escalate; do not proceed with deletion.** The builder pauses, surfaces the new caller to the human, and the PM decides whether to (a) abort and close the issue as superseded, or (b) expand scope to preserve the logging behavior under the new caller. No silent proceed-anyway path.

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

All 7 active docs below were found by `grep -rln "subagent_stop" docs/features/` at plan time (13 total references). Each has a definite disposition — no contingent "if found" language.

- [ ] `docs/features/harness-abstraction.md` — **AMEND**: the Phase 5 section (~line 155) states `subagent_stop.py` was "stripped to logging only". Append: "Subsequently deleted entirely in issue #1024 once the broader SDK path was confirmed fully unreachable." Preserve the timeline; do not rewrite earlier phases.
- [ ] `docs/features/sdlc-pipeline-integrity.md` — **REWRITE §D**: currently contains §D "SubagentStop Stage State Injection" (~lines 67-80) describing the hook as an active SDLC feedback mechanism. Convert to a "Removed" historical note: "§D — Removed. SubagentStop-based stage injection was stripped in the Phase 5 harness migration and the hook file was deleted in #1024. SDLC stage transitions now live in `_handle_dev_session_completion()` in `agent/agent_session_queue.py`." Total refs in this file: 4.
- [ ] `docs/features/sdk-modernization.md` — **UPDATE**: 3 references. Rewrite each to past-tense / removed; if any reference lists SubagentStop as a current SDK hook, drop it from the list.
- [ ] `docs/features/sdlc-stage-handoff.md` — **UPDATE**: 4 references. Replace any live-tense description of the SDK SubagentStop hook with the current mechanism (`_handle_dev_session_completion()`). Historical mentions may stay if they are already phrased as historical.
- [ ] `docs/features/pipeline-state-machine.md` — **UPDATE**: 2 references. Same treatment as above — drop SubagentStop from any active-mechanism description.
- [ ] `docs/features/pm-dev-session-architecture.md` — **UPDATE**: 1 reference (a hook registry row). Remove the row if the registry is meant to enumerate live SDK hooks only; otherwise annotate as "Removed (#1024)".
- [ ] `docs/features/hooks-best-practices.md` — **PARTIAL UPDATE**: 2 references. **Keep the `|| true` rule** — it governs the live `.claude/hooks/subagent_stop.py` (separate CLI-harness hook, not being deleted). **Drop the SDK-registry mention** that lists SubagentStop among the hooks registered via `HookMatcher`.
- [ ] `docs/features/session-watchdog-reliability.md` — **UPDATE**: 1 cross-reference. Update any pointer to the SDK SubagentStop hook; either redirect to the settings.json hook (if that was the intent) or remove the xref entirely.
- [ ] No entry needed in `docs/features/README.md` index — this deletion doesn't add or rename any feature.

**Audit gate:** After all 8 edits above, re-run `grep -rln "subagent_stop" docs/features/` and confirm the result set shrinks to: (a) historical-tense references only, and (b) `hooks-best-practices.md` where it describes the settings.json hook (`.claude/hooks/subagent_stop.py`), not the deleted SDK hook. No active-tense live-mechanism claims about the SDK hook should remain.

### External Documentation Site

No external documentation site (Sphinx/RTD/MkDocs) is used in this repo. Skip.

### Inline Documentation

- [ ] Update docstring at `agent/hooks/__init__.py:30` (the `SubagentStop: Fires when a subagent finishes.` bullet) to remove the stale entry.
- [ ] Update docstring comment at `tools/sdlc_stage_marker.py:7` to remove the `subagent_stop` reference.
- [ ] Update docstring comment at `agent/hooks/pre_tool_use.py:314` to replace `subagent_stop` with `_handle_dev_session_completion`.

## Success Criteria

- [ ] `agent/hooks/subagent_stop.py` does not exist on the feature branch
- [ ] `grep -rn "subagent_stop_hook" agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/` returns zero matches (empty output, exit code 1). Scope matches the Technical Approach step 1 sweep exactly — no asymmetry between sweep and verify.
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

### 1. Grep sweep, caller-count gate, deletion, and doc updates (single commit)
- **Task ID**: build-deletion
- **Depends On**: none
- **Validates**: `tests/unit/` (all must pass after deletion), `tests/integration/test_stage_comment.py`, active `docs/features/` grep
- **Informed By**: Technical Approach step 1 (inventory of 7 references + 2 file-scale deletions + `reflections/auditing.py:377` KEEP); Documentation section (8 enumerated doc edits)
- **Assigned To**: `hook-deletion-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- Run the inventory grep: `grep -rn "subagent_stop_hook\|SubagentStop\|agent\.hooks\.subagent_stop" agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/ docs/ --include="*.py" --include="*.md"`. Confirm each match is in the expected inventory (Technical Approach step 1). The `reflections/auditing.py:377` match is pre-declared and must be LEFT UNCHANGED — do not treat it as a surprise. If any OTHER surprise match surfaces, stop and raise as a blocker — do not proceed with deletion.
- Run the caller-count gate: `grep -rn "get_agent_response_sdk\|build_hooks_config\|ValorAgent(" agent/ bridge/ worker/`. Confirm matches are limited to `agent/sdk_client.py` (lines 1120, 1885, 1953, plus the ValorAgent class/instantiation) and `agent/__init__.py` (re-export). Any other match = STOP, escalate to human.
- `git rm agent/hooks/subagent_stop.py`
- `git rm tests/unit/test_subagent_stop_hook.py`
- Edit `agent/hooks/__init__.py`: remove line 17 import (`from agent.hooks.subagent_stop import subagent_stop_hook`), remove the `"SubagentStop"` dict entry in `build_hooks_config()`, update the docstring bullet at line 30 to drop the `SubagentStop` reference.
- Edit `tools/sdlc_stage_marker.py:7`: update the docstring comment to remove `subagent_stop` from the hook path list.
- Edit `agent/hooks/pre_tool_use.py:314`: update the docstring comment to reference `_handle_dev_session_completion` instead of `subagent_stop`.
- **Hygiene edit (optional but preferred)**: Edit `tests/integration/test_stage_comment.py:77` to replace `"agent/hooks/subagent_stop.py"` with `"agent/hooks/pre_tool_use.py"`. The current string is harmless — `format_stage_comment()` never touches the filesystem — so this is hygiene, not a correctness gate. Skip if any conflict surfaces; the test will still pass.
- Apply all 8 doc edits enumerated in the Documentation section (`harness-abstraction.md` AMEND, `sdlc-pipeline-integrity.md` §D REWRITE, `sdk-modernization.md` UPDATE, `sdlc-stage-handoff.md` UPDATE, `pipeline-state-machine.md` UPDATE, `pm-dev-session-architecture.md` UPDATE, `hooks-best-practices.md` PARTIAL UPDATE, `session-watchdog-reliability.md` UPDATE).
- Re-run `grep -rln "subagent_stop" docs/features/` and confirm the audit gate: only historical-tense references + the `hooks-best-practices.md` settings.json hook mention remain.
- Run `pytest tests/unit/ tests/integration/test_stage_comment.py -x -q`. All must pass.
- Run `python -m ruff check .` and `python -m ruff format --check .`. Both must exit 0.
- Commit with message `chore(#1024): delete orphan SubagentStop hook`. Single commit — the doc audit is part of the deletion, not a follow-up.

### 2. Validation
- **Task ID**: validate-deletion
- **Depends On**: build-deletion
- **Assigned To**: `hook-deletion-validator`
- **Agent Type**: `validator`
- **Parallel**: false
- Re-run the grep sweep and confirm the inventory shrunk per Success Criteria (zero `subagent_stop_hook` matches across `agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/`).
- Re-run the caller-count gate and confirm no new SDK-path callers landed during the build.
- Run `python -c "from agent.hooks import build_hooks_config; cfg = build_hooks_config(); assert set(cfg.keys()) == {'PreToolUse', 'PostToolUse', 'Stop', 'PreCompact'}, cfg.keys()"`. Must exit 0.
- Run `pytest tests/ -x -q`. Must exit 0.
- Run `python -m ruff check .` and `python -m ruff format --check .`. Both must exit 0.
- Verify every Success Criteria checkbox can be ticked.
- Report pass/fail status. If fail, hand back to builder with the specific failing check. Ready for PR.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hook file deleted | `test ! -f agent/hooks/subagent_stop.py && echo ok` | output contains ok |
| Test file deleted | `test ! -f tests/unit/test_subagent_stop_hook.py && echo ok` | output contains ok |
| No stale hook refs in prod code | `grep -rn "subagent_stop_hook" agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/` | exit code 1 |
| Caller-count gate | `grep -rn "get_agent_response_sdk\|build_hooks_config\|ValorAgent(" agent/ bridge/ worker/` | only matches in `agent/sdk_client.py` and `agent/__init__.py` — no new SDK callers |
| 4-key hooks config | `python -c "from agent.hooks import build_hooks_config; assert set(build_hooks_config().keys()) == {'PreToolUse', 'PostToolUse', 'Stop', 'PreCompact'}"` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic, Archaeologist | Plan inventory misses 7 active `docs/features/` files (13 references) with live prose treating the hook as wired. | `/do-plan` revision pass. Expand the Documentation section to enumerate each target file + disposition. | Files: `sdlc-pipeline-integrity.md` (4 refs incl. full §D "SubagentStop Stage State Injection"), `sdk-modernization.md` (3), `sdlc-stage-handoff.md` (4), `pipeline-state-machine.md` (2), `pm-dev-session-architecture.md` (1 registry row), `hooks-best-practices.md` (2 — SDK-hook list vs settings.json hook), `session-watchdog-reliability.md` (1 xref). For `hooks-best-practices.md`: keep the `\|\| true` rule (it governs the live `.claude/hooks/subagent_stop.py`); only drop the SDK-registry mention. For `sdlc-pipeline-integrity.md` §D: convert to a "Removed" historical note with pointer to `harness-abstraction.md` Phase 5 + issue #1024. |
| CONCERN | Adversary | Unlisted active-code reference: `reflections/auditing.py:377` checks `event_type in ("Stop", "SubagentStop")`. | Add to the inventory in Technical Approach step 1 and explicitly mark "LEAVE UNCHANGED — audits settings.json hooks, not SDK hooks." | The `auditing.py:377` check iterates `.claude/settings.json` hooks, which still registers a live `.claude/hooks/subagent_stop.py`. Deleting this check would break the `|| true` audit rule for the live CLI-harness hook. The pre-deletion grep sweep will surface this file — builder must not treat the match as a surprise-blocker and must not edit it. |
| CONCERN | Simplifier | Task 3 (Documentation) depends on Task 2 (validate-deletion), but Task 2 runs `pytest tests/ -x -q` *before* feature-doc updates happen — yet none of the validation is affected by doc prose. Meanwhile Task 1's inline `grep docs/features/` step duplicates Task 3's grep. | Merge Task 3's doc-audit into Task 1, or move the doc-audit ahead of Task 2. Either collapses a phase and removes the duplicated grep. | Current flow: Task 1 does partial doc audit → Task 2 validates (no doc gate) → Task 3 does the "real" doc audit → Task 4 re-validates. Simpler: Task 1 (grep + delete + edit + doc audit in one commit) → Task 2 (validate everything, incl. `grep -rln subagent_stop docs/features/` returns zero live-tense matches) → done. Saves the `docs(#1024):` commit and one agent hop. |
| CONCERN | Operator | Success Criterion "grep `subagent_stop_hook` returns zero matches in `agent/ bridge/ worker/ tests/`" omits `reflections/` and `scripts/` and `tools/`, but the inventory grep in Technical Approach *does* check `tools/` and `scripts/`. Criteria and build-time sweep disagree. | Make the two grep commands identical. Either widen the success-criterion grep to the full sweep, or tighten the sweep to match the criterion. | Recommended: use the full sweep in both places — `grep -rn "subagent_stop_hook" agent/ bridge/ worker/ tests/ scripts/ tools/ reflections/` — and expect zero. The `reflections/auditing.py` match is on the string `"SubagentStop"` not `subagent_stop_hook`, so it will not surface under this criterion (verified). |
| CONCERN | Skeptic | Risk 1 ("new caller lands on main between plan and build time") understates impact. Plan says "no crash — the hook just doesn't fire," but the net effect is that a new SDK-path caller would silently lose logging that was previously there. | Add a mitigation: if the pre-deletion grep surfaces a new caller for `get_agent_response_sdk` or anything that reaches `build_hooks_config()` beyond the existing single call site, pause and escalate; do not proceed with deletion. | Build-time check: `grep -rn "get_agent_response_sdk\|build_hooks_config\|ValorAgent(" agent/ bridge/ worker/` must return exactly the known inventory (`agent/sdk_client.py:1120`, `agent/sdk_client.py:1953`, `agent/sdk_client.py:1885`, plus `agent/__init__.py` export). Anything else = stop. |
| CONCERN | User | `tests/integration/test_stage_comment.py:77` is a fixture *string* inside `format_stage_comment(files=[...])` — the assertion only checks `"### Files Modified" in body`. The plan treats changing this as mandatory, but the current string is harmless (the formatter never touches the filesystem for that list). | Clarify the rationale: update is a hygiene change (removes a dangling reference for future readers), not a correctness fix. Flagging it as "UPDATE" in Test Impact is correct; flagging it as essential to deletion is not. | The test passes today with or without the file existing — `format_stage_comment()` formats markdown only. Mark the test-update as NIT-level unless the builder finds a second test that actually imports from the deleted path. Current Test Impact grep confirmed: zero `agent.hooks.subagent_stop` imports outside `tests/unit/test_subagent_stop_hook.py`. |
| NIT | Simplifier | Open Question #2 ("downstream forks or private branches") is explicitly scoped out in No-Gos line 233. The Open Question is noise. | Delete Open Question #2. | — |
| NIT | Skeptic | Freshness Check notes PR #912 is a citation error in the issue body but leaves the error in place. | Either fix the issue body during build or add a line to the PR body noting the correction. | — |
| NIT | User | Documentation section item 2 reads "If found, update each to reflect deletion" with target doc examples — but those docs *are* found (grep confirms 7 files). Phrasing implies uncertainty when the work is known. | Rewrite as a definite TODO list with specific dispositions per file (see Skeptic CONCERN above). | — |

---

## Open Questions

1. Should the Phase 5 note in `docs/features/harness-abstraction.md` be amended with a forward reference to this issue (#1024), or left as the authoritative "stripped to logging only" snapshot? **Resolved during revision**: the Documentation section now mandates the AMEND. No longer an open question.

**Note for builder on PR body:** include a correction line noting that issue #1024's body cites PR #912 as the Dev-session migration PR, but that PR doesn't exist — the actual migration landed in PRs #868 and #902 (documented in `docs/features/harness-abstraction.md`). The underlying fact (migration happened, hook orphaned) is correct; this is a citation-error footnote for future readers.
