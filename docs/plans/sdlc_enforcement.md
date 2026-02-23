---
status: Planning
type: chore
appetite: Large
owner: Valor
created: 2025-02-22
tracking: https://github.com/tomcounsell/ai/issues/151
---

# SDLC Enforcement

## Problem

Software development conversations — whether they come through Telegram, local Claude Code sessions, or the `/do-build` pipeline — share the same quality bar but get inconsistent enforcement. The formal `/do-build` pipeline has strong gates: worktree isolation, test loops, documentation checks. Everything else relies entirely on the agent "remembering" the right steps.

In practice this means:
- Ad-hoc fixes ("The agent didn't complete. Let me implement directly.") skip tests
- Docs get added as an afterthought after code is already committed
- Commit messages occasionally include co-author trailers despite explicit prohibition
- Sessions that write code and sessions that answer questions about YouTube links look identical to the system — no distinction, no tailored behavior

**What a software development conversation looks like:**
Valor modifies one or more `.py`, `.js`, or `.ts` source files in response to a coding request. Could be a hotfix, a feature, an ad-hoc rescue of a failed agent, or a full `/do-build` pipeline run. The defining signal is: *code files were written or edited this session.*

**What is NOT a software development conversation:**
- Answering questions about the codebase (Grep/Read only, no writes)
- Reviewing a YouTube link, article, or external content
- Planning discussions that produce only `.md` files
- Checking logs, reading error output, debugging without writing code
- Casual chat, status checks, "what issues remain?"
- Running tests without modifying code first

**Desired outcome:**
- Quality gates fire automatically on every *software development* session, not just `/do-build` runs
- Non-development sessions (conversation, research, review) pass through completely unaffected — zero latency, zero interference
- The agent is blocked from stopping a dev session if it wrote code but skipped tests/linting
- The pipeline has explicit stages with persisted state so any session — whether started fresh or resuming after a crash — picks up exactly where it left off
- A dedicated `/do-patch` skill handles targeted fixes at the test-fail and review-blocker lifecycle steps, replacing ad-hoc inline loops

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on which gates to enforce vs. warn)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

Three interlocking parts:

1. **Formal pipeline model** — a canonical stage sequence that all SDLC flows follow, with state persisted so sessions can resume mid-pipeline
2. **Hook enforcement** — lightweight Claude Code hooks that block shortcuts (skipping tests, co-author commits)
3. **`patch` skill** — a new dedicated skill for targeted fixes, separate from `do-build` which runs full implementation

### Session Classification

Before any SDLC logic runs, the system classifies the session:

| Signal | Classification | SDLC applies? |
|--------|---------------|---------------|
| Write/Edit tool used on `.py`, `.js`, `.ts` file | **Code session** | Yes |
| Only Glob/Grep/Read/Bash (no writes to code files) | **Research session** | No |
| Write/Edit used only on `.md`, `.json`, `.yaml`, `.toml` | **Docs/config session** | No |
| Bash only (running tests, checking git, reading logs) | **Ops session** | No |

Classification is determined by `post_tool_use.py` as each tool fires. The moment a code file is written or edited, the session is marked as a code session and SDLC tracking begins. If no code file is ever touched, the Stop hook exits 0 immediately — zero latency, zero interference.

**Examples that do NOT trigger SDLC:**
- "What issues remain?" → Valor reads GitHub, responds. No code written. Pass-through.
- "Review this YouTube link" → Valor fetches URL, summarizes. No code written. Pass-through.
- "What does `post_tool_use.py` do?" → Valor reads the file, explains. No code written. Pass-through.
- `/do-plan issue-128` → Valor writes a `.md` plan file. Not a code file. Pass-through.
- `pytest tests/` → Valor runs tests (Bash only). No code written. Pass-through.
- Checking Telegram messages → Valor reads via tool. No code written. Pass-through.

**Examples that DO trigger SDLC:**
- "Fix the import error in `bridge.py`" → Valor edits `.py`. Code session. Gates apply.
- "The agent didn't complete, let me implement directly" → Valor writes `.py`. Code session. Gates apply.
- `do-build` pipeline → Writes `.py`/`.ts` files. Code session. Gates apply.
- "Add `delete_all()` to the model" → Valor writes `.py`. Code session. Gates apply.

