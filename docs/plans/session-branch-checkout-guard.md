---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-05-05
tracking: https://github.com/tomcounsell/ai/issues/1288
last_comment_id: 4376959048
---

# Session-branch checkout guard (pre-commit hook)

## Problem

Nothing at the git layer prevents a `session/{slug}` branch from being checked out — and committed to — in the main checkout. The `/do-build` skill enforces worktree isolation when invoked, but a `git checkout session/X` issued against `/Users/valorengels/src/ai` (or any path that isn't the owning `.worktrees/{slug}/`) succeeds silently. Subsequent commits land on the session branch with no isolation guarantee, contaminating the main working tree.

**Current behavior:** A user (or a Claude Code session that bypasses `/do-build`) can run `git checkout session/X` and `git commit` from the main checkout. Git is happy; the commits push fine; the PR merges. The only signal of the violation is that `.worktrees/X/` never exists.

**Desired outcome:** A `git commit` on a `session/*` branch from outside `repo_root/.worktrees/{slug}/` fails fast with a clear actionable error pointing at the right path. Commits inside the proper worktree are unaffected. Commits on `main`, hotfix branches, or any other branch are unaffected.

## Freshness Check

**Baseline commit:** `2c0d43a1`
**Issue filed at:** 2026-05-05T05:13Z (≈30 minutes before plan start)
**Disposition:** Unchanged

The issue was filed inside the last hour. No commits have landed on `.githooks/pre-commit`, `agent/worktree_manager.py`, or `docs/features/session-isolation.md` since filing. The recon — both the inherited evidence from #1287 and the local git-hook inventory captured in #1288's `## Recon Summary` — still holds verbatim.

**Active plans in `docs/plans/` overlapping this area:** none. The most recent plan touching session isolation is `agent_session_field_cleanup.md` (unrelated — model-field rename) and the just-merged `pr-shape-aware-merge-gates.md` (unrelated — PR classification gates). No coordination conflict.

## Prior Art

- **#887** (closed, PR #888) — *Session isolation bypass: PM sessions created via `valor-session create` operate in main checkout instead of a worktree.* Three-layer fix: executor guard, `--slug` flag, PM persona prompt. **All worker-side.** This issue is the symmetric git-side complement: the same invariant ("`session/*` lives only in `.worktrees/{slug}/`") but enforced at the `git commit` boundary instead of the worker boundary.
- **#1287** (closed, this plan's parent) — *Investigation that initially blamed the AgentSession executor.* Recon refuted that premise; the actual mechanism is the git-layer bypass we're fixing here. `dcab49b1` (executor guard tightening) shipped from that investigation and is unrelated to this work.
- **#267** (closed) — *git checkout fails when branch has existing worktree (poor error UX).* The opposite problem: git refuses checkout when a worktree already owns the branch. Confirms git's native behavior — when a worktree exists for `session/X`, you *cannot* check it out elsewhere; the violation we're catching is when *no* worktree exists, so git happily creates a new HEAD on the session branch in main checkout.
- **#1158** (closed) — *Child sessions lose project scope.* Different surface (working_dir resolution); not load-bearing here.

No PRs found that propose git-hook-based enforcement of branch/worktree pairing. This is greenfield at the hook layer.

## Why Previous Fixes Failed

The #887 fix is not a "previous failed fix" — it correctly addressed the worker path. The bypass we're catching here is **outside** the worker entirely, so #887's three layers were never on the call stack. There is no failure pattern to learn from; this is simply an unaddressed surface.

## Architectural Impact

- **New dependencies:** None. Bash + standard `git` commands only.
- **Interface changes:** None. The hook is invoked transparently by git on `git commit`.
- **Coupling:** Adds a soft coupling between `.githooks/pre-commit` and the `WORKTREES_DIR` constant in `agent/worktree_manager.py`. We can either hardcode `.worktrees` (matches current value, drifts if the constant changes) or read the constant via `python -c "from agent.worktree_manager import WORKTREES_DIR; print(WORKTREES_DIR)"` (zero duplication, slower hook). **Decision below in Technical Approach.**
- **Data ownership:** None.
- **Reversibility:** Trivial. Revert the diff to the hook + delete the test + delete the doc subsection.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope is concrete and bounded)
- Review rounds: 1 (standard PR review)

