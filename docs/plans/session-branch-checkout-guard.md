---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-05-05
tracking: https://github.com/tomcounsell/ai/issues/1288
last_comment_id:
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

### Risk 2: Symlink resolution breaks the suffix check
**Impact:** Hook fails to detect a violation, or false-positive blocks a legitimate worktree, when the user's repo is mounted via symlinks (e.g., iCloud sync, dev container bind mount).
**Mitigation:** Add a symlink-resolved path test case. If `git rev-parse --show-toplevel` returns a path that doesn't match the expected suffix due to symlinks, fall back to comparing the realpath of both sides. If realpaths match the expected suffix, allow the commit.

### Risk 3: Hook is bypassed by `--no-verify`
**Impact:** Determined user (or AI agent) can commit through the block.
**Mitigation:** Acceptable. `--no-verify` is an explicit override and the user takes responsibility. The hook's job is to make the easy path correct, not to enforce policy against active circumvention. CLAUDE.md already discourages `--no-verify` for non-WIP commits.

## Race Conditions

No race conditions identified. The pre-commit hook is invoked synchronously by `git` in a single subprocess; it reads `git symbolic-ref` and `git rev-parse --show-toplevel`, applies a string predicate, and exits. There is no shared mutable state, no concurrent invocation path (git serializes hook execution per repo lock), and no cross-process data flow. The two `git` reads are trivially consistent: HEAD and `--show-toplevel` cannot change mid-hook, because git holds the repo lock for the duration of the commit operation that triggered the hook.

## No-Gos (Out of Scope)

- **`post-checkout` warning hook.** See Rabbit Holes.
- **`pre-push` enforcement.** See Rabbit Holes.
- **Centralizing `WORKTREES_DIR` between bash hook and Python.** The drift cost is one line if the constant ever changes; not worth a `python -c` invocation per commit.
- **Migrating manually-created worktrees outside `.worktrees/`.** Out of scope. The hook will block commits from non-canonical worktrees on session branches; that's correct.
- **Auto-creating the worktree when the violation is detected.** Out of scope. The hook surfaces the problem; the user (or agent) chooses how to fix it.
- **Updating `/setup` to verify `core.hooksPath`.** Already done — `/setup` configures `core.hooksPath = .githooks` on new machines. No additional work needed there.

## Update System

No update-system changes required for the **mechanism** — `core.hooksPath = .githooks` is already set by `/setup` on every machine, and the new phase ships in-tree under `.githooks/pre-commit`, so it propagates via `git pull` like any other code change.

One small note for awareness in `/update`: machines that have not run `git pull` since this lands will not have the guard. There is no migration to perform, but if a machine is exhibiting the bypass behavior, the first remediation step is "ensure your repo is up to date." Add a one-line note to the `/update` skill's troubleshooting section if one exists; otherwise no action.

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
- Insert new Phase 0.5 ahead of the lint phase (before line 27 "Phase 1: Auto-fix lint"), with a one-line header comment "── Phase 0.5: Session-branch worktree guard (#1288) ───────────────────".
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

## Critique Results

<!-- Populated by /do-plan-critique. -->

---

## Open Questions

1. **Issue-number in error message vs. permanent doc link.** I drafted the error to mention `#1288` so a future grep finds the origin. Alternative: link to the permanent path `.githooks/pre-commit Phase 0.5` or to `docs/features/session-isolation.md`. Issue numbers are stable and searchable, but the doc link is more durable if the issue gets archived or renumbered. Preference?
2. **Symlink fallback aggressiveness.** Risk 2 proposes "if suffix check fails due to symlink, retry with realpath." Alternative: only do the realpath compare unconditionally (one extra `realpath` call per commit on a session branch, ~1ms). The unconditional version is simpler and removes an edge-case branch. Acceptable extra latency, or keep the conditional fallback?
3. **Should the doc subsection live in `session-isolation.md` or a new `git-hooks.md`?** I chose `session-isolation.md` because the rule is part of the isolation contract. But there is no top-level doc inventorying the git hooks installed under `.githooks/`. If we anticipate more hook phases, a dedicated `docs/features/git-hooks.md` might be better — and would let `session-isolation.md` link to it instead of duplicating. No strong preference on my end; flag if you want the standalone doc.