### Pipeline Model

The canonical SDLC path for code sessions:

```
Plan → Branch → Implement → Test ──fail──→ /do-patch ─┐
                    ↑                                   │
                    │ (commits at checkpoints)          │(loop)
                    │                                   │
                    │           pass                    │
                    └───────────────────────────────────┘
                                  ↓
                             Review ──blockers──→ /do-patch → Test (max 3 iter)
                               │
                               success
                               ↓
                            Document → PR
```

Key properties:
- **Commits happen throughout Implement** — at each logical checkpoint, not batched at the end. Intermediate commits provide recovery points if the session crashes or the agent is interrupted. The commit message hook enforces hygiene at every commit.
- **Test failure loop**: test → `/do-patch` → test, no iteration cap (keep patching until it passes or the user intervenes)
- **Review blocker loop**: review → `/do-patch` → test → review, capped at **3 iterations** then escalates to human
- **Document** is a dedicated phase *after* review passes — not an afterthought added post-commit
- **PR** is the final step — push branch, open pull request

### Pipeline State Tracking

Pipeline state is persisted to `data/pipeline/{slug}/state.json`:

```json
{
  "slug": "my-feature",
  "branch": "session/my-feature",
  "worktree": ".worktrees/my-feature",
  "stage": "review",
  "completed_stages": ["plan", "branch", "implement", "test"],
  "patch_iterations": 1,
  "started_at": "2026-02-23T10:00:00Z",
  "updated_at": "2026-02-23T10:45:00Z"
}
```

When `/do-build` is invoked on a slug that already has a state file, it **resumes from the current stage** rather than starting over. This handles:
- Interrupted sessions (crash, timeout, manual stop)
- Deliberate mid-pipeline pivots (e.g., user wants to re-review after manual edits)
- Multi-session builds on long-running features

### Hook Enforcement

All enforcement lives in `.claude/hooks/` as lightweight Python scripts, registered in `.claude/settings.json`. No changes to the bridge, agent SDK, or core infrastructure.

**Three validators:**

1. **`validate_sdlc_on_stop.py`** (Stop hook)
   - Reads the session's tool use log (already captured by `post_tool_use.py`)
   - Checks: were any `.py`, `.js`, `.ts` files written/edited?
   - If yes, checks: did the session invoke `pytest`? `ruff`? `black`?
   - If quality tools were NOT run: exit 2 with clear instruction
   - If no code files were modified: exit 0 (pass through)
   - Escape hatch: env var `SKIP_SDLC=1` (for genuine emergencies), logs warning to `data/sessions/{id}/sdlc_state.json`

2. **`validate_commit_message.py`** (PreToolUse on Bash)
   - Inspects Bash tool input for `git commit` commands
   - Blocks commits containing `Co-Authored-By:` or `co-authored-by:` trailers
   - Blocks commits with `-m ""` (empty messages)
   - Fast: regex check on tool input string, no subprocess calls

3. **`sdlc_reminder.py`** (PostToolUse on Write/Edit)
   - When a `.py`/`.js`/`.ts` file is written/edited, logs it to the session tracking file
   - Emits a brief reminder: "SDLC: Remember to run tests and linting before completing this task"
   - Does NOT block (exit 0 always) -- purely advisory
   - Only fires once per session (checks session log to avoid spam)

**Session tracking enhancement:**
- The existing `post_tool_use.py` already logs tool usage. Extend it to track which file types were modified and which quality commands were run
- Store in `data/sessions/{session_id}/sdlc_state.json`

### `/do-patch` Skill

A new skill at `.claude/skills/do-patch/SKILL.md`, invoked as `/do-patch`. Follows the same `do-*` naming convention as `do-build`, `do-test`, `do-plan`. Distinct from `do-build`:

| | `do-build` | `do-patch` |
|--|--|--|
| Input | Plan document | Description of what's broken |
| Scope | Full implementation from scratch | Targeted fix to existing code |
| Worktree | Creates new worktree | Uses existing worktree or CWD |
| Agents | Heavy orchestration (multiple) | Single focused agent |
| Caller | User or model | `do-build` at test-fail/review-blocker steps, or user directly |
| Use case | Shipping a feature | Fixing test failures or review blockers |

