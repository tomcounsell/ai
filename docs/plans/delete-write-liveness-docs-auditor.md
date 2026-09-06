---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2743
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-06T08:11:34Z
---

# Delete `_write_liveness` from `docs_auditor`

## Problem

`reflections/docs_auditor.py::_write_liveness` writes two Redis keys,
`docs_audit:last_completed_run_ts` and `docs_audit:last_completed_run_summary`, that no
code reads. It is the last surviving half of a parallel-run migration: #2739 routed the
same information onto the reflections dashboard, where a human actually sees it, and left
this channel standing.

**Current behavior:**

Every `docs-auditor` rotation pass calls `_write_liveness` on one of five return paths and
sets two Redis keys. Nothing gets them back. The only documented consumption is a manual
`redis-cli GET` printed in two feature docs, which means an operator must SSH to the right
machine and know the key name to learn something the dashboard already shows. The
function's own docstring claims it exists "for PM monitoring" and, since #2782, that it is
"the only durable, queryable surface the rotation produces". Both claims stopped being
true when #2739 merged. Meanwhile the keys carry no TTL, so two orphaned values sit in
every machine's Redis forever.

Keeping two channels for one fact is the parallel-run migration CLAUDE.md Principle 1
forbids: the next person to change the rotation's outcome vocabulary has to remember to
change it in two places, and the docs already carry two mutually reinforcing descriptions
of a dead surface.

**Desired outcome:**

One channel. `run_docs_auditor` returns a `summary` string, the scheduler stores it as
`output_summary`, and the reflections dashboard renders it. `_write_liveness`, its five
call sites, its two constants, its six unit tests, and every doc paragraph describing the
Redis surface are gone. The one datum the summary string does not carry today,
`vault_narratives_compared`, moves into the summary rather than disappearing. The two
orphaned Redis keys are swept by a one-shot `/update` migration so no machine keeps a
value nothing writes.

## Freshness Check

**Baseline commit:** `67d714662`
**Issue filed at:** 2026-08-13T03:42:25Z
**Disposition:** Minor drift

The issue's conclusion survives intact. Its supporting numbers and its argument do not,
and both are corrected below. The full evidence is in the `## Recon Summary` appended to
the issue on 2026-09-05.

**File:line references re-verified:**

