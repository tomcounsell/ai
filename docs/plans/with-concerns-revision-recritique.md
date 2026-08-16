---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-16
tracking: https://github.com/yudame/ai/issues/2787
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-16T05:09:00Z
---

# A "READY TO BUILD (with concerns)" verdict must route the concern-closing revision back through critique

## Problem

`/do-plan-critique` can return **READY TO BUILD (with concerns)**. The pipeline
responds by dispatching `/do-plan` for a revision pass that embeds each concern's
Implementation Note into the plan, and then dispatches `/do-build`. Nothing ever
looks at the revision. It is the part of the plan written last, under the least
scrutiny, specifically to close findings the critics flagged as risky — and it is
the only part of the plan that is never reviewed.

The `verdict-finalize-cluster` lane (#2740 / #2767 / #2769) is the worked example.
Round 2 returned `READY TO BUILD (with concerns)` and prescribed a two-way
`isinstance` guard for `_extract_head_sha`. Round 3 caught that the duality is
actually three-way — `verdicts.get("REVIEW")` returns `None` for every issue not
yet reviewed, which is most live ledgers — and that a literal reading of round 2's
prescription routes that `None` into `re.compile(...).search()`, raising
`TypeError` that `tools/sdlc_next_skill.py::_resolve_enriched` swallows into an
empty ledger, sending a fully-worked issue back to `/do-plan`. Round 3's own words:
"a defect in the round-2 fix's own prescription", and strictly worse than the
blocker round 2 was written to prevent. Had round 2's verdict been terminal, that
defect ships.

**Current behavior**

Three things combine to make this unavoidable rather than unlucky:

1. **Router row 4c dispatches build on a with-concerns verdict.**
   `_rule_critique_ready_with_concerns_revision_applied`
   (`agent/sdlc_router.py:897`, registered at `:1600`) fires `/do-build` whenever
   the verdict contains `READY TO BUILD` + `WITH CONCERNS` and `meta["revision_applied"]`
   is truthy. There is no re-critique edge anywhere between the revision and BUILD.
2. **`revision_applied` is sticky.** `/do-plan` Phase 4 step 2a sets it `true` on
   every revision pass and it never resets. So from round 2 onward, row 4b
   (`/do-plan`, keyed on `not revision_applied`) can never fire again and row 4c
   fires unconditionally. **The more critique rounds a plan needs, the less each
   round is reviewed** — the opposite of the intended gradient.
3. **The `plan_revising` lock is inert on exactly these plans.**
   `/do-plan-critique` Step 5.6 skips setting the lock when `revision_applied: true`
   is already in the frontmatter, and even a hand-set lock is erased by G7 rule 3,
   which self-heals to `None` whenever `plan_revising` and `revision_applied` are
   both truthy. Once a plan has been revised once, the lock cannot bind again.

**Desired outcome**

A concern-closing revision is critiqued before it is built on, in a loop that is
bounded by construction, that costs a scoped pass rather than a full war room, and
that terminates in an explicit, recorded acceptance of residual concerns rather
than an escalation that strands the lane.

## Freshness Check

**Baseline commit:** `8b13098bc`
**Issue filed at:** 2026-08-13T11:54:32Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `agent/sdlc_router.py:897` — `_rule_critique_ready_with_concerns_revision_applied` — **still holds**, verbatim as the issue describes. Registered as row `4c` at `agent/sdlc_router.py:1600`.
- `agent/sdlc_router.py:873` — `_rule_critique_ready_with_concerns_no_revision` (row `4b`, `agent/sdlc_router.py:1594`) — **still holds**; keyed on `not meta["revision_applied"]`.
- `agent/sdlc_router.py:315` — `guard_g1_critique_loop` — **still holds**; fires only on `NEEDS REVISION` / `MAJOR REWORK`, and steps aside on `pr_number`. With-concerns is deliberately outside it.
- `agent/sdlc_router.py:350` — `guard_g2_critique_cycle_cap` — **still holds**, and is confirmed inert on this path (see Revised bucket in the issue's Recon Summary; `agent/pipeline_state.py:1074` increments `critique_cycle_count` only inside `fail_stage("CRITIQUE")`, and a with-concerns verdict marks CRITIQUE `completed`).
- `agent/sdlc_router.py:677-682` — G7 gates 2 and 3 — **still holds**; rule 3's `revision_applied` self-heal is present and is what makes a `plan_revising`-keyed fix dead on arrival.
- `agent/sdlc_router.py:1118-1128` — the #1760 `revision_applied_at` convergence latch inside `_critique_verdict_is_stale` — **still holds**, including #2049's `requires_revision` verdict-kind gate.
- `.claude/skills-global/do-plan-critique/SKILL.md:414-421` — Step 5.6 and its `revision_applied: true` exemption clause — **still holds**.
- `.claude/skills/sdlc/SKILL.md` — the "Convergence latch" and "G7 blocks build" paragraphs — **still hold**.

**Cited sibling issues/PRs re-checked:**

- #2740 / #2767 / #2769 — all closed; shipped in PR #2790, merged 2026-08-13T12:18:56Z (24 minutes *after* this issue was filed). The merge did not touch the with-concerns rows; it added row 2b's stale-verdict ownership. Confirmed irrelevant to the hole.
- #1760 / #1761 — closed. These are the direct counter-precedent and are treated as a first-class design constraint below, not a footnote.
- #1639 — closed; introduced row 2b, the structural sibling of the row this plan adds.
- #1302 — closed; introduced the `plan_revising` lock this plan declines to build on.

**Commits on main since issue was filed (touching referenced files):**

- `706fc4da0` "Honest finalize refusals, a clean verdict field, documented count flags, and a router row that owns the stale-verdict state (#2740) (#2767) (#2769) (#2790)" — **partially adjacent**: adds row 2b's stale-verdict handling and the `_verdicts[stage].verdict` field the new predicate reads. Does not address the with-concerns hole. Line numbers cited in the issue were re-read post-merge and are the ones above.
- `4f778447f` "Make the SDLC dispatch record unbypassable via a router upsert slot (#2730) (#2802)" — hardens `_sdlc_dispatches` as an unbypassable record of *what was dispatched*. **It does not make that history durable.** `agent/sdlc_router.py:98` defines `MAX_DISPATCH_HISTORY = 10` and `record_dispatch` FIFO-evicts to it at `agent/sdlc_router.py:1990-1991`; `tools/sdlc_dispatch.py` does not slice, but it delegates to `agent.sdlc_router.record_dispatch` (`tools/sdlc_dispatch.py:108,123`) and reports `len(history)` *after* truncation. Round 1 of critique caught that this plan's original bound read the wrong module. `_sdlc_dispatches` is therefore **not** a valid basis for a loop bound and this plan no longer uses it (see Technical Approach §3).
- `e2d3cf209` "next-skill takes the caller's run identity instead of guessing it (#2818)" — irrelevant to the routing rows; affects only the issue-lock peek.

**Active plans in `docs/plans/` overlapping this area:** `g8-terminal-pipeline-no-rebuild.md` (#2757) touches SDLC routing but on the *G8 / terminal-stage* axis, not the CRITIQUE rows. Its frontmatter is the live evidence quoted in the Recon Summary. No edit collision: this plan touches rows 4b/4c, G7 gate 3, and Step 5.6; #2757 touches G8 and the terminal-pipeline predicates. Coordination signal only, not a blocker.

**Notes:** The one genuine drift is that PR #2790 landed 24 minutes after the issue was filed and shifted line numbers within `agent/sdlc_router.py`. All line references in this plan are re-read against `8b13098bc`.

## Prior Art

- **#1760 / #1761**: "investigation: /do-sdlc PLAN↔CRITIQUE router never converges to BUILD (notes-only revision re-stales a clean verdict)" — **the counter-precedent**. A revision re-staled the critique verdict, row 2b re-dispatched critique, the new verdict triggered another revision, forever. Fixed by adding the event-scoped `revision_applied_at` latch to `_critique_verdict_is_stale`. This issue asks to *re-add* a re-critique edge that #1760 removed. The plan's entire safety argument is that the two are disjoint (below).
- **#1925 / #1968 (via #2049, WS4)**: the #1760 latch *recurred* as a deadlock because `/do-plan` rewrites `revision_applied_at` on every pass, re-arming suppression each round. Fixed by gating the latch on verdict kind. Evidence that naive timestamp latches in this exact code path have failed twice; the new predicate must be analyzed for the same re-arming shape.
- **#1639 / PR #1659**: "router — NEEDS REVISION + revision applied has no route back to re-critique (dead-end loop on /do-plan)" — **the structural twin**. The identical gap on the NEEDS REVISION path, fixed by adding row 2b (`_rule_critique_verdict_stale`) routing back to `/do-plan-critique`. This plan is the with-concerns analogue of that fix, and should look like it.
- **#1302 / PR (plan-revising lock)**: "do-build must block on in-flight do-plan-critique rounds" — introduced `_meta.plan_revising` and G7. The intent matches this issue exactly; the mechanism is what the sticky `revision_applied` conjunction defeated.
- **PR #815**: "propagation check, Implementation Note field, and concern-triggered revision pass" — introduced the with-concerns revision pass in the first place. It added the revision but never a review of it; this plan closes the half it left open.
- **PR #1554**: "Fix SDLC router: guard rule 4b against re-firing once a PR exists" — the D3 `pr_number` / `BUILD == completed` step-asides on rows 4a/4b/4c. Any new row must carry them or it re-dispatches on a finished PR.

## Research

No relevant external findings — this is entirely internal SDLC-router and skill-contract work with no external libraries, APIs, or ecosystem patterns involved. Phase 0.7 WebSearch skipped per the skill's own skip condition.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| PR #815 | Added the with-concerns revision pass and the Implementation Note field | Shipped the *write* half of the loop and none of the *read* half. A revision was mandated; a review of that revision never was. The verdict was left doing two jobs — "safe to build" and "here are N unreviewed changes to make" — and only the first is visible in its name. |
| #1302 (plan-revising lock) | `_meta.plan_revising` + G7 to block build during a revision | Keyed the release on the *sticky* `revision_applied` boolean. G7 rule 3 self-heals the lock whenever both are truthy, so after the first revision the lock is permanently unarmable. Correct intent, defeated by a state field that cannot express "since this round". |
| #1760 latch | `revision_applied_at` to stop a settle revision re-staling a clean verdict | Correct and still correct — but it was scoped by *timestamp only*, so it suppressed re-critique for the with-concerns case too. #2049 had to bolt on a verdict-kind gate after the #1925/#1968 deadlock recurrence. The lesson: in this code path, "should we re-critique?" must be decided by **verdict kind AND event ordering together**, never by either alone. |

**Root cause pattern:** every prior attempt keyed a per-round decision on a
process-lifetime-sticky boolean (`revision_applied`) or on a bare timestamp
without the verdict kind. Neither can express "has *this* verdict's revision been
judged?". The fix must ask exactly that question, and it must ask it with both
halves.

## Architectural Impact

- **New dependencies**: none. `agent/sdlc_router.py` stays import-free of `tools/` (enforced by `tests/unit/test_architectural_constraints.py`). `agent/pipeline_graph.py` gains an `os` import (stdlib) for the constant's env override.
- **Interface changes**: two existing dispatch-rule predicates change semantics; one new `DispatchRule` row (4d) is registered; two new module-level helpers and one new constant; `guard_g5_artifact_hash_cache` and `guard_g7_plan_revising` gate 3 change their step-aside conditions. No function signature changes.
- **New persisted state**: one new stage-states key, `_concern_round_hashes` (append-only list of strings). It is not in `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`) so the existing `_save()` merge preserves it, and not in `_SNAPSHOT_PROJECTION_KEYS` (`agent/sdlc_router.py:193-201`) so G4 is unaffected. No Popoto model field changes, therefore no `scripts/update/migrations.py` entry: absence of the key is indistinguishable from an empty list and reads as count 0, which is the correct starting state for every pre-existing lane.
- **Coupling**: reduces it. The routing decision moves off the sticky `revision_applied` boolean (a `/do-plan` frontmatter side effect the router reads secondhand) and onto `_verdicts` plus a counter written by the verdict recorder — state the router already reads.
- **Data ownership**: `tools/sdlc_verdict.py::record_verdict` gains one write, inside the transaction it already owns. **No skill gains a new write.** That distinction is the point: a skill can crash mid-step, be re-run, or be invoked standalone, which is what made `plan_revising` unreliable; the verdict recorder is a single-writer tool whose write is a precondition for the routing that consumes it.
- **Reversibility**: high. The change is confined to predicate bodies and one row registration; reverting restores row 4c's current target.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the loop bound and the cap-reached behavior are the two decisions worth confirming)
- Review rounds: 2+ (this is a change to the machinery that reviews changes; it deserves its own scrutiny, and it must survive the very loop it introduces)

## Prerequisites

No prerequisites — this work has no external dependencies. All state read by the
new predicates (`_verdicts`, `_sdlc_dispatches`, `_meta`) already exists on every
live ledger.

## Solution

### Key Elements

- **An event-scoped "has this verdict's revision been judged?" predicate.** A single helper that answers, for the latest CRITIQUE verdict, whether a `/do-plan` revision has landed *since* that verdict was recorded. It replaces the sticky-boolean test in both with-concerns rows. It is **verdict-kind-agnostic**; the `WITH CONCERNS` requirement is a caller obligation.
- **A re-critique edge for the concern-closing revision.** Row 4c stops meaning "revision done, build it" and starts meaning "revision done, judge it".
- **A G5 step-aside on the same condition.** G5 runs *before* the dispatch table and its cached-READY-TO-BUILD branch matches with-concerns verdicts too. Without a step-aside, rows 4b/4c/4d are unreachable in production. This is co-load-bearing with the rows, not a nicety.
- **A build edge that requires a clean verdict or an exhausted bound.** BUILD on a with-concerns plan becomes reachable only via a subsequent `READY TO BUILD (no concerns)` verdict, or via an explicit bounded-exhaustion path that records the acceptance.
- **A monotonic, loop-scoped round counter owned by the verdict recorder.** An append-only, hash-deduplicated list of the with-concerns CRITIQUE verdicts recorded on this lane, written inside `record_verdict`'s existing single-writer transaction. It never truncates, only ever grows, and counts *only* with-concerns rounds — so a lane's NEEDS REVISION history cannot consume the bound.
- **A repaired Step 5.6 and G7 gate 3.** Both stop keying on the sticky boolean, so the `plan_revising` lock becomes armable per round instead of permanently inert.

### Flow

**CRITIQUE records `READY TO BUILD (with concerns)`** (verdict recorder appends this round's plan hash to `_concern_round_hashes`) → row 4b → **`/do-plan` revision pass** (embeds Implementation Notes, writes `revision_applied_at`) → G5 steps aside → row 4c → **`/do-plan-critique`** → records a new verdict →

- verdict is `READY TO BUILD (no concerns)` → row 4a → **`/do-build`**
- verdict is `READY TO BUILD (with concerns)` again → row 4b → **another revision** → row 4c → … (bounded)
- verdict is `NEEDS REVISION` → G1 / row 3 → **`/do-plan`** (existing path, unchanged)
- bound exhausted → row 4d → **`/do-build`**, with residual concerns recorded as explicitly accepted

### Technical Approach

**1. The predicate: `_concern_revision_is_unjudged(stage_states, meta)`**

Returns `True` when the plan has been revised since the latest CRITIQUE verdict was
recorded — i.e. the revision that closed this round's concerns exists and has not
itself been judged. Reads:

- `stage_states["_verdicts"]["CRITIQUE"]["recorded_at"]` — when the verdict was written.
- `meta["revision_applied_at"]` — when `/do-plan` last settled a revision (`agent/sdlc_router.py:1118` already consumes this field exactly this way).

`revision_applied_at > recorded_at` → unjudged. Anything else — field absent,
unparseable, equal, or earlier — → `False`.

**Fail-safe direction matters and is deliberate.** `False` means "no unjudged
revision", which routes to row 4b (`/do-plan`), not to BUILD. So a malformed or
missing `revision_applied_at` degrades to *today's pre-revision behavior for round
1* — the plan gets revised — rather than to a free pass to build. This is the
inverse of the #1760 latch's fail-safe, and correctly so: #1760 was protecting a
*clean* verdict from being re-staled, and this predicate is protecting a *dirty*
one from being waved through.

**The predicate is verdict-kind-agnostic, deliberately and permanently.** It asks
one question — "did a `/do-plan` revision land after the latest CRITIQUE verdict
was recorded?" — and nothing about what that verdict *said*. The `WITH CONCERNS`
requirement lives at the call sites, never inside the predicate body. This is not
a stylistic choice:

- Rows 4b/4c/4d and G5's ready-to-build branch each test `"WITH CONCERNS" in
  verdict` themselves, *before* calling it.
- **G7 gate 3 must call it without any verdict-kind test.** G7's self-heal has to
  release a `plan_revising` lock left behind by a `NEEDS REVISION` or `MAJOR
  REWORK` round too. Pushing the `WITH CONCERNS` test into the predicate body
  would make gate 3 return `False` on those locks, so G7 would fall through to
  gate 5 and escalate to `Blocked` after `MAX_PLAN_REVISING_DISPATCHES`
  (`agent/sdlc_router.py:92`, value 2) — a new stall, introduced by the fix.

The docstring must say this outright, because the obvious "tidying" refactor a
future reader will attempt is to move the verdict test inside. Round 1's critique
caught this plan asserting the opposite in task 7; the validator task now checks
call sites, not the predicate.

**2. Rows 4b / 4c / 4d**

| Row | Predicate (all also require: with-concerns verdict, no `pr_number`, `BUILD` not `completed`, `BUILD` in `{None, pending, ready}`) | Skill |
|---|---|---|
| `4b` | `not _concern_revision_is_unjudged(...)` | `/do-plan` — revise, embed Implementation Notes |
| `4c` | `_concern_revision_is_unjudged(...)` **and** `_concern_round_count(...) < MAX_CONCERN_RECRITIQUE_ROUNDS` | `/do-plan-critique` — judge the concern-closing revision |
| `4d` | `_concern_revision_is_unjudged(...)` **and** `_concern_round_count(...) >= MAX_CONCERN_RECRITIQUE_ROUNDS` | `/do-build` — bound exhausted, residual concerns accepted |

Row 4b's predicate change is not cosmetic: it is the fix for the sticky boolean on
its own axis. Today, at round 2, `revision_applied` is already true, so 4b misses
and 4c fires straight to build. Event-scoped, 4b correctly fires on every round's
fresh with-concerns verdict.

Rows 4b/4c/4d are mutually exclusive by construction (`not P` / `P and Q` /
`P and not Q`), which is the property the tests must pin.

**2.5. G5 must step aside on an unjudged concern revision — co-load-bearing**

Guards run to completion *before* the dispatch table is consulted:
`evaluate_guards` (`agent/sdlc_router.py:783`) walks `GUARDS`
(`agent/sdlc_router.py:771-780`) and returns the first non-`None` decision. So a
guard that dispatches `/do-build` makes rows 4b/4c/4d unreachable no matter how
they are written. `guard_g5_artifact_hash_cache` (`agent/sdlc_router.py:524`) is
exactly such a guard:

- Its cached-verdict branch tests `if CRITIQUE_READY_TO_BUILD in verdict_text`
  (`agent/sdlc_router.py:598`) with **no `WITH CONCERNS` exclusion** —
  `"READY TO BUILD (WITH CONCERNS)"` contains `"READY TO BUILD"` and matches.
  `verdict_text` is already normalized at `agent/sdlc_router.py:583`.
- Its only revision-aware step-aside is
  `if meta.get("plan_revising") and not meta.get("revision_applied")`
  (`agent/sdlc_router.py:615`) — the same sticky boolean this plan is fixing, dead
  from round 2 onward.
- It then returns `Dispatch(SKILL_DO_BUILD, ..., row_id="G5")`
  (`agent/sdlc_router.py:619-622`).

The failure mode is precise and silent: once row 4c's re-critique records a fresh
with-concerns verdict, `artifact_hash` matches the current plan hash, G5 hits, and
`/do-build` ships under `row_id="G5"`. The rows would pass every unit test and be
dead in production.

**Two edits, both inside `guard_g5_artifact_hash_cache`:**

1. Re-key `agent/sdlc_router.py:615` to
   `if meta.get("plan_revising") and _concern_revision_is_unjudged(stage_states, meta): return None`.
2. Immediately before the `Dispatch(SKILL_DO_BUILD, ...)` return at
   `agent/sdlc_router.py:619`, insert
   `if "WITH CONCERNS" in verdict_text and _concern_revision_is_unjudged(stage_states, meta): return None`.

Both return `None`, never a `Dispatch`. G5 is a cache, not a router; stepping
aside hands the state to the dispatch table, where 4b/4c/4d own it. No change to
`GUARDS` ordering, and G5's NEEDS REVISION branch (`agent/sdlc_router.py:584-597`)
is untouched — it routes to `/do-plan`, which is correct under any revision state.

**G7's ordering guarantee is preserved.** G7 precedes G5 in `GUARDS`
(`agent/sdlc_router.py:776-778`) for the #1871 reason documented in G7's
docstring. The §7 gate-3 repair makes G7 *able* to arm again; edit (1) above keeps
G5's own short-circuit consistent with it rather than relying on the guard order.

**Mandatory test (this is the one that proves the feature is alive):** construct a
ledger where the current plan hash equals `_verdicts["CRITIQUE"]["artifact_hash"]`,
the verdict is `READY TO BUILD (WITH CONCERNS)`, `revision_applied` is `True`, and
`revision_applied_at` postdates `recorded_at` — then assert the full
`evaluate(...)` result is row `4c`, **not** `row_id="G5"`. Without this assertion
the rest of the plan can pass green while shipping nothing.

**3. The bound: a monotonic, loop-scoped round counter**

**The bound is NOT derived from `_sdlc_dispatches`.** That list is FIFO-evicted:
`MAX_DISPATCH_HISTORY = 10` (`agent/sdlc_router.py:98`) and the eviction at
`agent/sdlc_router.py:1990-1991`. A count over it is non-monotonic — it *shrinks*
as entries age out — which is the exact re-arming shape that made #1925/#1968
recur. Dispatch records also carry no row id (their fields are `skill`, `at`,
`stage_snapshot`, `stage`, `confirmed` — `agent/sdlc_router.py:1972-1988`), so
critique dispatches from rows 2/2b/2c, G1 and G5 are indistinguishable from
with-concerns rounds; a lane with three NEEDS REVISION rounds would enter its
first with-concerns verdict already at the bound and row 4d would fire
immediately. Both defects are fatal to a dispatch-derived bound.

**The counter: `stage_states["_concern_round_hashes"]`.** An append-only,
deduplicated list of the `artifact_hash` of every CRITIQUE verdict recorded whose
verdict text contains `WITH CONCERNS`. `_concern_round_count(stage_states)` is
`len()` of it.

**Written by the verdict recorder, inside its existing single-writer
transaction.** `tools/sdlc_verdict.py::record_verdict` is the sole writer of
`_verdicts` (`tools/sdlc_verdict.py:359`, `_apply` at `:477-482`). The append goes
inside that same `_apply`, so the round counter and the verdict it counts land in
one `update_stage_states` write. It reuses the `artifact_hash` already computed at
`tools/sdlc_verdict.py:448` and stored on the record at `:453`.

This satisfies every property the bound needs:

| Property | Why it holds |
|---|---|
| **Durable** | `_concern_round_hashes` is an underscore-prefixed key that is *not* in `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`), so `PipelineStateMachine._save()` reloads and merges it back on every write (`agent/pipeline_state.py:497-560`). Nothing truncates it. |
| **Monotonic** | Append-only. There is no code path that removes an entry. |
| **Loop-scoped** | Only a `WITH CONCERNS` CRITIQUE verdict appends. NEEDS REVISION and MAJOR REWORK rounds never consume the bound, so blocker 3's "3 NEEDS REVISION rounds exhaust the bound before the first concern" cannot happen. No stored "first with-concerns timestamp" is needed. |
| **Unbypassable by the loop** | Rows 4b/4c/4d and G5's branch all require a with-concerns verdict in `_verdicts["CRITIQUE"]`, and only `record_verdict` writes that. A round that skips the counter skips the routing too — there is no state in which the loop advances and the counter does not. |
| **Idempotent** | Dedupe on `artifact_hash`. A replayed `sdlc-tool verdict record` for the same plan bytes produces the same hash and does not double-count. A genuine next round judges a revised plan, so its hash differs and it does count. `update_stage_states` re-invokes `_apply` against a freshly reloaded snapshot on each optimistic retry (`tools/stage_states_helpers.py:209-215`), so the read-modify-write is already retry-safe. |
| **G4-inert** | `build_stage_snapshot` uses an explicit allow-list, `_SNAPSHOT_PROJECTION_KEYS` (`agent/sdlc_router.py:193-201`). The new key is not in it, so the G4 oscillation snapshot is byte-identical to today's. |

**Fail direction, and why it is the opposite of the predicate's.** When
`artifact_hash` is `None` (plan file unreadable — `_compute_artifact_hash`,
`tools/sdlc_verdict.py:227`, can return `None`), append the verdict's
`recorded_at` as a synthetic token so the round still counts. Over-counting
exhausts the bound early and fails toward row 4d — a build with a *recorded*
acceptance. Under-counting fails toward an unbounded PLAN↔CRITIQUE loop, which is
Risk 1, this plan's most serious risk and one this repo has already shipped twice.
The predicate (§1) fails toward `/do-plan` and the counter fails toward
`/do-build`, and that is coherent rather than contradictory: they answer different
questions. The predicate asks *is this round judged?* — being wrong there costs a
wasted revision. The counter asks *how many rounds have run?* — being wrong there
costs either a stall or a recorded acceptance, and the recorded acceptance is
strictly the safer error.

**The constant.** `MAX_CONCERN_RECRITIQUE_ROUNDS` in `agent/pipeline_graph.py`
alongside `MAX_CRITIQUE_CYCLES`, default **3**, read via `os.getenv` with the
provisional/tunable comment the repo requires for magic numbers. It counts
*with-concerns rounds on this lane*, i.e. `len(_concern_round_hashes)`.

Note for the builder: `MAX_CRITIQUE_CYCLES` (`agent/pipeline_graph.py:35`) is a
**bare literal**, and neither `agent/pipeline_graph.py` nor `agent/sdlc_router.py`
currently contains any `os.getenv`/`os.environ` reference (verified: zero matches
in both). This constant introduces the first env read in that module and needs the
`os` import; do not describe it as "like `MAX_CRITIQUE_CYCLES`, read with a
default", because that is false.

Why 3 and not 4: the `verdict-finalize-cluster` round-4 datapoint counted *mixed*
rounds on a lane-total basis and does not transfer to a loop-scoped counter, so
there is no empirical basis for 4. With Step 0.5 deferred (§5) every re-critique
round is full depth, so each round is expensive; 3 bounds the cost while still
admitting two rounds of genuine concern-closing after the first verdict. Revisit
after five with-concerns lanes have run: if any hits the bound with concerns that
a fourth round would plausibly have closed, raise it.

**Explicitly not G2.** `guard_g2_critique_cycle_cap` reads `critique_cycle_count`,
which `agent/pipeline_state.py:1074` increments only inside `fail_stage("CRITIQUE")`.
A with-concerns verdict marks CRITIQUE `completed`, so that counter is `0` on this
path forever. Extending G2 would require making a *passing* verdict increment a
*failure* counter, corrupting G2's meaning for the NEEDS REVISION path it correctly
bounds. Composing with G2 here means **staying out of its way**, not reusing it.

**4. Cap-reached behavior: accept, do not escalate**

At the bound, row 4d dispatches `/do-build` rather than returning `Blocked`. A
`Blocked` strands the lane waiting on a human who is usually not watching, and the
concerns are by definition non-blocking — that is what CONCERN means. The
accountability comes from the record, not the halt.

**The delivery channel is `stage-query`, not `row_id`.** Round 1 caught that
`row_id` exists only in the router's own output (`tools/sdlc_next_skill.py:663`,
`.claude/skills/sdlc/SKILL.md:246`), is consumed by the supervisor, and is never
plumbed into the dispatched skill's invocation — `/do-build`'s SKILL.md does not
mention it. A note that `/do-build` cannot know to write is no accountability at
all. Plumbing `row_id` through the dispatch would be its own build task with its
own blast radius; a skill-written meta key is exactly what §3 argues against.
Instead, `/do-build` Step 1 re-derives row 4d's own condition from the same
authoritative source the router used:

```
sdlc-tool stage-query --issue-number N
```

and writes the note when **all three** hold: (a) the latest CRITIQUE verdict
contains `WITH CONCERNS`, (b) `revision_applied_at` postdates that verdict's
`recorded_at`, (c) `len(_concern_round_hashes) >= MAX_CONCERN_RECRITIQUE_ROUNDS`.
That is row 4d's predicate, evaluated from state rather than passed as an
argument, so the note lands whenever row 4d was the reason `/do-build` is running
— and cannot land spuriously on a clean-verdict build, because (a) fails.

- Row 4d's `reason` names the bound, the count, and that residual concerns were
  accepted unreviewed.
- `/do-build` Step 1 writes an **Accepted Residual Concerns** note into the plan's
  `## Critique Results` section naming which round's concerns were accepted and
  why (bound exhausted at N rounds).

This resolves Open Question 2 in favour of recording over halting, and the
resolution is only defensible *because* the note has a working delivery channel.
If the `stage-query` derivation proves unreliable at build time, the honest
fallback is `Blocked` at the cap, not a silent build — that decision rule is
written into the build task.

**5. Re-critique depth: full, every round. Scoping is deferred.**

Every row-4c re-critique runs the **standard FULL-depth** `/do-plan-critique`
pass. No new Step 0.5, no reduced roster, no scoped mode, no skill-side changes to
`.claude/skills-global/do-plan-critique/SKILL.md` beyond the Step 5.6 repair (§7)
and the Outcome Contract row.

Round 1's critique was right that the scoped pass was the largest new surface in
the plan, the only one with no test, and justified by cost (Risk 2) rather than
correctness — the loop is *correct* with full depth every round. Worse, its
escalation trigger ("the revision touches the plan's structure") was prose
judgement handed to an LLM, unassertable, and it would have put a repo-only
`sdlc-tool stage-query` invocation into a global skill body, against the
skill-context convention. Shipping the correctness fix without it is strictly
better than shipping both together.

**Concretely deferred:** file a follow-up issue for the concerns-scoped pass,
carrying round 1's own suggested mechanical trigger — compute the set of
`^## `/`^### ` headers whose bodies changed in the plan diff between the prior
verdict's commit and HEAD, escalate to FULL when that set intersects
`{Solution, Technical Approach, Step by Step Tasks, Verification}` — which is
assertable over two plan-file fixtures. Creating that issue is a task below, not a
"someday". Risk 2's mitigation now rests on the bound alone, which is why the
bound dropped from 4 to 3.

**6. Nits: assert, do not change**

`/do-plan-critique` already emits `READY TO BUILD (no concerns)` when there are
zero CONCERN or BLOCKER findings; NITs never produce a with-concerns verdict and
therefore cannot enter this loop. No code change. A test pins it so a future
severity-taxonomy edit cannot silently make nits loop-bearing.

**7. Repair Step 5.6 and G7 gate 3**

Both currently key on the sticky `revision_applied`, which is why the
`plan_revising` lock is permanently inert on any once-revised plan. Both become
event-scoped:

- Step 5.6: set the lock when the verdict requires a revision **and** the plan's
  `revision_applied_at` is not later than this verdict's timestamp. Delete the
  "`revision_applied: true` is already in the frontmatter" exemption clause outright.
  **The clause exists in two files** — `.claude/skills-global/do-plan-critique/SKILL.md:414-421`
  and `docs/sdlc/do-plan-critique.md:76-78` ("Do NOT set it for `READY TO BUILD
  (no concerns)` or when `revision_applied: true` already"). Fixing only the
  global body leaves the defect documented in the addendum the skill actually
  reads at runtime — the half-migration this repo forbids.
- G7 gate 3 (`agent/sdlc_router.py:684-685`, currently `if meta.get("revision_applied"): return None`):
  self-heal only when `revision_applied_at` postdates the latest CRITIQUE verdict's
  `recorded_at` — the same predicate as (1), reused **without** a verdict-kind
  test, per §1. Gate 3 must keep self-healing `NEEDS REVISION` and `MAJOR REWORK`
  locks; that is precisely why the predicate is verdict-kind-agnostic.

This is defence in depth, not the primary fix. Rows 4b/4c/4d are the load-bearing
change; a G7 that can actually arm is the backstop for a state the rows miss. It is
in scope because leaving a documented lock in the tree that provably cannot arm is
exactly the "half-migration" the repo forbids.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_critique_verdict_is_stale` (`agent/sdlc_router.py:1129`) wraps its body in a bare `except Exception: return False`. The new `_concern_revision_is_unjudged` mirrors that shape and MUST have a test asserting the observable fallback: a malformed `revision_applied_at` yields `False`, which routes to row 4b (`/do-plan`), NOT to build. Assert the resulting *dispatch*, not just the boolean.
- [ ] `tools/sdlc_next_skill.py::_resolve_enriched`'s broad `except` is the swallower named in the issue's own evidence. Add a test that a router evaluation raising inside the new predicate still produces a determinate dispatch rather than an empty ledger.

### Empty/Invalid Input Handling

- [ ] `_verdicts` absent entirely → predicate returns `False` → row 4b. Test.
- [ ] `_verdicts["CRITIQUE"]` present but `recorded_at` missing or empty string → `False` → row 4b. Test.
- [ ] `meta["revision_applied_at"]` absent, empty, whitespace-only, or non-ISO → `False` → row 4b. Test each.
- [ ] `_concern_round_hashes` absent, empty, or not a list → count is `0` → row 4c can fire (correct: no rounds consumed yet, and every pre-existing lane starts here). Test each.
- [ ] Timestamps exactly equal → `False` (strict `>`), matching the existing `_critique_verdict_is_stale` convention. Test.

### Counter Durability and Scoping (blockers 1 and 3)

- [ ] **Durability under truncation pressure.** Append 15+ dispatches to `_sdlc_dispatches` (past `MAX_DISPATCH_HISTORY = 10`) interleaved with with-concerns verdict records, then assert `_concern_round_count` still reflects every round. This is the test that would have caught the round-1 blocker.
- [ ] **Durability across `_save()`.** Record a with-concerns verdict, then drive a `PipelineStateMachine` write (a stage transition), then assert `_concern_round_hashes` survived the `_save()` merge. The key is not in `_OWNED_METADATA_KEYS`, and this pins that the unowned-key merge covers it.
- [ ] **NEEDS REVISION rounds do not consume the bound.** Record three `NEEDS REVISION` CRITIQUE verdicts, then one `READY TO BUILD (WITH CONCERNS)`; assert the count is `1` and the dispatch is row 4c, not 4d. This is blocker 3's exact scenario.
- [ ] **Idempotent re-record.** Call `record_verdict` twice with the same with-concerns verdict and the same plan bytes; assert the count is `1`.
- [ ] **`artifact_hash is None` still counts the round.** Assert the synthetic-token fallback appends, i.e. the fail direction is toward the bound.
- [ ] **G4 snapshot is unchanged.** Assert `build_stage_snapshot` output is byte-identical with and without `_concern_round_hashes` present.

### G5 Bypass (blocker 2)

- [ ] **The alive test.** `current_plan_hash == _verdicts["CRITIQUE"]["artifact_hash"]`, verdict `READY TO BUILD (WITH CONCERNS)`, `revision_applied=True`, `revision_applied_at > recorded_at` → full `evaluate(...)` returns row `4c`. Assert the `row_id` is `4c` and explicitly **not** `G5`. Capture this as demonstrated-red before the G5 edit lands.
- [ ] **G5 still fires on a no-concerns cache hit.** Same hash-match state with `READY TO BUILD` and no concerns → `row_id="G5"`, `/do-build`. The step-aside must not break G5's actual job.
- [ ] **G5's NEEDS REVISION branch is unaffected.** Hash-match with `NEEDS REVISION` → `row_id="G5"`, `/do-plan`, unchanged.
- [ ] **G5 at the bound.** Hash-match, with-concerns, unjudged revision, count `>= MAX_CONCERN_RECRITIQUE_ROUNDS` → row `4d`, `/do-build`. G5 steps aside and 4d owns the terminal decision, so the reason string still names the acceptance.

### Error State Rendering

- [ ] Row 4d's `reason` string must name the bound, the count, and that residual concerns were accepted unreviewed. Assert on the string, not just the skill. A silent build at the cap is the failure mode this whole plan exists to prevent, one level up.
- [ ] `/do-build`'s Accepted Residual Concerns note must land in the plan document and be visible in the PR. Test that the note is written when the dispatch row is `4d` and not written otherwise.

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: every existing case asserting row `4c` → `/do-build` on a with-concerns verdict with `revision_applied: true` now asserts `/do-plan-critique`. These are the tests that currently encode the bug; they must flip, and each flip is the demonstrated-red proof for this change.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: same flip at the decision-table level; add rows for 4c and 4d.
- [ ] `tests/unit/test_sdlc_router.py` (G7 cases) — UPDATE: G7 gate-3 self-heal cases keyed on the sticky `revision_applied` must be rewritten against the event-scoped predicate. A case asserting "lock self-heals when `revision_applied` is true" is asserting the defect.
- [ ] `tests/unit/test_sdlc_router.py` (G5 cases) — UPDATE: any case asserting `row_id="G5"` → `/do-build` on a cached **with-concerns** verdict now asserts row 4c. Cases on no-concerns and NEEDS REVISION verdicts must stay green unchanged.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py:143-163` — UPDATE: add `"4d"` to the hard-coded `expected` row-id set. This test fails on an *unexpected* row, so omitting it breaks the suite before anything else runs. Note there is **no** router row table in `.claude/skills/sdlc/SKILL.md` to update — `test_step4_has_no_hand_authored_dispatch_table` (`tests/unit/test_sdlc_skill_md_parity.py:81`) fails the build if one is added.
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE/ADD: `record_verdict` now appends to `_concern_round_hashes` on a with-concerns CRITIQUE verdict. Existing assertions on the returned record shape stay green (the counter lives in `stage_states`, not in the returned record); add the counter cases from Failure Path Test Strategy.
- [ ] `tests/unit/test_sdlc_takeover_regression.py` — UPDATE if it asserts a with-concerns → build transition; verify at build time and update or leave.
- [ ] `tests/unit/test_architectural_constraints.py` — no change expected, but the new predicate must not import from `tools/`; this test is the guard and must stay green.

Existing #1760 / #2049 latch tests in `_critique_verdict_is_stale` must stay green
**unchanged**. If any of them go red, the change has leaked into the no-concerns
path and the disjointness argument is wrong — treat a red there as a blocker, not
as a test to update.

## Rabbit Holes

- **Rewriting the severity taxonomy.** The issue's own Out of Scope. BLOCKER/CONCERN/NIT is not the problem; what happens after a CONCERN is. Do not touch the classifier.
- **Making the whole thing a state machine.** There will be a pull to unify rows 2b/3/4a/4b/4c/4d into a single verdict-kind × event-ordering matrix. It is probably right eventually and it is not this plan. Six predicates that each fail safe beat one matrix nobody can review.
- **Deleting the `plan_revising` lock.** Tempting — the rows do the real work and the lock is currently inert. But G7 is referenced in `docs/sdlc/plan-revising-lock.md`, `.claude/skills/sdlc/SKILL.md`, `/do-plan` Phase 4 step 2b, and `/do-plan-critique` Step 5.6. Repair it here; propose removal separately with the full blast radius in hand.
- **Building a general "was artifact X reviewed after change Y?" abstraction.** G5's artifact-hash cache, G8's artifact verification, row 2b's staleness, and this predicate are four instances of one idea. Generalizing them is a real refactor with a real risk of re-introducing #1925. Not now.
- **Making the scoped re-critique cheap by cutting critics.** Moot in this plan — the scoped pass is deferred to a follow-up issue (§5) and every round runs at full depth. Carried forward as guidance for that issue: the saving must come from narrowing *what is judged* (prior concerns + revision diff), not from thinning the roster. A two-critic war room that misses the round-2 defect costs more than it saves.
- **Exempting `/do-plan-critique` entries from `_sdlc_dispatches` eviction.** Round 1 raised this as one way to make a dispatch-derived bound durable. Rejected: `MAX_DISPATCH_HISTORY` exists to bound memory, and selective retention breaks `compute_same_stage_count`'s contiguous-recency assumption (`agent/sdlc_router.py:1997+`), which would need its own G4 regression suite. The `_concern_round_hashes` counter gets the same durability without touching G4's substrate at all.
- **Plumbing `row_id` into dispatched skill invocations.** A real and probably-good idea — `/do-build` and others could act on *why* they were dispatched. It is a separate blast radius (`tools/sdlc_next_skill.py`, the `/sdlc` router contract, every skill's Step 1) and this plan gets what it needs by re-deriving row 4d's predicate from `stage-query`. Do not let it in through the side door.

## Risks

### Risk 1: Reopening the #1760 / #1925 / #1968 non-convergence loop

**Impact:** The exact failure this repo has already shipped twice — PLAN↔CRITIQUE ping-pong that never reaches BUILD, burning a lane and a human's attention. This is the single most important risk in the plan and the reason the bound is derived rather than stored.

**Mitigation, in three independent layers:**

1. **Disjointness, stated at call-site level.** #1760 is about a **no-concerns** verdict being re-staled by a notes-only revision. `_critique_verdict_is_stale` is not modified at all. The predicate added here is verdict-kind-agnostic (§1), so the disjointness claim is about *where it is called*, not about the predicate body — round 1 caught the earlier phrasing asserting the latter, which was false. Every new call site is one of: rows 4b/4c/4d and G5's ready-to-build branch, each of which tests `"WITH CONCERNS" in verdict` before calling; or G7 gate 3, which sits on the `plan_revising` lock path that #1760 never governed. The existing #1760/#2049 tests staying green *unchanged* is the mechanical proof, and is listed above as a blocker-if-red.
2. **No re-arming.** #1925/#1968 recurred because `/do-plan` rewrites `revision_applied_at` every pass, re-arming the latch each round. Here the counter is not a latch and cannot be re-armed: `_concern_round_hashes` is append-only and nothing in the loop's control removes an entry, so every round strictly tightens the bound. This is the property the original dispatch-count bound did *not* have — `_sdlc_dispatches` FIFO-evicts at `MAX_DISPATCH_HISTORY = 10` (`agent/sdlc_router.py:98,1990-1991`), so its count shrinks over a long lane, which is the #1925 re-arming shape wearing a different hat. State this asymmetry in the code comment on the constant *and* on the counter.
3. **Terminal by construction.** Row 4d dispatches `/do-build`, not `Blocked`. Even a pathological oscillation terminates in a build with a recorded acceptance rather than a stall — and, unlike the dispatch-derived bound, the counter actually reaches the threshold, so row 4d is genuinely reachable.
4. **G5 cannot short-circuit the loop.** §2.5's step-aside is what keeps rows 4b/4c/4d on the live path at all; without it the loop is not "risky", it is absent.

### Risk 2: The re-critique is expensive enough that lanes get slower than the defect is costly

**Impact:** Every with-concerns plan pays extra war-room rounds at full depth, since the scoped pass is deferred (§5). A lane that hits the bound pays 3 war rooms.

**Mitigation:** the bound is the only mitigation, and it is now the *whole* mitigation — that is why the default dropped from 4 to 3. `MAX_CONCERN_RECRITIQUE_ROUNDS` is genuinely env-overridable (`os.getenv` in `agent/pipeline_graph.py`), so a lane-level or machine-level override needs no code change. If cost proves to be the binding constraint in practice, the follow-up issue from §5 ships the concerns-scoped pass with a mechanically-assertable escalation trigger; the honest sequencing is correctness first, then cost.

### Risk 3: The `revision_applied_at` field is unreliable on live ledgers

**Impact:** If `/do-plan` skipped writing it (crash between the commit and the meta write, or a pre-#1760 lane), the predicate returns `False` and the plan routes to `/do-plan` for a revision it already did — a wasted round, possibly a repeated one.

**Mitigation:** the fail-safe direction is a wasted `/do-plan`, never an unreviewed build, which is the correct way to be wrong for this feature. G4's `same_stage_dispatch_count` oscillation cap already bounds repeated same-stage dispatch and escalates. `/do-plan` Phase 4 step 2a already mandates writing `revision_applied_at` in the *same step* as `revision_applied: true`, never as a follow-up edit — that ordering requirement exists precisely to make this field trustworthy, and a build task re-verifies it.

### Risk 4: This change ships through the very pipeline it modifies

**Impact:** The plan will itself receive a with-concerns verdict, and the fix will be routing its own critique rounds once merged. A defect in row 4c can strand this lane.

**Mitigation:** the change is confined to predicate bodies and one row registration, all pure functions over dicts, fully unit-testable with no live ledger. The demonstrated-red requirement (existing 4c tests flipping) proves the new routing binds before merge. Post-merge, a stuck lane is recoverable by `sdlc-tool meta-set` on `revision_applied_at`, which is documented in the feature doc.

## Race Conditions

### Race 1: `/do-plan` writes `revision_applied_at` and the router reads it mid-write

**Location:** `agent/sdlc_router.py` (new predicate) vs. `/do-plan` Phase 4 step 2a → `sdlc-tool meta-set`
**Trigger:** the router evaluates rows between the plan-doc commit and the `meta-set` landing.
**Data prerequisite:** `meta["revision_applied_at"]` must be present before the router can see the revision as judged-pending.
**State prerequisite:** the ledger's `_meta` and the plan file agree.
**Mitigation:** the window resolves to `False` → row 4b → `/do-plan`, which is idempotent (it re-writes the same frontmatter and re-runs `meta-set`), and the next evaluation sees the field. Costs at most one wasted dispatch, bounded by G4. Additionally, the SDLC issue lock (`ISSUE_LOCKED`, keyed by `run_id`) serializes all state-mutating calls for an issue to one live run, so there is no concurrent-writer variant of this race.

### Race 2: Two concurrent lanes on the same issue

**Location:** `_concern_round_hashes` (the loop bound's source)
**Trigger:** two runs record a with-concerns CRITIQUE verdict for one issue, inflating the count and prematurely exhausting the bound.
**Data prerequisite:** a single owner of the verdict record.
**State prerequisite:** the issue lock is held.
**Mitigation:** two independent layers. (a) The issue lock serializes state-mutating `sdlc-tool` calls to one live run, so there is no concurrent-writer variant. (b) Even if two writes did land, the `artifact_hash` dedupe (§3) collapses them: two runs critiquing the same plan bytes produce the same hash and append one entry. Genuine premature exhaustion would fail toward a build with a recorded acceptance, not toward a loop.

### Race 3: `record_verdict` appends the round before `/do-plan` writes `revision_applied_at`

**Location:** `tools/sdlc_verdict.py::record_verdict` `_apply` vs. `/do-plan` Phase 4 step 2a
**Trigger:** the counter is incremented at verdict time, but the predicate that gates row 4c depends on a *later* `/do-plan` write. Between them the ledger holds `count = N` with no unjudged revision.
**Data prerequisite:** none — the two are read by different predicates.
**State prerequisite:** none.
**Mitigation:** benign by construction. In that window `_concern_revision_is_unjudged` is `False`, so rows 4c and 4d both miss and row 4b fires (`/do-plan`) — exactly the intended next step. The count being already incremented does not shortcut anything, because the count is only consulted when the predicate is `True`.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. Specifically: the rows, the bound, the scoped re-critique, the Step 5.6 repair, the G7 gate-3 repair, the tests, and the docs all ship together. The `plan_revising` lock *removal* is named in Rabbit Holes as a deliberate non-goal rather than a deferral: this plan repairs the lock so it works, and takes no position on whether it should later be deleted.

## Update System

No update system changes required — this feature is purely internal. It touches
`agent/sdlc_router.py`, `agent/pipeline_graph.py`, `tools/sdlc_verdict.py`, and
skill/doc markdown. The `do-plan-critique` skill lives in `.claude/skills-global/`,
which `/update` already hardlinks to `~/.claude/skills/` via
`scripts/update/hardlinks.py`; no new directory is added, so no `RENAMED_REMOVALS`
entry and no registration step.

`MAX_CONCERN_RECRITIQUE_ROUNDS` is read via `os.getenv` with a literal default and
is not a secret, so it needs no `.env` or `.env.example` entry. It is **not**
following an existing precedent in that file: `MAX_CRITIQUE_CYCLES`
(`agent/pipeline_graph.py:35`) is a bare literal, and neither `agent/pipeline_graph.py`
nor `agent/sdlc_router.py` contains any `os.getenv`/`os.environ` reference today.
This constant introduces the first one and the `os` import that goes with it.

`_concern_round_hashes` needs **no migration**. It is a stage-states key, not a
Popoto model field, so `scripts/update/migrations.py` is not involved; and an
absent key reads as count `0`, which is the correct starting state for every lane
that predates the change. There is no backfill to do and nothing to clean up.

## Agent Integration

No agent integration required — this is internal to the SDLC router and the
critique skill. Both are reached through paths that already exist: the router via
`sdlc-tool next-skill` (already in `pyproject.toml [project.scripts]`), and the
skill via the `Skill` tool. No new CLI entry point, no new MCP surface, no bridge
import. The one integration assertion worth testing is that
`sdlc-tool next-skill` on a with-concerns-plus-unjudged-revision ledger returns
`/do-plan-critique` end-to-end through the real CLI, not only through the router
unit — that is listed as a build task.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/with-concerns-recritique-gate.md` describing the rows 4b/4c/4d split, the event-scoped predicate and why it is verdict-kind-agnostic, the G5 step-aside, the `_concern_round_hashes` counter and its ownership, and the recovery procedure for a stuck lane (`sdlc-tool meta-set` on `revision_applied_at`).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-pipeline.md` — the "Convergence latch" section must state that the latch governs the *no-concerns* path and point at the new doc for the with-concerns path, so the two are not confused by a future reader.
- [ ] Update `docs/sdlc/plan-revising-lock.md` — the Set and Clear Contract table and the G7 pseudocode both encode the sticky-`revision_applied` behaviour and are now wrong. Rewrite both against the event-scoped predicate. Describe only the new status quo.
- [ ] Update `.claude/skills/sdlc/SKILL.md` at **three specific locations**. There is **no router row table in this file** and one must not be added (`tests/unit/test_sdlc_skill_md_parity.py:81` fails the build if one is). The real edits are: (a) the G7 guards-table row at line 180, which encodes `revision_applied != True`; (b) the G5 guards-table row at line 182, whose "Never re-dispatch `/do-plan-critique` on an unchanged plan" and "`/do-build` (READY TO BUILD)" text is now wrong for with-concerns verdicts; (c) the hard-coded "dispatch rules (18 rows)" literal at line 223, which becomes 19.
- [ ] Update `.claude/skills-global/do-plan-critique/SKILL.md` — repaired Step 5.6 (lines 414-421: delete the `revision_applied: true` exemption clause, key on the event-scoped comparison) and an Outcome Contract table row making explicit that `READY TO BUILD (with concerns)` no longer reaches BUILD directly. No Step 0.5; the scoped pass is deferred (§5).
- [ ] Update `docs/sdlc/do-plan-critique.md` lines 76-78 — this addendum carries the *same* sticky Step 5.6 rule ("Do NOT set it for `READY TO BUILD (no concerns)` or when `revision_applied: true` already"). It was missing from the round-1 doc target list, and the Verification grep was scoped to the global SKILL.md only, so the plan could have passed its own gate with the defect still documented in the file the skill reads at runtime.
- [ ] Update `docs/features/sdlc-pipeline.md` (or the doc that indexes stage-states keys, whichever the documentarian finds) to register `_concern_round_hashes` as a persisted stage-states key alongside `_verdicts` / `_sdlc_dispatches`, naming `record_verdict` as its sole writer.

### Inline Documentation

- [ ] Docstring on `_concern_revision_is_unjudged` stating the fail-safe direction, *why* it is the inverse of the #1760 latch's, and — prominently — that it is **verdict-kind-agnostic by design**, that the `WITH CONCERNS` test is a caller obligation, and that moving that test into the body breaks G7 gate 3's self-heal on NEEDS REVISION locks. This is the comment that stops the obvious future "tidying" refactor.
- [ ] Comment on `MAX_CONCERN_RECRITIQUE_ROUNDS` marking it provisional and tunable, stating that it counts *with-concerns rounds on this lane* rather than lane-total critique dispatches, naming the revisit criterion (five with-concerns lanes), and stating the non-re-arming asymmetry from Risk 1 layer 2.
- [ ] Comment on the `_concern_round_hashes` append in `record_verdict` explaining the three properties it exists to provide (durable / monotonic / loop-scoped), why `_sdlc_dispatches` cannot provide them (`MAX_DISPATCH_HISTORY` eviction), and why the `artifact_hash is None` fallback counts the round rather than skipping it.
- [ ] Comment on each G5 step-aside naming what it protects and why it returns `None` rather than a `Dispatch`.
- [ ] Docstrings on rows 4b/4c/4d naming their mutual exclusivity and their D3 step-asides.

## Success Criteria

- [ ] A `READY TO BUILD (with concerns)` verdict cannot reach `/do-build` on the round that produced it, at any round number — verified specifically for rounds 2 and 3, where the sticky boolean currently exempts the plan.
- [ ] **The G5 alive test passes**: on a plan-hash cache hit with a with-concerns verdict and an unjudged revision, `evaluate(...)` returns row `4c`, not `row_id="G5"`. Without this, every other criterion can pass while the feature is dead in production.
- [ ] G5 still returns `row_id="G5"` → `/do-build` on a no-concerns cache hit, and `row_id="G5"` → `/do-plan` on a NEEDS REVISION cache hit.
- [ ] `sdlc-tool next-skill` returns `/do-plan-critique` on a ledger reconstructed from the #2757 state described in the issue's Recon Summary (with-concerns verdict, `revision_applied_at` postdating it) — end-to-end through the real CLI, not only the router unit.
- [ ] Rows 4b, 4c, and 4d are pairwise mutually exclusive over the full state space, asserted by an exhaustive test rather than by inspection.
- [ ] **The bound survives dispatch-history truncation**: 15+ recorded dispatches (past `MAX_DISPATCH_HISTORY = 10`) do not reduce `_concern_round_count`.
- [ ] **The bound is loop-scoped**: three `NEEDS REVISION` rounds followed by one with-concerns verdict yields count `1` and routes to row 4c, not 4d.
- [ ] The loop terminates in at most `MAX_CONCERN_RECRITIQUE_ROUNDS` with-concerns rounds, asserted by simulating a plan that returns with-concerns forever.
- [ ] At the bound, the dispatch is `/do-build` with a `reason` naming the bound and the accepted residual concerns; an Accepted Residual Concerns note lands in the plan's `## Critique Results`, written by `/do-build` from `stage-query`-derived state (not from `row_id`).
- [ ] A `READY TO BUILD (no concerns)` verdict with 3 NITs routes to `/do-build` with zero extra critique rounds.
- [ ] All existing `_critique_verdict_is_stale` tests (#1760, #2049) pass **unchanged**.
- [ ] `sdlc-tool meta-set --key plan_revising --value true` on a once-revised plan actually binds G7 (it currently cannot), **and** G7 gate 3 still self-heals a `plan_revising` lock left by a `NEEDS REVISION` round — the verdict-kind-agnostic predicate is what makes both true at once.
- [ ] A follow-up issue exists for the concerns-scoped re-critique pass, carrying the mechanical header-diff escalation trigger.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (router)**
  - Name: `router-builder`
  - Role: The predicate, the constant, rows 4b/4c/4d, and the G7 gate-3 repair in `agent/sdlc_router.py` / `agent/pipeline_graph.py`
  - Agent Type: builder
  - Resume: true

- **Builder (skills)**
  - Name: `skill-builder`
  - Role: `/do-plan-critique` Step 0.5 and Step 5.6, `/do-build` Step 1's residual-concerns note
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `router-tester`
  - Role: The demonstrated-red flips, the exhaustive mutual-exclusivity test, the forever-with-concerns termination simulation, the CLI end-to-end
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `loop-validator`
  - Role: Verify the #1760/#2049 disjointness argument holds mechanically, and that no existing latch test was modified
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `sdlc-documentarian`
  - Role: The six documentation targets above
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Re-verify the `revision_applied_at` write ordering

- **Task ID**: verify-revision-timestamp
- **Depends On**: none
- **Validates**: read-only
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Confirm `/do-plan` Phase 4 step 2a writes `revision_applied` and `revision_applied_at` in the same step, and that `meta-set` propagates `revision_applied_at` into router `meta`.
- Confirm `_concern_round_hashes` is preserved by `PipelineStateMachine._save()`'s unowned-`_*`-key merge (`agent/pipeline_state.py:497-560`) given it is absent from `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`).
- Confirm `build_stage_snapshot`'s allow-list (`_SNAPSHOT_PROJECTION_KEYS`, `agent/sdlc_router.py:193-201`) excludes it, so G4 is unaffected.
- If any is false, STOP and report — the bound's durability depends on all three.

### 1.5. Add the monotonic round counter to the verdict recorder

- **Task ID**: build-counter
- **Depends On**: verify-revision-timestamp
- **Validates**: tests/unit/test_sdlc_verdict.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_verdict.py::record_verdict`'s `_apply` (`:477-482`), when `stage == "CRITIQUE"` and the normalized verdict contains `WITH CONCERNS`, append `artifact_hash` to `states["_concern_round_hashes"]` if not already present. Fall back to the record's `recorded_at` as the token when `artifact_hash` is `None`.
- Defensive read: treat a non-list value as an empty list rather than raising — this function must never break verdict recording.
- Do NOT add a new `update_stage_states` call; the append rides the existing transaction.

### 2. Add the predicate, the count helper, and the constant

- **Task ID**: build-predicate
- **Depends On**: build-counter
- **Validates**: tests/unit/test_sdlc_router.py
- **Informed By**: the #1760 latch at `agent/sdlc_router.py:1118` (same field, opposite fail-safe)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_concern_revision_is_unjudged(stage_states, meta)` next to `_critique_verdict_is_stale`, **verdict-kind-agnostic**, with the §1 docstring.
- Add `MAX_CONCERN_RECRITIQUE_ROUNDS` (default 3, `os.getenv`-overridable) to `agent/pipeline_graph.py` with the provisional/tunable comment. This adds the module's first `os` import.
- Add `_concern_round_count(stage_states)` returning `len(stage_states.get("_concern_round_hashes") or [])`, tolerating a non-list value as `0`.
- Do NOT modify `_critique_verdict_is_stale`.
- Do NOT read `_sdlc_dispatches` anywhere in this feature.

### 3. Rewire rows 4b / 4c / 4d

- **Task ID**: build-rows
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Re-key 4b off the predicate instead of `not revision_applied`.
- Re-target 4c to `/do-plan-critique`, gated on the predicate and the bound.
- Add row 4d (`/do-build`, bound exhausted) with the reason string naming the bound, the count, and the acceptance.
- Preserve the D3 `pr_number` / `BUILD == completed` step-asides on all three.
- Add `"4d"` to the `expected` row-id set at `tests/unit/test_sdlc_skill_md_parity.py:143-163` in the same commit — that test fails on an unexpected row and will red the whole file otherwise.

### 3.5. Make G5 step aside on an unjudged concern revision

- **Task ID**: build-g5
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Re-key `agent/sdlc_router.py:615` from `not meta.get("revision_applied")` to `_concern_revision_is_unjudged(stage_states, meta)`.
- Insert the with-concerns step-aside immediately before the `Dispatch(SKILL_DO_BUILD, ...)` return at `agent/sdlc_router.py:619`, returning `None`.
- Do NOT touch G5's NEEDS REVISION branch (`:584-597`) or the `GUARDS` ordering.
- **Land the alive test first, red**: hash-match + with-concerns + unjudged revision must currently return `row_id="G5"`; capture that output before the edit. This task is not done until it returns `4c`.

### 4. Repair G7 gate 3

- **Task ID**: build-g7
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the sticky-`revision_applied` self-heal at `agent/sdlc_router.py:684-685` with the event-scoped predicate so the lock is armable per round.
- Call the predicate **without** a verdict-kind test. Add a test that a `plan_revising` lock left by a `NEEDS REVISION` round still self-heals — otherwise G7 falls through to gate 5 and escalates to `Blocked`.

### 5. Skill-side changes

- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- `/do-plan-critique` Step 5.6 (`.claude/skills-global/do-plan-critique/SKILL.md:414-421`): delete the `revision_applied: true` exemption; key on the event-scoped comparison.
- `docs/sdlc/do-plan-critique.md:76-78`: the same exemption clause in the repo addendum. Both files or neither.
- `/do-plan-critique` Outcome Contract: state that with-concerns no longer reaches BUILD directly.
- `/do-build` Step 1: derive row 4d's condition from `sdlc-tool stage-query` (with-concerns verdict AND unjudged revision AND count at the bound) and write the Accepted Residual Concerns note. Do **not** rely on `row_id` — it is not plumbed into skill invocations.
- If the `stage-query` derivation cannot be made to work reliably, STOP and report: the cap-reached decision flips to `Blocked` (see §4).
- No Step 0.5. The concerns-scoped pass is deferred.

### 5.5. File the follow-up issue for the concerns-scoped pass

- **Task ID**: file-followup
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Use `/do-issue`. Carry §5's mechanical escalation trigger (changed `^## `/`^### ` header set intersected with `{Solution, Technical Approach, Step by Step Tasks, Verification}`), the two-plan-fixture assertability requirement, and the skill-context split (generic probe in the global body, `stage-query` invocation in `docs/sdlc/do-plan-critique.md`).
- Label `skills`. Reference #2787.

### 6. Tests

- **Task ID**: build-tests
- **Depends On**: build-rows, build-g5, build-g7, build-skills
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_skill_md_parity.py, tests/unit/test_sdlc_verdict.py
- **Assigned To**: router-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Flip the existing 4c → `/do-build` cases; capture the red output before the fix as the demonstrated-red paper trail.
- The G5 alive test (row `4c`, not `row_id="G5"`), plus the three G5-still-works cases.
- Round-2 and round-3 regression: with-concerns + sticky `revision_applied` must not reach build.
- Exhaustive mutual-exclusivity over 4b/4c/4d.
- Counter durability: 15+ dispatches past `MAX_DISPATCH_HISTORY`; survival across a `PipelineStateMachine._save()`; three NEEDS REVISION rounds not consuming the bound; idempotent re-record; `artifact_hash is None` still counting.
- `build_stage_snapshot` output byte-identical with and without `_concern_round_hashes`.
- G7 gate 3 still self-heals a `NEEDS REVISION`-set lock.
- Forever-with-concerns termination at the bound, with the row-4d reason asserted.
- Nits-only verdict routes to build with zero extra rounds.
- All Failure Path Test Strategy cases above.
- End-to-end `sdlc-tool next-skill` on a reconstructed #2757-shaped ledger.
- Run with `scripts/pytest-clean.sh`, targeted files only. Redis test DB 11 for any live-state verification.

### 7. Disjointness validation

- **Task ID**: validate-disjointness
- **Depends On**: build-tests
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_critique_verdict_is_stale` is byte-for-byte unmodified.
- Verify no #1760/#2049 latch test was edited.
- Verify the **call sites**, not the predicate: rows 4b/4c/4d and G5's ready-to-build branch each test `"WITH CONCERNS" in verdict` before calling `_concern_revision_is_unjudged`; G7 gate 3 calls it without a verdict-kind test (that is required, not a defect); and the predicate body itself contains **no** verdict-kind test.
- Verify no code path in this feature reads `_sdlc_dispatches`.
- Report pass/fail; a failure here is a blocker, not a test to update.

### 8. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-disjointness
- **Assigned To**: sdlc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- All six documentation targets above. Describe only the new status quo — no "previously the lock behaved as…" residue.

### 9. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row below.
- Confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_decision.py -q` | exit code 0 |
| Verdict recorder tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_verdict.py -q` | exit code 0 |
| Skill parity tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Architectural constraints hold | `scripts/pytest-clean.sh tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Row 4d is registered | `grep -c 'row_id="4d"' agent/sdlc_router.py` | output contains 1 |
| The bound constant exists and is env-overridable | `grep -A3 'MAX_CONCERN_RECRITIQUE_ROUNDS' agent/pipeline_graph.py \| grep -c 'getenv'` | output > 0 |
| Row 4c no longer targets build | `grep -A6 'row_id="4c"' agent/sdlc_router.py \| grep -c 'SKILL_DO_BUILD'` | match count == 0 |
| G5 steps aside on with-concerns | `grep -c '_concern_revision_is_unjudged' agent/sdlc_router.py` | output >= 4 (predicate def + rows 4b/4c/4d + G5 x2 + G7) |
| The feature never reads the truncated history | `grep -n '_sdlc_dispatches' agent/sdlc_router.py \| grep -c '_concern'` | match count == 0 |
| The counter is written by the verdict recorder | `grep -c '_concern_round_hashes' tools/sdlc_verdict.py` | output > 0 |
| Step 5.6 sticky exemption is gone from the global skill | `sed -n '/^### Step 5.6/,/^### /p' .claude/skills-global/do-plan-critique/SKILL.md \| grep -c 'revision_applied: true'` | match count == 0 |
| Step 5.6 sticky exemption is gone from the addendum | `sed -n '/plan_revising --value true/,/^## /p' docs/sdlc/do-plan-critique.md \| grep -c 'revision_applied: true'` | match count == 0 |
| SKILL.md row count updated | `grep -c 'dispatch rules (19 rows)' .claude/skills/sdlc/SKILL.md` | output == 1 |
| No hand-authored row table was added | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py::test_step4_has_no_hand_authored_dispatch_table -q` | exit code 0 |
| The #1760 latch is untouched | `git diff origin/main -- agent/sdlc_router.py \| grep -c '^-.*revision_dt is not None and not (plan_dt > revision_dt)'` | match count == 0 |
| Feature doc exists | `test -f docs/features/with-concerns-recritique-gate.md` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_sdlc_router.py` | exit code 1 |

**Note on the two Step 5.6 greps.** They are scoped to the *section* in each file,
not to the whole file, deliberately: both files legitimately continue to mention
`revision_applied_at` (the event-scoped field is the replacement), and a whole-file
"string absent" anti-criterion would also trip on any comment or changelog line
that quotes the deleted rule. Section-scoped greps assert the rule is gone from
where it was operative.

## Critique Results

**Round 1** — FULL depth (force-FULL: touches `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/skills-global/`). Verdict: **NEEDS REVISION** (3 blockers, 5 concerns, 1 nit).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | The derived bound is not durable. `_sdlc_dispatches` IS truncated: `MAX_DISPATCH_HISTORY = 10` at `agent/sdlc_router.py:98`, FIFO-evicted in `record_dispatch` at `agent/sdlc_router.py:1990-1991`. `tools/sdlc_dispatch.py` does not slice, but it calls `agent.sdlc_router.record_dispatch` and reports `len(history)` after truncation — so the Freshness Check verified the wrong module. A count of `/do-plan-critique` entries decreases as entries age out, so it is not monotonic and Risk 1 layer 2's "tightens on every rewrite" asymmetry does not hold; layer 3 also fails because row 4d only fires at the bound. | **RESOLVED** — §3 + task 1.5 + task 2 | Do not derive the bound from `_sdlc_dispatches`. Either persist a monotonic counter in the ledger's owned metadata (the `_critique_cycle_count` pattern at `agent/pipeline_state.py:1074`, but incremented on the row-4c dispatch rather than inside `fail_stage`), or exempt `/do-plan-critique` entries from eviction at `agent/sdlc_router.py:1990` — the latter changes `compute_same_stage_count`'s contiguous-recency assumption and needs its own G4 regression. Pin it with a test that appends 15+ dispatches and asserts the count survives. |
| BLOCKER | Risk & Robustness | G5 bypasses the new rows entirely. `evaluate_guards` (`agent/sdlc_router.py:771-789`) runs before the dispatch table, and G5's ready-to-build branch matches with-concerns too (`if CRITIQUE_READY_TO_BUILD in verdict_text`, `agent/sdlc_router.py:589`, no `WITH CONCERNS` exclusion). Its only revision-aware step-aside, `plan_revising and not revision_applied` at `agent/sdlc_router.py:606`, reads the same sticky boolean the plan is fixing and is dead from round 2 on. After a row-4c re-critique records a fresh with-concerns verdict the plan hash matches `artifact_hash`, so G5 dispatches `/do-build` with `row_id="G5"` and rows 4b/4c/4d never evaluate. | **RESOLVED** — §2.5 + task 3.5 | In `guard_g5_artifact_hash_cache`, before the `Dispatch(SKILL_DO_BUILD, ...)` return at `agent/sdlc_router.py:610`, add `if "WITH CONCERNS" in verdict_text and _concern_revision_is_unjudged(stage_states, meta): return None`, and re-key line 606 to `meta.get("plan_revising") and _concern_revision_is_unjudged(stage_states, meta)`. `verdict_text` is already normalized at line 583. Add a test pinning `current_plan_hash == artifact_hash` on a with-concerns verdict with `revision_applied=True` and asserting row 4c, not `row_id="G5"` — without it the feature passes its own unit tests while being dead in production. |
| BLOCKER | History & Consistency | The bound counts *total* critique dispatches, but `_sdlc_dispatches` records carry no row id, so rows 2/2b/2c, G1 and G5 NEEDS-REVISION re-critiques are indistinguishable from with-concerns rounds. A plan that took 3 NEEDS REVISION rounds enters its first with-concerns verdict already at the bound, so row 4d fires immediately: straight to `/do-build` with concerns "accepted unreviewed" and zero re-critique. That reproduces the plan's own stated pathology for exactly the plans needing the most scrutiny, and silently, because the outcome is a recorded acceptance rather than a failure. Independent of the truncation defect. | **RESOLVED** — §3 (loop-scoped counter) | Scope the count to the with-concerns loop, not the lane. Dispatch records carry only `skill`/`at`/`stage`/`confirmed`/`stage_snapshot` (`agent/sdlc_router.py:1972-1996`), so row-id filtering is impossible at read time; the available key is `at >= first_with_concerns_at`. Note `_verdicts["CRITIQUE"]` is a single record (`agent/sdlc_router.py:1090-1092`), so the first with-concerns timestamp is NOT recoverable from `_verdicts` and must be stored when the loop starts. This changes the shape of Technical Approach section 3. |
| CONCERN | Risk & Robustness | The cap-reached accountability mechanism has no delivery channel. `row_id` exists only in the router's own output (`tools/sdlc_next_skill.py:663`, `.claude/skills/sdlc/SKILL.md:246`) and is consumed by the supervisor; it is not plumbed into the dispatched skill's invocation, and `/do-build`'s SKILL.md never mentions `row_id`. As written, the only thing between a bound-exhausted lane and a silently unreviewed build is a note the build skill cannot know to write. | **RESOLVED** — §4 + D2 + task 5 | Do not add a skill-written meta key (Technical Approach section 3 argues against exactly that). Have `/do-build` Step 1 derive the condition from `sdlc-tool stage-query` — the same source Step 0.5 uses — testing `WITH CONCERNS` in the latest CRITIQUE verdict AND the event-scoped unjudged condition AND the bound being reached, i.e. row 4d's own predicate. If you instead plumb `row_id`, that plumbing is its own build task and must appear in Step by Step Tasks. |
| CONCERN | Scope & Value | Step 0.5 is the largest new surface and the only one with no test. It adds a state read, a git-diff computation, a reduced-roster mode and a heuristic escalation trigger, all as prose to an LLM; Test Impact covers only `test_sdlc_skill_md_parity.py`, which checks row-id coverage and docstrings, not Step 0.5 behavior. Its justification is Risk 2 (cost), not correctness — the loop is correct with full depth every round — which inverts the plan's own Rabbit Hole reasoning about cheap war rooms. | **RESOLVED** — §5 + D3 + task 5.5 (deferred) | If Step 0.5 stays in scope, make the escalation trigger mechanical: compute the set of `^## `/`^### ` headers whose bodies changed in the plan diff between the prior verdict's commit and HEAD, and escalate to FULL when that set intersects `{Solution, Technical Approach, Step by Step Tasks, Verification}`. That is assertable over two plan-file fixtures; "the revision touches the plan's structure" as a prose judgement is not, and without it Risk 2's mitigation cannot be verified. Preferred alternative: split Step 0.5 to a follow-up issue and ship full-depth re-critique first. |
| CONCERN | History & Consistency | The plan contradicts itself on whether the new predicate tests verdict kind. Technical Approach section 1 defines it as a pure timestamp comparison with `WITH CONCERNS` enforced by the rows; section 7 reuses "the same predicate" for G7 gate 3, which must also self-heal on NEEDS REVISION and MAJOR REWORK locks; task 7 then orders the validator to verify every new predicate requires `WITH CONCERNS`, treating failure as a blocker. Applied literally that forces the verdict test into the predicate body and stops G7 gate 3 self-healing on a NEEDS REVISION lock, escalating to `Blocked` after `MAX_PLAN_REVISING_DISPATCHES` (`agent/sdlc_router.py:92`). Risk 1's disjointness argument rests on the same false claim. | **RESOLVED** — §1 + Risk 1 layer 1 + task 7 | State explicitly that `_concern_revision_is_unjudged` is verdict-kind-agnostic and the `WITH CONCERNS` requirement is a caller obligation, then rewrite task 7's third bullet to check the rows and G5 rather than the predicate. Restate the number-1760 disjointness proof at call-site level: `_critique_verdict_is_stale` unmodified, and every new call site (rows 4b/4c/4d, G5's ready-to-build branch, G7 gate 3) either tests `WITH CONCERNS` first or sits on a path number 1760 never governed. Put that in the predicate docstring — the current phrasing invites a future reader to add the verdict test inside it and silently break G7. |
| CONCERN | History & Consistency | Three documentation targets are wrong or missing. (a) There is no router row table in `.claude/skills/sdlc/SKILL.md`, and `tests/unit/test_sdlc_skill_md_parity.py:81` fails the build if one is added; the real edits there are the G7 guards-table row (line 180, encoding `revision_applied != True`), the G5 row (line 182, "Never re-dispatch `/do-plan-critique` on an unchanged plan"), and the hard-coded "18 rows" count at line 223. (b) `docs/sdlc/do-plan-critique.md:77-78` carries the same sticky Step 5.6 rule and is not a listed doc target, so the Verification grep — scoped to the global SKILL.md only — passes with the defect still documented. That is the half-migration the plan's own No-Gos forbid. (c) Step 0.5 reads `sdlc-tool stage-query`, a repo-only CLI, but is placed in `.claude/skills-global/`, against the convention that global bodies stay generic. | **RESOLVED** — Documentation + Verification | Retarget the doc bullets to those lines, add `docs/sdlc/do-plan-critique.md` as a target, and extend the Verification grep across both files scoped to the Step 5.6 sections — a whole-file "string absent" anti-criterion will fail because both files legitimately still mention `revision_applied_at`. Split Step 0.5 into a generic probe in the global body plus the concrete `stage-query` invocation in the addendum. Also add row 4d to the hard-coded `expected` set at `tests/unit/test_sdlc_skill_md_parity.py:143-163`, which fails on an unexpected row before any other test runs. |
| CONCERN | Scope & Value | Open Questions 1-3 reopen the three decisions the Technical Approach states as settled (bound value, cap-reached behavior, escalation trigger), and tasks 2, 3 and 5 already implement the settled answers. A builder reading top-to-bottom ships all three before anyone answers them, and the plan itself names two of them as the PM check-ins worth having. | **RESOLVED** — Decisions D1/D2/D3 | Answer all three in the plan before build, or demote them to "chosen default, revisit after N lanes" with the revisit criterion written down. Question 2 is the consequential one and must be resolved together with the row-4d note's delivery channel: choosing `/do-build` at the cap is only defensible if the Accepted Residual Concerns note reliably lands. If it cannot, the honest cap-reached behavior is `Blocked`. |
| NIT | Scope & Value | The plan calls `MAX_CONCERN_RECRITIQUE_ROUNDS` env-overridable and claims `MAX_CRITIQUE_CYCLES` "is read with a default". `MAX_CRITIQUE_CYCLES` is a bare literal (`agent/pipeline_graph.py:35`) and neither `agent/pipeline_graph.py` nor `agent/sdlc_router.py` contains any `os.getenv`/`os.environ` reference, so the Update System section's justification rests on a false premise. | **RESOLVED** — §3 + Update System | — |

### Round 1 revision (applied)

All 3 blockers, 5 concerns and the nit are addressed. Every code claim below was
re-verified against the working tree at `667fecc16` before being written in.

| Finding | Resolution |
|---|---|
| B1 — bound not durable (`MAX_DISPATCH_HISTORY` eviction) | The bound no longer touches `_sdlc_dispatches` at all. It is `len(_concern_round_hashes)`, an append-only hash-deduplicated list written inside `record_verdict`'s existing single-writer transaction (`tools/sdlc_verdict.py:477-482`). Durable because the key is absent from `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`) and so is merged back by `_save()`; monotonic because nothing removes entries; G4-inert because it is absent from `_SNAPSHOT_PROJECTION_KEYS` (`agent/sdlc_router.py:193-201`). §3 + tasks 1, 1.5, 2. |
| B2 — G5 bypasses rows 4b/4c/4d | New §2.5: two step-asides inside `guard_g5_artifact_hash_cache` (re-key `agent/sdlc_router.py:615`; new with-concerns return-`None` before the `Dispatch` at `:619`), both returning `None` so the dispatch table owns the state. Task 3.5, with the alive test landed red first, plus a Success Criterion and a Verification row. |
| B3 — bound conflates NEEDS-REVISION rounds with with-concerns rounds | Dissolved rather than mitigated: the counter only increments on a `WITH CONCERNS` CRITIQUE verdict, so NEEDS REVISION rounds cannot consume it. No "first with-concerns timestamp" needs storing, which was the part not recoverable from the single-record `_verdicts["CRITIQUE"]`. §3 + a dedicated test. |
| C1 — `row_id` has no delivery channel | §4 rewritten: `/do-build` re-derives row 4d's own predicate from `sdlc-tool stage-query`. `row_id` plumbing moved to Rabbit Holes. D2 is now explicitly conditional on this working, with the flip-to-`Blocked` rule written into task 5. |
| C2 — Step 0.5 untested, cost-justified, unassertable trigger | Deferred to a follow-up issue (task 5.5) carrying the mechanical header-diff trigger. Full depth every round; the bound dropped 4 → 3 to absorb the cost. D3. |
| C3 — predicate verdict-kind contradiction | §1 states the predicate is verdict-kind-agnostic and the `WITH CONCERNS` test is a caller obligation, with the G7-gate-3 reason spelled out. Risk 1 layer 1 restated at call-site level. Task 7 now checks call sites and asserts the predicate body has *no* verdict test. |
| C4 — three wrong documentation targets | `.claude/skills/sdlc/SKILL.md` retargeted to lines 180 / 182 / 223 with an explicit "do not add a row table" note (`tests/unit/test_sdlc_skill_md_parity.py:81`); `docs/sdlc/do-plan-critique.md:76-78` added as a target; the Step 5.6 Verification grep split across both files and scoped to sections, not whole files. `"4d"` added to `tests/unit/test_sdlc_skill_md_parity.py:143-163`. The global-body/`stage-query` conflict is moot with Step 0.5 deferred. |
| C5 — Open Questions reopen settled decisions | Open Questions deleted; replaced by a Decisions section with D1/D2/D3 and a written revisit criterion each. |
| N1 — false `MAX_CRITIQUE_CYCLES` env-override premise | Corrected. `MAX_CRITIQUE_CYCLES` (`agent/pipeline_graph.py:35`) is a bare literal and neither module contains any `os.getenv`/`os.environ` (verified: zero matches). The new constant introduces the first env read and its `os` import; the Update System section says so. |

### Round 1 execution note

The war room ran at FULL depth with the frozen 3-critic roster and passed `critique-roster-check --plan-path` (3/3 complete, 0 ungrounded). The three lenses were executed in a single agent context rather than as three independent sub-agents, because no Agent/Task dispatch tool was exposed to the critique session; every finding is grounded in file:line reads of the verified source bundle rather than in independent judgement. Treat cross-critic corroboration in the Critics column as attribution to a lens, not as agreement between separate reviewers.

---

## Decisions

Round 1 flagged that the plan's Open Questions reopened three decisions the
Technical Approach already stated as settled, and that tasks 2, 3 and 5 already
implemented the settled answers — so a builder reading top-to-bottom would ship
all three before anyone answered them. All three are now resolved in the plan,
each with a written revisit criterion. There are no open questions.

**D1 — The bound's value: `MAX_CONCERN_RECRITIQUE_ROUNDS = 3`.**
The original 4 was justified by the `verdict-finalize-cluster` lane converging at
round 4, but that counted *lane-total* critique rounds of mixed kind. The counter
is now loop-scoped (only with-concerns rounds), so that datapoint does not
transfer and there is no empirical basis for 4. 3 is chosen because every round is
full depth (D3) and therefore expensive, while still admitting two rounds of
genuine concern-closing after the first verdict. `os.getenv`-overridable.
**Revisit after five with-concerns lanes:** if any hits the bound carrying
concerns a fourth round would plausibly have closed, raise it.

**D2 — Cap-reached behavior: `/do-build` with a recorded acceptance, not `Blocked`.**
CONCERNs are non-blocking by definition and a `Blocked` strands the lane on a human
who is usually not watching. This is only defensible because the Accepted Residual
Concerns note now has a working delivery channel — `/do-build` re-derives row 4d's
predicate from `sdlc-tool stage-query` rather than depending on a `row_id` that is
never plumbed into skill invocations (§4). **The decision is conditional and the
condition is written into task 5:** if that derivation cannot be made reliable at
build time, the builder stops and the cap-reached behavior flips to `Blocked`. A
silent build at the cap is the failure mode this plan exists to prevent, one level
up.

**D3 — Scoped vs. full re-critique: full depth, every round. Scoping deferred.**
The scoped pass was the plan's largest new surface, had no test, was justified by
cost rather than correctness, and its escalation trigger was unassertable prose.
It is deferred to a follow-up issue (task 5.5) carrying a mechanical trigger —
the changed `^## `/`^### ` header set intersected with `{Solution, Technical
Approach, Step by Step Tasks, Verification}` — which is assertable over two plan
fixtures. **Revisit when** a with-concerns lane's war-room cost is measured and
shown to be the binding constraint, not before.
