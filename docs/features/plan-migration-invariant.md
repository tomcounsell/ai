# Plan Migration Invariant

**Issue:** [#1900](https://github.com/tomcounsell/ai/issues/1900)
**Status:** Shipped (Tier 0 — PR 1 of a two-PR delivery)
**Related features:** [Reflections](reflections.md) · [Plan Checkbox Writers](plan-checkbox-writers.md)

## Why

`docs/plans/` root is supposed to hold only in-flight plans. Once a plan's
tracking issue closes, the plan should move to `docs/plans/completed/`. Before
this work, that migration was **prose** — `docs/sdlc/do-merge.md` instructed
the agent to hand-`git mv` the plan on `main` after merge — and prose is not
an invariant. 212 plans had accumulated in root (13MB), including plans whose
tracking issue had been closed for months: every merge path that didn't
faithfully execute that hand instruction (a raw-terminal `gh pr merge`, a
forked `/do-sdlc` run, a cross-machine merge) leaked a plan into root forever.

## What this ships

One guarded primitive, called from two independent sites, so no single missed
step leaves a plan stranded.

### The primitive: `migrate_plan_to_completed()`

`scripts/migrate_completed_plan.py::migrate_plan_to_completed(plan_path, *, apply)`
is the **only** code that moves a plan out of root. It:

- Resolves repo layout from the plan's own path (works regardless of the
  caller's process `cwd`).
- Guards existence: if the plan is already absent from root, returns
  `"already-migrated"` rather than treating a second attempt as failure
  (`git mv` is not idempotent on its own).
- Requires `HEAD == main` and a clean working tree before doing anything. If
  either check fails, it takes a **report-only fallback**: logs which plan it
  *would* migrate, mutates nothing, and returns `"dirty-tree-skip"`. This
  never mutates a checkout another session is using.
- On a real move, commits and pushes with a bounded rebase-retry loop: a
  losing push (another process won the race to `main`) replays via
  `git pull --rebase && git push`, retried up to 3 times. A genuine textual
  rebase conflict (not just a non-fast-forward rejection) aborts the rebase,
  leaves the tree clean, and returns `"rebase-conflict-skip"` — it never
  resolves a conflict unattended.

Two thin CLIs wrap it:

```bash
python scripts/migrate_completed_plan.py --issue <N> [--apply|--dry-run]
python scripts/migrate_completed_plan.py --sweep [--apply] [--cap N]
```

`--issue N` resolves the one root plan whose `tracking:` frontmatter matches
issue `N` (never a branch-slug guess — slug and plan filename frequently
differ) and migrates it. `--sweep` iterates every root plan and migrates the
ones whose tracking issue is closed, with an optional cap.

### Site D — the deterministic primary path

`docs/sdlc/do-merge.md`'s Plan Migration section instructs running, on `main`
after every merge:

```bash
python scripts/migrate_completed_plan.py --issue <closed-issue-number> --apply
```

This is issue-keyed, so it always finds the right plan even when the branch
slug doesn't match the plan's filename. This is the primary path — every
`/do-merge`-driven merge migrates its plan synchronously, in the same
transaction as the merge.

### Site C — the path-independent backstop

`reflections/housekeeping/merged_branch_cleanup.py::run()` already swept
`docs/plans/` root daily, extracted issue references, and classified a
`closed_issue` finding — it just reported instead of acting. It is now
extended to call `migrate_plan_to_completed()` on that branch, so it catches
every plan orphaned by a merge that bypassed `/do-merge` entirely (a
raw-terminal `gh pr merge`, a forked `/do-sdlc` run, a cross-machine merge)
within one daily cycle. No net-new reflection was added — extending the
existing sweep (which already reads, extracts, and classifies) keeps one
mechanism, not two.

The reflection's migration gate is **evidence-gated and non-vacuous**:

- It reads the plan's **own** `tracking:` frontmatter issue — never the
  broader prose scan of every issue number the plan happens to mention — so a
  plan that merely references a closed *sibling* issue is never swept.
- It is evaluated **independent of `is_complete`** (the all-checkboxes-ticked
  check). Previously the `is_complete` branch `continue`d before the
  `closed_issue` branch ever ran, so an all-checkboxes-complete plan with a
  closed tracking issue could never migrate — about 34 of the 212 leaked
  plans were in exactly this state.
- It requires a **literal `"closed"`** state for that one issue. The old
  `check_issue_state()` returns `"unknown"` on any `gh` timeout/exception, and
  the previous gate (`all(s == "closed" for s in states if s != "unknown")`)
  was vacuously `True` when every state was `"unknown"` — a transient `gh`
  outage could have `git mv`'d an ACTIVE plan. `"unknown"` now always defers;
  it never migrates.

### Unattended-safety posture

The reflection runs daily with no human in the loop, so it is:

- **Apply-gated.** `MIGRATION_APPLY_ENABLED` in `merged_branch_cleanup.py` was
  armed to `True` only after the evidence-gate regression test proved both
  fixes above, in a discrete `arm-reflection` task that depends on
  `validate-tier0` passing — arming never rides silently inside the mechanism
  build.
- **Capped.** `MIGRATION_PER_RUN_CAP = 10` bounds how many plans one daily run
  can move; the remainder is deferred and logged for the next cycle. This cap
  applies only to the unattended reflection — the one-time Tier 1 historical
  backfill (`--sweep --apply`, PR 2) is a separate, human-supervised,
  uncapped pass over the same primitive.
- **Alerting.** Every migration, skip, and failure is logged and folds into
  the reflection's summary line, which the reflection scheduler surfaces
  through its normal channel — a run that migrates or fails is never silent.

### Why the registry flip needed its own update-system step

`config/reflections.yaml` is gitignored: it's an install-time copy of the
iCloud vault source (`~/Desktop/Valor/reflections.yaml`), refreshed by
`env_sync.sync_reflections_yaml()` on every `/update`. A commit that edits
only the in-repo copy would be silently clobbered the next time that copy
step runs, and a machine that has never synced the vault copy would never see
the repo edit at all. `scripts/update/reflection_arm.py` closes that gap: a
guarded `/update` step (vault file must exist; this machine must own the
`valor` project per `config/projects.json`, mirroring
`tools.reflection_machine_filter`'s ownership model) flips
`merged-branch-cleanup`'s `enabled` to `True` in **both** the vault file and
the in-repo copy, then reloads the reflection-worker subprocess so the change
takes effect immediately.

## Verified by

- `tests/unit/test_plan_migration_invariant.py` — the Tier 0 regression test:
  a merged issue's plan is not left in root after migration, plus static
  assertions that the enforcement wiring exists (`merged-branch-cleanup`
  registered with `enabled: true`, its `closed_issue` branch calls
  `migrate_plan_to_completed`, `do-merge.md` carries the deterministic
  `--issue` call).
- `tests/unit/test_migrate_completed_plan.py` — the primitive itself: closed
  → migrated, open → skipped, already-migrated → idempotent, `git mv` failure
  → plan preserved, dirty-tree/non-main → report-only fallback.
- `tests/unit/reflections/test_merged_branch_cleanup.py` — the extended
  reflection branch: both evidence-gate fixes (the `is_complete` short-circuit
  no longer hides closed-issue plans; a `gh` `"unknown"` never migrates), the
  per-run cap, and apply-off/report-only behavior.
- `tests/unit/test_reflection_arm.py` — the update-system arming step:
  ownership-gated flip of both the vault and repo copies, no-op when already
  enabled, fail-closed when the machine doesn't own the project or the vault
  file is absent.

## Not in this PR

Tier 1 (the ~130-plan historical backfill via `--sweep --apply`), Tier 2
(de-duplicating diverged docs), Tier 3 (removing one-off decks), and Tier 4
(vendor docs to the knowledge base) ship in a separate PR, branched from
`main` after this one merges — see issue #1900's plan for the full four-tier
scope.
