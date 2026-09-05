---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/2743
last_comment_id:
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
- PM check-ins: 0 (scope is fully determined by the recon; the one judgment call is
  recorded as an Open Question with a recommended default so the build is never blocked)
- Review rounds: 1

The deletion itself is mechanical. The cost is in not losing anything on the way out:
seven test patch sites, two feature docs with eight reference clusters between them, and one
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
(`:2384-2385`), `return compared` on success (`:2396`), and `return 0` from its
`except Exception` (`:2399-2400`). The local at `:2460` is therefore never `None`, and a
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
the end of the string, and there is no reordering fallback. Re-measure once during the
build to confirm the number, then move on; do not introduce a conditional reorder branch
that no test would ever exercise. See Risk 1.

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

**5. Docs.** Eight reference clusters across two files, enumerated in the Documentation
section. The `## Rotation State` section the issue names is only one of them; the issue
was written before #2782 and #2739 added the rest.

## Failure Path Test Strategy

### Exception Handling Coverage

- [x] The one handler in scope is `_write_liveness`'s own
      `except Exception as e: logger.warning(...)` (`:2191-2192`). It is deleted with the
      function, so its coverage obligation disappears rather than needing a new test. No
      handler is added by this work.
- [ ] Confirm no other `except` block in `run_docs_auditor` changes behavior. The outer
      `except Exception` at `:2726` converts any raise into `{"status": "error"}`; the
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
      (`:2384`, `:2396`, `:2399-2400`).
- [ ] Empty `summary` reaching the scheduler is out of scope: every return path builds a
      non-empty f-string, and the scheduler's `if summary_str else None` guard
      (`:648`) already handles a falsy value.

### Error State Rendering

- [ ] The user-visible surface is the reflections dashboard modal. Its render is guarded
      by `{% if r.last_run_summary and r.last_run_summary.output_summary %}`
      (`modal_content.html:59`), so a missing summary renders nothing rather than erroring.
      No change needed; verify the guard is untouched.
- [ ] The `"error"` return path (`:2726`) carries its own summary and is unaffected by
      this work. Confirm by test that an exception inside the rotation still produces a
      summary string the scheduler can forward.

## Test Impact

All in `tests/unit/test_docs_auditor_substrate.py`.

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
- [ ] `TestStep9Suppression::test_step9_suppression_reaches_summary_before_pr_url`
      (`:1762`, assertion at `:1787`, patch at `:1781`). **UPDATE**: drop the
      `_write_liveness` patch line only. Its ordering invariant
      (`summary.index("suppressed") < summary.index("PR=")`) is why the vault clause is
      appended after `PR={pr_url}` and never reordered ahead of it; leave the assertion
      untouched and do not let a truncation-headroom edit displace `suppressed_note`.

No test outside this file references `_write_liveness` or either constant.

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

**Impact:** The issue names only `## Rotation State`. Following it literally leaves seven
other reference clusters, including a whole `## Liveness signal` section in
`vault-drift-audit.md` describing a function that no longer exists. That is precisely the
"historical artifact in docs" Principle 1 forbids, and the docs-auditor itself would file
an issue about it.

**Mitigation:** The Documentation section below enumerates all eight clusters by file and
line. The Verification table greps both files for zero occurrences of `_write_liveness`
and `last_completed_run`.

## Race Conditions

No race conditions identified. The work deletes two `r.set` calls and adds no concurrent
access. `run_docs_auditor` already serializes itself behind `docs_audit:running:global`
(SETNX, 1h TTL) and the deleted writes were the last operations before a return, read by
nobody. The summary string is built and returned synchronously inside the same function
call the scheduler awaits.

One ordering fact worth stating so it is not mistaken for a race: the two orphaned keys
may still hold values while a machine runs post-deletion code but pre-migration `/update`.
That window is benign because nothing reads them in either state, and the migration is
idempotent.

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

- [ ] `docs/features/README.md`: check whether either file's index row summary mentions
      the liveness keys; update if so, leave alone if not.

### External Documentation Site

- [ ] Check `site/` for any page describing the docs-auditor's Redis surface. The
      vault↔site drift detector compares vault narratives against site pages, so a stale
      site page here would be a finding the auditor files against itself.

### Inline Documentation

- [ ] `reflections/docs_auditor.py:132`: the `# Redis key namespace for state/locks/
      liveness.` comment loses its "liveness" clause with the constants.
