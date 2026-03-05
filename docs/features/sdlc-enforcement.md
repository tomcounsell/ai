# SDLC Enforcement

Automated quality gates that fire on every code session, not just `/do-build` runs. Non-code sessions pass through with zero latency and zero interference.

## What Is Enforced

### Session Classification

The system classifies every session by the files it touches:

| Signal | Classification | SDLC applies? |
|--------|----------------|---------------|
| Write/Edit on `.py`, `.js`, `.ts` | **Code session** | Yes |
| Write/Edit on `.md`, `.json`, `.yaml`, `.toml` | **Docs/config session** | No |
| Bash only (tests, git, logs) | **Ops session** | No |
| Glob/Grep/Read only | **Research session** | No |

Classification is file-extension based — deterministic, no LLM calls, no false positives on non-code sessions.

### Quality Gate for Code Sessions

Before a code session ends, these commands must have been run:

1. `pytest tests/` — full test suite
2. `ruff check .` — linter
3. `ruff format .` — formatter

The stop hook reads `data/sessions/{session_id}/sdlc_state.json` and blocks exit (exit code 2) if any command was skipped.

**Absence of `sdlc_state.json` triggers a fallback check:** on `main`, the hook examines both uncommitted changes (`git diff HEAD`) and the most recent commit (`git diff HEAD~1 HEAD`) for code files. If code is found on main without SDLC tracking, the hook blocks with a remediation message. On feature branches, absence of `sdlc_state.json` = non-code session = exit 0 immediately.

## The Three Hooks

### 1. `validate_sdlc_on_stop.py` — Stop Hook

Fires when Claude attempts to end a session.

- Reads `data/sessions/{session_id}/sdlc_state.json`
- If file doesn't exist AND on `main`: runs fallback — checks both uncommitted working tree changes and most recent commit for code files. Blocks if code found without SDLC tracking.
- If file doesn't exist AND on feature branch: exit 0 immediately (< 200ms)
- If `code_modified: true` and any quality command missing: exit 2 with list of what's missing
- If all quality commands present: exit 0

### 2. `validate_commit_message.py` — PreToolUse/Bash Hook

Fires before any Bash tool call containing `git commit`.

- **Blocks code file commits on main unconditionally**: If on `main` branch and staged files include `.py`, `.js`, or `.ts` files, the commit is blocked regardless of SDLC context. Non-code files (docs, plans, configs) are allowed on main.
- Blocks commits with `Co-Authored-By:` trailers (case-insensitive)
- Blocks commits with empty messages
- All other Bash commands pass through immediately

### 3. `sdlc_reminder.py` — PostToolUse/Write+Edit Hook

Fires after any Write or Edit on a `.py`/`.js`/`.ts` file.

- Emits a one-time advisory per session: `"SDLC: Remember to run tests and linting before completing this task"`
- Checks `sdlc_state.json` for `reminder_sent: true` to suppress duplicates
- Always exits 0 — purely advisory, never blocking

## Escape Hatch

```bash
SKIP_SDLC=1 claude
```

Set `SKIP_SDLC=1` to bypass the stop gate. The hook exits 0 with a warning logged. Use for genuine emergencies only (production incidents, broken environments). Routine use defeats the gate.

## Pipeline Stage Model

See `.claude/skills/sdlc/SKILL.md` for the ground truth on pipeline stages.

Stages: **Plan → Build → Test → Patch → Review → Patch → Docs → Merge**

Key properties:
- **Commits happen throughout Build** at logical checkpoints — not batched at end
- **Test failure loop**: no iteration cap — keep patching until it passes or human intervenes
- **Review blocker loop**: capped at 3 patch→test→review iterations, then escalates to human
- **Docs** is a dedicated phase *after* review passes, right before merge

## Pipeline State Persistence

State is persisted to `data/pipeline/{slug}/state.json`:

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

When `/do-build` is invoked on a slug with an existing state file, it resumes from `state["stage"]` rather than starting over. Handles: interrupted sessions, crashes, manual mid-pipeline pivots, multi-session builds.

## Session State File

Each code session gets `data/sessions/{session_id}/sdlc_state.json`:

```json
{
  "code_modified": true,
  "files": ["bridge/telegram_bridge.py"],
  "quality_commands": {"pytest": true, "ruff": false, "ruff-format": false},
  "reminder_sent": true
}
```

## Troubleshooting

**"SDLC Quality Gate" appears at session end:**
```bash
pytest tests/ && ruff check . && ruff format --check .
```
Then end the session normally.

**False positive (non-code session blocked):**
Set `SKIP_SDLC=1` to unblock, then file a GitHub issue with the session ID and `sdlc_state.json` contents. Do not patch the classification inline.

**Stop hook is slow on non-code sessions:**
Should not happen — the first check is a file existence test. If slow, verify `sdlc_state.json` doesn't exist for the session.

## Agent SDK Enforcement

A second layer of SDLC enforcement operates at the Claude Agent SDK level, independent of the Claude Code hook system. This layer catches code pushed directly to `main` when the agent runs outside of a `/do-build` worktree.

### System Prompt Injection (SDLC_WORKFLOW)

Every agent session started by `ValorAgent` receives the mandatory pipeline rules injected into the system prompt. The rules are hardcoded in `agent/sdk_client.py` as the `SDLC_WORKFLOW` constant — not in `config/SOUL.md` or any config file. This prevents accidental removal via persona doc edits.

The system prompt structure assembled by `load_system_prompt()`:

```
[SOUL.md — persona, attitude, purpose, communication style]
---
[SDLC_WORKFLOW — mandatory pipeline rules, hardcoded in sdk_client.py]
---
[Work Completion Criteria — from CLAUDE.md]
```

