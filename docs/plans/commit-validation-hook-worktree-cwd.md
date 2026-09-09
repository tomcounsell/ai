---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-09
tracking: https://github.com/tomcounsell/ai/issues/3259
last_comment_id:
---

# Commit-Validation Hook Resolves Git State From the Invoking Worktree

## Problem

`~/.claude/hooks/sdlc/validate_commit_message_sdlc.py` is the fleet-wide guard behind popoto's "never push code directly to main" rule. It is a `PreToolUse` / `Bash` hook: it inspects the `git commit` the agent is about to run and blocks it when the target branch is `main` and the staged set contains `.py`, `.js`, or `.ts`.

It decides all of that by shelling out to git **with no `cwd` argument**. Every git call inherits the hook process's working directory instead of the directory the intercepted `git commit` would actually execute in. The hook reads its stdin payload (`sdlc_context.read_stdin()`, `:52`) and uses only `tool_name` and `tool_input.command`; the `cwd` key that payload carries is discarded.

**Current behavior:**

Three call sites answer about the wrong repository whenever the effective directory differs from the hook process's cwd — which is the normal condition in a multi-lane SDLC run, where each lane lives in its own `.worktrees/<lane>` checkout:

- `get_current_branch()` (`:38-47`, called at `:66`) — reads the *main checkout's* branch.
- `git rev-parse --show-toplevel` (`:72-76`) — feeds the `repo_name != "popoto"` allow-gate at `:78`.
- `git diff --cached --name-only` (`:85-90`) — the staged-code check that does the actual blocking at `:96-101`.

Two consequences, both observed in the popoto SDLC run of 2026-09-08:

1. **False block.** The main checkout sits on `main` with stale staged `.py` files left over from a crashed lane. Every other lane's commit is blocked, by name, citing files that lane never touched and cannot unstage from where it stands.
2. **False allow — the dangerous direction.** A lane genuinely committing code to `main` inside its worktree sails through whenever the main checkout happens to sit on a feature branch. The guard's entire reason to exist is silently defeated, and nothing reports it.

The failure mode is not that the hook errors. It is that it answers confidently about the wrong repository, which no single-checkout test can catch.

**Desired outcome:**

The hook's verdict is a function of the directory the intercepted command runs in, and of nothing ambient. Given a worktree whose branch and staged index deliberately disagree with the main checkout's, the verdict follows the worktree in both directions. A two-checkout regression test proves it.

## Freshness Check

**Baseline commit:** `d441f972eb6f49788b35355d22de7643330875f8` (`origin/main`, fetched at plan time)
**Issue filed at:** 2026-09-08T09:30:53Z
**Disposition:** Unchanged

**File:line references re-verified (all against the baseline SHA):**
- `.claude/hooks/sdlc/validate_commit_message_sdlc.py:38-47` — `get_current_branch()`, no `cwd` — still holds; call site `:66`.
- `.claude/hooks/sdlc/validate_commit_message_sdlc.py:72-76` — `git rev-parse --show-toplevel`, no `cwd` — still holds; repo-name gate at `:77-79`.
- `.claude/hooks/sdlc/validate_commit_message_sdlc.py:85-90` — `git diff --cached --name-only`, no `cwd` — still holds; block at `:96-101`.
- Payload read at `:52`, `tool_input.command` at `:59`, `cwd` never referenced anywhere in the file — confirmed by grep.
- `~/.claude/hooks/sdlc/validate_commit_message_sdlc.py` and the repo copy are the **same inode** (link count 2), so the deployed guard is byte-identical to the one this plan edits.

