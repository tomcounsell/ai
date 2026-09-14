---
status: docs_complete
type: bug
appetite: Large
owner: Valor Engels
created: 2026-07-11
tracking: https://github.com/tomcounsell/ai/issues/2029
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-07-11T16:19:28Z
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
- the **`revision_applied` settle *event*** (timestamped via `revision_applied_at`)
  is the authoritative "plan settled" convergence latch — a READY-TO-BUILD verdict
  is never re-staled by the revision pass that set it, yet a *later* unrelated
  `/do-plan` still re-stales normally (fixes #1760);
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
  are re-audited under this framing here (READ-only; never modified by this plan).

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
predicate (make the `revision_applied` settle event the convergence latch, inside
`_critique_verdict_is_stale` itself), do not add a ninth row.
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
   (live `gh` calls). Assembles `stage_states`, `meta`, `context`. **[Fix 3] The
   context assembler now also verifies claimed stage artifacts against the live
   world and records the result in `context["stage_artifacts_verified"]` /
   `context["unverified_stage"]` — no dispatch decision is made here.**
3. **Guard chain** (`evaluate_guards`, `agent/sdlc_router.py:640-660`): walks the
   pinned `GUARDS = [g1, g2, g3, g4, g8, g7, g5, g6]`, returns the first non-None
   `Dispatch`/`Blocked`. **This is where #1871 lives** (G5 before G7, corrected to
   G7 before G5/G6) **and where Fix 3's re-dispatch *decision* lives** (`g8`
   consumes the verification flag, after G4 so the oscillation cap fires first).
4. **Dispatch table** (rows 1-10, evaluated only if guards return None): rows
   consult `_critique_verdict_is_stale` and `revision_applied`. **This is where
   #1760 lives** (row 2b staleness before rows 4a/4c).
5. **Stage advance:** when a dispatched skill completes, it writes a
   stage-completion marker (`sdlc-tool stage-marker`) and/or a verdict. next-skill
   reads those markers as truth on the next tick. **This is where #1267 lives** —
   the Fix 3 check in step (2) is what interposes a live-world check between the
   marker write and the advance.

The fix layer is (2)'s new verification, (3)'s guard reorder + `g8`, and (4)'s
latch predicate change.

## Architectural Impact

- **New dependencies:** none. Reuses #2003's `resolve_ledger_lease`,
  `merge_predicate.py`, and `sdlc_stage_query.py` live-`gh` helpers.
- **Interface changes:** `GUARDS` gains a new `g8` verification-consumer guard and
  is reordered to `[g1, g2, g3, g4, g8, g7, g5, g6]` (G7 before G5/G6). The staleness
  predicate `_critique_verdict_is_stale` gains a `meta` parameter and a
  `revision_applied_at`-aware convergence branch. A new verification function is added
  to the next-skill context-assembly path (or a thin `tools/` module it calls) that
  sets `context["stage_artifacts_verified"]` / `context["unverified_stage"]`; the
  router core stays free of `tools/` imports (the existing dependency-inversion
  boundary — `context`-supplied values only — is preserved). `tools/sdlc_stage_query.py::_compute_meta`
  gains a `revision_applied_at` parse. No Popoto model/schema change (all new signals
  ride existing `meta`/`stage_states`/`_verdicts` dicts and the plan frontmatter;
  `revision_applied_at` is a plan-frontmatter field parsed like `revision_applied`).
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
- PM check-ins: 1 (overlap confirmation with #1926 — already resolved in Freshness
  Check). The verification-gate scope sign-off previously named here is **satisfied
  in-plan**: the artifact set (top-3) and the false-claim consequence policy (G4-cap
  re-dispatch) are committed decisions in "Resolved Decisions" 1-2, so `verify-builder`
  builds against a locked spec with no mid-build human call pending. This removes the
  scope ambiguity that would otherwise argue for sequencing Fix 3 behind a sign-off —
  the three builders stay `Parallel: true`.
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
build fast-path.

**Pinned target order (binding):** `GUARDS = [g1, g2, g3, g4, g8, g7, g5, g6]`,
where `g8` is the new artifact-verification consumer guard from Fix 3 (see below).
G7 now precedes **both** G5 and G6 in list position. This is deliberate and does
**not** violate G6's "an already-mergeable PR is never blocked" property: G7's
**Gate 1** returns `None` the instant `pr_number` is set (`agent/sdlc_router.py:548-550`),
and G6 fires **only** when `pr_number` is set (`agent/sdlc_router.py:615-617`). So in
every state where G6 could dispatch `/do-merge`, G7 has already deferred at Gate 1 —
G6 still wins. The "never blocked by a stale plan_revising flag" guarantee is
preserved by G7's `pr_number` self-gate, not by list ordering. G7's docstring
(`agent/sdlc_router.py:539-541`) currently claims "evaluated AFTER G6"; that prose
becomes inaccurate after the reorder and MUST be rewritten to describe the
`pr_number` self-gate mechanism instead.

**Present-gap short-circuit in G5 (not "future-proofing").** Also add a
`plan_revising` + `not revision_applied` short-circuit returning `None` inside
G5's READY-TO-BUILD branch. This closes a **present** fallthrough, not a
hypothetical future one: G7 **Gate 6** (`agent/sdlc_router.py:594-595`) returns
`None` today whenever the lock is set, `revision_applied` is false, and a `/do-plan`
already appears in recent dispatch history — falling through to G5 **even with the
reorder in place**. Without the G5 short-circuit, that fallthrough state ships the
pre-revision design via G5's cached READY-TO-BUILD branch. The short-circuit is
load-bearing for the Gate-6 fallthrough case and is exercised by a dedicated test
(see Step 1 and Test Impact).

G7's existing `pr_number` gate and `revision_applied` self-heal (lines 548-558)
keep the reorder from blocking shipped PRs. The NEEDS-REVISION branch of G5 (routes
to `/do-plan`) is unaffected — routing toward revision under a revision lock is correct.