**Invocation — user-facing:**
```
/do-patch "3 tests failing in test_bridge.py — connection timeout"
/do-patch "review blocker: race condition in session lock"
/do-patch  # (no args — reads most recent test failure from session context)
```

**Invocation — model-invocable (called by `do-build`):**

`do-build` invokes `/do-patch` automatically at two lifecycle points:
1. **Test fails** → `do-build` calls `/do-patch` with the pytest failure output, then re-runs `/do-test`. No iteration cap at this stage (keep patching until it passes or the user intervenes).
2. **Review blockers found** → `do-build` calls `/do-patch` with the review comment, then re-runs `/do-test` and `/do-pr-review`. Capped at **3 patch→test→review iterations**, then escalates to human.

**`/do-patch` flow:**
1. Accept failure description (or read last test/review output from context)
2. Read the failing test output or review comment in full
3. Deploy a single builder agent to make targeted edits
4. Re-run `/do-test` to verify the fix
5. If pass: report success, update pipeline state to the appropriate next stage
6. If fail: retry up to the caller's iteration cap, then report stuck with details
7. Never creates PRs, never commits, never touches the Document or Commit stages — those stay in `do-build`

## Rabbit Holes

- **Coverage measurement**: Don't try to enforce coverage percentages (100%/95%/90%) -- just ensure tests are run at all. Coverage enforcement is a separate, much harder problem
- **mypy enforcement**: mypy is slow and many files have issues. Don't block on mypy failures for now -- just `pytest`, `ruff`, `black`
- **Per-file test mapping**: Don't try to figure out which tests correspond to which files. Just require `pytest` was invoked
- **Subagent enforcement**: Subagents (Task tool) have their own sessions. Don't try to track quality across parent/child sessions -- enforce at the top-level session only
- **Pipeline state UI**: Don't build a dashboard or status command for pipeline state. The JSON file is the state; reading it directly is sufficient
- **`do-patch` auto-detection of worktree**: Don't try to auto-detect which worktree to use. The user (or `do-build`) invokes it from the right CWD context, or passes the slug explicitly
- **Commit squashing**: Don't squash intermediate commits before the PR. Recovery value outweighs a clean history. Commit hygiene is enforced per-commit via the message hook, not by reorganizing history
- **Session intent classification via LLM**: Don't use an LLM call to classify whether a session is "development" vs "conversation" — it's slow and expensive. File-type detection is the right signal

## Risks

### Risk 1: False positives — non-code sessions incorrectly flagged
**Impact:** Agent gets blocked during a conversation session that only touched config or docs
**Mitigation:** Only trigger on `.py`, `.js`, `.ts` file extensions. `.md`, `.json`, `.yaml`, `.toml`, `.txt`, `.sh` never trigger. Classification is purely file-extension based — deterministic, no false positives on non-code sessions.

### Risk 2: False negatives — code session slips through without enforcement
**Impact:** Agent modifies a `.py` file but the tracking hook misses it (e.g., tool fires too fast, file write fails silently)
**Mitigation:** `post_tool_use.py` runs synchronously after every tool use. If the tool succeeded (exit 0 from Write/Edit), the tracking update runs. Accept rare misses — enforcement is best-effort, not a security gate.

### Risk 3: Hook performance slowing down every interaction
**Impact:** Every Stop event takes 10+ seconds, degrading responsiveness for all sessions including casual conversation
**Mitigation:** First check: does `sdlc_state.json` exist? If not (non-code session), exit 0 immediately. File read only — no subprocess calls. Target < 200ms for non-code sessions, < 2s for code sessions.

### Risk 4: Agent learns to game the system
**Impact:** Agent runs `pytest` with no test files, or `ruff check` on an empty directory, to satisfy the gate
**Mitigation:** Phase 2 concern. For now, checking that the commands were invoked is sufficient. Trust but verify.

### Risk 5: Session classification is inadequate
**Impact:** A session type not covered by the file-extension heuristic slips through (e.g., code written via a tool not yet tracked, or a non-code session incorrectly flagged)
**Mitigation:** If inadequate classification is discovered during testing or live observation, the validator or observer must open a GitHub issue on this repo with:
- The session type that failed classification
- What tool use occurred (tool name, file path or command)
- Whether it was a false positive (non-code session blocked) or false negative (code session not gated)
- Any relevant Stop hook output or `sdlc_state.json` contents

