---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2029
last_comment_id: none
revision_applied: false
plan_revising: false
---

# T3.1 SDLC router redesign: dispatch precedence, convergence latch, and outcome-verified advance

## Problem

Three open issues are three symptoms of one subsystem — the SDLC router's
dispatch/convergence logic in `agent/sdlc_router.py`. Each was filed as its own
defect; each is the same underlying flaw: **the router advances on a single
cached or self-attested signal instead of on the live composite state.** The
`#2003` substrate (already merged: `2f324bff`) established live-composite-state
discipline for the *ownership* and *merge* layers — run-id issue leases, live-ref
PR resolution (`tools/_sdlc_utils.py::resolve_ledger_lease`), and a live
merge-predicate gate (`tools/merge_predicate.py`). This redesign carries that
same discipline back into the *dispatch/convergence* layer that #2003 did not
touch.

**The three symptoms, and the one shared root:**

1. **#1871 — G5 build fast-path ignores the plan-revising lock.**
   `guard_g5_artifact_hash_cache` (`agent/sdlc_router.py:419-508`) dispatches
   `/do-build` on a cached READY-TO-BUILD verdict when the plan hash is unchanged.
   Its READY-TO-BUILD branch (lines 493-506) gates only on `pr_number` and
   `BUILD == completed` (D3) — it never reads `plan_revising`. The lock guard that
   *would* block this, `guard_g7_plan_revising` (lines 511-595), is evaluated
   **after** G5 in the `GUARDS` list (lines 640-648). So when a critique sets
   `plan_revising=true` and leaves the CRITIQUE marker in-progress to request a
   revision pass, G5 fires first and ships the pre-revision design. Observed live
   on #1821 (2026-07-03).

2. **#1760 — PLAN↔CRITIQUE never converges to BUILD.**
   `_critique_verdict_is_stale` (`agent/sdlc_router.py:841-874`) is a purely
   timestamp-based predicate: verdict `recorded_at` < latest `/do-plan` dispatch
   `at`. It does **not** consult `revision_applied`. Row 2b
   (`_rule_critique_verdict_stale`, lines 877-891) fires on that staleness and
   re-dispatches `/do-plan-critique` — and it is evaluated *before* rows 4a/4c,
   which are the only rows that honor `revision_applied` as the convergence
   signal. A revision pass that embeds concern/nit notes into the plan **body**
   busts the body hash (G5 returns None) *and* re-stales the verdict by timestamp
   → row 2b → re-critique → fresh non-blocking nits → "with concerns" → row 4b →
   `/do-plan` revision → busts hash + re-stales → loop. The pipeline only reaches
   BUILD when a human overrides it. Observed live on both cuttlefish#547 and #550
   (2026-06-22).

3. **#1267 — no outcome verification before advance.**
   The router advances on stage-completion markers and verdicts that the executing
   agent *self-attests* (the `<!-- OUTCOME {...} -->` contract parsed by
   `classify_outcome` Tier 0, `agent/pipeline_state.py:858-899`). Nothing
   independently checks that the claimed load-bearing artifact — a PR actually
   opened, a branch actually pushed, tests actually run — exists in the world.
   #2003 added live verification *at the merge gate only* (`merge_predicate.py`).
   The stage-advance boundary still trusts the claim.