The work is one bash phase (~30 lines), one test file (~50 lines of pytest invoking the hook via subprocess), and one doc subsection (~20 lines). The bottleneck is not coding; it's getting the hook predicate right on the first attempt so we don't ship false positives that block legitimate commits.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `core.hooksPath = .githooks` | `git config core.hooksPath` returns `.githooks` | Hook is actually installed |
| Existing `.githooks/pre-commit` is intact | `test -x .githooks/pre-commit` | We're appending a phase to an existing hook |

Run all checks: `python scripts/check_prerequisites.py docs/plans/session-branch-checkout-guard.md`

## Solution

### Key Elements

- **`.githooks/pre-commit` Phase 0.5 (Session-branch worktree guard)** — added at the very top of the hook, before lint/lockfile/secret-scan phases, so a misplaced commit fails immediately without doing avoidable work.
- **`tests/unit/test_session_branch_guard.py` (new)** — invokes the hook directly via `subprocess.run` against three scenarios (main on main, main on session branch, worktree on session branch); asserts exit codes and stderr messages.
- **`docs/features/session-isolation.md` extension** — adds a new subsection under "Worktree Enforcement for Dev Sessions" titled "Git-Layer Enforcement for Manual Operations" describing the new hook phase.

### Flow

`git commit` invoked → `.githooks/pre-commit` runs → Phase 0.5 reads `git symbolic-ref --short HEAD` → if branch matches `session/*` and `git rev-parse --show-toplevel` doesn't end in `.worktrees/{slug}` → exit 1 with actionable message pointing at the worktree path → commit aborted.

In the legitimate case (commit inside the right worktree, or commit on any non-session branch, or detached HEAD), Phase 0.5 is silent and the hook proceeds to existing phases.

### Technical Approach

**Hook predicate (bash):**

```bash
BRANCH=$(git symbolic-ref --short HEAD 2>/dev/null || echo "")
if [[ "$BRANCH" == session/* ]]; then
    SLUG="${BRANCH#session/}"
    TOPLEVEL=$(git rev-parse --show-toplevel)
    EXPECTED_SUFFIX=".worktrees/${SLUG}"
    if [[ "$TOPLEVEL" != *"$EXPECTED_SUFFIX" ]]; then
        # Block with actionable message
        exit 1
    fi
fi
```

