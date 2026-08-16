---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-16
tracking: https://github.com/yudame/ai/issues/2787
last_comment_id: none
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
- `4f778447f` "Make the SDLC dispatch record unbypassable via a router upsert slot (#2730) (#2802)" — **load-bearing in our favor**: it hardens `_sdlc_dispatches` as an unbypassable durable record, which is the state this plan's loop bound counts. Verified `tools/sdlc_dispatch.py` applies no truncation to the history list (it reports `len(history)` and never slices), so a dispatch-count bound is durable across a long lane.
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

- **New dependencies**: none. No new imports; `agent/sdlc_router.py` stays import-free of `tools/` (enforced by `tests/unit/test_architectural_constraints.py`).
- **Interface changes**: two existing dispatch-rule predicates change semantics; one new `DispatchRule` row is registered; one new module constant. No function signature changes. No new meta keys written by any skill — the bound is *derived* from existing durable state (see Technical Approach).
- **Coupling**: reduces it. The routing decision moves off the sticky `revision_applied` boolean (a `/do-plan` frontmatter side effect the router reads secondhand) and onto `_verdicts` + `_sdlc_dispatches`, which the router already owns and reads.
- **Data ownership**: unchanged. No skill gains a new write.
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

- **An event-scoped "has this verdict's revision been judged?" predicate.** A single helper that answers, for the latest CRITIQUE verdict, whether a `/do-plan` revision has landed *since* that verdict was recorded. It replaces the sticky-boolean test in both with-concerns rows.
- **A re-critique edge for the concern-closing revision.** Row 4c stops meaning "revision done, build it" and starts meaning "revision done, judge it".
- **A build edge that requires a clean verdict or an exhausted bound.** BUILD on a with-concerns plan becomes reachable only via a subsequent `READY TO BUILD (no concerns)` verdict, or via an explicit bounded-exhaustion path that records the acceptance.
- **A derived, unforgeable loop bound.** The number of `/do-plan-critique` dispatches already recorded in `_sdlc_dispatches` — durable, unbypassable since #2730/#2802, and written by nobody the loop can influence.
- **A concerns-scoped re-critique.** The re-critique round reads the prior round's CONCERN rows and the plan diff since the revision, not the whole plan from scratch.
- **A repaired Step 5.6 and G7 gate 3.** Both stop keying on the sticky boolean, so the `plan_revising` lock becomes armable per round instead of permanently inert.

### Flow

**CRITIQUE records `READY TO BUILD (with concerns)`** → row 4b → **`/do-plan` revision pass** (embeds Implementation Notes, writes `revision_applied_at`) → row 4c → **`/do-plan-critique`, concerns-scoped** → records a new verdict →

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

**2. Rows 4b / 4c / 4d**

| Row | Predicate (all also require: with-concerns verdict, no `pr_number`, `BUILD` not `completed`, `BUILD` in `{None, pending, ready}`) | Skill |
|---|---|---|
| `4b` | `not _concern_revision_is_unjudged(...)` | `/do-plan` — revise, embed Implementation Notes |
| `4c` | `_concern_revision_is_unjudged(...)` **and** `_critique_dispatch_count(...) < MAX_CONCERN_RECRITIQUE_ROUNDS` | `/do-plan-critique` — judge the concern-closing revision |
| `4d` | `_concern_revision_is_unjudged(...)` **and** `_critique_dispatch_count(...) >= MAX_CONCERN_RECRITIQUE_ROUNDS` | `/do-build` — bound exhausted, residual concerns accepted |

Row 4b's predicate change is not cosmetic: it is the fix for the sticky boolean on
its own axis. Today, at round 2, `revision_applied` is already true, so 4b misses
and 4c fires straight to build. Event-scoped, 4b correctly fires on every round's
fresh with-concerns verdict.

Rows 4b/4c/4d are mutually exclusive by construction (`not P` / `P and Q` /
`P and not Q`), which is the property the tests must pin.

**3. The bound: `MAX_CONCERN_RECRITIQUE_ROUNDS`**

Defined in `agent/pipeline_graph.py` alongside `MAX_CRITIQUE_CYCLES`, as a named
env-overridable constant with a grain-of-salt comment marking it provisional.
Default **4**, counted as *total* `/do-plan-critique` dispatches in
`_sdlc_dispatches` — not as extra rounds. Justification: the
`verdict-finalize-cluster` lane converged at round 4 (`no concerns`), which is the
only empirical convergence datapoint we have; 4 admits it exactly.

