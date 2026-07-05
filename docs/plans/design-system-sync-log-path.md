---
status: docs_complete
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1901
last_comment_id:
---

# Anchor design-system-sync hook log path to repo root

## Problem

The `skills-audit` reflection (rule 19, husk directories) fires a FAIL when a
directory named `logs/` — with no `SKILL.md`, containing only
`validate_design_system_sync.jsonl` — appears inside a skills root
(`.claude/skills-global/` or `.claude/skills/`). The finding recurred on two
consecutive audit runs, auto-filing issue #1901.

**Current behavior:**
The PreToolUse hook `.claude/hooks/validators/validate_design_system_sync.py:53`
defines `_LOG_PATH = Path("logs/validate_design_system_sync.jsonl")` — a
**cwd-relative** path. `_log()` (lines 56-63) calls
`_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)` then appends. When a Claude
Code session's cwd is a skills root (or any directory other than the repo root),
the hook silently creates a stray `logs/` directory *there* holding only that
jsonl file. `rule_19_husk_directories`
(`.claude/skills-global/do-skills-audit/scripts/audit_skills.py:625-652`) scans
each skills root and flags every subdir lacking `SKILL.md` as a husk — so the
stray `logs/` dir trips the audit. The husk keeps reappearing because the write
is cwd-relative; deleting it treats the symptom, not the cause.

**Desired outcome:**
The hook always writes to `<repo_root>/logs/validate_design_system_sync.jsonl`
regardless of process cwd. No stray `logs/` husk is ever created inside a skills
root (or any other directory), so rule 19 stays green permanently.

## Freshness Check