**Cited sibling issues/PRs re-checked:** the issue cites none. The three reference implementations found during recon (#2050/PR #2057, #2137/PR #2501, #2738/PR #2746) are all merged and stable.

**Commits on main since issue was filed (touching referenced files):** none. `git log origin/main --since=<createdAt> -- .claude/hooks/sdlc/ .claude/hooks/manifest.toml scripts/update/hardlinks.py` is empty. The hook's last change is `a419b2777` (#2503, 2026-08).

**Active plans in `docs/plans/` overlapping this area:** none touching `.claude/hooks/sdlc/`.

**Bug still reproducible:** yes, by inspection of the three call sites, and confirmed structurally — see spike-3, which found the fix is *incomplete* without a second correction the issue did not name.

## Prior Art

- **PR #2746** (merged, closes #2738) — *Resolve hook validator targets from the hook payload.* Same bug class in a different family: four `PostToolUse` validators read stdin, threw the payload away, and inferred their target from ambient state (`find_newest_plan_file`, a git-status + mtime scan). The fix deleted the guessing helper, resolved the target from the payload, and added a permanent anti-regression test asserting the guessing helper never comes back. This is the template for both the fix shape and the test shape.
- **PR #2057** (merged, closes #2050) — *PreToolUse guard blocking `uv sync` from a worktree.* Introduced `_effective_dir(command, hook_cwd)` in `validators/validate_no_uv_sync_in_worktree.py:107-122`: the payload `cwd`, overridden by a leading `cd <path>` simple command, with `_split_simple_commands` (`:70`) splitting on `&& || ; |` and newlines. This is the resolution algorithm to port.
- **PR #2501** (merged, refs #2137) — *Guardrail: forbid destructive whole-tree git ops in the shared main checkout.* Same `_effective_dir` seam plus `_git_toplevel(cwd)` (`:201-208`), which shells `git -C <cwd> rev-parse --show-toplevel` rather than setting `cwd=`, and a pure `find_violation(command, cwd, *, repo_root, in_worktree)` testability seam (`:160`). This is the precedent for both the `-C` call form and the injectable-predicate structure.
- **PR #2453 / #2503** — established the manifest-generated registration and the global-interpreter floor this hook lives under. Constrains *how* the fix may be written (see Architectural Impact), not *what* it does.

No prior attempt has been made on this specific hook, so there is no `## Why Previous Fixes Failed` section — this is a first fix, not a repeat.

## Research

No relevant external findings — proceeding with codebase context. The change is confined to this repo's own hook scripts and the stdlib `subprocess`/`shlex`/`pathlib` modules; no external library, API, or ecosystem pattern is involved.

## Spike Results

### spike-1: Does the `PreToolUse` payload actually carry the working directory?
- **Assumption**: "The hook input carries enough to determine the invoking directory" — the issue explicitly flagged that if it does not, closing *that* gap is the real work.
- **Method**: code-read
- **Finding**: **Yes.** Claude Code hook payloads carry a top-level `cwd`, and two hooks in this repo already consume it: `.claude/hooks/post_compact.py:161` (`hook_input.get("cwd") or ""`) and `validate_no_destructive_git_in_shared_checkout.py:266` (`hook_input.get("cwd", "") or ""`, threaded into `find_violation_from_hook_input`). There is no upstream gap to close.
- **Confidence**: high
- **Impact on plan**: The issue's conditional branch collapses. The fix is a local port, and the fallback to the hook process's cwd survives only as an explicit, documented last resort for an empty payload key.

### spike-2: Can the fix reuse the existing `_effective_dir` helper by import?
- **Assumption**: "The `_effective_dir` implementation in `validators/` can be shared rather than duplicated."
- **Method**: code-read
- **Finding**: **No — and the reason is load-bearing.** This hook is one of only three `scope = "global"` entries in `.claude/hooks/manifest.toml`. Global hooks are hardlinked to `~/.claude/hooks/sdlc/` by `scripts/update/hardlinks.py::sync_user_hooks` so they run inside *foreign* repos' sessions, where this repo's `hook_utils` is not on the path. `manifest.toml:296-303` and `hardlinks.py:30` (`MIN_GLOBAL_PYTHON = (3, 9)`) pin them to a resolved absolute system `python3` — worst case `/usr/bin/python3` = 3.9 on macOS — stdlib-only, no PEP 604 annotations without `from __future__ import annotations`, no `tomllib`/`datetime.UTC`/`typing.Self`. An always-on AST test (`tests/unit/test_hook_interpreter.py:293-299`) enforces that floor. The manifest also warns in-line: do **not** unify the SDLC forks with their project twins.
- **Confidence**: high
- **Impact on plan**: The helper goes in the sibling `sdlc_context.py`, which `sync_user_hooks` already deploys into the same directory (the directory-granularity note at `hardlinks.py:1184-1188` records that a global hooks dir missing `sdlc_context.py` kills every global hook with `ModuleNotFoundError`, so its co-deployment is already load-bearing and tested). No new file, no new manifest entry, no import of `hook_utils`.

### spike-3: Is passing the resolved directory to all three calls sufficient?
- **Assumption**: "Threading the effective directory into the three git calls fully fixes the hook."
- **Method**: code-read + live probe against a real linked worktree in this repo
- **Finding**: **False. It fixes two of three and silently guts the guard.** In a linked worktree, `git rev-parse --show-toplevel` returns the *worktree* root, not the main repo root. Probed directly in `/Users/valorengels/src/ai/.claude/worktrees/agent-a4d5f83767f0a1a2b` (git 2.50.1): `--show-toplevel` → `/Users/valorengels/src/ai/.claude/worktrees/agent-a4d5f83767f0a1a2b`, whose `os.path.basename` is the lane name, while `--git-common-dir` → `/Users/valorengels/src/ai/.git`, whose parent basename is `ai`. Applied to popoto: after a naive cwd fix, every commit made from a popoto worktree would compute `repo_name = "<lane-slug>"`, fail the `!= "popoto"` test, and take the `allow()` at `:79`. The guard would be *more* permissive than today for exactly the population the issue is about.
- **Confidence**: high
- **Impact on plan**: Repo identity must be derived from `git -C <dir> rev-parse --path-format=absolute --git-common-dir` and the basename of that path's parent (with `--show-toplevel` retained only as the fallback for a non-worktree checkout, where the two agree). This becomes its own task and its own regression assertion — a worktree in a repo named popoto must still be identified as popoto.

### spike-4: Does the `cd`-prefix rule cover how lane commits actually reach the hook?
- **Assumption**: "`hook_cwd` plus a leading `cd <path>` is the complete set of ways the effective directory diverges."
- **Method**: code-read
- **Finding**: **Incomplete.** `git -C <path> commit ...` relocates the commit just as effectively and appears nowhere in `_effective_dir`'s model. `_git_toplevel` in the destructive-git validator uses `-C` itself, so the form is in active use in this codebase. A fix that honors `cd` but not `-C` leaves a hole of precisely the kind being closed.
- **Confidence**: high
- **Impact on plan**: The resolver takes an explicit precedence order — `git -C <path>` on the commit command wins over a leading `cd <path>`, which wins over the payload `cwd`, which wins over the process cwd. Each rung gets a test.

## Data Flow

1. **Entry point**: an agent (often a lane running inside `.worktrees/<slug>/`) issues a `Bash` tool call whose command contains `git commit`.
2. **Harness**: Claude Code fires the `PreToolUse` / `Bash` hooks registered in `~/.claude/settings.json`, writing a JSON payload to the hook's stdin: `tool_name`, `tool_input.command`, `session_id`, **`cwd`**. The hook process itself is started in the session's project directory.
3. **`validate_commit_message_sdlc.py`** — today: reads `tool_name` and `command`, drops `cwd`, and shells three git commands that resolve against the hook process's directory. After this change: resolves an *effective directory* from `command` + payload `cwd`, and passes it to every git call via `git -C <dir>`.
4. **Git queries** (all `-C`-scoped): current branch; repo identity via `--git-common-dir`; staged file list.
5. **Output**: `block({"decision": "block", "reason": ...})` on stdout, or silence. `exit_policy = "propagate"` means any uncaught non-zero exit denies the Bash call, so every failure path stays fail-open.

## Architectural Impact

- **New dependencies**: none. Stdlib only (`os`, `subprocess`, `shlex`, `pathlib`), as the 3.9 global-interpreter floor requires.
- **Interface changes**: `sdlc_context.py` gains exported helpers (`effective_git_dir`, and the small `_split_simple_commands` it needs). The hook gains a pure predicate seam so tests can drive a verdict without the harness.
- **Coupling**: unchanged and deliberately so. The SDLC forks stay import-free of this repo's `hook_utils`; the resolution logic is duplicated from `validators/` on purpose, as `manifest.toml:294-296` instructs.
- **Data ownership**: none.
- **Reversibility**: high. One script plus one sibling module; revert is a single-commit revert followed by `/update` to re-propagate.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The two-checkout test fixture is built from `git init` / `git worktree add` inside `tmp_path`; no network, no credentials, no service.

## Solution

### Key Elements

- **`effective_git_dir(command, hook_cwd)` in `sdlc_context.py`**: resolves the directory an intercepted shell command will actually run its git in, by an explicit precedence order, stdlib-only and 3.9-clean.
- **`-C`-scoped git calls in the hook**: all three git invocations name their directory instead of inheriting one.
- **Worktree-correct repo identity**: repo name comes from the *common* git dir, so a lane inside `popoto/.worktrees/x` is still recognized as popoto.
- **A pure verdict seam**: `commit_block_reason(command, hook_cwd) -> str | None`, which `main()` wraps. Tests call it directly.
- **A two-checkout regression test**: a real repo plus a real linked worktree, deliberately disagreeing on branch and staged index, asserting the verdict follows the worktree in both directions.

### Flow

Agent in `.worktrees/lane-a` runs `git commit -m ...` → harness fires the PreToolUse hook with `cwd=/repo/.worktrees/lane-a` → hook resolves the effective directory from the payload (and any `cd` / `-C` override) → `git -C /repo/.worktrees/lane-a` answers branch, repo identity, and staged set → verdict describes **this** lane → block cites files this lane actually staged, or allows.

### Technical Approach

- **Resolution precedence**, highest first, implemented in `effective_git_dir`:
  1. `git -C <path>` appearing in the simple command that contains `commit` (relative paths resolved against the next rung down).
  2. A leading `cd <path>` simple command, resolved against `hook_cwd` when relative — ported from `validate_no_uv_sync_in_worktree.py::_effective_dir`, including `_split_simple_commands` splitting on `&& || ; |` and newlines and `shlex` tokenization with a `ValueError` fail-open.
  3. The payload `cwd`.
  4. `os.getcwd()` — reached only when the payload carries no `cwd`, and commented as an explicit last resort rather than the accidental default it is today.
- **Call form**: `git -C <effective_dir> ...` for all three queries, matching `validate_no_destructive_git_in_shared_checkout.py::_git_toplevel:201-208`, rather than `subprocess.run(cwd=...)`. A directory that no longer exists then produces a git error the existing handlers already absorb, instead of a `FileNotFoundError` from `subprocess` itself.
- **Repo identity** (spike-3): `git -C <dir> rev-parse --path-format=absolute --git-common-dir`, then `Path(that).parent.name`. Fall back to `--show-toplevel`'s basename when `--path-format` is unsupported (git < 2.31) or the call fails. Both paths agree for a non-worktree checkout; only the worktree case diverges, and the worktree case is the one the guard exists for.
- **Testability seam**: extract the decision into `commit_block_reason(command: str, hook_cwd: str) -> str | None`, returning the block reason or `None`. `main()` becomes payload-parsing plus `block(...)` / `allow()`. Every behavioral test drives the pure function; one thin test drives `main()` over real stdin to prove the wiring.
- **Fail-open is preserved verbatim**: the outer `except Exception: sys.exit(0)` at `:108-110` stays, and the new resolver never raises on malformed input — it returns a directory, always.
- **3.9 floor**: `from __future__ import annotations` is already at the top of the hook (`:26`) and must be added to `sdlc_context.py` if any new annotation there needs it. `tests/unit/test_hook_interpreter.py` enforces this and will fail the build if violated.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `validate_commit_message_sdlc.py:80-81` (`except Exception: pass` around the repo-identity probe) — after the change this swallows a failed `--git-common-dir` probe. Test: a `hook_cwd` that is not a git repo at all must produce `None` (allow), not a crash and not a block.
- [ ] `:102-103` (`except Exception: pass` around `git diff --cached`) — test with an effective directory that has been deleted out from under the hook; assert allow (fail-open) and no traceback on stdout.
- [ ] `:108-110` (outer fail-open) — test with malformed stdin (non-JSON, and JSON missing `tool_input`); assert exit 0 and empty stdout. A `PreToolUse` hook under `exit_policy = "propagate"` that exits 2 denies every Bash call, so this path is safety-critical.

### Empty/Invalid Input Handling
- [ ] `effective_git_dir("", "")` and `effective_git_dir(cmd, "")` — assert a usable directory is returned (the process cwd rung), never `None` and never an exception.
- [ ] `command` containing unbalanced quotes — `shlex.split` raises `ValueError`; assert the resolver falls through to the next rung rather than propagating.
- [ ] `cd` with no argument, and `git -C` with no argument — assert the rung is skipped, not indexed into.
- [ ] Empty staged set and a `git diff` returning only blank lines — assert allow (the existing `if f` filter at `:92` covers this; the test pins it).

### Error State Rendering
- [ ] The block reason must name files staged in the **effective** directory. Test asserts the reason string contains the worktree's staged filename and does **not** contain the main checkout's differently-named staged file — this is the user-visible half of the false-block bug.

## Test Impact

- [ ] `tests/unit/test_hook_interpreter.py::` (AST floor over global-scope scripts) — UPDATE: no code change expected, but the new `sdlc_context.py` helpers fall under its scan; confirm it still passes and that it covers `sdlc_context.py` and not only the three registered scripts. If it scans only registered scripts, extend it to sibling modules in `hooks/sdlc/` so the 3.9 floor cannot be broken through the helper module.
- [ ] `tests/unit/test_hook_migration.py` — UPDATE only if the file set under `hooks/sdlc/` changes. This plan adds no file there, so no change is expected; verify.
- [ ] `tests/unit/test_hook_manifest.py` — no change: registration (event, matcher, scope, exit_policy, timeout) is untouched.
- [ ] `tests/unit/test_validate_sdlc_on_stop.py` — REFERENCE, not modified: its `sys.path` setup (inserting both `.claude/hooks` and `.claude/hooks/validators`) is the import pattern the new test file copies.
- [ ] `tests/unit/hooks/test_validate_commit_message_sdlc.py` — CREATE: the behavioral suite for this hook. No test file for it exists today, which is why a bug this severe survived in a fleet-deployed guard.

## Rabbit Holes

- **Writing a real shell parser.** `_split_simple_commands` is a deliberate approximation (subshells, quoted `&&`, `eval`, heredocs all defeat it) and the existing validators say so in-line. Port the approximation, keep the fail-open, do not grow a grammar.
- **Unifying the SDLC forks with their `validators/` twins.** `manifest.toml:294-296` records this as a load-bearing fork with a spike behind it. The duplication is the design. Touching it turns a Small into a Large and risks every global hook on every machine.
- **Fixing the `"git commit" not in command` substring test.** Not command-position anchored, so `echo "git commit"` enters the slow path. Harmless behind the branch gate, and a different fix. Out of scope (see No-Gos).
- **Relitigating the `repo_name != "popoto"` hardcode.** Which repos get branch protection is a design question, not this bug.
- **Chasing `cd` inside `&&` chains beyond the first simple command** (`git status && cd x && git commit`). The ported helper looks only at the leading `cd`. Matching the existing validators exactly is worth more than covering a shape no lane emits.

## Risks

### Risk 1: The fix lands correctly and the guard still allows everything from worktrees
**Impact:** The `--show-toplevel` basename of a worktree is the lane slug, never `popoto`, so a naive cwd fix converts a false-allow-sometimes into a false-allow-always for worktree commits — while looking fixed. This is the single most likely way to ship a worse guard.
**Mitigation:** spike-3 caught it before build. Repo identity moves to `--git-common-dir` in its own task, and the regression test asserts a worktree of a repo named `popoto` is still identified as `popoto`. This assertion is non-negotiable; without it the suite cannot tell the two implementations apart.

### Risk 2: A crash in a `PreToolUse` hook denies every Bash call, fleet-wide
**Impact:** `exit_policy = "propagate"` passes a non-zero exit straight through, and exit 2 is the deny code. A hook that raises on some command shape bricks the agent everywhere, including the `/update` that would repair it.
**Mitigation:** The outer fail-open stays. The resolver is total (always returns a string). Explicit tests for malformed stdin, unparseable shell tokens, missing directories, and non-repo directories. The change is additive to a script whose every failure path already exits 0.

### Risk 3: 3.9 syntax creeps in through `sdlc_context.py`
**Impact:** The helper module is imported by all three global hooks. A `str | None` annotation without `from __future__ import annotations` raises `TypeError` at import time under `/usr/bin/python3` on macOS, killing all three on every machine — the exact shape of #2503.
**Mitigation:** `from __future__ import annotations` at the top of `sdlc_context.py`, plus the always-on AST test in `test_hook_interpreter.py` (extend its scan to sibling modules if it does not already cover them — see Test Impact).

### Risk 4: The repo copy and the deployed hardlink diverge
**Impact:** Editing `.claude/hooks/sdlc/*.py` with a replace-and-rename editor breaks the hardlink, leaving `~/.claude/hooks/sdlc/` running the old code while the repo shows the fix — a fixed-looking guard that is not deployed.
**Mitigation:** Known repo hazard with an existing PostToolUse repair hook. Verification includes an inode-equality check between the repo file and `~/.claude/hooks/sdlc/validate_commit_message_sdlc.py`, and `/update` re-propagates regardless.

## Race Conditions

No race conditions identified within the hook: it is a short-lived, single-threaded process that reads stdin, shells out synchronously, and exits.

The bug being fixed is nonetheless a concurrency *artifact* — it only manifests when a second checkout exists and its state disagrees with the first. That is cross-process interference through shared filesystem state, not a race in the hook's own control flow, and the fix (never read ambient state; read the state the payload names) is the correct resolution for it.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3259] Nothing about this hook's cwd resolution is deferred — the full fix, including the `--git-common-dir` correction spike-3 surfaced, is in scope for this plan.
- **Command-position anchoring of the `"git commit"` substring test** — not deferred to a tracked issue and not done here either: it is a false-positive-toward-checking with no user-visible effect (the branch gate discards it immediately), and touching the detection predicate in the same PR as the resolution fix would make the regression test ambiguous about which change fixed what. If it ever produces an observed symptom, it gets its own issue then.
- **The `repo_name != "popoto"` hardcode** — same disposition: a design question about branch-protection policy, not a defect, and no issue exists to point at.
- **Unifying the three SDLC forks with their `validators/` twins** — the manifest documents this fork as deliberate and spike-backed. Not deferred; rejected.

Every item this plan could finish, it finishes.

## Update System

The hook is `scope = "global"`: `scripts/update/hardlinks.py::sync_user_hooks` hardlinks it, and its sibling `sdlc_context.py`, into `~/.claude/hooks/sdlc/` on every machine, and regenerates the `hooks` block of `~/.claude/settings.json` from `.claude/hooks/manifest.toml`.

- No change to `scripts/remote-update.sh` or the `/update` skill is required. Both files this plan edits are already in the synced set, and no manifest entry changes (event, matcher, script path, scope, timeout, and exit policy all stay as they are).
- **Propagation is required to take effect**: until `/update` runs on a machine, that machine keeps the buggy guard. The plan's rollout note is simply that the fix is inert fleet-wide until `/update` lands there.
- No migration is needed. No new file appears under `hooks/sdlc/`, so `RENAMED_REMOVALS` in `hardlinks.py` is untouched and no `scripts/update/migrations.py` entry is warranted.
- No Popoto model changes, so no schema migration.

## Agent Integration

No agent integration required — this is a Claude Code harness hook, not agent-reachable functionality. It is invoked by the harness through `~/.claude/settings.json`, needs no `[project.scripts]` entry, no MCP surface, and no import from `bridge/telegram_bridge.py`. The agent's only relationship to this code is being governed by it.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-enforcement.md` — document that the commit-message guard resolves its git state from the intercepted command's effective directory (payload `cwd`, overridden by `cd` / `git -C`), and that repo identity comes from the common git dir so worktrees of a protected repo stay protected.
- [ ] Update `docs/features/hook-manifest.md` — note the `sdlc_context.py` helper addition in the global-scope section and restate the 3.9 stdlib-only floor that constrains it.
- [ ] `docs/features/README.md` index — no new entry; both pages already exist. Confirm no index change is needed.

### Inline Documentation
- [ ] Docstring on `effective_git_dir` giving the four-rung precedence order and stating that it never raises.
- [ ] Comment at the `--git-common-dir` call recording *why* `--show-toplevel` is wrong for worktrees, with the probed evidence from spike-3, so the next reader does not "simplify" it back.
- [ ] Comment marking the `os.getcwd()` rung as an explicit last resort, since its accidental use is the bug being fixed.

## Success Criteria

- [ ] All three git calls in `validate_commit_message_sdlc.py` are `-C`-scoped to the resolved effective directory; no unscoped git call remains in the file.
- [ ] `effective_git_dir` resolves, in order: `git -C <path>` on the commit command, a leading `cd <path>`, the payload `cwd`, then `os.getcwd()` — each rung covered by a test.
- [ ] Repo identity derives from `git -C <dir> rev-parse --path-format=absolute --git-common-dir`; a worktree of a repo named `popoto` is identified as `popoto`.
- [ ] Two-checkout regression test exists and is **proven red against the pre-fix hook**: it must fail on the parent commit and pass on the fix. Both directions are asserted — false block (worktree on a feature branch, main checkout on `main` with stale staged `.py` → allow) and false allow (worktree on `main` with staged `.py`, main checkout on a feature branch → block).
- [ ] Block reason names the effective directory's staged files and not the other checkout's.
- [ ] Fail-open preserved: malformed stdin, unparseable command, missing directory, and non-git directory each exit 0 with empty stdout.
- [ ] `sdlc_context.py` and the hook remain stdlib-only and import clean under Python 3.9 (`test_hook_interpreter.py` green).
- [ ] Repo file and `~/.claude/hooks/sdlc/` deployed copy share an inode after the change.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hook resolution)**
  - Name: `hook-cwd-builder`
  - Role: Implement `effective_git_dir` in `sdlc_context.py` and rewire the three git calls plus repo identity in `validate_commit_message_sdlc.py`.
  - Agent Type: builder
  - Resume: true