**Why a derived count and not a new counter.** A stored counter needs a writer,
and every candidate writer (`/do-plan-critique` Step 5.6, `/do-plan` Phase 4) is a
skill that can crash mid-step, be re-run, or be invoked standalone — the same class
of failure that made `plan_revising` unreliable. `_sdlc_dispatches` is written by
the router's own unbypassable upsert slot (#2730 / #2802), is never truncated
(verified: `tools/sdlc_dispatch.py` reports `len(history)` and never slices), and
cannot be influenced by the loop it bounds.

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
accountability comes from the record, not the halt:

- Row 4d's `reason` names the bound and the residual concerns explicitly.
- `/do-build` Step 1 reads the row id and, when it is `4d`, writes an
  **Accepted Residual Concerns** note into the plan's `## Critique Results` section
  naming which round's concerns were accepted unreviewed and why (bound exhausted).

This is the "explicit supervisor decision point recording accepted residual
concerns" option, resolved in favour of recording over halting.

**5. Concerns-scoped re-critique (`/do-plan-critique` Step 0.5)**

A new Step 0.5 in `.claude/skills-global/do-plan-critique/SKILL.md`: read
`sdlc-tool stage-query`; if the latest CRITIQUE verdict is with-concerns and
`revision_applied_at` postdates its `recorded_at`, run a **CONCERNS-SCOPED** pass:

- Read the prior round's CONCERN and BLOCKER rows from the plan's `## Critique Results` table.
- Read the plan diff since the revision (`git log -1 --format=%H` for the plan file at the prior verdict time, then `git diff`).
- Dispatch a reduced war room judging **only**: (a) does each prior concern's Implementation Note actually close it? (b) is the revision internally consistent with the rest of the plan? (c) does the revision introduce a new defect?
- **Escalate to a FULL pass** when the revision touches the plan's structure — the Solution, Technical Approach, Step by Step Tasks, or Verification sections — rather than only adding Implementation Notes. A structural revision is a new plan and deserves a new war room.

The determination is made by the skill from `stage-query` (authoritative state),
with the router's dispatch `reason` string as human-readable corroboration. No new
meta key, no new plumbing.

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
- G7 gate 3: self-heal only when `revision_applied_at` postdates the latest CRITIQUE
  verdict's `recorded_at` — the same predicate as (1), reused.

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
- [ ] `_sdlc_dispatches` absent or empty → count is `0` → row 4c can fire (correct: no rounds consumed yet). Test.
- [ ] Timestamps exactly equal → `False` (strict `>`), matching the existing `_critique_verdict_is_stale` convention. Test.

### Error State Rendering

- [ ] Row 4d's `reason` string must name the bound, the count, and that residual concerns were accepted unreviewed. Assert on the string, not just the skill. A silent build at the cap is the failure mode this whole plan exists to prevent, one level up.
- [ ] `/do-build`'s Accepted Residual Concerns note must land in the plan document and be visible in the PR. Test that the note is written when the dispatch row is `4d` and not written otherwise.

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: every existing case asserting row `4c` → `/do-build` on a with-concerns verdict with `revision_applied: true` now asserts `/do-plan-critique`. These are the tests that currently encode the bug; they must flip, and each flip is the demonstrated-red proof for this change.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: same flip at the decision-table level; add rows for 4c and 4d.
- [ ] `tests/unit/test_sdlc_router.py` (G7 cases) — UPDATE: G7 gate-3 self-heal cases keyed on the sticky `revision_applied` must be rewritten against the event-scoped predicate. A case asserting "lock self-heals when `revision_applied` is true" is asserting the defect.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: the parity check between the router and `.claude/skills/sdlc/SKILL.md`'s documented row table must cover the new row 4d and the changed 4b/4c targets.
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
- **Making the scoped re-critique cheap by cutting critics.** The saving comes from narrowing *what is judged* (prior concerns + revision diff), not from thinning the roster. A two-critic war room that misses the round-2 defect costs more than it saves.

## Risks

### Risk 1: Reopening the #1760 / #1925 / #1968 non-convergence loop

**Impact:** The exact failure this repo has already shipped twice — PLAN↔CRITIQUE ping-pong that never reaches BUILD, burning a lane and a human's attention. This is the single most important risk in the plan and the reason the bound is derived rather than stored.