**Baseline commit:** `d9fa955751d0797cbe67c1538e8ecd4712ab7a21`
**Issue filed at:** 2026-07-05T04:47:23Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/hooks/validators/validate_design_system_sync.py:53` — issue claims a
  cwd-relative `_LOG_PATH = Path("logs/validate_design_system_sync.jsonl")` —
  **still holds** (read at baseline HEAD; line 53 matches verbatim).
- `.claude/skills-global/do-skills-audit/scripts/audit_skills.py:625-652` —
  `rule_19_husk_directories` scans skills roots and flags SKILL.md-less subdirs —
  **still holds**.

**Cited sibling issues/PRs re-checked:** None cited in the issue.

**Commits on main since issue was filed (touching referenced files):**
`git log --since=2026-07-05T04:47:23Z -- validate_design_system_sync.py` returned
nothing — the hook is untouched since filing.

**Active plans in `docs/plans/` overlapping this area:** None.

**Notes:** The stray `logs/` husk is currently absent from the skills roots on
`main` (a prior cleanup removed it), but two stray copies persist under
`.claude/worktrees/agent-*/logs/`, confirming the cwd-relative write keeps
recurring. The root cause is unfixed. The repo-root `logs/` directory is already
gitignored (`git check-ignore logs/validate_design_system_sync.jsonl` matches),
so anchoring the log there aligns with existing convention and produces no new
tracked artifacts.

## Prior Art

No prior issues or merged PRs found related to this work
(`gh pr list --state merged --search "design_system_sync log path cwd"` returned
empty). The hook and its log path were introduced by the design-system-sync
feature and have not been patched for the cwd bug before.

## Research

No relevant external findings — proceeding with codebase context and training
data. This is a purely internal one-file path-anchoring fix using only the
Python standard library (`pathlib`).

## Data Flow

1. **Entry point**: A Claude Code session runs a Bash `git add`/`git commit`
   command; the PreToolUse hook `validate_design_system_sync.py` fires with the
   process cwd set to wherever the session is working.
2. **`_log()`**: On every invocation the hook appends a JSON line to `_LOG_PATH`,
   first `mkdir`-ing the parent. Today `_LOG_PATH` is cwd-relative, so the parent
   `logs/` dir is created under the current cwd.
3. **Husk creation**: When cwd is a skills root, `logs/` lands inside it with no
   `SKILL.md`.
4. **Output (bug)**: The nightly `skills-audit` reflection scans skills roots,
   `rule_19_husk_directories` flags the `logs/` husk, and after two consecutive
   FAILs auto-files an issue.

After the fix, step 2 resolves `_LOG_PATH` from the hook file's own location
(`Path(__file__).resolve().parents[3] / "logs" / "..."`), so the write always
lands at the repo-root `logs/` dir and steps 3-4 never occur.

## Architectural Impact

- **New dependencies**: None (stdlib `pathlib` only).
- **Interface changes**: None. `_LOG_PATH` is a module-private constant; its value
  changes from a relative to an absolute path, its type (`Path`) and use are
  unchanged.
- **Coupling**: Unchanged. The hook already knows its own location implicitly;
  this makes that dependency explicit and correct.
- **Data ownership**: Unchanged — the same JSONL log, now written to one stable
  location instead of scattered per-cwd copies.
- **Reversibility**: Trivial — a one-line revert.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (root cause and fix are fully specified)
- Review rounds: 1 (standard SDLC review)

## Prerequisites

No prerequisites — this work has no external dependencies. It edits one hook file
using only the Python standard library.

## Solution

### Key Elements

- **Repo-root-anchored log path**: Derive the repo root from the hook file's own
  location and build the log path from it, so the write target is cwd-independent.

### Flow

Hook fires (any cwd) → `_LOG_PATH` resolves to `<repo_root>/logs/validate_design_system_sync.jsonl` → `_log()` mkdir+appends there → no stray `logs/` husk anywhere.

### Technical Approach

- Replace line 53
  `_LOG_PATH = Path("logs/validate_design_system_sync.jsonl")`
  with a repo-root-anchored path:
  ```python
  _REPO_ROOT = Path(__file__).resolve().parents[3]
  _LOG_PATH = _REPO_ROOT / "logs" / "validate_design_system_sync.jsonl"
  ```
  The hook lives at `<repo_root>/.claude/hooks/validators/validate_design_system_sync.py`,
  so `parents[0]=validators`, `parents[1]=hooks`, `parents[2]=.claude`,
  `parents[3]=<repo_root>`. Derived from `__file__`, not cwd and not git — the hook
  is invoked from arbitrary working directories and must not depend on either.
- `_log()` already `mkdir(parents=True, exist_ok=True)`s the parent and wraps
  everything in a bare `except Exception: pass`, so it stays fail-silent; only the
  target path changes.
- **Sibling validator confirmed clean**: `validate_design_system_readonly.py`
  performs no file logging (no `_LOG_PATH`, no `_log()`), so it does not share the
  bug and needs no change. Scope stays single-file.
- **Secondary, minor cleanup** (not a standing utility): remove any pre-existing
  stray `logs/` husk directories left inside the skills roots by the old behavior.
  On the current baseline the skills roots are already clean; if a husk reappears
  before this ships, `git rm -r`/`rm -rf` the specific `logs/` dir. This is a
  one-shot manual step, deliberately not automated (prevention is the fix).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_log()` (lines 56-63) wraps its body in `except Exception: pass` by design
  (logging must never break the hook). This is intentional and stays. The existing
  tests already assert the observable behavior — that a line *is* written on
  success (`test_jsonl_log_records_each_invocation`,
  `test_jsonl_log_captures_bypass`) — which covers the non-exception path. No new
  assertion on the swallow branch is warranted; forcing the log write to raise
  would require monkeypatching `Path.open` in a subprocess-run hook and provides no
  signal beyond "the hook did not crash", which the exit-code assertions already
  cover.

### Empty/Invalid Input Handling
- [ ] Empty/whitespace stdin is already handled by the hook (`if not raw.strip():
  return 0`) and is out of scope for this path change. No new function is
  introduced, so no new empty-input surface is added.

### Error State Rendering
- [ ] No user-visible output changes. The hook's `decision: block` rendering path
  is untouched by this fix.

## Test Impact