**Fix 2 — `revision_applied_at` event-scoped convergence latch (closes #1760).**
Make the *settle-and-build event* the authoritative "plan settled" signal in the
staleness path — **not** a sticky boolean. A bare `revision_applied` boolean is
insufficient: `/do-plan` sets `revision_applied: true` on **every** revision pass
(G7 docstring, `agent/sdlc_router.py:518-519`), and `_compute_meta` re-parses it
from the plan frontmatter on every tick (`tools/sdlc_stage_query.py:464-467`), so
the boolean cannot distinguish the settle-and-build dispatch from a later unrelated
`/do-plan`. Reading the sticky boolean would route a genuinely-stale verdict to
BUILD — the exact failure Risk 2 claims to rule out.

**Mechanism (event-scoped):** `/do-plan` writes a `revision_applied_at:` ISO-8601
frontmatter timestamp in the *same step* it sets `revision_applied: true`.
`_compute_meta` parses it into `meta["revision_applied_at"]` (a new
`_parse_revision_applied_at`, structural twin of the existing
`_parse_revision_applied`). The staleness change lands **inside**
`_critique_verdict_is_stale` itself (its signature gains `meta`; both call sites —
`agent/sdlc_router.py:721` and `:889` — pass it). The predicate suppresses staleness
**only** when `_latest_dispatch_at(stage_states, SKILL_DO_PLAN)` is **not later than**
`meta["revision_applied_at"]`. Any `/do-plan` dispatch whose `at` postdates
`revision_applied_at` re-stales normally, regardless of the boolean — so a later
unrelated revision (or a substantive edit that re-runs `/do-plan` after
`revision_applied_at` was written) does not get a free pass to BUILD. When
`revision_applied_at` is absent or unparseable, the latch is inert and the predicate
falls back to its existing timestamp-only staleness (fail-safe to the pre-fix
behavior, never wrongly "not stale").

Once the latch suppresses staleness, the router routes to BUILD (row 4c) instead of
re-dispatching critique. This removes the loop at the predicate, **inside
`_critique_verdict_is_stale`** — **not** in the row-2b wrapper
`_rule_critique_verdict_stale` (implementing it in the wrapper would be exactly the
"add a special-case row" anti-pattern that "Why Previous Fixes Failed" diagnoses). A
unit test calls `_critique_verdict_is_stale(stage_states, meta)` in isolation (not via
the row-2b wrapper) and asserts it returns `False` on the settle-and-build case,
proving the *predicate* changed.

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

**Mechanism (G4-reachable — resolves the ordering contradiction).** The
verification runs in the next-skill **context-assembly** path (`tools/`, deterministic,
router stays import-free). On a mismatch it does **not** short-circuit a dispatch
directly — that would sit *before* `evaluate_guards` and never let G4 accumulate its
streak. Instead the context assembler sets `context["stage_artifacts_verified"] = False`
and records the offending stage in `context["unverified_stage"]`. A **new guard `g8`
(`guard_g8_artifact_verification`) is inserted into `GUARDS` immediately after `g4`**
(pinned order `[g1, g2, g3, g4, g8, g7, g5, g6]`). `g8` consumes the context flag and
returns `Dispatch(skill=<same stage's skill>)` — so `evaluate_guards` runs on **every**
tick, and because **G4 precedes g8 in the list**, `guard_g4_oscillation` fires **first**
once `same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES` and returns `Blocked`. Each
`g8` re-dispatch appends a same-skill entry to `_sdlc_dispatches`, so the count
accumulates and G4 escalates a persistently-false claim to a human after the cap. This is
the same "trust live state, not the claim" principle #2003 applied at merge, extended one
gate earlier — and it makes the "bounded by the existing G4 cap" claim in Success
Criteria and Risk 3 **actually true** given the guard ordering, rather than assumed.

Verification is deterministic and side-effect-free. **Fail-open is scoped to infra
errors only** (concern-narrowing): the verification catch is limited to
`subprocess.TimeoutExpired`, `subprocess.SubprocessError`, and `OSError` (mirroring the
`subprocess.run(..., timeout=5)` pattern already in `_build_context`). On those it logs a
warning and leaves `stage_artifacts_verified` **True** (advances) so the gate never
wedges on `gh`/network flakiness — the merge-gate remains the hard backstop. An
**unexpected** exception (e.g. a `KeyError`/`TypeError` from a malformed artifact spec or
bad slug — a *logic* bug, not infra) is **not** swallowed: it surfaces at error level so a
broken gate is visible rather than silently failing open forever. A unit test asserts a
non-infra exception does **not** silently advance.

### Flow

`next-skill tick` →
assemble live context (#2003 substrate), **[Fix 3] during assembly, verify claimed
stage artifacts against the live world; on mismatch set `context["stage_artifacts_verified"]=False`
+ `context["unverified_stage"]` (no dispatch decision here)** →
`evaluate_guards` over the pinned order `[g1, g2, g3, g4, g8, g7, g5, g6]`:
**G4 (oscillation cap) runs before g8**, so a persistently-false claim Blocks;
**[Fix 3] g8** consumes the verification flag and re-dispatches the same stage;
**[Fix 1] G7 precedes G5** (and G6, self-gated on `pr_number`) →
dispatch table with **[Fix 2] `revision_applied_at` latch inside `_critique_verdict_is_stale`** →
`Dispatch`/`Blocked`.

The verification *check* happens during context assembly (before `evaluate_guards`);
the verification *decision* (re-dispatch vs. advance) happens **inside**
`evaluate_guards` at `g8`, after G4. This ordering is what makes the G4 backstop
reachable — a contradiction the prior draft's Flow left open.

### Technical Approach

- **Guard reorder is a `GUARDS` list edit** (`agent/sdlc_router.py:640-648`) to the
  pinned order `[g1, g2, g3, g4, g8, g7, g5, g6]`, plus the load-bearing
  `plan_revising` + `not revision_applied` short-circuit inside G5's build branch, plus
  a docstring rewrite of G7's now-inaccurate "evaluated AFTER G6" prose (→ describe the
  `pr_number` self-gate). Covered by new unit tests asserting: (i) `plan_revising=true`
  + cached READY-TO-BUILD + unchanged hash → routes to `/do-plan` (via G7), **never**
  `/do-build`; (ii) the **G7 Gate-6 fallthrough** case → G7 returns `None` and it is
  **G5's short-circuit** that prevents `/do-build`; (iii) a **terminal-merge-ready state
  with `plan_revising=true`** → G6 still dispatches `/do-merge` (G7 defers at Gate 1 on
  `pr_number`), confirming the reorder does not cross G6.
- **Convergence latch is a predicate change inside `_critique_verdict_is_stale`**, not a
  new row and not in the row-2b wrapper. The function's signature gains `meta`; both call
  sites (`agent/sdlc_router.py:721`, `:889`) pass it. `_latest_dispatch_at` already
  exposes the `/do-plan` dispatch history; the change compares it against the new
  `meta["revision_applied_at"]` event timestamp (parsed by
  `tools/sdlc_stage_query.py::_parse_revision_applied_at` from the plan frontmatter, which
  `/do-plan` now writes alongside `revision_applied: true`). Suppress staleness **only**
  when `_latest_dispatch_at(stage_states, SKILL_DO_PLAN)` is not later than
  `revision_applied_at`. The function keeps its fail-safe-to-False contract on parse
  error, and is inert (falls back to timestamp-only staleness) when `revision_applied_at`
  is absent. Unit tests: (a) the exact #1760 loop (READY-TO-BUILD → notes-only revision
  that sets `revision_applied_at` → assert next dispatch is `/do-build`, not
  `/do-plan-critique`); (b) `_critique_verdict_is_stale(stage_states, meta)` called in
  isolation returns `False` on the settle-and-build case; (c) a **later unrelated
  `/do-plan` dispatch postdating `revision_applied_at` re-stales normally**.
