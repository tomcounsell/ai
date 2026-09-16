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

### 2. `validate_commit_message_sdlc.py` — PreToolUse/Bash Hook

Fires before any Bash tool call that invokes `git commit`.

- **Blocks code file commits on main unconditionally**: If on `main` branch and staged files include `.py`, `.js`, or `.ts` files, the commit is blocked regardless of SDLC context. Non-code files (docs, plans, configs) are allowed on main.
- All other Bash commands pass through immediately

Co-author trailers and empty messages are **not** this hook's concern. That check lives in the project-scope `.claude/hooks/validators/validate_commit_message.py` (listed below), and no such logic exists anywhere in the `sdlc/` fork.

**What counts as a commit** is decided by tokenizing, never by a substring search (`is_git_commit`). `git -C <worktree> commit` is a commit even though the literal string `git commit` never appears in it, and `echo "git commit"` is not one. The recognizer steps over leading `VAR=value` assignments and the values of git's value-taking global options (`-C`, `-c`, `--git-dir`, `--work-tree`), then requires the first bare subcommand token to be `commit`.

Recognition and directory resolution share one parser (`parse_git_invocation`), and it **stops at the subcommand**. That boundary carries weight in both directions. Everything after the subcommand belongs to the subcommand, so `git commit -C HEAD` is `--reuse-message` and not a directory change; treating it as a path produced an effective directory that could not exist, which failed every git query into the fail-open handler and allowed the commit. The same requirement keeps `grep -C 3 commit README.md` from being read as a git invocation at all.

**Command splitting is quote-aware.** A newline, `&&`, `;` or `|` separates commands only outside quotes. A multi-line `-m` message — the normal shape here, and the shape of every message carrying a `Closes #N` disposition line — would otherwise be shredded into fragments with unbalanced quotes, none of which tokenize, so the command would not be recognized as a commit and would be allowed. Comments are skipped for the same reason: an apostrophe inside a `#` comment opens a quote that never closes, swallowing every separator after it, so a commit further down the script stops being recognized. In real bash an unbalanced quote is a syntax error everywhere except inside a comment, which is why that is the one shape worth handling. A `#` inside quotes stays message text.