- [ ] `tests/unit/hooks/test_validate_design_system_sync.py::test_jsonl_log_records_each_invocation`
  — UPDATE (no code change expected to pass): it already reads
  `REPO_ROOT / "logs/validate_design_system_sync.jsonl"` and runs the hook with
  `cwd=str(REPO_ROOT)`, so it keeps passing after the fix. Verify it still passes
  unchanged; no edit needed unless it regresses.
- [ ] `tests/unit/hooks/test_validate_design_system_sync.py::test_jsonl_log_captures_bypass`
  — same as above: reads the repo-root log path already; verify green, no edit.
- [ ] `tests/unit/hooks/test_validate_design_system_sync.py` — ADD one regression
  test: run the hook from a **non-repo-root cwd** (a `tmp_path`) and assert (a) the
  log line lands at `REPO_ROOT / "logs/validate_design_system_sync.jsonl"`, and
  (b) NO `logs/` directory is created under that temporary cwd. This is the test
  that locks in the prevention and would fail against the old cwd-relative code.

## Rabbit Holes

- Do not build a standing cleanup utility, cron sweep, or reflection to delete
  stray `logs/` husks. Prevention (anchoring the path) is the fix; a cleanup script
  "that should never need to run" is a smell.
- Do not refactor the sibling `validate_design_system_readonly.py` — it has no
  logging and no bug. Leave it alone.
- Do not touch `rule_19_husk_directories` in the audit script. The rule is correct;
  the husk should not exist in the first place.
- Do not clean up the `.claude/worktrees/agent-*/logs/` copies as part of this
  plan — worktrees are ephemeral and are not scanned by rule 19 (it scans only the
  two skills roots). Chasing them is out of scope.

## Risks

### Risk 1: Wrong `parents[]` index anchors to the wrong directory
**Impact:** Log writes to the wrong path; if it lands back inside `.claude/` a new
husk could appear, or logging silently no-ops.
**Mitigation:** The regression test asserts the concrete resolved path equals
`REPO_ROOT / "logs/validate_design_system_sync.jsonl"`, catching an off-by-one in
the `parents[]` index. The existing two log tests also exercise the real path.