Do not attempt to patch the classification inline. File the issue and let it be addressed as a follow-up improvement to `post_tool_use.py`.

## No-Gos (Out of Scope)

- No coverage enforcement (just test execution)
- No mypy enforcement (too slow, too many existing issues)
- No enforcement on docs-only, config-only, or conversation-only sessions
- No cross-session tracking (each session stands alone)
- No enforcement on subagent sessions (only top-level)
- No pipeline state UI or status command
- No commit squashing or history reorganization — intermediate commits stay as-is
- No LLM-based session classification — file-extension detection only
- `do-patch` does not create PRs or commit — those stay in `do-build`
- `do-patch` does not replace `do-build` for full builds

## Update System

No update system changes required -- this is purely internal hook infrastructure. The hooks are synced via the existing `.claude/` hardlink system in the update script.

## Agent Integration

No agent integration required -- these are Claude Code hooks that fire automatically. No MCP server changes, no bridge changes, no `.mcp.json` changes. The hooks interact with the agent purely through the Claude Code hook protocol (stdin JSON, stdout JSON, exit codes).

## Documentation

- [ ] Create `docs/features/sdlc-enforcement.md` — pipeline stage model, hooks, escape hatch, resume behavior
- [ ] Create `docs/features/do-patch-skill.md` — when to use `/do-patch` vs `/do-build`, user and model invocation examples, iteration caps
- [ ] Add both entries to `docs/features/README.md` index table
- [ ] Code comments on non-obvious logic in each validator and in `pipeline_state.py`

## Success Criteria

**Session classification:**
- [ ] Conversation-only sessions (no code writes) complete Stop in < 200ms with zero SDLC interference
- [ ] Docs/config-only sessions (`.md`, `.json`, `.yaml` edits) pass through without triggering quality gates
- [ ] Research sessions (Grep/Read/Bash only) pass through without triggering quality gates
- [ ] Code sessions (any `.py`/`.js`/`.ts` write or edit) correctly trigger quality gate on Stop
- [ ] The classification table above is exercised in validation tests

**Pipeline model:**
- [ ] `agent/pipeline_state.py` reads/writes state to `data/pipeline/{slug}/state.json`
- [ ] `/do-build` resumes from the correct stage when state file exists
- [ ] `/do-build` on a fresh slug initializes state and runs all stages
- [ ] Document stage runs after review passes, not during implementation
- [ ] Commits happen throughout Implement at logical checkpoints (not batched at end)

**Hook enforcement:**
- [ ] `validate_sdlc_on_stop.py` blocks agent from stopping if code was modified but tests/lint not run
- [ ] `validate_commit_message.py` blocks commits with co-author trailers
- [ ] `sdlc_reminder.py` emits one-time reminder when code files are modified
- [ ] All hooks registered in `.claude/settings.json`
- [ ] Code session Stop hook runs in < 2s
- [ ] Escape hatch (`SKIP_SDLC=1`) works for genuine emergencies
- [ ] Existing hooks (`stop.py`, `post_tool_use.py`, `pre_tool_use.py`) continue to work unchanged

**`/do-patch` skill:**
- [ ] `/do-patch "description"` applies a targeted fix without creating PRs or commits
- [ ] `/do-patch` loops up to the caller's iteration cap before reporting stuck
- [ ] `/do-patch` updates pipeline state on success (e.g., `test` → `review`)
- [ ] `/do-patch` with no args reads failure context from session
- [ ] `do-build` automatically invokes `/do-patch` at test-fail and review-blocker lifecycle steps
- [ ] Review-blocker loop capped at 3 patch→test→review iterations then escalates

**Overall:**
- [ ] Tests pass (`/do-test`)
- [ ] Documentation created (`docs/features/sdlc-enforcement.md`, `docs/features/do-patch-skill.md`)

## Team Orchestration

### Team Members

- **Builder (hooks)**
  - Name: hook-builder
  - Role: Implement validators, register hooks in settings.json, create patch skill, update do-build skill
  - Agent Type: builder
  - Resume: true

