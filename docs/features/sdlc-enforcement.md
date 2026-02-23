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
3. `black .` — formatter

The stop hook reads `data/sessions/{session_id}/sdlc_state.json` and blocks exit (exit code 2) if any command was skipped.

**Absence of `sdlc_state.json` = non-code session = stop hook exits 0 immediately.**

## The Three Hooks

### 1. `validate_sdlc_on_stop.py` — Stop Hook

Fires when Claude attempts to end a session.

- Reads `data/sessions/{session_id}/sdlc_state.json`
- If file doesn't exist: exit 0 immediately (< 200ms)
- If `code_modified: true` and any quality command missing: exit 2 with list of what's missing
- If all quality commands present: exit 0

### 2. `validate_commit_message.py` — PreToolUse/Bash Hook

Fires before any Bash tool call containing `git commit`.

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
- **Commits happen throughout Implement** at logical checkpoints — not batched at end
- **Test failure loop**: no iteration cap — keep patching until it passes or human intervenes
- **Review blocker loop**: capped at 3 patch→test→review iterations, then escalates to human
- **Document** is a dedicated phase *after* review passes

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
  "quality_commands": {"pytest": true, "ruff": false, "black": false},
  "reminder_sent": true
}
```

## Troubleshooting

**"SDLC Quality Gate" appears at session end:**
```bash
pytest tests/ && ruff check . && black .
```
Then end the session normally.

**False positive (non-code session blocked):**
Set `SKIP_SDLC=1` to unblock, then file a GitHub issue with the session ID and `sdlc_state.json` contents. Do not patch the classification inline.

**Stop hook is slow on non-code sessions:**
Should not happen — the first check is a file existence test. If slow, verify `sdlc_state.json` doesn't exist for the session.

## Related

- [do-patch Skill](do-patch-skill.md) — repair loop invoked on test failure or review blockers
- `.claude/hooks/validators/validate_commit_message.py` — commit message validation (blocks co-author trailers and empty messages)
- `agent/pipeline_state.py` — pipeline state read/write module
- `.claude/hooks/validators/validate_sdlc_on_stop.py` — stop hook source