### Risk 2: Hook is copied/hardlinked into worktrees at a different depth
**Impact:** In a git worktree the hook file still lives at
`<worktree_root>/.claude/hooks/validators/...`, so `parents[3]` resolves to the
worktree root — which is the correct, intended behavior (log stays inside that
checkout's gitignored `logs/`, never inside a skills root).
**Mitigation:** None needed — `parents[3]` is correct for both the main checkout
and any worktree because the relative depth of the hook within `.claude/` is
invariant.

## Race Conditions

No race conditions identified. The hook runs synchronously per Bash tool call and
only appends to a log file; concurrent hook invocations across sessions already
share append-mode writes today, and this change does not alter that (it only
changes the target directory to a single stable location).

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Bulk deletion of `logs/` directories across the filesystem or
  under `.claude/worktrees/` — the fix prevents new husks; existing ephemeral
  worktree copies are harmless and not scanned by rule 19. A blanket recursive
  delete risks removing legitimate log data.

Nothing else deferred — the single-file prevention fix plus its regression test
is the entirety of the work.

## Update System

No update system changes required — this is a purely internal one-line change to a
repo-local hook file. The hook ships with the repo checkout; `scripts/update/`
propagates it as part of the normal git pull. No new dependencies, config files, or
migrations.

## Agent Integration

No agent integration required — this is a hook-internal change. The hook is invoked
by the Claude Code PreToolUse machinery, not by the bridge or any MCP tool surface.
No CLI entry point, `.mcp.json` entry, or bridge import is involved.

## Documentation

No documentation changes needed. The hook's module docstring (lines 16-19) already
describes that it "appends one JSON line to `logs/validate_design_system_sync.jsonl`";
that statement stays accurate after the fix (the path is still repo-root `logs/`,
now made robust). There is no `docs/features/` page dedicated to this hook, and the
fix changes no user-facing or agent-facing behavior. If the builder wishes, a
one-line inline comment on the anchored `_REPO_ROOT` explaining "cwd-independent so
no stray husk is created" is sufficient and covered under Inline Documentation.

### Inline Documentation
- [ ] Add a brief comment above `_REPO_ROOT`/`_LOG_PATH` explaining the anchor
  prevents cwd-relative husk directories (rule 19 husks).

## Success Criteria

- [x] `_LOG_PATH` in `validate_design_system_sync.py` is anchored to the repo root
  via `Path(__file__).resolve().parents[3]` (no cwd-relative `Path("logs/...")`).
- [x] Running the hook from a non-repo-root cwd creates NO `logs/` directory under
  that cwd; the log line lands at `<repo_root>/logs/validate_design_system_sync.jsonl`.
- [x] New regression test in `test_validate_design_system_sync.py` covers the
  non-repo-root-cwd case and passes.
- [x] Existing `test_jsonl_log_records_each_invocation` and
  `test_jsonl_log_captures_bypass` still pass unchanged.
- [x] `skills-audit` rule 19 reports no `logs` husk finding
  (`python .claude/skills-global/do-skills-audit/scripts/audit_skills.py` clean for rule 19).
- [x] Tests pass (`/do-test`)
- [ ] Documentation reviewed (`/do-docs`) — inline comment added; no feature-doc change.

## Team Orchestration

### Team Members

- **Builder (log-path-anchor)**
  - Name: log-path-builder
  - Role: Anchor `_LOG_PATH` to repo root and add the regression test
  - Agent Type: builder
  - Resume: true

- **Validator (log-path-anchor)**
  - Name: log-path-validator
  - Role: Verify the anchor is correct, the regression test fails against old code
    and passes against new, and rule 19 is clean
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Anchor the log path and add regression test
- **Task ID**: build-log-path-anchor
- **Depends On**: none
- **Validates**: `tests/unit/hooks/test_validate_design_system_sync.py`
- **Assigned To**: log-path-builder
- **Agent Type**: builder
- **Parallel**: false
- Edit `.claude/hooks/validators/validate_design_system_sync.py` line 53: replace
  `_LOG_PATH = Path("logs/validate_design_system_sync.jsonl")` with a
  `Path(__file__).resolve().parents[3]`-anchored path (define `_REPO_ROOT` and
  build `_LOG_PATH` from it). Add a one-line comment noting it prevents cwd-relative
  rule-19 husks.
- Add a regression test to `tests/unit/hooks/test_validate_design_system_sync.py`
  that runs the hook with `cwd=<tmp_path>` (not `REPO_ROOT`) and asserts (a) the log
  line lands at `REPO_ROOT / "logs/validate_design_system_sync.jsonl"` and (b) no
  `logs/` dir is created under `tmp_path`.
- Confirm the sibling `validate_design_system_readonly.py` needs no change (it has
  no logging).
- Run `python -m ruff format .` on touched files.

### 2. Validate the fix
- **Task ID**: validate-log-path-anchor
- **Depends On**: build-log-path-anchor
- **Assigned To**: log-path-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/hooks/test_validate_design_system_sync.py -q` — all pass.
- Verify the new regression test FAILS if the anchor is reverted to the cwd-relative
  path (red-state proof), then passes with the fix in place.
- Run the skills audit and confirm rule 19 reports no `logs` husk.
- Report pass/fail.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hook tests pass | `pytest tests/unit/hooks/test_validate_design_system_sync.py -q` | exit code 0 |
| Log path is repo-root-anchored | `grep -c 'parents\[3\]' .claude/hooks/validators/validate_design_system_sync.py` | output contains 1 |
| No cwd-relative log path remains | `grep -c 'Path("logs/validate_design_system_sync.jsonl")' .claude/hooks/validators/validate_design_system_sync.py` | match count == 0 |
| Format clean | `python -m ruff format --check .claude/hooks/validators/validate_design_system_sync.py` | exit code 0 |
| No logs husk in skills roots | `find .claude/skills-global .claude/skills -maxdepth 2 -type d -name logs` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None. The root cause, fix, sibling verification, and test strategy are fully
specified. This is a single-line prevention fix plus one regression test.