- **Builder (tracking)**
  - Name: tracking-builder
  - Role: Extend post_tool_use.py, create agent/pipeline_state.py
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify hooks fire correctly, test pipeline resumption, test patch skill edge cases
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs for SDLC enforcement and patch skill
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Extend session tracking in post_tool_use.py
- **Task ID**: build-tracking
- **Depends On**: none
- **Assigned To**: tracking-builder
- **Agent Type**: builder
- **Parallel**: true
- Extend `post_tool_use.py` to classify sessions by file type on every Write/Edit tool use
- Code files (`.py`, `.js`, `.ts`): mark session as code session, create `data/sessions/{session_id}/sdlc_state.json`
- Non-code files (`.md`, `.json`, `.yaml`, `.toml`, `.sh`, `.txt`): do nothing — no state file, no tracking
- Read-only tools (Glob, Grep, Read, Bash without writes): do nothing
- State file schema: `{"code_modified": true, "files": [...], "quality_commands": {"pytest": false, "ruff": false, "black": false}}`
- Detect when Bash tool runs `pytest`, `ruff`, or `black` and update `quality_commands` accordingly
- Absence of `sdlc_state.json` = non-code session = Stop hook exits immediately without checking anything
- Ensure state file creation is fast (< 100ms)

### 2. Create pipeline state manager
- **Task ID**: build-pipeline-state
- **Depends On**: none
- **Assigned To**: tracking-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/pipeline_state.py` — a simple module for reading/writing pipeline state
- State file location: `data/pipeline/{slug}/state.json`
- Schema: `slug`, `branch`, `worktree`, `stage`, `completed_stages`, `patch_iterations`, `started_at`, `updated_at`
- Stages enum: `plan`, `branch`, `implement`, `test`, `patch`, `review`, `document`, `commit`, `pr`
- Expose: `load(slug)`, `save(state)`, `advance_stage(slug, next_stage)`, `exists(slug)`
- If state file is missing, `load()` returns None (new build, start from scratch)

### 3. Update do-build skill to use pipeline state
- **Task ID**: build-pipeline-integration
- **Depends On**: build-pipeline-state
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `.claude/skills/do-build/SKILL.md` to:
  - On invocation: check `agent/pipeline_state.py` for existing state for the slug
  - If state exists: resume from `state["stage"]`, skip completed stages
  - If no state: initialize state at `plan` stage, proceed normally
  - After each stage completes: call `advance_stage()` to persist progress
  - Move the **Document** stage to after Review passes (not interleaved with implementation)
  - Move the **Document** stage explicitly after Review passes
  - Remove any "batch commit at end" instruction — commits happen at logical checkpoints during Implement
  - The commit message hook enforces hygiene at each commit; no special Commit stage needed
- Update the pipeline diagram in the skill to match the new canonical flow (Plan → Branch → Implement → Test → Review → Document → PR)

### 4. Create validate_commit_message.py
- **Task ID**: build-commit-gate
- **Depends On**: none
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/hooks/validators/validate_commit_message.py`
- Hook into PreToolUse on Bash
- Parse tool input for `git commit` commands
- Block if commit message contains `Co-Authored-By:` (case-insensitive)
- Block if commit message is empty
- Pass through all non-commit Bash commands immediately (fast path)

### 5. Create validate_sdlc_on_stop.py
- **Task ID**: build-stop-gate
- **Depends On**: build-tracking
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/validators/validate_sdlc_on_stop.py`
- Read the session's `sdlc_state.json` tracking file
- If `code_modified` is true and any quality command is false: exit 2 with clear instructions
- If no code was modified: exit 0
- Escape hatch: `SKIP_SDLC=1` env var (not a CLI flag — avoids accidental trigger)
- Follow existing validator patterns (read stdin JSON, output JSON on success, stderr on failure)

### 6. Create sdlc_reminder.py
- **Task ID**: build-reminder
- **Depends On**: build-tracking
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/hooks/sdlc_reminder.py`
- PostToolUse hook for Write/Edit on code files
- Emit one-time advisory reminder per session
- Check session tracking to avoid duplicate reminders
- Always exit 0 (never blocks)

