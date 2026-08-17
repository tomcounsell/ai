---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-16
tracking: https://github.com/yudame/ai/issues/2787
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-17T03:12:00Z
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

**Baseline commit:** `2dce9812d` (current `main` HEAD at revision round 2; all
file:line references below were re-read against this tree, not carried forward)
**Issue filed at:** 2026-08-13T11:54:32Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `agent/sdlc_router.py:897` — `_rule_critique_ready_with_concerns_revision_applied` — **still holds**, verbatim as the issue describes. Registered as row `4c` at `agent/sdlc_router.py:1600`.
- `agent/sdlc_router.py:873` — `_rule_critique_ready_with_concerns_no_revision` (row `4b`, `agent/sdlc_router.py:1594`) — **still holds**; keyed on `not meta["revision_applied"]`.
- `agent/sdlc_router.py:315` — `guard_g1_critique_loop` — **still holds**; fires only on `NEEDS REVISION` / `MAJOR REWORK`, and steps aside on `pr_number`. With-concerns is deliberately outside it.
- `agent/sdlc_router.py:350` — `guard_g2_critique_cycle_cap` — **still holds**, and is confirmed inert on this path (see Revised bucket in the issue's Recon Summary; `agent/pipeline_state.py:1074` increments `critique_cycle_count` only inside `fail_stage("CRITIQUE")`, and a with-concerns verdict marks CRITIQUE `completed`).
- `agent/sdlc_router.py:677-682` — G7 gates 2 and 3 — **still holds**; rule 3's `revision_applied` self-heal is present and is what makes a `plan_revising`-keyed fix dead on arrival.
- `agent/sdlc_router.py:1033` — `_critique_verdict_is_stale`; the #1760 latch and #2049's verdict-kind gate live at `agent/sdlc_router.py:1118-1124` (`if revision_applied_at and not requires_revision:`). **Still holds, and its polarity is the opposite of what round-1's plan text claimed** — see the rewritten Prior Art bullet and Risk 1.
- `agent/sdlc_router.py:1132` — `_rule_critique_verdict_stale` (row 2b), registered at `agent/sdlc_router.py:1563`, evaluated before rows 4a/4b/4c because `evaluate` walks `DISPATCH_RULES` in order — **re-verified**.
- `agent/sdlc_router.py:598-622` — G5's `if CRITIQUE_READY_TO_BUILD in verdict_text:` branch: the D3 step-aside at `:601`, the #1871 short-circuit at `:615`, the `Dispatch(SKILL_DO_BUILD, ..., row_id="G5")` at `:619-622` — **re-verified line-for-line**.
- `agent/sdlc_router.py:680-682` — G7 Gate 3, `if meta.get("revision_applied"): return None` — **re-verified**.
- `tools/sdlc_stage_query.py:766-768` — the `for _router_key in ("_verdicts", "_sdlc_dispatches")` threading loop. **New finding, see Technical Approach §3**: `stage-query` projects only stage names plus those two underscore keys into `stages`, so any *new* underscore stage-states key is invisible to `sdlc-tool next-skill`.
- `tools/sdlc_stage_query.py:597-620` and `:621-648` (`_default_meta`) — the `_meta` projection and its key-parity requirement (#2769). `critique_cycle_count` is the working precedent for surfacing an underscore counter into `_meta`.
- `agent/pipeline_state.py:88` — `_OWNED_METADATA_KEYS = {"_patch_cycle_count", "_critique_cycle_count", "_stage_skips"}` — **re-verified**; every other `_*` key is merged back from the live store on `_save()`.
- `agent/pipeline_graph.py:35` — `MAX_CRITIQUE_CYCLES = 2`, a bare literal. `grep -c 'os\.getenv\|os\.environ'` returns **0** for both `agent/pipeline_graph.py` and `agent/sdlc_router.py` — re-verified at this baseline.
- `agent/sdlc_router.py:2003-2056` — `compute_same_stage_count` counts *consecutive same-skill* dispatches and breaks the streak on a skill change. **New finding, see Risk 1**: G4 therefore cannot bound a two-skill alternation.
- `.claude/skills/sdlc/SKILL.md:223` — reads "dispatch rules (18 rows)". `len(DISPATCH_RULES)` is **19** today (`['1','2','2b','2c','3','4a','4b','4c','5','6','7','8','8b','8c','8d','8e','8f','9','10']`, executed against this tree). The literal is already stale by one; this plan adds no row, so the correct post-change value is **19**.
- `tests/unit/test_sdlc_skill_md_parity.py:143-163` — the hard-coded `expected` row-id set. It matches the 19 rows above; **no edit needed**, because this plan registers no new row.
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

**Notes:** The one genuine drift is that PR #2790 landed 24 minutes after the issue was filed and shifted line numbers within `agent/sdlc_router.py`. All line references in this plan are re-read against `2dce9812d`.

## Prior Art

- **#1760 / #1761**: "investigation: /do-sdlc PLAN↔CRITIQUE router never converges to BUILD (notes-only revision re-stales a clean verdict)" — **not a counter-precedent: the direct predecessor, and this plan supersedes it.** #1760's loop *was* the with-concerns settle path, and the latch it added is still pointed straight at it. `_critique_verdict_is_stale` engages the latch only when `revision_applied_at and not requires_revision` (`agent/sdlc_router.py:1118`), where `requires_revision` means NEEDS REVISION / MAJOR REWORK — so #2049 narrowed the latch **away from** NEEDS REVISION and left `READY TO BUILD (with concerns)` as its live domain. The docstring says so verbatim (`agent/sdlc_router.py:1100-1103`): "the latch protects ONLY the settle-and-build path — a READY TO BUILD (with concerns) verdict whose own settle revision must not re-stale it back into critique (#1760)". Round 1's plan text asserted the opposite polarity and built its whole safety argument on it; that claim is retracted. **The re-critique edge this issue asks for already exists — it is row 2b — and #1760 switched it off here because it had no bound.** This plan supplies the bound and switches it back on, at the same line #1760 acted. See Technical Approach §2 and Risk 1.
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
| #1760 latch | `revision_applied_at` to stop a settle revision re-staling a with-concerns verdict | Not a failure — a **deliberate suppression taken because no bound existed**. Row 2b was already the re-critique edge; #1760 could either let it loop forever or switch it off, and with no counter available it switched it off. #2049 then narrowed the latch to the non-revision-requiring verdicts, which is exactly the with-concerns settle path. The lesson: the missing ingredient was never the edge, it was a **terminating bound**. Supply the bound and the suppression becomes unnecessary — for with-concerns only. |

**Root cause pattern:** every prior attempt keyed a per-round decision on a
process-lifetime-sticky boolean (`revision_applied`) or on a bare timestamp
without the verdict kind. Neither can express "has *this* verdict's revision been
judged?". The fix must ask exactly that question, and it must ask it with both
halves.

## Architectural Impact

- **New dependencies**: none. `agent/sdlc_router.py` stays import-free of `tools/` (enforced by `tests/unit/test_architectural_constraints.py`). `agent/pipeline_graph.py` gains an `os` import (stdlib) for the constant's env override.
- **Interface changes**: `_critique_verdict_is_stale` gains one bounded with-concerns branch; two existing dispatch-rule predicates (4b, 4c) change semantics; one new module-level helper and one new constant; `guard_g5_artifact_hash_cache` and `guard_g7_plan_revising` gate 3 change their step-aside conditions. **No new `DispatchRule` row** — `len(DISPATCH_RULES)` stays 19 and `tests/unit/test_sdlc_skill_md_parity.py:143-163` needs no edit. No function signature changes.
- **New persisted state**: one new stage-states key, `_concern_round_count` (a monotonically incremented integer). It is not in `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`) so the existing `_save()` merge preserves it, and not in `_SNAPSHOT_PROJECTION_KEYS` (`agent/sdlc_router.py:193-201`) so G4 is unaffected. It is surfaced to the router as `_meta["concern_round_count"]`, following the existing `_critique_cycle_count` → `critique_cycle_count` precedent (`tools/sdlc_stage_query.py:597-620`). No Popoto model field changes, therefore no `scripts/update/migrations.py` entry: an absent key reads as `0`, the correct starting state for every pre-existing lane.
- **Coupling**: reduces it. The routing decision moves off the sticky `revision_applied` boolean (a `/do-plan` frontmatter side effect the router reads secondhand) and onto `_verdicts` plus a counter written by the verdict recorder — state the router already reads. It also **removes** a mechanism rather than adding one: the re-critique edge is row 2b, which already exists.
- **Data ownership**: `tools/sdlc_verdict.py::record_verdict` gains one write, inside the transaction it already owns. **No skill gains a new write.** That distinction is the point: a skill can crash mid-step, be re-run, or be invoked standalone, which is what made `plan_revising` unreliable; the verdict recorder is a single-writer tool whose write is a precondition for the routing that consumes it.
- **Reversibility**: high. The change is confined to predicate bodies and one row registration; reverting restores row 4c's current target.

## Appetite

**Size:** Large

Round 3 flagged that `**Review rounds:** 2+` below is verbatim
`.claude/skills-global/do-plan/SCOPING.md`'s **Large** definition ("2-3 PM
check-ins, 2+ review rounds"), not its Medium one ("1 review round"). Three
critique rounds across 13 tasks, 6 roles, 20 Verification rows and 11
documentation targets settle it. The scope itself is unchanged — only the label,
which was understating the communication budget.

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

Round 2's third blocker forced a re-derivation of how this interacts with #1760,
and the answer changed the shape of the fix. The re-critique edge is not missing
— it is **row 2b**, which already routes a stale CRITIQUE verdict back to
`/do-plan-critique`. #1760 switched it off for the with-concerns settle path
because the loop had no terminating bound. So the work is not "add a re-critique
edge and hope it does not collide with 2b"; it is **bound the loop, then let 2b
run**. That is a smaller change, it lands the bound at the one line that decides
whether the loop turns, and it makes the #1760 relationship a supersession that
can be stated plainly instead of a disjointness that was never true.

- **A bounded with-concerns branch in the #1760 latch.** `_critique_verdict_is_stale` is modified — deliberately, and this is the load-bearing edit. For a `READY TO BUILD (with concerns)` verdict the latch engages *only once the bound is exhausted*; below the bound it stands down and the settle revision re-stales the verdict, so row 2b re-critiques it. The no-concerns and NEEDS REVISION / MAJOR REWORK paths are untouched.
- **A monotonic round counter owned by the verdict recorder.** `_concern_round_count`, incremented inside `record_verdict`'s existing single-writer transaction on every recorded with-concerns CRITIQUE verdict. It only grows, it counts *only* with-concerns rounds, and it is surfaced to the router through the same `_meta` channel `critique_cycle_count` already uses.
- **An event-scoped "has this verdict's revision landed?" predicate**, replacing the sticky-boolean test on rows 4b and 4c. It is **verdict-kind-agnostic**; the `WITH CONCERNS` requirement is a caller obligation.
- **Row 4c becomes the bound-exhausted build edge.** It keeps dispatching `/do-build`, but only when the bound is spent — and the last round's Implementation Notes have been embedded by a preceding 4b revision, so the acceptance is recorded in the plan text before the build starts. **No new row is registered.**
- **An unconditional G5 step-aside on with-concerns.** G5 runs before the dispatch table and its cached-READY-TO-BUILD branch matches `READY TO BUILD (WITH CONCERNS)` too. Without the step-aside, rows 2b/4b/4c are unreachable in production. This is co-load-bearing, not a nicety.
- **A repaired Step 5.6 and G7 gate 3.** Both stop keying on the sticky boolean, so the `plan_revising` lock becomes armable per round instead of permanently inert.

### Flow

Two ledger states drive everything. Call them **S1** — a with-concerns verdict is
the most recent event, no revision has landed since — and **S2** — a `/do-plan`
revision has landed since that verdict.

**CRITIQUE records `READY TO BUILD (with concerns)`** (recorder increments
`_concern_round_count`) → state **S1** → G5 steps aside on with-concerns → G7
gate 4 (while the Step 5.6 lock is set) or row 4b (once it is cleared), **both
dispatching `/do-plan`** → **`/do-plan` revision pass** (embeds Implementation
Notes, co-writes `revision_applied_at`) → state **S2** →

- **below the bound:** the latch stands down → row 2b → **`/do-plan-critique`** → a new verdict is recorded, which returns the ledger to S1 for whatever that verdict says:
  - `READY TO BUILD (no concerns)` → row 4a → **`/do-build`**
  - `READY TO BUILD (with concerns)` again → S1 → row 4b → another revision → S2 → … (bounded)
  - `NEEDS REVISION` → G1 / row 3 → **`/do-plan`** (existing path, unchanged)
- **at the bound:** the latch engages → row 2b steps aside → row 4c → **`/do-build`**, residual concerns recorded as explicitly accepted.

The terminal round therefore reads S1 → 4b (`/do-plan`, embed the final round's
notes) → S2 → 4c (`/do-build`). That extra revision is not waste: it is what puts
the accepted concerns into the plan document before anyone builds from it.

### Technical Approach

**1. The predicate: `_concern_revision_is_unjudged(stage_states, meta)`**

Returns `True` when a `/do-plan` revision has landed *since* the latest CRITIQUE
verdict was recorded — state **S2** in the Flow above. Reads:

- `stage_states["_verdicts"]["CRITIQUE"]["recorded_at"]` — when the verdict was written.
- `meta["revision_applied_at"]` — when `/do-plan` last settled a revision. This field is parsed out of the plan frontmatter by `_parse_revision_applied_at` (`tools/sdlc_stage_query.py:414`) and projected into `_meta` at `tools/sdlc_stage_query.py:597`; `agent/sdlc_router.py:1118` already consumes it exactly this way.

`revision_applied_at > recorded_at` → `True`. Anything else — field absent,
unparseable, equal, or earlier — → `False`.

**Fail-safe direction matters and is deliberate.** `False` means "no revision has
landed since this verdict", which routes to row 4b (`/do-plan`), not to BUILD. So
a malformed or missing `revision_applied_at` degrades to *a revision pass* rather
than to a free pass to build.

**The predicate is verdict-kind-agnostic, deliberately and permanently.** It asks
one question — "did a `/do-plan` revision land after the latest CRITIQUE verdict
was recorded?" — and nothing about what that verdict *said*. The `WITH CONCERNS`
requirement lives at the call sites, never inside the predicate body:

- Rows 4b and 4c each test `"WITH CONCERNS" in verdict` themselves, *before* calling it.
- **G7 gate 3 must call it without any verdict-kind test.** G7's self-heal has to release a `plan_revising` lock left behind by a `NEEDS REVISION` or `MAJOR REWORK` round too. Pushing the `WITH CONCERNS` test into the predicate body would make gate 3 return `False` on those locks, so G7 would fall through to gate 5 and escalate to `Blocked` after `MAX_PLAN_REVISING_DISPATCHES` (`agent/sdlc_router.py:92`, value 2) — a new stall, introduced by the fix.

The docstring must say this outright, because the obvious "tidying" refactor a
future reader will attempt is to move the verdict test inside.

**2. The load-bearing edit: bound the #1760 latch on the with-concerns path**

This is the change round 2's blocker 3 forced, and it replaces the previous
revision's "add a re-critique row that works disjointly from #1760" design. That
design was built on an inverted reading of the code. The correct reading:

```python
# agent/sdlc_router.py:1113-1124 (current)
requires_revision = (
    CRITIQUE_NEEDS_REVISION in verdict_text or CRITIQUE_MAJOR_REWORK in verdict_text
)
revision_applied_at = (meta or {}).get("revision_applied_at")
if revision_applied_at and not requires_revision:
    ...
    if revision_dt is not None and not (plan_dt > revision_dt):
        return False  # latest /do-plan dispatch settled this verdict
```

`not requires_revision` covers exactly two verdict kinds: `READY TO BUILD` and
`READY TO BUILD (with concerns)`. The docstring at `agent/sdlc_router.py:1100-1103`
names the second one explicitly as what the latch protects. **The with-concerns
settle path is the latch's live domain, and suppressing row 2b there is precisely
what this issue is asking to undo.**

The edit adds one branch inside the same `try`. **Placement is load-bearing: the
with-concerns branch goes ahead of the `if not latest_plan_at: return False`
early return**, not merely ahead of the existing latch, so that the whole
with-concerns decision is control-flow independent of `_sdlc_dispatches` (round
3's concern 3 — the previous revision claimed that independence while the early
return still gated it):

```python
with_concerns = (not requires_revision) and "WITH CONCERNS" in verdict_text
if with_concerns:
    # #2787: row 2b IS the re-critique edge for the concern-closing revision.
    # #1760 suppressed it here because the loop had no bound; the bound now
    # exists, so the latch engages only once it is spent.
    if concern_round_count(meta) >= MAX_CONCERN_RECRITIQUE_ROUNDS:
        return False          # bound exhausted: never stale again on this path
    # Below the bound: stale IFF a revision actually LANDED since this verdict
    # (state S2). Deliberately NOT the timestamp fallthrough: `plan_dt` comes
    # from `_latest_dispatch_at`, stamped when /do-plan is DISPATCHED, so a
    # fallthrough would call the verdict stale the instant row 4b fires and let
    # row 2b (registered earlier) re-critique a plan nobody has revised yet.
    return _concern_revision_is_unjudged(stage_states, meta)
elif revision_applied_at and not requires_revision:
    ...existing #1760 latch, byte-for-byte unchanged...
```

Round 3's concern 2 is why the below-bound arm is a predicate call rather than a
fallthrough. Under a fallthrough, the sequence S1 → row 4b dispatches `/do-plan`
→ `record_dispatch` stamps `at` → `verdict_dt < plan_dt` is immediately true →
row 2b fires, **before `/do-plan` has written anything**. In the normal path that
is merely early; when `/do-plan` crashes or no-ops it re-critiques an unrevised
plan and burns a `_concern_round_count` slot. It is benign in today's tree only
because row 2b's documented loop bound is G5's unchanged-hash short-circuit — the
exact backstop §4.5 removes for with-concerns. Keying on
`_concern_revision_is_unjudged` makes row 2b own S2 and only S2, which is what
the Flow always claimed.

Three properties this shape has that the previous design did not:

- **It answers round 2's blocker 2 by construction.** Row 2b is no longer a competitor that routes around the bound; it *is* the bounded edge. There is no "row 2b fires when the latch goes inert" hole, because the bound-exhausted `return False` is unconditional — it does not depend on `revision_applied_at` being present or on any timestamp ordering, which is exactly the dependency that made the latch go inert.
- **The bound sits at the single point that decides whether the loop turns.** Every with-concerns re-critique goes through `_critique_verdict_is_stale` → row 2b. There is no second path to bound.
- **The #1760 tests become a real proof instead of a vacuous one.** Round 2 was right that "task 2 forbids modifying the function, so its tests are green by construction" proved nothing. Now the function *is* modified, and the split is falsifiable: every test in `TestConvergenceLatchRevisionAppliedAt` (`tests/unit/test_sdlc_router_decision.py:771-901`) uses a bare `"READY TO BUILD"` verdict and must stay green **unchanged**, while `test_1760_inverse_guarantee_preserved` (`tests/unit/test_sdlc_router_decision.py:1362`) uses `"READY TO BUILD (with concerns)"`, asserts `_critique_verdict_is_stale(...) is False` and a `/do-build` dispatch, and **must flip**. That single test is the demonstrated-red for the whole change.

**3. The bound: a monotonic counter owned by the verdict recorder**

**The bound is NOT derived from `_sdlc_dispatches`.** That list is FIFO-evicted:
`MAX_DISPATCH_HISTORY = 10` (`agent/sdlc_router.py:98`) with the eviction at
`agent/sdlc_router.py:1990-1991`. A count over it *shrinks* as entries age out,
which is the re-arming shape that made #1925/#1968 recur. Dispatch records also
carry no row id, so critique dispatches from rows 2/2b/2c, G1 and G5 are
indistinguishable from with-concerns rounds. Both defects are fatal to a
dispatch-derived bound.

**The counter: `stage_states["_concern_round_count"]`, a plain integer.**
`tools/sdlc_verdict.py::record_verdict` is the sole writer of `_verdicts`
(`_apply` at `tools/sdlc_verdict.py:475-482`). Inside that same `_apply`, when
`stage == "CRITIQUE"` and the normalized verdict contains `WITH CONCERNS`,
increment it. The counter and the verdict it counts land in one
`update_stage_states` write.

**No dedupe. Every recorded with-concerns CRITIQUE verdict counts.** Round 2's
concern 5 correctly demolished the previous `artifact_hash` dedupe — a replay and
a genuine round on unchanged bytes are byte-identical inputs — and prescribed a
compound `(artifact_hash, revision_applied_at)` key. That is better, and it is
still not sufficient, because it can *also* fail to advance: if `/do-plan` runs
without rewriting `revision_applied_at` (Risk 3's degraded-field case), both
components are unchanged and the round does not count. A bound that can silently
fail to advance is unacceptable here, because of a finding round 2 did not have:

> **G4 cannot bound this loop.** `compute_same_stage_count`
> (`agent/sdlc_router.py:2003-2056`) counts *consecutive same-skill* dispatches
> and `break`s the streak the moment the skill changes. The loop alternates
> `/do-plan` and `/do-plan-critique`, so the streak resets every single turn and
> `same_stage_dispatch_count` never reaches G4's threshold of 3. **The bound is
> the only terminator this loop has.**

Given that, the fail directions are not close: under-counting costs an unbounded
PLAN↔CRITIQUE loop with no backstop — the failure this repo has already shipped
twice — while over-counting costs a build with a *recorded* acceptance one round
early. So the counter takes the safe error and counts unconditionally. A duplicate
`sdlc-tool verdict record` for the same round consumes one of three slots; that is
the price, it is stated in the code comment, and it is cheap next to the
alternative.

Properties:

| Property | Why it holds |
|---|---|
| **Durable** | `_concern_round_count` is an underscore-prefixed key that is *not* in `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`), so `PipelineStateMachine._save()` reloads and merges it back on every write. Nothing truncates it. |
| **Monotonic** | Increment-only. No code path decrements or resets it. |
| **Loop-scoped** | Only a `WITH CONCERNS` CRITIQUE verdict increments it. NEEDS REVISION and MAJOR REWORK rounds never consume the bound. |
| **Unbypassable by the loop** | Row 2b's with-concerns branch, rows 4b/4c and G5's step-aside all require a with-concerns verdict in `_verdicts["CRITIQUE"]`, and only `record_verdict` writes that. A round that skips the counter skips the routing too. |
| **Retry-safe** | `update_stage_states` re-invokes `_apply` against a freshly reloaded snapshot on each optimistic retry (`tools/stage_states_helpers.py:209-215`), so the read-modify-write is already correct under contention. |
| **G4-inert** | `build_stage_snapshot` uses an explicit allow-list, `_SNAPSHOT_PROJECTION_KEYS` (`agent/sdlc_router.py:193-201`). The new key is not in it, so the oscillation snapshot is byte-identical to today's. |

**The counter must be plumbed into `_meta`, or it is structurally inert.** This is
the second finding round 2 did not have, and it would have shipped a dead feature:

> `sdlc-tool next-skill` does not read raw `stage_states`. It reads
> `enriched["stages"]` and `enriched["_meta"]` (`tools/sdlc_next_skill.py:629-630`)
> from `stage-query`, and `stage-query` projects into `stages` **only** the
> keys in `ALL_STAGES` plus a two-element allow-list —
> `for _router_key in ("_verdicts", "_sdlc_dispatches")`
> (`tools/sdlc_stage_query.py:766-768`). A new `_*` key is dropped on the floor.
> The comment on that very loop documents this exact class of bug: without the
> threading, "those rules are structurally inert in the CLI path".

The counter therefore rides the `_meta` channel instead, following the working
precedent one field over: `_compute_meta` already lifts `_critique_cycle_count`
out of raw states into `_meta["critique_cycle_count"]`
(`tools/sdlc_stage_query.py:597-620`). Add
`"concern_round_count": int(raw_states.get("_concern_round_count", 0) or 0)` there
**and** to `_default_meta` (`tools/sdlc_stage_query.py:621-648`) — the two must
stay key-for-key by the #2769 rule stated in `_default_meta`'s own comment. The
router helper is then `concern_round_count(meta)`, reading `meta`, not
`stage_states`.

**The constant.** `MAX_CONCERN_RECRITIQUE_ROUNDS` in `agent/pipeline_graph.py`
alongside `MAX_CRITIQUE_CYCLES`, default **3**, read via `os.getenv` with the
provisional/tunable comment the repo requires for magic numbers.

Note for the builder: `MAX_CRITIQUE_CYCLES` (`agent/pipeline_graph.py:35`) is a
**bare literal**, and neither `agent/pipeline_graph.py` nor `agent/sdlc_router.py`
currently contains any `os.getenv`/`os.environ` reference (re-verified at this
baseline: zero matches in both). This constant introduces the first env read in
that module and needs the `os` import; do not describe it as "like
`MAX_CRITIQUE_CYCLES`, read with a default", because that is false.

Why 3 and not 4: the `verdict-finalize-cluster` round-4 datapoint counted *mixed*
rounds on a lane-total basis and does not transfer to a loop-scoped counter, so
there is no empirical basis for 4. With the scoped pass deferred (§6) every
re-critique round is full depth, so each round is expensive; 3 bounds the cost
while still admitting two rounds of genuine concern-closing after the first
verdict. Revisit after five with-concerns lanes have run.

**Explicitly not G2.** `guard_g2_critique_cycle_cap` reads `critique_cycle_count`,
which `agent/pipeline_state.py:1074` increments only inside `fail_stage("CRITIQUE")`.
A with-concerns verdict marks CRITIQUE `completed`, so that counter is `0` on this
path forever. Extending G2 would require making a *passing* verdict increment a
*failure* counter, corrupting G2's meaning for the NEEDS REVISION path it correctly
bounds. Composing with G2 here means **staying out of its way**, not reusing it.

**4. Rows 4b and 4c**

| Row | Predicate (both also require: with-concerns verdict, no `pr_number`, `BUILD` not `completed`, `BUILD` in `{None, pending, ready}`) | Skill |
|---|---|---|
| `4b` | `not _concern_revision_is_unjudged(...)` — state S1 | `/do-plan` — revise, embed Implementation Notes |
| `4c` | `_concern_revision_is_unjudged(...)` **and** `concern_round_count(meta) >= MAX_CONCERN_RECRITIQUE_ROUNDS` — state S2, bound spent | `/do-build` — residual concerns accepted |

Row 4b's predicate change is not cosmetic: it is the fix for the sticky boolean on
its own axis. Today, at round 2, `revision_applied` is already `true`, so 4b misses
and 4c fires straight to build. Event-scoped, 4b correctly fires on every round's
fresh with-concerns verdict.

Row 4c keeps its skill and its `row_id`; it gains the two conditions that make it
the *bound-exhausted* build edge rather than the unconditional one. Below the
bound in state S2 it does not fire at all — row 2b, evaluated earlier in
`DISPATCH_RULES`, owns that state.

Rows 4b and 4c are mutually exclusive by construction (`not P` / `P and Q`), and
row 2b covers `P and not Q`. Ordering is on this plan's side rather than against
it: `evaluate` walks `DISPATCH_RULES` in order and breaks on the first match
(`agent/sdlc_router.py:1750-1758`), and row 2b is registered at
`agent/sdlc_router.py:1563`, ahead of 4b/4c. **A test must pin that order**, because
the design depends on it.

**4.5. G5 must step aside on every with-concerns verdict — co-load-bearing**

Guards run to completion *before* the dispatch table is consulted:
`evaluate_guards` (`agent/sdlc_router.py:783`) walks `GUARDS`
(`agent/sdlc_router.py:771-780`) and returns the first non-`None` decision. So a
guard that dispatches `/do-build` makes rows 2b/4b/4c unreachable no matter how
they are written. `guard_g5_artifact_hash_cache` (`agent/sdlc_router.py:524`) is
exactly such a guard: its cached-verdict branch tests
`if CRITIQUE_READY_TO_BUILD in verdict_text` (`agent/sdlc_router.py:598`) with **no
`WITH CONCERNS` exclusion**, and `"READY TO BUILD (WITH CONCERNS)"` contains
`"READY TO BUILD"`.

**One edit, unconditional.** Inside that branch, after the D3 block at
`agent/sdlc_router.py:601-603` and before the #1871 short-circuit at
`agent/sdlc_router.py:615`:

```python
if "WITH CONCERNS" in verdict_text:
    return None
```

Round 2's blocker 1 was right that the previous revision's version of this edit
did not work: both of its step-asides were gated on
`_concern_revision_is_unjudged`, which is **`False`** in state S1 — the state
immediately after a with-concerns verdict is recorded, where `recorded_at`
postdates `revision_applied_at` — so G5 still returned
`Dispatch(SKILL_DO_BUILD, ..., row_id="G5")` in exactly the state the blocker
described. An unconditional test has no such hole. G5 never needs to serve a
with-concerns verdict: rows 2b/4b/4c cover every with-concerns state including the
bound-exhausted one.

**`agent/sdlc_router.py:615` is not touched.** The previous revision proposed
re-keying it, which would have deleted the documented #1871 short-circuit; that
line's own comment explains that without it, G7's gate-6 fallthrough state "would
let G5's cached READY-TO-BUILD verdict ship the pre-revision design via
/do-build". The new unconditional test sits *ahead* of it and is strictly
stronger for with-concerns verdicts, while `:615` keeps owning the no-concerns
case unchanged.

G5's NEEDS REVISION branch (`agent/sdlc_router.py:584-597`) is untouched — it
routes to `/do-plan`, which is correct under any revision state. `GUARDS` ordering
is untouched.

**A consequence to state honestly:** G5's docstring and
`.claude/skills/sdlc/SKILL.md:182` both advertise G5 as row 2b's loop bound ("never
re-dispatch `/do-plan-critique` on an unchanged plan"). For with-concerns verdicts
that bound is now `MAX_CONCERN_RECRITIQUE_ROUNDS` instead. Both texts are doc
targets below, and the substitution is sound: the counter advances on every
recorded round regardless of whether the plan bytes changed, so an unchanged-plan
re-critique still consumes a slot and still terminates.

**Mandatory alive test, in BOTH revision states.** Round 2 caught that the previous
revision specified only one of them, which is why the defect survived its own
mandatory test. Construct a ledger where the current plan hash equals
`_verdicts["CRITIQUE"]["artifact_hash"]` and the verdict is
`READY TO BUILD (WITH CONCERNS)`, then assert the full `evaluate(...)` result:

- (a) `revision_applied_at` **older** than `recorded_at` (state S1), `plan_revising` **unset** → row `4b`, and explicitly **not** `row_id="G5"`.
- (a2) same as (a) but with `plan_revising=True` and `last_dispatched_skill="/do-plan-critique"` — the ledger Step 5.6 actually produces — → `skill == SKILL_DO_PLAN` with `row_id in {"4b", "G7"}`, and explicitly **not** `row_id="G5"`.
- (a3) same as (a) plus a `/do-plan` dispatch whose `at` postdates `recorded_at` (row 4b fired, `/do-plan` crashed before writing `revision_applied_at`) → row `4b`, and explicitly **not** `2b` and **not** `G5`.
- (b) `revision_applied_at` **newer** than `recorded_at` (state S2), count below the bound → row `2b`, and explicitly **not** `row_id="G5"`.
- (c) same as (b) with the count at the bound → row `4c`, and explicitly **not** `row_id="G5"`.

Without all five the rest of the plan can pass green while shipping nothing.

**(a2) exists because G7 legitimately owns S1's first turn.** Round 3's concern 1
— raised independently by both war rooms — is that `guard_g7_plan_revising` gate 4
(`agent/sdlc_router.py:690-699`) dispatches `/do-plan` with `row_id="G7"` whenever
the lock is set, gate 3 does not self-heal, and `last_dispatched_skill ==
"/do-plan-critique"`. Guards run to completion before the dispatch table
(`evaluate_guards`, `agent/sdlc_router.py:783`). Once §8's repairs land — Step 5.6
setting the lock on every with-concerns verdict, and gate 3 keyed on
`_concern_revision_is_unjudged`, which is `False` in S1 — that is exactly the
state right after a with-concerns critique. The dispatched **skill** is the same
(`/do-plan`), so nothing about routing or build safety breaks; the defect was
purely in asserting a row id that production does not produce. Assert the skill
for the locked case and the row id for the unlocked one. Do **not** "fix" this by
reordering `GUARDS` or weakening gate 4.

**(a3) is round 3's concern 2 as a test.** It is the state a fallthrough-based
below-bound arm would misroute to `2b`; with §2's predicate-keyed arm it must
resolve to `4b`.

**5. Cap-reached behavior: accept, do not escalate**

At the bound, row 4c dispatches `/do-build` rather than returning `Blocked`. A
`Blocked` strands the lane waiting on a human who is usually not watching, and the
concerns are by definition non-blocking — that is what CONCERN means. The
accountability comes from the record, not the halt.

**The delivery channel is `stage-query`, not `row_id`.** Round 1 caught that
`row_id` exists only in the router's own output (`tools/sdlc_next_skill.py:663`,
`.claude/skills/sdlc/SKILL.md:246`), is consumed by the supervisor, and is never
plumbed into the dispatched skill's invocation. A note that `/do-build` cannot
know to write is no accountability at all. So `/do-build` re-derives row 4c's own
condition from the same authoritative source the router used:

```
sdlc-tool stage-query --issue-number N
```

and writes the note when **all three** hold: (a) the latest CRITIQUE verdict
contains `WITH CONCERNS`, (b) `revision_applied_at` postdates that verdict's
`recorded_at`, (c) `concern_round_count >= MAX_CONCERN_RECRITIQUE_ROUNDS`. That is
row 4c's predicate, evaluated from state rather than passed as an argument, so the
note lands whenever row 4c was the reason `/do-build` is running — and cannot land
spuriously on a clean-verdict build, because (a) fails.

**Where each half of that instruction lives** (round 2's concern 4). `/do-build` is
a **global** skill (`.claude/skills-global/do-build/SKILL.md`, hardlinked to every
machine), so the concrete repo-only invocation must not go in its body. Per the
skill-context convention:

- **Global body** gets the generic obligation only: "If the pipeline's state substrate records that this build was reached by exhausting a critique bound, record the accepted residual concerns in the plan before building."
- **`docs/sdlc/do-build.md`** gets the concrete derivation. That file already carries `sdlc-tool stage-query` invocations (`docs/sdlc/do-build.md:93`), so this is the established home, not a new precedent.

**Name the nesting explicitly, or the builder will not find the fields.**
`stage-query` emits `{"stages": {...}, "_meta": {...}}` (`tools/sdlc_stage_query.py:771`).
`_verdicts` — and therefore `recorded_at` — lives under **`stages`**, threaded there
by `tools/sdlc_stage_query.py:766-768`. `revision_applied_at` and
`concern_round_count` live under **`_meta`**. `_meta["latest_critique_verdict"]` is a
bare verdict string with no timestamp, so (b) is *not* derivable from `_meta` alone;
a builder told only "derive it from stage-query" will look there, fail, and
conclude the derivation is impossible.

- Row 4c's `reason` names the bound, the count, and that residual concerns were accepted unreviewed.
- `/do-build` Step 1 writes an **Accepted Residual Concerns** note into the plan's `## Critique Results` section naming which round's concerns were accepted and why.

This resolves D2 in favour of recording over halting, and the resolution is only
defensible *because* the note has a working delivery channel. If the `stage-query`
derivation proves unreliable at build time, the honest fallback is `Blocked` at the
cap, not a silent build — that decision rule is written into the build task.

**6. Re-critique depth: full, every round. Scoping is deferred.**

Every row-2b re-critique runs the **standard FULL-depth** `/do-plan-critique`
pass. No new Step 0.5, no reduced roster, no scoped mode, no skill-side changes to
`.claude/skills-global/do-plan-critique/SKILL.md` beyond the Step 5.6 repair (§8)
and the Outcome Contract row.

Round 1's critique was right that the scoped pass was the largest new surface in
the plan, the only one with no test, and justified by cost (Risk 2) rather than
correctness — the loop is *correct* with full depth every round. Worse, its
escalation trigger ("the revision touches the plan's structure") was prose
judgement handed to an LLM, unassertable, and it would have put a repo-only
`sdlc-tool stage-query` invocation into a global skill body.

**Concretely deferred:** file a follow-up issue for the concerns-scoped pass,
carrying round 1's own suggested mechanical trigger — compute the set of
`^## `/`^### ` headers whose bodies changed in the plan diff between the prior
verdict's commit and HEAD, escalate to FULL when that set intersects
`{Solution, Technical Approach, Step by Step Tasks, Verification}` — which is
assertable over two plan-file fixtures. Creating that issue is a task below.

**7. Nits: assert, do not change**

`/do-plan-critique` already emits `READY TO BUILD (no concerns)` when there are
zero CONCERN or BLOCKER findings; NITs never produce a with-concerns verdict and
therefore cannot enter this loop. No code change. A test pins it so a future
severity-taxonomy edit cannot silently make nits loop-bearing.

**8. Repair Step 5.6 and G7 gate 3**

Both currently key on the sticky `revision_applied`, which is why the
`plan_revising` lock is permanently inert on any once-revised plan.

**Step 5.6 — delete the exemption, do not replace it with a comparison.** Round 2's
concern 6 showed the previously-proposed replacement ("set the lock when the
verdict requires a revision **and** the plan's `revision_applied_at` is not later
than this verdict's timestamp") is vacuous: Step 5.6 runs immediately after Step
5.5 recorded the verdict, so "this verdict's timestamp" is effectively now and
`revision_applied_at` is by definition an earlier `/do-plan` pass — the comparison
can never be false. It also gave the builder nothing implementable, since the skill
has no stated way to read back the `recorded_at` it just wrote. The rule becomes
what it actually is:

> Set the lock whenever the verdict is `NEEDS REVISION`, `MAJOR REWORK`, or
> `READY TO BUILD (with concerns)`. Do NOT set it for
> `READY TO BUILD (no concerns)`.

That is one deleted clause per file, greppable, with **all** event-scoping moved
into `agent/sdlc_router.py` G7 gate 3, where it reads real ledger state and is
unit-testable. **The clause exists in two files** —
`.claude/skills-global/do-plan-critique/SKILL.md:414-421` and
`docs/sdlc/do-plan-critique.md:76-78` ("Do NOT set it for `READY TO BUILD (no
concerns)` or when `revision_applied: true` already"). Fixing only the global body
leaves the defect documented in the addendum the skill actually reads at runtime —
the half-migration this repo forbids.

**G7 gate 3** (`agent/sdlc_router.py:680-682`, currently
`if meta.get("revision_applied"): return None`): self-heal only when
`_concern_revision_is_unjudged(stage_states, meta)` — i.e. when a revision has
landed since the latest CRITIQUE verdict — reused **without** a verdict-kind test,
per §1. Gate 3 must keep self-healing `NEEDS REVISION` and `MAJOR REWORK` locks;
that is precisely why the predicate is verdict-kind-agnostic.

**One behavior change to test explicitly.** With no `_verdicts["CRITIQUE"]` record
at all, the predicate returns `False`, so gate 3 no longer heals a lock that the
sticky boolean would have released. G7 then falls through to its own deadlock
backstop and escalates to `Blocked` after `MAX_PLAN_REVISING_DISPATCHES`, with the
documented manual recovery (`sdlc-tool meta-set --key plan_revising --value false`).
That is the backstop working as designed rather than a regression, but it is a
reachable state and it gets a test.

This is defence in depth, not the primary fix. §2's latch bound and the rows are
the load-bearing change; a G7 that can actually arm is the backstop for a state
they miss. It is in scope because leaving a documented lock in the tree that
provably cannot arm is exactly the half-migration the repo forbids.

## Failure Path Test Strategy

### Exception Handling Coverage

- [ ] `_critique_verdict_is_stale` (`agent/sdlc_router.py:1129`) wraps its body in a bare `except Exception: return False`. The new with-concerns branch lives inside that `try`, so a raise anywhere in it degrades to "not stale". Test the observable consequence: with a malformed `recorded_at` on a with-concerns verdict, the dispatch is row `4b` (`/do-plan`), never `/do-build`.
- [ ] `_concern_revision_is_unjudged` mirrors the same shape. A malformed `revision_applied_at` yields `False` → row 4b. Assert the resulting *dispatch*, not just the boolean.
- [ ] `tools/sdlc_next_skill.py::_resolve_enriched`'s broad `except` is the swallower named in the issue's own evidence. Add a test that a router evaluation raising inside the new predicate still produces a determinate dispatch rather than an empty ledger.

### Empty/Invalid Input Handling

- [ ] `_verdicts` absent entirely → predicate returns `False` → row 4b. Test.
- [ ] `_verdicts["CRITIQUE"]` present but `recorded_at` missing or empty string → `False` → row 4b. Test.
- [ ] `meta["revision_applied_at"]` absent, empty, whitespace-only, or non-ISO → `False` → row 4b. Test each.
- [ ] `meta["concern_round_count"]` absent, `None`, or a non-integer → coerces to `0` → the latch stands down and row 2b owns state S2 (correct: no rounds consumed yet, and every pre-existing lane starts here). Test each.
- [ ] Timestamps exactly equal → `False` (strict `>`), matching the existing `_critique_verdict_is_stale` convention. Test.

### The #1760 split (blocker 3)

- [ ] **The flip.** `test_1760_inverse_guarantee_preserved` (`tests/unit/test_sdlc_router_decision.py:1362`) currently asserts `_critique_verdict_is_stale(...) is False` and `/do-build` on a `READY TO BUILD (with concerns)` verdict with a settled revision. Below the bound it must now assert `True` and `/do-plan-critique`. **Capture its failure output before the change as the demonstrated-red paper trail**, and rewrite the test's docstring to state the new contract rather than editing the assertion in place with the old prose.
- [ ] **The other half stays green, unchanged.** Every test in `TestConvergenceLatchRevisionAppliedAt` (`tests/unit/test_sdlc_router_decision.py:771-901`) uses a bare `"READY TO BUILD"` verdict. If any goes red, the with-concerns branch has leaked into the no-concerns path — treat it as a blocker, not a test to update.
- [ ] **The end-to-end latch test stays green, unchanged.** `tests/unit/test_sdlc_stage_query.py:880-921` round-trips the documented `revision_applied_at` writer format through `_critique_verdict_is_stale` on a bare `"READY TO BUILD"` verdict.
- [ ] **No-concerns regression.** `READY TO BUILD` (no concerns) with `revision_applied_at` postdating `recorded_at` → dispatch is row `4a`, never `2b` and never `4c`. This is the test round 2 asked for: one that can actually fail if the change leaks.
- [ ] **NEEDS REVISION regression.** The `TestNeedsRevisionInvalidatedByRevision` cases (`tests/unit/test_sdlc_router_decision.py:1270-1361`) stay green unchanged — the `requires_revision` path is not touched.

### Bound durability and scoping

- [ ] **The bound is the only terminator.** Assert directly that `compute_same_stage_count` returns a streak of at most 1 over an alternating `/do-plan` / `/do-plan-critique` history with an unchanged snapshot, so G4 demonstrably cannot fire on this loop. This test exists to stop a future reader from re-deleting the bound on the belief that G4 backstops it.
- [ ] **Durability under truncation pressure.** Append 15+ dispatches to `_sdlc_dispatches` (past `MAX_DISPATCH_HISTORY = 10`) interleaved with with-concerns verdict records, then assert `_concern_round_count` still reflects every round.
- [ ] **Durability across `_save()`.** Record a with-concerns verdict, then drive a `PipelineStateMachine` write (a stage transition), then assert `_concern_round_count` survived the `_save()` merge. The key is not in `_OWNED_METADATA_KEYS`, and this pins that the unowned-key merge covers it.
- [ ] **The `_meta` plumb.** `stage-query` on a ledger with `_concern_round_count` set must return it as `_meta["concern_round_count"]`, and `_default_meta()` must carry the same key (the #2769 key-parity rule). **Without this the feature is inert through the CLI**, which is the only path the pipeline actually uses.
- [ ] **NEEDS REVISION rounds do not consume the bound.** Record three `NEEDS REVISION` CRITIQUE verdicts, then one `READY TO BUILD (WITH CONCERNS)`; assert the count is `1` and that state S2 dispatches row 2b, not 4c.
- [ ] **Every recorded round counts.** Two with-concerns records on identical plan bytes and identical `revision_applied_at` → count `2`, not `1`. This pins the deliberate no-dedupe decision (§3) against a future "optimization".
- [ ] **G4 snapshot is unchanged.** Assert `build_stage_snapshot` output is byte-identical with and without `_concern_round_count` present.

### G5 bypass

- [ ] **The alive test, all three states** (§4.5): hash-match + with-concerns with (a) `revision_applied_at` older than `recorded_at` → row `4b`; (b) newer, count below bound → row `2b`; (c) newer, count at bound → row `4c`. Each asserts `row_id != "G5"`. Capture all three as demonstrated-red before the G5 edit lands.
- [ ] **G5 still fires on a no-concerns cache hit.** Hash-match with `READY TO BUILD` and no concerns → `row_id="G5"`, `/do-build`.
- [ ] **G5's NEEDS REVISION branch is unaffected.** `TestCritiqueStaleG5LoopBound` (`tests/unit/test_sdlc_router_decision.py:995-1034`) stays green unchanged.
- [ ] **`agent/sdlc_router.py:615` is byte-for-byte unmodified.** Assert by diff in the validator task; the #1871 short-circuit is an existing protection this plan must not delete.

### Ordering

- [ ] **Row 2b precedes rows 4b/4c.** Assert on `[r.row_id for r in DISPATCH_RULES]` that `2b` appears before `4b` and `4c`. The design depends on first-match ordering (`agent/sdlc_router.py:1750-1758`); a future reordering must break a test, not production.

### Error State Rendering

- [ ] Row 4c's `reason` string must name the bound, the count, and that residual concerns were accepted unreviewed. Assert on the string, not just the skill. A silent build at the cap is the failure mode this whole plan exists to prevent, one level up.
- [ ] `/do-build`'s Accepted Residual Concerns note must land in the plan document and be visible in the PR. Test that the note is written when the three `stage-query`-derived conditions hold and not written otherwise.

## Test Impact

- [ ] `tests/unit/test_sdlc_router_decision.py:1362` `test_1760_inverse_guarantee_preserved` — REPLACE: this is the single test that encodes the behaviour being superseded. Below the bound, a with-concerns settle revision is now stale and routes to `/do-plan-critique`. Rewrite the docstring to the new contract; add a sibling asserting the *at-the-bound* case still routes to `/do-build`.
- [ ] `tests/unit/test_sdlc_router_decision.py:771-901` `TestConvergenceLatchRevisionAppliedAt` — NO CHANGE, and a red here is a blocker. All bare `"READY TO BUILD"`.
- [ ] `tests/unit/test_sdlc_router_decision.py:1270-1361` `TestNeedsRevisionInvalidatedByRevision` (minus the one test above) — NO CHANGE.
- [ ] `tests/unit/test_sdlc_router_decision.py:124-191` `TestRow4bReadyWithConcernsNoRevision` and `TestRow4cReadyWithConcernsRevisionApplied` — UPDATE: both classes key their fixtures on `meta["revision_applied"]`. They must be rebuilt on `revision_applied_at` vs `recorded_at`, and 4c's cases must add a bound-exhausted `concern_round_count`. Each flip is demonstrated-red proof.
- [ ] `tests/unit/test_sdlc_router_decision.py:192-238` `TestD3FinishedPrNeverRoutesBackToBuild` — VERIFY at build time: the D3 `pr_number` / `BUILD == completed` step-asides must survive the 4b/4c rewrite unchanged.
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: every case asserting a with-concerns verdict with `revision_applied: true` reaches `/do-build`; G7 gate-3 cases keyed on the sticky boolean (a case asserting "lock self-heals when `revision_applied` is true" is asserting the defect); any G5 case asserting `row_id="G5"` → `/do-build` on a cached **with-concerns** verdict.
- [ ] `tests/unit/test_sdlc_router_decision.py:995-1034` `TestCritiqueStaleG5LoopBound` — NO CHANGE (NEEDS REVISION fixtures), but add a with-concerns sibling asserting the bound, not G5, is what terminates that loop now.
- [ ] `tests/unit/test_sdlc_verdict.py` — ADD: `record_verdict` increments `_concern_round_count` on a with-concerns CRITIQUE verdict and on nothing else. Existing assertions on the returned record shape stay green — the counter lives in `stage_states`, not in the returned record.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE/ADD: any test asserting the exact `_meta` key set must gain `concern_round_count`; add the `_default_meta`/`_compute_meta` key-parity case (#2769).
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — NO CHANGE expected: no new `DispatchRule` row is registered, so the hard-coded `expected` set at `:143-163` still matches. Re-run it to confirm; if it goes red, a row was added that this plan did not sanction.
- [ ] `tests/unit/test_sdlc_takeover_regression.py` — VERIFY at build time whether it asserts a with-concerns → build transition; update or leave.
- [ ] `tests/unit/test_architectural_constraints.py` — NO CHANGE expected, but the new predicate must not import from `tools/`; this test is the guard and must stay green.


## Rabbit Holes

- **Rewriting the severity taxonomy.** The issue's own Out of Scope. BLOCKER/CONCERN/NIT is not the problem; what happens after a CONCERN is. Do not touch the classifier.
- **Making the whole thing a state machine.** There will be a pull to unify rows 2b/3/4a/4b/4c and the latch into a single verdict-kind × event-ordering matrix. It is probably right eventually and it is not this plan. Predicates that each fail safe beat one matrix nobody can review.
- **Deleting the `plan_revising` lock.** Tempting — the latch bound and the rows do the real work and the lock is currently inert. But G7 is referenced in `docs/sdlc/plan-revising-lock.md`, `.claude/skills/sdlc/SKILL.md`, `/do-plan` Phase 4 step 2b, and `/do-plan-critique` Step 5.6. Repair it here; propose removal separately with the full blast radius in hand.
- **Building a general "was artifact X reviewed after change Y?" abstraction.** G5's artifact-hash cache, G8's artifact verification, row 2b's staleness, and this predicate are four instances of one idea. Generalizing them is a real refactor with a real risk of re-introducing #1925. Not now.
- **Making the scoped re-critique cheap by cutting critics.** Moot in this plan — the scoped pass is deferred to a follow-up issue (§6) and every round runs at full depth. Carried forward as guidance for that issue: the saving must come from narrowing *what is judged* (prior concerns + revision diff), not from thinning the roster. A two-critic war room that misses the round-2 defect costs more than it saves.
- **Exempting `/do-plan-critique` entries from `_sdlc_dispatches` eviction.** Round 1 raised this as one way to make a dispatch-derived bound durable. Rejected: `MAX_DISPATCH_HISTORY` exists to bound memory, and selective retention breaks `compute_same_stage_count`'s contiguous-recency assumption (`agent/sdlc_router.py:1997+`), which would need its own G4 regression suite. The `_concern_round_count` integer gets the same durability without touching G4's substrate at all.
- **Plumbing `row_id` into dispatched skill invocations.** A real and probably-good idea — `/do-build` and others could act on *why* they were dispatched. It is a separate blast radius (`tools/sdlc_next_skill.py`, the `/sdlc` router contract, every skill's Step 1) and this plan gets what it needs by re-deriving row 4c's predicate from `stage-query`. Do not let it in through the side door.

## Risks

### Risk 1: Reopening the #1760 / #1925 / #1968 non-convergence loop

**Impact:** The exact failure this repo has already shipped twice — PLAN↔CRITIQUE
ping-pong that never reaches BUILD, burning a lane and a human's attention. This is
the single most important risk in the plan, and this revision states it without
hedging: **the plan deliberately re-opens the loop #1760 suppressed.**

Round 1 and round 2 of this plan both claimed the new work was *disjoint* from
#1760. That was factually inverted. `_critique_verdict_is_stale` engages the latch
only when `revision_applied_at and not requires_revision`
(`agent/sdlc_router.py:1118`), and #2049 narrowed `requires_revision` to NEEDS
REVISION / MAJOR REWORK — so the `READY TO BUILD (with concerns)` settle path is
the latch's *live domain*, stated verbatim in its own docstring
(`agent/sdlc_router.py:1100-1103`). There is no disjointness to claim. The correct
framing is supersession: **#1760 had no bound available, so suppression was the
only fix it could make. This plan supplies the bound and lifts the suppression, on
the with-concerns path only.**

**Mitigation, in four layers:**

1. **The bound is unconditional at the point of suppression.** The bound-exhausted `return False` in `_critique_verdict_is_stale` does not read `revision_applied_at` and does not compare timestamps. It is independent of dispatch history **only because §2 places the with-concerns branch ahead of the `if not latest_plan_at: return False` early return** — round 3's concern 3 caught the previous revision claiming that independence while the early return still gated it, and the fix is placement, not prose. Below the bound the branch reads `_concern_revision_is_unjudged`, which is itself dispatch-history-free (it compares `_verdicts["CRITIQUE"]["recorded_at"]` against `meta["revision_applied_at"]`). Once `concern_round_count >= MAX_CONCERN_RECRITIQUE_ROUNDS`, a with-concerns verdict is never stale again — which is exactly #1760's post-fix behaviour, restored permanently at the cap. The loop cannot outlive the bound by finding a state in which the bound's own precondition is missing; that class of hole is what round 2's blocker 2 identified in the previous design.
2. **No re-arming.** #1925/#1968 recurred because `/do-plan` rewrites `revision_applied_at` every pass, re-arming the latch each round. The counter is not a latch and cannot be re-armed: `_concern_round_count` only increments, and it increments on *every* recorded with-concerns verdict with no dedupe (§3), so every round strictly tightens the bound. This is the property the dispatch-count bound did *not* have — `_sdlc_dispatches` FIFO-evicts at `MAX_DISPATCH_HISTORY = 10` (`agent/sdlc_router.py:98,1990-1991`), so its count shrinks over a long lane, which is the #1925 re-arming shape wearing a different hat. State this asymmetry in the code comment on the constant *and* on the counter.
3. **Terminal by construction.** At the bound, row 2b steps aside and row 4c dispatches `/do-build`, not `Blocked`. Even a pathological oscillation terminates in a build with a recorded acceptance rather than a stall.
4. **G5 cannot short-circuit the loop.** §4.5's unconditional step-aside is what keeps rows 2b/4b/4c on the live path at all; without it the loop is not "risky", it is absent.

**The proof is now falsifiable.** Round 2 correctly called the previous "the #1760
latch tests stay green unchanged" argument vacuous — the plan forbade modifying the
function, so its tests were green by construction. The function is now modified, and
the split is testable in both directions: the with-concerns latch test
(`tests/unit/test_sdlc_router_decision.py:1362`) **must flip**, and every
no-concerns latch test (`tests/unit/test_sdlc_router_decision.py:771-901`,
`tests/unit/test_sdlc_stage_query.py:880-921`) **must stay green unchanged**. A red
in the second group means the change leaked, and is a blocker rather than a test to
update.

**And the safety net people assume is there, is not.** `compute_same_stage_count`
(`agent/sdlc_router.py:2003-2056`) counts *consecutive same-skill* dispatches and
breaks the streak on any skill change, so G4 cannot fire on a `/do-plan` ↔
`/do-plan-critique` alternation. The bound is this loop's only terminator, which is
why §3 refuses any dedupe that could stop it advancing, and why a test asserts G4's
inertness directly so a future reader cannot delete the bound believing G4 catches it.

### Risk 2: The re-critique is expensive enough that lanes get slower than the defect is costly

**Impact:** Every with-concerns plan pays extra war-room rounds at full depth, since the scoped pass is deferred (§6). A lane that hits the bound pays 3 war rooms.

**Mitigation:** the bound is the only mitigation, and it is now the *whole* mitigation — that is why the default dropped from 4 to 3. `MAX_CONCERN_RECRITIQUE_ROUNDS` is genuinely env-overridable (`os.getenv` in `agent/pipeline_graph.py`), so a lane-level or machine-level override needs no code change. If cost proves to be the binding constraint in practice, the follow-up issue from §6 ships the concerns-scoped pass with a mechanically-assertable escalation trigger; the honest sequencing is correctness first, then cost.

### Risk 3: The `revision_applied_at` field is unreliable on live ledgers

**Impact:** If `/do-plan` skipped writing it (crash between the commit and the meta write, or a pre-#1760 lane), the predicate returns `False` and the plan routes to `/do-plan` for a revision it already did — a wasted round, possibly a repeated one.

**Mitigation:** the fail-safe direction is a wasted `/do-plan`, never an unreviewed build, which is the correct way to be wrong for this feature. A *repeated* `/do-plan` is a same-skill streak, so G4's `same_stage_dispatch_count` cap does bound this particular degeneration and escalates — unlike the alternating loop in Risk 1, which it cannot see. `/do-plan` Phase 4 step 2a already mandates writing `revision_applied_at` in the *same step* as `revision_applied: true`, never as a follow-up edit — that ordering requirement exists precisely to make this field trustworthy, and a build task re-verifies it.

### Risk 4: This change ships through the very pipeline it modifies

**Impact:** The plan will itself receive a with-concerns verdict, and the fix will be routing its own critique rounds once merged. A defect in the latch branch or row 4c can strand this lane — and this plan is already at critique round 3, so it would enter the new loop at or near the bound.

**Mitigation:** the change is confined to predicate bodies and two rule predicates, all pure functions over dicts, fully unit-testable with no live ledger. The demonstrated-red requirements (the #1760 with-concerns latch test flipping; the three G5 alive states) prove the new routing binds before merge. Post-merge, a stuck lane is recoverable two ways, both documented in the feature doc: `sdlc-tool meta-set` on `revision_applied_at`, or an env override of `MAX_CONCERN_RECRITIQUE_ROUNDS` to `0`, which restores exactly today's behaviour by keeping the latch permanently engaged. That override is the feature's kill switch and is a build task.

## Race Conditions

### Race 1: `/do-plan` writes `revision_applied_at` and the router reads it mid-write

**Location:** `agent/sdlc_router.py` (new predicate) vs. `/do-plan` Phase 4 step 2a → `sdlc-tool meta-set`
**Trigger:** the router evaluates rows between the plan-doc commit and the `meta-set` landing.
**Data prerequisite:** `meta["revision_applied_at"]` must be present before the router can see the revision as judged-pending.
**State prerequisite:** the ledger's `_meta` and the plan file agree.
**Mitigation:** `_concern_revision_is_unjudged` resolves to `False`, and because §2's below-bound arm returns that predicate rather than falling through to the dispatch-time comparison, the verdict is **not** stale — so row 2b (registered ahead of 4b) misses and row 4b re-fires `/do-plan`, which is idempotent (it re-writes the same frontmatter and re-runs `meta-set`). Costs at most one wasted `/do-plan`, which is a same-skill streak and therefore genuinely bounded by G4. **This is the corrected form of round 3's concern 2:** under a fallthrough the same window instead routed to row 2b — a full war room on an unrevised plan, consuming a `_concern_round_count` slot, with G4 unable to see the alternation. Additionally, the SDLC issue lock (`ISSUE_LOCKED`, keyed by `run_id`) serializes all state-mutating calls for an issue to one live run, so there is no concurrent-writer variant of this race.

### Race 2: Two concurrent lanes on the same issue

**Location:** `_concern_round_count` (the loop bound's source)
**Trigger:** two runs record a with-concerns CRITIQUE verdict for one issue, inflating the count and prematurely exhausting the bound.
**Data prerequisite:** a single owner of the verdict record.
**State prerequisite:** the issue lock is held.
**Mitigation:** the SDLC issue lock (`ISSUE_LOCKED`, keyed by `run_id`) serializes state-mutating `sdlc-tool` calls to one live run, so there is no concurrent-writer variant. If two writes did land, the counter over-counts by one, which fails toward a build with a *recorded* acceptance rather than toward an unbounded loop — the direction §3 chose deliberately. `update_stage_states`' optimistic-retry loop (`tools/stage_states_helpers.py:209-215`) re-runs `_apply` against a freshly reloaded snapshot, so neither write is lost.

### Race 3: `record_verdict` increments the round before `/do-plan` writes `revision_applied_at`

**Location:** `tools/sdlc_verdict.py::record_verdict` `_apply` vs. `/do-plan` Phase 4 step 2a
**Trigger:** the counter is incremented at verdict time, but the routing that consumes it depends on a *later* `/do-plan` write. Between them the ledger holds `count = N` with no revision landed (state S1).
**Data prerequisite:** none — the two are read by different predicates.
**State prerequisite:** none.
**Mitigation:** benign by construction. In state S1 `_concern_revision_is_unjudged` is `False`, so row 4c misses and row 4b fires (`/do-plan`) — exactly the intended next step. Row 2b also misses, because no `/do-plan` dispatch postdates the verdict. The count being already incremented shortcuts nothing: at the bound, S1 still routes through 4b first, so the final round's Implementation Notes are embedded before 4c builds.

## No-Gos (Out of Scope)

- Nothing deferred that this plan needs. In scope and shipping together: the latch bound, rows 4b/4c, the counter and its `_meta` plumb, the G5 step-aside, the Step 5.6 repair, the G7 gate-3 repair, the tests, and the docs. Two things are named non-goals rather than deferrals: the `plan_revising` lock *removal* (this plan repairs the lock so it works and takes no position on later deletion), and any new `DispatchRule` row. The concerns-scoped critique pass is a genuine deferral with a filed follow-up issue (§6).

## Update System

No update system changes required — this feature is purely internal. It touches
`agent/sdlc_router.py`, `agent/pipeline_graph.py`, `tools/sdlc_verdict.py`,
`tools/sdlc_stage_query.py`, and skill/doc markdown. The `do-plan-critique` and
`do-build` skills live in `.claude/skills-global/`, which `/update` already
hardlinks to `~/.claude/skills/` via `scripts/update/hardlinks.py`; no new
directory is added, so no `RENAMED_REMOVALS` entry and no registration step.

`MAX_CONCERN_RECRITIQUE_ROUNDS` is read via `os.getenv` with a literal default and
is not a secret, so it needs no `.env` or `.env.example` entry. It is **not**
following an existing precedent in that file: `MAX_CRITIQUE_CYCLES`
(`agent/pipeline_graph.py:35`) is a bare literal, and neither `agent/pipeline_graph.py`
nor `agent/sdlc_router.py` contains any `os.getenv`/`os.environ` reference today
(re-verified at baseline `2dce9812d`: zero matches in both). This constant
introduces the first one and the `os` import that goes with it.

`_concern_round_count` needs **no migration**. It is a stage-states key, not a
Popoto model field, so `scripts/update/migrations.py` is not involved; and an
absent key reads as `0`, which is the correct starting state for every lane that
predates the change. There is no backfill to do and nothing to clean up.

## Agent Integration

No agent integration required — this is internal to the SDLC router and the
critique skill. Both are reached through paths that already exist: the router via
`sdlc-tool next-skill` (already in `pyproject.toml [project.scripts]`), and the
skills via the `Skill` tool. No new CLI entry point, no new MCP surface, no bridge
import.

One integration assertion is **not optional**, because the router unit tests
cannot see it: `sdlc-tool next-skill` must return `/do-plan-critique` on a
with-concerns-plus-settled-revision ledger *end-to-end through the real CLI*. That
path reads `stage-query`'s projection, not raw `stage_states`, and the projection
drops unknown `_*` keys (`tools/sdlc_stage_query.py:766-768`) — so a counter that
is not plumbed into `_meta` produces a feature that passes every unit test and is
inert in production. This is a build task and a Success Criterion.

## Documentation

### Feature Documentation

- [ ] Create `docs/features/with-concerns-recritique-gate.md` describing: the S1/S2 state split and which row owns each; the bounded with-concerns branch in `_critique_verdict_is_stale` and its supersession of #1760; the event-scoped predicate and why it is verdict-kind-agnostic; the unconditional G5 step-aside; the `_concern_round_count` counter, its sole writer, and its `_meta` plumb; that G4 cannot bound this loop so the counter is the only terminator; and the two recovery procedures for a stuck lane (`sdlc-tool meta-set` on `revision_applied_at`, or `MAX_CONCERN_RECRITIQUE_ROUNDS=0` as the kill switch that restores today's behaviour).
- [ ] Add an entry to `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md:473-507` — the "Convergence latch: `revision_applied_at` (#1760)" section currently documents the latch as this path's terminating bound. It is now the *no-concerns* path's bound; the with-concerns path terminates on `MAX_CONCERN_RECRITIQUE_ROUNDS`. Rewrite, do not annotate.
- [ ] Update `docs/features/sdlc-pipeline.md` — the "Convergence latch" section, same substitution, pointing at the new feature doc for the with-concerns path.
- [ ] Update `docs/sdlc/plan-revising-lock.md` — the Set and Clear Contract table and the G7 pseudocode both encode the sticky-`revision_applied` behaviour and are now wrong. Rewrite both against the event-scoped predicate. Describe only the new status quo.
- [ ] Update `.claude/skills/sdlc/SKILL.md` at **four specific locations**. There is **no router row table in this file** and one must not be added (`tests/unit/test_sdlc_skill_md_parity.py:81` fails the build if one is). The edits are: (a) the G7 guards-table row at line 180, which encodes `revision_applied != True`; (b) the G5 guards-table row at line 182, whose "Never re-dispatch `/do-plan-critique` on an unchanged plan" and "`/do-build` (READY TO BUILD)" text is now wrong for with-concerns verdicts; (c) the Convergence latch paragraph at line 200, which states the #2049 verdict-kind gate as the whole story; (d) the hard-coded "dispatch rules (18 rows)" literal at line 223 — **already stale**: `len(DISPATCH_RULES)` is 19 today and this plan adds no row, so the correct value is **19**.
- [ ] Update `.claude/skills-global/do-plan-critique/SKILL.md:414-421` — delete the `revision_applied: true` exemption clause; the rule becomes the plain verdict-kind list (§8). Add an Outcome Contract table row making explicit that `READY TO BUILD (with concerns)` no longer reaches BUILD directly.
- [ ] Update `docs/sdlc/do-plan-critique.md:76-78` — this addendum carries the *same* sticky Step 5.6 rule ("Do NOT set it for `READY TO BUILD (no concerns)` or when `revision_applied: true` already"). Both files or neither; fixing only the global body leaves the defect in the file the skill reads at runtime.
- [ ] Update `.claude/skills-global/do-build/SKILL.md` — the **generic obligation sentence only** (§5). No `sdlc-tool` invocation, no key names, no constant name: this is a global body hardlinked to every machine.
- [ ] Update `docs/sdlc/do-build.md` — the concrete `stage-query` derivation, including the `stages` vs `_meta` nesting, alongside the existing `stage-query` usage at line 93.
- [ ] Register `_concern_round_count` and `_meta["concern_round_count"]` wherever stage-states keys and the `_meta` projection are documented, naming `record_verdict` as the counter's sole writer and `tools/sdlc_stage_query.py::_compute_meta` as the projection.

### Inline Documentation

- [ ] Comment on the with-concerns branch in `_critique_verdict_is_stale` stating that it deliberately re-opens what #1760 suppressed, that the bound is what makes that safe, and that G4 cannot substitute for the bound. Name #1760, #2049 and #2787.
- [ ] Docstring on `_concern_revision_is_unjudged` stating the fail-safe direction and — prominently — that it is **verdict-kind-agnostic by design**, that the `WITH CONCERNS` test is a caller obligation, and that moving that test into the body breaks G7 gate 3's self-heal on NEEDS REVISION locks. This is the comment that stops the obvious future "tidying" refactor.
- [ ] Comment on `MAX_CONCERN_RECRITIQUE_ROUNDS` marking it provisional and tunable, stating that it counts *with-concerns rounds on this lane*, naming the revisit criterion (five with-concerns lanes), noting `0` as the kill switch, and stating the non-re-arming asymmetry from Risk 1 layer 2.
- [ ] Comment on the `_concern_round_count` increment in `record_verdict` explaining the properties it provides (durable / monotonic / loop-scoped), why `_sdlc_dispatches` cannot provide them (`MAX_DISPATCH_HISTORY` eviction), and **why there is no dedupe** — that over-counting costs one round while under-counting costs an unbounded loop G4 cannot see.
- [ ] Comment on the G5 step-aside naming what it protects, why it is unconditional, and why it returns `None` rather than a `Dispatch`.
- [ ] Comment at `tools/sdlc_stage_query.py`'s `_compute_meta` addition noting the #2769 key-parity obligation with `_default_meta`.
- [ ] Docstrings on rows 4b/4c naming their mutual exclusivity, that row 2b owns the state between them, and their D3 step-asides.

## Success Criteria

- [ ] A `READY TO BUILD (with concerns)` verdict cannot reach `/do-build` on the round that produced it, at any round number — verified specifically for rounds 2 and 3, where the sticky boolean currently exempts the plan.
- [ ] **The G5 alive test passes in all five states** (§4.5 a / a2 / a3 / b / c): on a plan-hash cache hit with a with-concerns verdict, `evaluate(...)` returns row `4b` (S1 unlocked), `/do-plan` via `4b` or `G7` (S1 with the Step 5.6 lock set), `4b` (S1 after a `/do-plan` dispatch that never landed a revision), `2b` (S2 below the bound), or `4c` (S2 at the bound) — never `row_id="G5"`. Without this, every other criterion can pass while the feature is dead in production.
- [ ] G5 still returns `row_id="G5"` → `/do-build` on a no-concerns cache hit, and `row_id="G5"` → `/do-plan` on a NEEDS REVISION cache hit.
- [ ] `agent/sdlc_router.py:615` (the #1871 short-circuit) is unmodified.
- [ ] `sdlc-tool next-skill` returns `/do-plan-critique` on a ledger reconstructed from the #2757 state described in the issue's Recon Summary — **end-to-end through the real CLI**, which is the only path that exercises the `_meta` plumb.
- [ ] Rows 4b and 4c are mutually exclusive, row 2b owns the state between them, and `DISPATCH_RULES` order places `2b` before `4b`/`4c` — all asserted by tests, not by inspection.
- [ ] `len(DISPATCH_RULES)` is still 19 and `tests/unit/test_sdlc_skill_md_parity.py` passes with its `expected` set unedited.
- [ ] **The bound survives dispatch-history truncation**: 15+ recorded dispatches (past `MAX_DISPATCH_HISTORY = 10`) do not reduce `concern_round_count`.
- [ ] **The bound is loop-scoped**: three `NEEDS REVISION` rounds followed by one with-concerns verdict yields count `1` and, in state S2, dispatches row 2b — not `4c`.
- [ ] **Every recorded with-concerns round counts**: two records on identical plan bytes and identical `revision_applied_at` yield count `2`.
- [ ] **G4's inertness on this loop is pinned by a test**, so a future reader cannot delete the bound believing G4 backstops it.
- [ ] The loop terminates in at most `MAX_CONCERN_RECRITIQUE_ROUNDS` with-concerns rounds, asserted by simulating a plan that returns with-concerns forever.
- [ ] At the bound, the dispatch is `/do-build` with a `reason` naming the bound and the accepted residual concerns; an Accepted Residual Concerns note lands in the plan's `## Critique Results`, written by `/do-build` from `stage-query`-derived state (not from `row_id`).
- [ ] A `READY TO BUILD (no concerns)` verdict with 3 NITs routes to `/do-build` with zero extra critique rounds.
- [ ] **The #1760 split is demonstrated in both directions**: the with-concerns latch test (`tests/unit/test_sdlc_router_decision.py:1362`) flips with a captured red, and every no-concerns latch test passes **unchanged**.
- [ ] `sdlc-tool meta-set --key plan_revising --value true` on a once-revised plan actually binds G7 (it currently cannot), **and** G7 gate 3 still self-heals a `plan_revising` lock left by a `NEEDS REVISION` round.
- [ ] `MAX_CONCERN_RECRITIQUE_ROUNDS=0` restores exactly today's routing (kill switch), asserted by a test.
- [ ] A follow-up issue exists for the concerns-scoped re-critique pass, carrying the mechanical header-diff escalation trigger.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (router)**
  - Name: `router-builder`
  - Role: The latch bound, the predicate, the constant, rows 4b/4c, the G5 step-aside and the G7 gate-3 repair in `agent/sdlc_router.py` / `agent/pipeline_graph.py`
  - Agent Type: builder
  - Resume: true

- **Builder (state)**
  - Name: `state-builder`
  - Role: The counter in `tools/sdlc_verdict.py` and its `_meta` projection in `tools/sdlc_stage_query.py`
  - Agent Type: builder
  - Resume: true

- **Builder (skills)**
  - Name: `skill-builder`
  - Role: `/do-plan-critique` Step 5.6 and Outcome Contract (both files), `/do-build`'s generic obligation plus the `docs/sdlc/do-build.md` derivation, and the follow-up issue. **No Step 0.5** — the scoped pass is deferred (§6).
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `router-tester`
  - Role: The demonstrated-red captures, the must-pass gate set (task 10a) and the remaining coverage (task 10b)
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `loop-validator`
  - Role: Verify the #1760 split mechanically — which latch tests flipped, which stayed byte-identical — and that `agent/sdlc_router.py:615` was not touched
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `sdlc-documentarian`
  - Role: The eleven documentation targets above
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Re-verify the state substrate

- **Task ID**: verify-substrate
- **Depends On**: none
- **Validates**: read-only
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: true
- Confirm `/do-plan` Phase 4 step 2a writes `revision_applied` and `revision_applied_at` in the same step, and that `tools/sdlc_stage_query.py::_parse_revision_applied_at` reads it into `_meta`.
- Confirm `_concern_round_count` is preserved by `PipelineStateMachine._save()`'s unowned-`_*`-key merge given it is absent from `_OWNED_METADATA_KEYS` (`agent/pipeline_state.py:88`).
- Confirm `build_stage_snapshot`'s allow-list (`_SNAPSHOT_PROJECTION_KEYS`, `agent/sdlc_router.py:193-201`) excludes it, so G4 is unaffected.
- Confirm the `stage-query` projection drops unknown `_*` keys (`tools/sdlc_stage_query.py:766-768`) — this is why the counter rides `_meta`.
- If any is false, STOP and report — the bound's durability depends on all four.

### 2. Add the counter and its `_meta` projection

- **Task ID**: build-counter
- **Depends On**: verify-substrate
- **Validates**: tests/unit/test_sdlc_verdict.py, tests/unit/test_sdlc_stage_query.py
- **Assigned To**: state-builder
- **Agent Type**: builder
- **Parallel**: false
- In `tools/sdlc_verdict.py::record_verdict`'s `_apply` (`:475-482`), when `stage == "CRITIQUE"` and the normalized verdict contains `WITH CONCERNS`, increment `states["_concern_round_count"]`. Coerce a missing or non-integer value to `0` first; this function must never break verdict recording.
- **No dedupe.** Every recorded with-concerns CRITIQUE verdict counts. Put §3's fail-direction argument in the comment so the next reader does not "optimize" it back in.
- Do NOT add a new `update_stage_states` call; the increment rides the existing transaction.
- Add `"concern_round_count"` to `_compute_meta`'s return (`tools/sdlc_stage_query.py:597-620`) **and** to `_default_meta` (`:621-648`) — the #2769 key-parity rule is stated in `_default_meta`'s own comment.

### 3. Add the predicate, the count helper, and the constant

- **Task ID**: build-predicate
- **Depends On**: build-counter
- **Validates**: tests/unit/test_sdlc_router.py
- **Informed By**: the #1760 latch at `agent/sdlc_router.py:1118` (same field, opposite fail-safe)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_concern_revision_is_unjudged(stage_states, meta)` next to `_critique_verdict_is_stale`, **verdict-kind-agnostic**, with the §1 docstring.
- Add `MAX_CONCERN_RECRITIQUE_ROUNDS` (default 3, `os.getenv`-overridable) to `agent/pipeline_graph.py` with the provisional/tunable comment. This adds the module's first `os` import.
- Add `concern_round_count(meta)` returning `int(meta.get("concern_round_count") or 0)`, tolerating a non-integer as `0`.
- Do NOT read `_sdlc_dispatches` anywhere in this feature.

### 4. Bound the #1760 latch on the with-concerns path

- **Task ID**: build-latch-bound
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router_decision.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- **Land the red first.** Run `test_1760_inverse_guarantee_preserved` (`tests/unit/test_sdlc_router_decision.py:1362`) and capture its current green; then write the replacement expectation (below the bound → stale → `/do-plan-critique`) and capture its red. That capture is the demonstrated-red paper trail for the whole change.
- Add the with-concerns branch to `_critique_verdict_is_stale` per §2: bound exhausted → unconditional `return False`; below the bound → skip the latch and fall through to the timestamp comparison. The existing `elif revision_applied_at and not requires_revision:` body stays byte-for-byte identical.
- Do NOT touch the `requires_revision` computation or the NEEDS REVISION path.
- Every test in `TestConvergenceLatchRevisionAppliedAt` and `tests/unit/test_sdlc_stage_query.py:880-921` must stay green **unchanged**. A red there means the branch leaked — STOP and report rather than editing the test.

### 5. Rewire rows 4b and 4c

- **Task ID**: build-rows
- **Depends On**: build-latch-bound
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Re-key 4b from `not meta["revision_applied"]` to `not _concern_revision_is_unjudged(...)`.
- Re-key 4c from `meta["revision_applied"]` to `_concern_revision_is_unjudged(...) and concern_round_count(meta) >= MAX_CONCERN_RECRITIQUE_ROUNDS`, and rewrite its `reason` to name the bound, the count, and the acceptance.
- Preserve the D3 `pr_number` / `BUILD == completed` step-asides on both.
- **Register no new row.** `len(DISPATCH_RULES)` stays 19 and `tests/unit/test_sdlc_skill_md_parity.py:143-163` is not edited. If you find yourself adding a row, stop — the design intends row 2b to own the state a fourth row would have covered.

### 6. Make G5 step aside on with-concerns

- **Task ID**: build-g5
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Insert `if "WITH CONCERNS" in verdict_text: return None` inside G5's `if CRITIQUE_READY_TO_BUILD in verdict_text:` branch, after the D3 block (`agent/sdlc_router.py:601-603`) and before the #1871 short-circuit (`:615`). Unconditional — no predicate call.
- **Do NOT modify `agent/sdlc_router.py:615`.** It is the documented #1871 protection and the previous revision's proposal to re-key it was a blocker.
- Do NOT touch G5's NEEDS REVISION branch (`:584-597`) or the `GUARDS` ordering.
- **Land the five alive states red first** (§4.5 a / a2 / a3 / b / c); capture the `row_id="G5"` output before the edit. This task is not done until they return `4b` / `/do-plan` via `4b`-or-`G7` / `4b` / `2b` / `4c` respectively.

### 7. Repair G7 gate 3

- **Task ID**: build-g7
- **Depends On**: build-predicate
- **Validates**: tests/unit/test_sdlc_router.py
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace the sticky-`revision_applied` self-heal at `agent/sdlc_router.py:680-682` with `_concern_revision_is_unjudged(stage_states, meta)` so the lock is armable per round.
- Call the predicate **without** a verdict-kind test. Add a test that a `plan_revising` lock left by a `NEEDS REVISION` round still self-heals once the revision lands.
- Add a test for the no-`_verdicts["CRITIQUE"]` case (§8): the lock is no longer healed and G7 escalates via its own backstop. Assert the `Blocked` reason names the manual recovery.

### 8. Skill-side changes

- **Task ID**: build-skills
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_skill_md_parity.py
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- `/do-plan-critique` Step 5.6 (`.claude/skills-global/do-plan-critique/SKILL.md:414-421`): delete the `revision_applied: true` exemption; state the plain verdict-kind rule from §8. Do **not** substitute a timestamp comparison — round 2 showed it is vacuous and unimplementable from inside the skill.
- `docs/sdlc/do-plan-critique.md:76-78`: the same exemption clause in the repo addendum. Both files or neither.
- `/do-plan-critique` Outcome Contract: state that with-concerns no longer reaches BUILD directly.
- `.claude/skills-global/do-build/SKILL.md`: the **generic obligation sentence only** (§5). No `sdlc-tool` invocation and no repo-specific key names in a global body.
- `docs/sdlc/do-build.md`: the concrete `stage-query` derivation, naming the `stages` vs `_meta` nesting explicitly, and the Accepted Residual Concerns note format.
- If the `stage-query` derivation cannot be made to work reliably, STOP and report: the cap-reached decision flips to `Blocked` (D2).

### 9. File the follow-up issue for the concerns-scoped pass

- **Task ID**: file-followup
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Use `/do-issue`. Carry §6's mechanical escalation trigger (changed `^## `/`^### ` header set intersected with `{Solution, Technical Approach, Step by Step Tasks, Verification}`), the two-plan-fixture assertability requirement, and the skill-context split (generic probe in the global body, `stage-query` invocation in `docs/sdlc/do-plan-critique.md`).
- Label `skills`. Reference #2787.

### 10a. Tests — must pass (Definition-of-Done gate)

- **Task ID**: build-tests-gate
- **Depends On**: build-rows, build-g5, build-g7, build-skills
- **Validates**: tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_verdict.py, tests/unit/test_sdlc_stage_query.py
- **Assigned To**: router-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Round 2 flagged that a single undifferentiated test task lets a builder under pressure drop whichever obligation is last, including the ones that are the only proof the feature exists. These six are the gate; `build-tests-gate` is not done until all six pass.
- (1) **The G5 alive test in all five states** (§4.5 a / a2 / a3 / b / c), each asserting `row_id != "G5"`. States a2 and a3 are round 3's concerns 1 and 2 as tests; do not drop them as duplicates of (a).
- (2) **The #1760 split**: `test_1760_inverse_guarantee_preserved` flipped with a captured red, **and** every test in `TestConvergenceLatchRevisionAppliedAt` plus `tests/unit/test_sdlc_stage_query.py:880-921` green and byte-identical.
- (3) **The `_meta` plumb**: `sdlc-tool next-skill` end-to-end on a #2757-shaped ledger returns `/do-plan-critique`. This is the only test that catches the projection-drop failure.
- (4) **Loop scoping**: three `NEEDS REVISION` rounds then one with-concerns → count `1`, state S2 dispatches row `2b`.
- (5) **Termination**: forever-with-concerns terminates at the bound, with row 4c's `reason` string asserted.
- (6) **No-concerns regression**: `READY TO BUILD` with a settled revision still routes to row `4a`.
- Run with `scripts/pytest-clean.sh`, targeted files only. Redis test DB 11 for any live-state verification.

### 10b. Tests — remaining coverage

- **Task ID**: build-tests-rest
- **Depends On**: build-tests-gate
- **Validates**: tests/unit/test_sdlc_router.py, tests/unit/test_sdlc_router_decision.py, tests/unit/test_sdlc_verdict.py
- **Assigned To**: router-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Rebuild `TestRow4bReadyWithConcernsNoRevision` and `TestRow4cReadyWithConcernsRevisionApplied` on the event-scoped fixtures; capture the reds.
- `DISPATCH_RULES` ordering assertion (`2b` before `4b`/`4c`).
- G4-inertness assertion over an alternating dispatch history.
- Counter durability: 15+ dispatches past `MAX_DISPATCH_HISTORY`; survival across a `PipelineStateMachine._save()`; two identical records → count `2`.
- `build_stage_snapshot` output byte-identical with and without `_concern_round_count`.
- `MAX_CONCERN_RECRITIQUE_ROUNDS=0` kill switch restores today's routing.
- G7 gate 3: NEEDS REVISION lock self-heals; no-verdict case escalates.
- Nits-only verdict routes to build with zero extra rounds.
- All remaining Failure Path Test Strategy cases.

### 11. Split validation

- **Task ID**: validate-split
- **Depends On**: build-tests-rest
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify by diff that the *only* change inside `_critique_verdict_is_stale` is the added with-concerns branch, and that the existing `elif` latch body is byte-identical.
- Verify by diff that `agent/sdlc_router.py:615` is unmodified.
- Verify exactly which latch tests changed: `test_1760_inverse_guarantee_preserved` may change; nothing in `TestConvergenceLatchRevisionAppliedAt`, `TestNeedsRevisionInvalidatedByRevision` (other than that test), `TestCritiqueStaleG5LoopBound`, or `tests/unit/test_sdlc_stage_query.py` may.
- Verify the **call sites**: rows 4b/4c and the latch branch each test `"WITH CONCERNS"` themselves; G7 gate 3 calls the predicate without a verdict-kind test (required, not a defect); the predicate body contains **no** verdict-kind test.
- Verify no code path in this feature reads `_sdlc_dispatches`, and that `len(DISPATCH_RULES) == 19`.
- Report pass/fail; a failure here is a blocker, not a test to update.

### 12. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-split
- **Assigned To**: sdlc-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- All eleven documentation targets above. Describe only the new status quo — no "previously the latch behaved as…" residue.

### 13. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: loop-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row below. The absence checks are written as `! grep -q ...` so a zero-match pass exits 0; do not rewrite them back to `grep -c ... | match count == 0`, which exits 1 on a pass and reads as a failure under any exit-code-based harness (round 3, nit 2).
- Confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_decision.py -q` | exit code 0 |
| Verdict recorder tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_verdict.py -q` | exit code 0 |
| Stage-query tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_query.py -q` | exit code 0 |
| Skill parity tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Architectural constraints hold | `scripts/pytest-clean.sh tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No new dispatch row was registered | `.venv/bin/python -c "from agent.sdlc_router import DISPATCH_RULES; print(len(DISPATCH_RULES))"` | output is `19` |
| The bound constant exists and is env-overridable | `grep -A3 'MAX_CONCERN_RECRITIQUE_ROUNDS' agent/pipeline_graph.py \| grep -c 'getenv'` | output > 0 |
| The latch is bounded, not bypassed | `grep -c 'MAX_CONCERN_RECRITIQUE_ROUNDS' agent/sdlc_router.py` | output >= 2 (latch branch + row 4c) |
| The #1871 short-circuit survives | `! git diff origin/main -- agent/sdlc_router.py \| grep -q '^-.*plan_revising") and not meta.get("revision_applied")'` | exit code 0 (no deleted line matches) |
| G5 steps aside unconditionally on with-concerns | `grep -c 'WITH CONCERNS' agent/sdlc_router.py` | output >= 4 (rows 4a/4b/4c + G5 + latch branch) |
| The with-concerns decision is dispatch-history-free | `scripts/pytest-clean.sh tests/unit/test_sdlc_router_decision.py -k with_concerns_no_plan_dispatch -q` | exit code 0 — a ledger with a with-concerns verdict at the bound and **no** `/do-plan` entry in `_sdlc_dispatches` dispatches row `4b`, never `4c`. Replaces round 2's `grep -c` row, which proved only textual non-co-occurrence on one line rather than control-flow independence (round 3, concern 3). |
| The counter is written by the verdict recorder | `grep -c '_concern_round_count' tools/sdlc_verdict.py` | output > 0 |
| The counter reaches the router | `grep -c 'concern_round_count' tools/sdlc_stage_query.py` | output >= 2 (`_compute_meta` + `_default_meta`) |
| Step 5.6 sticky exemption is gone from the global skill | `! sed -n '/^### Step 5.6/,/^## /p' .claude/skills-global/do-plan-critique/SKILL.md \| grep -q 'revision_applied: true'` | exit code 0 |
| Step 5.6 sticky exemption is gone from the addendum | `! sed -n '/plan_revising --value true/,/^## /p' docs/sdlc/do-plan-critique.md \| grep -q 'revision_applied: true'` | exit code 0 |
| The global do-build body stays generic | `! grep -qE 'sdlc-tool\|_concern_round_count\|MAX_CONCERN_RECRITIQUE_ROUNDS' .claude/skills-global/do-build/SKILL.md` | exit code 0 |
| SKILL.md row count corrected | `grep -c 'dispatch rules (19 rows)' .claude/skills/sdlc/SKILL.md` | output == 1 |
| No hand-authored row table was added | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py::test_step4_has_no_hand_authored_dispatch_table -q` | exit code 0 |
| Feature doc exists | `test -f docs/features/with-concerns-recritique-gate.md` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/unit/test_sdlc_router.py` | exit code 1 |

**Note on the two Step 5.6 greps.** They are scoped to the *section* in each file,
not to the whole file, deliberately: both files legitimately continue to mention
`revision_applied_at` (the event-scoped field is the replacement), and a whole-file
"string absent" anti-criterion would also trip on any comment that quotes the
deleted rule. Section-scoped greps assert the rule is gone from where it was
operative.

**Note on the row-count row.** `.claude/skills/sdlc/SKILL.md:223` currently reads
"18 rows" and `len(DISPATCH_RULES)` is already **19** — the literal is stale before
this plan touches anything. This plan registers no new row, so the corrected value
is 19, not 20. Verified by executing `len(DISPATCH_RULES)` against baseline
`2dce9812d`, not by counting `DispatchRule(` occurrences.


## Critique Results

**Round 3** — FULL depth (force-FULL: touches `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/skills-global/`, `.claude/skills/sdlc/`). Verdict: **READY TO BUILD (with concerns)** (0 blockers, 4 concerns, 2 nits). Roster gate: 3/3 complete, 0 ungrounded.

**Round 3 ran twice, independently.** A first war room recorded its findings table in `f7a896e11` but never reached the verdict recorder, leaving the orphaned-table state the addendum documents as self-healing. A second full 3-critic roster was then dispatched against the same plan hash without sight of the first. The table below is the merged, deduplicated result of both. The G7-gate-4 finding (concern 1) was raised **independently by both war rooms**, which is the strongest signal in this round. The second roster's Risk critic filed its concern 2 as a BLOCKER; it is recorded here as a CONCERN, and the downgrade is deliberate and stated rather than silent: the loop still terminates at the bound, the fail direction is still a build with a recorded acceptance, and the correction is to the plan's Race 1 prose and its test fixtures rather than to the latch, the counter, the rows, the G5 step-aside or the G7 repair. Reasonable reviewers could rate it either way.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | **G7 gate 4 preempts row 4b in state S1.** Once §8's Step 5.6 repair lands, `plan_revising=True` is set on every with-concerns verdict, and immediately after CRITIQUE records it `last_dispatched_skill == /do-plan-critique`. `evaluate_guards` runs before `DISPATCH_RULES` (`agent/sdlc_router.py:783`), and the repaired Gate 3 self-heal (`_concern_revision_is_unjudged`) is `False` in fresh S1, so Gate 4 fires `Dispatch(SKILL_DO_PLAN, row_id="G7")` before row 4b is ever evaluated. §4.5(a) and Success Criterion 2 assert S1 yields `row_id == "4b"` — false against the ledger state Step 5.6 itself produces, and passable only by omitting `plan_revising`/`last_dispatched_skill` from the fixture, which is the same vacuous-mandatory-test shape round 2 flagged for G5. | **RESOLVED** — §4.5 (a2) + Flow | Both G7 Gate 4 and row 4b dispatch the **same skill** (`/do-plan`), so there is no routing or build-safety break — the defect is confined to the row-id-level assertion. Fix by building the S1 alive-test fixture with `plan_revising=True` and `last_dispatched_skill="/do-plan-critique"` (Step 5.6's real output) and asserting `dispatch.skill == SKILL_DO_PLAN` with `row_id in {"4b", "G7"}` and explicitly `row_id != "G5"` — the G5-aliveness property is what the test exists to pin. Keep a second S1 fixture *without* the lock (the standalone/`plan_revising`-cleared path) asserting `row_id == "4b"` exactly. State in §4.5 and the Flow that G7 legitimately owns S1's first turn after a with-concerns verdict and row 4b owns S1 once the lock is cleared. |
| CONCERN | Risk & Robustness | **"The feature never reads the truncated history" is inaccurate.** The with-concerns bound-exhausted branch lives inside `_critique_verdict_is_stale`, which unconditionally computes `latest_plan_at = _latest_dispatch_at(stage_states, SKILL_DO_PLAN)` (reading the FIFO-truncated `_sdlc_dispatches`, `MAX_DISPATCH_HISTORY = 10` at `agent/sdlc_router.py:98`) and early-returns `False` via `if not latest_plan_at: return False` **before** the new branch is reached. That contradicts Risk 1 layer 1's "does not depend on dispatch history", and the Verification row's grep (`grep -n '_sdlc_dispatches' … \| grep -c 'concern'` == 0) proves only textual non-co-occurrence on one line, not control-flow independence. | **RESOLVED** — §2 placement + Risk 1 layer 1 + Verification | The early-return gate sits inside `_critique_verdict_is_stale`'s `try`, ahead of the `requires_revision` computation the plan quotes at `agent/sdlc_router.py:1113-1124`. Benign in the real alternating `/do-plan` ↔ `/do-plan-critique` loop (FIFO evicts oldest first, so the most-recent `/do-plan` entry is never displaced), so this is a **claim/verification correction, not a design change**. Correct Risk 1 layer 1 to say only `_concern_revision_is_unjudged` (rows 4b/4c, G7 gate 3) is genuinely dispatch-history-free, while row 2b's with-concerns re-critique inherits the pre-existing "has any `/do-plan` dispatch been recorded" gate. Replace the grep row with a test: a ledger whose `_sdlc_dispatches` carries no `/do-plan` entry plus a with-concerns verdict at the bound must dispatch row `4b` (fail-safe to a revision pass), never `4c`. |
| CONCERN | Risk & Robustness | **The below-the-bound branch decides staleness on dispatch time, not on a landed revision.** §2 prescribes that below the bound the latch "stands down" and falls through to `verdict_dt < plan_dt`, where `plan_dt` is `_latest_dispatch_at(stage_states, SKILL_DO_PLAN)` (`agent/sdlc_router.py:935-942`) — the `at` stamped by `record_dispatch` when the router **dispatches** `/do-plan`, not when `/do-plan` finishes. So the instant row 4b dispatches the revision pass the verdict is already stale, and row 2b — registered at `agent/sdlc_router.py:1563`, ahead of 4b/4c, with a predicate (`_rule_critique_verdict_stale`) that tests only staleness plus a non-empty verdict, with no with-concerns and no revision-landed test — fires `/do-plan-critique` against a plan that has not been revised. This contradicts the plan's own state model: the Flow gives row 2b **S2** ("a `/do-plan` revision has landed since that verdict"), but the prescribed code also fires it in S1-after-dispatch, including when `/do-plan` crashed or no-opped. It is benign today only because row 2b's docstring bound — G5's unchanged-plan-hash short-circuit — catches it, and §4.5 removes exactly that backstop for with-concerns. It also invalidates Race 1's mitigation ("resolves to `False` → row 4b → `/do-plan`, which is idempotent … bounded by G4"): 2b precedes 4b, so the wasted turn is a full war room that consumes a `_concern_round_count` slot, and G4 cannot see the alternation. Terminates safely at the bound, hence CONCERN not BLOCKER. | **RESOLVED** — §2 + Race 1 + §4.5 (a3) | Gate the below-bound branch on the revision having actually landed rather than on dispatch time: below the bound return `True` only when `_concern_revision_is_unjudged(stage_states, meta)` (state S2). In S1-after-a-crashed-`/do-plan` the verdict is then not stale, row 2b misses, row 4b re-fires `/do-plan` — idempotent, a same-skill streak, genuinely bounded by G4. Place the with-concerns branch **ahead of** the `if not latest_plan_at: return False` early return so it is control-flow independent of `_sdlc_dispatches`, which also closes concern 3. Rewrite Race 1's mitigation to state this instead of the false "row 4b, idempotent" claim. Add a fourth mandatory alive state to §4.5: with-concerns, hash-match, `revision_applied_at` older than `recorded_at`, **and** a `/do-plan` dispatch postdating `recorded_at` → row `4b`, never `2b`, never `G5`. |
| CONCERN | Scope & Value | The Appetite section declares `**Size:** Medium` while the same section states `**Review rounds:** 2+`. `.claude/skills-global/do-plan/SCOPING.md` defines Medium as "1 review round" and Large as "2-3 PM check-ins, 2+ review rounds" — the plan's own stated interaction budget is verbatim the Large definition, and this is now critique round 3 across 13 tasks, 6 roles, 20 Verification rows and 11 documentation targets. | **RESOLVED** — Appetite | Change `**Size:** Medium` to `**Size:** Large`, or keep Medium and add one sentence saying why the review-round count exceeds SCOPING.md's Medium tier. Do not leave the label and the budget in open contradiction. |
| NIT | History & Consistency | G7 gate 3 (`if meta.get("revision_applied"): return None`) was cited as `agent/sdlc_router.py:684-685` in the Freshness Check, §8 and task 7; it is actually at `:680-682` (`:684-685` is unrelated Gate-4 preamble). The Freshness Check self-contradicts two bullets earlier, where "`agent/sdlc_router.py:677-682` — G7 gates 2 and 3" is correct. Everything else this lens checked verified clean against `2205e2dd4`: all row citations, the #1760/#2049 polarity claims, `len(DISPATCH_RULES) == 19`, the `MAX_CRITIQUE_CYCLES` bare-literal claim, the durability and projection claims, the doc targets, and every cited historical issue/PR. | **RESOLVED** — 3 citations corrected | Change the three `684-685` citations to `680-682`. |
| NIT | Structural check | Five Verification rows state their PASS condition as "match count == 0" over a `grep -c` pipeline (the #1871 short-circuit row, the truncated-history row, both Step 5.6 section greps, and the global-`do-build` leakage row). `grep -c` prints `0` but **exits 1** on zero matches, so task 13's "run every Verification row" will read every one of these passes as a command failure under any `set -e` / exit-code-based harness. | **RESOLVED** — Verification + task 13 | — |

### Round 3 revision (applied)

All four concerns are corrections to the plan's own claims, prose and test fixtures. **None changes the design**: the latch bound, the counter and its `_meta` plumb, rows 4b/4c, the unconditional G5 step-aside and the G7 gate-3 repair all stand as specified in round 2. Concern 2 adds one guard condition to a branch the plan already prescribes, and that same edit subsumes concern 3.

This is the bounded settle pass, not another war room. Per the issue's own thesis, a with-concerns verdict earns exactly one concern-closing revision, which is then built on. The revision embeds the four Implementation Notes into §2, §4.5, the Flow, Risk 1, Race 1, the Appetite section, the Success Criteria and the Verification table, and folds both NITs in (the `684-685` → `680-682` citations, and the `grep -c` rows restated as "prints `0`" rather than an exit-code assertion).

**Round 2** — FULL depth (force-FULL: touches `agent/sdlc_router.py`, `agent/pipeline_graph.py`, `.claude/skills-global/`, `.claude/skills/sdlc/`). Verdict: **NEEDS REVISION** (3 blockers, 4 concerns, 1 nit). Roster gate: 3/3 complete, 0 ungrounded.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | §2.5's two G5 edits are both gated on `_concern_revision_is_unjudged`, which is **False** in the state the blocker describes. Right after CRITIQUE records a with-concerns verdict, `recorded_at` postdates `revision_applied_at`, so step-aside (2) does not fire and G5 still returns `Dispatch(SKILL_DO_BUILD, ..., row_id="G5")`. Edit (1) additionally re-keys `agent/sdlc_router.py:615` to the same predicate, deleting the documented #1871 short-circuit — that line's own comment says without it the G7-gate-6 fallthrough state "would let G5's cached READY-TO-BUILD verdict ship the pre-revision design via /do-build". The prescribed fix does not fix the blocker it was written for, and removes an existing protection. | **RESOLVED** — §4.5 + task 6 | Use a single **unconditional** guard inside the `if CRITIQUE_READY_TO_BUILD in verdict_text:` branch, placed after the D3 block: `if "WITH CONCERNS" in verdict_text: return None`. G5 never needs to serve a with-concerns verdict — rows 4b/4c/4d cover every with-concerns state including the bound-exhausted one (row 4d dispatches `/do-build` with the acceptance reason the Success Criteria require). Do **NOT** touch `agent/sdlc_router.py:615`. The alive test must cover BOTH revision states: (a) hash-match + with-concerns + `revision_applied_at` OLDER than `recorded_at` → row `4b`, not `G5`; (b) same with `revision_applied_at` NEWER → row `4c`, not `G5`. The plan currently specifies only (b), which is why the defect survives its own mandatory test. |
| BLOCKER | Risk & Robustness | Row 2b (`_rule_critique_verdict_stale`, registered `agent/sdlc_router.py:1563`) dispatches `/do-plan-critique`, is evaluated **before** rows 4b/4c/4d (`evaluate` walks `DISPATCH_RULES` in order and `break`s on first match, `agent/sdlc_router.py:1750-1758`), and contains no with-concerns test and no bound test. Whenever the #1760 latch goes inert on this path (any `/do-plan` dispatch whose `at` postdates `revision_applied_at` — reachable via G7 gate 4's own `/do-plan` dispatch or any second plan turn), row 2b re-critiques regardless of `_concern_round_count`, so row 4d never owns the terminal decision. The bound is not "bounded by construction". The plan names row 2b "the structural twin" in Prior Art and never checks whether it competes. | **RESOLVED** — §2 (dissolved: row 2b IS the edge) | Add an early step-aside to `_rule_critique_verdict_stale`: `verdict = normalize_verdict(_latest_critique_verdict(stage_states, meta)); if "WITH CONCERNS" in verdict and _concern_round_count(stage_states) >= MAX_CONCERN_RECRITIQUE_ROUNDS: return False`. Do **not** add a general verdict-kind test to row 2b — its NEEDS REVISION domain is #1639's fix and must keep firing. Add a test constructing the bound-exhausted state **with** a `/do-plan` dispatch postdating `revision_applied_at` (latch inert, so 2b would otherwise match) and asserting `row_id == "4d"`. |
| BLOCKER | History & Consistency | The plan's #1 risk mitigation rests on an inverted reading of #1760. Risk 1 layer 1 says "#1760 is about a **no-concerns** verdict being re-staled by a notes-only revision." The code says the opposite: `_critique_verdict_is_stale` engages the latch only when `revision_applied_at and not requires_revision` (`agent/sdlc_router.py:1119`), where `requires_revision` is NEEDS REVISION / MAJOR REWORK — so #2049 narrowed the latch **away from** NEEDS REVISION, leaving with-concerns as its live domain. The docstring states it: "the latch protects ONLY the settle-and-build path — a READY TO BUILD (with concerns) verdict whose own settle revision must not re-stale it back into critique (#1760)". This plan therefore re-opens the loop #1760 suppressed. The stated mechanical proof is also vacuous: task 2 forbids modifying `_critique_verdict_is_stale`, so its tests are green by construction whether or not the new rows re-open the loop. | **RESOLVED** — Prior Art + Risk 1 + §2 | Rewrite Prior Art's #1760 bullet and Risk 1 layer 1 as supersession, not disjointness: "#1760's latch governs the with-concerns settle-and-build path (`agent/sdlc_router.py:1119`). This plan deliberately re-opens that path and makes it terminate by bounding it — #1760 had no bound, which is why suppression was the only fix available. The no-concerns and NEEDS REVISION paths are untouched." Replace the "latch tests stay green" proof with a test that can actually fail: no-concerns READY TO BUILD with `revision_applied_at` postdating `recorded_at` → dispatch is still row `4a`, never `4c`. Add `docs/features/sdlc-router-oscillation-guard.md:473-507` (Convergence latch) to the Documentation targets — it currently documents the latch as this path's terminating bound and will be wrong. |
| CONCERN | Scope & Value | Task 5 puts a repo-only `sdlc-tool stage-query` derivation, plus knowledge of `_concern_round_hashes` and `MAX_CONCERN_RECRITIQUE_ROUNDS`, into `/do-build` Step 1 — but `/do-build` is a **global** skill (`.claude/skills-global/do-build/SKILL.md`, hardlinked to every machine). This is round 1's C4(c) violation moved to a different skill, not resolved; §5's "moot with Step 0.5 deferred" is wrong. `docs/sdlc/do-build.md` already exists and already carries `sdlc-tool stage-query` invocations (line 93). | **RESOLVED** — §5 + task 8 | Global body gets only the generic obligation: "If the pipeline's state substrate records that this build was reached by exhausting a critique bound, record the accepted residual concerns in the plan before building." Concrete derivation goes in `docs/sdlc/do-build.md`. Also name the nesting explicitly in §4: `stage-query` emits `{"stages": {...raw stage_states incl. `_verdicts`/`_sdlc_dispatches`...}, "_meta": {...}}`, so `recorded_at` and `_concern_round_hashes` live under **`stages`**, not `_meta`. A builder told only "derive it from stage-query" will look in `_meta`, find `latest_critique_verdict` is a bare string with no `recorded_at`, and conclude the derivation is impossible. |
| CONCERN | Risk & Robustness | §3's "Idempotent" property claims `artifact_hash` dedupe distinguishes a replay from a genuine round: "A replayed `sdlc-tool verdict record` for the same plan bytes produces the same hash and does not double-count. A genuine next round judges a revised plan, so its hash differs and it does count." Those are byte-identical inputs to the dedupe and are indistinguishable by construction. `_compute_artifact_hash` (`tools/sdlc_verdict.py:227`) delegates to `compute_plan_body_hash`, which strips only `^revision_applied:\s*\S+\s*$` (`tools/sdlc_verdict.py:209-211`), so any second with-concerns verdict recorded without an intervening plan-body change silently fails to advance the bound — the same non-monotonicity the plan rejects `_sdlc_dispatches` for. | **RESOLVED** — §3 (dedupe removed entirely) | Key the dedupe on the revision epoch: append `f"{artifact_hash or recorded_at}\|{revision_applied_at or ''}"` instead of the bare `artifact_hash`, reading `revision_applied_at` from the same `_meta` the router consumes. A true replay carries the same plan bytes AND the same `revision_applied_at`, so it still dedupes; a genuine round always follows a `/do-plan` pass that rewrote `revision_applied_at`, so it always counts. Test: two with-concerns records on identical plan bytes, different `revision_applied_at` → count `2`; same value → count `1`. |
| CONCERN | Risk & Robustness | §7's repaired Step 5.6 rule ("set the lock when the verdict requires a revision **and** the plan's `revision_applied_at` is not later than this verdict's timestamp") is vacuous. Step 5.6 runs immediately after Step 5.5 recorded the verdict, so "this verdict's timestamp" is effectively now and `revision_applied_at` is by definition an earlier `/do-plan` pass — the comparison can never be false in normal operation. It also gives the builder nothing implementable: the skill has no stated way to read back the `recorded_at` it just wrote. | **RESOLVED** — §8 | State the rule as what it is, in both files: "Set the lock whenever the verdict is `NEEDS REVISION`, `MAJOR REWORK`, or `READY TO BUILD (with concerns)`. Delete the `revision_applied` exemption entirely — the lock is released by the event-scoped G7 gate 3, not by the skill declining to set it." Keep the "Do NOT set for READY TO BUILD (no concerns)" clause. That is one deleted clause per file, greppable by the two Verification rows already in the plan, and moves all event-scoping into `agent/sdlc_router.py` G7 gate 3 where it reads real ledger state and is unit-testable. |
| CONCERN | Scope & Value | Task 6 assigns thirteen test obligations to one non-parallel `router-tester` task, on top of eighteen Failure Path checkboxes and sixteen Success Criteria, for a **Medium** appetite — with no ranking. Several are genuinely expensive (exhaustive 4b/4c/4d mutual exclusivity, forever-with-concerns simulation, the `next-skill` end-to-end, the `build_stage_snapshot` byte-identity check). A builder under pressure drops whichever is last, and the plan gives no signal about which drops are survivable; two of these tests are the only proof the feature exists at all. | **RESOLVED** — tasks 10a / 10b | Split task 6 into `6a` (must-pass, gates the task) and `6b` (should-pass). 6a holds exactly four: (1) the G5 alive test in **both** revision states; (2) three NEEDS REVISION rounds then one with-concerns → count `1`, row `4c`; (3) forever-with-concerns terminating at the bound with row 4d's reason string asserted; (4) the no-concerns READY TO BUILD path still routing to row `4a` unchanged. Mark 6a as the Definition-of-Done gate for `build-tests`. |
| NIT | History & Consistency | Consistency slips: (a) the Freshness Check names baseline `8b13098bc`, a real commit belonging to an unrelated lane ("Plan critique round 7 (#2643)"), while the round-1 revision block cites `667fecc16` and HEAD is `3a6150fb7` — three baselines, none current. (b) The Verification row `grep -c '_concern_revision_is_unjudged' agent/sdlc_router.py` expects `>= 4` while its own parenthetical enumerates seven call sites, so the gate passes with three missing. (c) Team Orchestration still describes `skill-builder` as owning "`/do-plan-critique` Step 0.5 and Step 5.6" although §5 deferred Step 0.5. (d) `.claude/skills/sdlc/SKILL.md:223` says "(18 rows)" while `DISPATCH_RULES` already has 19; the plan's doc bullet and the `grep -c 'dispatch rules (19 rows)'` Verification row would ship a fresh off-by-one — the correct post-change value is **20**. | **RESOLVED** — see round-2 revision table | — |

### Round 2 revision (applied)

All 3 blockers, 4 concerns and the nit are addressed. Blocker 3 changed the shape
of the fix rather than being patched: re-deriving the #1760 interaction from
`agent/sdlc_router.py:1113-1124` showed the plan's core safety argument was
backwards, and that the re-critique edge it wanted to add already existed as row
2b. Every code claim below was re-verified against `main` at `2dce9812d`; two
findings surfaced that round 2 did not have (the `stage-query` projection drop and
G4's inability to see this loop) and both are folded in.

| Finding | Resolution |
|---|---|
| B1 — the G5 fix does not fix the blocker, and deletes the #1871 protection | §4.5 now specifies **one unconditional** `if "WITH CONCERNS" in verdict_text: return None` inside G5's ready-to-build branch, placed after the D3 block and before `agent/sdlc_router.py:615`, which is **not touched**. The alive test covers all three revision states (S1 → `4b`, S2 below bound → `2b`, S2 at bound → `4c`), each asserting `row_id != "G5"`. Task 6 forbids modifying `:615`; task 11 verifies it by diff. |
| B2 — row 2b routes around the bound | Dissolved, not patched. Row 2b is no longer a competitor: it **is** the bounded re-critique edge (§2). The bound now lives inside `_critique_verdict_is_stale`, which row 2b calls, and the bound-exhausted `return False` is **unconditional** — it reads no timestamp and no dispatch history, so the "latch goes inert" state that made 2b bypass the bound cannot arise. Ordering (2b before 4b/4c) becomes load-bearing and gets its own test. |
| B3 — the #1760 disjointness argument is inverted | Retracted outright and replaced with supersession. `_critique_verdict_is_stale` engages the latch only when `not requires_revision` (`agent/sdlc_router.py:1118`), and its docstring (`:1100-1103`) names `READY TO BUILD (with concerns)` as what it protects — so the with-concerns settle path is the latch's live domain. Prior Art, Why Previous Fixes Failed, Risk 1 and §2 all now say the plan deliberately re-opens what #1760 suppressed, and that the bound is what #1760 lacked. The vacuous "latch tests stay green" proof is replaced by a falsifiable split: `test_1760_inverse_guarantee_preserved` (`tests/unit/test_sdlc_router_decision.py:1362`) **must flip** with a captured red; every no-concerns latch test **must stay byte-identical**. `docs/features/sdlc-router-oscillation-guard.md:473-507` added as a doc target. |
| C1 — `/do-build` is a global skill; task 5 relocated round 1's C4(c) rather than resolving it | §5 splits it: the global body gets one generic obligation sentence with no `sdlc-tool` call and no repo key names; the concrete derivation goes in `docs/sdlc/do-build.md`, which already carries `stage-query` invocations at line 93. The `stages` vs `_meta` nesting is named explicitly, including that `_meta["latest_critique_verdict"]` is a bare string with no `recorded_at`. A Verification row greps the global body for leakage. |
| C2 — `artifact_hash` dedupe cannot distinguish a replay from a genuine round | Went further than the prescription. The compound `(artifact_hash, revision_applied_at)` key is better but can *also* stall — if `/do-plan` fails to rewrite `revision_applied_at`, both components are unchanged. That is fatal here because of a finding round 2 did not have: `compute_same_stage_count` (`agent/sdlc_router.py:2003-2056`) breaks its streak on any skill change, so **G4 cannot bound a `/do-plan` ↔ `/do-plan-critique` alternation** and the counter is the loop's only terminator. §3 therefore drops dedupe entirely and counts every recorded with-concerns verdict: over-counting costs one round, under-counting costs an unbounded loop with no backstop. The counter is now a plain integer, `_concern_round_count`. |
| C3 — the repaired Step 5.6 rule is vacuous and unimplementable | Adopted the prescription verbatim (§8): the rule becomes the plain verdict-kind list, the `revision_applied` exemption is deleted from both files, and **all** event-scoping moves into G7 gate 3 where it reads real ledger state and is unit-testable. |
| C4 — thirteen unranked test obligations in one task | Task 6 split into **10a** (six must-pass items that gate the task, including the three-state G5 alive test, the two-directional #1760 split, and the CLI end-to-end that is the only check on the `_meta` plumb) and **10b** (remaining coverage). 10a is the Definition-of-Done gate. |
| N1 — baseline/count/role inconsistencies | (a) One baseline everywhere: `2dce9812d`, re-read for every reference. (b) The `>= 4` grep with a seven-site parenthetical is gone; Verification now greps for `MAX_CONCERN_RECRITIQUE_ROUNDS` and asserts `len(DISPATCH_RULES)` by execution. (c) Team Orchestration's `skill-builder` no longer mentions Step 0.5. (d) The row count was verified by executing `len(DISPATCH_RULES)` → **19**; `.claude/skills/sdlc/SKILL.md:223` says 18 and is already stale by one. This plan adds **no** row, so the corrected value is **19**, not 20 — the round-2 nit's own suggested number would itself have shipped an error. `tests/unit/test_sdlc_skill_md_parity.py:143-163` needs no edit. |

### Round 2 findings the revision went beyond

| Finding | Why it matters |
|---|---|
| `stage-query` drops unknown `_*` keys | `sdlc-tool next-skill` reads `enriched["stages"]` / `["_meta"]` (`tools/sdlc_next_skill.py:629-630`), and `stage-query` threads only `("_verdicts", "_sdlc_dispatches")` into `stages` (`tools/sdlc_stage_query.py:766-768`). The previous revision's `_concern_round_hashes` would have read as empty on every real dispatch — a feature that passes every unit test and is inert in production. The counter now rides `_meta`, following the `critique_cycle_count` precedent, and the CLI end-to-end is a must-pass test. |
| G4 cannot bound this loop | `compute_same_stage_count` counts *consecutive same-skill* dispatches. The loop alternates two skills, so the streak resets every turn. Every earlier revision of this plan cited G4 as a backstop for the loop; it is not one. Stated in Risk 1, pinned by a test, and the reason §3 refuses any dedupe. |

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
Concerns note now has a working delivery channel — `/do-build` re-derives row 4c's
predicate from `sdlc-tool stage-query` rather than depending on a `row_id` that is
never plumbed into skill invocations (§5). **The decision is conditional and the
condition is written into task 8:** if that derivation cannot be made reliable at
build time, the builder stops and the cap-reached behavior flips to `Blocked`. A
silent build at the cap is the failure mode this plan exists to prevent, one level
up.

**D3 — Scoped vs. full re-critique: full depth, every round. Scoping deferred.**
The scoped pass was the plan's largest new surface, had no test, was justified by
cost rather than correctness, and its escalation trigger was unassertable prose.
It is deferred to a follow-up issue (task 9) carrying a mechanical trigger —
the changed `^## `/`^### ` header set intersected with `{Solution, Technical
Approach, Step by Step Tasks, Verification}` — which is assertable over two plan
fixtures. **Revisit when** a with-concerns lane's war-room cost is measured and
shown to be the binding constraint, not before.