- **Test engineer (two-checkout regression)**
  - Name: `hook-cwd-tester`
  - Role: Build the dual-checkout fixture and the behavioral suite, and prove it red against the pre-fix hook.
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `hook-cwd-docs`
  - Role: Update `docs/features/sdlc-enforcement.md` and `docs/features/hook-manifest.md`.
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: `hook-cwd-validator`
  - Role: Verify every success criterion, including the red-state proof and the 3.9 floor.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add the effective-directory resolver to `sdlc_context.py`
- **Task ID**: build-resolver
- **Depends On**: none
- **Validates**: `tests/unit/hooks/test_validate_commit_message_sdlc.py` (create)
- **Informed By**: spike-2 (helper must live in `sdlc_context.py`, stdlib-only, 3.9 floor), spike-4 (`git -C` rung is required)
- **Assigned To**: `hook-cwd-builder`
- **Agent Type**: builder
- **Parallel**: false
- Port `_split_simple_commands` from `validators/validate_no_uv_sync_in_worktree.py:70` verbatim in behavior (split on `&& || ; |` and newlines, strip, drop empties).
- Add `effective_git_dir(command, hook_cwd)` implementing the four-rung precedence; `shlex.split` failures fall through to the next rung; relative paths resolve against the rung below; the function never raises and always returns a string.
- Add `from __future__ import annotations` to `sdlc_context.py` if any new annotation requires it. Stdlib imports only.
- Docstring states the precedence order and the never-raises contract.

