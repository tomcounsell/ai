---
status: docs_complete
type: feature
appetite: Small
owner: Valor
created: 2026-04-23
tracking: https://github.com/tomcounsell/ai/issues/1139
last_comment_id:
revision_applied: true
---

# PostCompact Re-Grounding Hook

## Problem

After context compaction, dev sessions resume without a targeted nudge to re-anchor on current scope and progress. The compacted summary is lossy — it rarely preserves working-state precision ("I was mid-edit of `process_batch()` at line 214; next step is the `num_turns` snapshot"). Without a re-grounding prompt, multi-hour builds routinely drift from plan, repeat already-completed steps, or abandon in-progress work.

**Current behavior:**
- Compaction fires (auto or manual). PreCompact hook writes JSONL backup (#1127/#1135).
- Agent session resumes against the compacted summary — zero guidance to re-read the plan doc, check SDLC stage progress, or look at any PROGRESS.md scratchpad.
- Agent has no idea that a compaction just happened from within the conversation context.

**Desired outcome:**
- Immediately after every compaction, the agent receives a short re-grounding message pointing at plan doc, SDLC stage states, and any scratchpad.
- Message is < 300 tokens. It is not a recap — it is a directed "go read these three things."
- Fires only after actual compaction events; never on normal turns.

## Freshness Check

**Baseline commit:** `91d54039b1950c6cf61527bf27fe2046e8ed674c`
**Issue filed at:** 2026-04-23T05:53:06Z (today — ~few hours before plan time)
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/settings.json` — no PostCompact hook registered. Confirmed: only UserPromptSubmit, PreToolUse, PostToolUse, Stop, SubagentStop are present.
- `agent/hooks/pre_compact.py` — lookup pattern `AgentSession.query.filter(claude_session_uuid=input["session_id"])` confirmed present at lines 153, 195. This is the exact pattern the PostCompact hook will reuse to fetch `plan_url` and `stage_states`.
- `agent/hooks/__init__.py` — `build_hooks_config()` confirmed: PreCompact, PreToolUse, PostToolUse, Stop. No PostCompact (SDK doesn't support it).
- `models/agent_session.py::plan_url` — confirmed field exists (`Field(null=True)` in the field block).
- `models/agent_session.py::stage_states` — confirmed as a property backed by `session_events`, returning JSON string.

**Cited sibling issues/PRs re-checked:**
- #1127 — closed 2026-04-22T21:41:24Z; merged as PR #1135. Ships PreCompact hook, cooldown, nudge guard. Infrastructure foundation for this work. Still holds — no regressions on main.
- #1130 — still OPEN. PR #1141 in review (adds PROGRESS.md guidance to builder.md). This plan does not depend on #1130 merging — the PostCompact hook works with or without a PROGRESS.md scratchpad present.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none — only `long-task-checkpointing.md` for #1130, which is prompt-layer guidance (complementary, not overlapping).

**Notes:** The issue refers to "PostCompact hook" as a Claude Code CLI hook event. Confirmed via claude binary v2.1.118 inspection: `PostCompact` IS a supported hook event in `settings.json`. Hook receives `{hook_event_name: "PostCompact", session_id, trigger, compact_summary, transcript_path, cwd}`. Exit code 0 = stdout displayed to the user as a message in the conversation (not `additionalContext` — that's the UserPromptSubmit/PostToolUse mechanism). This distinction matters for the nudge delivery path.

## Prior Art

No prior issues or PRs attempted a PostCompact re-grounding hook. Closest related work:

- **PR #1135 / Issue #1127** — Compaction hardening (JSONL backup, cooldown, nudge guard). Established the `AgentSession.claude_session_uuid` lookup pattern and `last_compaction_ts` field. The PostCompact hook reuses the same lookup pattern. Shipped; complementary.
- **Issue #1130** — Long-task checkpointing (PROGRESS.md + commit-frequency guidance). Originated the Q3 that spawned this issue. Adds prompt instructions in builder.md to re-read PROGRESS.md after compaction. This issue is the hook-based enforcement of the same re-grounding intent.

## Research

This is a purely internal change — no external libraries, APIs, or ecosystem patterns involved. The hook mechanism is defined by the Claude CLI binary. All signal is available in the codebase.

**Key finding from binary inspection:**
PostCompact hook stdout (exit code 0) is surfaced to the user directly as a message in the conversation — NOT via `additionalContext`. This is documented in the CLI binary as: "Exit code 0 - stdout shown to user." The pre_compact.py lookup pattern via `claude_session_uuid` is the correct path to resolve `AgentSession` context. No external research needed.

## Spike Results

### spike-1: PostCompact hook event support in the CLI
- **Assumption**: PostCompact is a real hook event type, not just a future placeholder
- **Method**: code-read (binary inspection via `strings`)
- **Finding**: Confirmed. Line 139867 in binary: `PostCompact:{summary:"After conversation compaction",description:"Input to command is JSON with compaction details and the summary. Exit code 0 - stdout shown to user"}`. Function `Y5H` (line 140995) fires post-compaction hooks with `{hook_event_name:"PostCompact", trigger, compact_summary}`. It is listed under **Hook events** documentation in the binary alongside PreToolUse, PostToolUse, PreCompact, Stop.
- **Confidence**: high
- **Impact on plan**: Hook goes in `.claude/hooks/post_compact.py` registered via `settings.json`. Cannot go in `agent/hooks/` + `build_hooks_config()` because the SDK's `HookEvent` type does not include PostCompact.

### spike-2: Output delivery mechanism for PostCompact
- **Assumption**: PostCompact stdout becomes additionalContext (like PostToolUse)
- **Method**: code-read (binary inspection)
- **Finding**: **FALSE.** PostCompact stdout (exit code 0) is shown directly to the user as a message — different from PostToolUse which uses `additionalContext` for thought injection. The `Y5H` function collects stdout into a `userDisplayMessage` field, not a context injection path.
- **Confidence**: high
- **Impact on plan**: The nudge appears as a user-visible message (like a system message in the conversation). Claude reads it as part of the next turn's context. Functionally equivalent for re-grounding — no workaround needed.

### spike-3: AgentSession lookup available in PostCompact context
- **Assumption**: `session_id` in PostCompact input maps to `AgentSession.claude_session_uuid`
- **Method**: code-read (pre_compact.py lookup pattern at lines 153, 195)
- **Finding**: Confirmed. `input["session_id"]` is the claude_session_uuid. `AgentSession.query.filter(claude_session_uuid=...)` returns the session row. `plan_url`, `slug`, and `stage_states` are all available on the row.
- **Confidence**: high
- **Impact on plan**: Hook can conditionally include the plan path and stage state in the nudge when present on the session.

## Data Flow

1. **Entry**: Claude Code CLI fires `PostCompact` hook after compaction completes. Delivers JSON to the hook process's stdin: `{hook_event_name: "PostCompact", session_id: "<claude-uuid>", trigger: "auto"|"manual", compact_summary: "<...>", transcript_path: "<path>", cwd: "<cwd>"}`.
2. **`.claude/hooks/post_compact.py`**: Reads stdin. Looks up `AgentSession` by `claude_session_uuid=session_id`. Reads `plan_url` and `stage_states` (if present on the row). Constructs a short re-grounding message (<300 tokens). Writes it to stdout and exits 0.
3. **CLI**: Surfaces the hook's stdout as a `userDisplayMessage` — shown as a message in the conversation at the start of the next turn.
4. **Claude reads** the re-grounding message as part of its next turn context. Re-reads plan doc if instructed.

**No path through the SDK/bridge.** Bridge sessions go through `build_hooks_config()` (agent/hooks/), which cannot register PostCompact (SDK limitation). Those sessions rely on the existing `defer_post_compact` nudge guard mechanism in the output router + #1130's prompt instructions in builder.md.

## Architectural Impact

- **New file**: `.claude/hooks/post_compact.py` — CLI-side hook, runs as a subprocess of the claude binary. Standalone Python script (same pattern as `user_prompt_submit.py`, `stop.py`). Never imported as a library.
- **New registration**: PostCompact entry added to `.claude/settings.json` hooks block.
- **No SDK/bridge changes**: PostCompact is not in the SDK HookEvent type; bridge sessions are unaffected. This is scoped purely to local CLI sessions (interactive Claude Code sessions).
- **New coupling**: `.claude/hooks/post_compact.py` imports from `models.agent_session` via `sys.path` manipulation (identical pattern to `user_prompt_submit.py`). Coupling is one-way and already established by the PreCompact hook.
- **Reversibility**: High — remove the `settings.json` entry to disable. The hook file can remain dormant.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — all dependencies (AgentSession model, `claude_session_uuid` lookup pattern, hook infrastructure) are already in main.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| models.agent_session importable | `python -c "from models.agent_session import AgentSession"` | Hook lookup |
| .claude/hooks/ accessible | `test -d .claude/hooks` | Hook registration |

## Solution

### Key Elements

- **`.claude/hooks/post_compact.py`**: Receives PostCompact input, builds a targeted re-grounding nudge, and writes it to stdout. Optional AgentSession lookup enriches the nudge with plan path and current stage state when available. Never raises.
- **`settings.json` PostCompact registration**: Connects the hook file to the PostCompact event. Matcher: empty (all sessions). Timeout: 10 seconds (generous — lookup may hit Redis).
- **Nudge content** (< 300 tokens): Hierarchy-aware — SDLC stages > plan doc > PROGRESS.md scratchpad > TodoWrite. Each item is conditionally included only if the data exists. Format is imperative ("Re-read X now"), not descriptive.
- **Tests**: Structural and behavioral unit tests covering the nudge builder function, AgentSession lookup success/failure/no-session paths, and stdout output format.

### Flow

**Compaction fires** → PostCompact hook subprocess spawned → stdin JSON parsed → AgentSession looked up by `claude_session_uuid` → nudge assembled conditionally → written to stdout → CLI surfaces message → Claude reads it on next turn → re-grounds on plan/stages/scratchpad.

### Technical Approach

The hook is a standalone Python script, following the same pattern as `.claude/hooks/user_prompt_submit.py`:

- `sys.path.insert(0, PROJECT_ROOT)` so it can import from `models/` and `hook_utils/`.
- `read_hook_input()` from `hook_utils/constants.py` reads stdin.
- AgentSession lookup: `AgentSession.query.filter(claude_session_uuid=session_id)` — same as `pre_compact.py:153`. Wrapped in `try/except Exception` — never raises.
- Build the nudge string from a helper function `_build_regrounding_nudge(plan_url, stage_states_json, cwd)`:
  - Always include: "Context was just compacted. Re-ground:"
  - If `plan_url` is set: "1. Re-read the plan: `{plan_url}`"
  - If `stage_states_json` is set: "2. Check SDLC stage progress: `python -m tools.sdlc_stage_query --issue-number {N}`" (issue number extracted from plan_url or issue_url)
  - If `PROGRESS.md` exists in `cwd`: "3. Re-read `PROGRESS.md` for working state"
  - Always: "4. Re-read your current TodoWrite task list"
- Print the nudge to stdout, exit 0.
- On any failure: print nothing (no stdout), exit 0. The CLI must not be interrupted by hook errors.

**Token budget**: The nudge text runs ~80-120 tokens for the full 4-item case. Well under the 300-token ceiling.

**PROGRESS.md detection**: Use `cwd = hook_input.get("cwd") or ""`, then `if cwd and os.path.exists(os.path.join(cwd, "PROGRESS.md")):` — the `or ""` guard handles both missing-key and empty-string cases (since `os.path.join("", "PROGRESS.md")` returns a relative path checked against the subprocess's cwd rather than the project root). If `cwd` is absent or empty, skip the PROGRESS.md item. No traversal up the tree.

**Issue number extraction**: When `plan_url` is set (e.g., `https://github.com/X/Y/blob/main/docs/plans/foo.md`), the issue number is not directly in the URL. Use `AgentSession.issue_url` instead (e.g., `https://github.com/.../issues/1139`) — extract the trailing integer with a regex.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Identify `except Exception` blocks in the new hook — each must log a warning (no silent pass). The outer bail-out should log at warning level with context. Inner Redis/save calls follow the pre_compact.py pattern (swallow, log).
- [ ] Test: when AgentSession lookup raises, `_build_regrounding_nudge` is still called with `plan_url=None, issue_url=None, stage_states_json=None, cwd=...` and a partial nudge is emitted.

### Empty/Invalid Input Handling
- [ ] `session_id` missing from hook input → no-op, exit 0, nothing written to stdout.
- [ ] `cwd` missing → nudge built without PROGRESS.md check.
- [ ] AgentSession found but `plan_url` is None → nudge omits plan item.
- [ ] AgentSession found but `stage_states` is None → nudge omits stage item.
- [ ] No AgentSession row found → nudge still emitted with only the always-present items (context compacted + TodoWrite reminder).

### Error State Rendering
- [ ] Hook script exits 0 in all paths — never exit 1 or 2 (which would show stderr to user or block the session).
- [ ] Test: subprocess invocation with malformed stdin produces empty stdout (no Python traceback escapes).

## Test Impact

No existing tests affected — this is a greenfield hook with no prior coverage. The new test file is `tests/unit/hooks/test_post_compact_hook.py`.

## Rabbit Holes

- **Adding PostCompact to build_hooks_config()**: The SDK HookEvent type doesn't include PostCompact. Attempting to register it would require SDK changes — out of scope and unnecessary since CLI sessions already cover the use case.
- **Injecting re-grounding into the compacted summary itself**: The PreCompact hook fires before compaction. Injecting into the summary would require modifying the compaction output (complex SDK internals). The PostCompact approach is cleaner.
- **Fetching and diff-ing plan content in the hook**: The hook should output a short directive, not a recap. Including actual plan text would exceed the token budget and defeat the purpose.
- **Cross-session compaction analytics**: Tracking how often agents re-read the plan post-compaction. Interesting but a separate observability feature — out of scope here.

## Risks

### Risk 1: AgentSession not found for local CLI sessions
**Impact:** Nudge emits without plan/stage items — still useful (TodoWrite reminder always fires), but less targeted.
**Mitigation:** The "no session" path is expected and handled gracefully. Local CLI sessions that were started without the UserPromptSubmit hook creating an AgentSession row (e.g., direct `claude` invocations) will receive a partial nudge. This is acceptable — the four-item nudge gracefully degrades.

### Risk 2: PostCompact hook blocks or delays the session
**Impact:** If the hook exceeds its timeout (10s), the CLI may cancel it. Redis lookup on a cold connection could be slow.
**Mitigation:** Set timeout=10 in settings.json (same as user_prompt_submit). The `|| true` in the command string ensures the CLI is never blocked — no additional async handling needed. Call Popoto/Redis synchronously, identical to `.claude/hooks/user_prompt_submit.py` and `.claude/hooks/stop.py` (both fully synchronous). Do NOT import or use `asyncio` — this hook has no event loop. Never raise from the hook.

### Risk 3: Nudge appears as a stray message in non-SDLC CLI sessions
**Impact:** Developers running one-off Claude Code sessions see a "re-ground on your plan" message that is irrelevant.
**Mitigation:** When `plan_url` and `stage_states` are absent (no AgentSession row), the nudge degrades to a minimal nudge (header + TodoWrite item): "Context was just compacted. Re-read your current TodoWrite task list." This is universally useful and not confusing.

## Race Conditions

No race conditions identified. The PostCompact hook fires after compaction completes — it is sequential relative to the compaction event. The AgentSession lookup is a read-only Redis query (no writes in this hook). The hook does not modify any shared state.

## No-Gos (Out of Scope)

- Adding PostCompact support to the SDK/bridge path (`build_hooks_config()` + `agent/hooks/`) — SDK limitation; bridge sessions rely on existing mechanisms.
- Persisting a flag when the nudge fires (no `post_compact_nudge_count` field on AgentSession) — observability is out of scope for Small appetite.
- Any form of compaction summary analysis or content extraction — nudge is directive only.
- Hooking into agent-SDK compaction events via the `onCompactProgress` callback — internal SDK detail, not a public hook surface.

## Update System

The `.claude/settings.json` change will be deployed to all machines via the `/update` skill. The `settings.json` file is tracked in git and synced on update. No new dependencies or config files.

The hook script `.claude/hooks/post_compact.py` is tracked in git and automatically available on all machines after sync. No migration steps needed.

## Agent Integration

No agent integration required — this is a Claude Code CLI hook. It fires via the CLI's hook mechanism (settings.json), not through MCP servers or the bridge. The bridge's SDK-based sessions are explicitly out of scope. No `.mcp.json` changes needed.

## Documentation

- [ ] Create `docs/features/post-compact-regrounding.md` describing the hook behavior, nudge content, degradation behavior, and relationship to compaction-hardening.
- [ ] Add entry to `docs/features/README.md` index table (alphabetically near "compaction-hardening").
- [ ] Add inline docstring to `.claude/hooks/post_compact.py` explaining the hook contract, input fields, and bail-out guarantee.

## Success Criteria

- [ ] `.claude/hooks/post_compact.py` exists and passes all unit tests.
- [ ] `.claude/settings.json` has a PostCompact entry pointing to `post_compact.py`.
- [ ] Hook emits the full 4-item nudge when AgentSession has `plan_url` + `stage_states` + `PROGRESS.md` present.
- [ ] Hook emits a minimal nudge (header + TodoWrite item) when no AgentSession row exists.
- [ ] Hook never raises — subprocess always exits 0.
- [ ] Nudge text is < 300 tokens in all code paths.
- [ ] Tests pass (`pytest tests/unit/hooks/test_post_compact_hook.py -v`).
- [ ] Linting clean (`python -m ruff check .`).
- [ ] Documentation created (`docs/features/post-compact-regrounding.md`).

## Team Orchestration

### Team Members

- **Builder (hook)**
  - Name: hook-builder
  - Role: Implement `.claude/hooks/post_compact.py` and register in `settings.json`
  - Agent Type: builder
  - Resume: true

- **Validator (hook)**
  - Name: hook-validator
  - Role: Verify hook behavior, test coverage, and settings.json registration
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: feature-documentarian
  - Role: Write `docs/features/post-compact-regrounding.md` and add README index entry
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list. Using: builder, validator, documentarian.

## Step by Step Tasks

### 1. Implement `.claude/hooks/post_compact.py`
- **Task ID**: build-hook
- **Depends On**: none
- **Validates**: `tests/unit/hooks/test_post_compact_hook.py` (create)
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/post_compact.py` as a standalone script:
  - Add module docstring explaining hook contract, input fields, bail-out guarantee, and why PostCompact is CLI-only (not in build_hooks_config)
  - Implement `read_hook_input()` call via `hook_utils/constants.py`
  - Implement `_lookup_session(claude_session_uuid)` → returns `(plan_url, issue_url, stage_states_json)` or `(None, None, None)`. Wrapped in `except Exception`.
  - Implement `_build_regrounding_nudge(plan_url, issue_url, stage_states_json, cwd)` → returns nudge string. Conditional item inclusion per Technical Approach. Always includes "Context was just compacted." header and TodoWrite item.
  - Implement `_extract_issue_number(issue_url)` → int or None (regex on trailing digits).
  - Implement `main()`: parse input, extract session_id and cwd, call lookup and nudge builder, print to stdout if non-empty, exit 0 in all paths.
  - Add `if __name__ == "__main__"` guard with try/except calling `log_hook_error`.
- Register PostCompact in `.claude/settings.json`:
  - Add `"PostCompact": [{"matcher": "", "hooks": [{"type": "command", "command": "python \"$CLAUDE_PROJECT_DIR\"/.claude/hooks/post_compact.py || true", "timeout": 10}]}]`
- Write `tests/unit/hooks/test_post_compact_hook.py`:
  - `test_full_nudge_with_all_context`: mock AgentSession with plan_url, issue_url, stage_states; tmp PROGRESS.md in cwd; assert all 4 items in nudge.
  - `test_partial_nudge_no_session`: no AgentSession row; assert nudge is the minimal nudge (header + TodoWrite item only).
  - `test_partial_nudge_no_plan`: AgentSession found but plan_url=None; assert plan item absent.
  - `test_partial_nudge_no_progress_md`: AgentSession found, plan_url set, no PROGRESS.md; assert PROGRESS.md item absent.
  - `test_nudge_under_token_budget`: full nudge; assert word count < 300 (proxy for token count).
  - `test_no_session_id_in_input`: input missing session_id; assert stdout is empty, exits cleanly.
  - `test_lookup_exception_handled`: AgentSession.query raises; assert nudge still emits (partial).
  - `test_extract_issue_number`: various issue_url formats; assert correct int extraction.

### 2. Validate hook implementation
- **Task ID**: validate-hook
- **Depends On**: build-hook
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/hooks/test_post_compact_hook.py -v` — all 8 tests must pass.
- Verify `.claude/hooks/post_compact.py` exists on disk.
- Verify `.claude/settings.json` contains `PostCompact` key.
- Check nudge text for the full-context case: count tokens (words × 1.3); must be < 300. Run the Verification table's "Nudge under budget" command: `python3 -c "import sys; sys.path.insert(0, '.claude/hooks'); from post_compact import _build_regrounding_nudge; n=_build_regrounding_nudge(None,None,None,'/tmp'); assert len(n.split()) < 250"` — must exit 0.
- Verify hook file is standalone (no `from agent.` imports — only `from models.` and `from hook_utils.`).
- Verify hook does NOT import `asyncio` — the hook is fully synchronous (same as `user_prompt_submit.py` and `stop.py`).
- Run `python -m ruff check .claude/hooks/post_compact.py` — must be clean.

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hook
- **Assigned To**: feature-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/post-compact-regrounding.md`:
  - ## Status (Shipped — issue #1139)
  - ## Problem (semantic drift after compaction)
  - ## Behavior (hook fires, nudge content, conditional items, degradation)
  - ## Why CLI-only (SDK HookEvent limitation; bridge sessions use existing mechanisms)
  - ## Relationship to Compaction Hardening (#1127/#1135) and Long-Task Checkpointing (#1130)
  - ## Hook Contract (input fields, exit code semantics, bail-out guarantee)
- Add entry to `docs/features/README.md` index table alphabetically (between "compaction-hardening" and the next C entry).

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/hooks/ -v` — all hook tests pass.
- Run `python -m ruff check .` — clean.
- Run `python -m ruff format --check .` — clean.
- Verify `docs/features/post-compact-regrounding.md` exists.
- Verify `docs/features/README.md` has the new entry.
- Confirm all Success Criteria checkboxes are met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hook tests pass | `pytest tests/unit/hooks/test_post_compact_hook.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hook file exists | `test -f .claude/hooks/post_compact.py` | exit code 0 |
| PostCompact registered | `python3 -c "import json; d=json.load(open('.claude/settings.json')); assert 'PostCompact' in d.get('hooks',{})"` | exit code 0 |
| Feature doc exists | `test -f docs/features/post-compact-regrounding.md` | exit code 0 |
| Nudge under budget | `python3 -c "import sys; sys.path.insert(0, '.claude/hooks'); from post_compact import _build_regrounding_nudge; n=_build_regrounding_nudge(None,None,None,'/tmp'); assert len(n.split()) < 250"` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Operator, Skeptic | C1: `asyncio.to_thread` guidance in Risk 2 is inapplicable — hook is a sync CLI subprocess with no event loop; builder could add unnecessary async machinery | Risk 2 rewritten; validate-hook task adds asyncio-import guard | Hook must NOT import or use `asyncio`; correct pattern is identical to `user_prompt_submit.py` and `stop.py` (fully sync) |
| CONCERN | Adversary, Skeptic | C2: Verification table "Nudge under budget" command uses `from .claude.hooks.post_compact import ...` — broken due to dot-prefixed directory not being a valid Python package | Fixed to `sys.path.insert(0, '.claude/hooks'); from post_compact import ...`; validate-hook task now explicitly runs the command | Use `python3 -c "import sys; sys.path.insert(0, '.claude/hooks'); from post_compact import _build_regrounding_nudge; ..."` |
| NIT | Adversary | N1: `cwd` empty-string guard not specified — `hook_input.get("cwd", "")` and `os.path.join("", "PROGRESS.md")` resolves to a relative path | Technical Approach updated with explicit `cwd = hook_input.get("cwd") or ""` guard | Use `if cwd and os.path.exists(os.path.join(cwd, "PROGRESS.md")):` |
| NIT | Consistency Auditor | N2: Failure Path description of lookup-raises path omits `issue_url=None` from the 3-tuple | Failure Path updated to include `issue_url=None` | `_lookup_session` returns `(plan_url, issue_url, stage_states_json)` — all three are None on exception |
| NIT | Consistency Auditor | N3: "1-item nudge (TodoWrite only)" vs "header + TodoWrite item" inconsistency across Success Criteria, test description, Risk 3 | All three locations standardized to "minimal nudge (header + TodoWrite item)" | Test asserts exactly two things: the "Context was just compacted." header line and the TodoWrite item |

---

## Open Questions

None — scope is fully locked by the issue, recon, and spike results. No open questions require human input before build.