- **Verification gate lives in the next-skill context path** (`tools/sdlc_next_skill.py`
  or a thin `tools/sdlc_outcome_verify.py` it calls), NOT in `agent/sdlc_router.py`
  — the router core must stay import-free of `tools/` (architectural constraint,
  `tests/unit/test_architectural_constraints.py`). The tools-side check sets
  `context["stage_artifacts_verified"]` + `context["unverified_stage"]`; the router-side
  **`guard_g8_artifact_verification`** (in `agent/sdlc_router.py`, no `tools/` import)
  consumes those context values and returns the re-dispatch `Dispatch`. Dependency
  inversion is intact: the router reads only `context`-supplied values. The fail-open
  catch in the tools-side check is scoped to `subprocess.TimeoutExpired` /
  `subprocess.SubprocessError` / `OSError`; unexpected exceptions surface at error level.
  The #1267 re-audit of `classify_outcome` / `session_completion.py` is **read-only** —
  this plan never modifies those modules.
- **No parallel-run artifacts, no legacy shims** (NO_LEGACY_CODE): the timestamp-only
  staleness path is *replaced* by the latch-aware one, not kept alongside it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_critique_verdict_is_stale(stage_states, meta)` retains its `except → False`
  (not-stale) fail-safe after the latch change; test asserts a malformed `recorded_at`
  still returns False, and a malformed/absent `revision_applied_at` leaves the predicate
  at its timestamp-only staleness (latch inert, never wrongly "not stale").
- [ ] The verification check fails **open only on infra errors**: test simulates a
  `subprocess.TimeoutExpired` / `OSError` from the `gh`/`git` call and asserts the check
  leaves `stage_artifacts_verified=True` (advances) with a warning.
- [ ] The verification check does **not** silently fail open on a **logic** error: test
  injects a non-infra exception (e.g. `TypeError` from a malformed artifact spec) and
  asserts it surfaces at error level rather than advancing — the narrowed catch
  (`TimeoutExpired`/`SubprocessError`/`OSError`) does not swallow it.
- [ ] G5's new `plan_revising` short-circuit does not raise on a missing/None
  `plan_revising` key (defaults falsy → fast-path proceeds normally).

### Empty/Invalid Input Handling
- [ ] Guard chain with empty `stage_states={}` / `meta={}` returns None (no dispatch)
  — regression guard that the reorder + `g8` + G5 change did not break the empty case.
- [ ] Verification check with no claimed artifact (stage marker absent) is a no-op —
  it verifies only *claimed* completions, never invents a check, and leaves
  `stage_artifacts_verified` unset/True so `g8` does not fire.

### Error State Rendering
- [ ] On a false-claim mismatch, the check emits an observable log naming the stage
  and the missing artifact (not a silent re-dispatch); test asserts the log fires.
- [ ] The G4 oscillation cap escalates to Blocked after `MAX_SAME_STAGE_DISPATCHES`
  verification-driven re-dispatches. Test drives `evaluate_guards` with
  `stage_artifacts_verified=False` and `same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES`
  and asserts **G4** returns `Blocked` (fires before `g8` given the pinned order
  `[g1,g2,g3,g4,g8,...]`), proving a persistently-false claim Blocks rather than looping
  forever.

## Test Impact

- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: (i) G7-before-G5 ordering
  (`plan_revising=true` + cached READY-TO-BUILD → `/do-plan` via G7, never `/do-build`);
  (ii) **G7 Gate-6 fallthrough** — `plan_revising=True`, `revision_applied=False`,
  `last_dispatched_skill != "/do-plan-critique"`, `_sdlc_dispatches` holding a `/do-plan`
  within the last `MAX_PLAN_REVISING_DISPATCHES+1`, cached
  `_verdicts.CRITIQUE.artifact_hash == context["current_plan_hash"]`, verdict READY_TO_BUILD,
  no `pr_number` → assert **G7 returns `None` (Gate 6)** and it is **G5's short-circuit**
  that prevents `/do-build`; (iii) **G6 not crossed** — terminal-merge-ready state
  (`pr_number` set, `pr_merge_state=CLEAN`, `ci_all_passing=True`, `DOCS=completed`,
  REVIEW APPROVED) **with `plan_revising=true`** → assert G6 still dispatches `/do-merge`
  (G7 defers at Gate 1 on `pr_number`); (iv) `g8` re-dispatch fires only when
  `stage_artifacts_verified=False`; existing G5/G7 cases stay green.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: (i) the #1760 convergence replay
  (READY-TO-BUILD → notes-only revision that sets `revision_applied_at` → `/do-build`,
  no re-critique); (ii) `_critique_verdict_is_stale(stage_states, meta)` called **in
  isolation** returns `False` on the settle-and-build case (predicate changed, not a
  caller); (iii) a **later unrelated `/do-plan` dispatch postdating `revision_applied_at`
  re-stales normally** (genuinely-stale verdict is NOT routed to BUILD).
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: assert the G4 cap bounds
  verification-driven re-dispatches — with `stage_artifacts_verified=False` and
  `same_stage_dispatch_count >= MAX_SAME_STAGE_DISPATCHES`, **G4 fires before `g8`** (pinned
  order) and returns `Blocked`.
- [ ] `tests/unit/test_sdlc_next_skill.py` — UPDATE: (i) the verification check runs in
  context assembly and a false BUILD claim sets `stage_artifacts_verified=False` +
  `unverified_stage`, so `g8` re-dispatches BUILD rather than advancing to REVIEW;
  (ii) fail-**open** on infra error (`subprocess.TimeoutExpired`/`OSError` → advances with
  warning); (iii) a **non-infra exception (`TypeError`) does NOT silently advance** (the
  narrowed catch does not swallow it).
- [ ] `tests/unit/test_sdlc_verdict.py` — VERIFY UNCHANGED: `compute_plan_body_hash`
  behavior is reused, not modified; if a change is needed the design is wrong — stop.
- [ ] `tests/unit/test_sdlc_stage_query.py` — UPDATE: assert `_compute_meta` parses
  `revision_applied_at` from the plan frontmatter into `meta["revision_applied_at"]`
  (absent/unparseable → `None`, latch inert).
- [ ] `tests/unit/test_architectural_constraints.py` — VERIFY UNCHANGED: the router
  must not gain a `tools/` import; `g8` reads only `context`-supplied values.
- [ ] `tests/**/test_pipeline_state*.py`, `test_session_completion*.py` — VERIFY UNCHANGED:
  the `classify_outcome` re-audit is read-only; the OUTCOME contract stays; the gate wraps
  it, does not replace it in phase 1. Assert no behavior change.

Exact cases enumerated by the builder from `grep -rl` at build start; the
dispositions above are binding.

## Rabbit Holes

- **Rewriting the whole staleness model or the guard framework.** The fix is a
  predicate change + a list reorder + one new guard, not a state-machine rebuild. Do not
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
  recorded as a decided No-Go (see Resolved Decisions 3), not folded in.
- **Touching `agent/output_router.py` / the bridge nudge loop.** That is #1926's
  file and the *bridge* router, not the SDLC router. Do not touch it.
- **Modifying `classify_outcome` / `session_completion.py`.** The #1267 re-audit only
  READS these to confirm the self-attestation surface; the verification gate wraps them
  from the next-skill context path. Editing them is out of scope.

## Risks

### Risk 1: Reordering G7 before G5 **and G6** changes routing for an unforeseen state.
**Impact:** The pinned order `[g1,g2,g3,g4,g8,g7,g5,g6]` moves G7 ahead of both G5 and
G6. A state that relied on G5 or G6 firing first could route differently — most
sharply, an already-mergeable PR could be blocked by a stale `plan_revising` flag,
violating G6's documented invariant.
**Mitigation (G7-vs-G5):** G7 returns None whenever `plan_revising` is falsy or
`pr_number` is set (the common cases), so for any state without an active revision lock
the chain behaves identically to today.
**Mitigation (G7-vs-G6, the newly-analyzed crossing):** G6 fires **only** when
`pr_number` is set (`agent/sdlc_router.py:615-617`); G7 **Gate 1** returns `None` the
instant `pr_number` is set (`:548-550`). Therefore in every state where G6 could
dispatch `/do-merge`, G7 has already deferred — G6 still wins, and the "already-mergeable
PR never blocked" property holds by construction. This is no longer an unverified
premise: Test Impact adds a **terminal-merge-ready + `plan_revising=true`** case asserting
G6 still dispatches `/do-merge` after the reorder. The full existing `test_sdlc_router*`
suite must stay green; any required edit to a non-#1871 case is a signal to stop and
re-examine.

### Risk 2: The convergence latch lets a genuinely-stale verdict through.
**Impact:** The router builds on a plan whose *substantive* content changed after
the verdict.
**Mitigation:** The latch is **event-scoped, not a sticky boolean** — this is the
correction from the critique. It suppresses staleness only when
`_latest_dispatch_at(stage_states, SKILL_DO_PLAN)` is **not later than**
`meta["revision_applied_at"]` (the timestamp `/do-plan` wrote when it settled the plan).
Any `/do-plan` dispatch whose `at` postdates `revision_applied_at` — a later unrelated
revision, or a substantive edit that re-runs `/do-plan` — re-stales normally, so a
genuinely-stale verdict is never treated as fresh merely because `revision_applied`
happens to still read `true`. A new critique (fresh verdict + hash) also re-stales. Unit
tests cover the converge case, the isolated-predicate case, **and** the "later unrelated
`/do-plan` dispatch re-stales" case.

### Risk 3: The verification gate wedges the pipeline on `gh`/network flakiness.
**Impact:** A transient infra failure halts advancement.
**Mitigation:** The gate fails **open on infra errors only** — a check that cannot run
(`subprocess.TimeoutExpired`/`SubprocessError`/`OSError`) advances with a warning. It
only *blocks* on a positive mismatch (marker claims done, world confirms artifact
absent), and a persistently-false claim is bounded by G4 (which fires before `g8`). A
logic bug is NOT swallowed (it surfaces at error level). The merge-gate (#2003) remains
the hard live backstop, so a false BUILD claim that slips past a failed-open check is
still caught before merge.

## Race Conditions

The verification gate reads live `gh`/`git` state that can change between the marker
write and the check (e.g. a PR opened milliseconds after the tick). Mitigation:
verification runs inside next-skill's context assembly, on the same tick as the
dispatch decision, using the same live snapshot — it never caches a verification
result across ticks. A false-negative (artifact appears just after the check)
self-corrects on the next tick (the re-dispatched stage finds its own work already
done and advances). This same-tick race is also why the false-claim policy is a
bounded re-dispatch rather than a first-mismatch human escalation (Resolved
Decision 2). No new shared mutable state is introduced; run-id ownership
(#2003) already serializes concurrent runs on the same issue.

## No-Gos (Out of Scope)

- [SEPARATE #1926] Anything in `agent/output_router.py` or the bridge nudge loop —
  a different router in a concurrent lane.
- [SEPARATE / LATER] A typed per-session outcome schema with new `AgentSession`
  Popoto fields (#1267's maximal design). Phase 1 verifies the top 3 side-effects
  deterministically on existing dicts.
- [DO NOT MODIFY] `agent/pipeline_state.py::classify_outcome` and
  `agent/session_completion.py` — the #1267 re-audit is read-only; the gate wraps the
  self-attestation surface from the next-skill context path.
- [DECIDED OUT — Resolved Decision 3] The `/do-plan-critique` verdict-labeling behavior
  (nit-only findings sometimes emitting "with concerns") is a property of the critique
  *skill*, not of this router plan's problem statement. The convergence latch (Fix 2)
  makes the router converge to BUILD regardless of that label, which fully resolves the
  #1760 routing symptom. Tightening the critique skill's labeling is a decided follow-up,
  not router work this plan is withholding.
- [DONE ON MAIN] Re-implementing run-id ownership, live-ref PR resolution, or the
  merge-gate — #2003 shipped these; this plan reuses them.

## Update System

No update system changes required — this is purely internal router/tooling logic
in `agent/sdlc_router.py`, `tools/sdlc_next_skill.py`, and `tools/sdlc_stage_query.py`
(plus, if extracted, a new `tools/sdlc_outcome_verify.py`). No new dependencies, config
files, or env vars to propagate. **No Popoto model/schema change** (all new signals ride
the existing `meta` / `stage_states` / `_verdicts` dicts and the plan frontmatter), so
`scripts/update/migrations.py` needs no new migration. The `/do-plan` convergence step
gains a one-line `revision_applied_at:` frontmatter write; that is a skill-convention
change, not an update-script change. The `/update` skill picks up the code changes
automatically on the next run.

## Agent Integration

No new CLI entry point and no bridge import change. The router is reached through
the existing `sdlc-tool next-skill` surface (`tools/sdlc_next_skill.py`, already in
`pyproject.toml [project.scripts]` via the `sdlc-tool` resolver); the guard reorder,
the `g8` verification guard, the convergence latch, and the verification gate all live
behind that existing command. The SDLC skills (`/do-sdlc`, `/sdlc`) invoke it unchanged;
`/do-plan` additionally writes `revision_applied_at:` alongside `revision_applied: true`
in its convergence step (skill-convention update in `docs/sdlc/do-plan.md`). Integration
coverage: `tests/integration/test_sdlc_sessionless_e2e.py` and
`test_sdlc_session_ensure_integration.py` exercise the next-skill path end-to-end
and serve as the regression surface; extend one to assert the verification gate
re-dispatches on a synthesized false BUILD claim.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-tool-resolver.md` (or the router's feature doc) —
  document the guard-precedence rule (plan-revising lock before build fast-path), the
  pinned `GUARDS` order `[g1,g2,g3,g4,g8,g7,g5,g6]` with the G7 `pr_number` self-gate that
  preserves G6, the `revision_applied_at` event-scoped convergence latch, and the
  stage-advance verification gate with its top-3 verifiable-artifact table and fail-open
  (infra-only) contract.
- [ ] Update `.claude/skills/sdlc/SKILL.md` state-machine reference — reflect the new
  guard order (G7 before G5/G6, `g8` after G4) and the latch/gate semantics so the skill's
  documented ground truth matches the code.
- [ ] Update `docs/sdlc/do-plan.md` — document the `revision_applied_at:` frontmatter
  write in the convergence step (the skill-convention half of Fix 2).
- [ ] Add or update the entry in `docs/features/README.md` index pointing to the
  router-convergence documentation.

### Inline Documentation
- [ ] Update the `GUARDS` list comment and `guard_g5_artifact_hash_cache` /
  `guard_g7_plan_revising` / `_critique_verdict_is_stale` docstrings to describe the new
  precedence and latch behavior. In particular, **rewrite G7's "evaluated AFTER G6"
  prose** (`agent/sdlc_router.py:539-541`) — after the reorder G7 precedes G6 in the list;
  the "never-blocked" property now rests on G7's Gate-1 `pr_number` self-gate, and the
  docstring must say so. Document `guard_g8_artifact_verification`'s contract (consumes
  `context["stage_artifacts_verified"]`, positioned after G4 so the oscillation cap fires
  first).