- [ ] `reflections/docs_auditor.py:2688-2691`: the "11. Liveness signal" step comment
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
  - Role: the eight reference clusters across `docs-auditor.md` and `vault-drift-audit.md`
  - Agent Type: documentarian
  - Resume: true

- **Validator (deletion completeness)**
  - Name: `liveness-validator`
  - Role: prove the symbols are gone, the archives are untouched, the vault count reaches
    the summary on `0`, and the truncation budget holds
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
- Append the vault clause to the created-PR summary string, emitting on `0` and omitting
  on `None`. Measure the worst-case summary length against the scheduler's 500-character
  truncation and record the number in the PR description; reorder the clause ahead of
  `PR={pr_url}` if the budget is tight.
- Delete `_write_liveness` (`:2153-2192`) and the two constants (`:136-137`); trim the
  "liveness" clause from the `:132` comment.
- Run `python -m ruff check reflections/docs_auditor.py`. An F841 on
  `vault_narratives_compared` at `:2460` means the rehoming step did not land.

### 2. Repair and extend the tests

- **Task ID**: build-tests
- **Depends On**: build-delete-liveness
- **Validates**: `tests/unit/test_docs_auditor_substrate.py`
- **Informed By**: Test Impact (all eight bullets)
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
- Add the two vault-clause tests: present-and-zero, absent-when-None.
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
- **Assigned To**: liveness-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Work the eight clusters enumerated in the Documentation section.
- Rewrite `vault-drift-audit.md`'s `## Liveness signal` around the summary clause, keeping
  the three-way `0`/absent/unresolvable distinction intact.
- Touch nothing under `docs/archive/`.

### 5. Final validation

- **Task ID**: validate-all
- **Depends On**: build-delete-liveness, build-tests, build-migration, document-feature
- **Assigned To**: liveness-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table.
- Confirm `git diff --stat -- docs/archive/` is empty.
- Confirm the vault clause renders on a `0` count by driving the created-PR path, not by
  reading the code.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Function gone | `grep -rc '_write_liveness' reflections/` | match count == 0 |