**`WORKTREES_DIR` coupling:** hardcode the literal `.worktrees` in the bash hook. The constant in `agent/worktree_manager.py:16` has not changed since introduction (#887, April 2026) and changing it is a much larger migration that would touch dozens of call sites — the bash duplication is acceptable. If the constant ever does change, a one-line edit to the hook is part of that migration.

**Detached-HEAD safety:** `git symbolic-ref --short HEAD` exits non-zero in detached-HEAD state. The `2>/dev/null || echo ""` defaults `$BRANCH` to empty, the `session/*` glob match fails, and the phase becomes a no-op. No false positives on rebase, cherry-pick, or bisect operations.

**Test strategy:** Invoke `.githooks/pre-commit` directly via `subprocess.run` in three temp-repo fixtures:

1. `tmp_repo / .git / `... `HEAD` set to `refs/heads/main`, commit attempted → should pass through Phase 0.5.
2. `tmp_repo` on `session/test-feature`, no worktree under `tmp_repo/.worktrees/test-feature/` → should exit 1, stderr mentions `.worktrees/test-feature`.
3. `tmp_repo / .worktrees / test-feature` set up as a real worktree on `session/test-feature`, hook invoked from inside it → should pass through Phase 0.5.

Tests subprocess the hook rather than refactoring the predicate to Python, because the deployment artifact *is* the bash hook — testing the bash directly is the closest fidelity test. Hook execution is ~10ms; running three tests adds negligible CI time.

**Error message:**

```
COMMIT BLOCKED: branch session/{slug} must be committed from inside .worktrees/{slug}/.
Current working tree: {TOPLEVEL}
Expected:             {repo_root}/.worktrees/{slug}/

This is the #1288 guard. To proceed correctly:
  cd <repo_root>/.worktrees/{slug}
  git commit ...

If the worktree doesn't exist, create it:
  python -c "from agent.worktree_manager import get_or_create_worktree; from pathlib import Path; print(get_or_create_worktree(Path('.'), '{slug}'))"
```

The message names the issue number so a future grep finds it; provides the recovery `cd`; and offers the worktree-creation one-liner for the case where the user landed on a session branch without provisioning.

## Failure Path Test Strategy

### Exception Handling Coverage
- The hook is bash, not Python. There are no `except Exception: pass` blocks in scope. The new test file has no exception handlers either — it asserts on subprocess return codes, not on caught exceptions.
- "No exception handlers in scope."

### Empty/Invalid Input Handling
- Detached HEAD (no symbolic ref) — covered above; defaults to empty string, predicate skips.
- Branch named exactly `session/` (empty slug) — pathological case. The predicate would compute `SLUG=""` and `EXPECTED_SUFFIX=".worktrees/"`, which any `--show-toplevel` ending in `.worktrees/` would (vacuously) satisfy. Add an explicit guard: if `$SLUG` is empty, also block with a message about the invalid branch name.
- Worktree mounted at a symlinked path — `git rev-parse --show-toplevel` follows the worktree's recorded path, which may resolve through a symlink. Test with a symlinked tmp-repo to confirm the suffix check still holds. If symlinks break it, switch to comparing against the resolved path.

### Error State Rendering
- The error message must be visible to humans, not swallowed by `set -e`. Hook prints to stderr (visible in `git commit` output) and exits 1 (visible as `husky-style` block).
- Verify in test: assert `result.stderr` contains both the issue number and the expected path.

## Test Impact

- [ ] `tests/unit/test_session_branch_guard.py` — CREATE (new file). Three test cases: main-on-main passes, main-on-session blocks, worktree-on-session passes. Plus edge cases (detached HEAD, empty-slug pathological).
- [ ] `tests/unit/test_session_isolation_bypass.py` — UPDATE: this file already covers the worker-side enforcement (#887 fix). Add a comment at the top noting that the git-side complement now lives in `test_session_branch_guard.py`. No code changes; cross-reference only so future readers don't think one file covers both surfaces.

No other existing tests affected — the hook is purely additive at the start of `.githooks/pre-commit` and skipping fast paths when the predicate doesn't match.

## Rabbit Holes

- **Building a `post-checkout` warning hook.** Tempting because "warn early" feels right, but post-checkout fires *after* the user is already on the wrong branch and can't block. Pre-commit alone catches the only case that does damage (writing commits). Defer post-checkout until we have evidence pre-commit alone misses something.
- **Implementing this as a Python validator under `.claude/hooks/validators/`.** That directory is for Claude Code session hooks, not git hooks. They're entirely different surfaces — Claude Code hooks fire on UserPromptSubmit / PostToolUse / Stop, not on `git commit`. Mixing them would confuse readers and gain nothing.
- **Refactoring the existing `.githooks/pre-commit` to source phase scripts from a `phases/` directory.** Tempting cleanup, but out of scope for a one-phase addition. Keeps the diff focused and reviewable.
- **Adding the same check to `pre-push`.** A `pre-push` check would catch the case where a violator commits with `--no-verify` then pushes. But `--no-verify` is an explicit user override and we should respect it. Defer until evidence shows `--no-verify` is the common bypass.

## Risks

### Risk 1: False positive blocks a legitimate commit
**Impact:** A commit that should succeed fails with a confusing message; user must override with `--no-verify` or debug the hook.
**Mitigation:** The predicate is two clear conditions joined by AND: branch starts with `session/` AND toplevel doesn't end with `.worktrees/{slug}`. Both are easily verified by hand. The test suite covers the three real scenarios. Detached HEAD is explicitly handled. The pathological empty-slug case is explicitly handled. We accept that a worktree mounted at a non-canonical path (e.g., `/tmp/manual-worktree`) will trigger the block — that's correct behavior, not a bug.

### Risk 2: Symlink resolution (verified — already handled by `git`)
**Impact:** Theoretical concern that `git rev-parse --show-toplevel` might return a path differing from `pwd` when symlinks are involved.
**Mitigation:** Verified empirically during planning. From a symlinked CWD (`/tmp/symlink_test/linked_repo` → `real_repo`), `git rev-parse --show-toplevel` returns the canonical path `/private/tmp/symlink_test/real_repo` regardless of how the user `cd`'d in. The suffix predicate works without a `realpath` fallback. The remaining theoretical edge — `.worktrees/` itself being a symlink — does not occur because `get_or_create_worktree` creates a real directory. No symlink-specific test case is needed.

### Risk 3: Hook is bypassed by `--no-verify` (including via the agent's Bash tool)
**Impact:** Determined user — or an AI agent calling `git commit --no-verify` through its Bash tool — can commit through the block.
**Mitigation (rev1, concern #2):** Acceptable for this PR. `--no-verify` is an explicit override and the caller takes responsibility. CLAUDE.md already discourages it for non-WIP commits, and the bash hook's job is to make the easy path correct, not to enforce policy against active circumvention.

If `--no-verify` becomes the dominant bypass vector after this hook ships, the layered fix is a `.claude/hooks/validators/` PreToolUse hook on the Bash tool that inspects the command for `git commit --no-verify` on `session/*` branches and refuses. **Not built here** — would expand scope from "git-layer enforcement" to "git-layer + agent-layer enforcement," and we have no current evidence the agent uses `--no-verify` against this guard. File a follow-up bug if the bypass is observed in practice. Tracking note: title would be something like "PreToolUse validator: refuse `git commit --no-verify` on `session/*` branches."

## Race Conditions

No race conditions identified. The pre-commit hook is invoked synchronously by `git` in a single subprocess; it reads `git symbolic-ref` and `git rev-parse --show-toplevel`, applies a string predicate, and exits. There is no shared mutable state, no concurrent invocation path (git serializes hook execution per repo lock), and no cross-process data flow. The two `git` reads are trivially consistent: HEAD and `--show-toplevel` cannot change mid-hook, because git holds the repo lock for the duration of the commit operation that triggered the hook.

## No-Gos (Out of Scope)

- **`post-checkout` warning hook.** See Rabbit Holes.
- **`pre-push` enforcement.** See Rabbit Holes.
- **Centralizing `WORKTREES_DIR` between bash hook and Python.** The drift cost is one line if the constant ever changes; not worth a `python -c` invocation per commit.
- **Migrating manually-created worktrees outside `.worktrees/`.** Out of scope. The hook will block commits from non-canonical worktrees on session branches; that's correct.
- **Auto-creating the worktree when the violation is detected.** Out of scope. The hook surfaces the problem; the user (or agent) chooses how to fix it.
- **Adding `core.hooksPath` provisioning to `/setup`.** Out of scope for this plan. `/update` already does it (`scripts/update/git.py:166`); migrating that responsibility to `/setup` is a separate concern about deployment ordering, not about the hook itself.

## Update System

**Implementation Note (rev1, concern #1):** `core.hooksPath = .githooks` is configured by **`/update`** (specifically `scripts/update/git.py:166`), **not** by `/setup`. Earlier draft of this plan claimed the opposite — corrected here so the builder doesn't ship a deployment story based on the wrong skill.

Practical implications for this plan:

- The new pre-commit Phase 0.5 ships in-tree under `.githooks/pre-commit` and propagates via `git pull` like any other code change. No update-script edit is required on the *mechanism* side.
- **Machines that have run `/update` at any point** already have `core.hooksPath` set; the new phase activates on their next `git pull` automatically. Most active machines are in this category.
- **Machines that have only run `/setup`** have not had `core.hooksPath` configured and will silently skip the new hook (and every other phase in `.githooks/pre-commit`). Remediation is a one-liner: `git config core.hooksPath .githooks`. Either run `/update` (which does this and other provisioning) or set the config manually.
- No migration code added in this PR. If we want defensive-set in `/setup`, that's a separate plan — out of scope here.

## Agent Integration

No agent integration required. The pre-commit hook is invoked by git itself, not by the agent. The agent will encounter this hook organically when it runs `git commit` from the wrong CWD on a session branch — and that's exactly when we want it to fail. No MCP tool, no `tools/` Python wrapper, no bridge change.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md` — add a new subsection under "Worktree Enforcement for Dev Sessions" titled "Git-Layer Enforcement for Manual Operations" (~20 lines). Describe the predicate, point at `.githooks/pre-commit`, link to issue #1288, note the `--no-verify` override.
- [ ] No new entry in `docs/features/README.md` index — this extends an existing feature doc, doesn't create a new one.

### External Documentation Site
- Repo doesn't use Sphinx / Read the Docs / MkDocs. Skip.

### Inline Documentation
- [ ] One short comment block at the top of the new Phase 0.5 in `.githooks/pre-commit`, explaining what it guards and pointing at issue #1288. One line max — the predicate is self-explanatory.

## Success Criteria

- [ ] `git commit` on `session/X` from `/Users/valorengels/src/ai` (when `.worktrees/X/` does not exist) exits 1 with stderr containing `#1288` and `.worktrees/X`.
- [ ] `git commit` on `session/X` from `/Users/valorengels/src/ai/.worktrees/X/` proceeds to the existing lint/lockfile/secret-scan phases unaffected.
- [ ] `git commit` on `main` (and any non-`session/*` branch) proceeds unaffected.
- [ ] Detached HEAD (rebase, cherry-pick, bisect) does not trigger the guard.
- [ ] `tests/unit/test_session_branch_guard.py` passes (`pytest tests/unit/test_session_branch_guard.py -v`).
- [ ] Existing `.githooks/pre-commit` phases (lint, lockfile, secret-scan) continue to pass on a clean commit.
- [ ] `docs/features/session-isolation.md` extended with the new subsection.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (hook + tests + doc)**
  - Name: `hook-builder`
  - Role: Implement the Phase 0.5 bash predicate, write the test file, extend session-isolation.md.
  - Agent Type: builder
  - Resume: true

- **Validator (predicate correctness)**
  - Name: `hook-validator`
  - Role: Verify the three scenarios (main-on-main, main-on-session blocks, worktree-on-session passes) plus detached-HEAD and empty-slug edge cases.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

(Standard core tier, see template.)

## Step by Step Tasks

### 1. Implement Phase 0.5 in `.githooks/pre-commit`
- **Task ID**: build-hook
- **Depends On**: none
- **Validates**: tests/unit/test_session_branch_guard.py (create)
- **Informed By**: none — design is concrete in Technical Approach
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Read current `.githooks/pre-commit` to understand the existing structure.
- **Insert by marker, not line number (rev1, concern #4):** find the line `# ── Phase 1: Auto-fix lint on staged Python files ──────────────────────` and insert the new Phase 0.5 immediately above it. Header comment for the new phase: `# ── Phase 0.5: Session-branch worktree guard (#1288) ───────────────────`. Line numbers in the existing hook drift under unrelated edits; the marker comment is stable.
- Implement the predicate per Technical Approach: `git symbolic-ref --short HEAD 2>/dev/null || echo ""`, glob match on `session/*`, suffix check on `git rev-parse --show-toplevel`, explicit empty-slug guard, detached-HEAD safe.
- Emit the actionable error message from Technical Approach to stderr on block.

### 2. Write `tests/unit/test_session_branch_guard.py`
- **Task ID**: build-tests
- **Depends On**: none (can run in parallel with build-hook; the test file references the hook path)
- **Validates**: itself
- **Assigned To**: hook-builder
- **Agent Type**: builder
- **Parallel**: true
- Create three primary test cases using `tmp_path` fixture and `subprocess.run` to invoke `.githooks/pre-commit` directly:
  - `test_main_branch_in_main_checkout_passes`
  - `test_session_branch_in_main_checkout_blocks` (assert exit=1, stderr contains `#1288`)
  - `test_session_branch_in_owning_worktree_passes`
- Add edge case tests:
  - `test_detached_head_does_not_trigger_guard`
  - `test_session_slash_empty_slug_blocks`
- Use real `git init`, `git checkout -b`, and `git worktree add` in fixtures — no mocks. The hook is bash; testing it via Python mocks would lose fidelity.
- **Stage a non-Python plain-text file in every fixture (rev1, concern #3).** The "passes" test cases run through to subsequent pre-commit phases (Phase 1: ruff auto-fix, Phase 1.5: `uv lock --locked`, Phase 2: secret scan). In `tmp_path` fixtures with no `.venv` and no `uv` binary, those phases will fail and contaminate the test signal. To short-circuit them, stage exactly one file like `notes.txt` containing benign content (e.g., `"placeholder for hook test fixture"`):
  - Phase 1 (ruff): `STAGED_PY_FILES` is empty → block skipped.
  - Phase 1.5 (uv lock): `LOCKFILE_STAGED` is empty (no `pyproject.toml` or `uv.lock` staged) → block skipped.
  - Phase 2 (secret scan): runs on `notes.txt`, no patterns match → passes.
  Result: the passes-cases isolate Phase 0.5 cleanly and don't depend on the test machine having ruff/uv installed.

### 3. Validate predicate correctness
- **Task ID**: validate-hook
- **Depends On**: build-hook, build-tests
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_session_branch_guard.py -v` and confirm 5/5 pass.
- Manually exercise the hook in three real scenarios (main, main-on-session, worktree-on-session) and confirm exit codes match expectations.
- Confirm the existing pre-commit phases (lint, lockfile, secret-scan) still execute on legitimate commits — run `python -m ruff format` on a known dirty file in main checkout and confirm the auto-fix phase runs after Phase 0.5 passes.

### 4. Extend `docs/features/session-isolation.md`
- **Task ID**: document-hook
- **Depends On**: validate-hook
- **Assigned To**: hook-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Add a new subsection "Git-Layer Enforcement for Manual Operations" under the existing "Worktree Enforcement for Dev Sessions" section.
- Cover: what the predicate guards, where it lives (`.githooks/pre-commit` Phase 0.5), the issue reference (#1288), the `--no-verify` override semantics, and a one-line note on the relationship to the worker-side #887 fix (sibling enforcement, different surface).
- Keep the subsection ≤ 25 lines.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-hook, build-tests, validate-hook, document-hook
- **Assigned To**: hook-validator
- **Agent Type**: validator
- **Parallel**: false
- `pytest tests/unit/test_session_branch_guard.py -v` — 5/5 pass.
- `pytest tests/ -x -q` — full suite still passes (no regression in other hook-adjacent tests).
- `python -m ruff check . && python -m ruff format --check .` — clean.
- Verify `docs/features/session-isolation.md` builds-as-markdown (no broken links, the new subsection renders).
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hook tests pass | `pytest tests/unit/test_session_branch_guard.py -q` | exit code 0 |
| Full unit suite still passes | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hook is executable | `test -x .githooks/pre-commit` | exit code 0 |
| Hook contains #1288 marker | `grep -q '#1288' .githooks/pre-commit` | exit code 0 |
| Doc subsection present | `grep -q 'Git-Layer Enforcement for Manual Operations' docs/features/session-isolation.md` | exit code 0 |

## Resolved Decisions

These three calls were resolved at plan-finalization time. Recording them inline so reviewers and the builder don't relitigate:

1. **Error-message reference: issue number only (`#1288`).** Stable, grep-friendly, survives issue closure, doesn't rot under doc reorganizations. The error already tells the user what to do (`cd …`, `python -c …`); the breadcrumb just answers "where did this come from?" — a number suffices.
2. **Symlink handling: no fallback needed.** Empirical test confirmed `git rev-parse --show-toplevel` already canonicalizes through symlinks. See Risk 2 for the verification details.
3. **Doc location: extend `docs/features/session-isolation.md`.** The doc already covers the worker-side enforcement (#887); the git-side enforcement belongs in the same file so a reader learning the isolation invariant finds both halves together. No standalone `docs/features/git-hooks.md` until a second hook phase justifies the inventory split.

## Critique Results

War-room critique cycle 1 — verdict: **READY TO BUILD (with concerns)**. Issue comment: https://github.com/tomcounsell/ai/issues/1288#issuecomment-4376959048

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| concern  | Operator | Plan claimed `/setup` configures `core.hooksPath`. Verified false — configurator is `scripts/update/git.py:166`, runs via `/update`. Machines that have only run `/setup` would silently skip the hook. | rev1 edit to `## Update System` and `## No-Gos` | Builder must NOT add hooksPath provisioning to `/setup` in this PR. The fix is documentation-only: explain in plan + feature doc that `/update` is the install path. |
| concern  | Adversary | `--no-verify` is trivially typeable by the agent's Bash tool. Plan accepted as out-of-scope without a tracked follow-up. | rev1 edit to `## Risks` Risk 3 | Out of scope for this PR. If agent-side bypass becomes a real vector after the hook ships, file follow-up: PreToolUse Bash validator that refuses `git commit --no-verify` on `session/*` branches. |
| concern  | Operator | Test "passes" cases will hit existing pre-commit Phases 1/1.5/2 (ruff, uv lock, secret scan), which fail in `tmp_path` fixtures lacking `.venv`/`uv`. | rev1 edit to Step-by-step Task 2 | Stage exactly one non-Python plain-text file (e.g., `notes.txt`) per fixture. Phase 1 skips on empty `STAGED_PY_FILES`, Phase 1.5 skips on empty `LOCKFILE_STAGED`, Phase 2 sees no secrets. Hook runs on a clean substrate. |
| concern  | Archaeologist | Task 1 cited "before line 27" — actual line is 24. Drift-prone under unrelated edits. | rev1 edit to Step-by-step Task 1 | Switched to marker-based insertion: anchor on the existing `# ── Phase 1: Auto-fix lint on staged Python files ──────────────────────` comment, insert above it. Stable across hook edits. |
| nit      | Simplifier | Phase numbering "0.5" reads as a hot-fix afterthought. Could rename to "Phase 0" or rotate the existing phases. | (not addressed) | Cosmetic; left to builder discretion. Renaming all phases is a wider edit than the value justifies for one new phase. |
| nit      | Simplifier | Empty-slug guard adds a special case for a pathological branch name that shouldn't occur. | (not addressed) | Cosmetic; cheap to keep, defensive. Builder may delete if they prefer minimalism. |
| nit      | User | Error-message recovery block is verbose (4 lines + a one-liner). | (not addressed) | Cosmetic. The error fires on a violation that's already a surprise; verbose recovery is appropriate over terse. Builder may shorten if they have a stronger preference. |