The `SDLC_WORKFLOW` block tells the agent:
- ALL code changes require: Issue → Plan → Build → PR → Merge
- NEVER commit code to main
- NEVER push code to main — all pushes go to `session/{slug}` branches
- Plan/doc changes (`.md`, `.json`, `.yaml`) may be committed directly to main
- Code changes (`.py`, `.js`, `.ts`) never go directly to main

### Pre-Completion Check (_check_no_direct_main_push)

`agent/sdk_client.py` provides `_check_no_direct_main_push(session_id, repo_root)` which:

1. Reads `data/sessions/{session_id}/sdlc_state.json`
2. If no state file exists: passes (non-code session)
3. If `code_modified: false`: passes (docs/ops session)
4. If `code_modified: true`: checks current git branch via `git rev-parse --abbrev-ref HEAD`
5. If branch is not `main`: passes (inside a `/do-build` worktree on `session/{slug}`)
6. If branch IS `main`: returns a hard-block error with the list of modified files and remediation steps

**Hard-block, no escape hatch at this layer.** The Claude Code hook system has `SKIP_SDLC=1` for genuine emergencies. The SDK stop hook does not — Telegram is free text and there is no mechanism for a user to signal an override through the message channel.

**Fail-open on errors.** If the state file is corrupt or git fails, the check fails open (returns None) and logs a warning. The check never crashes a session.

### Stop Hook Integration

The check is wired into `agent/hooks/stop.py`, which fires when the Agent SDK session ends:

```python
violation = _check_no_direct_main_push(session_id)
if violation:
    return {"decision": "block", "reason": violation}
```

Sessions on `session/{slug}` branches — all `/do-build` builder agents — always pass the branch check. The check exclusively targets direct-to-main code pushes from ad-hoc sessions.

### SOUL.md Cleanup

`config/SOUL.md` no longer contains SDLC workflow instructions. The "Orchestration Instructions" section (Task Classification, SDLC Pattern, Parallel Execution, Validation Loop, Response Pattern blocks) has been removed. SOUL.md is now pure persona — who Valor is, communication style, values, machine setup. Pipeline rules live in `agent/sdk_client.py`.

### Two-Layer Summary

| Layer | Where | Enforcement |
|-------|-------|-------------|
| **Behavioral** | Agent system prompt (`SDLC_WORKFLOW`) | Instructs the agent to follow the pipeline |
| **Structural** | SDK Stop hook (`_check_no_direct_main_push`) | Hard-blocks code-on-main at session end |
| **Quality gate** | Claude Code Stop hook (`validate_sdlc_on_stop.py`) | Blocks if pytest/ruff/ruff-format not run |

## User-Level Deployment

SDLC enforcement hooks are deployed to `~/.claude/` so they fire in **every repo on every machine**, not just the AI repo.

### How Hooks Are Deployed

The update system (`scripts/update/hardlinks.py`) includes `sync_user_hooks()` which:

1. Copies `.claude/hooks/sdlc/*.py` to `~/.claude/hooks/sdlc/` via hardlinks
2. Merges hook entries into `~/.claude/settings.json` (deduplicated by command string)
3. Never clobbers non-SDLC user hooks

Running the update script on any machine automatically installs the hooks.

### Shared Context Module

All 3 hooks import shared utilities from `sdlc_context.py` (`read_stdin`, `allow`, `block`). The `sdlc_reminder.py` and `validate_sdlc_on_stop.py` hooks also use `is_sdlc_context()` for context-aware behavior. `validate_commit_message.py` does **not** use `is_sdlc_context()` — it blocks code commits on main unconditionally based on staged file extensions.

The `is_sdlc_context()` detection is two-tier:
1. **Branch check**: Is the current git branch `session/*`? (Works in any repo)
2. **AgentSession check**: Does the Redis-backed AgentSession have SDLC stages? (Requires AI repo + Redis)

The AgentSession import is wrapped in try/except — on machines without Redis or the AI repo, detection falls back to branch-only, which is sufficient for worktree-based builds.

### Hook Files at User Level

```
~/.claude/
├── hooks/
│   └── sdlc/
│       ├── sdlc_context.py              # Shared detection utilities
│       ├── validate_commit_message.py    # PreToolUse: blocks code commits on main
│       ├── sdlc_reminder.py             # PostToolUse: one-time test reminder
│       └── validate_sdlc_on_stop.py     # Stop: quality gate enforcement
└── settings.json                         # Hook entries merged here
```

### Settings.json Hook Entries

The merger adds these entries (if not already present):

| Event | Matcher | Script | Timeout |
|-------|---------|--------|---------|
| PreToolUse | Bash | validate_commit_message.py | 10s |
| PostToolUse | Write | sdlc_reminder.py | 10s |
| PostToolUse | Edit | sdlc_reminder.py | 10s |
| Stop | (all) | validate_sdlc_on_stop.py | 15s |

## Related

- [do-patch Skill](do-patch-skill.md) — repair loop invoked on test failure or review blockers
- `.claude/hooks/validators/validate_commit_message.py` — commit message validation (blocks co-author trailers and empty messages)
- `agent/pipeline_state.py` — pipeline state read/write module
- `.claude/hooks/validators/validate_sdlc_on_stop.py` — stop hook source
- `agent/sdk_client.py` — `SDLC_WORKFLOW` constant, `load_system_prompt()`, `_check_no_direct_main_push()`
- `agent/hooks/stop.py` — SDK stop hook that fires `_check_no_direct_main_push()`
