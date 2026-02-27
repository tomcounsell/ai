---
status: Complete
type: bug
appetite: Small
owner: Valor
created: 2026-02-27
tracking: https://github.com/tomcounsell/ai/issues/207
---

# SDLC Bypass Guardrails (5 Whys)

## Problem

Commit `351e538d` added Python code to `.claude/hooks/pre_tool_use.py` and `post_tool_use.py` and pushed directly to `main` — bypassing the entire SDLC pipeline. This happened during a diagnostic conversation where the system coach auto-continued with "implement a fix."

**Current behavior:**
1. `validate_commit_message.py` only blocks commits on main when `is_sdlc_context()` is True — but non-SDLC sessions (diagnostic conversations) return False, allowing direct main commits
2. `validate_sdlc_on_stop.py` relies on `sdlc_state.json` existing — if the state file was never created (hook failure), it treats the session as non-code
3. Hook state tracking (`post_tool_use.py`) swallows all errors silently
4. SOUL.md says "commit and push to ANY branch including main" which contradicts CLAUDE.md's "NEVER commit code directly to main"
5. Auto-continue coaching can suggest implementation work without routing through `/sdlc`

**Desired outcome:**
Code files (.py, .js, .ts) cannot be committed to main regardless of session type or SDLC context.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Five targeted edits to five files. Each fix is one-to-five lines of code or documentation change.

## Prerequisites

No prerequisites — all files already exist.

## Solution

### Key Elements

- **Branch-agnostic commit guard**: Block code commits on main unconditionally (not just in SDLC context)
- **Stop hook fallback**: Check `git diff` for code files when no state file exists
- **Error visibility**: Log hook state failures to stderr
- **Policy alignment**: Remove SOUL.md contradiction about main branch pushes
- **Coach routing**: Add note to coaching prompt about implementation work

### Technical Approach

#### Fix 1: validate_commit_message.py — Block code commits on main unconditionally

The current hook only blocks when `is_sdlc_context()` is True. Change it to block ALL code-file commits on main, regardless of context.

```python
# Current (line 66):
if is_sdlc_context():
    block("SDLC enforcement: Cannot commit directly to main...")

# New: Check if staged files include code extensions
# If committing on main with .py/.js/.ts files staged, always block
result = subprocess.run(
    ["git", "diff", "--cached", "--name-only"],
    capture_output=True, text=True, timeout=5
)
code_extensions = {".py", ".js", ".ts"}
staged_code = [f for f in result.stdout.strip().split("\n")
               if any(f.endswith(ext) for ext in code_extensions)]
if staged_code:
    block(f"Cannot commit code files to main: {', '.join(staged_code[:3])}. "
          "Use /sdlc to create a branch and PR.")
# Non-code files (docs, plans, configs) are still allowed on main
allow()
```

Remove the `is_sdlc_context()` dependency entirely for this check.

#### Fix 2: validate_sdlc_on_stop.py — Git diff fallback

Add a fallback when no `sdlc_state.json` exists: check `git diff HEAD` for code files on main.

```python
# After line 68 (the early return for missing state file):
# Fallback: if on main with code changes, enforce quality gates
branch = subprocess.run(
    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
    capture_output=True, text=True, timeout=5
).stdout.strip()
if branch == "main":
    diff = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, timeout=5
    ).stdout.strip()
    code_exts = {".py", ".js", ".ts"}
    if any(f.split(".")[-1] in {"py", "js", "ts"} for f in diff.split("\n") if f):
        # Code on main without state tracking — create a synthetic state
        return _ERROR_TEMPLATE.format(
            missing_lines="  - Code files modified on main without SDLC tracking.\n"
            "  - Run quality checks or use /sdlc to create a branch."
        )
```

#### Fix 3: post_tool_use.py — Log hook state failures

Replace `pass` in `save_sdlc_state` error handling with stderr logging.

```python
# In update_sdlc_state_for_file_write, add error visibility:
try:
    save_sdlc_state(session_id, state)
except Exception as e:
    print(f"HOOK WARNING: Failed to save SDLC state for {session_id}: {e}", file=sys.stderr)
```

Note: `save_sdlc_state` already has its own try/except that re-raises. The issue is in the caller functions that don't catch failures. Add a try/except wrapper around the `save_sdlc_state` call in `update_sdlc_state_for_file_write()` and `update_sdlc_state_for_bash()`.

#### Fix 4: config/SOUL.md — Remove main branch override

