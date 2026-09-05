---
status: Planning
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
| "its four call sites" | `:2450`, `:2465`, `:2493`, `:2573`, `:2695` | **Drifted — five, not four.** `:2493` is the PR-cap / open-PR guard #2739 added |
| `models/reflection.py:186`, `:221` | `mark_completed(output_summary=...)` param at `:186`, stored at `:221` | Holds, exact |
| `ui/data/reflections.py:139`, `:286` | `last_run_summary` dict at `:139`, `output_summary` at `:286` | Holds, exact |
| Fixed 4-arg signature | `:2153-2160` now takes six params | **Drifted** — `vault_narratives_compared` (#2096) and `fixes_withheld` (#2782) |

**Cited sibling issues/PRs re-checked:**

- **#2739** — CLOSED 2026-08-28 via PR #2887 (`7ccd27d5d`). The prerequisite this issue
  names. Verified in code, not assumed: see the Data Flow section for the full chain.
- **#2782** — MERGED 2026-08-13 (`ffbae5b1d`). Added `fixes_withheld` to the liveness
  payload after this issue was filed, which is why the issue's signature description is
  stale.
- **#2741** — CLOSED 2026-08-18 via PR #2842 (`a9205b065`). Deleted the rename channel
  from the same module. Not a dependency; a directly reusable precedent for how this repo
  lands a dead-code deletion.
- **#2834** — CLOSED, folded into #2739's lane.

**Commits on main since the issue was filed (touching referenced files):**

- `7ccd27d5d` fix(docs-auditor): review-gate every write (#2739, #2834) — **changed the
  premise**. Added a fifth `_write_liveness` call site, rewrote the rotation's outcome
  vocabulary so four of five post-lock returns report `"skipped"`, and added the
  `modal_content.html` render of `output_summary` that makes the dashboard a real reader.
- `ffbae5b1d` fix(docs-auditor): migration-context hatch and bare-path existence
  invariant (#2782) — **partially opposes**. Threaded `fixes_withheld` into the liveness
  payload and wrote the "only durable, queryable surface" docstring. That claim is what
  this plan retires.
- `a9205b065` (#2741), `45d0961f9` (#2728), `15023ee97`, `5eaa74230`, `6c68f29ab`,
  `974be6532` — touched the same module elsewhere; none touch `_write_liveness` or its
  keys.
- `90a319df7`, `974e8d4c9`/`974eb8d4c` — scheduler changes; neither touches the
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

- **PR #2842 / #2741** — "chore(#2741): delete the docs-auditor rename channel". Deleted
  six symbols and a module-level global from this exact file, and pinned the removal with
  a grep-assertion test class. The closest precedent available; this plan copies its
  shape, including the `TestVaultDeadCodeRemoved`-style guard class already living at
  `tests/unit/test_docs_auditor_substrate.py:3093`.
- **PR #2887 / #2739** — "review-gate every write, report broken .md links". The
  prerequisite. Built the `output_summary` channel end to end and explicitly deferred this
  deletion, recording the re-argument requirement quoted in the Freshness Check.
- **PR #2782** — "migration-context hatch and bare-path existence invariant". Added
  `fixes_withheld` to the liveness payload. Its two test assertions
  (`liveness.call_args.kwargs["fixes_withheld"]`) are the only behavioral coverage that
  the withheld count reaches a durable surface, so they must be re-pointed rather than
  deleted.
- **PR #2096 / #2084** — "Integrate the work-vault knowledge base". Added
  `vault_narratives_compared` and the `## Liveness signal` section of
  `docs/features/vault-drift-audit.md`. Its stated goal, making "detector ran, found zero
  drift" distinguishable from "the mapping is silently broken", is a real requirement that
  outlives the channel it was built on.
- **PR #1253 / #1247** — "Consolidate docs hygiene: unified auditor substrate". Introduced
  `_write_liveness` in the first place, as a Phase-2 answer to critique finding O1: "No
  liveness signal during Phase 2 — how does PM know the reflection is actually running?"
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

**Channel A — the one being deleted:**

1. **Entry point**: `run_docs_auditor()` reaches one of five post-lock returns.
2. **`_write_liveness(...)`** (`reflections/docs_auditor.py:2153`) builds a dict of
   `slug`, `pr_url`, `files_touched`, `status`, plus `vault_narratives_compared` when not
   `None` and `fixes_withheld` when non-zero.
3. **Redis**: `r.set("docs_audit:last_completed_run_ts", str(time.time()))` and
   `r.set("docs_audit:last_completed_run_summary", json.dumps(summary))`. No TTL.
4. **Output**: nothing. A human types `redis-cli GET` on the right machine, or the value
   is never seen. Failures are swallowed into `logger.warning`.

**Channel B — the one that stays:**

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
| `slug` | Yes | `f"docs-auditor: zero-diff ({slug})"`, `f"docs-auditor skipped ({slug}): {reason}"` |
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
seven test patch sites, two feature docs with six reference clusters between them, and one
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

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

_placeholder_

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

_placeholder_

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

_placeholder_

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

## Open Questions

_placeholder_