**Mitigation, in three independent layers:**

1. **Disjointness.** #1760 is about a **no-concerns** verdict being re-staled by a notes-only revision. Every predicate added here requires `"WITH CONCERNS" in verdict`. The two cases cannot both hold on one verdict, and `_critique_verdict_is_stale` is not modified at all. The existing #1760/#2049 tests staying green *unchanged* is the mechanical proof, and is listed above as a blocker-if-red.
2. **No re-arming.** #1925/#1968 recurred because `/do-plan` rewrites `revision_applied_at` every pass, re-arming the latch each round. Here that same rewrite is what *advances* the loop — each round consumes a `/do-plan-critique` dispatch from a monotonically growing history the loop cannot rewind. The bound therefore tightens on every rewrite instead of resetting. This asymmetry must be stated in the code comment on the constant.
3. **Terminal by construction.** Row 4d dispatches `/do-build`, not `Blocked`. Even a pathological oscillation terminates in a build with a recorded acceptance rather than a stall.

### Risk 2: The re-critique is expensive enough that lanes get slower than the defect is costly

**Impact:** Every with-concerns plan pays extra war-room rounds. If a full-depth pass runs on every round, a 4-round lane pays 4 war rooms and the pipeline's throughput drops for a defect class that is real but not universal.

**Mitigation:** the concerns-scoped pass (Technical Approach §5) judges only the prior concerns and the revision diff, with full-depth escalation reserved for structurally-revising rounds. The bound caps total exposure at `MAX_CONCERN_RECRITIQUE_ROUNDS`. If lanes still feel slow, the constant is env-overridable and can be lowered to 3 without a code change.

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