**Every git query is scoped to the directory the command will actually run in.** `effective_git_dir` (in `sdlc_context.py`) resolves, in order: a directory-bearing **global** option of the git invocation that carries the commit (`-C`, else `--work-tree`, else `--git-dir`, whose parent is the working tree), a leading `cd <path>` (read through `cd`'s own option grammar — `-L`, `-P` and the `cd -- "$dir"` idiom are options, not the path), the hook payload's `cwd`, and only as a last resort the hook process's own working directory. A path token that was never expanded is rejected in favor of the next rung — `$(...)`, `` ` ``, `${...}`, a leading `$`, or a leading `~` (tilde expansion is the shell's job, not `shlex`'s). Using any of them as a directory would send every git call into its fail-open handler, which allows.

Reading git state from the process working directory is the defect this replaced (#3259). It produced false blocks (the lane is on a feature branch, the shared checkout is on main) and, worse, false allows in the other direction.

**Repository identity comes from the common git dir**, via `git rev-parse --path-format=absolute --git-common-dir` with a fallback to the bare flag for git < 2.31. The worktree-root probe (`--show-toplevel`) is not used on any path: inside `popoto/.worktrees/lane-a` it returns the *worktree* root, whose basename is the lane slug, so the commit clears the `!= "popoto"` gate and is allowed — a false-allow-always for exactly the population this guard protects. When identity cannot be determined at all, the hook takes the **restrictive** branch: a guard that cannot tell which repo it is in must not conclude "not the protected one, therefore fine."

**Deployment is verified, not assumed.** `sync_user_hooks` ends in `verify_deployed_commit_guard`, which builds a throwaway `popoto` repo with a linked worktree on `main`, stages a `.py` file, and asks the *deployed* hook — through the same interpreter the generated settings command names — whether it blocks. It asks in **both** directions: the staged commit must block, and a read-only `git status` must be allowed. Asserting only that something blocks would certify a deny-all guard as healthy, which wedges every session on the machine. A fixture that cannot be built warns and continues (it must still be *said* — a behavioral proof that can silently stop running is not a proof); either wrong answer is a hard error that fails the run — routed to `result.errors`, not `result.warnings`, because a guard that is deployed and wrong is not a file that failed to link. The hardlink existing and the script importing were both green throughout the #3259 window; this is the only signal that asks the question the fleet depends on.

### 3. `sdlc_reminder.py` — PostToolUse/Write+Edit Hook

Fires after any Write or Edit on a `.py`/`.js`/`.ts` file.

- Emits a one-time advisory per session: `"SDLC: Remember to run tests and linting before completing this task"`
- Checks `sdlc_state.json` for `reminder_sent: true` to suppress duplicates
- Always exits 0 — purely advisory, never blocking

## Escape Hatch

```bash
SKIP_SDLC=1 claude
```

Set `SKIP_SDLC=1` to bypass **all** SDLC stop gates — both the Claude Code hook system and the Agent SDK stop hook. The hooks exit cleanly with a warning logged. Use for genuine emergencies only (production incidents, broken environments, recovery from false-positive infinite loops). Routine use defeats the gate.

## Pipeline Stage Model

See `.claude/skills-global/do-sdlc/SKILL.md` for the ground truth on pipeline stages.

Stages: **Plan → Critique → Build → Test → Patch → Review → Patch → Docs → Merge**

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
  "modified_on_branch": "session/my-feature",
  "files": ["bridge/telegram_bridge.py"],
  "quality_commands": {"pytest": true, "ruff": false, "ruff-format": false},
  "reminder_sent": true
}
```

The `modified_on_branch` field tracks where code was first written. It is updated dynamically:
- **First code edit**: Records the current git branch (may be `main` if code is edited before branching)
- **Branch switch**: `git checkout -b session/*` or `git switch -c session/*` updates the field to the new session branch, so a stale `"main"` recording does not persist
- **PR merge**: `gh pr merge` clears both `code_modified` and `modified_on_branch` to prevent stale state

## Troubleshooting

**"SDLC Quality Gate" appears at session end:**
```bash
pytest tests/ && ruff check . && ruff format --check .
```
Then end the session normally.

**False positive (non-code session blocked):**
Set `SKIP_SDLC=1` to unblock, then file a GitHub issue with the session ID and `sdlc_state.json` contents. Do not patch the classification inline.

**Stop hook infinite loop ("SDLC VIOLATION" fires repeatedly):**
This occurs when code was edited on `main` before creating a feature branch, leaving `modified_on_branch: "main"` in the state file. The stop hook blocks, the agent responds, the hook fires again — an unrecoverable loop. Three defenses prevent it:
1. Branch switch detection updates `modified_on_branch` when `git checkout -b session/*` is run
2. Live git diff check verifies actual uncommitted changes before reporting violations
3. `SKIP_SDLC=1` escape hatch is available at the SDK layer for manual recovery

If you encounter a stale loop, set `SKIP_SDLC=1` or manually edit `data/sessions/{session_id}/sdlc_state.json` to update `modified_on_branch`.

**Stop hook is slow on non-code sessions:**
Should not happen — the first check is a file existence test. If slow, verify `sdlc_state.json` doesn't exist for the session.

## Agent SDK Enforcement

A second layer of SDLC enforcement operates at the Claude Agent SDK level, independent of the Claude Code hook system. This layer catches code pushed directly to `main` when the agent runs outside of a `/do-build` worktree.

### System Prompt Injection (SDLC_WORKFLOW)

Every agent session receives the mandatory pipeline rules injected into the system prompt.

The system prompt structure assembled by `load_system_prompt()`:

```
[WORKER_RULES — safety rails for the worker, FIRST — takes precedence]
---
[Persona prompt — base + developer overlay from config/personas/]
---
[Principal Context — condensed mission/goals/priorities from PRINCIPAL.md]
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

1. If `SKIP_SDLC=1` env var is set: passes immediately (escape hatch for recovery)
2. Reads `data/sessions/{session_id}/sdlc_state.json`
3. If no state file exists: passes (non-code session)
4. If `code_modified: false`: passes (docs/ops session)
5. If `code_modified: true`: checks current git branch via `git rev-parse --abbrev-ref HEAD`
6. If branch is not `main`: passes (inside a `/do-build` worktree on `session/{slug}`)
7. If branch IS `main` and `modified_on_branch` starts with `session/`: passes (arrived via merge)
8. If branch IS `main`: cross-checks live `git diff` for actual uncommitted code files
9. If no actual uncommitted code changes: passes (stale state)
10. If actual code changes uncommitted on `main`: returns a hard-block error

**Fail-open on errors.** If the state file is corrupt, git fails, or the diff check errors, the check fails open (returns None) and logs a warning. The check never crashes a session.

### Stop Hook Integration

The check is wired into `agent/hooks/stop.py`, which fires when the Agent SDK session ends:

```python
violation = _check_no_direct_main_push(session_id)
if violation:
    return {"decision": "block", "reason": violation}
```

Sessions on `session/{slug}` branches — all `/do-build` builder agents — always pass the branch check. The check exclusively targets direct-to-main code pushes from ad-hoc sessions.

### Persona Segment Cleanup

The persona segments (`config/personas/segments/`) contain only behavioral content -- identity, work patterns, and tool references. Pipeline rules live exclusively in `agent/sdk_client.py`.

### Two-Layer Summary

| Layer | Where | Enforcement |
|-------|-------|-------------|
| **Behavioral** | Agent system prompt (`SDLC_WORKFLOW`) | Instructs the agent to follow the pipeline |
| **Structural** | SDK Stop hook (`_check_no_direct_main_push`) | Hard-blocks code-on-main at session end |
| **Quality gate** | Claude Code Stop hook (`validate_sdlc_on_stop.py`) | Blocks if pytest/ruff/ruff-format not run |

## User-Level Deployment

SDLC enforcement hooks are deployed to `~/.claude/` so they fire in **every repo on every machine**, not just the AI repo.

### How Hooks Are Deployed

The 3 user-scope SDLC hooks are declared as `scope = "global"` entries in `.claude/hooks/manifest.toml` (see [Hook Manifest](hook-manifest.md)). The update system (`scripts/update/hardlinks.py`) includes `sync_user_hooks()` which:

1. Copies `.claude/hooks/sdlc/*.py` to `~/.claude/hooks/sdlc/` via hardlinks
2. Merges hook entries into `~/.claude/settings.json`, keyed by the stable `manifest_id` embedded as a trailing `# hook:<id>` comment on each command — not the command string. `_merge_hook_settings()` supports full add/update/**remove**, so a hook removed from the manifest is swept from `~/.claude/settings.json` on the next `/update`.
3. Never clobbers non-SDLC user hooks

Running the update script on any machine automatically installs the hooks.

### User-scope Stop hook `|| true` guard

The manifest declares `validate_sdlc_on_stop_fork` with `blocking = false`, so
the generator always emits the `|| true` guard on the user-scope Stop command. A
non-zero exit from `validate_sdlc_on_stop.py` (an exception, a timeout SIGKILL)
is swallowed rather than blocking the agent's turn-end.

### Both-Scope Audit

`reflections/audits/hooks_audit.py` (the daily `hooks-audit` reflection) validates **both** `.claude/settings.json` and `~/.claude/settings.json`, prefixing findings `[project]`/`[user]`, so a regression in either scope's Stop-hook guard is caught.

### Shared Context Module

All 3 hooks import shared utilities from `sdlc_context.py` (`read_stdin`, `allow`, `block`, plus `effective_git_dir` / `split_simple_commands` for directory resolution). The `sdlc_reminder.py` and `validate_sdlc_on_stop.py` hooks also use `is_sdlc_context()` for context-aware behavior. `validate_commit_message_sdlc.py` does **not** use `is_sdlc_context()` — it blocks code commits on main unconditionally based on staged file extensions.

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
│       ├── validate_commit_message_sdlc.py  # PreToolUse: blocks code commits on main
│       ├── sdlc_reminder.py             # PostToolUse: one-time test reminder
│       └── validate_sdlc_on_stop.py     # Stop: quality gate enforcement
└── settings.json                         # Hook entries merged here
```

### Settings.json Hook Entries

The manifest declares these entries (`scope = "global"`), merged into `~/.claude/settings.json`:

| Event | Matcher | Script | Timeout |
|-------|---------|--------|---------|
| PreToolUse | Bash | validate_commit_message_sdlc.py | 10s |
| PostToolUse | `Write\|Edit` | sdlc_reminder.py | 10s |
| Stop | (all) | validate_sdlc_on_stop.py | 15s |

The manifest declares `sdlc_reminder.py` **once** with the alternation matcher `Write|Edit`, covering both `Write` and `Edit` events.

## Merge Guard

A PreToolUse hook (`.claude/hooks/validators/validate_merge_guard.py`) blocks `gh pr merge` commands in Bash tool calls. PR merges require human authorization through the `/do-merge` skill, which validates prerequisites (TEST, REVIEW, DOCS completed) before presenting the PR for merge approval.

MERGE is a formal pipeline stage routed after DOCS completes. See [SDLC Pipeline Integrity](sdlc-pipeline-integrity.md) for full details.

**Cross-repo detection**: the merge predicate is inherently local, so the guard refuses to judge a PR belonging to a different repository. It recognizes both an explicit `-R`/`--repo` flag and a directory change — a literal `cd`/`pushd` chain preceding the merge is resolved to an effective working directory whose origin slug is compared against the local one. A positively-foreign slug blocks with the cross-repo message (which never offers the `data/merge_authorized_{pr}` break-glass, since that file cannot authorize a foreign PR). Same-slug and unresolvable directories (shell variables, quoted or substituted targets, non-checkout paths) fall through to the normal predicate path, so ambiguity never false-blocks a local merge. `git -C` is deliberately not a cross-repo signal: it does not change the process cwd that `gh` resolves its base repo from.

## Related

- [Hook Manifest](hook-manifest.md) — the manifest declaration, generators, and both-scope audit
- [SDLC Pipeline Integrity](sdlc-pipeline-integrity.md) — session hardening, URL validation, merge guard, MERGE stage
- [do-patch Skill](do-patch-skill.md) — repair loop invoked on test failure or review blockers
- `.claude/hooks/validators/validate_commit_message.py` — commit message validation (blocks co-author trailers and empty messages)
- `.claude/hooks/validators/validate_merge_guard.py` — merge guard hook (blocks `gh pr merge`)
- `agent/build_pipeline.py` — build pipeline state read/write module
- `.claude/hooks/validators/validate_sdlc_on_stop.py` — stop hook source
- `agent/sdk_client.py` — `SDLC_WORKFLOW` constant, `load_system_prompt()`, `_check_no_direct_main_push()`
- `agent/hooks/stop.py` — SDK stop hook that fires `_check_no_direct_main_push()`