| Constants gone | `grep -rc 'REDIS_LAST_COMPLETED' reflections/` | match count == 0 |
| Keys gone from code | `grep -rn 'last_completed_run' reflections/ ui/ agent/ models/ \| wc -l` | match count == 0 |
| No test references survive | `grep -c '_write_liveness\|REDIS_LAST_COMPLETED' tests/unit/test_docs_auditor_substrate.py` | match count == 0 |
| Deletion guard exists | `grep -c 'class TestLivenessDeadCodeRemoved' tests/unit/test_docs_auditor_substrate.py` | output > 0 |
| Vault count rehomed | `grep -c 'vault_narratives_compared' reflections/docs_auditor.py` | output > 0 |
| Withheld count still asserted behaviorally | `grep -c 'fix(es) withheld" in result\["summary"\]' tests/unit/test_docs_auditor_substrate.py` | output > 0 |
| Docs clean (docs-auditor) | `grep -c '_write_liveness\|last_completed_run' docs/features/docs-auditor.md` | match count == 0 |
| Docs clean (vault-drift) | `grep -c '_write_liveness\|last_completed_run' docs/features/vault-drift-audit.md` | match count == 0 |
| Archives untouched | `git diff --stat origin/main -- docs/archive/ \| wc -l` | match count == 0 |
| Migration registered | `grep -c 'clear_docs_audit_liveness_keys' scripts/update/migrations.py` | output > 0 |
| Auditor tests pass | `scripts/pytest-clean.sh tests/unit/test_docs_auditor_substrate.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

War room, FULL depth: Risk & Robustness, Scope & Value, History & Consistency, plus automated structural validation.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| BLOCKER | Structural check | The plan's `None` arm for the vault clause is unreachable in production. `_run_vault_drift_detection` is annotated `-> int` at `reflections/docs_auditor.py:2370` and returns `0` on every path: `return 0` when `_resolve_vault_root` yields None (:2384-2385), `return compared` (an int) on success, and `return 0` from its `except Exception` (:2399-2400). So `vault_narratives_compared` at `:2460` is always an int and never `None`. Four sections prescribe the opposite: Failure Path Test Strategy says "the created-PR path when `_run_vault_drift_detection` returns `None`"; Test Impact orders a new "absent-when-None" test; Success Criteria requires "emitted on `0` and omitted on `None`"; Documentation tells the documentarian to keep a three-way distinction whose "absent" arm the source doc actually defines as "key absent entirely, from a call site that never ran the vault comparison" (`docs/features/vault-drift-audit.md:184-186`), meaning a different call site, never a `None` return. A builder following this literally patches the function to return `None` against its own annotation and ships a dead `is not None` guard, adding dead code inside a dead-code-deletion PR. | pending | The real distinction to preserve is per-call-site, not per-value: today all four non-PR call sites pass no vault count at all, and the created-PR site always passes an int, so the key is always present on the PR path. The faithful replacement is therefore unconditional: append the clause to the created-PR summary string with no `is not None` guard, and leave the other four summary strings without it. Rewrite Success Criteria to "the created-PR summary always carries the vault count, including when it is 0; no other summary string carries it", replace the "absent-when-None" test with one asserting the clause is absent from the zero-diff and no-candidates summaries, and reword `vault-drift-audit.md`'s three-way distinction so "the mapping is silently broken" maps to "the run never reached the created-PR path", not to a `None` return. |
| CONCERN | History & Consistency, Structural check | The `## Tests` documentation cluster is bounded at `docs/features/docs-auditor.md:738-750`, but a stale reference survives at `:762`: `TestWithheldBlocksStaleClose` is described as "a bare-name withhold reaching the PR body, Telegram, and liveness". That sentence describes the surface being deleted and a test the plan renames to drop `_and_liveness` (Test Impact bullet 2), yet it falls outside all seven enumerated clusters. Both Verification rows for this file grep only `_write_liveness` and `last_completed_run`, neither of which appears on that line, so the checks report clean while the stale sentence survives. This is exactly the residue Risk 4 exists to prevent, escaping through Risk 4's own mitigation. | pending | Extend the `## Tests` cluster range to `:738-765` and reword `:762` to drop "and liveness". Add a Verification row `grep -c 'liveness' docs/features/docs-auditor.md docs/features/vault-drift-audit.md` expecting 0, since the existing literal-string greps are provably blind to the bare word. |
| CONCERN | Risk & Robustness | Technical Approach item 4 cites `_migrate_clear_orphaned_warn_state_key` (`scripts/update/migrations.py:1174-1200`) as "a direct precedent, written for the same shape of problem", but that function never touches Redis. It calls `warn_state.should_emit(_ORPHANED_WARN_KEY, "", project_dir)`, which pops a key from `data/update_warn_state.json`. The precedent settles the fail-soft shape but not how `migrations.py` obtains a Redis connection, which is the part the build actually has to invent. | pending | `reflections/docs_auditor._get_redis()` exists at `reflections/docs_auditor.py:183` and is the natural source, reached the way `_migrate_backfill_job_last_active_scores` reaches `models.job`: `sys.path.insert(0, str(project_dir))` then import inside the try block. Confirm the import resolves with no circular import after the constants are deleted, and hard-code the two key strings in the migration rather than importing the constants, which this change removes. Keep the precedent's fail-soft shape: wrap in try/except, log, and `return None` unconditionally. |
| CONCERN | Risk & Robustness | Risk 1's fallback, "the clause moves ahead of `PR={pr_url}`", collides with an existing invariant test the plan never names: `test_step9_suppression_reaches_summary_before_pr_url` (`tests/unit/test_docs_auditor_substrate.py:1762`) asserts `result["summary"].index("suppressed") < result["summary"].index("PR=")` at `:1787`. Reordering the string to buy truncation headroom can displace `suppressed_note` past `PR=` and break that assertion, and the Test Impact list does not include it. | pending | The fallback is very likely unnecessary: a worst-realistic created-PR summary (long slug, withheld note, Telegram suppressed, real PR URL) measures about 194 characters, so the roughly 35-character clause leaves about 270 characters of headroom against the 500-character truncation at `agent/reflection_scheduler.py:648`. Measure it in the build and, if it clears, delete the reorder fallback from Risk 1 rather than leaving a hazardous unexercised branch. If the reorder is ever taken, insert the clause after `{withheld_note}{suppressed_note}` and before `, PR=`, and add `test_step9_suppression_reaches_summary_before_pr_url` to the Test Impact list. |
| CONCERN | Scope & Value | The plan's whole justification is that operators now read this information on the dashboard, but every Success Criterion, Verification row, and validator task is a grep, a `result["summary"]` string containment, or `git diff --stat`. Nothing reaches the template layer the premise depends on. Task 5 asks the validator to "Confirm the vault clause renders on a `0` count by driving the created-PR path", which still stops at the returned dict. | pending | The render guard `{% if r.last_run_summary and r.last_run_summary.output_summary %}` at `ui/templates/reflections/_partials/modal_content.html:59` is untouched by this change and exercised by nothing in Test Impact. Add one validator task that seeds a `Reflection` through the ORM with `mark_completed(duration, output_summary=<the new PR summary>)` and asserts the rendered modal partial contains the vault clause, so the end-to-end claim is demonstrated once rather than assumed. |
| CONCERN | Scope & Value | Open Question 1 self-selects Option (a), adding a new user-visible clause to an operator-facing string, under an appetite that declares "PM check-ins: 0". The plan frames the choice as forced by `ruff` F841, but F841 only requires the local at `:2460` to be consumed: `_ = _run_vault_drift_detection(project_key)` clears it with zero behavioral change, which is Option (b). The constraint forces a resolution, not this one. The BLOCKER above weakens Option (a)'s stated rationale further, since the `0`-versus-absent argument it rests on does not describe real behavior. | pending | Re-argue Option (a) on the surviving ground rather than the `None` distinction: the count still distinguishes "detector ran and compared N narratives" from "detector ran and compared 0", which is the observability guarantee PR #2096 argued for and the only one at stake. If that argument holds, keep (a) and say so explicitly in Technical Approach item 1; if it does not, take (b) and delete the `vault_narratives_compared` rows from Data Flow, Test Impact, and Success Criteria in the same pass. Either way the answer belongs in the plan text before build, not in an Open Question. |
| CONCERN | Structural check | The Data Flow field-coverage table, whose stated purpose is "to prove the replacement channel carries everything the deleted one did", marks `slug` as "Yes" citing the zero-diff and PR-cap summary strings. The created-PR path carries no slug: its summary is `f"docs-auditor: {len(files_touched)} files touched, {fixes} fixes{withheld_note}{suppressed_note}, PR={pr_url}"` (`reflections/docs_auditor.py:2718-2723`), while `_write_liveness(slug, "ok", ...)` at `:2695-2702` carried the real doc slug. Two of five paths carry it, and the one that loses real information is marked covered without qualification. | pending | The datum is recoverable from the PR URL, so this is an accuracy fix to the table, not a scope change. Change the `slug` row to "Partial: on the zero-diff and PR-cap paths; on the created-PR path it is recoverable from the PR URL, and the dirty-tree and no-candidates paths carried only the literal placeholders `(dirty)` and `(no-candidates)` that their summary prose already states". If the build instead decides the created-PR slug is worth keeping, add it to that summary string in the same edit as the vault clause. |
| NIT | Structural check | Six Verification rows read `grep -c ...` with an Expected of "match count == 0". `grep -c` exits 1 when the count is zero, so a validator or wrapper that gates on exit status reads every one of those rows as a failure. Separately, the first three rows use `grep -rc PATTERN reflections/`, which prints one `file:count` line per file in the tree rather than a single number. | pending |  |

## Open Questions

One question, with a recommended default so the build is never blocked on an answer.

1. **Where does `vault_narratives_compared` land?** It is the one field the liveness
   payload carries that no summary string does, and deleting `_write_liveness` orphans its
   local variable, so the build must choose. Three options:

   a. **Append it to the created-PR summary string** (recommended, and what this plan is
      written around). Preserves the `0`-versus-absent distinction #2084 built the field
      for, keeps today's asymmetry exactly, costs about 35 characters against a
      500-character budget.

   b. **Drop it.** Call `_run_vault_drift_detection(project_key)` for its side effect
      (issue filing) and discard the return. Simplest diff, and it deletes an
      observability guarantee a prior plan argued for across a full critique round. Cheap
      now, expensive the day the vault mapping silently breaks.

   c. **Thread it into all four post-vault summary strings.** More useful than (a), and a
      behavioral widening this issue did not ask for. Belongs in its own issue.

   Proceeding on (a) unless told otherwise.
