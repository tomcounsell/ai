---
slug: generalize-migration-guards-2524
status: Ready
type: chore
appetite: Small
tracking: https://github.com/yudame/ai/issues/2524
---

# Generalize the migration zero-record guard and guarded index repair to sibling strip migrations

## Problem

`scripts/migrate_strip_pid_fields.py` was hardened by #2518 / PR #2538 with two
properties its two siblings still lack:

1. A `total_records == 0` guard. An empty scan is indistinguishable from a scan
   blinded by popoto's index-rebuild class-set window (#1720,
   `agent/index_drift.py:1-12`). Without the guard, a blinded run exits 0,
   `run_pending_migrations` records the migration complete permanently, and the
   stale fields are never reclaimed on that machine.
2. A production-safe trailing index sweep. The siblings call popoto's raw
   `AgentSession.rebuild_indexes()`, which is the call that *opens* the #1720
   window and which currently fails outright with `unpack(b) received extra
   data` on pre-existing phantom index metadata (#2536).

The parent plan already names this exactly
(`docs/plans/durability-m1-fence-canary.md:431`): *"Both call unguarded
`rebuild_indexes()` and neither is tested. #2516 copied the template faithfully,
inheriting both traits."*

The deeper problem is that all three scripts are near-verbatim clones. The guard
was fixed in one copy; the other two drifted. Fixing them by copy-paste
reproduces the failure mode that created this issue.

## Appetite

Small. This is a deduplication plus two registry entries, not a redesign.

## Freshness Check

Baseline commit: `984d3bb7f` (main, 2026-08-06).

| Reference | Verified | Disposition |
|---|---|---|
| `scripts/migrate_strip_pty_fields.py:161` | `AgentSession.rebuild_indexes()` present at line 161. | Unchanged |
| `scripts/migrate_schema_diet_fields.py:230` | `AgentSession.rebuild_indexes()` present at line 230. | Unchanged |
| `scripts/migrate_strip_pid_fields.py` | Carries the zero-record guard (`:182`) and `clean_indexes()` (`:224`). | Unchanged |
| #2518 (parent) | Still OPEN. Its plan is active on main. | Unchanged |
| #2536 | Open; `rebuild_indexes()` failure mode confirmed as the reason to prefer `clean_indexes()`. | Unchanged |
| `git log` on both sibling scripts | Last touched by `1a23e1e8b` (#2046) and `e8351e4ca` (#1930) — no drift since filing. | Unchanged |

**Overall: Unchanged.** No line-number drift, no overlapping active plan.

## Canary Evidence (the reclaim-vs-accept input)

Dry-run of both siblings against live Redis on host *Valor the Pirate*
(read-only, no `--apply`):

```
migrate_strip_pty_fields:   {'total_records': 4004, 'clean': 4004, 'stripped': 0,
                             'deferred_non_terminal': 0, 'errors': 0}
migrate_schema_diet_fields: {'total_records': 4004, 'clean': 4004, 'stripped': 0,
                             'deferred_non_terminal': 0, 'errors': 0}
```

Both siblings are genuinely clean here — 4004 records scanned (so not a blinded
zero-scan), zero carrying stale fields. This host needs no reclaim. It says
nothing about the other fleet machines, which is the whole point: the canary
machine's `strip_pid_fields` was *recorded complete while 20 terminal records
still carried all four stale fields*.

## Solution

### Decision 1 — reclaim both siblings (rename + rerun)

Register `strip_pty_session_fields_v2` and `schema_diet_fields_v2` pointing at
the same helper functions, exactly as #2518 did with `strip_pid_fields_v2`.

Rationale: "recorded complete" has been demonstrated to not imply "actually
did the work". The scripts are idempotent, so the cost on a clean machine is one
extra scan of ~4k records (seconds) producing a captured log line that proves
cleanliness. The cost of *not* re-running is that a machine in the canary's
state keeps its orphaned fields forever with no signal. The asymmetry decides it.

This is a registry-name change only — no hand-editing of
`data/migrations_completed.json`.

### Decision 2 — one implementation, three thin scripts

Extract `scripts/_strip_migration.py` holding the single copy of:

- `raw_field_names(instance)` — detection-only `HKEYS` read of field *names*.
- `run_strip_migration(...)` — the scan / terminal-only atomic rewrite /
  zero-record guard / `clean_indexes()` sweep.
- `strip_migration_main(...)` — argparse, mode banner, stats line, exit codes
  (2 = zero-record guard fired, 1 = per-record errors, 0 = clean).

Each of the three scripts keeps only its module docstring (they document
genuinely different field sets and different plan history — that content is not
duplication) and its `STALE_FIELDS` frozenset, then delegates.

`logging.basicConfig(..., stream=sys.stdout)` moves into all three: the stdout
targeting is load-bearing for the output capture, and today only the pid script
has it.

### Decision 3 — generalize subprocess output capture

`_migrate_strip_pid_fields`'s docstring points at this issue: *"generalizing
capture to every helper needs the MIGRATIONS value contract widened to carry
output, which is tracked separately (#2524)."*

In fact no contract widening is needed. The helper logs through the module
logger rather than returning output, so a shared
`_run_migration_script(project_dir, script_name, label, timeout, args)` in
`scripts/update/migrations.py` gives every subprocess-shaped migration the same
both-streams logging and the same both-tails error string, with the return
contract untouched (`None` | error string).

Applies to: `agent_session_keyfield_rename`, `unify_parent_session_field`,
`steering_queue_drain`, `strip_pty_session_fields`, `schema_diet_fields`,
`strip_pid_fields`. Not `purge_phantom_agent_sessions` — it has a distinct exit
code 3 contract and its own time budget.

## No-Gos (Out of Scope)

**Do not change the zero-record guard's fail-closed behavior.** #2518
deliberated and accepted its consequence: on a genuinely empty keyspace (a fresh
install) the migration fails on every `/update`, forever, and the recurring
`FAIL:` line is expected output. Registering two more v2 migrations multiplies
that from one recurring failure to three on a fresh machine.

That is worth fixing — a detection-only `SCAN` for `AgentSession:*` key *names*
would distinguish "genuinely empty keyspace" (succeed) from "index-rebuild
window blinded the query" (fail closed) — but it reverses a decision made in the
parent issue's critique. Relitigating it inside a generalization PR is the wrong
venue. **File a follow-up issue instead**, and carry the accepted-consequence
documentation through to the shared helper unchanged.

## Step by Step Tasks

1. **Create `scripts/_strip_migration.py`.** Move `raw_field_names`, the scan
   loop, the zero-record guard (verbatim comment text, including the
   `INSURANCE` and `ACCEPTED CONSEQUENCE` markers), the `clean_indexes()` sweep,
   and the CLI/exit-code main out of `migrate_strip_pid_fields.py`. The
   `field_names` detection function is injectable so a caller's module-level
   name stays patchable in tests.

2. **Rewrite `scripts/migrate_strip_pid_fields.py` as a thin delegate.** Keep
   its docstring and `STALE_FIELDS` verbatim. Keep module-level
   `_raw_field_names` and `migrate` names (existing tests bind to them).

3. **Rewrite `scripts/migrate_strip_pty_fields.py` as a thin delegate.** Adds
   the zero-record guard, swaps `rebuild_indexes()` → `clean_indexes()`, adds
   `stream=sys.stdout`. Update the docstring's false quiescence claim ("the
   worker never writes terminal rows") the same way #2518 corrected the pid
   script's — `cleanup_corrupted_agent_sessions` re-saves terminal rows.

4. **Rewrite `scripts/migrate_schema_diet_fields.py` as a thin delegate.** Same
   three changes and the same docstring correction. Its long field-disposition
   docstring is preserved verbatim.

5. **Add `_run_migration_script()` to `scripts/update/migrations.py`** and route
   the six subprocess-shaped helpers through it.

6. **Register `strip_pty_session_fields_v2` and `schema_diet_fields_v2`** in
   `MIGRATIONS`, with a comment matching `strip_pid_fields_v2`'s explaining why
   a rename is the auditable re-run mechanism.

7. **Fix the stale assertions in `tests/unit/test_migrate_strip_pid_fields.py`.**
   `TestGuardedIndexRepair` patches `AgentSession.repair_indexes`, but hotfix
   `369d782c8` changed the call to `clean_indexes()` — so
   `repair.assert_not_called()` passes vacuously and
   `test_repair_failure_does_not_raise` exercises nothing. Repoint at
   `clean_indexes`. Repoint the `inspect.getsource` assertions at the module
   that now holds the code.

8. **Add `tests/unit/test_strip_migration_shared.py`** covering the shared
   helper once: zero-record guard fires / logs / exits 2 / skips the index
   sweep; per-record isolation; `clean_indexes()` only on `stripped > 0` and
   never raising; and — parametrized across all three scripts — that no script
   references `rebuild_indexes` and every script targets stdout.

9. **Add a registry test** asserting both `_v2` names are registered and point
   at the same functions as their `_v1` counterparts.

10. **Ruff check + format; focused pytest runs.**

## Success Criteria

- `grep -rn 'rebuild_indexes' scripts/migrate_*.py` → zero matches.
- All three strip scripts fail closed (exit 2) on a zero-record scan.
- The zero-record guard text exists in exactly one file.
- `MIGRATIONS` contains `strip_pty_session_fields_v2` and `schema_diet_fields_v2`.
- `tests/unit/test_migrate_strip_pid_fields.py` and the two new test files pass.
- No behavior change to the pid migration beyond where its code lives.

## Test Impact

- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestGuardedIndexRepair::test_repair_runs_only_when_something_was_stripped` — UPDATE: patch `AgentSession.clean_indexes`, not `repair_indexes`. The current target is vacuous since hotfix `369d782c8`.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestGuardedIndexRepair::test_repair_failure_does_not_raise` — UPDATE: same repoint. Today it injects a `side_effect` on a method the script never calls, so it exercises nothing.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestZeroRecordGuard::test_zero_records_does_not_touch_indexes` — UPDATE: same repoint.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestZeroRecordGuard::test_the_accepted_unbounded_retry_is_documented_in_code` — UPDATE: `inspect.getsource` must target the shared helper, which is where the `INSURANCE` / `ACCEPTED CONSEQUENCE` markers now live.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestGuardedIndexRepair::test_raw_rebuild_is_not_called_anywhere_in_the_script` — UPDATE: widen to cover the shared helper too, then parametrize across all three scripts in the new shared test file.
- [ ] `tests/unit/test_migrate_strip_pid_fields.py::TestOutputIsCapturedNonEmpty::test_both_streams_are_captured` and `::test_a_failure_reason_includes_the_stdout_tail` — UPDATE: `inspect.getsource` must target `_run_migration_script`, which is where the capture now lives.
- [ ] `tests/unit/test_migrations.py` — UPDATE if it asserts an exact `MIGRATIONS` key set; two keys are added.
- [ ] `tests/unit/test_strip_migration_shared.py` — NEW: the shared helper's guard, isolation, and index-sweep behavior, plus the cross-script parametrized invariants.

The pid script's other existing tests must pass **unmodified** — they are the
regression gate proving the refactor is behavior-preserving.

## Documentation

### Feature Documentation
- [ ] No new feature doc. This is a refactor of existing migration scripts; no
      user- or operator-facing surface changes.
- [ ] `docs/plans/durability-m1-fence-canary.md` — its line 431 table row states
      the siblings "call unguarded `rebuild_indexes()` and neither is tested".
      That becomes false when this ships. Update that row to record the
      resolution and cite this issue.

### External Documentation Site
- [ ] Not applicable — this repo has no external docs site.

### Inline Documentation
- [ ] The shared helper carries the full safety-property commentary (guard
      rationale, `clean_indexes()`-not-`rebuild_indexes()` reasoning,
      accepted-consequence note) in one place instead of three.
- [ ] Each thin script keeps its own field-disposition docstring — that content
      is genuinely per-migration and must not be collapsed.
- [ ] `_migrate_strip_pid_fields`'s docstring loses its "tracked separately
      (#2524)" deferral, since this plan resolves it.

## Update System

This plan changes the update system directly.

- `scripts/update/migrations.py` gains `_run_migration_script()` and two new
  `MIGRATIONS` registry entries (`strip_pty_session_fields_v2`,
  `schema_diet_fields_v2`).
- Effect on the next `/update` (Step 3.6, before the service restart): both new
  entries run once per machine with `--apply`, and every subprocess-shaped
  migration now logs both streams into `logs/update.log`.
- No change to `data/migrations_completed.json` handling, ordering, or the
  `None | error-string` return contract.
- **This build machine runs migrations dry-run only.** Executing the `--apply`
  path against production Redis is an operator action performed by `/update`
  after merge, not part of this build.

## Agent Integration

No agent integration changes. These scripts are invoked only by
`run_pending_migrations()` during `/update`; no skill, subagent, MCP tool, or
session-runner path reads or dispatches them, and no prompt text references
them.

## Risks

| Risk | Mitigation |
|---|---|
| Refactor silently changes pid-migration behavior, which #2518's canary depends on. | The pid script's guard/sweep/exit codes move verbatim. Its existing test file is the regression gate and must pass unmodified except for the two provably-stale patch targets (Task 7). |
| Two new v2 migrations run `--apply` against production Redis on next `/update`. | That is the intended, decided behavior (Decision 1) and matches `strip_pid_fields_v2` already on main. The operation is the same ORM-safe atomic delete+recreate, terminal rows only. This build machine runs dry-run only; `--apply` on the fleet is an operator action via `/update`. |
| Three fail-closed v2 migrations on a fresh install. | Known and documented (Explicitly NOT in scope). Follow-up issue filed. |
| Injectable `field_names` adds indirection for one test affordance. | It is one keyword argument with a default; it also makes the shared helper unit-testable without Redis. |

## Prior Art

- PR #2538 / commit `369d782c8` — established the pattern in the pid script.
- `docs/plans/durability-m1-fence-canary.md` — parent plan; line 431 names this
  exact gap; line 505 records why `clean_indexes()` beats both
  `rebuild_indexes()` and the `repair_indexes()` wrapper.
- #1720 / `agent/index_drift.py` — the class-set window the guard insures against.
- #2101 / #2207 — phantom re-inflation.
- #2536 — `rebuild_indexes()` failing with `unpack(b) received extra data`.

## Critique Results

**Critique pass 2026-08-06, against plan baseline `984d3bb7f`.** Depth: FULL
(triage: new shared abstraction + cross-component change + production-Redis
migration path). Critics: Risk & Robustness, Scope & Value, History &
Consistency, plus driver structural checks and independent source verification.
Roster gate: 3/3 complete, 3/3 grounded.

Driver verification notes, recorded because they change two critics' premises:

- **Task 7's stale-assertion claim is correct and was confirmed at the source.**
  `tests/unit/test_migrate_strip_pid_fields.py` patches
  `AgentSession.repair_indexes` at `:119`, `:374`, and `:385`, while the script
  calls `AgentSession.clean_indexes()` at `scripts/migrate_strip_pid_fields.py:224`.
  Those three assertions are vacuous today, exactly as the plan states.
- **Risk & Robustness's second CONCERN was disproved and is not carried into the
  table.** It posited that a pre-existing test asserting the old stderr-only
  error string would silently break under Decision 3. A repo-wide grep of
  `tests/` for `_migrate_agent_session_keyfield_rename`,
  `_migrate_unify_parent_session_field`, `_migrate_steering_queue_drain`, and
  `stderr[-` returns zero matches — no such assertion exists. The residual
  (those three helpers have no test coverage at all) is folded into the
  Decision 3 concern below rather than raised separately.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | History & Consistency, Risk & Robustness, Driver | The first Success Criterion, `grep -rn 'rebuild_indexes' scripts/migrate_*.py` → zero matches, is unsatisfiable within this plan's own declared scope. Five migration scripts that the plan never touches match the `scripts/migrate_*.py` glob and call `rebuild_indexes()`. After this plan ships in full the grep still returns matches, so a stated Success Criterion permanently fails — inviting either a false "not done" reading or unplanned scope creep into five unrelated migrations to silence it. | pending | The five out-of-scope call sites, verified on `984d3bb7f`: `scripts/migrate_agent_session_keyfield_rename.py:180`, `scripts/migrate_parent_session_field.py:161`, `scripts/migrate_session_type_pm_to_eng.py:321`, `scripts/migrate_unify_parent_session_field.py:110`, `scripts/migrate_session_type_chat_to_pm.py:155` (plus docstring mentions at `:14`, `:10`, `:20`, `:15`). Replace the criterion with an explicit file list rather than a glob, and note the five other sites as out of scope. Critically, the file list MUST include `scripts/_strip_migration.py`: the new shared helper does not match the `migrate_*.py` glob, so a merely-narrowed glob would stop covering the one file the `rebuild_indexes` text actually moves into. Related gotcha for Task 1: the comment being moved verbatim from `scripts/migrate_strip_pid_fields.py:218-222` reads "The Verification row greps this file for those two identifiers and expects zero matches, so do not name them here even in a comment" — "this file" changes referent on the move, so the sentence must be re-pointed at the new criterion. |
| CONCERN | Scope & Value | Decision 3 spends this `appetite: Small` plan's budget on a generalization the issue never asked for, while the No-Gos section declines the generalization the issue DID ask for (zero-record guard into `run_pending_migrations()`) as "the wrong venue" for this PR. Decision 3 then routes three helpers with no connection to the sibling-strip problem — `agent_session_keyfield_rename`, `unify_parent_session_field`, `steering_queue_drain` — through the new `_run_migration_script()`, changing their failure-string format. Those three helpers have zero test coverage repo-wide, so the change is unverified in either direction. | pending | Either shrink Task 5 so `_run_migration_script()` is adopted only by the three strip helpers this plan already rewrites (`_migrate_strip_pty_session_fields`, `_migrate_schema_diet_fields`, `_migrate_strip_pid_fields`), filing the remaining three as a follow-up chore alongside the zero-record-guard follow-up the No-Gos already promises; or keep the six-helper scope and add a Test Impact entry plus a new test asserting the shared error-string shape, since no existing test constrains it. The current failure strings for the three out-of-scope helpers are stderr-only `f"exit code {rc}: {result.stderr[-500:]}"`; the shared helper emits a both-tails string, so anything parsing `logs/update.log` for that shape sees a format change. |
| NIT | Scope & Value | The injectable `field_names` parameter on the shared helper is, by the plan's own Risks-table admission, indirection "for one test affordance" with a single consumer pattern. | pending | Optional. The pid script's existing tests already achieve Redis-free testing by patching the module-level name (`patch.object(strip, "_raw_field_names", ...)`), so keeping the module-level `_raw_field_names` as the sole patch point would give the same affordance without adding a keyword argument to the shared signature. Task 2 already requires preserving that module-level name, so the patch point exists either way. |

**Verdict: NEEDS REVISION.** One blocker: the unsatisfiable Success Criterion
must be re-scoped (and must be extended to cover `scripts/_strip_migration.py`)
before build.
