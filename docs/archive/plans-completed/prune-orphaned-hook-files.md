---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-08-06
tracking: https://github.com/tomcounsell/ai/issues/2521
last_comment_id: 5202126010
revision_applied: true
revision_applied_at: 2026-08-06T08:43:17Z
---

# Prune orphaned pre-manifest hook files under `~/.claude/hooks/`

## Problem

Every fleet machine's `~/.claude/hooks/` still holds whole trees of scripts that
predate the manifest-driven hook generator (#2435 / PR #2453). The generator
hardlinks only the manifest's `scope = "global"` declarations — today exactly
three scripts, all under `sdlc/` — so it has no concept of, and no ability to
remove, anything the old hardcoded `_SDLC_HOOK_DEFS` path put there.

Sibling issue #2503 (landed as PR #2522, `a419b2777`) removed the last orphaned
*registration* from `~/.claude/settings.json`. That made the orphaned files
inert, but it deliberately deleted no files. They are still on disk on every
machine.

**Current behavior:** `~/.claude/hooks/` carries `validators/` (20 scripts),
`dispatch/`, `hook_utils/` (5 modules), nine top-level scripts, a stray
`manifest.toml`, and several `__pycache__/` trees — none of which any live
registration references, and none of which `/update` will ever clean up. Every
machine accumulates them permanently, and every future reader of that directory
has to work out which half is real.

**Desired outcome:** `/update` self-heals the directory down to the files the
manifest actually declares (plus their runtime import siblings), on every
machine, without a hand-run `rm`. An unrecognized file is left alone.

## Freshness Check

**Baseline commit:** `a85b7cc7c39a74eff225f8d695e01274f81b80e9`
**Issue filed at:** `2026-08-04T05:30:05Z`
**Disposition:** Minor drift — the declared blocker resolved in our favor.

**File:line references re-verified:**

- `~/.claude/settings.json` — issue claimed an unmarked
  `validators/validate_no_raw_redis_delete.py` PreToolUse/Bash entry. **Gone.**
  The live file now registers five commands; the only three touching
  `~/.claude/hooks/` are the marked `sdlc/` forks. #2503's sweep landed.
- `tests/unit/test_hook_migration.py:106` — the foreign
  `python /opt/my-own-tool/guard.py` fixture the acceptance criteria name.
  **Still present**, inside `_write_legacy_settings(..., extra_non_sdlc_hook=True)`.
- `scripts/update/hardlinks.py:78` `RENAMED_REMOVALS` — still present, still
  carries the `("hooks", "sdlc/validate_commit_message.py")` entry proving the
  `"hooks"` kind is wired. See Prior Art for why it is nonetheless the wrong
  vehicle here.
- `.claude/hooks/manifest.toml:274-306` — the three `scope = "global"`
  declarations. Unchanged.

**Cited sibling issues/PRs re-checked:**

- #2503 — **CLOSED** `2026-08-04T11:24:17Z`, merged as PR #2522. This is the
  stated blocker; it is cleared.
- #2435 / PR #2453 — the manifest generator this issue's premise rests on.
  Merged, unchanged.

**Commits on main since issue was filed (touching referenced files):**

- `a419b2777` "Resolve hook interpreter deterministically under stripped
  /bin/sh (#2503)" — **this is the unblocker.** Added
  `_migrate_sweep_legacy_unmarked_global_hooks` and
  `resolve_global_interpreter`; changed no file-level behavior.
- `df6097fe6`, `877720530` — durability-M1 work. Both add migrations to
  `MIGRATIONS`; neither alters the registry contract (`name -> (fn, description)`,
  `fn(project_dir) -> str | None`) this plan depends on. Irrelevant to the
  premise, but they establish the current house style for a migration that
  logs, which Task 1 follows.

**Active plans in `docs/plans/` overlapping this area:**
`generalize-migration-guards-2524.md` (#2524) touches `MIGRATIONS` output
capture. Coordination note, not a blocker: it widens the *value contract* of the
registry, while this plan only adds one more entry conforming to the current
contract. If #2524 lands first, this migration inherits its output capture for
free; if this lands first, #2524 picks up one more entry to generalize.

## Prior Art

- **#2503 / PR #2522** — *SDLC hooks are dead in every foreign repo.* Fixed the
  interpreter token and swept the one orphaned **registration**. Its plan
  critique explicitly carved the **file** prune out into this issue, rating the
  original negative-reference deletion gate "worse than the bug being fixed."
  Directly upstream; its `_migrate_sweep_legacy_unmarked_global_hooks` is the
  structural template this plan copies (precise-match allowlist, `.bak` before
  write, idempotent no-op on a clean machine).
- **#2435 / PR #2453** — introduced `manifest.toml` and the `# hook:<id>`
  marker-keyed generators. Established that the generator is *structurally*
  blind to unmarked/undeclared artifacts, which is the root cause here.
- **`RENAMED_REMOVALS`** (`scripts/update/hardlinks.py:78`) — the fleet-wide
  stale-artifact sweeper the issue body points at as precedent. See
  "Why the obvious vehicle doesn't work" below.

**No prior attempt to prune these files exists.** This is the first.

## Research

Purely internal — no external libraries, APIs, or ecosystem patterns are
involved. The work is a filesystem sweep inside this repo's own update machinery.

No relevant external findings — proceeding with codebase context.

## Spike Results

Three code-read spikes were run against the live machine before drafting. All
three changed the plan.

### spike-1: Are the orphaned files independent copies or hardlinks?

- **Assumption**: "Deleting them is destructive to user-owned data."
- **Method**: code-read + `stat` on the live tree
- **Finding**: They are **hardlinks to live sources in this repo**.
  `~/.claude/hooks/validators/validate_no_raw_redis_delete.py` and
  `.claude/hooks/validators/validate_no_raw_redis_delete.py` share inode
  `5917700`. Unlinking the user-level path leaves the repo source intact.
- **Confidence**: high
- **Impact on plan**: Drops the risk class substantially — content is never
  lost, only a link. For **manifest-declared** files, re-running `/update`
  re-creates the link. For `sdlc/sdlc_context.py` it does not (see the
  Reversibility note under Architectural Impact), so the `.bak` is that one
  file's only recovery path and is not merely belt-and-braces. Recorded in Risks.

### spike-2: Can `RENAMED_REMOVALS` do this job?

- **Assumption**: "`RENAMED_REMOVALS` is the right vehicle — the issue cites it
  as precedent and it already supports the `hooks` kind."
- **Method**: code-read of `_cleanup_renamed` (`scripts/update/hardlinks.py:547-585`)
- **Finding**: **No.** `_cleanup_renamed` is deliberately inode-guarded — the
  `_target_is_hardlinked_to_project(target, src_dir)` call at
  `hardlinks.py:571`. A target still hardlinked to a live source under this
  project is **preserved**, to
  protect a foreign repo that supplies its own same-named user-level artifact.
  Per spike-1, *every* file we want to prune is exactly such a hardlink — so
  `RENAMED_REMOVALS` would preserve all of them. Using it would also mean
  weakening a guard that exists for an unrelated and still-valid reason.
- **Confidence**: high
- **Impact on plan**: Confirms the acceptance criterion's choice of `MIGRATIONS`.
  This is worth stating explicitly because the issue body's own pointer to
  `RENAMED_REMOVALS` as precedent is misleading.

### spike-3: Does any keep-worthy file escape a command-string-derived keep-set?

- **Assumption**: "The declared global scripts are the whole keep-set."
- **Method**: code-read of the three global scripts' imports
- **Finding**: **`sdlc/sdlc_context.py` escapes it.** All three global fork
  scripts import it at runtime (`from sdlc_context import ...` at
  `sdlc/validate_commit_message_sdlc.py:35`, `sdlc/validate_sdlc_on_stop.py:34`,
  `sdlc/sdlc_reminder.py:29`), but it is not itself a manifest declaration, so it
  appears in no command string anywhere. A keep-set derived from command strings
  deletes it and breaks all three global hooks in every foreign repo.
- **Confidence**: high
- **Impact on plan**: `sdlc/sdlc_context.py` is named explicitly in the keep-set
  constant, with a comment tying it to the three importers. This is the concrete
  instance of the unsoundness the issue predicted in the abstract.

**Adjacent gap surfaced by spike-3 (deliberately not fixed here):**
`sync_user_hooks` hardlinks only `decl.script` for global declarations, so it
would not *restore* `sdlc/sdlc_context.py` if it went missing. The file is
present on this machine (inode `1526758`, shared with the repo source) — put
there by the pre-manifest path — but a genuinely fresh machine has a latent
import failure. Filed as #2561; see No-Gos.

## Why the obvious vehicle doesn't work

Summarizing spike-2 for readers who skim: the issue body points at
`RENAMED_REMOVALS` as precedent, and the acceptance criteria point at
`MIGRATIONS`. They conflict, and `MIGRATIONS` is right.
`_cleanup_renamed` (`scripts/update/hardlinks.py:547-585`) preserves anything
hardlinked to a live project source — the guard is the
`_target_is_hardlinked_to_project` call at `hardlinks.py:571`. That describes
every file in scope. Do not "fix" this by relaxing the inode guard — it protects
foreign repos supplying their own same-named user-level skills.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. One new entry in the existing `MIGRATIONS` dict,
  conforming to the current `fn(project_dir) -> str | None` contract.
- **Coupling**: adds a static, hand-maintained inventory of the pre-manifest
  layout. This is intentional and its staleness is harmless — the list names
  historical artifacts, which by definition never grow. A path that drops off
  the list simply stops being pruned.
- **Data ownership**: unchanged.
- **Reversibility**: high, with one named exception. Revert the commit and the
  migration stops running. Re-running `/update` re-creates any wrongly-removed
  hardlink **that the manifest declares** — which is the three global `sdlc/`
  scripts and nothing else. It does **not** restore `sdlc/sdlc_context.py`:
  `sync_user_hooks` (`scripts/update/hardlinks.py:916-936`) hardlinks only
  `decl.script` per declaration, and `sdlc_context.py` has no declaration
  (`grep -c sdlc_context .claude/hooks/manifest.toml` returns 0). Until #2561
  lands, the `.bak` snapshot is that file's **only** recovery path. This is why
  it is named explicitly in the keep-set rather than left to a general rule.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (merge slot)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The one ordering
constraint (#2503 must land first) is already satisfied.

## Solution

### Key Elements

- **`_PREMANIFEST_HOOK_ORPHANS`** — a static, explicit inventory of exactly the
  pre-manifest paths (files and directory trees) that may be removed. Nothing
  outside this list is ever touched.
- **`_USER_HOOK_KEEP_PATHS`** — a static keep-set naming the three
  manifest-declared global scripts plus their runtime import sibling
  `sdlc/sdlc_context.py`. Intersecting the orphan list against this is
  belt-and-braces (the two sets are already disjoint), and it means a future
  edit that mistakenly adds a live path to the orphan list still cannot delete it.
- **Registration guard** — before removing anything, parse
  `~/.claude/settings.json` and refuse to remove any path still named by a
  surviving hook command. Defense in depth, not the primary gate.
- **`_migrate_prune_orphaned_premanifest_hook_files`** — the migration, registered
  in `MIGRATIONS`, that takes a `.bak` snapshot and applies the removal.

### Flow

`/update` → Step 3.6 `run_pending_migrations` → migration reads
`~/.claude/hooks/` → computes removals as
`(orphan inventory ∩ files on disk) − keep-set − paths named in settings.json`
→ if empty, return immediately (no `.bak`, no writes) → else snapshot the whole
tree to `~/.claude/hooks.bak.{UTC timestamp}` → unlink → record complete.

### Technical Approach

- **Every path comparison keys on the path relative to `~/.claude/hooks/`, never
  on the basename.** This is load-bearing, not stylistic: the orphan inventory
  names top-level `sdlc_reminder.py` for deletion while the keep-set names
  `sdlc/sdlc_reminder.py` for preservation. They share a basename and differ
  only in their directory. A basename comparison either spares the orphan or
  deletes the file all three global fork hooks depend on. Concretely, every gate
  computes `rel = str(path.relative_to(hooks_root))` and tests `rel`; no gate
  ever touches `path.name`.
- **Three independent gates, all of which must permit a removal.** A path is
  removed only when it is (a) named in `_PREMANIFEST_HOOK_ORPHANS`, (b) not in
  `_USER_HOOK_KEEP_PATHS`, and (c) not referenced by any command string in
  `~/.claude/settings.json`. Gate (a) alone makes "unrecognized ⇒ kept" the
  default, which is the issue's central design constraint.
- **Gate (c) is permissive whenever it cannot be evaluated — uniformly.** An
  absent `~/.claude/settings.json`, an unreadable one, and a malformed one all
  take the *same* code path: gate (c) contributes no restriction and gates (a)
  and (b) decide alone. Gate (c) is explicitly redundant defense-in-depth, so
  letting it veto the two gates the design calls sufficient would invert the
  safety argument. A read or parse failure is logged at WARNING and the
  migration proceeds; it does not abort.
- **Follow `_migrate_sweep_legacy_unmarked_global_hooks`'s shape.** Same file,
  same module, established and reviewed pattern: precise-match allowlist,
  timestamped `.bak` before any destructive step, early return on nothing-to-do,
  every failure returned as a string rather than raised.
- **Snapshot the whole tree, not per-file backups.** `shutil.copytree` of
  `~/.claude/hooks/` to `~/.claude/hooks.bak.{ts}` is one operation, is trivially
  auditable, and restores the exact prior state. Taken only when there is
  something to remove.
- **Directory removal is bounded to the enumerated trees.** For a tree entry
  (`validators/`, `dispatch/`, `hook_utils/`), each contained file is still run
  through gates (b) and (c) individually; the directory itself is removed only
  once it is empty. A tree containing an unrecognized user file therefore
  survives, holding just that file.
- **`__pycache__` handling.** Top-level `__pycache__/` and the `__pycache__/`
  inside each pruned tree are on the orphan list — they are regenerable
  bytecode for sources being removed. `sdlc/__pycache__/` is **not** on the
  list: it belongs to a kept tree.
- **Idempotency comes from the disk state, not just the migration ledger.** The
  removal set is computed from what is actually present, so a second invocation
  finds nothing and writes nothing — even if the ledger entry were cleared.
- **Never raises.** Wrapped so any unexpected exception returns an error string,
  keeping `/update` alive.

## Failure Path Test Strategy

### Exception Handling Coverage

The migration's outer `try/except Exception` returns the error as a string
(house style for this module — see `_migrate_backfill_pipeline_ledger`). It is
not `except: pass`: the string surfaces in `MigrationResult.errors` and the
migration stays pending, so it retries on the next `/update`.

- [ ] Test that a malformed `~/.claude/settings.json` logs a WARNING, leaves
      gate (c) permissive, and lets gates (a)+(b) proceed normally — the same
      observable outcome as the absent-file case. Asserting these two produce
      *identical* removal sets is the regression guard against the two postures
      drifting apart.

### Empty/Invalid Input Handling

- [ ] Absent `~/.claude/hooks/` → returns `None`, writes nothing, no `.bak`.
- [ ] Present but already-pruned tree → returns `None`, writes nothing, no `.bak`.
- [ ] Absent `~/.claude/settings.json` → treated as "no registrations", gate (c)
      permits; gates (a) and (b) still apply.
- [ ] Both `sdlc_reminder.py` and `sdlc/sdlc_reminder.py` present → the
      top-level one is removed, the `sdlc/` one survives.

### Error State Rendering

- [ ] A `.bak` that cannot be written aborts before any unlink and returns the
      OS error — the tree is never left half-pruned with no snapshot.

## Test Impact

- [ ] `tests/unit/test_hook_migration.py` — UPDATE: add a class of tests for the
      new migration. The existing `_write_legacy_settings` /
      `extra_non_sdlc_hook` fixtures are reused, not modified.
- [ ] `tests/unit/test_hook_migration.py:106` — UNCHANGED: the foreign
      `python /opt/my-own-tool/guard.py` fixture must keep passing untouched.
      Asserted, not assumed (acceptance criterion 4).

No other existing tests are affected — the migration adds a new registry entry
and touches no shared code path.

## Rabbit Holes

- **Transitive AST import walking.** Explicitly ruled out by the issue. One real
  import edge exists (`sdlc_context.py`); it is enumerable by hand.
- **Making the orphan list "self-maintaining"** by diffing the user tree against
  the manifest. That inverts the default back to delete-by-default and reopens
  exactly the unsoundness #2503's critique rejected.
- **Fixing `sync_user_hooks` to sync import siblings.** Real, adjacent, separately
  filed. Doing it here doubles the blast radius of a cleanup commit.
- **Relaxing `_cleanup_renamed`'s inode guard** (`hardlinks.py:571`) to make
  `RENAMED_REMOVALS` usable. The guard protects an unrelated, still-valid case.
- **Documenting the stale trees instead of deleting them** — a README marker in
  `~/.claude/hooks/` saying "these are dead". Cheapest option and worth naming
  explicitly so it is rejected on the merits rather than overlooked: a README
  does not stop tooling, greps, or future agents from enumerating the dead
  trees, and it does nothing about the `__pycache__/` bytecode, which can still
  shadow an import. The harm is legibility plus a live shadowing hazard, and
  only deletion addresses the second.

## Risks

### Risk 1: The static orphan inventory is wrong about some path

**Impact:** A file that something still needs gets unlinked from
`~/.claude/hooks/`.
**Mitigation:** Three independent gates, the narrowest of which (the explicit
inventory) was built from the live tree on this machine and cross-checked
against the manifest. Beyond that: every file in scope is a hardlink to a repo
source (spike-1), so the content is not lost; the full-tree `.bak` restores byte
state; and `/update` re-creates any manifest-declared file on its next run.

### Risk 2: A machine whose `~/.claude/hooks/` has drifted from this one's

**Impact:** The inventory under-matches and leaves orphans behind on that
machine.
**Mitigation:** Accepted, by design. Under-pruning is the safe direction and is
the deliberate consequence of "unrecognized ⇒ kept". A machine with extra
pre-manifest debris keeps it, inert, until the inventory is extended.

### Risk 3: Removing `manifest.toml` from the user tree confuses a reader or tool

**Impact:** Something reads `~/.claude/hooks/manifest.toml` expecting the
manifest.
**Mitigation:** Verified nothing does — `load_hook_manifest` is called only with
`project_dir / ".claude" / "hooks" / "manifest.toml"` (`hardlinks.py:258,841,887`
and `migrations.py:780`). The user-tree copy is a stale artifact, and its
staleness is itself a hazard worth removing.

## Race Conditions

No race conditions identified. `run_pending_migrations` executes serially within
a single `/update` invocation, and `/update` is not run concurrently against the
same home directory. The migration's removal set is recomputed from live disk
state on every run, so even a concurrent `sync_claude_dirs` (which only *creates*
hardlinks for manifest-declared scripts, all of which are in the keep-set) cannot
cause it to delete something that step just created.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2521] Nothing deferred within this issue's own scope — the
  full prune, the keep-set, the `.bak`, and the tests are all in scope for this
  plan.
- [SEPARATE-SLUG #2561] `sync_user_hooks` does not hardlink the runtime import
  siblings of global-scope scripts, so `sdlc/sdlc_context.py` would not be
  restored on a fresh machine. Surfaced by spike-3; a generator change, not a
  prune change.

## Update System

This work **is** an update-system change: it adds one entry to
`scripts/update/migrations.py::MIGRATIONS`, which `/update` Step 3.6 runs
automatically. No new dependencies, no new config files, no operator action.
Propagation to the fleet is the normal `/update` path — that is the entire point
of putting the prune here rather than in a one-off `rm`.

## Agent Integration

No agent integration required — this is update-machinery-internal. No MCP
surface, no bridge wiring, no new tool.

## Documentation

- [ ] Update `docs/features/hook-manifest.md` with a short "User-tree hygiene"
      subsection: what the generator does and does not own under
      `~/.claude/hooks/`, and that a one-time migration prunes the pre-manifest
      residue.
- [ ] Note in that same subsection that `sdlc/sdlc_context.py` is a runtime
      import sibling with no manifest declaration of its own, so any future
      keep-set or sweep must account for it. This is the durable form of
      spike-3's finding.
- [ ] Inline: the two module-level constants carry comments explaining the
      delete-by-inventory / keep-by-default posture and the `sdlc_context.py`
      import edge.

No `docs/features/README.md` index entry — `hook-manifest.md` is already indexed.

## Success Criteria

- [ ] Orphaned pre-manifest files under `~/.claude/hooks/` are removed by an
      idempotent migration registered in `MIGRATIONS`
- [ ] The removal inventory is static; a file under `~/.claude/hooks/` that is
      not on it is NOT deleted — asserted by test
- [ ] `sdlc/sdlc_context.py` and the three manifest-declared global scripts
      survive — asserted by test
- [ ] Gates compare paths relative to `~/.claude/hooks/`, not basenames:
      top-level `sdlc_reminder.py` is removed while `sdlc/sdlc_reminder.py`
      survives when both are present — asserted by test
- [ ] A timestamped `.bak` snapshot is taken before any destructive operation —
      asserted by test
- [ ] `tests/unit/test_hook_migration.py:106`'s foreign
      `python /opt/my-own-tool/guard.py` fixture still survives
- [ ] Running the migration twice is a no-op on the second run (no writes, no
      second `.bak`) — asserted by test
- [ ] Tests pass (`/do-test`, focused on `tests/unit/test_hook_migration.py`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

One agent. This is a Small chore — one constant pair, one function, one test
class — following an existing reviewed pattern in the same file. Splitting build
from tests would buy no parallelism (the tasks are strictly sequential) and no
existing `MIGRATIONS` entry needed that choreography. The SDLC Test and Review
stages already supply the independent read.

- **Builder (migration + tests)**
  - Name: `hook-prune-builder`
  - Role: implement the constants, the migration, its registration, and the
    test class, in one pass
  - Agent Type: builder
  - Resume: true

## Step by Step Tasks

### 1. Implement the constants, the migration, and its tests

- **Task ID**: build-migration
- **Depends On**: none
- **Validates**: `tests/unit/test_hook_migration.py`
- **Informed By**: spike-1 (hardlinks, not copies — recovery is cheap), spike-2
  (`MIGRATIONS`, not `RENAMED_REMOVALS`), spike-3 (`sdlc_context.py` must be in
  the keep-set)
- **Assigned To**: `hook-prune-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `_PREMANIFEST_HOOK_ORPHANS` to `scripts/update/migrations.py`: the
  explicit inventory of removable paths relative to `~/.claude/hooks/` —
  trees `validators/`, `dispatch/`, `hook_utils/`, `__pycache__/`; files
  `format_file.py`, `hook_python`, `manifest.toml`, `post_compact.py`,
  `post_tool_use.py`, `pre_tool_use.py`, `sdlc_reminder.py`, `stop.py`,
  `subagent_stop.py`, `user_prompt_submit.py`.
- Add `_USER_HOOK_KEEP_PATHS`: `sdlc/validate_commit_message_sdlc.py`,
  `sdlc/sdlc_reminder.py`, `sdlc/validate_sdlc_on_stop.py`,
  `sdlc/sdlc_context.py` — with a comment naming the three importers of
  `sdlc_context.py` and stating that it carries no manifest declaration.
- Implement `_migrate_prune_orphaned_premanifest_hook_files(project_dir)`
  applying all three gates, taking the full-tree `.bak` only when the removal
  set is non-empty, and returning an error string (never raising) on failure.
- **Compare on `str(path.relative_to(hooks_root))` in every gate — never on
  `path.name`.** Top-level `sdlc_reminder.py` (remove) and
  `sdlc/sdlc_reminder.py` (keep) share a basename; a basename comparison breaks
  one of the two.
- Gate (c) is permissive on absent, unreadable, AND malformed
  `~/.claude/settings.json`, via one shared code path. Log at WARNING on read or
  parse failure; do not abort the migration.
- Register it in `MIGRATIONS` as `prune_orphaned_premanifest_hook_files` with a
  description naming issue #2521.
- In the same pass, add the test class to `tests/unit/test_hook_migration.py`,
  reusing the existing `fake_home` fixture (`tests/unit/test_hook_migration.py:35`):
  - Assert every inventory path is removed and all four keep-set files survive.
  - **Assert the basename collision explicitly:** with BOTH `sdlc_reminder.py`
    and `sdlc/sdlc_reminder.py` present, the top-level one is removed and the
    `sdlc/` one survives. This is the highest-value test in the set.
  - Assert an unrecognized file (`validators/my_own_validator.py`, and a
    top-level `my_notes.md`) survives, and its containing directory survives
    with it.
  - Assert a `~/.claude/hooks.bak.*` snapshot exists after the first run and
    contains the removed files.
  - Assert a second run removes nothing, writes no second `.bak`, returns `None`.
  - Assert absent hooks dir and absent settings.json are clean no-ops, and that
    malformed settings.json yields the SAME removal set as absent.
  - Assert a path named by a surviving `settings.json` command is NOT removed
    even when it is on the inventory.
  - Confirm the existing `extra_non_sdlc_hook` foreign-hook test still passes
    untouched.

### 2. Validation

- **Task ID**: validate-all
- **Depends On**: build-migration
- **Assigned To**: `hook-prune-builder`
- **Agent Type**: builder
- **Parallel**: false
- Run the focused test file serially and report results.
- Run `ruff check` and `ruff format --check` on the two touched files.
- Re-read the live `~/.claude/hooks/` inventory and confirm the constant covers
  it with no live path caught.
- Verify every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Focused tests pass | `scripts/pytest-clean.sh tests/unit/test_hook_migration.py -n0 -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update/migrations.py tests/unit/test_hook_migration.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update/migrations.py tests/unit/test_hook_migration.py` | exit code 0 |
| Migration registered | `python -c "from scripts.update.migrations import MIGRATIONS; print('prune_orphaned_premanifest_hook_files' in MIGRATIONS)"` | output contains True |
| Keep-set names sdlc_context | `grep -c "sdlc/sdlc_context.py" scripts/update/migrations.py` | output > 0 |
| Foreign-hook fixture intact | `grep -c "/opt/my-own-tool/guard.py" tests/unit/test_hook_migration.py` | output > 0 |
| Anti-criterion: no AST import walk | `! grep -qE '^[[:space:]]*import ast' scripts/update/migrations.py` | exit code 0 |
| Anti-criterion: prune not routed through RENAMED_REMOVALS | `! grep -q '("hooks", "validators\|("hooks", "hook_utils\|("hooks", "dispatch\|("hooks", "__pycache__' scripts/update/hardlinks.py` | exit code 0 |
| Anti-criterion: no basename comparison in the prune | `! grep -n 'path.name\|\.name in _PREMANIFEST\|\.name in _USER_HOOK' scripts/update/migrations.py` | exit code 0 |

Both anti-criterion rows use the `! grep -q` form rather than `grep -c ... == 0`.
`grep -c` prints `0` but *exits 1* on a zero-count match, so an `&&`-chained or
`set -e` runner misreports the intended pass as a command failure. The negated
`grep -q` form exits 0 exactly when the forbidden pattern is absent.

## Critique Results

**Critique pass 2026-08-06, against plan baseline `984d3bb7f`.** Depth: FULL
(triage: fleet-wide destructive filesystem operation on `~/.claude/hooks/`, a
doctrine path). Critics: Risk & Robustness, Scope & Value, History &
Consistency, plus driver structural checks and independent source verification.
Roster gate: 3/3 complete, 3/3 grounded.

Two critic findings asserting the live `~/.claude/hooks/` tree was empty were
**discarded** as artifacts of a truncated directory listing in the critic source
bundle. The driver re-verified the live tree directly: it contains `validators/`
(20 scripts), `dispatch/`, `hook_utils/` (5 modules + `__init__.py`), the nine
top-level scripts, `manifest.toml`, and the `__pycache__/` trees — matching the
Problem section and Task 1's inventory exactly.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness (Adversary) | The orphan inventory names top-level `sdlc_reminder.py` for deletion while the keep-set names `sdlc/sdlc_reminder.py` for preservation — same basename, different relative path — but the plan never states that the gate comparison keys on the full path relative to `~/.claude/hooks/` rather than on filename. A naive basename match either spares the orphan or deletes the keep-listed file that all three global fork hooks transitively depend on. | Technical Approach now mandates `rel = str(path.relative_to(hooks_root))` in every gate and forbids `path.name`; Task 1 repeats it; a dedicated test with both files present is a Success Criterion and a Verification anti-criterion row | All three gates must compare `str(path.relative_to(hooks_root))`, never `path.name`. Concretely: `rel = str(p.relative_to(hooks_root))` then `if rel in _USER_HOOK_KEEP_PATHS: continue`. Add a unit test with BOTH `sdlc_reminder.py` and `sdlc/sdlc_reminder.py` present simultaneously, asserting the first is removed and the second survives. |
| CONCERN | Risk & Robustness (Operator) | `## Architectural Impact` > Reversibility claims without qualification that re-running `/update` re-creates any wrongly-removed hardlink. Verified false for `sdlc/sdlc_context.py`: `sync_user_hooks` (`scripts/update/hardlinks.py:916-925`) hardlinks only `decl.script` for `scope == "global"` declarations, and `grep -c sdlc_context .claude/hooks/manifest.toml` returns 0. This contradicts the plan's own No-Go #2561. | Reversibility rewritten to 'manifest-declared paths only', naming `sdlc/sdlc_context.py` as recoverable ONLY via the `.bak` until #2561 lands, with the `hardlinks.py:916-936` citation | Narrow the Reversibility claim to "manifest-declared paths only". `sdlc/sdlc_context.py`'s ONLY recovery path is the `.bak` snapshot until #2561 lands — state this explicitly in Reversibility and cross-reference the #2561 No-Go, not just Risk 1's mitigation prose. |
| CONCERN | Scope & Value (Simplifier) | Gate (c) is framed as "Defense in depth, not the primary gate", yet `## Failure Path Test Strategy` makes a malformed `~/.claude/settings.json` fail closed for the ENTIRE migration, while an ABSENT one is treated permissively. A non-primary, admittedly-redundant gate should not be able to block gates (a)+(b), which the plan calls sufficient on their own. | Resolved toward permissive. Absent, unreadable, and malformed settings.json now share one code path; gate (c) can never veto gates (a)+(b). Test asserts malformed and absent yield identical removal sets | Decide one posture and state it. Either: on `json.JSONDecodeError`/`OSError` reading `~/.claude/settings.json`, treat gate (c) as permissive via the SAME code path as the absent-file case (gates (a)+(b) still apply); or keep fail-closed and justify why a malformed file is more dangerous than a missing one. Do not ship both postures. |
| CONCERN | History & Consistency (Consistency Auditor) | spike-2 and `## Why the obvious vehicle doesn't work` cite a function `_remove_renamed` at `scripts/update/hardlinks.py:548-556`. Verified: that name has zero hits in the repo. The real function is `_cleanup_renamed`, `def` at `hardlinks.py:547`; lines 548-556 are mid-docstring, and the inode guard is the `_target_is_hardlinked_to_project(target, src_dir)` call at `hardlinks.py:571`. | All three citations corrected to `_cleanup_renamed` (`hardlinks.py:547-585`), guard at `hardlinks.py:571` | Correct all three citations (spike-2 Method line, "Why the obvious vehicle doesn't work", `## Rabbit Holes`) to `_cleanup_renamed` (`scripts/update/hardlinks.py:547-585`), citing line 571 where the guard logic itself is meant. The spike's CONCLUSION is correct and unchanged — only the identifier is wrong. |
| CONCERN | Scope & Value (Simplifier) | `## Team Orchestration` declares three named agents for an appetite:Small chore the plan itself scopes as one constant + one function + one test class, following an existing reviewed pattern. Tasks 1 and 2 are strictly sequential (`Depends On: build-migration`), so the split buys no parallelism. None of the 15+ existing `MIGRATIONS` entries (`scripts/update/migrations.py:967-1042`) needed this choreography. | Folded to a single `builder` task writing both files; the separate test-engineer and validator agents are gone. Validation is now a step of the same agent | Fold `build-tests` into `build-migration` under a single `builder` task writing both `scripts/update/migrations.py` and `tests/unit/test_hook_migration.py` in one pass. Keep `hook-prune-validator` only if the live-tree dry check genuinely needs an independent reader; the SDLC Test stage already provides test review. |
| NIT | History & Consistency | The `## Verification` table's two anti-criterion rows use `grep -c ... == 0`. Verified on this machine: `grep -c` prints `0` but EXITS 1 on a zero-count match. Wired into a `&&`-chained or `set -e` runner, the intended "pass" case is misreported as a command failure. | Both rows converted to the `! grep -q` form with `exit code 0`, plus a note in the Verification section explaining why `grep -c` is unsafe here | Append `\|\| true` to both anti-criterion commands, or annotate the table that these two rows are evaluated by printed output, not exit code. |
| NIT | Scope & Value (User) | The stated harm is purely legibility, yet `## Rabbit Holes` rejects four alternatives without naming the cheapest one: documenting the stale paths (e.g. a README marker) instead of deleting them. | Rabbit Holes now rejects the README/document-only alternative explicitly, on the grounds that it leaves `__pycache__` bytecode able to shadow imports | Add one sentence to `## Rabbit Holes` rejecting the documentation-only alternative (e.g. a README does not stop tooling from enumerating dead trees, and the `__pycache__` bytecode still shadows imports). Low severity — spike-1 already shows the delete is hardlink-backed and cheap. |

**Structural checks:** all four required sections present and substantive
(`## Documentation` carries a `docs/features/hook-manifest.md` checkbox;
`## Update System` names `scripts/update/migrations.py`; `## Agent Integration`
explicitly N/A with rationale; `## Test Impact` carries dispositions). Task
numbering 1-3 contiguous, all `Depends On` references resolve, no cycles, every
task has a validation command. All referenced repo file paths exist. The one
prerequisite (#2503) is verified CLOSED/merged. Success criteria all map to
tasks; No-Gos and Rabbit Holes do not reappear as planned work. Sole structural
defect is the `_remove_renamed` identifier, recorded above.

---

## Open Questions

None remaining. Critique returned READY TO BUILD with 0 blockers; all 5 concerns
and 2 nits are embedded above with Implementation Notes. The issue's design
constraint (static allowlist, no AST walk) is explicit,
its blocker has landed, and the three spikes resolved every assumption the
approach rested on.