Change the git autonomy section to align with CLAUDE.md:

```markdown
# Current:
- I commit and push to ANY branch including main without approval

# New:
- I commit and push to feature branches (session/*) without approval
- Code changes to main require a PR — only docs/plans/configs go directly to main
```

Also remove: "The 'Git Safety Protocol' from Claude Code defaults does NOT apply to me" and replace with: "Git operations follow the SDLC pipeline for code changes."

#### Fix 5: Coaching prompt — Route implementation to /sdlc

In `bridge/summarizer.py`, the `SUMMARIZER_SYSTEM_PROMPT` generates coaching messages. Add guidance that when the coaching message suggests writing code, it should recommend `/sdlc` instead of inline implementation.

This is a one-line addition to the coaching_message prompt instructions in the classifier.

## Rabbit Holes

- Redesigning the entire hook system — just add the missing checks
- Building a comprehensive git pre-commit framework — the existing hook infrastructure is sufficient
- Changing how auto-continue works systemically — just add a note to the coach prompt

## Risks

### Risk 1: False positives blocking legitimate main commits
**Impact:** Agent can't commit doc/plan changes to main
**Mitigation:** Fix 1 only blocks code files (.py/.js/.ts), not docs/configs. Plan/doc changes remain unrestricted on main.

### Risk 2: Stop hook fallback creating noise
**Impact:** False quality gate warnings on main
**Mitigation:** Fallback only triggers when code files are detected in `git diff` on main AND no state file exists. Normal SDLC sessions have state files.

## No-Gos (Out of Scope)

- Redesigning auto-continue routing architecture
- Adding git server-side branch protection (GitHub branch rules)
- Changing the hook execution model (fire-and-forget is fine for progress tracking)
- Reverting commit 351e538d (the code itself is correct, just the process was wrong)

## Update System

The SOUL.md change (Fix 4) will propagate via the update system. No new dependencies or scripts.

## Agent Integration

No agent integration required — these are internal enforcement changes to hooks and config.

## Documentation

- [ ] Update `docs/features/sdlc-enforcement.md` to document the new unconditional main-branch guard
- [ ] Ensure `config/SOUL.md` changes are reflected in any docs that reference git autonomy

## Success Criteria

- [ ] Attempting to `git commit` a `.py` file on `main` is blocked with a clear error message
- [ ] Stop hook catches code modifications on `main` even without `sdlc_state.json`
- [ ] Hook state tracking failures are logged to stderr
- [ ] SOUL.md and CLAUDE.md agree on main branch policy
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (guardrails)**
  - Name: guardrail-builder
  - Role: Apply all 5 fixes across hooks, SOUL.md, and summarizer
  - Agent Type: builder
  - Resume: true

- **Validator (enforcement)**
  - Name: enforcement-validator
  - Role: Verify commit blocking and stop hook work correctly
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply all 5 guardrail fixes
- **Task ID**: build-guardrails
- **Depends On**: none
- **Assigned To**: guardrail-builder
- **Agent Type**: builder
- **Parallel**: false
- Fix 1: `validate_commit_message.py` — block code commits on main unconditionally
- Fix 2: `validate_sdlc_on_stop.py` — git diff fallback for missing state file
- Fix 3: `post_tool_use.py` — log state save failures to stderr
- Fix 4: `config/SOUL.md` — align git autonomy with SDLC requirements
- Fix 5: `bridge/summarizer.py` — add coaching note about `/sdlc` for implementation

### 2. Validate enforcement
- **Task ID**: validate-enforcement
- **Depends On**: build-guardrails
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Simulate `git commit` of a .py file on main — verify it's blocked
- Verify stop hook detects code on main without state file
- Verify stderr logging works for hook failures
- Check SOUL.md no longer mentions "ANY branch including main"

### N. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-enforcement
- **Assigned To**: enforcement-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `echo '{"tool_name":"Bash","tool_input":{"command":"git commit -m test"},"session_id":"test"}' | python .claude/hooks/sdlc/validate_commit_message.py` — verify blocking behavior on main
- `grep "feature branches" config/SOUL.md` — verify policy alignment
- `grep "HOOK WARNING" .claude/hooks/post_tool_use.py` — verify error logging
- `grep "git diff" .claude/hooks/validators/validate_sdlc_on_stop.py` — verify fallback check
- `pytest tests/ -q --ignore=tests/e2e` — all tests pass