**Desired outcome:** the router treats three signals as authoritative composite
state rather than trusting one cached/attested value:
- the **plan-revising lock** gates *every* build fast-path (fixes #1871);
- **`revision_applied: true`** is the authoritative "plan settled" convergence
  latch — a READY-TO-BUILD verdict is never re-staled by the revision pass that
  set it (fixes #1760);
- a **stage-advance verification gate** asserts the small set of high-value
  claimed artifacts against the live world (reusing #2003's live-ref resolution)
  before honoring a self-attested completion marker (fixes #1267).

## Freshness Check

**Baseline commit:** `9c559db4` (`main`, clean; re-verified at PLAN stage after the
#1926 lane merged). Original recon ran against `c52be651`; the only commits since
(`88345cc0`..`9c559db4`) are this plan's own commit plus the #1926/#2030 lane —
**neither touched `agent/sdlc_router.py`** (`git log c52be651..HEAD -- agent/sdlc_router.py`
returns nothing), so every cited line reference below still holds exactly
(guard_g5=419, guard_g7=511, `GUARDS`=640, `_critique_verdict_is_stale`=841 all
re-confirmed live).
**Issues filed at:** #1871 2026-07-03, #1267 (reframed 2026-07-10), #1760 2026-06-22.
**Disposition:** Minor drift — line numbers moved under #2003 (`2f324bff`) and the
#1761 body-hash migration; all three root-cause claims still hold on current `main`.

**File:line references re-verified against `c52be651`:**

- **#1871** cited G5 at `374-423`; now at **`agent/sdlc_router.py:419-508`**. Claim
  intact: the READY-TO-BUILD branch (493-506) gates only on `pr_number` /
  `BUILD == completed`, never on `plan_revising`; and G5 precedes G7 in the
  `GUARDS` list (640-648). `guard_g7_plan_revising` now exists (511-595, added
  after the issue was filed) but its *ordering after G5* means it cannot save the
  cached-verdict fast-path. **The fix moves from "add a lock guard" (done) to
  "make the lock precede the build fast-path."**
- **#1760** cited `_critique_verdict_is_stale` at `750-783`, row 2b at `786-800`,
  row 4b at `649-671`, G5 at `374-423`. Now at **841-874 / 877-891 / 740-762 /
  419-508** respectively. Claim intact: staleness is timestamp-only and ignores
  `revision_applied`; row 2b precedes rows 4a/4c. **Partial mitigation already on
  main:** the #1761 migration switched G5 to `compute_plan_body_hash`
  (`tools/sdlc_verdict.py:144`), which strips the `revision_applied:` frontmatter
  line — so a revision that *only* sets that flag no longer busts the hash. But
  #1760's scenario embeds concern notes into the plan **body**, which still busts
  the body hash and still re-stales the verdict by timestamp. The loop remains
  reachable. Confirmed still present.
- **#1267** reframed itself on 2026-07-10: `session_type="dev"` no longer exists
  (`models/agent_session.py`: discriminator is `"eng"` / `"teammate"`; `"granite"`
  historical-only). Verified. Dev work runs as an in-turn Agent-tool subagent
  inside the eng session's turn, so verification belongs at the PM turn boundary /
  stage-marker substrate — exactly where the router consumes markers. The issue's
  own note states it will be folded into "T3.1 (verdict/staleness routing)" — **this
  plan is that T3.1.** `classify_outcome` / `session_completion.py` still exist and
  are re-audited under this framing here.

**Cited sibling issues/PRs re-checked:**
- **#2003 (PR merged as `2f324bff`)** — the pipeline substrate this plan builds ON.
  Provides run-id ownership, `resolve_ledger_lease` live-ref PR resolution, and
  `merge_predicate.py` live merge-gate. Landed; not a blocker; a foundation.
- **#1761** — body-hash migration; landed; partially mitigates #1760 (see above).
- **#1941 (`9e2f2b5c`)** — added open-PR guards on row 3/G1/G5 and row 8d recovery;
  already reflected in the current G5 D3 branches. No conflict.

**Commits on main since the issues were filed (touching `agent/sdlc_router.py`):**
`2f324bff` (#2003), `9e2f2b5c` (#1941), `313724f3` (#1903), `04621530` (#1763).
All are consistent with — and in the case of #2003, foundational to — this plan.

**Active plans in `docs/plans/` overlapping this area:**
- `post-teardown-scar-tissue-removal.md` (#1926) — **now merged as #2030
  (`5ac64a8c`).** It touched `agent/output_router.py` (the *bridge* nudge/deliver
  router, `MAX_NUDGE_COUNT`), stall-recovery, and slot-lease reap — NOT
  `agent/sdlc_router.py` (the *SDLC dispatch* router). Zero file overlap, confirmed
  post-merge (`git log c52be651..HEAD -- agent/sdlc_router.py` is empty). The
  earlier "Overlap" disposition is resolved: the sibling lane landed cleanly and
  this plan's target file is untouched.

## Prior Art

The stale-critique / convergence region of the router has a long lineage of
point fixes. This plan treats the pattern, not another point:

- **`5bc6243a` (#1638/#1640/#1641)** — verdict normalization, plan-existence gate,
  stale-verdict supersession. Introduced the timestamp-staleness model this plan
  refines.
- **`6e943ea9` (#1639)** — stale-critique dead-end fix; added row 2b.
- **`3e1e3dae` (#1668)** — CRITIQUE in_progress with empty verdict (row 2c).
- **`8218c5af` (#1554)** — guard row 4b against re-firing once a PR exists (the D3
  guards this plan preserves).
- **`627e3cf0` (#1755)** — row 8c REVIEW empty-verdict re-dispatch.
- **`04621530` (#1763)** — 3-layer cross-repo plan resolution (a prior PLAN↔CRITIQUE
  loop fix; addressed *cross-repo path resolution*, not the staleness/`revision_applied`
  gap #1760 names).
- **#1761** — body-hash migration; the partial mitigation named in the Freshness
  Check.
- **`2f324bff` (#2003)** — the substrate: run-id ownership, live-ref PR resolution,
  live merge-gate. The live-verification primitives #1267's gate reuses.

The `<!-- OUTCOME {...} -->` self-attestation contract (`agent/pipeline_state.py:102-135`)
has no single tracking issue; it is the mechanism #1267 says is structurally
insufficient (self-attested, unverified).

## Why Previous Fixes Failed

Every prior staleness fix added a **row** to special-case one dead-end
(2b, 2c, 8c, 4b guards). None changed the **staleness predicate** itself, which
remained timestamp-only. So each fix moved the loop's boundary without removing
the loop: #1760 is the residual because the predicate still cannot tell a
*substantive* plan revision from a *convergence* revision. The lesson: fix the
predicate (make `revision_applied` the convergence latch), do not add a ninth row.
Symmetrically, #1871's `guard_g7_plan_revising` was added as a *new guard* but
placed after G5 — a guard that cannot run before the fast-path it must gate is not
a fix. The pattern across all three: point additions instead of ordering/predicate
corrections.

## Research

No external research required — this is entirely internal to `agent/sdlc_router.py`,
`tools/sdlc_next_skill.py`, and the #2003 substrate modules. Ground truth is the
codebase and the three issues. Proceeding with codebase context and training data.

## Data Flow

The dispatch decision, end-to-end (the layer this plan changes):

1. **Supervisor** (`/do-sdlc` or `/sdlc`) calls `sdlc-tool next-skill --issue-number N`
   (`tools/sdlc_next_skill.py`).
2. **next-skill builds live context:** reads stage markers + `_verdicts` +
   `_sdlc_dispatches` from the run-id-keyed ledger; computes `current_plan_hash`
   via `compute_plan_body_hash` and `legacy_plan_hash`; pulls live PR state
   (`pr_number`, `pr_merge_state`, `ci_all_passing`) via `tools/sdlc_stage_query.py`
   (live `gh` calls). Assembles `stage_states`, `meta`, `context`.
3. **Guard chain** (`evaluate_guards`, `agent/sdlc_router.py:640-660`): walks
   `GUARDS = [g1, g2, g3, g4, g5, g6, g7]`, returns the first non-None
   `Dispatch`/`Blocked`. **This is where #1871 lives** (G5 before G7).
4. **Dispatch table** (rows 1-10, evaluated only if guards return None): rows
   consult `_critique_verdict_is_stale` and `revision_applied`. **This is where
   #1760 lives** (row 2b staleness before rows 4a/4c).
5. **Stage advance:** when a dispatched skill completes, it writes a
   stage-completion marker (`sdlc-tool stage-marker`) and/or a verdict. next-skill
   reads those markers as truth on the next tick. **This is where #1267 lives** —
   no live-world check of the claimed artifact between the marker write and the
   advance.

The fix layer is (3), (4), and a new verification step wrapping (5).

## Architectural Impact

- **New dependencies:** none. Reuses #2003's `resolve_ledger_lease`,
  `merge_predicate.py`, and `sdlc_stage_query.py` live-`gh` helpers.
- **Interface changes:** `GUARDS` ordering changes (G7 → before G5). The staleness
  predicate gains a `revision_applied`-aware convergence branch. A new
  verification function is added to the next-skill context-assembly path (or a
  thin `tools/` module it calls); the router core stays free of `tools/` imports
  (the existing dependency-inversion boundary — `context`-supplied values only —
  is preserved). No Popoto model/schema change (all new signals ride existing
  `meta`/`stage_states`/`_verdicts` dicts).
- **Coupling:** unchanged or lower. The redesign removes the need for future
  special-case rows by fixing the predicate; the guard reorder is a list edit.
- **Reversibility:** high. Each of the three fixes is independently revertable and
  independently testable; they share a plan but not a single irreversible commit.

## Appetite

**Size:** Large

**Team:** Lead (Dev) orchestrates; 3 builders on disjoint concerns (guard-order,
convergence-latch, verification-gate) in one worktree so commits never interleave;
1 code reviewer.

**Interactions:**
- PM check-ins: 2 (sign-off on the verification-gate scope — which artifacts, and
  the false-claim consequence policy; overlap confirmation with #1926).
- Review rounds: 1-2 (router logic is high-blast-radius; a critique/review pass on
  the guard-order change and the staleness-predicate change is warranted).

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| #2003 substrate merged | `git merge-base --is-ancestor 2f324bff HEAD && echo ok` | Live-ref resolution + merge-gate + run-id ownership must exist |
| Router tests green on main | `pytest tests/unit/test_sdlc_router*.py -q` | Establish a clean baseline before touching the state machine |

## Solution

### Key Elements

**Fix 1 — Lock precedence (closes #1871).**
Move `guard_g7_plan_revising` **ahead of** `guard_g5_artifact_hash_cache` in the
`GUARDS` list, so the plan-revising lock is consulted before any cached-verdict
build fast-path. Belt-and-suspenders: also add a `plan_revising` short-circuit to
G5's READY-TO-BUILD branch itself (return None while the lock is set and
`revision_applied` is not), so the fix survives a future reorder. G7's existing
`pr_number` gate and `revision_applied` self-heal (lines 548-558) already keep the
reorder from blocking shipped PRs. The NEEDS-REVISION branch of G5 (routes to
`/do-plan`) is unaffected — routing toward revision under a revision lock is correct.

**Fix 2 — `revision_applied` convergence latch (closes #1760).**
Make `revision_applied: true` the authoritative "plan settled" signal in the
staleness path. Concretely: `_critique_verdict_is_stale` (or the row-2b guard that
calls it) must **not** report a READY-TO-BUILD verdict as stale when the only
post-verdict `/do-plan` dispatch is the revision pass that set
`revision_applied: true`. Once the latch is set, the router routes to BUILD
(row 4c) instead of re-dispatching critique. A subsequent *substantive* plan edit
(one that clears/re-opens the latch, or lands after a fresh critique) still
re-stales normally — the latch guards only the single settle-and-build transition,
not all future edits. This removes the loop at the predicate, not with a tenth row.

**Fix 3 — Stage-advance outcome verification gate (closes #1267).**
Before next-skill honors a self-attested stage-completion marker to advance the
pipeline, verify the stage's **load-bearing artifact** against the live world,
reusing #2003's live-ref helpers. Initial verifiable set (top 3, deterministic —
no LLM on the critical path):
| Stage | Claimed artifact | Live check (reused helper) |
|-------|------------------|-----------------------------|
| BUILD | PR opened | live `gh pr` resolution (`sdlc_stage_query` / `resolve_ledger_lease`) — the PR the marker claims must actually exist and be OPEN |
| BUILD/PATCH | branch pushed | `git ls-remote` / `gh` head-ref check for the claimed branch |
| PLAN | plan committed on `main` | plan file present at `docs/plans/{slug}.md` on `main` (already partly covered by the `plan_exists` context flag; extend it to a commit check) |
On mismatch (marker says completed, world says the artifact is absent), the gate
**does not advance** — it re-dispatches the same stage (bounded by the existing G4
oscillation cap, which escalates to a human after N retries) rather than shipping a
false completion. This is the same "trust live state, not the claim" principle
#2003 applied at merge, extended one gate earlier. Verification is deterministic and
side-effect-free; a check that cannot run (e.g. `gh` unavailable) fails **open**
(logs a warning, advances) so the gate never wedges the pipeline on infrastructure
flakiness — the merge-gate remains the hard backstop.

### Flow

`next-skill tick` → assemble live context (#2003 substrate) →
**[Fix 3] verify claimed stage artifacts against live world; re-dispatch same stage on mismatch** →
`evaluate_guards` with **[Fix 1] G7 before G5** →
dispatch table with **[Fix 2] revision_applied latch in staleness** → `Dispatch`/`Blocked`.

### Technical Approach

- **Guard reorder is a one-line list edit** (`GUARDS` at 640-648) plus a defensive
  `plan_revising` check inside G5's build branch. Both are covered by new unit
  tests asserting: `plan_revising=true` + cached READY-TO-BUILD + unchanged hash →
  routes to `/do-plan` (via G7), **never** `/do-build`.
- **Convergence latch is a predicate change**, not a new row. `_latest_dispatch_at`
  already exposes the `/do-plan` dispatch history; the change reads `revision_applied`
  from `meta` alongside it. The staleness function keeps its fail-safe-to-False
  contract. A unit test replays the exact #1760 loop (READY-TO-BUILD → notes-only
  revision → assert next dispatch is `/do-build`, not `/do-plan-critique`).
- **Verification gate lives in the next-skill context path** (`tools/sdlc_next_skill.py`
  or a thin `tools/sdlc_outcome_verify.py` it calls), NOT in `agent/sdlc_router.py`
  — the router core must stay import-free of `tools/` (architectural constraint,
  `tests/unit/test_architectural_constraints.py`). The router receives a
  `context["stage_artifacts_verified"]` / re-dispatch signal, keeping dependency
  inversion intact.
- **No parallel-run artifacts, no legacy shims** (NO_LEGACY_CODE): the timestamp-only
  staleness path is *replaced* by the latch-aware one, not kept alongside it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_critique_verdict_is_stale` retains its `except → False` (not-stale) fail-safe
  after the latch change; test asserts a malformed `recorded_at` still returns False.
- [ ] The verification gate's live checks fail **open** on `gh`/network error: test
  simulates a `gh` failure and asserts the gate advances with a warning rather than
  raising or wedging.
- [ ] G5's new `plan_revising` short-circuit does not raise on a missing/None
  `plan_revising` key (defaults falsy → fast-path proceeds normally).

### Empty/Invalid Input Handling
- [ ] Guard chain with empty `stage_states={}` / `meta={}` returns None (no dispatch)
  — regression guard that the reorder + G5 change did not break the empty case.
- [ ] Verification gate with no claimed artifact (stage marker absent) is a no-op —
  it verifies only *claimed* completions, never invents a check.

### Error State Rendering
- [ ] On a false-claim mismatch, the gate emits an observable log naming the stage
  and the missing artifact (not a silent re-dispatch); test asserts the log fires.
- [ ] The G4 oscillation cap still escalates to a human after N verification-driven
  re-dispatches (a persistently-false claim must Block, not loop forever).

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: add cases for G7-before-G5 ordering
  and the G5 `plan_revising` short-circuit; existing G5/G7 cases stay green.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: add the #1760 convergence
  replay (READY-TO-BUILD → notes-only revision → `/do-build`); assert no
  re-critique.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: assert the G4 cap now
  also bounds verification-driven re-dispatches and escalates to Blocked.
- [ ] `tests/unit/test_sdlc_next_skill.py` — UPDATE: assert the new verification
  step runs in context assembly and that a false BUILD claim yields re-dispatch of
  BUILD, not advance to REVIEW.
- [ ] `tests/unit/test_sdlc_verdict.py` — VERIFY UNCHANGED: `compute_plan_body_hash`
  behavior is reused, not modified; if a change is needed the design is wrong — stop.
- [ ] `tests/unit/test_architectural_constraints.py` — VERIFY UNCHANGED: the router
  must not gain a `tools/` import; the verification gate lives in `tools/`.
- [ ] `tests/**/test_pipeline_state*.py`, `test_session_completion*.py` — UPDATE only
  if the `classify_outcome` re-audit changes a covered path; otherwise assert no
  behavior change (the OUTCOME contract stays; the gate wraps it, does not replace
  it in phase 1).

Exact cases enumerated by the builder from `grep -rl` at build start; the
dispositions above are binding.

## Rabbit Holes

- **Rewriting the whole staleness model or the guard framework.** The fix is a
  predicate change + a list reorder + one gate, not a state-machine rebuild. Do not
  re-architect the guard/row split.
- **A typed per-session outcome schema (the maximal #1267 design).** #1267's open
  questions floated typed `BuildOutcome`/`MessageOutcome` fields on `AgentSession`.
  Out of scope: phase 1 is a **deterministic live-artifact check on the top 3
  side-effects**, riding existing dicts, with the OUTCOME contract retained. A typed
  schema is a separate, later question — do not add Popoto fields here.
- **LLM-mediated verification on the critical path.** The gate is deterministic
  (`gh`/`git`/filesystem). No Haiku extraction in the dispatch loop.
- **Fixing the critique skill's nit→"with concerns" mislabel.** #1760's secondary
  observation is that `/do-plan-critique` sometimes emits "with concerns" for
  nit-only findings. The convergence latch makes the *router* converge regardless;
  tightening the critique verdict emission is a valuable but separable follow-up,
  flagged as an Open Question, not folded in.
- **Touching `agent/output_router.py` / the bridge nudge loop.** That is #1926's
  file and the *bridge* router, not the SDLC router. Do not touch it.

## Risks

### Risk 1: Reordering G7 before G5 changes routing for an unforeseen state.
**Impact:** A state that relied on G5 firing first routes differently.
**Mitigation:** G7 returns None whenever `plan_revising` is falsy or `pr_number` is
set (the common cases), so for any state without an active revision lock the chain
behaves identically. The full existing `test_sdlc_router*` suite must stay green
after the reorder; any required edit to a non-#1871 case is a signal to stop and
re-examine.

### Risk 2: The convergence latch lets a genuinely-stale verdict through.
**Impact:** The router builds on a plan whose *substantive* content changed after
the verdict.
**Mitigation:** The latch is scoped to the single settle-and-build transition — it
suppresses staleness only when the post-verdict `/do-plan` dispatch is the revision
pass that set `revision_applied: true` on an existing READY-TO-BUILD verdict. A new
critique (which records a fresh verdict + hash) or a plan edit that does not set the
latch re-stales normally. Unit test covers both the converge case and the
"substantive edit still re-stales" case.

### Risk 3: The verification gate wedges the pipeline on `gh`/network flakiness.
**Impact:** A transient infra failure halts advancement.
**Mitigation:** The gate fails **open** — a check that cannot run advances with a
warning. It only *blocks* on a positive mismatch (marker claims done, world
confirms artifact absent). The merge-gate (#2003) remains the hard live backstop, so
a false BUILD claim that slips past a failed-open check is still caught before merge.

## Race Conditions

The verification gate reads live `gh`/`git` state that can change between the marker
write and the check (e.g. a PR opened milliseconds after the tick). Mitigation:
verification runs inside next-skill's context assembly, on the same tick as the
dispatch decision, using the same live snapshot — it never caches a verification
result across ticks. A false-negative (artifact appears just after the check)
self-corrects on the next tick (the re-dispatched stage finds its own work already
done and advances). No new shared mutable state is introduced; run-id ownership
(#2003) already serializes concurrent runs on the same issue.

## No-Gos (Out of Scope)

- [SEPARATE #1926] Anything in `agent/output_router.py` or the bridge nudge loop —
  a different router in a concurrent lane.
- [SEPARATE / LATER] A typed per-session outcome schema with new `AgentSession`
  Popoto fields (#1267's maximal design). Phase 1 verifies the top 3 side-effects
  deterministically on existing dicts.
- The `/do-plan-critique` verdict-labeling behavior (nit-only findings sometimes
  emitting "with concerns") is a property of the critique *skill*, not of this
  router plan's problem statement. The convergence latch (Fix 2) makes the router
  converge to BUILD regardless of that label, which fully resolves the #1760
  routing symptom. Whether to additionally tighten the critique skill's labeling is
  a judgment call surfaced for the human in Open Questions — it is not router work
  this plan is withholding.
- [DONE ON MAIN] Re-implementing run-id ownership, live-ref PR resolution, or the
  merge-gate — #2003 shipped these; this plan reuses them.

## Update System

No update system changes required — this is purely internal router/tooling logic
in `agent/sdlc_router.py` and `tools/sdlc_next_skill.py` (plus, if extracted, a new
`tools/sdlc_outcome_verify.py`). No new dependencies, config files, or env vars to
propagate. **No Popoto model/schema change** (all new signals ride the existing
`meta` / `stage_states` / `_verdicts` dicts), so `scripts/update/migrations.py`
needs no new migration. The `/update` skill picks up the code changes automatically
on the next run.

## Agent Integration

No new CLI entry point and no bridge import change. The router is reached through
the existing `sdlc-tool next-skill` surface (`tools/sdlc_next_skill.py`, already in
`pyproject.toml [project.scripts]` via the `sdlc-tool` resolver); the guard reorder,
the convergence latch, and the verification gate all live behind that existing
command. The SDLC skills (`/do-sdlc`, `/sdlc`) invoke it unchanged. Integration
coverage: `tests/integration/test_sdlc_sessionless_e2e.py` and
`test_sdlc_session_ensure_integration.py` exercise the next-skill path end-to-end
and serve as the regression surface; extend one to assert the verification gate
re-dispatches on a synthesized false BUILD claim.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` (or the router's feature doc) —
  document the guard-precedence rule (plan-revising lock before build fast-path),
  the `revision_applied` convergence latch, and the stage-advance verification gate
  with its top-3 verifiable-artifact table and fail-open contract.
- [ ] Update `.claude/skills/sdlc/SKILL.md` state-machine reference — reflect the new
  guard order (G7 before G5) and the latch/gate semantics so the skill's documented
  ground truth matches the code.
- [ ] Add or update the entry in `docs/features/README.md` index pointing to the
  router-convergence documentation.

### Inline Documentation
- [ ] Update the `GUARDS` list comment and `guard_g5_artifact_hash_cache` /
  `_critique_verdict_is_stale` docstrings to describe the new precedence and latch
  behavior (remove any now-inaccurate "G7 evaluated after G6" ordering prose).

## Success Criteria

- [ ] `plan_revising=true` + cached READY-TO-BUILD verdict + unchanged plan hash
  routes to `/do-plan` (revision), **never** `/do-build` (#1871). Unit test asserts.
- [ ] The #1760 loop replay (READY-TO-BUILD → notes-only revision that sets
  `revision_applied: true`) converges to `/do-build` in one step, with zero
  re-critique dispatches. Unit test asserts.
- [ ] A stage-completion marker whose claimed artifact is absent from the live world
  re-dispatches the same stage instead of advancing; a persistently-false claim
  escalates to Blocked via the G4 cap (#1267). Unit + integration tests assert.
- [ ] The verification gate fails **open** on `gh`/network error (advances with a
  warning). Test asserts.
- [ ] `agent/sdlc_router.py` gains **no** `tools/` import (architectural-constraint
  test stays green).
- [ ] The implementation PR body carries `Closes #1871`, `Closes #1267`,
  `Closes #1760`.
- [ ] Full `tests/unit/test_sdlc_router*.py` + `test_sdlc_next_skill.py` green;
  no unrelated router test required an edit.
- [ ] `python -m ruff format --check .` exit 0.
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

The lead (Dev) orchestrates; builders execute disjoint concerns in one worktree so
commits never interleave.

### Team Members

- **Builder (guard-order)** — `guard-builder`
  - Role: Fix 1 — reorder `GUARDS` (G7 before G5), add G5 `plan_revising`
    short-circuit; update `test_sdlc_router.py`. Agent Type: builder. Resume: true.
- **Builder (convergence-latch)** — `latch-builder`
  - Role: Fix 2 — `revision_applied`-aware staleness predicate; update
    `test_sdlc_router_decision.py` with the #1760 replay. Agent Type: builder.
    Resume: true.
- **Builder (verification-gate)** — `verify-builder`
  - Role: Fix 3 — verification gate in the next-skill context path (extract
    `tools/sdlc_outcome_verify.py` if warranted); update `test_sdlc_next_skill.py`
    + one integration test. Agent Type: builder. Resume: true.
- **Reviewer** — `router-reviewer`
  - Role: Correctness review on a high-blast-radius state-machine diff; confirm the
    architectural-constraint test stays green and no unrelated router test needed an
    edit. Agent Type: code-reviewer. Resume: true.

### Available Agent Types
Tier 1 builders + `code-reviewer`.

## Step by Step Tasks

### 1. Fix guard precedence (#1871)
- **Task ID**: build-guard-order
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_sdlc_router.py -q`
- **Assigned To**: guard-builder
- **Agent Type**: builder
- **Parallel**: true
- Reorder `GUARDS` so `guard_g7_plan_revising` precedes `guard_g5_artifact_hash_cache`;
  add a `plan_revising`+`not revision_applied` short-circuit returning None inside
  G5's READY-TO-BUILD branch. Add tests asserting the fast-path never ships while the
  lock is set. Update the `GUARDS`/G5 ordering docstrings.

### 2. Fix convergence latch (#1760)
- **Task ID**: build-convergence-latch
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_sdlc_router_decision.py -q`
- **Assigned To**: latch-builder
- **Agent Type**: builder
- **Parallel**: true
- Make `revision_applied: true` suppress staleness for the settle-and-build
  transition on a READY-TO-BUILD verdict; route to BUILD (row 4c) instead of
  re-critique. Add the #1760 loop-replay test plus the "substantive edit still
  re-stales" test. Update the `_critique_verdict_is_stale` docstring.

### 3. Fix outcome-verified advance (#1267)
- **Task ID**: build-verification-gate
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_sdlc_next_skill.py -q`
- **Assigned To**: verify-builder
- **Agent Type**: builder
- **Parallel**: true
- Add the deterministic top-3 artifact verification (PR opened, branch pushed, plan
  committed) to the next-skill context path, reusing #2003 live-ref helpers; fail
  open on infra error; re-dispatch the same stage on a positive mismatch; keep the
  router `tools/`-import-free. Add unit + one integration test.

### 4. Integrate + validate
- **Task ID**: validate-router
- **Depends On**: build-guard-order, build-convergence-latch, build-verification-gate
- **Assigned To**: router-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Run the full `test_sdlc_router*` + `test_sdlc_next_skill` + architectural-constraint
  suite. Confirm no unrelated router test required an edit; confirm the router gained
  no `tools/` import.

### 5. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: validate-router
- **Assigned To**: verify-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-tool-resolver.md`, `.claude/skills/sdlc/SKILL.md` state
  reference, and the feature index per the Documentation section.

### 6. Final review
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: router-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify all Success Criteria; confirm the PR body carries `Closes #1871`,
  `Closes #1267`, `Closes #1760`; no commented-out code, no parallel-run staleness
  path left behind.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard order fixed | `pytest tests/unit/test_sdlc_router.py -q -k "plan_revising or g5 or g7"` | exit 0 |
| Convergence latch | `pytest tests/unit/test_sdlc_router_decision.py -q -k "revision_applied or converge or stale"` | exit 0 |
| Verification gate | `pytest tests/unit/test_sdlc_next_skill.py -q -k "verif or artifact or outcome"` | exit 0 |
| Router stays tools-free | `pytest tests/unit/test_architectural_constraints.py -q` | exit 0 |
| Full router suite | `pytest tests/unit/test_sdlc_router*.py tests/unit/test_sdlc_next_skill.py -q` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Verification-gate artifact set.** Phase 1 verifies the top 3 deterministic
   side-effects (PR opened, branch pushed, plan committed on main). Is that the
   right initial set, or should TEST-actually-ran (pytest exit code) be in phase 1
   too? (It is verifiable but noisier to check live.)
2. **False-claim consequence policy.** The plan re-dispatches the same stage on a
   mismatch and lets the G4 oscillation cap escalate to Blocked after N retries.
   Is silent-re-dispatch-then-escalate the right policy, or should a first
   verified false claim escalate to the human immediately (skip the retries)?
3. **Critique nit-labeling — in or out?** #1760's secondary observation is that
   `/do-plan-critique` sometimes emits "with concerns" for nit-only findings. The
   convergence latch (Fix 2) makes the router converge to BUILD regardless, so this
   plan's router scope is complete without touching the critique skill. Do you want
   the critique-skill labeling tightened as part of this work (expanding scope into
   `.claude/skills-global/do-plan-critique/`), or is the router-side latch the
   intended resolution and the critique labeling left as-is?