### 7. Register hooks in settings.json
- **Task ID**: build-registration
- **Depends On**: build-stop-gate, build-commit-gate, build-reminder
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `validate_sdlc_on_stop.py` to Stop hooks
- Add `validate_commit_message.py` to PreToolUse hooks with Bash matcher
- Add `sdlc_reminder.py` to PostToolUse hooks with Write/Edit matcher
- Preserve all existing hooks

### 8. Create do-patch skill
- **Task ID**: build-patch-skill
- **Depends On**: build-pipeline-state
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.claude/skills/do-patch/SKILL.md`
- Invocation: `/do-patch [description of what's broken]`
- If no description given: read last test output or review comment from session context
- Flow: read failures → single builder agent makes targeted edits → re-run `/do-test`
- Loop up to caller's iteration cap before reporting stuck
- On success: update pipeline state to next stage (test → review, or review → document)
- Never creates PRs, never commits — those stay in `do-build`
- Skill description must flag it as **model-invocable** (called by `do-build` at test-fail and review-blocker lifecycle steps)
- Trigger phrases: "patch this", "fix the failures", "fix the blockers", "do-patch"

### 9. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-registration, build-pipeline-integration, build-patch-skill
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify hook registration in settings.json is syntactically correct
- Verify each validator script runs standalone with test inputs
- **Session classification tests** (must all pass):
  - Simulate a session with only Read/Grep/Bash tools → Stop hook exits 0 in < 200ms (no `sdlc_state.json` created)
  - Simulate a session that writes only a `.md` file → Stop hook exits 0, no quality gate triggered
  - Simulate a session that writes a `.py` file but runs neither `pytest` nor `ruff` → Stop hook exits 2 with clear instruction
  - Simulate a session that writes a `.py` file and runs `pytest` + `ruff` + `black` → Stop hook exits 0
  - Simulate a session checking a Telegram message, answering a question about the codebase → no `sdlc_state.json`, Stop exits 0
- Verify the `SKIP_SDLC=1` escape hatch works
- Verify pipeline state resume: create a mock state file at `stage: test`, invoke `/do-build`, confirm it skips Plan/Branch/Implement and resumes at Test
- Verify `/do-patch` applies a targeted fix without touching commit/PR steps
- Run existing tests to ensure no regressions
- If any classification test produces an unexpected result, open a GitHub issue on this repo (title: "SDLC classification gap: [description]") with the tool use details, expected vs. actual classification, and any relevant state file contents — do not patch inline

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-enforcement
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-enforcement.md`
  - What's enforced, how each hook works, escape hatch, troubleshooting
  - Pipeline stage diagram (ASCII, matches the photo)
  - How to resume a mid-pipeline session
- Create `docs/features/do-patch-skill.md`
  - When to use `/do-patch` vs `/do-build`
  - User invocation examples, model-invocable lifecycle integration, iteration caps
- Add both entries to `docs/features/README.md` index table

### 11. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python .claude/hooks/validators/validate_sdlc_on_stop.py --help` - Verify stop gate script loads
- `python .claude/hooks/validators/validate_commit_message.py --help` - Verify commit gate script loads
- `python .claude/hooks/sdlc_reminder.py --help` - Verify reminder script loads
- `python -c "import json; json.load(open('.claude/settings.json'))"` - Verify settings.json is valid JSON
- `python -c "from agent.pipeline_state import load, save; print('ok')"` - Verify pipeline state module imports
- `python -c "from agent.pipeline_state import load; print(load('nonexistent'))"` - Should print None
- `pytest tests/ -v` - Full test suite passes
- `ruff check .` - Linting passes
- `black --check .` - Formatting passes

## Open Questions

1. Stop gate blocks (exit 2) with `SKIP_SDLC=1` escape hatch. ~~Resolved.~~
2. Enforce `pytest` invocation only (not pass/fail). Exit code tracking is a follow-up. ~~Resolved.~~
3. `/do-patch` is model-invocable (by `do-build`) and user-invocable. ~~Resolved.~~
4. Commits happen at logical checkpoints throughout Implement — not batched at end. Commit message hook enforces hygiene at each commit. ~~Resolved.~~
5. Session classification is file-extension based (deterministic), not LLM-based. Non-code sessions have zero SDLC overhead. ~~Resolved.~~
