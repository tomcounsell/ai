---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-08-06
tracking: https://github.com/tomcounsell/ai/issues/2561
last_comment_id: 5202633042
---

# Restore whole-directory deployment for global-scope hook scripts

## Problem

All three `scope = "global"` hook scripts import a shared helper at module
scope:

```python
from sdlc_context import is_sdlc_context, read_stdin
```

`sdlc/sdlc_context.py` registers no hook, so it has no `[[hook]]` declaration —
and `sync_user_hooks` hardlinks exactly one file per declaration. The helper is
therefore never deployed to `~/.claude/hooks/sdlc/`.

**Current behavior:** on any machine whose `~/.claude/hooks/` is a real
directory, all three global fork hooks die at import with `ModuleNotFoundError`
on every Bash call, every Stop, and every Write/Edit — **in every foreign repo**.
That is the exact blast radius #2503 was filed about, reintroduced through a
different mechanism.

The break is invisible on this development machine, and that is the only reason
it has gone unnoticed: `~/.claude/hooks` here is a legacy symlink into the repo
(#2567), so the helper resolves to the repo's own copy. The machine that looks
healthiest is the one running the least representative configuration.

**Desired outcome:** a machine whose `~/.claude/hooks/` was built solely by
`sync_user_hooks` can execute all three global hooks without import errors, and
adding a fourth global hook with a new helper cannot silently reintroduce the
gap.

## Freshness Check

**Baseline commit:** `ac0da707bb2452b5366034f4ce546180110c09f7`
**Issue filed at:** `2026-08-06T08:21:06Z` (today)
**Disposition:** Unchanged — but the issue's own recon was corrected before
planning, by me, and that correction is what raised its severity.

**File:line references re-verified:**

- `scripts/update/hardlinks.py:893-936` — `global_decls` filter and the
  per-`decl.script` hardlink loop. Confirmed present and unchanged.
- `.claude/hooks/sdlc/validate_commit_message_sdlc.py:35`,
  `sdlc/validate_sdlc_on_stop.py:34`, `sdlc/sdlc_reminder.py:29` — all three
  `from sdlc_context import ...` statements confirmed at module scope.
- `.claude/hooks/manifest.toml` — `grep -c sdlc_context` returns **0**.
  Confirmed: the helper has no declaration.

**Correction carried in from the issue thread (comment `5202610696`):** the
issue as originally filed said the helper "is present on this host only as a
pre-manifest artifact," citing shared inode `1526758`. The shared inode was real
but the explanation was wrong — `~/.claude/hooks` is a symlink to
`<repo>/.claude/hooks`, so the two paths are the same file. Nothing has ever
deposited that helper into a real user-tree directory. This changes the issue
from "latent on a hypothetical fresh machine" to "live on every correctly
configured machine."

**Cited sibling issues/PRs re-checked:**

- #2521 — **CLOSED** today as premise-disproved. Same symlink was the root of
  its false premise. Its PR #2565 is closed unmerged.
- #2567 — **OPEN**, filed today. The symlink migration. **Ordering constraint:
  this plan must land before or with #2567** — see Risks.
- #2503 / PR #2522 — merged. The "global hooks dead in every foreign repo"
  failure mode this issue reproduces by a different route.
- #2435 / PR #2453 (`44e362d83`) — merged. **This is the regression's origin.**
  See Prior Art.

**Commits on main since the issue was filed (touching referenced files):** none.
The issue is hours old.

**Active plans in `docs/plans/` overlapping this area:** none. The one adjacent
plan, `prune-orphaned-hook-files.md`, is dead alongside #2521.

## Prior Art

**The decisive finding: this is a regression with a known-good prior
implementation.**

- **PR #195 (`ee8095d67`) — "SDLC user-level hooks"** introduced
  `sync_user_hooks` and deployed the `sdlc/` directory **by glob**:

  ```python
  src_hooks = project_dir / ".claude" / "hooks" / "sdlc"
  dst_hooks = user_claude / "hooks" / "sdlc"
  for src_file in sorted(src_hooks.glob("*.py")):
  ```

  That loop covered `sdlc_context.py` automatically. The same PR is what
  *extracted* the shared helper in the first place, so directory-granular
  deployment and the shared-helper design were introduced together, as one
  coherent idea.

- **PR #2453 (`44e362d83`) — "Hook registration: per-event dispatcher +
  manifest-generated scopes"** replaced that loop with a per-declaration one
  (`for decl in global_decls: ... src_file = ... / decl.script`). Verified in
  the diff: `- for src_file in sorted(src_hooks.glob("*.py")):` removed,
  `+ src_file = project_dir / ".claude" / "hooks" / decl.script` added.
  Correct for *registration*, which genuinely is per-declaration. But it
  silently narrowed *deployment* from the directory to the declared file, and
  nothing in the manifest could express the helper.

- **`git log -S "sdlc_context" -- scripts/update/hardlinks.py` returns nothing.**
  The sync machinery has never named the helper. The old code did not need to;
  the new code cannot.

- **`sdlc_context.py`'s own docstring still asserts the old contract:** *"This is
  a STANDALONE module deployed to `~/.claude/hooks/sdlc/` by the update system."*
  The file documents a guarantee the update system stopped honoring 
  underneath it.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #195 | Deployed `sdlc/*.py` by directory glob; extracted the shared helper | Did not fail. This is the behavior being restored. |
| PR #2453 | Made the manifest the single source of truth for hook *registration* | Conflated registration granularity with deployment granularity. A declaration names a file **that registers a hook**; it was silently treated as naming **every file needed to run**. Nothing tested that a synced hook could actually import. |
| PR #2522 (#2503) | Fixed the interpreter token so global hooks could execute at all | Correct and necessary, but it verified the *command* resolves, not that the *script* imports. The two failure modes look identical from the outside — a global hook that does nothing in a foreign repo. |

**Root cause pattern:** every fix in this area has verified one link of the chain
(is it registered? does the interpreter resolve?) without ever verifying the
chain end to end (does the thing actually run?). This plan adds that end-to-end
check, which is the part that generalizes beyond `sdlc_context.py`.

## Research

Purely internal — no external libraries, APIs, or ecosystem patterns involved.
The one language-level fact the design relies on is standard CPython behavior:
running `python /path/to/script.py` puts `/path/to/` at `sys.path[0]`, which is
why a sibling-module import works at all once the sibling is present.

No relevant external findings — proceeding with codebase context.

## Spike Results

### spike-1: Is the gap original, or a regression?

- **Assumption**: "The generator never handled helpers; this is an original
  design omission."
- **Method**: code-read of `git log -S` history on `scripts/update/hardlinks.py`
- **Finding**: **Regression.** PR #195 deployed the whole `sdlc/` directory by
  glob; PR #2453 replaced it with per-declaration sync. Diff hunks confirmed in
  both directions.
- **Confidence**: high
- **Impact on plan**: Changes the fix from "invent a companion-file mechanism"
  to "restore directory-granular deployment." Restoring proven prior behavior is
  a far smaller risk than designing a new manifest concept, and it needs no
  manifest schema change.

### spike-2: Will an import smoke-check actually catch this class?

- **Assumption**: "The three scripts can be executed in a test without side
  effects, so a smoke-check is viable."
- **Method**: code-read of `sdlc_context.read_stdin` and the three scripts'
  entry paths
- **Finding**: **Viable.** `read_stdin()` returns `{}` on empty or unparseable
  stdin (`sdlc_context.py:58-66`), and each script's guard path exits cleanly on
  an empty payload. A subprocess run with empty stdin exercises module-scope
  imports — where the failure lives — without triggering hook side effects.
- **Confidence**: high
- **Impact on plan**: The smoke-check becomes the primary regression guard, and
  it is mechanism-agnostic: it keeps passing if deployment is later changed
  again, and fails the moment any global script gains an unsatisfiable import.

## Data Flow

1. **Entry point**: `/update` → `scripts/update/run.py` Step 1.5 →
   `sync_claude_dirs(project_dir)`.
2. **`sync_user_hooks`** (`hardlinks.py:873`): loads the manifest, filters to
   `scope == "global"`, resolves the global interpreter.
3. **Deployment (the defect)**: hardlinks `<repo>/.claude/hooks/<decl.script>`
   → `~/.claude/hooks/<decl.script>`, once per declaration. `sdlc_context.py` is
   in no declaration, so it is never copied.
4. **Registration**: `_merge_hook_settings` writes
   `/usr/bin/python3 ~/.claude/hooks/sdlc/<script>.py # hook:<id>` into
   `~/.claude/settings.json`.
5. **Runtime**: Claude Code runs that command in a foreign repo. CPython sets
   `sys.path[0] = ~/.claude/hooks/sdlc/`. The `from sdlc_context import ...` at
   module scope raises `ModuleNotFoundError`. The hook is dead.

The fix is at step 3. Steps 4 and 5 are already correct.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none to the manifest schema. `sync_user_hooks`'s
  internal deployment unit changes from "declared file" to "directory containing
  declared files".
- **Coupling**: *decreases* the coupling between the manifest and the filesystem
  layout — the manifest goes back to being authoritative for registration only,
  which is what it is good at, rather than doubling as an implicit dependency
  manifest, which it has no vocabulary for.
- **Data ownership**: unchanged.
- **Reversibility**: high. One function's loop, plus tests.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (merge slot)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Directory-granular deployment.** For each distinct directory containing a
  `scope = "global"` declared script, hardlink every `*.py` in that directory to
  the matching directory under `~/.claude/hooks/`. Today that is exactly
  `sdlc/`, so the deployed set becomes the three fork scripts plus
  `sdlc_context.py`.
- **Manifest stays authoritative for registration.** Only declared scripts get
  a `settings.json` entry. Deploying a helper does not register it, and could
  not — it declares no event.
- **An end-to-end import smoke-check.** A test syncs into an empty temp home and
  executes each declared global script under the resolved interpreter with empty
  stdin, asserting no `ModuleNotFoundError`/`ImportError`. This is the guard
  that generalizes: it fails for *any* future global script with an
  unsatisfiable import, regardless of how deployment is implemented.

### Flow

`/update` → `sync_claude_dirs` → `sync_user_hooks` → for each global
declaration, resolve its parent directory → hardlink every `*.py` in that
directory → register only the declared scripts in `~/.claude/settings.json` →
foreign-repo hook invocation imports cleanly.

### Technical Approach

- **Derive the directory set from the declarations, never hardcode `sdlc/`.**
  `dirs = {PurePosixPath(d.script).parent for d in global_decls}`. A future
  global hook in a new directory is covered automatically; a helper added beside
  an existing one is covered automatically. Hardcoding `sdlc/` would restore the
  bug for the next directory.
- **A declaration at the hooks root (`parent == "."`) deploys only its own
  file.** Globbing the root would sweep every project-scope script into the user
  tree — recreating precisely the residue #2521 imagined. This case must be
  handled explicitly, not left to fall out of the glob.
- **`glob("*.py")` only.** Non-Python files and subdirectories (notably
  `__pycache__/`) are not deployed. Bytecode is regenerable and machine-specific.
- **Deployment is additive.** No file is deleted from `~/.claude/hooks/`. Stale
  helper removal needs the symlink guard that #2567 owns; see No-Gos.
- **Preserve the existing error posture.** A missing source file records an error
  in `HardlinkSyncResult` and continues, matching the current loop. A declared
  script that is missing remains an error; a directory that yields no extra
  helpers is simply a no-op.
- **Idempotent.** `_ensure_hardlink` already no-ops when the target is the
  correct inode, so repeat `/update` runs make no changes.

## Failure Path Test Strategy

### Exception Handling Coverage

`sync_user_hooks` has no `except Exception: pass`. Its failure mode is recording
a `LinkAction(..., "error", ...)` and incrementing `result.errors`, which
`/update` surfaces. That posture is preserved, not changed.

- [ ] Test that a declared global script missing from the repo still records an
      error and does not abort the sync of its siblings.

### Empty/Invalid Input Handling

- [ ] A manifest with zero global declarations deploys nothing and still runs
      the registration-removal pass (existing behavior, must not regress).
- [ ] A declaration whose script sits at the hooks root deploys only that file —
      asserted explicitly, since this is the case that could sweep the whole
      project tree.
- [ ] `read_stdin()` already returns `{}` on empty stdin, which is what makes the
      smoke-check safe to run.

### Error State Rendering

- [ ] The smoke-check asserts on the *content* of stderr, not merely on exit
      status: a hook that exits 0 while printing `ModuleNotFoundError` is
      exactly today's silent failure, and an exit-code-only assertion would
      pass it.

## Test Impact

- [ ] `tests/unit/test_hook_migration.py` — no change expected; it exercises the
      settings-registration migrations, not deployment. Re-run to confirm.
- [ ] Existing `sync_user_hooks` coverage — UPDATE if any test asserts an exact
      count of deployed files; the count rises from 3 to 4. Located during build;
      the assertion should become "declared scripts ⊆ deployed", not an equality
      on a magic number.

No other existing tests are affected.

## Rabbit Holes

- **Adding a `companions`/`also_sync` key to the manifest schema.** The obvious
  design, and the one the issue body floated first. spike-1 makes it
  unnecessary: directory-granular deployment was the original behavior and needs
  no new vocabulary. A schema addition would also be a second place to remember
  when adding a helper — the same failure mode, moved.
- **A transitive AST import walk** to discover dependencies. Rejected in this
  area twice already (#2503, #2521). The smoke-check gets the safety benefit
  without the machinery.
- **Fixing the `~/.claude/hooks` symlink here.** That is #2567 and it has a
  hard ordering dependency on this work. Doing both at once means shipping a
  change that can brick a working machine if only half lands.
- **Adding stale-file deletion to the synced directory.** Tempting for symmetry,
  and genuinely wrong to do before #2567: on a symlinked machine, deleting
  "user-tree files with no source" would operate on tracked repo source. The
  36-file near-miss on #2521 is what that looks like.

## Risks

### Risk 1: Landing after #2567 would brick every machine

**Impact:** #2567 migrates `~/.claude/hooks` from a symlink to a real directory.
If that lands first, the real directory is built by today's `sync_user_hooks` —
three scripts, no helper — which is exactly this bug, now unmasked on the
development machine too. A working machine becomes a broken one.
**Mitigation:** Stated as an explicit ordering constraint in both issues.
#2567's acceptance criteria require the sequencing to be enforced by test rather
than by discipline. This plan is the one that must land first, and it is
independently safe to land alone.

### Risk 2: Directory glob deploys something unintended

**Impact:** A future `.py` dropped into `sdlc/` reaches every machine's user
tree without review.
**Mitigation:** Accepted, and it is the pre-#2453 status quo. `sdlc/` exists
solely to hold fork-scope scripts, so proximity and intent coincide there. The
glob is `*.py` only, so `__pycache__/` and data files are excluded. The
root-directory case — the one where proximity and intent genuinely diverge — is
handled explicitly rather than by glob.

### Risk 3: The smoke-check is flaky in CI

**Impact:** The three scripts shell out to `git` and may touch Redis via an
optional `AgentSession` import.
**Mitigation:** `sdlc_context.py` documents the `AgentSession` import as
optional with graceful fallback, and the `git` call is wrapped in
`try/except (CalledProcessError, FileNotFoundError)`. The check asserts only the
absence of import errors in stderr — not exit status, not behavior — so it is
insensitive to whether the environment has git, Redis, or a session.

## Race Conditions

No race conditions identified. `sync_user_hooks` runs synchronously and
single-threaded within one `/update` invocation, and `_ensure_hardlink` is
idempotent. There is no concurrent writer to `~/.claude/hooks/`.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2567] Migrating the `~/.claude/hooks` legacy directory symlink
  to a real directory, and the shared repo-alias guard. Must land after this.
- [SEPARATE-SLUG #2567] Removing stale `.py` files from the synced directory
  when their source disappears. Deletion under `~/.claude/hooks/` is unsafe
  until #2567's symlink guard exists; that issue owns the guard, so it owns this.

## Update System

This is an update-system change: `scripts/update/hardlinks.py::sync_user_hooks`
is invoked by `sync_claude_dirs` at `/update` Step 1.5. No new dependencies, no
new config, no operator action. Every machine self-heals on its next `/update` —
which is the whole delivery mechanism for the fix.

## Agent Integration

No agent integration required — this is update-machinery-internal. No MCP
surface, no bridge wiring, no new tool.

## Documentation

- [ ] Update `docs/features/hook-manifest.md`: state that the manifest is
      authoritative for **registration**, while **deployment** is
      directory-granular, and explain why the two differ. Name `sdlc_context.py`
      as the worked example of a deployed-but-unregistered file.
- [ ] In the same doc, record the ordering constraint with #2567 so whoever
      picks that up finds it without reading this plan.
- [ ] Inline: a comment at the deployment loop explaining that the directory —
      not the declaration — is the deployment unit, citing #2561 and the PR
      #2453 regression, so the next refactor does not re-narrow it.
- [ ] `sdlc/sdlc_context.py`'s docstring already claims it is "deployed to
      `~/.claude/hooks/sdlc/` by the update system". Once this lands that is true
      again; no edit needed, but verify it rather than assume.

No `docs/features/README.md` index entry — `hook-manifest.md` is already indexed.

## Success Criteria

- [ ] `sync_user_hooks` into an empty temp home deploys `sdlc/sdlc_context.py`
      alongside the three declared scripts — asserted by test
- [ ] Each declared global script, executed from that temp home under the
      resolved interpreter with empty stdin, produces no `ModuleNotFoundError`
      or `ImportError` on stderr — asserted by test
- [ ] The deployment directory set is derived from the declarations, not
      hardcoded to `sdlc/` — asserted by a test using a synthetic manifest whose
      global script lives in a different directory
- [ ] A global declaration at the hooks root deploys only its own file and does
      not sweep the directory — asserted by test
- [ ] Only declared scripts appear in `~/.claude/settings.json`; the helper is
      deployed but never registered — asserted by test
- [ ] No file is deleted from `~/.claude/hooks/` by this change — asserted by
      test
- [ ] Tests pass (`/do-test`, focused on the hook sync tests)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

One agent. Appetite:Small, one function's loop plus a focused test module,
restoring a behavior that has a known-good prior implementation to copy.

### Team Members

- **Builder (sync + tests)**
  - Name: `hook-sync-builder`
  - Role: implement directory-granular deployment and the smoke-check tests
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Restore directory-granular deployment and add the smoke-check

- **Task ID**: build-sync
- **Depends On**: none
- **Validates**: `tests/unit/test_update_hardlinks.py` (the existing home for
  `sync_user_hooks` coverage; `test_hook_interpreter.py`, `test_hooks_audit.py`,
  and `test_hook_migration.py` also touch it and must stay green)
- **Informed By**: spike-1 (regression from PR #2453; PR #195's
  `glob("*.py")` is the behavior to restore), spike-2 (`read_stdin()` returns
  `{}` on empty stdin, so subprocess smoke-checks are safe)
- **Assigned To**: `hook-sync-builder`
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/update/hardlinks.py::sync_user_hooks`, derive the deployment
  directory set from the global declarations
  (`{PurePosixPath(d.script).parent for d in global_decls}`) and hardlink every
  `*.py` in each such directory. Do not hardcode `sdlc/`.
- Handle the hooks-root case (`parent == "."`) explicitly: deploy only the
  declared file, never the whole root.
- Keep registration unchanged — only declared scripts reach
  `_merge_hook_settings`.
- Preserve the existing error posture: a missing declared source records a
  `LinkAction(..., "error", ...)` and continues.
- Add a comment at the loop naming #2561 and the PR #2453 regression, so a
  future refactor does not re-narrow deployment to the declaration.
- Add tests:
  - `sdlc_context.py` is deployed into an empty temp home.
  - Each declared global script runs under the resolved interpreter with empty
    stdin and prints no `ModuleNotFoundError`/`ImportError` to stderr. Assert on
    stderr content, not exit status.
  - A synthetic manifest with a global script in a non-`sdlc/` directory
    deploys that directory's helpers — proving the set is derived.
  - A synthetic manifest with a global script at the hooks root deploys only
    that file.
  - The helper is deployed but does NOT appear in `~/.claude/settings.json`.
  - Nothing under `~/.claude/hooks/` is deleted.
  - Re-running the sync is a no-op.

### 2. Validation

- **Task ID**: validate-all
- **Depends On**: build-sync
- **Assigned To**: `hook-sync-builder`
- **Agent Type**: builder
- **Parallel**: false
- Run the focused test files serially (`-n0`) and report results.
- Run `ruff check` and `ruff format --check` on touched files.
- Confirm no existing `sync_user_hooks` test asserted a hardcoded deployed-file
  count; update to a subset assertion if one did.
- Verify every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Focused sync tests pass | `scripts/pytest-clean.sh tests/unit/test_update_hardlinks.py tests/unit/test_hook_interpreter.py -n0 -q` | exit code 0 |
| Existing hook migration/audit tests pass | `scripts/pytest-clean.sh tests/unit/test_hook_migration.py tests/unit/test_hooks_audit.py -n0 -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update/hardlinks.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update/hardlinks.py` | exit code 0 |
| Helper is deployed, not just declared | `scripts/pytest-clean.sh tests/unit/test_update_hardlinks.py -n0 -q -k sdlc_context` | exit code 0 |
| Global scripts import cleanly after sync | `scripts/pytest-clean.sh tests/unit/test_update_hardlinks.py -n0 -q -k import_smoke` | exit code 0 |
| Anti-criterion: `sdlc/` not hardcoded in the sync | `! grep -nE '"sdlc/"\|/ "sdlc"' scripts/update/hardlinks.py` | exit code 0 |
| Anti-criterion: no AST import walk | `! grep -qE '^[[:space:]]*import ast' scripts/update/hardlinks.py` | exit code 0 |
| Anti-criterion: no deletion added under user hooks | `! grep -nE 'hooks_root.*unlink\|unlink\(\).*hooks_root' scripts/update/hardlinks.py` | exit code 0 |

Anti-criterion rows use the `! grep` form deliberately: `grep -c` prints `0` but
*exits 1* on a zero-count match, so a `match count == 0` row misreports the
intended pass as a command failure under an `&&`-chained or `set -e` runner.

## Critique Results

**War room, 2026-08-06.** Depth: FULL (force-FULL — the plan touches the
`.claude/hooks/` doctrine path). Critics: Risk & Robustness, Scope & Value,
History & Consistency, plus driver structural checks. Roster gate: 3/3 complete,
3/3 grounded. Verdict: **READY TO BUILD (with concerns)** — no blockers; the
core design (restore PR #195's directory-granular glob) was not challenged by any
critic, and History & Consistency independently re-verified the plan's PR #195 /
PR #2453 / `git log -S` claims against real git history and found them accurate.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | History & Consistency + driver structural check | Both anti-criterion rows in the Verification table escape the alternation pipe as `\|` inside a `grep -E` invocation (`'"sdlc/"\|/ "sdlc"'` and `'hooks_root.*unlink\|unlink\(\).*hooks_root'`). Under `-E` a backslash-escaped pipe is a *literal* pipe character, not alternation, so neither pattern can ever match; grep always exits 1 and the `!`-negated row always reports pass regardless of file content. Verified empirically against a fixture containing `"sdlc/"`. The plan reasoned carefully about one grep pitfall in the note below the table and walked into a different one in the same table. | pending | `grep -E 'a\|b'` matches only the literal three-character sequence `a\|b`; use `grep -E 'a\|b'` without the backslash (or `grep -e a -e b`) for true alternation. Drop the backslash in both rows, then prove each row flips to a failing exit code when the forbidden substring IS present — do not accept "exits 0 today" as evidence. |
| CONCERN | driver structural check | The Verification row "Helper is deployed, not just declared" runs `grep -c "glob" scripts/update/hardlinks.py` expecting output > 0, but `glob` already appears 50 times in that file on `main` **before** the fix. The row passes today and therefore proves nothing about the fix. | pending | Replace the proxy grep with a direct assertion on behavior: either drop the row (the temp-home deployment test already covers it) or make it specific, e.g. `grep -n 'glob("\*.py")' scripts/update/hardlinks.py` scoped to the `sync_user_hooks` body. Confirm the replacement row fails on `git stash`ed working tree before the change lands. |
| CONCERN | Risk & Robustness (Skeptic) | spike-2 claims a subprocess run with empty stdin exercises module-scope imports "without triggering hook side effects", but `validate_sdlc_on_stop.py` and `sdlc_reminder.py` call `is_sdlc_context()`, which reads `CLAUDE_SESSION_ID` from the environment and, when set, imports the real `models.agent_session.AgentSession` and issues a live Redis query. The smoke-check test itself runs inside a live agent session, so `subprocess.run`'s default env inheritance very likely leaks `CLAUDE_SESSION_ID` into the child — a unit test silently touching production Redis, against the repo's test-isolation rule. | pending | Build the subprocess env explicitly instead of inheriting: `env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE_")}`, then `subprocess.run([interpreter, str(dst_file)], input=b"", env=env, capture_output=True, timeout=10)`. This makes "empty stdin" actually imply "no external calls" rather than merely "no stdin-derived branch". |
| CONCERN | Risk & Robustness (Operator) + Scope & Value (User) — two critics agreed independently | Every Success Criterion and Verification row validates only against a synthetic temp `HOME` in pytest. Nothing checks a machine whose `~/.claude/hooks` is a real directory actually running `/update` and invoking a hook live — which is precisely the scenario the Problem section says is broken in every foreign repo. The plan's stated delivery mechanism ("Every machine self-heals on its next `/update`") has no corresponding staleness signal, echoing the repo's prior "worker running old sha" incidents. | pending | This dev machine cannot perform the real check (its `~/.claude/hooks` is the #2567 symlink), so say so **explicitly** in Verification rather than letting the temp-home pytest stand in silently as "verified on a real machine". Cheapest durable signal: after `sync_claude_dirs` in `scripts/update/run.py`, print a greppable line — `"hooks: sdlc_context deployed"` vs `"hooks: MISSING sdlc_context (see #2561)"` keyed on `(Path.home()/".claude/hooks/sdlc/sdlc_context.py").exists()`. |
| NIT | Scope & Value (Simplifier) | The same six assertions (helper deployed, no `ModuleNotFoundError`, root case not swept, helper unregistered, nothing deleted, re-run is a no-op) are independently restated across four checklists — Failure Path Test Strategy, Test Impact, Success Criteria, and the Verification table — for a change the plan itself calls "one function's loop, plus tests". | pending | Task 1's own bullet list is already the complete spec; the other sections could reference it by name. Non-blocking. |

---

## Open Questions

None. spike-1 resolved the design question by finding a known-good prior
implementation to restore, and spike-2 confirmed the regression guard is
viable. The one cross-issue decision — ordering against #2567 — is settled and
recorded in both issues.