**Location:** `_sdlc_dispatches` (the loop bound's source)
**Trigger:** two runs dispatch `/do-plan-critique` for one issue, inflating the count and prematurely exhausting the bound.
**Data prerequisite:** a single owner of the dispatch history.
**State prerequisite:** the issue lock is held.
**Mitigation:** already prevented. `sdlc-tool dispatch record` refuses on `ISSUE_LOCKED` (`tools/sdlc_dispatch.py:252`) and the router's upsert slot (#2730/#2802) is the only write path. Premature exhaustion would fail toward a build with a recorded acceptance, not toward a loop.

## No-Gos (Out of Scope)

- Nothing deferred — every relevant item is in scope for this plan. Specifically: the rows, the bound, the scoped re-critique, the Step 5.6 repair, the G7 gate-3 repair, the tests, and the docs all ship together. The `plan_revising` lock *removal* is named in Rabbit Holes as a deliberate non-goal rather than a deferral: this plan repairs the lock so it works, and takes no position on whether it should later be deleted.

## Update System

No update system changes required — this feature is purely internal. It touches
`agent/sdlc_router.py`, `agent/pipeline_graph.py`, and skill/doc markdown. The
`do-plan-critique` skill lives in `.claude/skills-global/`, which `/update` already
hardlinks to `~/.claude/skills/` via `scripts/update/hardlinks.py`; no new directory
is added, so no `RENAMED_REMOVALS` entry and no registration step. The new
`MAX_CONCERN_RECRITIQUE_ROUNDS` env override needs no `.env` entry — like
`MAX_CRITIQUE_CYCLES` it is read with a default and is not a secret.

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

- [ ] Create `docs/features/with-concerns-recritique-gate.md` describing the rows 4b/4c/4d split, the event-scoped predicate, the derived bound, and the recovery procedure for a stuck lane (`sdlc-tool meta-set` on `revision_applied_at`).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-pipeline.md` — the "Convergence latch" section must state that the latch governs the *no-concerns* path and point at the new doc for the with-concerns path, so the two are not confused by a future reader.
- [ ] Update `docs/sdlc/plan-revising-lock.md` — the Set and Clear Contract table and the G7 pseudocode both encode the sticky-`revision_applied` behaviour and are now wrong. Rewrite both against the event-scoped predicate. Describe only the new status quo.
- [ ] Update `.claude/skills/sdlc/SKILL.md` — the "G7 blocks build while plan revision is in flight" and "Convergence latch" paragraphs, plus the router row table, to cover rows 4c and 4d.
- [ ] Update `.claude/skills-global/do-plan-critique/SKILL.md` — new Step 0.5 (concerns-scoped pass), repaired Step 5.6, and an Outcome Contract table row making explicit that `READY TO BUILD (with concerns)` no longer reaches BUILD directly.

### Inline Documentation

- [ ] Docstring on `_concern_revision_is_unjudged` stating the fail-safe direction and *why* it is the inverse of the #1760 latch's.
- [ ] Comment on `MAX_CONCERN_RECRITIQUE_ROUNDS` marking it provisional and tunable, naming the `verdict-finalize-cluster` 4-round convergence as its only empirical basis, and stating the non-re-arming asymmetry from Risk 1 layer 2.
- [ ] Docstrings on rows 4b/4c/4d naming their mutual exclusivity and their D3 step-asides.

## Success Criteria

- [ ] A `READY TO BUILD (with concerns)` verdict cannot reach `/do-build` on the round that produced it, at any round number — verified specifically for rounds 2 and 3, where the sticky boolean currently exempts the plan.
- [ ] `sdlc-tool next-skill` returns `/do-plan-critique` on a ledger reconstructed from the #2757 state described in the issue's Recon Summary (with-concerns verdict, `revision_applied_at` postdating it) — end-to-end through the real CLI, not only the router unit.
- [ ] Rows 4b, 4c, and 4d are pairwise mutually exclusive over the full state space, asserted by an exhaustive test rather than by inspection.
- [ ] The loop terminates in at most `MAX_CONCERN_RECRITIQUE_ROUNDS` critique dispatches, asserted by simulating a plan that returns with-concerns forever.
- [ ] At the bound, the dispatch is `/do-build` with a `reason` naming the bound and the accepted residual concerns; an Accepted Residual Concerns note lands in the plan's `## Critique Results`.
- [ ] A `READY TO BUILD (no concerns)` verdict with 3 NITs routes to `/do-build` with zero extra critique rounds.
- [ ] All existing `_critique_verdict_is_stale` tests (#1760, #2049) pass **unchanged**.
- [ ] `sdlc-tool meta-set --key plan_revising --value true` on a once-revised plan actually binds G7 (it currently cannot).
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
- Confirm `tools/sdlc_dispatch.py` applies no truncation to `_sdlc_dispatches`.
- If either is false, STOP and report — the bound's durability depends on both.

### 2. Add the predicate and the constant

- **Task ID**: build-predicate
- **Depends On**: verify-revision-timestamp
- **Validates**: tests/unit/test_sdlc_router.py
- **Informed By**: the #1760 latch at `agent/sdlc_router.py:1118` (same field, opposite fail-safe)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_concern_revision_is_unjudged(stage_states, meta)` next to `_critique_verdict_is_stale`.
- Add `MAX_CONCERN_RECRITIQUE_ROUNDS` (default 4, env-overridable) to `agent/pipeline_graph.py` with the provisional/tunable comment.
- Add `_critique_dispatch_count(stage_states)` counting `/do-plan-critique` entries in `_sdlc_dispatches`.
- Do NOT modify `_critique_verdict_is_stale`.

### 3. Rewire rows 4b / 4c / 4d

- **Task ID**: build-rows
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Re-key 4b off the predicate instead of `not revision_applied`.
- Re-target 4c to `/do-plan-critique`, gated on the predicate and the bound.
- Add row 4d (`/do-build`, bound exhausted) with the reason string naming the bound and the acceptance.
- Preserve the D3 `pr_number` / `BUILD == completed` step-asides on all three.

### 4. Repair G7 gate 3

- **Task ID**: build-g7
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the sticky-`revision_applied` self-heal with the event-scoped predicate so the lock is armable per round.

### 5. Skill-side changes

- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- `/do-plan-critique` Step 0.5: the concerns-scoped pass with structural-revision escalation to full depth.
- `/do-plan-critique` Step 5.6: delete the `revision_applied: true` exemption; key on the event-scoped comparison.
- `/do-plan-critique` Outcome Contract: state that with-concerns no longer reaches BUILD directly.
- `/do-build` Step 1: write the Accepted Residual Concerns note when the dispatch row is `4d`.

### 6. Tests

- **Task ID**: build-tests
- **Depends On**: build-rows, build-g7, build-skills
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: router-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Flip the existing 4c → `/do-build` cases; capture the red output before the fix as the demonstrated-red paper trail.
- Round-2 and round-3 regression: with-concerns + sticky `revision_applied` must not reach build.
- Exhaustive mutual-exclusivity over 4b/4c/4d.
- Forever-with-concerns termination at the bound, with the row-4d reason asserted.
- Nits-only verdict routes to build with zero extra rounds.
- All Failure Path Test Strategy cases above.
- End-to-end `sdlc-tool next-skill` on a reconstructed #2757-shaped ledger.
- Run with `scripts/pytest-clean.sh`, targeted files only.

### 7. Disjointness validation

- **Task ID**: validate-disjointness
- **Depends On**: build-tests
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `_critique_verdict_is_stale` is byte-for-byte unmodified.
- Verify no #1760/#2049 latch test was edited.
- Verify every new predicate requires `"WITH CONCERNS" in verdict`.
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
| Skill parity tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Architectural constraints hold | `scripts/pytest-clean.sh tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Row 4d is registered | `grep -c 'row_id="4d"' agent/sdlc_router.py` | output contains 1 |
| The bound constant exists | `grep -c 'MAX_CONCERN_RECRITIQUE_ROUNDS' agent/pipeline_graph.py` | output > 0 |
| Row 4c no longer targets build | `grep -A6 'row_id="4c"' agent/sdlc_router.py \| grep -c 'SKILL_DO_BUILD'` | match count == 0 |
| Step 5.6 sticky exemption is gone | `grep -c 'revision_applied: true. is already in the plan frontmatter' .claude/skills-global/do-plan-critique/SKILL.md` | match count == 0 |
| The #1760 latch is untouched | `git diff origin/main -- agent/sdlc_router.py \| grep -c '^-.*revision_dt is not None and not (plan_dt > revision_dt)'` | match count == 0 |
| Feature doc exists | `test -f docs/features/with-concerns-recritique-gate.md` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_sdlc_router.py` | exit code 1 |

## Critique Results

**Round 1** — FULL depth (force-FULL: touches `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/skills-global/`). Verdict: **NEEDS REVISION** (3 blockers, 5 concerns, 1 nit).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | The derived bound is not durable. `_sdlc_dispatches` IS truncated: `MAX_DISPATCH_HISTORY = 10` at `agent/sdlc_router.py:98`, FIFO-evicted in `record_dispatch` at `agent/sdlc_router.py:1990-1991`. `tools/sdlc_dispatch.py` does not slice, but it calls `agent.sdlc_router.record_dispatch` and reports `len(history)` after truncation — so the Freshness Check verified the wrong module. A count of `/do-plan-critique` entries decreases as entries age out, so it is not monotonic and Risk 1 layer 2's "tightens on every rewrite" asymmetry does not hold; layer 3 also fails because row 4d only fires at the bound. | pending | Do not derive the bound from `_sdlc_dispatches`. Either persist a monotonic counter in the ledger's owned metadata (the `_critique_cycle_count` pattern at `agent/pipeline_state.py:1074`, but incremented on the row-4c dispatch rather than inside `fail_stage`), or exempt `/do-plan-critique` entries from eviction at `agent/sdlc_router.py:1990` — the latter changes `compute_same_stage_count`'s contiguous-recency assumption and needs its own G4 regression. Pin it with a test that appends 15+ dispatches and asserts the count survives. |
| BLOCKER | Risk & Robustness | G5 bypasses the new rows entirely. `evaluate_guards` (`agent/sdlc_router.py:771-789`) runs before the dispatch table, and G5's ready-to-build branch matches with-concerns too (`if CRITIQUE_READY_TO_BUILD in verdict_text`, `agent/sdlc_router.py:589`, no `WITH CONCERNS` exclusion). Its only revision-aware step-aside, `plan_revising and not revision_applied` at `agent/sdlc_router.py:606`, reads the same sticky boolean the plan is fixing and is dead from round 2 on. After a row-4c re-critique records a fresh with-concerns verdict the plan hash matches `artifact_hash`, so G5 dispatches `/do-build` with `row_id="G5"` and rows 4b/4c/4d never evaluate. | pending | In `guard_g5_artifact_hash_cache`, before the `Dispatch(SKILL_DO_BUILD, ...)` return at `agent/sdlc_router.py:610`, add `if "WITH CONCERNS" in verdict_text and _concern_revision_is_unjudged(stage_states, meta): return None`, and re-key line 606 to `meta.get("plan_revising") and _concern_revision_is_unjudged(stage_states, meta)`. `verdict_text` is already normalized at line 583. Add a test pinning `current_plan_hash == artifact_hash` on a with-concerns verdict with `revision_applied=True` and asserting row 4c, not `row_id="G5"` — without it the feature passes its own unit tests while being dead in production. |
| BLOCKER | History & Consistency | The bound counts *total* critique dispatches, but `_sdlc_dispatches` records carry no row id, so rows 2/2b/2c, G1 and G5 NEEDS-REVISION re-critiques are indistinguishable from with-concerns rounds. A plan that took 3 NEEDS REVISION rounds enters its first with-concerns verdict already at the bound, so row 4d fires immediately: straight to `/do-build` with concerns "accepted unreviewed" and zero re-critique. That reproduces the plan's own stated pathology for exactly the plans needing the most scrutiny, and silently, because the outcome is a recorded acceptance rather than a failure. Independent of the truncation defect. | pending | Scope the count to the with-concerns loop, not the lane. Dispatch records carry only `skill`/`at`/`stage`/`confirmed`/`stage_snapshot` (`agent/sdlc_router.py:1972-1996`), so row-id filtering is impossible at read time; the available key is `at >= first_with_concerns_at`. Note `_verdicts["CRITIQUE"]` is a single record (`agent/sdlc_router.py:1090-1092`), so the first with-concerns timestamp is NOT recoverable from `_verdicts` and must be stored when the loop starts. This changes the shape of Technical Approach section 3. |
| CONCERN | Risk & Robustness | The cap-reached accountability mechanism has no delivery channel. `row_id` exists only in the router's own output (`tools/sdlc_next_skill.py:663`, `.claude/skills/sdlc/SKILL.md:246`) and is consumed by the supervisor; it is not plumbed into the dispatched skill's invocation, and `/do-build`'s SKILL.md never mentions `row_id`. As written, the only thing between a bound-exhausted lane and a silently unreviewed build is a note the build skill cannot know to write. | pending | Do not add a skill-written meta key (Technical Approach section 3 argues against exactly that). Have `/do-build` Step 1 derive the condition from `sdlc-tool stage-query` — the same source Step 0.5 uses — testing `WITH CONCERNS` in the latest CRITIQUE verdict AND the event-scoped unjudged condition AND the bound being reached, i.e. row 4d's own predicate. If you instead plumb `row_id`, that plumbing is its own build task and must appear in Step by Step Tasks. |
| CONCERN | Scope & Value | Step 0.5 is the largest new surface and the only one with no test. It adds a state read, a git-diff computation, a reduced-roster mode and a heuristic escalation trigger, all as prose to an LLM; Test Impact covers only `test_sdlc_skill_md_parity.py`, which checks row-id coverage and docstrings, not Step 0.5 behavior. Its justification is Risk 2 (cost), not correctness — the loop is correct with full depth every round — which inverts the plan's own Rabbit Hole reasoning about cheap war rooms. | pending | If Step 0.5 stays in scope, make the escalation trigger mechanical: compute the set of `^## `/`^### ` headers whose bodies changed in the plan diff between the prior verdict's commit and HEAD, and escalate to FULL when that set intersects `{Solution, Technical Approach, Step by Step Tasks, Verification}`. That is assertable over two plan-file fixtures; "the revision touches the plan's structure" as a prose judgement is not, and without it Risk 2's mitigation cannot be verified. Preferred alternative: split Step 0.5 to a follow-up issue and ship full-depth re-critique first. |
| CONCERN | History & Consistency | The plan contradicts itself on whether the new predicate tests verdict kind. Technical Approach section 1 defines it as a pure timestamp comparison with `WITH CONCERNS` enforced by the rows; section 7 reuses "the same predicate" for G7 gate 3, which must also self-heal on NEEDS REVISION and MAJOR REWORK locks; task 7 then orders the validator to verify every new predicate requires `WITH CONCERNS`, treating failure as a blocker. Applied literally that forces the verdict test into the predicate body and stops G7 gate 3 self-healing on a NEEDS REVISION lock, escalating to `Blocked` after `MAX_PLAN_REVISING_DISPATCHES` (`agent/sdlc_router.py:92`). Risk 1's disjointness argument rests on the same false claim. | pending | State explicitly that `_concern_revision_is_unjudged` is verdict-kind-agnostic and the `WITH CONCERNS` requirement is a caller obligation, then rewrite task 7's third bullet to check the rows and G5 rather than the predicate. Restate the number-1760 disjointness proof at call-site level: `_critique_verdict_is_stale` unmodified, and every new call site (rows 4b/4c/4d, G5's ready-to-build branch, G7 gate 3) either tests `WITH CONCERNS` first or sits on a path number 1760 never governed. Put that in the predicate docstring — the current phrasing invites a future reader to add the verdict test inside it and silently break G7. |
| CONCERN | History & Consistency | Three documentation targets are wrong or missing. (a) There is no router row table in `.claude/skills/sdlc/SKILL.md`, and `tests/unit/test_sdlc_skill_md_parity.py:81` fails the build if one is added; the real edits there are the G7 guards-table row (line 180, encoding `revision_applied != True`), the G5 row (line 182, "Never re-dispatch `/do-plan-critique` on an unchanged plan"), and the hard-coded "18 rows" count at line 223. (b) `docs/sdlc/do-plan-critique.md:77-78` carries the same sticky Step 5.6 rule and is not a listed doc target, so the Verification grep — scoped to the global SKILL.md only — passes with the defect still documented. That is the half-migration the plan's own No-Gos forbid. (c) Step 0.5 reads `sdlc-tool stage-query`, a repo-only CLI, but is placed in `.claude/skills-global/`, against the convention that global bodies stay generic. | pending | Retarget the doc bullets to those lines, add `docs/sdlc/do-plan-critique.md` as a target, and extend the Verification grep across both files scoped to the Step 5.6 sections — a whole-file "string absent" anti-criterion will fail because both files legitimately still mention `revision_applied_at`. Split Step 0.5 into a generic probe in the global body plus the concrete `stage-query` invocation in the addendum. Also add row 4d to the hard-coded `expected` set at `tests/unit/test_sdlc_skill_md_parity.py:143-163`, which fails on an unexpected row before any other test runs. |
| CONCERN | Scope & Value | Open Questions 1-3 reopen the three decisions the Technical Approach states as settled (bound value, cap-reached behavior, escalation trigger), and tasks 2, 3 and 5 already implement the settled answers. A builder reading top-to-bottom ships all three before anyone answers them, and the plan itself names two of them as the PM check-ins worth having. | pending | Answer all three in the plan before build, or demote them to "chosen default, revisit after N lanes" with the revisit criterion written down. Question 2 is the consequential one and must be resolved together with the row-4d note's delivery channel: choosing `/do-build` at the cap is only defensible if the Accepted Residual Concerns note reliably lands. If it cannot, the honest cap-reached behavior is `Blocked`. |
| NIT | Scope & Value | The plan calls `MAX_CONCERN_RECRITIQUE_ROUNDS` env-overridable and claims `MAX_CRITIQUE_CYCLES` "is read with a default". `MAX_CRITIQUE_CYCLES` is a bare literal (`agent/pipeline_graph.py:35`) and neither `agent/pipeline_graph.py` nor `agent/sdlc_router.py` contains any `os.getenv`/`os.environ` reference, so the Update System section's justification rests on a false premise. | pending | — |

### Round 1 execution note

The war room ran at FULL depth with the frozen 3-critic roster and passed `critique-roster-check --plan-path` (3/3 complete, 0 ungrounded). The three lenses were executed in a single agent context rather than as three independent sub-agents, because no Agent/Task dispatch tool was exposed to the critique session; every finding is grounded in file:line reads of the verified source bundle rather than in independent judgement. Treat cross-critic corroboration in the Critics column as attribution to a lens, not as agreement between separate reviewers.

---

## Open Questions

1. **The bound's value.** `MAX_CONCERN_RECRITIQUE_ROUNDS = 4` total critique dispatches is derived from a single datapoint — the `verdict-finalize-cluster` lane converging at round 4. Is 4 the right default, or should it be 3 (accepting that the one lane we have evidence for would have been cut off one round early, at a round that returned a single concern)?
2. **Cap-reached behavior.** The plan chooses `/do-build` with a recorded acceptance over `Blocked`, on the grounds that CONCERNs are non-blocking by definition and a stall costs more than it saves. Is that the right call, or should exhausting the bound stop the lane for a human?
3. **Scoped vs. full re-critique.** The plan runs a concerns-scoped pass by default and escalates to full depth only when the revision touches the plan's structure. Is "touches Solution / Technical Approach / Step by Step Tasks / Verification" the right escalation trigger, or too narrow?