### 2. Rewire the hook's three git calls and fix repo identity
- **Task ID**: build-hook
- **Depends On**: build-resolver
- **Validates**: `tests/unit/hooks/test_validate_commit_message_sdlc.py` (create)
- **Informed By**: spike-1 (payload `cwd` exists), spike-3 (`--show-toplevel` is wrong for worktrees)
- **Assigned To**: `hook-cwd-builder`
- **Agent Type**: builder
- **Parallel**: false
- Read `cwd` from the payload in `main()` and compute the effective directory once.
- Extract `commit_block_reason(command, hook_cwd) -> str | None` as the pure verdict seam; reduce `main()` to payload parsing plus `block()` / `allow()`.
- Convert `get_current_branch`, the repo-identity probe, and `git diff --cached --name-only` to `git -C <effective_dir> ...`.
- Replace the repo-identity probe with `rev-parse --path-format=absolute --git-common-dir` + parent basename, falling back to `--show-toplevel`'s basename on failure. Comment the worktree reasoning inline.
- Mark the `os.getcwd()` rung as an explicit last resort in a comment.
- Preserve every existing fail-open path unchanged.

### 3. Two-checkout regression suite, proven red first
- **Task ID**: build-tests
- **Depends On**: none (fixture and red-state proof are authored against the pre-fix hook)
- **Validates**: `tests/unit/hooks/test_validate_commit_message_sdlc.py` (create)
- **Informed By**: spike-3 (worktree identity assertion is the discriminating test), spike-4 (per-rung resolution tests)
- **Assigned To**: `hook-cwd-tester`
- **Agent Type**: test-engineer
- **Parallel**: true
- Build a `tmp_path` fixture: `git init` a repo whose directory basename is `popoto`, commit a base, then `git worktree add` a linked worktree; set branch and staged index on the two checkouts independently.
- Assert the false-allow direction: worktree on `main` with a staged `.py`, main checkout on a feature branch → verdict blocks and the reason names the worktree's file.
- Assert the false-block direction: worktree on a feature branch, main checkout on `main` with a stale staged `.py` → verdict allows.
- Assert worktree repo identity resolves to `popoto` (this is the assertion that distinguishes a correct fix from a guard that allows everything).
- Cover each resolution rung: `git -C` over `cd`, `cd` over payload `cwd`, payload `cwd` over process cwd.
- Cover the failure paths enumerated in Failure Path Test Strategy: malformed stdin, unbalanced quotes, `cd`/`-C` with no argument, deleted directory, non-git directory, empty staged set.
- One end-to-end test drives `main()` over real stdin to prove the wiring, asserting exit 0 and the JSON block payload on stdout.
- Follow the `sys.path` pattern from `tests/unit/test_validate_sdlc_on_stop.py:12-17`.
- **Red-state proof**: run the discriminating tests against the parent commit's hook and capture the FAIL output for the PR description. A guard test that was never red is not evidence.