| Issue claim | Verified at | Status |
|---|---|---|
| `REDIS_LAST_COMPLETED_TS_KEY` / `_SUMMARY_KEY` definitions | `reflections/docs_auditor.py:136-137` | Holds, verbatim |
| `_write_liveness` definition | `reflections/docs_auditor.py:2153` | Holds |
| Two `r.set` calls, no `r.get` | `:2178`, `:2189`; no reader repo-wide | Holds |
| "its four call sites" | `:2450`, `:2465`, `:2493`, `:2573`, `:2695` | **Drifted: five, not four.** `:2493` is the PR-cap / open-PR guard #2739 added |
| `models/reflection.py:186`, `:221` | `mark_completed(output_summary=...)` param at `:186`, stored at `:221` | Holds, exact |
| `ui/data/reflections.py:139`, `:286` | `last_run_summary` dict at `:139`, `output_summary` at `:286` | Holds, exact |
| Fixed 4-arg signature | `:2153-2160` now takes six params | **Drifted**: `vault_narratives_compared` (#2096) and `fixes_withheld` (#2782) |

**Cited sibling issues/PRs re-checked:**

- **#2739**: CLOSED 2026-08-28 via PR #2887 (`7ccd27d5d`). The prerequisite this issue
  names. Verified in code, not assumed: see the Data Flow section for the full chain.
- **#2782**: MERGED 2026-08-13 (`ffbae5b1d`). Added `fixes_withheld` to the liveness
  payload after this issue was filed, which is why the issue's signature description is
  stale.
- **#2741**: CLOSED 2026-08-18 via PR #2842 (`a9205b065`). Deleted the rename channel
  from the same module. Not a dependency; a directly reusable precedent for how this repo
  lands a dead-code deletion.
- **#2834**: CLOSED, folded into #2739's lane.

**Commits on main since the issue was filed (touching referenced files):**

- `7ccd27d5d` fix(docs-auditor): review-gate every write (#2739, #2834). **Changed the
  premise.** Added a fifth `_write_liveness` call site, rewrote the rotation's outcome
  vocabulary so four of five post-lock returns report `"skipped"`, and added the
  `modal_content.html` render of `output_summary` that makes the dashboard a real reader.
- `ffbae5b1d` fix(docs-auditor): migration-context hatch and bare-path existence
  invariant (#2782). **Partially opposes.** Threaded `fixes_withheld` into the liveness
  payload and wrote the "only durable, queryable surface" docstring. That claim is what
  this plan retires.
- `a9205b065` (#2741), `45d0961f9` (#2728), `15023ee97`, `5eaa74230`, `6c68f29ab`,
  `974be6532`: touched the same module elsewhere; none touch `_write_liveness` or its
  keys.
- `90a319df7`, `974e8d4c9`/`974eb8d4c`: scheduler changes; neither touches the
  `mark_completed(output_summary=...)` call at `:644-648`.

**Active plans in `docs/plans/` overlapping this area:** none. No plan under
`docs/plans/` mentions #2743, `_write_liveness`, or `docs_audit:`. The two files that do
are both under `docs/archive/plans-completed/` and stay untouched.

**Notes:**

The one substantive drift is argumentative rather than factual, and it comes from #2739's
own plan (`docs/archive/plans-completed/docs-auditor-review-gate.md:1549-1557`), which
recorded it as a deliberate handoff to this issue:

> So this plan does not *depend* on the keys, but it does invalidate #2743's premise in
> the opposite direction from how #2743 states it: the justification for deletion becomes
> "Q5 superseded it", not "nobody ever read it". #2743 is out of scope here and should be
> re-argued on that basis.

Adopted. This plan deletes `_write_liveness` because #2739 gave the same information a
surface a human actually looks at, not merely because nothing calls `r.get` on the keys.
The distinction matters for the build: the deletion is only safe if every fact the
liveness payload carried also reaches `output_summary`, which is why
`vault_narratives_compared` gets explicit treatment instead of being dropped.

## Prior Art

- **PR #2842 / #2741**: "chore(#2741): delete the docs-auditor rename channel". Deleted
  six symbols and a module-level global from this exact file, and pinned the removal with
  a grep-assertion test class. The closest precedent available; this plan copies its
  shape, including the `TestVaultDeadCodeRemoved`-style guard class already living at
  `tests/unit/test_docs_auditor_substrate.py:3093`.
- **PR #2887 / #2739**: "review-gate every write, report broken .md links". The
  prerequisite. Built the `output_summary` channel end to end and explicitly deferred this
  deletion, recording the re-argument requirement quoted in the Freshness Check.
- **PR #2782**: "migration-context hatch and bare-path existence invariant". Added
  `fixes_withheld` to the liveness payload. Its two test assertions
  (`liveness.call_args.kwargs["fixes_withheld"]`) are the only behavioral coverage that
  the withheld count reaches a durable surface, so they must be re-pointed rather than
  deleted.
- **PR #2096 / #2084**: "Integrate the work-vault knowledge base". Added
  `vault_narratives_compared` and the `## Liveness signal` section of
  `docs/features/vault-drift-audit.md`. Its stated goal, making "detector ran, found zero
  drift" distinguishable from "the mapping is silently broken", is a real requirement that
  outlives the channel it was built on.
- **PR #1253 / #1247**: "Consolidate docs hygiene: unified auditor substrate". Introduced
  `_write_liveness` in the first place, as a Phase-2 answer to critique finding O1: "No
  liveness signal during Phase 2. How does PM know the reflection is actually running?"
  The dashboard now answers that question directly.

No prior attempt to delete this function exists. Nothing to analyze under "Why Previous
Fixes Failed"; this is a first attempt on a target three separate PRs have circled.

## Research

No external research performed. The work is a deletion inside one module plus two feature
docs, with no external library, API, or ecosystem pattern involved. `/do-plan` Phase 0.7
names exactly this case ("refactoring internal code") as the skip condition.

## Data Flow

The point of this section is to prove the replacement channel carries everything the
deleted one did. Both flows start at the same place.

**Channel A, the one being deleted:**

1. **Entry point**: `run_docs_auditor()` reaches one of five post-lock returns.
2. **`_write_liveness(...)`** (`reflections/docs_auditor.py:2153`) builds a dict of
   `slug`, `pr_url`, `files_touched`, `status`, plus `vault_narratives_compared` when not
   `None` and `fixes_withheld` when non-zero.
3. **Redis**: `r.set("docs_audit:last_completed_run_ts", str(time.time()))` and
   `r.set("docs_audit:last_completed_run_summary", json.dumps(summary))`. No TTL.
4. **Output**: nothing. A human types `redis-cli GET` on the right machine, or the value
   is never seen. Failures are swallowed into `logger.warning`.

**Channel B, the one that stays:**

1. **Entry point**: the same five returns, each carrying a `"summary"` string.
2. **`agent/reflection_scheduler.py:644-648`**: `summary_str = result.get("summary")`,
   then `state.mark_completed(duration, projects=..., output_summary=str(summary_str)[:500])`.
3. **`models/reflection.py:214-221`**: written into
   `last_run_summary = {"timestamp": ts, "status": ..., "duration": ..., "error": ...,
   "projects": ..., "output_summary": ...}` and saved on the `Reflection` record, with
   `ran_at` and `run_count` updated alongside.
4. **`models/reflection.py:246-254`**: mirrored onto a durable `ReflectionRun` history row
   carrying the same `output_summary`.
5. **`ui/data/reflections.py:139`** copies `last_run_summary` into the dashboard row;
   `:286` exposes `output_summary` on the run-history rows.
6. **Output**: `ui/templates/reflections/_partials/modal_content.html:59-61` renders it
   under a "Last run summary" heading in the reflection detail modal.

**Field-by-field coverage, checked against the actual summary strings:**

| Liveness field | Reaches `output_summary`? | Where |
|---|---|---|
| `slug` | **Partial** | Carried verbatim on the zero-diff and PR-cap paths (`f"docs-auditor: zero-diff ({slug})"`, `f"docs-auditor skipped ({slug}): {reason}"`). The created-PR summary (`:2718-2723`) carries no slug even though `_write_liveness(slug, "ok", ...)` at `:2695` did; it is recoverable from the PR URL in the same string. The dirty-tree and no-candidates paths passed only the literal placeholders `(dirty)` and `(no-candidates)`, which their summary prose already states in words |
| `status` | Yes, as prose | every summary string names its outcome ("skipped: dirty_tree", "zero-diff", "N files touched ... PR=") |
| `pr_url` | Yes | `f"..., PR={pr_url}"` on the created-PR return |
| `files_touched` | Yes | `f"docs-auditor: {len(files_touched)} files touched, ..."` |
| `fixes_withheld` | Yes | `withheld_note` (`:2524-2526`) is interpolated into both the zero-diff and the PR summary strings as `"; N fix(es) withheld (target-absent)"` |
| liveness timestamp | Yes | `Reflection.ran_at` and `last_run_summary["timestamp"]`, both dashboard-visible |
| `vault_narratives_compared` | **No** | The one gap. Reaches `_write_liveness` at `:2700` and nothing else |

That last row is the whole design decision in this plan, and it is forced rather than
optional: `vault_narratives_compared` is assigned at `:2460` and used only at `:2700`, so
deleting the call orphans the local and `ruff check` fails on F841. The build cannot
sidestep it.

## Architectural Impact

- **New dependencies**: none. This subtracts.
- **Interface changes**: `_write_liveness` is module-private with no importers outside its
  own file and its own test module, so no public contract moves. The `summary` string on
  the created-PR return gains a trailing clause; that string is already free-form prose
  read by humans, never parsed.
- **Coupling**: strictly decreased. `reflections/docs_auditor.py` loses two of its raw
  Redis writes and its dependence on a private key namespace for reporting. The rotation's
  only remaining reporting obligation becomes its return value, which is the interface the
  scheduler already documents.
- **Data ownership**: run-outcome reporting consolidates onto the `Reflection` /
  `ReflectionRun` models, which already own it for all 23 registered reflections. The
  docs-auditor stops being the one reflection with a private side channel.
- **Reversibility**: trivial. A single revert restores the function, the constants, the
  five call sites, and the tests. The `/update` migration that sweeps the orphaned keys is
  the only non-idempotent-looking piece, and re-running the restored writer would simply
  repopulate them on the next rotation pass.

## Appetite

**Size:** Small

**Team:** Solo dev, plus a reviewer

**Interactions:**
- PM check-ins: 0 (scope is fully determined by the recon; the one judgment call — where
  `vault_narratives_compared` lands — is now *decided in the plan text*, argued in
  Technical Approach item 1, rather than left as an Open Question for a human to answer)
- Review rounds: 1

The deletion itself is mechanical. The cost is in not losing anything on the way out:
seven test patch sites, two feature docs with nine reference clusters between them, and one
field that needs a new home.

## Prerequisites

No environment prerequisites. The work needs no secret, no external service, and no new
dependency.

One code prerequisite, already satisfied and verified rather than assumed:

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #2739's `output_summary` channel is wired | `grep -c 'output_summary=str(summary_str)' agent/reflection_scheduler.py` | The scheduler forwards a function reflection's summary |
| The dashboard renders it | `grep -c 'last_run_summary.output_summary' ui/templates/reflections/_partials/modal_content.html` | The replacement surface is visible, not just stored |

## Solution

### Key Elements

- **`_write_liveness` and its two constants**: deleted outright, along with all five call
  sites. No deprecation shim, no commented-out body, no "kept for reference" block.
- **`vault_narratives_compared`**: appended to the created-PR summary string so the count
  keeps reaching a surface. This is the only behavioral change in the plan; everything
  else is subtraction.
- **`tests/unit/test_docs_auditor_substrate.py`**: seven `patch(...)` sites removed, two
  behavioral assertions re-pointed at `result["summary"]`, the six-test
  `TestWriteLivenessVaultParam` class deleted, and a deletion-guard test added in the
  shape `TestVaultDeadCodeRemoved` already established in this file.
- **Two feature docs**: every paragraph describing the Redis liveness surface removed or
  rewritten, including the whole `## Liveness signal` section of `vault-drift-audit.md`
  and the two `redis-cli GET` lines in the operational cheatsheet.
- **One `/update` migration**: a one-shot sweep of the two orphaned keys, so a machine
  that ran the auditor before this change does not keep two values nothing writes.

### Flow

Rotation run finishes → `run_docs_auditor` returns `{"status", "findings", "summary"}` →
scheduler stores `summary` as `output_summary` → reflections dashboard modal shows "Last
run summary" → operator reads the outcome without touching Redis.

### Technical Approach

**1. Where `vault_narratives_compared` goes.** Today it reaches only the created-PR
`_write_liveness` call (`:2695-2702`); the no-candidates and zero-diff paths compute it at
`:2460` and pass nothing. The faithful move is to append it to that same one summary
string, preserving today's asymmetry exactly rather than quietly widening the signal:

```
f"docs-auditor: {n} files touched, {k} fixes{withheld_note}{suppressed_note}, "
f"PR={pr_url}; vault {vault_narratives_compared} narratives compared"
```

**The clause is unconditional. Write no `is not None` guard.**
`_run_vault_drift_detection` is annotated `-> int` (`reflections/docs_auditor.py:2370`)
and returns an `int` on every path: `return 0` when `_resolve_vault_root` yields `None`
(`:2384-2385`), `return compared` on success (`:2398`), and `return 0` from its
`except Exception` (`:2399`, `return 0` at `:2401`). The local at `:2460` is therefore never `None`, and a
guard against a value that cannot occur would ship dead code inside a dead-code-deletion
PR. A builder must not "fix" this by making the function return `None`; its annotation is
correct and stays.

The distinction #2084 built the field to preserve is real, but it is **per-call-site, not
per-value**. Today the created-PR call site always passes an int (so the
`vault_narratives_compared` key is always present in that payload) and the other four call
sites pass nothing (so the key is always absent in theirs). The faithful replacement keeps
exactly that shape: the created-PR summary always carries the clause, including when the
count is `0`; the other four summary strings never carry it.

**Why the clause is worth adding at all** (this settles the choice rather than deferring
it to a human): with the clause, `; vault 0 narratives compared` on a created-PR run is
observably different from `; vault 137 narratives compared`. That is the observability
guarantee PR #2096 argued for — "detector ran and compared N narratives" versus "detector
ran and compared none, so the vault mapping may be silently broken" — and it is the only
one actually at stake. Discarding the return with `_ = _run_vault_drift_detection(...)`
would also clear `ruff`'s F841 with a smaller diff, so the lint rule forces *a* resolution
and not *this* one; the argument above is what picks this one. The build proceeds on it
with no PM check-in.

**Truncation budget: measured, not assumed.** `agent/reflection_scheduler.py:648`
truncates `output_summary` to 500 characters. A worst-realistic created-PR summary (42
files, 137 fixes, withheld note present, Telegram suppressed with the full repo path, a
real six-digit PR URL) measures **195 characters**, and **227** with the 32-character
vault clause appended — roughly 273 characters of headroom. The clause therefore stays at
the end of the string, and there is no reordering fallback. The build pins the budget
with a permanent test rather than a one-off measurement.
`test_worst_case_summary_stays_under_truncation_budget` drives the created-PR path with a
worst-case payload and asserts both that the vault clause survives and that
`len(result["summary"]) < 500`. It is mandated in `## Test Impact`, in task 2 of
`## Step by Step Tasks`, and by the `Truncation budget pinned` row of `## Verification`.
Do not introduce a conditional reorder branch that no test would ever exercise. See Risk 1.

**2. Deletion order.** Delete the five call sites first, then the function, then the
constants. `ruff check` after each step turns the orphaned `vault_narratives_compared`
local into an F841 that names the exact line, which is a cheap correctness check that the
threading step actually landed.

**3. Test re-pointing, not test deletion.** Two assertions currently prove the withheld
count reaches a durable surface:

- `tests/unit/test_docs_auditor_substrate.py:1443`,
  `assert liveness.call_args.kwargs["fixes_withheld"] == 1`
- `:1525`, `assert liveness.call_args.kwargs["fixes_withheld"] == 2`

Both become assertions on the returned summary, which is where the count now lives via
`withheld_note`:

```python
assert "1 fix(es) withheld" in result["summary"]
```

That is a stronger test than the one it replaces: it exercises the real string an operator
reads instead of a mock's kwargs. `:2105`
(`assert mocks["liveness"].call_args.args[1] == "skipped"`) is genuinely redundant once
the mock is gone, because the test three lines up already asserts
`result["status"] == "skipped"` from the same run; delete it and let the status assertion
carry the claim. The class docstring at `:1322-1328` and the comment at `:1960` both name
"Redis liveness" as an operator surface and need their wording corrected in the same pass.

**4. Orphaned-key sweep.** Two separate precedents, because no single existing migration
covers both halves of this one.

*Fail-soft shape* comes from `_migrate_clear_orphaned_warn_state_key`
(`scripts/update/migrations.py:1174-1200`): try/except, log the exception, `return None`
unconditionally. Note what that function does **not** demonstrate — it never touches
Redis. It calls `warn_state.should_emit(_ORPHANED_WARN_KEY, "", project_dir)`, which pops
a key from `data/update_warn_state.json`. Copy its shape, not its mechanism.

*Redis connection* comes from `reflections/docs_auditor.py:183`, where `_get_redis()`
returns `POPOTO_REDIS_DB` via a lazy import. Reach it the way
`_migrate_backfill_job_last_active_scores` (`:1115`) reaches `models.job`:
`sys.path.insert(0, str(project_dir))` followed by the import inside the try block.
Confirm during the build that the import resolves with no circular import once the two
constants are deleted. **Hard-code the two key strings** in the migration
(`"docs_audit:last_completed_run_ts"`, `"docs_audit:last_completed_run_summary"`) rather
than importing the constants, which this change removes.

Add `_migrate_clear_docs_audit_liveness_keys`, register it in the `MIGRATIONS` dict, delete
the two keys, return `None` unconditionally, and log rather
than raise on failure. These are not Popoto-managed keys, so the ORM rule does not apply
and `instance.delete()` has nothing to operate on; the raw-Redis guard is a Bash
PreToolUse hook and does not fire on Python source. Do the sweep through the migration,
never an ad-hoc `redis-cli DEL` from a build agent's shell.

**5. Docs.** Nine reference clusters across two files (seven in `docs-auditor.md`, two in
`vault-drift-audit.md`), enumerated in the Documentation
section. The `## Rotation State` section the issue names is only one of them; the issue
was written before #2782 and #2739 added the rest.

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] The one handler in scope is `_write_liveness`'s own
      `except Exception as e: logger.warning(...)` (`:2191-2192`). It is deleted with the
      function, so its coverage obligation disappears rather than needing a new test. No
      handler is added by this work.
- [ ] Confirm no other `except` block in `run_docs_auditor` changes behavior. The outer
      `except Exception` at `:2727` converts any raise into `{"status": "error"}`; the
      summary-string change must not be able to raise. Assert this: the new clause
      interpolates an `int | None` into an f-string, which cannot raise, but a test that
      drives the created-PR path with `_run_vault_drift_detection` returning `0` and
      asserts `result["status"] == "ok"` proves it rather than reasoning about it.

### Empty/Invalid Input Handling

- [ ] `vault_narratives_compared == 0` on the created-PR path: assert the clause is
      present and reads `0`. This is the case the whole field exists for and the one an
      "if the value is truthy" bug would silently break.
- [ ] The clause is **absent** from the zero-diff and no-candidates summaries, which
      never carried the count and still must not. This is the per-call-site half of the
      distinction (Technical Approach item 1); there is no `None`-valued case to test,
      because `_run_vault_drift_detection` is `-> int` and returns `0` on every path
      (`:2384-2385`, `:2398`, `:2401`).
- [ ] Empty `summary` reaching the scheduler is out of scope: every return path builds a
      non-empty f-string, and the scheduler's `if summary_str else None` guard
      (`:648`) already handles a falsy value.

### Error State Rendering

- [ ] The user-visible surface is the reflections dashboard modal. Its render is guarded
      by `{% if r.last_run_summary and r.last_run_summary.output_summary %}`
      (`modal_content.html:59`), so a missing summary renders nothing rather than erroring.
      No change needed; verify the guard is untouched.
- [ ] The `"error"` return path (`:2727`) carries its own summary and is unaffected by
      this work. Confirm by test that an exception inside the rotation still produces a
      summary string the scheduler can forward.

## Test Impact

All in `tests/unit/test_docs_auditor_substrate.py`, with one exception noted at the end.

- [ ] `TestWriteLivenessVaultParam` (`:3042-3091`, six tests: `_summary` helper,
      `test_four_arg_call_omits_vault_count`, `test_five_arg_call_includes_vault_count`,
      `test_five_arg_zero_is_emitted`, `test_withheld_count_absent_when_zero`,
      `test_withheld_count_emitted_when_nonzero`,
      `test_withheld_is_trailing_and_preserves_positional_contract`). **DELETE** the whole
      class. Every test targets a function that will not exist.
- [ ] `TestWithheldBlocksStaleClose::test_bare_name_withhold_propagates_to_pr_body_telegram_and_liveness`
      (`:1358`, patch at `:1433`, assertion at `:1443`). **UPDATE**: drop the
      `_write_liveness` patch, replace the kwargs assertion with
      `assert "1 fix(es) withheld" in result["summary"]`, and rename the test to drop
      `_and_liveness`. Its class docstring (`:1322-1328`) also names Redis liveness as one
      of three surfaces and must be reworded.
- [ ] The zero-diff withheld test (patch at `:1512`, assertion at `:1525`). **UPDATE**:
      same treatment, asserting `"2 fix(es) withheld"` in the summary.
- [ ] Bare `patch("reflections.docs_auditor._write_liveness")` context lines at `:1471`,
      `:1541`, `:1754`, `:1781`. **UPDATE**: delete the lines. Nothing asserts on them;
      they exist only to stop the real function reaching Redis.
- [ ] `TestHoistedPRGuards._run` helper (patch at `:2061`, mock exported at `:2069`).
      **UPDATE**: remove the patch and the `"liveness"` entry from the returned mock dict.
- [ ] `TestHoistedPRGuards::test_guard_still_stamps_the_rotation_hash_for_the_picked_doc`
      (`:2105`). **UPDATE**: delete the
      `assert mocks["liveness"].call_args.args[1] == "skipped"` line. The
      `result["status"] == "skipped"` assertion in the sibling test already covers the
      claim, and the rotation-hash assertion on the line above is what this test is for.
- [ ] Comment at `:1960` referencing `_write_liveness(..., "skipped", ...)`. **UPDATE**:
      reword to cite the returned status instead.
- [ ] **NEW** `TestLivenessDeadCodeRemoved`, modeled on `TestVaultDeadCodeRemoved`
      (`:3093`): assert `not hasattr(docs_auditor, "_write_liveness")`,
      `not hasattr(docs_auditor, "REDIS_LAST_COMPLETED_TS_KEY")`, and
      `not hasattr(docs_auditor, "REDIS_LAST_COMPLETED_SUMMARY_KEY")`. This is what stops
      a future revert from silently reintroducing the channel.
- [ ] **NEW** two tests for the `vault_narratives_compared` clause: (1) present-and-zero
      on the created-PR path, asserting the summary contains `vault 0 narratives
      compared`; (2) absent on the zero-diff and no-candidates paths, asserting `"vault"`
      does not appear in either summary. There is no `None`-valued case: the detector is
      `-> int` on every path.
- [ ] **NEW** `test_worst_case_summary_stays_under_truncation_budget`, which pins Risk 1's
      truncation budget permanently instead of leaving it to a one-time measurement. Drive
      `run_docs_auditor()` down the created-PR path with a mocked `audit()` result carrying
      42 `files_touched` entries, `fixes_applied=137`, a non-zero `fixes_withheld` so
      `withheld_note` populates, a Telegram-suppression path so `suppressed_note`
      populates, and a six-digit `pr_url`; then assert `len(result["summary"]) < 500` and
      `"narratives compared" in result["summary"]`. Reuse the `TestHoistedPRGuards` and
      `TestStep9Suppression` fixture shapes already in this file rather than rebuilding the
      f-string by hand, so the test exercises the real code path Risk 1 protects. The
      function name is load-bearing: the `Truncation budget pinned` Verification row greps
      for it.
- [ ] `TestStep9Suppression::test_step9_suppression_reaches_summary_before_pr_url`
      (`:1762`, assertion at `:1787`, patch at `:1781`). **UPDATE**: drop the
      `_write_liveness` patch line only. Its ordering invariant
      (`summary.index("suppressed") < summary.index("PR=")`) is why the vault clause is
      appended after `PR={pr_url}` and never reordered ahead of it; leave the assertion
      untouched and do not let a truncation-headroom edit displace `suppressed_note`.

No test outside this file references `_write_liveness` or either constant.

- [ ] **NEW**, and the one exception to "all in this file": the dashboard-render check of
      task `validate-dashboard-render` lands in `tests/unit/test_per_project_modal.py`,
      the module that already owns direct Jinja2 renders of
      `reflections/_partials/modal_content.html`. It reuses that file's existing `env`
      fixture (`:30-35`) and `_base_reflection_ctx()` (`:38-53`) and adds no ORM or Redis
      I/O. Nothing in that file changes; the work is purely additive.

## Rabbit Holes

- **Widening `vault_narratives_compared` to the other summary strings.** The no-candidates
  and zero-diff paths compute the count and today throw it away. Adding it there is a
  behavioral improvement, arguably a correct one, and it is not this issue. Preserve the
  existing asymmetry; if the wider signal is wanted, it deserves its own issue with its own
  argument.
- **Auditing the other 22 reflections for private side channels.** Tempting once you
  notice the docs-auditor was the only one with one. Out of scope and probably empty.
- **Rewriting the rotation's outcome vocabulary.** `run_docs_auditor` has five
  `"status": "ok"`/`"skipped"` returns whose semantics #2739 litigated across four review
  rounds. This plan touches one summary string and zero status values. Do not reopen it.
- **Making `redis-cli` inspection work some other way.** The point is that no such
  inspection is needed. Do not replace two keys with one key, a TTL, or a `docs_audit:`
  hash field.
- **Chasing the archived plans.** `docs/archive/plans-completed/sdlc-1247.md`,
  `vault-site-integration.md`, and `docs-auditor-review-gate.md` carry 30-plus references
  to `_write_liveness` and the two keys. Archives are historical records of what was true
  when they were written. Leave every one of them alone.
- **A deprecation window.** There is no external consumer to warn. Delete in one commit.

## Risks

### Risk 1: The 500-character truncation silently eats the new clause

**Impact:** `agent/reflection_scheduler.py:648` truncates `output_summary` to 500
characters. The created-PR summary already interpolates a PR URL, a file count, a fix
count, an optional withheld note, and an optional Telegram-suppression note. Appending the
vault clause at the end makes it the first thing lost, so the field this plan is trying to
preserve is exactly the field truncation would drop.

**Mitigation:** Measured at plan time, not deferred. The worst realistic case (42 files,
137 fixes, withheld note present, Telegram suppressed with the full repo path, a real
six-digit PR URL) renders at **195 characters**, and **227** with the 32-character vault
clause — about 273 characters of headroom. The clause stays at the end of the string.

**No reorder fallback.** An earlier draft of this plan proposed moving the clause ahead of
`PR={pr_url}` if the budget were tight. That branch is deleted, for two reasons. It is
unnecessary given the measurement above, and it would collide with an existing invariant:
`test_step9_suppression_reaches_summary_before_pr_url`
(`tests/unit/test_docs_auditor_substrate.py:1762`) asserts
`result["summary"].index("suppressed") < result["summary"].index("PR=")` at `:1787`.
Inserting anything between `{suppressed_note}` and `, PR=` risks displacing
`suppressed_note` past `PR=` and breaking that assertion. Do not add a conditional
reorder no test would exercise.

Pin the budget with one test that builds a worst-case created-PR summary and asserts both
that the vault clause survives and that the length stays under 500.

### Risk 2: Deleting the mock patches lets the real Redis writer run in tests

**Impact:** Four of the seven `patch("reflections.docs_auditor._write_liveness")` lines
exist purely to keep the real function away from Redis during a `run_docs_auditor` test.
Deleting the function makes them unnecessary, but deleting the patch lines while leaving a
call site behind would send test writes to whatever Redis the suite resolves.

**Mitigation:** Order the work function-first, patches-second: once `_write_liveness` does
not exist, `patch(...)` on it raises `AttributeError` and every stale patch line fails
loudly rather than silently. The failure mode is a red test, not a production write. The
suite also runs under a claimed test DB (`pytest_configure` exports `REDIS_URL`
process-wide), so even a leak lands off production.

### Risk 3: Orphaned keys outlive the code on machines that never run `/update`

**Impact:** Two valueless keys persist in a machine's Redis. Cosmetic, but it is exactly
the residue Principle 1 exists to prevent, and a future operator finding
`docs_audit:last_completed_run_summary` with a months-old timestamp would reasonably
conclude the auditor had stopped running.

**Mitigation:** The `/update` migration sweeps them, recorded once in
`data/migrations_completed.json`. It returns `None` unconditionally so a bookkeeping
cleanup can never fail `/update`, following `_migrate_clear_orphaned_warn_state_key`'s
documented rationale.

### Risk 4: The build treats "delete the docs section" as "delete the Rotation State section"

**Impact:** The issue names only `## Rotation State`. Following it literally leaves eight
other reference clusters, including a whole `## Liveness signal` section in
`vault-drift-audit.md` describing a function that no longer exists. That is precisely the
"historical artifact in docs" Principle 1 forbids, and the docs-auditor itself would file
an issue about it.

**Mitigation:** The Documentation section below enumerates all nine clusters by file and
line. The Verification table greps both files for zero occurrences of `_write_liveness`
and `last_completed_run`.

## Race Conditions

No race conditions identified. Two `/update`-ordering windows are documented below, both
of them cosmetic and neither of them a race. The work deletes two `r.set` calls and adds no concurrent
access. `run_docs_auditor` already serializes itself behind `docs_audit:running:global`
(SETNX, 1h TTL) and the deleted writes were the last operations before a return, read by
nobody. The summary string is built and returned synchronously inside the same function
call the scheduler awaits.

One ordering fact worth stating so it is not mistaken for a race: the two orphaned keys
may still hold values while a machine runs post-deletion code but pre-migration `/update`.
That window is benign because nothing reads them in either state, and the migration is
idempotent.

The opposite window exists too and is equally benign. `/update` runs migrations at Step 3.6
— "after git pull, before service restart" (`scripts/update/run.py:1629`) — while the
worker restart happens later in the same run (Step 5 "Service management" at `:2245`,
with `service.install_worker` at `:2311`). A rotation pass firing
between those two points executes still-loaded pre-deletion code and can repopulate both
Redis keys *after* the one-shot sweep has already recorded itself permanently complete,
since `run_pending_migrations` treats a `None` return as done. This is the same cosmetic
Risk-3-class residue arriving from the other direction, and it resolves on the next
restart, when the code that writes the keys is gone. Do **not** respond by making the
migration re-runnable or by returning an error string to force a retry:
the docstring of `_migrate_clear_orphaned_warn_state_key`
(`scripts/update/migrations.py:1174-1194`) records why a bookkeeping cleanup must never be
able to fail `/update`.

## No-Gos (Out of Scope)

Nothing deferred. Every relevant item is in scope for this plan.

The deletion, the `vault_narratives_compared` rehoming, all seven test patch sites, both
feature docs, and the orphaned-key migration land together. Splitting any of them out
would leave exactly the half-migration this issue exists to close.

Two boundaries are worth stating so they are not mistaken for deferrals:

- **`docs/archive/plans-completed/` stays byte-identical.** Those files are the historical
  record of decisions made when `_write_liveness` was live; editing them would falsify
  the record rather than update documentation. This is a permanent rule, not a
  postponement.
- **The other four summary strings keep their current `vault_narratives_compared`
  behavior** (they never carried it and still will not). Preserving today's asymmetry is
  the scope boundary, not a task put off until later. The Rabbit Holes section explains
  why widening it would be a different issue with a different argument.

## Update System

The `/update` skill needs one addition: a migration that sweeps the two orphaned Redis
keys.

- **New migration**: `_migrate_clear_docs_audit_liveness_keys` in
  `scripts/update/migrations.py`, registered in the `MIGRATIONS` dict (required;
  `run_pending_migrations()` iterates that dict and an unregistered function never runs).
  Idempotent by construction: deleting an absent key is a no-op. Returns `None`
  unconditionally and logs on failure, following
  `_migrate_clear_orphaned_warn_state_key` (`:1174-1199`), whose docstring records the
  reasoning: a bookkeeping cleanup must never fail `/update`, and a silently swallowed
  exception would never retry because `run_pending_migrations` records a `None` return as
  permanently completed.
- **No new dependencies or config files** to propagate. No `.env` key, no
  `projects.json` field, no `config/reflections.yaml` change: the `docs-auditor`
  reflection entry is unaffected because its callable name and return shape are unchanged.
- **No migration steps for existing installations** beyond the sweep above. A machine
  running old code and new keys, or new code and old keys, behaves identically in both
  directions since nothing reads them.
- **Service restart**: none required by this change on its own. `reflections/` is loaded
  by the worker, so a machine picks the change up on its next `/update`-driven restart in
  the ordinary way.

## Agent Integration

No agent integration required. This removes an internal function and its Redis writes.

- **No new CLI entry point.** `pyproject.toml [project.scripts]` is untouched;
  `_write_liveness` was never reachable from a `valor-*` binary.
- **The bridge does not import it.** `bridge/telegram_bridge.py` has no reference to
  `reflections.docs_auditor`; the rotation reaches the agent through the reflection
  scheduler in the worker, and that path is unchanged.
- **The agent-facing surface improves slightly and needs no wiring.** The information the
  deleted keys carried already reaches the agent through the `Reflection` model, which the
  dashboard and any `Reflection.query` read share. Nothing new to expose.
- **No integration test needed** for a capability nothing gained. The unit tests in
  `tests/unit/test_docs_auditor_substrate.py` cover the full change.

## Documentation

### Feature Documentation

`docs/features/docs-auditor.md`, seven clusters, verified on `67d714662`:

- [ ] `:374-379`: the withheld-count paragraph passes the count "to `_write_liveness` as
      a keyword `fixes_withheld`, emitted into the Redis summary only when non-zero".
      Rewrite so the durable surfaces are the GitHub issue and the dashboard's rendered
      `output_summary`, with the withheld count reaching the latter through the summary
      string's `withheld_note`.
- [ ] `:405-412`: the outcome-vocabulary paragraph justifies each `"skipped"` return by
      "matching each one's own `_write_liveness(..., "skipped", ...)` call". The
      justification has to stand on the returned status alone.
- [ ] `:559-581` (`## Rotation State`, the section the issue names): no direct
      `_write_liveness` mention, but it is the section that frames what state the rotation
      persists. Add the one sentence this issue asks for: run outcomes reach the operator
      through the reflection's `output_summary`, not through a `docs_audit:` key.
- [ ] `:582-596` (`## Locking`): remove the two `docs_audit:last_completed_run_*` lines
      from the key listing. The remaining four entries are all real locks and state.
- [ ] `:668-678`: the `vault_narratives_compared` bullet describes the "explicit optional
      5th parameter" threading. Rewrite to describe the summary-string clause, keeping the
      `0`-versus-absent distinction that is the bullet's actual point.
- [ ] `:717-737` (`## Operational Cheatsheet`): delete the two `redis-cli GET` lines and
      their comment block. Replace with a pointer to the reflections dashboard modal.
- [ ] `:738-765` (`## Tests`): the sentence naming `TestWriteLivenessVaultParam` and the
      "4-arg/5-arg positional contract" (`:747`) must go, replaced by the new
      `TestLivenessDeadCodeRemoved` class. **The cluster runs to `:765`, not `:750`**:
      `:762` describes `TestWithheldBlocksStaleClose` as "a bare-name withhold reaching
      the PR body, Telegram, and liveness". That sentence names the deleted surface and a
      test this plan renames to drop `_and_liveness` (Test Impact bullet 2). It uses the
      bare word `liveness`, so neither of this file's literal-string Verification greps
      (`_write_liveness`, `last_completed_run`) can see it. Reword it to drop "and
      liveness".

`docs/features/vault-drift-audit.md`:

- [ ] `:156-196` (`## Liveness signal`): the entire section is built on
      `_write_liveness`, including a verbatim copy of its signature (`:163-172`), the
      four-call-site positional-contract rationale (`:174-181`), and the `redis-cli GET`
      at `:195`. Rewrite it around the summary-string clause, and rename the section since
      "liveness" is the deleted surface's name — `## Narratives-compared signal` or
      similar. Keep the section's real content, the three-way distinction, but **restate
      its middle arm correctly**: the source text defines "the mapping is silently broken"
      as *key absent entirely, from a call site that never ran the vault comparison*
      (`:184-186`), which is a per-call-site fact and never a `None` return. In the new
      wording the three arms are: "detector ran and compared N narratives" (clause reads
      `N`), "the run never reached the created-PR path so no count was reported" (clause
      absent from the summary entirely), and "vault unresolvable" (clause reads `0`, paired
      with the `docs_audit: vault root resolution failed` / `no knowledge_base mapping`
      warning in the logs). Do not describe any arm as a `None` return;
      `_run_vault_drift_detection` is `-> int` and cannot produce one.

- [ ] `:254-266` (`## Tests`), specifically the bullet at `:263-264`: the list of test
      classes in this file's own `## Tests` section carries
      "`TestWriteLivenessVaultParam` — 4-arg call sites unaffected, 5-arg call site
      includes the count", naming the six-test class this plan deletes in full. It sits
      outside the `:156-196` range above, so the documentarian gets no instruction for it
      from that bullet, while the bare-word Verification grep does match it
      (`WriteLiveness` under `-i`). **Replace that one bullet** with a
      `TestLivenessDeadCodeRemoved` entry naming what the new guard proves — the function
      and both constants stay gone. This is a single-bullet swap inside an existing
      bulleted list, not a rewrite of the section. Leave the `TestVaultDeadCodeRemoved`
      bullet directly below it (`:265`) alone; it covers a different guard.

- [ ] `docs/features/README.md`: check whether either file's index row summary mentions
      the liveness keys; update if so, leave alone if not.

### External Documentation Site

- [ ] Check `site/` for any page describing the docs-auditor's Redis surface. The
      vault↔site drift detector compares vault narratives against site pages, so a stale
      site page here would be a finding the auditor files against itself.

### Inline Documentation

- [ ] `reflections/docs_auditor.py:132`: the `# Redis key namespace for state/locks/
      liveness.` comment loses its "liveness" clause with the constants.
- [ ] `reflections/docs_auditor.py:2692-2694`: the "11. Liveness signal" step comment
      above the created-PR call site is deleted with the call, but its explanation of why
      the vault count is threaded from this one site belongs on the new summary clause.
- [ ] `tests/unit/test_docs_auditor_substrate.py:1322-1328`, `:1960`: class docstring and
      comment naming Redis liveness as an operator surface.

## Success Criteria

- [ ] `_write_liveness`, `REDIS_LAST_COMPLETED_TS_KEY`, and
      `REDIS_LAST_COMPLETED_SUMMARY_KEY` do not exist anywhere in `reflections/`.
- [ ] All five call sites are gone; `run_docs_auditor` still returns the same five
      `status` values on the same five paths.
- [ ] The created-PR summary always carries the vault count, including when it is `0`,
      with no `is not None` guard; no other summary string carries it. The worst-case
      created-PR summary measures under the scheduler's 500-character truncation (195
      characters today, 227 with the clause).
- [ ] `TestLivenessDeadCodeRemoved` exists and fails if any of the three symbols returns.
- [ ] The replacement surface is demonstrated, not assumed: a direct Jinja2 render of
      `reflections/_partials/modal_content.html` (no ORM, no Redis — the
      `tests/unit/test_per_project_modal.py` pattern) with
      `last_run_summary.output_summary` set to the new created-PR string emits the vault
      clause, and `modal_content.html:59`'s render guard is unchanged.
- [ ] The two behavioral assertions that the withheld count reaches a durable surface
      still exist, now asserting on `result["summary"]` rather than a mock's kwargs.
- [ ] `docs/features/docs-auditor.md` and `docs/features/vault-drift-audit.md` contain
      zero occurrences of `_write_liveness` and `last_completed_run`.
- [ ] `docs/archive/plans-completed/` is byte-identical to its pre-change state.
- [ ] `scripts/update/migrations.py` carries a registered, idempotent sweep of the two
      keys.
- [ ] Tests pass (`/do-test`), specifically
      `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py`.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

Small appetite, one file cluster, one reviewer. The lead deploys a single builder for the
code-plus-test change, a documentarian for the two feature docs, and a validator to
confirm the deletion is total.

### Team Members

- **Builder (deletion)**
  - Name: `liveness-deleter`
  - Role: delete the function, constants, and five call sites; rehome
    `vault_narratives_compared`; repair all seven test sites; add the deletion-guard class;
    add the `/update` migration
  - Agent Type: builder
  - Resume: true

- **Documentarian (feature docs)**
  - Name: `liveness-documentarian`
  - Role: the nine reference clusters across `docs-auditor.md` and `vault-drift-audit.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator (deletion completeness)**
  - Name: `liveness-validator`
  - Role: prove the symbols are gone, the archives are untouched, the vault count reaches
    the summary on `0`, the truncation budget holds, and the clause actually renders in
    the dashboard modal the plan's whole premise rests on
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete the channel and rehome the vault count

- **Task ID**: build-delete-liveness
- **Depends On**: none
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Informed By**: Data Flow (field-coverage table), Technical Approach items 1 and 2
- **Assigned To**: liveness-deleter
- **Agent Type**: builder
- **Parallel**: false
- Delete the five `_write_liveness(...)` call sites at `reflections/docs_auditor.py:2450`,
  `:2465`, `:2493`, `:2573`, `:2695`, and the "11. Liveness signal" comment above the last.
- Append the vault clause to the created-PR summary string **unconditionally**. Write no
  `is not None` guard: `_run_vault_drift_detection` is annotated `-> int`
  (`reflections/docs_auditor.py:2370`) and returns an `int` on all three of its exits —
  `return 0` when the vault root is unresolvable (`:2384-2385`), `return compared` on
  success (`:2398`), and `return 0` from its `except Exception` (`:2401`). A `None` arm
  would guard a value that cannot occur, which is exactly the dead code this PR deletes,
  and it would fail the plan's own "Clause is unconditional" Verification row.
- Measure the worst-case summary length against the scheduler's 500-character truncation
  (`agent/reflection_scheduler.py:648`, `str(summary_str)[:500]`) and record the number in
  the PR description. Do **not** reorder the clause ahead of `PR={pr_url}`: Risk 1 deletes
  that fallback outright, because inserting anything between `{suppressed_note}` and
  `, PR=` can displace `suppressed_note` past `PR=` and break
  `test_step9_suppression_reaches_summary_before_pr_url`
  (`tests/unit/test_docs_auditor_substrate.py:1762`, assertion at `:1787`).
- Delete `_write_liveness` (`:2153-2192`) and the two constants (`:136-137`); trim the
  "liveness" clause from the `:132` comment.
- Run `python -m ruff check reflections/docs_auditor.py`. An F841 on
  `vault_narratives_compared` at `:2460` means the rehoming step did not land.

### 2. Repair and extend the tests

- **Task ID**: build-tests
- **Depends On**: build-delete-liveness
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Informed By**: Test Impact (the ten `test_docs_auditor_substrate.py` bullets; the
  eleventh belongs to task `validate-dashboard-render`)
- **Assigned To**: liveness-deleter
- **Agent Type**: builder
- **Parallel**: false
- Delete `TestWriteLivenessVaultParam` (`:3042-3091`) in full.
- Re-point the two withheld assertions (`:1443`, `:1525`) at `result["summary"]`; drop the
  now-redundant `:2105` liveness assertion; remove all seven `patch(...)` lines and the
  `"liveness"` entry in `TestHoistedPRGuards._run`'s mock dict.
- Reword the class docstring at `:1322-1328`, the test name ending `_and_liveness`, and the
  comment at `:1960`.
- Add `TestLivenessDeadCodeRemoved` (three `hasattr` assertions) beside
  `TestVaultDeadCodeRemoved` at `:3093`.
- Add the two vault-clause tests: **present-and-zero** on the created-PR path (the summary
  contains `vault 0 narratives compared`); **absent** on the zero-diff and no-candidates
  paths (`"vault"` appears in neither summary). There is no `None`-valued case to test —
  the detector is `-> int` on every path (`:2370`, `:2384-2385`, `:2398`, `:2401`).
- Add `test_worst_case_summary_stays_under_truncation_budget`, the budget test Risk 1
  mandates: drive the created-PR path with 42 `files_touched`, `fixes_applied=137`, a
  non-zero `fixes_withheld`, a suppressed Telegram notification, and a six-digit `pr_url`,
  then assert `len(result["summary"]) < 500` and `"narratives compared" in
  result["summary"]`. Use that exact function name; the `Truncation budget pinned`
  Verification row greps for it.
- Run `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py`.

### 3. Add the orphaned-key migration

- **Task ID**: build-migration
- **Depends On**: build-delete-liveness
- **Validates**: `scripts/update/migrations.py`
- **Informed By**: Technical Approach item 4
- **Assigned To**: liveness-deleter
- **Agent Type**: builder
- **Parallel**: true
- Add `_migrate_clear_docs_audit_liveness_keys` modeled on
  `_migrate_clear_orphaned_warn_state_key` (`:1174`): delete both keys, return `None`
  unconditionally, log on failure.
- Register it in the `MIGRATIONS` dict with a one-line description.
- Do the sweep through the migration only. No ad-hoc `redis-cli DEL` from a shell.

### 4. Cascade the feature docs

- **Task ID**: document-feature
- **Depends On**: build-delete-liveness, build-tests, build-migration
- **Validates**: `docs/features/docs-auditor.md`, `docs/features/vault-drift-audit.md`
  (Verification rows "Docs clean (literal symbols)" and "Docs clean (bare word)")
- **Informed By**: Documentation (all nine clusters), Risk 4
- **Assigned To**: liveness-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Work the nine clusters enumerated in the Documentation section.
- Rewrite `vault-drift-audit.md`'s `## Liveness signal` around the summary clause, keeping
  the three-way `0`/absent/unresolvable distinction intact.
- Touch nothing under `docs/archive/`.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-delete-liveness, build-tests, build-migration, document-feature
- **Validates**: the whole `## Verification` table (every row), plus `docs/archive/`
  byte-identity
- **Informed By**: Verification, Success Criteria
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm `git diff --stat -- docs/archive/` is empty.
- Confirm the vault clause reaches `result["summary"]` on a `0` count by driving the
  created-PR path, not by reading the code.

### 6. Prove the replacement surface actually renders

- **Task ID**: validate-dashboard-render
- **Depends On**: build-delete-liveness, build-tests
- **Validates**: `ui/templates/reflections/_partials/modal_content.html`, via a new test in
  `tests/unit/test_per_project_modal.py`
- **Informed By**: Data Flow (Channel B, steps 2-6), Test Impact (final bullet)
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false

Every other check in this plan stops at `result["summary"]` — a grep, a string
containment, or a `git diff --stat`. None of them reaches the template layer, yet the
entire justification for the deletion is that an operator now reads this on the dashboard.
Demonstrate the end-to-end claim once instead of assuming it:

- Render the template directly, with **no ORM write and no Redis I/O**, following the
  pattern this repo already uses against this exact template in
  `tests/unit/test_per_project_modal.py`. Reuse its `env` fixture (`:30-35`:
  `Environment(loader=FileSystemLoader(str(UI_TEMPLATES)), autoescape=True)` then
  `register_template_filters(e)`, imported from `ui.app`) and its `_base_reflection_ctx()`
  helper (`:38-53`), which already supplies every top-level `r.*` field the template
  dereferences. Merge one key into that context:
  `"last_run_summary": {"output_summary": <the new created-PR summary string with vault
  count 0>}`. That key alone satisfies the render guard. Render
  `reflections/_partials/modal_content.html` the way `_render_modal` (`:56-63`) does —
  `r=<merged ctx>, recent_runs=[], sparkline=[], manual_command=None` — and assert
  `"vault 0 narratives compared" in html`. A seed-and-delete through the ORM would add a
  real Redis write plus a teardown that leaks a record if the test fails midway, all to
  prove a Jinja2 template interpolates a string.
- The assertion proves the render guard
  `{% if r.last_run_summary and r.last_run_summary.output_summary %}`
  (`ui/templates/reflections/_partials/modal_content.html:59`) passes and the string
  survives to HTML.
- Confirm that guard is byte-identical to its pre-change state; this work must not touch
  it.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Function gone | `grep -rn '_write_liveness' reflections/ \| wc -l` | `0` |
| Constants gone | `grep -rn 'REDIS_LAST_COMPLETED' reflections/ \| wc -l` | `0` |
| Keys gone from code | `grep -rn 'last_completed_run' reflections/ ui/ agent/ models/ \| wc -l` | `0` |
| No live test references survive | `grep -n 'docs_auditor\._write_liveness\|_write_liveness(\|docs_auditor\.REDIS_LAST_COMPLETED' tests/unit/test_docs_auditor_substrate.py \| wc -l` | `0` |
| Deletion guard exists | `grep -c 'class TestLivenessDeadCodeRemoved' tests/unit/test_docs_auditor_substrate.py` | `1` |
| Vault count rehomed | `grep -c 'vault_narratives_compared' reflections/docs_auditor.py` | `> 0` |
| Clause is unconditional | `grep -n 'vault_narratives_compared is not None' reflections/docs_auditor.py \| wc -l` | `0` |
| Truncation budget pinned | `grep -c 'def test_worst_case_summary_stays_under_truncation_budget' tests/unit/test_docs_auditor_substrate.py` | `1` |
| Withheld count still asserted behaviorally | `grep -c 'fix(es) withheld" in result\["summary"\]' tests/unit/test_docs_auditor_substrate.py` | `> 0` |
| Docs clean (literal symbols) | `grep -rn '_write_liveness\|last_completed_run' docs/features/docs-auditor.md docs/features/vault-drift-audit.md \| wc -l` | `0` |
| Docs clean (bare word) | `grep -rni 'liveness' docs/features/docs-auditor.md docs/features/vault-drift-audit.md \| grep -v 'TestLivenessDeadCodeRemoved' \| wc -l` | `0` |
| Archives untouched | `git diff --stat origin/main -- docs/archive/ \| wc -l` | `0` |
| Migration registered | `grep -c 'clear_docs_audit_liveness_keys' scripts/update/migrations.py` | `> 0` |
| Dashboard renders the clause | see task `validate-dashboard-render` below | modal partial HTML contains `vault 0 narratives compared` |
| Auditor tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

**Why two rows are scoped rather than bare.** The deletion-guard class this plan mandates,
`TestLivenessDeadCodeRemoved`, must contain the string literals `"_write_liveness"`,
`"REDIS_LAST_COMPLETED_TS_KEY"`, and `"REDIS_LAST_COMPLETED_SUMMARY_KEY"` inside its
`hasattr` assertions — that is the whole point of a guard, and it is the shape
`TestVaultDeadCodeRemoved` (`tests/unit/test_docs_auditor_substrate.py:3093`) already
uses. A bare `grep -n '_write_liveness\|REDIS_LAST_COMPLETED'` therefore counts the guard
itself and can never reach `0`; measured against a stub of the guard class it returns `4`.
Two rows are scoped so they stay satisfiable without weakening what they check:

- **No live test references survive** matches *uses* of the symbols
  (`docs_auditor._write_liveness`, a `_write_liveness(` call or comment, and
  `docs_auditor.REDIS_LAST_COMPLETED*`) rather than the bare names. Measured on
  `67d714662` this pattern returns the same `15` lines the bare pattern does — every
  `patch(...)` site, the `:1960` comment, and every `TestWriteLivenessVaultParam` call —
  and returns `0` against the guard-class stub. It catches everything the broad pattern
  caught and nothing the plan orders written.
- **Docs clean (bare word)** filters `TestLivenessDeadCodeRemoved` out before counting,
  because Documentation cluster 7 (and the new cluster 9) instruct writing that class
  name into both feature docs.

Do **not** resolve either row by renaming the guard class away from `Liveness` or by
dropping it: it is the artifact that stops a future revert from silently reintroducing the
channel, and the `Deletion guard exists` row expects it by that exact name.

**Every other row was re-audited against `67d714662` and reasoned about for the
post-change tree.** Current counts: `_write_liveness` in `reflections/` = 6,
`REDIS_LAST_COMPLETED` in `reflections/` = 4, `last_completed_run` across
`reflections/ ui/ agent/ models/` = 2 (both the constant definitions at
`reflections/docs_auditor.py:136-137`), docs literal-symbol matches = 13, docs bare-word
matches = 16 (all 16 map to an enumerated Documentation cluster), archives diff = 0,
migration = 0. Each goes to its expected value by deletion alone; none of them matches a
symbol this plan orders the builder to write. The migration hard-codes the two key
strings in `scripts/update/migrations.py`, which no row's search path covers.

Every zero-expectation row is written as `grep ... | wc -l` rather than `grep -c`.
`grep -c` **exits 1 when the count is zero**, so a validator that gates on exit status
reads a passing check as a failure; and `grep -rc PATTERN dir/` prints one `file:count`
line per file in the tree instead of a single number. The `wc -l` form exits 0 and prints
one number in both cases.

## Critique Results

War room, FULL depth: Risk & Robustness, Scope & Value, History & Consistency, plus
automated structural validation. Mode: independent roster (3 critics).
Round 3 (closure verification of the round-2 findings). Verdict: READY TO BUILD (with
concerns) — 0 blockers, 1 concern, 2 nits.

**Cycle disposition.** Round 3 was the final authorized round; the lane is past its G2
critique cycle cap and there is no round 4. Per the skill's Outcome Contract, a
`READY TO BUILD (with concerns)` verdict at an exhausted concern re-critique bound exits
to the build with the remaining concern **accepted on the record**. The `plan_revising`
lock is deliberately not set: no revision pass is scheduled to clear it, and setting it
would wedge the lane at G7.

**Round-2 closure verification.** All six round-2 rows were re-checked independently
against the plan text and the current tree rather than taken from the fix-table cells.
All six are genuinely closed.

- **BLOCKER 1 (`None`-arm and reorder residue in `## Step by Step Tasks`)** — closed.
  Task 1's clause bullet now appends unconditionally with the no-`is not None` rationale
  inline, and the reorder sentence is replaced by an explicit "do **not** reorder" citing
  Risk 1 and `test_step9_suppression_reaches_summary_before_pr_url`. Task 2's test bullet
  now reads present-and-zero / absent-on-zero-diff-and-no-candidates with the no-`None`
  reason stated. A grep of the whole `## Step by Step Tasks` range for `None`, `omit`, and
  `reorder` finds only the corrected instructions and the unrelated
  `manual_command=None` render argument. No residue of the defect survives.
- **BLOCKER 2 (two unsatisfiable zero-expectation `## Verification` rows)** — closed, and
  both rewritten commands were **run, not reasoned about**. The renamed
  "No live test references survive" row returns `15` on the current tree, identical to the
  broad pattern it replaces, so it catches everything the old row caught; run against a
  stub of the mandated guard class it returns `0` where the broad pattern returns `4`.
  The "Docs clean (bare word)" row returns `16` unfiltered and `0` against a stub doc
  containing only the `TestLivenessDeadCodeRemoved` bullet. Both reach their expected value
  by deletion alone. All 16 current bare-word doc matches were individually mapped to an
  enumerated Documentation cluster, and all 15 current test matches to a Test Impact
  bullet. The guard class is still mandated by name and the `Deletion guard exists` row
  still expects it.
- **CONCERN 1 (migrations-before-restart window)** — closed. `## Race Conditions` now
  names both ordering windows up front and carries the second as cosmetic Risk-3-class
  residue with the explicit "do not make the migration re-runnable" instruction.
  `scripts/update/run.py:1629` is verified as the Step 3.6 comment reading "after git pull,
  before service restart"; the service restart is genuinely later in the run.
- **CONCERN 2 (ORM seed-and-delete in task 6)** — closed. Task 6 now specifies a zero-I/O
  Jinja2 render following `tests/unit/test_per_project_modal.py`, and every citation checks
  out exactly: the `env` fixture at `:30-35`, `_base_reflection_ctx()` at `:38-53`,
  `_render_modal` at `:56-63`, `register_template_filters` imported from `ui.app` at `:25`.
  The claim that the helper "already supplies every top-level `r.*` field the template
  dereferences" was measured: the template dereferences 13 `r.*` fields, the helper supplies
  12, and the thirteenth is `r.last_run_summary` — precisely the one key the task instructs
  merging in. The task's second and third bullets are unchanged.
- **CONCERN 3 (ninth documentation cluster)** — closed. The cluster is enumerated at
  `docs/features/vault-drift-audit.md:254-266` with the bullet at `:263-264`, both verified
  exact, and `TestVaultDeadCodeRemoved` does sit at `:265` as the "leave it alone"
  instruction says. The count reads nine in `## Appetite`, `## Technical Approach` item 5,
  `## Risk 4`, the Team Members role, and task 4; Risk 4's impact line reads eight others.
- **NIT (missing `**Validates**` on tasks 4 and 5)** — closed. All six tasks now carry both
  `**Validates**` and `**Informed By**`.

**Citation re-derivation.** Every file:line the plan asserts was located by symbol on the
current tree, not trusted from the table. The four citations the revision pass reported
correcting are all correct as written: `return compared` at `reflections/docs_auditor.py:2398`,
the `except`-arm `return 0` at `:2401`, `_base_reflection_ctx()` at
`tests/unit/test_per_project_modal.py:38-53`, and the ninth cluster's bullet at
`docs/features/vault-drift-audit.md:263-264`. So are the constants at `:136-137`, the
comment at `:132`, `_write_liveness` at `:2153`, the detector signature at `:2370`, all five
call sites, the vault-count assignment at `:2460`, `withheld_note` at `:2524-2526`, all
eighteen `tests/unit/test_docs_auditor_substrate.py` citations, `modal_content.html:59`,
`agent/reflection_scheduler.py:644-648`, the migration precedents at
`scripts/update/migrations.py:1115` and `:1174-1199`, all seven `docs-auditor.md` cluster
ranges, the `vault-drift-audit.md:156-196` cluster, and the archived quote at
`docs/archive/plans-completed/docs-auditor-review-gate.md:1549-1557`. Three are off by a
few lines and are recorded as a nit below. The plan's load-bearing claim that the raw-Redis
guard cannot fire on Python source was also checked and holds: `validate_no_raw_redis_delete`
is reachable only through the `dispatch_pre_tool_use_bash` hook, whose manifest matcher is
`Bash`.

**One correction to the round-2 record.** That round's narrative gave the worst-case
created-PR summary as 196 characters. The authoritative figure is **195**, the number
carried by `## Technical Approach` item 1, `## Risk 1`, and `## Success Criteria`, and the
only one arithmetically consistent with the 227-character measurement including the
32-character clause. Task 1 instructs re-measuring during the build regardless.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Scope & Value | Risk 1's mitigation orders a permanent regression test — "Pin the budget with one test that builds a worst-case created-PR summary and asserts both that the vault clause survives and that the length stays under 500" — but no section a builder executes from repeats it. `## Technical Approach` item 1 says "Re-measure once during the build to confirm the number, then move on"; task 1 says only to record the number in the PR description; `## Test Impact` and the `## Verification` table contain no length assertion. This is the round-2 defect shape (a mandate landing in prose but not in the task list) recurring in a different section. It is a CONCERN rather than a BLOCKER because nothing here produces wrong code and no check becomes unsatisfiable: Technical Approach item 1 ends with "See Risk 1", so the pointer to the mandate does exist, and a builder following both sections writes the test, re-measures, and adds no reorder branch. | **Accepted on the record.** The concern re-critique bound is exhausted, so this does not gate the build. The builder should write the test; if it is skipped, the risk is a lost regression guard, not an incorrect change — the measured headroom is roughly 273 characters against a 500-character budget. | Add one bullet to task 2 beside the two vault-clause test bullets: drive `run_docs_auditor()` down the created-PR path with a mocked `audit()` result carrying 42 `files_touched` entries, `fixes_applied=137`, a non-zero `fixes_withheld` so `withheld_note` populates, a Telegram-suppression path so `suppressed_note` populates, and a six-digit `pr_url`; then assert `len(result["summary"]) < 500` and `"narratives compared" in result["summary"]`. Reuse the `TestHoistedPRGuards` / `TestStep9Suppression` fixture shapes already in the file rather than rebuilding the f-string by hand, so the test exercises the real code path Risk 1 protects. |
| NIT | Risk & Robustness, History & Consistency | The round-2 narrative paragraph in this section states the worst-case created-PR summary "holds (196 characters worst-case, 227 with the clause)", while `## Technical Approach` item 1, `## Risk 1`, and `## Success Criteria` all state 195. Only 195 is arithmetically consistent with the 227 figure and the 32-character clause. The disagreement lives entirely in the historical record and changes no pass/fail outcome. | **Corrected above.** The "One correction to the round-2 record" paragraph names 195 as authoritative; the three instructive sections were already correct and are untouched. | |
| NIT | Structural check | Three file:line citations are off by a few lines, though each names its target unambiguously in prose so a builder locating by symbol still finds it. The `## Documentation` "Inline Documentation" bullet cites `reflections/docs_auditor.py:2688-2691` for the "11. Liveness signal" step comment, which actually sits at `:2692-2694` (`:2689-2690` is the "10. Update rotation hash" step). `## Failure Path Test Strategy` cites the outer `except Exception` at `:2726` twice; it is at `:2727` and `:2726` is blank. `## Race Conditions` cites `scripts/update/run.py:1925` as the worker restart, but that line is the Step 4.5 Telegram auth check — service management is Step 5 at `:2245-2246`. The section's argument (migrations at Step 3.6 precede the restart) is unaffected and correct. | **Accepted on the record.** Cosmetic; each artifact is named by its text and locatable by symbol. | |

**Round-3 disposition (revision pass).** All three round-3 findings are closed in the plan
text. Nothing else moved: no section restructured, no closed row reopened, no scope added.

- **CONCERN (Risk 1's budget mandate lands in prose but in no executable section)** —
  closed. The mandate now appears in all three places a builder executes from.
  `## Test Impact` carries a **NEW** bullet naming
  `test_worst_case_summary_stays_under_truncation_budget`, with its worst-case payload (42
  `files_touched`, `fixes_applied=137`, non-zero `fixes_withheld`, suppressed Telegram
  path, six-digit `pr_url`) and its two assertions. Task 2 of `## Step by Step Tasks`
  repeats it as a bullet beside the two vault-clause test bullets and pins the exact
  function name. `## Verification` gains a `Truncation budget pinned` row,
  `grep -c 'def test_worst_case_summary_stays_under_truncation_budget' tests/unit/test_docs_auditor_substrate.py`
  expecting `1`. That row is satisfiable by construction because task 2 orders that exact
  name written, and its `grep -c` form is legitimate here: the hazard that forces `wc -l`
  on the deletion rows is `grep -c` exiting 1 on a zero count, which a
  non-zero-expectation row never hits. Measured on the current tree the row returns `0`,
  and it reaches `1` only by the builder writing the mandated test. `## Technical
  Approach` item 1 no longer ends with "re-measure once, then move on"; it names the
  permanent test and its three homes.
- **NIT (196 vs 195 in the round-2 narrative)** — closed. Round 3's rewrite of this
  section had already replaced the round-2 narrative wholesale, so no assertion of 196
  survives anywhere in the plan; re-verified by grep across the whole document. The only
  remaining occurrences of that figure are the "One correction to the round-2 record"
  paragraph, which names **195** as authoritative, and the NIT row above, which quotes the
  superseded wording as the finding itself. The three instructive sections
  (`## Technical Approach` item 1, `## Risk 1`, `## Success Criteria`) read 195 and were
  left untouched.
- **NIT (three drifted citations)** — closed, each re-derived by symbol against the
  current tree rather than copied from the finding text. The `## Documentation` "Inline
  Documentation" bullet now cites `reflections/docs_auditor.py:2692-2694` for the
  "11. Liveness signal" step comment (`:2689-2690` is the "10. Update rotation hash" step
  and `:2695` is the call the comment introduces). Both `## Failure Path Test Strategy`
  citations of `run_docs_auditor`'s outer `except Exception` now read `:2727`; `:2726` is
  blank. `## Race Conditions` no longer cites `scripts/update/run.py:1925`, which is the
  Step 4.5 Telegram auth check, and instead cites Step 5 "Service management" at `:2245`
  with `service.install_worker` at `:2311`. The section's argument is unchanged: Step 3.6
  migrations (`:1629`, re-verified) still precede the restart.

**Citation drift sweep.** Every other `reflections/docs_auditor.py` citation the plan
carries was re-derived by symbol in the same pass, because concurrent work on
`FALLBACK_ENG_CHAT` near the top of that file could have shifted every line below it. No
drift was found. `FALLBACK_ENG_CHAT` is still at `:44`, the Redis-namespace comment at
`:132`, the two constants at `:136-137`, `_write_liveness` at `:2153-2192` with its own
`except` at `:2191-2192`, `_run_vault_drift_detection` at `:2370` with `return 0` at
`:2384-2385`, `return compared` at `:2398`, the `except`-arm `return 0` at `:2401`, all
five call sites at `:2450`, `:2465`, `:2493`, `:2573`, `:2695`, the vault-count
assignment at `:2460`, and `withheld_note` at `:2524-2526`.
`agent/reflection_scheduler.py:648` is still
`output_summary=str(summary_str)[:500] if summary_str else None`. No task-1 or task-2 line
number needed correcting.

---

War room, LITE depth: Consolidated Critic, plus automated structural validation. Mode:
independent roster (1 critic).
Round 4 (delta re-critique of commit `5dbce4325`). Verdict: READY TO BUILD (no concerns) —
0 blockers, 0 concerns, 1 nit.

**Why LITE and why a delta.** `appetite: Small`, no doctrine path touched. Round 3 already
ran a FULL-depth roster over the whole plan and returned zero blockers, and rounds 1 and 2
were each verified closed there. The only change since is `5dbce4325`, which touched the
plan file alone, so round 4 reviewed that diff and its consistency with the sections it
edits rather than re-deriving the document.

**Round-3 closures, verified independently against the tree.**

- **CONCERN (budget mandate not executable)** — **verified closed.** The name
  `test_worst_case_summary_stays_under_truncation_budget` is byte-identical across all three
  executable homes: `## Test Impact` (`:462`, marked **NEW**), task 2 of
  `## Step by Step Tasks` (`:856-861`, "Use that exact function name"), and the
  `Truncation budget pinned` row of `## Verification` (`:957`). The row's command,
  `grep -c 'def test_worst_case_summary_stays_under_truncation_budget' tests/unit/test_docs_auditor_substrate.py`
  expecting `1`, is therefore satisfiable by construction: it returns `0` on the current
  tree and reaches `1` only by the builder writing the name task 2 orders.
  `## Technical Approach` item 1 (`:313-324`) no longer ends with "re-measure once during
  the build to confirm the number, then move on"; it now reads "The build pins the budget
  with a permanent test rather than a one-off measurement" and names the test's three homes.
- **NIT (196 vs 195 characters)** — **verified closed.** No live assertion of 196 as the
  worst-case figure survives. Every remaining `196` in the document is either an unrelated
  line-range citation (`:156-196`, `:1960`) or historical record: the "One correction to
  the round-2 record" paragraph, which names **195** as authoritative, and the round-3 NIT
  row, which quotes the superseded wording as the finding itself. `## Technical Approach`
  item 1 (`:316`), `## Risk 1` (`:522`), and `## Success Criteria` (`:753`) all read 195,
  consistent with 195 + 32 = 227.
- **NIT (three drifted citations)** — **verified closed**, each re-derived by symbol on the
  current tree. `reflections/docs_auditor.py:2692-2694` is the "11. Liveness signal"
  comment, with `:2689-2690` the "10. Update rotation hash" step and `:2695` the call it
  introduces. `run_docs_auditor`'s outer `except Exception as e:` is at `:2727`, `:2726`
  blank; both `## Failure Path Test Strategy` citations now read `:2727`.
  `scripts/update/run.py:2245` is `# Step 5: Service management` and `:2311` is
  `service.install_worker(project_dir)`; `:1629` is still the Step 3.6 migration comment,
  so the section's ordering argument holds unchanged.

**Citation drift sweep, spot-checked.** `FALLBACK_ENG_CHAT` at `:44`, the Redis-namespace
comment at `:132`, `REDIS_LAST_COMPLETED_TS_KEY`/`REDIS_LAST_COMPLETED_SUMMARY_KEY` at
`:136-137`, `def _write_liveness` at `:2153`, `_run_vault_drift_detection` at `:2370` with
`return 0` at `:2385` and `:2401` and `return compared` at `:2398`, the vault-count
assignment at `:2460`, `withheld_note` at `:2524-2526`, all five call sites at `:2450`,
`:2465`, `:2493`, `:2573`, `:2695`, and `agent/reflection_scheduler.py:648` — all accurate.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| NIT | Consolidated Critic, Structural check | The citation drift sweep records `_write_liveness`'s own `except` at `:2191-2192`; the `except Exception as e:` is at `:2190` and its `logger.warning` at `:2191`, with `:2192` the blank separator. The stated function range `:2153-2192` is right for a deletion (it carries the trailing separator line). This lives in the round-3 disposition record, not in any section a builder executes from. | **Accepted on the record.** Cosmetic and non-gating; the symbol is named unambiguously and locatable by `grep -n 'def _write_liveness'`. | |

**Round-4 disposition.** Zero blockers, zero concerns. `plan_revising` stays `false` and the
lane proceeds to `/do-build`.