## Success Criteria

- [ ] `plan_revising=true` + cached READY-TO-BUILD verdict + unchanged plan hash
  routes to `/do-plan` (revision), **never** `/do-build` (#1871). Unit test asserts.
- [ ] The **G7 Gate-6 fallthrough** state (lock set, `revision_applied` false, `/do-plan`
  in recent history, cached READY-TO-BUILD, no PR) is prevented from `/do-build` by **G5's
  short-circuit** (G7 returns None). Unit test asserts.
- [ ] A **terminal-merge-ready state with `plan_revising=true`** still dispatches
  `/do-merge` via G6 (G7 defers at Gate 1 on `pr_number`) — the reorder does not cross G6.
  Unit test asserts.
- [ ] The #1760 loop replay (READY-TO-BUILD → notes-only revision that sets
  `revision_applied_at`) converges to `/do-build` in one step, with zero re-critique
  dispatches; and a later unrelated `/do-plan` dispatch postdating `revision_applied_at`
  re-stales normally. Unit tests assert both.
- [ ] A stage-completion marker whose claimed artifact is absent from the live world
  re-dispatches the same stage instead of advancing; a persistently-false claim
  escalates to Blocked via the G4 cap **(which fires before `g8`)** (#1267). Unit +
  integration tests assert.
- [ ] The verification gate fails **open on infra errors only** (`subprocess`/`OSError`
  → advances with a warning); a non-infra logic exception does **not** silently advance.
  Tests assert both.
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
  - Role: Fix 1 — reorder `GUARDS` to `[g1,g2,g3,g4,g8,g7,g5,g6]`, add G5 `plan_revising`
    + `not revision_applied` short-circuit, rewrite G7's "evaluated AFTER G6" docstring;
    update `test_sdlc_router.py` with the G7-vs-G5, Gate-6-fallthrough, and G6-not-crossed
    cases. Agent Type: builder. Resume: true.
- **Builder (convergence-latch)** — `latch-builder`
  - Role: Fix 2 — `revision_applied_at` event-scoped staleness change **inside
    `_critique_verdict_is_stale`** (signature gains `meta`; update both call sites),
    add `_parse_revision_applied_at` to `tools/sdlc_stage_query.py`; update
    `test_sdlc_router_decision.py` (loop replay, isolated-predicate, later-dispatch
    re-stale) and `test_sdlc_stage_query.py`. Agent Type: builder. Resume: true.
- **Builder (verification-gate)** — `verify-builder`
  - Role: Fix 3 — verification in the next-skill context path (extract
    `tools/sdlc_outcome_verify.py` if warranted) setting
    `context["stage_artifacts_verified"]`/`unverified_stage`, plus
    `guard_g8_artifact_verification` in the router (positioned after G4); narrowed
    infra-only catch; update `test_sdlc_next_skill.py`, `test_sdlc_router_oscillation.py`
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
- Reorder `GUARDS` to the pinned `[g1, g2, g3, g4, g8, g7, g5, g6]` (`g8` is added by
  Task 3; coordinate the list edit so the final order matches — if Task 3 lands first,
  insert G7 before G5/G6 around the existing `g8`). Add a `plan_revising` +
  `not revision_applied` short-circuit returning None inside G5's READY-TO-BUILD branch.
  Rewrite G7's "evaluated AFTER G6" docstring to describe the `pr_number` Gate-1 self-gate.
  Add tests: (i) fast-path never ships while the lock is set; (ii) the **Gate-6
  fallthrough** case (G7 returns None, G5 short-circuit prevents `/do-build`); (iii) the
  **terminal-merge-ready + `plan_revising=true`** case (G6 still dispatches `/do-merge`).

### 2. Fix convergence latch (#1760)
- **Task ID**: build-convergence-latch
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_sdlc_router_decision.py -q`
- **Assigned To**: latch-builder
- **Agent Type**: builder
- **Parallel**: true
- Change `_critique_verdict_is_stale` **itself** (not the row-2b wrapper): add a `meta`
  parameter and suppress staleness only when `_latest_dispatch_at(stage_states,
  SKILL_DO_PLAN)` is not later than `meta["revision_applied_at"]`; update both call sites
  (`:721`, `:889`). Add `_parse_revision_applied_at` to `tools/sdlc_stage_query.py::_compute_meta`.
  Add the #1760 loop-replay test, the isolated-predicate test, and the "later unrelated
  `/do-plan` re-stales" test. Update the `_critique_verdict_is_stale` docstring.

### 3. Fix outcome-verified advance (#1267)
- **Task ID**: build-verification-gate
- **Depends On**: none
- **Parallel**: true (scope is locked in Resolved Decisions 1-2 — no pending sign-off)
- **Validates**: `pytest tests/unit/test_sdlc_next_skill.py -q`
- **Assigned To**: verify-builder
- **Agent Type**: builder
- Add the deterministic top-3 artifact verification (PR opened, branch pushed, plan
  committed) to the next-skill context path, reusing #2003 live-ref helpers; set
  `context["stage_artifacts_verified"]` / `context["unverified_stage"]` (no dispatch
  decision here). Add `guard_g8_artifact_verification` to the router **positioned after
  G4** so the oscillation cap fires first; it consumes the context flag and re-dispatches
  the same stage's skill. Scope the fail-open catch to
  `subprocess.TimeoutExpired`/`SubprocessError`/`OSError` (let logic exceptions surface).
  Keep the router `tools/`-import-free. The `classify_outcome` re-audit is read-only.
  Add unit tests (false-claim re-dispatch, infra fail-open, non-infra no-swallow, G4-cap
  Blocks before `g8`) + one integration test.

### 4. Integrate + validate
- **Task ID**: validate-router
- **Depends On**: build-guard-order, build-convergence-latch, build-verification-gate
- **Assigned To**: router-reviewer
- **Agent Type**: validator
- **Parallel**: false
- Confirm the final `GUARDS` order is exactly `[g1, g2, g3, g4, g8, g7, g5, g6]`. Run the
  full `test_sdlc_router*` + `test_sdlc_next_skill` + `test_sdlc_stage_query` +
  architectural-constraint suite. Confirm no unrelated router test required an edit;
  confirm the router gained no `tools/` import.

### 5. Documentation cascade
- **Task ID**: document-feature
- **Depends On**: validate-router
- **Assigned To**: verify-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/sdlc-tool-resolver.md`, `.claude/skills/sdlc/SKILL.md` state
  reference, `docs/sdlc/do-plan.md` (the `revision_applied_at` convention), and the
  feature index per the Documentation section.

### 6. Final review
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: router-reviewer
- **Agent Type**: code-reviewer
- **Parallel**: false
- Verify all Success Criteria; confirm the PR body carries `Closes #1871`,
  `Closes #1267`, `Closes #1760`; no commented-out code, no parallel-run staleness
  path left behind, `classify_outcome`/`session_completion.py` untouched.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard order fixed | `pytest tests/unit/test_sdlc_router.py -q -k "plan_revising or g5 or g6 or g7 or g8"` | exit 0 |
| Convergence latch | `pytest tests/unit/test_sdlc_router_decision.py -q -k "revision_applied or converge or stale"` | exit 0 |
| Verification gate | `pytest tests/unit/test_sdlc_next_skill.py -q -k "verif or artifact or outcome"` | exit 0 |
| Router stays tools-free | `pytest tests/unit/test_architectural_constraints.py -q` | exit 0 |
| Full router suite | `pytest tests/unit/test_sdlc_router*.py tests/unit/test_sdlc_next_skill.py tests/unit/test_sdlc_stage_query.py -q` | exit 0 |
| Format clean | `python -m ruff format --check .` | exit 0 |

## Critique Results

**Verdict: NEEDS REVISION** (war room, FULL depth — Risk & Robustness, Scope & Value, History & Consistency). 4 blockers, 4 concerns. Recorded 2026-07-11, run `9cfd4ffb`. **Revision applied 2026-07-11 (this pass): all 4 blockers and all 4 concerns addressed — see the "Addressed By" column.**

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency | Fix 3 re-dispatch sits BEFORE `evaluate_guards` in the Flow (PLAN.md:288-291), but `guard_g4_oscillation` — the named loop-bound backstop — lives inside `evaluate_guards`/`GUARDS`, only reached AFTER Fix 3 short-circuits. On a persistent false claim G4 never runs, so it cannot Block. Contradicts Success Criteria (482-484) and Risk 3. Technical Approach (304-309) says the router receives a `context` flag, contradicting Flow's "next-skill re-dispatches directly." | **RESOLVED (option a):** Fix 3 no longer short-circuits. The context assembler sets `context["stage_artifacts_verified"]=False` + `unverified_stage`; a new `guard_g8_artifact_verification` inserted into `GUARDS` **after G4** consumes it and returns `Dispatch(same skill)`. G4 fires first, so the cap Blocks a persistent false claim. Flow, Technical Approach, Success Criteria, Risk 3, and Test Impact are now internally consistent (all reference the `g8`-after-G4 mechanism). | Either (a) have Fix 3 set `context["stage_artifacts_verified"]=False` and add a guard in `GUARDS` (agent/sdlc_router.py:640-648) that consumes it and returns `Dispatch(same skill)` so `evaluate_guards`/G4 run every tick; or (b) if Fix 3 must short-circuit pre-guard, have it write `meta["same_stage_dispatch_count"]` (the field G4 reads) so G4 fires on the next tick reaching `evaluate_guards`. Spell out the actual mechanism — "bounded by the existing G4 cap" is not automatically true given the stated ordering. |
| BLOCKER | Risk & Robustness | Fix 2's latch reads a bare `revision_applied` boolean from `meta` (Technical Approach 299-302), but `/do-plan` sets that flag on every revision pass (G7 docstring, agent/sdlc_router.py:518-519), so the latch cannot distinguish the settle-and-build dispatch from a later unrelated `/do-plan`. A genuinely-stale verdict recorded before that later dispatch would be treated as fresh and routed to BUILD — the exact failure Risk 2 claims to rule out. | **RESOLVED:** Latch is now event-scoped. `/do-plan` writes `revision_applied_at:` (parsed into `meta["revision_applied_at"]`); the predicate suppresses staleness only when `_latest_dispatch_at(…, SKILL_DO_PLAN)` is not later than `revision_applied_at`. A later `/do-plan` postdating it re-stales normally. Fix 2 + Technical Approach + Risk 2 + Test Impact all updated; a "later unrelated dispatch re-stales" test is added. | Scope the latch to an event, not a sticky flag: add `meta["revision_applied_at"]` and only suppress staleness when `_latest_dispatch_at(stage_states, SKILL_DO_PLAN)` is NOT later than `revision_applied_at`; any `/do-plan` dispatch after that timestamp re-stales normally regardless of the boolean. |
| BLOCKER | Risk & Robustness + History & Consistency (cross-validated) | Fix 1 is specified only as "move G7 ahead of G5"; the natural `GUARDS` edit `[g1,g2,g3,g4,g7,g5,g6]` also moves G7 ahead of G6, contradicting G7's documented invariant "evaluated AFTER G6 ... so an already-mergeable PR is never blocked by a stale plan_revising flag" (agent/sdlc_router.py:539-541). Risk 1 analyzes only G7-vs-G5; Test Impact adds no G6 case. Safety rests on the unverified premise that every G6-firing state has `pr_number` set (so G7 gate-1 at 548-550 defers). | **RESOLVED:** Order pinned as `[g1,g2,g3,g4,g8,g7,g5,g6]` in Fix 1. The G7-vs-G6 crossing is analyzed explicitly: G6 fires only with `pr_number` set, and G7 Gate 1 defers on `pr_number`, so G6 still wins. Risk 1 now covers G7-vs-G6; Test Impact + Success Criteria add a terminal-merge-ready + `plan_revising=true` case; the inaccurate "evaluated AFTER G6" docstring is scheduled for rewrite. | Pin the target order explicitly as `[g1,g2,g3,g4,g7,g5,g6]` in Fix 1's spec. Add a Test Impact case: construct a terminal-merge-ready state (G6's condition) with `plan_revising=true` and assert G6's dispatch still wins after the reorder — confirming G7's `pr_number` self-gate preserves the "never blocked" property now that G7 precedes G6. |
| BLOCKER | Scope & Value | Plan is `status: Ready` with all three builders `Parallel: true` from Step 1, yet Open Question 2 asks the human whether "silent-re-dispatch-then-escalate via the G4 cap" is the right false-claim policy — the exact policy Fix 3 and Success Criteria bullet 3 already lock in as binding, testable behavior. `verify-builder` would assert a policy the plan simultaneously flags as unresolved. | **RESOLVED:** Open Questions replaced by "Resolved Decisions". OQ2 → Resolved Decision 2 declares silent-re-dispatch-then-escalate the **committed phase-1 default** (with same-tick-race rationale), so no policy is pending while builders assert it. OQ1 (artifact set) and OQ3 (critique labeling) are likewise decided. | Resolve OQ2 before build (or state the G4-cap policy IS the committed phase-1 default and OQ2 is a later-follow-up question only). If the answer becomes "escalate on first verified false claim," Success Criteria bullet 3 and the Step-3 test need the gate to return `Blocked` on the first mismatch rather than a G4-bounded re-dispatch — a materially different code path, so answering it after Step 3 starts is rework, not augmentation. |
| CONCERN | Risk & Robustness | Fix 3's fail-open is intended for infra/network errors only, but the module it extends already uses blanket `except Exception` (tools/sdlc_next_skill.py:119-120, 145-146; agent/sdlc_router.py:873-874). If the gate follows that pattern, a logic bug (bad slug, malformed `gh`/`git` call, `KeyError` on an unexpected artifact shape) is indistinguishable from infra flakiness and silently fails open forever, defeating the gate. | **RESOLVED:** Fail-open catch narrowed to `subprocess.TimeoutExpired`/`SubprocessError`/`OSError`; unexpected exceptions surface at error level. Failure Path + Test Impact add a non-infra `TypeError`-does-not-advance test. | Scope the fail-open catch to `subprocess.TimeoutExpired` / `subprocess.SubprocessError` / `OSError` (mirroring the `subprocess.run(..., timeout=5)` pattern in `_build_context`); let unexpected exceptions surface at error level or Block. Add a unit test asserting a non-infra exception (e.g. `TypeError` from a malformed artifact spec) does NOT silently advance. |
| CONCERN | History & Consistency | Fix 2's spec permits landing the change in "`_critique_verdict_is_stale` (**or the row-2b guard that calls it**)" (PLAN.md:258-259) — implementing inside `_rule_critique_verdict_stale` (row 2b) is exactly the "add a special-case row" pattern "Why Previous Fixes Failed" (166-168) diagnoses as the root cause of every prior failed fix. | **RESOLVED:** The "(or the row-2b guard)" alternative is dropped; the change is mandated **inside `_critique_verdict_is_stale`**. An isolated-predicate test (not via the wrapper) is added to Fix 2 + Test Impact + Success Criteria. | Drop the "(or the row-2b guard that calls it)" alternative; mandate the change lands inside `_critique_verdict_is_stale` (agent/sdlc_router.py:841-874) itself. Add a unit test calling `_critique_verdict_is_stale` in isolation (not via the row-2b wrapper) asserting it returns False on the settle-and-build case — proving the predicate changed, not a caller. |
| CONCERN | Scope & Value | Fix 3 introduces a new live-verification subsystem (`tools/sdlc_next_skill.py::_build_context` + possibly a new `tools/sdlc_outcome_verify.py`) with its own unresolved Open Questions 1-2 — a different size and shape of change than Fix 1/Fix 2's same-file guard-order/predicate edits. Bundling all three as parallel disjoint-concern builders under one "Large" plan is questionable. | **RESOLVED:** With OQ1-2 now decided in Resolved Decisions, the scope ambiguity that motivated sequencing is gone. Appetite updated (PM check-in for scope sign-off is satisfied in-plan); Step 3 explicitly notes its scope is locked, so `verify-builder` stays `Parallel: true` against a fixed spec. | If kept bundled, sequence `verify-builder`'s Step 3 behind the "PM check-in: sign-off on verification-gate scope" already named in Appetite — change Step 3's `Parallel: true` to depend on that sign-off rather than starting all three builders concurrently. Otherwise split Fix 3 into its own plan once OQ1-2 resolve. |
| CONCERN | Scope & Value | The G5 `plan_revising` short-circuit is justified only as "future-proofing" ("survives a future reorder"), but it closes a PRESENT gap: G7 Gate 6 (agent/sdlc_router.py:594-595) already returns `None` today whenever the lock is set, `revision_applied` is false, and a `/do-plan` appears in recent history — falling through to G5 even with the reorder in place. Step 1's named test only covers the common Gate-4 case, so the short-circuit's necessity isn't exercised. | **RESOLVED:** Fix 1 now frames the short-circuit as closing a **present** Gate-6 fallthrough (not future-proofing). Step 1 + Test Impact + Success Criteria add the Gate-6 fallthrough test (G7 returns None; G5 short-circuit prevents `/do-build`). | Add the Gate-6 fallthrough test to Step 1: `plan_revising=True`, `revision_applied=False`, `last_dispatched_skill != "/do-plan-critique"`, `_sdlc_dispatches` containing a `/do-plan` entry within the last `MAX_PLAN_REVISING_DISPATCHES+1` (=3), cached `_verdicts.CRITIQUE.artifact_hash == context["current_plan_hash"]`, verdict text READY_TO_BUILD, no `pr_number` — assert G7 returns `None` (Gate 6) and it is G5's short-circuit, not G7, that prevents `/do-build`. |

### Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS | Documentation, Update System, Agent Integration, Test Impact all present and substantive |
| Task numbering | PASS | Tasks 1-6 contiguous; dependencies valid (4←1,2,3; 5←4; 6←5); no cycles |
| Dependencies valid | PASS | All `Depends On` reference existing task IDs |
| File paths exist | PASS | All referenced source files, test files, doc files exist on `main` |
| Prerequisites met | PASS | #2003 (`2f324bff`) is an ancestor of HEAD; router tests baseline assumed green |
| Cross-references | PASS | Cited line numbers (G5=419, G7=511, GUARDS=640, staleness=841, row 2b=877) all verified exact against `main`; router carries no `tools/` import |
| Popoto migration | N/A | Plan asserts no model/schema change (rides existing `meta`/`stage_states`/`_verdicts` dicts + plan frontmatter) — verified consistent with No-Gos |

---

## Resolved Decisions (formerly Open Questions)

These are **committed phase-1 defaults**, not open questions. They are stated here
so the plan carries no ambiguity into a `status: Ready` parallel build — every
behavior Fix 3 and the Success Criteria assert is a decided policy, not a pending
human call.

1. **Verification-gate artifact set — DECIDED.** Phase 1 verifies exactly the top 3
   deterministic side-effects (PR opened, branch pushed, plan committed on main).
   TEST-actually-ran (pytest exit code) is **out of phase 1** — it is verifiable but
   noisier to check live, and the merge-gate (#2003) already backstops a false green.
   Adding it is a separate later question, not withheld router work.
2. **False-claim consequence policy — DECIDED (blocker-4 resolution).** On a verified
   false claim (marker says completed, world says the artifact is absent), the gate
   **re-dispatches the same stage** and lets the **G4 oscillation cap** escalate to
   `Blocked` after `MAX_SAME_STAGE_DISPATCHES` retries. **Silent-re-dispatch-then-escalate
   is the committed phase-1 default.** This is deliberately *not* "escalate to a human on
   the first verified false claim": a single mismatch is most often a same-tick race (the
   artifact appears milliseconds later, see Race Conditions), so immediate escalation would
   be noisier and less self-healing than a bounded re-dispatch. `verify-builder` implements
   and tests exactly this policy; Success Criteria assert it. Revisiting toward
   first-mismatch escalation is a *separate later plan* (a materially different code path —
   `Blocked` on first mismatch rather than a G4-bounded re-dispatch — so it is fixed now
   rather than left open mid-build).
3. **Critique nit-labeling — OUT (router-side latch is the intended resolution).** #1760's
   secondary observation is that `/do-plan-critique` sometimes emits "with concerns" for
   nit-only findings. The convergence latch (Fix 2) makes the router converge to BUILD
   regardless, so this plan's router scope is complete without touching the critique skill.
   Tightening the critique-skill labeling (in `.claude/skills-global/do-plan-critique/`) is
   **out of scope** for this plan — a valuable but separable follow-up, recorded in Rabbit
   Holes and No-Gos. No `verify-builder`/`latch-builder` behavior depends on it.