### 4. Validate the 3.9 floor coverage
- **Task ID**: build-floor-check
- **Depends On**: build-resolver
- **Validates**: `tests/unit/test_hook_interpreter.py`
- **Assigned To**: `hook-cwd-tester`
- **Agent Type**: test-engineer
- **Parallel**: false
- Confirm the AST floor test scans `sdlc_context.py`, not only the three registered scripts. If it does not, extend it to every `.py` under `.claude/hooks/sdlc/` — the helper module is imported by all three global hooks, so a violation there is exactly as fatal.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-hook, build-tests
- **Assigned To**: `hook-cwd-docs`
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-enforcement.md` and `docs/features/hook-manifest.md` per the Documentation section.
- Confirm `docs/features/README.md` needs no new row.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-hook, build-tests, build-floor-check, document-feature
- **Assigned To**: `hook-cwd-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm the red-state proof is in the PR description.
- Confirm the repo file and the `~/.claude/hooks/sdlc/` copy still share an inode.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Hook tests pass | `scripts/pytest-clean.sh tests/unit/hooks/test_validate_commit_message_sdlc.py -q` | exit code 0 |
| Interpreter floor green | `scripts/pytest-clean.sh tests/unit/test_hook_interpreter.py -q` | exit code 0 |
| Manifest unchanged in shape | `scripts/pytest-clean.sh tests/unit/test_hook_manifest.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/hooks/sdlc/ tests/unit/hooks/` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/hooks/sdlc/ tests/unit/hooks/` | exit code 0 |
| No unscoped git call remains | `grep -c '\["git", "rev-parse"' .claude/hooks/sdlc/validate_commit_message_sdlc.py` | match count == 0 |
| Repo identity uses the common git dir | `grep -c 'git-common-dir' .claude/hooks/sdlc/validate_commit_message_sdlc.py` | output > 0 |
| Payload cwd is actually read | `grep -c 'get("cwd"' .claude/hooks/sdlc/validate_commit_message_sdlc.py` | output > 0 |
| Resolver exists in the deployed sibling | `grep -c 'def effective_git_dir' .claude/hooks/sdlc/sdlc_context.py` | output > 0 |
| Anti-criterion: no `hook_utils` import in the global fork | `grep -rn 'hook_utils' .claude/hooks/sdlc/` | match count == 0 |
| Anti-criterion: `--show-toplevel` is not the identity source | `grep -c 'repo_name = os.path.basename(repo_root)' .claude/hooks/sdlc/validate_commit_message_sdlc.py` | match count == 0 |
| Deployed hardlink intact | `python -c "import os,pathlib; a=os.stat('.claude/hooks/sdlc/validate_commit_message_sdlc.py'); b=os.stat(pathlib.Path.home()/'.claude/hooks/sdlc/validate_commit_message_sdlc.py'); print(a.st_ino==b.st_ino)"` | output contains True |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None blocking. Two decisions were made inside the plan rather than escalated, and are flagged here for a reviewer who disagrees:

1. The `--show-toplevel` → `--git-common-dir` correction (spike-3) widens the diff beyond the literal text of the issue. It is included because shipping the issue's fix without it would make the guard strictly more permissive for worktree commits than it is today.
2. The `git -C <path> commit` rung (spike-4) is not named in the issue. It is included because omitting it would leave a hole of the same class the fix exists to close.
