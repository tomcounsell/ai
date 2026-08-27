---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-26
tracking: https://github.com/tomcounsell/ai/issues/2894
last_comment_id: none
revision_applied: true
revision_applied_at: 2026-08-26T12:34:01Z
---

# Wave 2: SDLC router terminal state and verdict integrity

Ships the nine issues of [`docs/bug-backlog-waves.md`](../bug-backlog-waves.md) §"Wave 2":
**#2894, #2817, #2895, #2850, #2885, #2851, #2849, #2832, #2886**.

**Supersedes** [`docs/plans/next-skill-terminal-verdict-and-pr-resolution.md`](next-skill-terminal-verdict-and-pr-resolution.md)
for its remaining open scope. That plan shipped nothing; its two siblings **#2825 and #2824
are both CLOSED**, leaving #2817 as its only live issue. WS-1 here absorbs it. The old
document is deleted by this plan rather than left as a second doc tracking #2894's twin.

## Problem

The router that decides which SDLC skill runs next (`agent/sdlc_router.py`) has two holes,
and the critique machinery that feeds it has four more. Together they burn tokens on live
lanes right now.

**Current behavior:**

1. **The router cannot express "this lane is finished."** It never reads
   `stage_states["MERGE"]`, and `decide_next_dispatch` has only two outcome types —
   `Dispatch` and `Blocked`. So a merged, closed, shipped lane keeps matching dispatch rules
   forever.
2. **The head-staleness gate fails open by default.** `_review_verdict_head_is_stale`
   returns "fresh" whenever `"pr_head_sha"` is absent from `context`, and one of the two
   production callers passes no context at all.
3. The plan↔critique loop has no working cap, critique run dirs lose control files, the
   ambient issue number goes stale, and the critique roster silently runs serially.

**Desired outcome:** a finished lane produces an explicit terminal result; a staleness gate
that stops guarding says so; and the critique loop is bounded, attributable, and honest about
whether its critics ran independently.

## Freshness Check

**Baseline commit:** `fc97f7318afcda67c063e83c8e63773c1a4557fe` (worktree rebased onto
`origin/main` at plan time; `git rev-parse HEAD == origin/main`)
**Issues filed:** #2817 2026-08-14 · #2832 2026-08-17 · #2849/#2850/#2851 2026-08-17 ·
#2885/#2886 2026-08-19 · #2894/#2895 2026-08-20
**Disposition: Major drift.** Two issues' acceptance criteria are obsolete and one issue's
crux claim is falsified. Details below. The drift does not invalidate the wave — it
*strengthens* WS-1 and *shrinks* WS-5 and WS-7.

**Recon gate.** All nine issues now pass
`.claude/hooks/validators/validate_issue_recon.py`. Eight lacked a `## Recon Summary`
entirely at plan start; rather than bypass the ISSUE→PLAN gate, four grounded recon passes
were run against this baseline and appended to the issue bodies (21–29 items each). Every
line number below was re-derived at `fc97f7318` — both #2894 and #2895 warn in their own
bodies that peer-supplied citations were wrong, one because it came from a worktree branched
before ~60 commits of drift in `agent/sdlc_router.py`.

**File:line references re-verified (corrections carried into Technical Approach):**

| Cited | Status at `fc97f7318` |
|---|---|
| `_rule_ready_to_merge` `:1758`, `needed` `:1772`, fallback `:2051`, msg `:2063` | all exact |
| `_review_verdict_head_is_stale` `:1196` | **drifted → `:1195`** |
| `decide_next_dispatch` sole caller `tools/sdlc_next_skill.py:702` | **wrong → `:722`** |
| `_fetch_pr_head_sha` "contains a `gh` fallback" `:135` | **wrong → `:150`, body `:166-168` delegates entirely to `pr_head_resolver`; the cited "fallback" is docstring prose at `:155-159`** |
| `_backfill_predecessors` `~757-773` | **short by 22 lines → `:757-795`** |
| `critique_cycle_count` increment `:1072` | `:1072` is the comment; the statement is `:1073-1074` |
| `_review_trailer_present` `~603-628`, `SKIPPABLE_STAGES` `:83`, `skip_stage` `:857`, `_qualifies_as_never_dispatched` `:853`, `sdk_client.py:491-498` | all exact |

**Cited sibling issues/PRs re-checked:**

- **#2734 CLOSED 2026-08-18T03:44:37Z / PR #2844 MERGED 03:44:36Z**
- **#2741 CLOSED 2026-08-18T03:05:16Z / PR #2842 MERGED 03:05:15Z**
  → #2851's AC "#2734 and #2741 specifically become unwedged" is **moot**. Replaced with a
  synthesized-lane criterion. The "do not advance them by hand" ordering hazard is stale.
- **#2825, #2824, #2757 all CLOSED; PR #2826 MERGED** → the superseded plan's siblings are done.
- **#2636 / PR #2833 MERGED 2026-08-18T02:57:37Z** → #2850's evidence lane has rolled
  forward; its REVIEW verdict is now APPROVED with a populated `head_sha`. The null evidence
  survives only inside `_sdlc_dispatches[].stage_snapshot`.
- **#2649 CLOSED (refuted-as-filed); PR #2672 MERGED**, and its four-file diff does **not**
  include `do-plan-critique/SKILL.md` → #2886's "this is not #2649" claim holds.

**Commits on main since the earliest issue, touching referenced files:** `506942c07`
(next-skill dispatch persistence), `f376ae315` (#2931 router/skill consolidation),
`e4f8e6e5f` (#2797 G9), `838182c16` (#2826 G8), `839d70a5f` (#2830/#2787 concern
re-critique), `e2d3cf209` (#2818 run identity). None changed the root cause of any of the
nine. `#2826` is the important neighbour: it deliberately shipped **no** MERGE short-circuit
and **no** terminal predicate (both cut during its critique) and left `agent/sdlc_router.py`
byte-identical to its merge base — so WS-1 is the first change to that surface.

**Active plans overlapping this area:** `docs/plans/next-skill-terminal-verdict-and-pr-resolution.md`
(status Ready, tracking #2817) — superseded by this plan, see header.
`docs/plans/router-docs-skip-trivial.md` touches the router but a disjoint rule.

**The drift that changes the shape of the work — three findings:**

1. **The post-merge misroute is not one row, it is at least four.** Recon measured, on
   already-merged lanes: row 10 → `/do-merge` (#2894, as filed); row 8f → `/do-pr-review`
   on the two ex-wedged lanes #2734/#2741; row 5 → `/do-build` (#2817's own matrix); and row
   1 → `/do-plan` on lane `sdlc-2853` whose ledger has since emptied. **This is the single
   most important finding in the wave.** Patching row 10's `needed` list fixes one of four.
   The fix must be a guard that preempts the entire dispatch table, not a per-row predicate.
2. **#2832's crux claim is falsified.** `tools/critique_roster_check.py::_load_roster`
   (`:173-203`) **raises `ValueError`** on a missing `_roster.json`; `evaluate()` catches at
   `:266-278` and returns `complete: false` → `MAJOR REWORK (CRITIQUE INCOMPLETE)`. It fails
   *loudly*, not silently, so the "findings silently dropped" framing does not hold. A real
   but different silent path exists (see WS-5).
3. **#2885 is worse than filed.** `grep -rn "fail_stage"` excluding tests/docs returns
   **zero production call sites**. The counter cannot leave 0 by *any* live path, not merely
   by the verdict-driven one. G2 is dead code today.

## Prior Art

- **PR #2826 / #2757** — *G8 must not rebuild shipped work.* Fenced G8's post-merge
  `/do-build` by teaching the artifact verifier to resolve merged PRs. Succeeded for G8;
  explicitly declined to add a terminal predicate. Its residual **is** #2817.
- **PR #2672 / #2679** — fixed missing `allowed-tools` frontmatter stripping `Agent` from
  skills. Succeeded, but for a different mechanism than #2886 (verified: its diff does not
  touch `do-plan-critique`). #2649, the first framing, was closed refuted-as-filed and left
  one checkbox alive — that checkbox is #2886.
- **PR #2769** (`docs/archive/plans-completed/verdict-finalize-cluster.md`) — introduced the
  `head_sha` **record field**, moving the SHA out of the in-token `REVIEW_CONTEXT head_sha=`
  trailer. Succeeded, but only wired the APPROVED path — the omission is #2850.
- **#2193 "Risk 1"** — scoped the trailer-*presence assertion* to APPROVED because
  non-APPROVED verdicts legitimately carry no trailer. Correct and must survive WS-2.
- **#1731** — established the args-only hand-off for `/do-sdlc` and the "latched onto the
  wrong issue" framing. `tests/unit/test_sdlc_fork_issue_number.py:371-394` already forbids
  `/do-sdlc` exporting `SDLC_ISSUE_NUMBER`. #2849 is that same defect surviving one layer
  down in `sdk_client.py`.
- **#2415 / #2554 / #2577** — the verdict invariant, the backfill-vs-invariant precedence
  rule, and `skip_stage` as the honest alternative to forging a verdict. All three must
  survive WS-7 intact.
- **#2801 / #2787** — G4's counter caps alternating oscillation at 1; a with-concerns
  verdict does not mandate re-critique. Context for WS-3's design fork.

## Research

No external research performed. Every one of the nine issues is a defect in this repo's own
router, pipeline-state machine, and skill definitions — no external libraries, APIs, or
ecosystem patterns are involved. The one arguably-external question (#2886's harness
tool-inheritance for nested `context: fork` skills) is a property of the Claude Code harness
that no documentation in or out of this repo settles, and which WS-6 deliberately declines to
fix. Proceeding on codebase context.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| PR #2826 (#2757) | Stopped G8 rebuilding shipped work by resolving merged PRs in the artifact verifier | Fixed **one guard's** view of a finished lane. Left the dispatch table with no notion of terminality at all, so rows 1/5/8f/10 each reintroduce the same class through a different door. Its critique cut the terminal predicate as scope. |
| PR #2769 (#2193) | Added the `head_sha` record field to verdicts | Wired only the APPROVED path, conflating "what we *record*" with "what we *assert*". The two are separable; #2850 is the unwired half. |
| PR #2672 (#2679) | Restored `Agent` by fixing `allowed-tools` frontmatter | Correct for its mechanism, but its evidence came from directly-invoked skills. It never tested a fork nested inside a subagent — the standard `/do-sdlc` shape — which is #2886. |
| #2577 `skip_stage` | Gave lanes an honest escape from a verdict-less CRITIQUE, gated on "no plan document" | The gate reads `docs/plans/` only. Recon found that **archiving a plan doc silently makes a lane's CRITIQUE retroactively skippable** — an undesigned hole through the very invariant it protects (WS-7). |

**Root cause pattern:** every one of these fixed a *site* where a general property was
violated, rather than establishing the property. The router has no notion of a finished lane;
the staleness gate has no notion of a caller that must supply a signal; the roster has no
notion of independence. This plan's bias throughout is to fix the **default and the
contract**, then pin the contract with a test that enumerates call sites — so the next caller
cannot reintroduce the hole by omission.

## Architectural Impact

- **New dependencies:** none. No new packages, services, or config.
- **Interface changes:** `decide_next_dispatch` gains a third outcome type (`Terminal`)
  alongside `Dispatch`/`Blocked`, and `sdlc-tool next-skill` gains a corresponding
  `decision: "terminal"` output shape. Both are additive; existing consumers that branch on
  `blocked` / `skill` keep working, but `/do-sdlc`'s loop must learn to stop on terminal.
  `_extract_sdlc_env_vars` loses two keys (WS-4).
- **Coupling:** decreases. WS-4 deletes an ambient env-var channel in favour of explicit
  resolution. WS-2 replaces an implicit "absent key means not applicable" convention with an
  explicit opt-out.
- **Data ownership:** unchanged. `agent/sdlc_router.py` must continue never importing from
  `tools/` (enforced by `tests/unit/test_architectural_constraints.py`).
- **Reversibility:** high for WS-1/2/3/5/6 (pure logic, revert the commit). WS-4 is a
  deletion across four markdown consumers and is the least reversible; it is sequenced last
  within its PR for that reason.

## Appetite

**Size:** Large

**Team:** Solo dev + PM + code reviewer, with fan-out to builder subagents per workstream.

**Interactions:**
- PM check-ins: 2-3 (the PR split below is a PM decision; WS-7's direction is a design call)
- Review rounds: 2+

**On the appetite.** The wave was framed as one seam. It is not — see Scope Reality below.
Large is the honest size for seven workstreams across ~15 files, and the mitigation is the
three-PR split, not a smaller plan.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worktree on the pinned interpreter | `scripts/pytest-clean.sh --collect-only tests/unit/test_sdlc_router.py -q` | The wrapper aborts on an off-pin venv; an off-pin `.pyc` is the Wave 0 failure class this lane must not re-diagnose |
| `gh` authenticated for this repo | `gh repo view --json nameWithOwner -q .nameWithOwner` | Issue/PR reads across all nine issues |
| Git-first PR head resolution works | `python -c "from tools.pr_head_resolver import resolve_pr_head_sha; print(bool(resolve_pr_head_sha(2884)))"` | WS-2 depends on `git ls-remote refs/pull/N/head`; if this returns False the staleness tests will pass for the wrong reason |
| Redis test db claimable | `scripts/pytest-clean.sh tests/unit/test_pipeline_state_machine.py -q --collect-only` | Pipeline-state tests need the claimed test db, not production |

Run via `python scripts/check_prerequisites.py docs/plans/wave2-router-verdict-integrity.md`.

## Scope Reality (read before building)

`docs/bug-backlog-waves.md:91-93` asserts *"All nine touch `agent/sdlc_router.py`."* **That
is false**, verified by grep at `fc97f7318`:

| Issue | Primary surface | Touches `agent/sdlc_router.py`? |
|---|---|---|
| 2894 | `agent/sdlc_router.py` | yes |
| 2817 | `agent/sdlc_router.py`, `tools/sdlc_next_skill.py` | yes |
| 2895 | `agent/sdlc_router.py`, `agent/session_runner/runner.py` | yes |
| 2850 | `tools/sdlc_review_finalize.py` | no (feeds it) |
| 2885 | `agent/pipeline_state.py`, `agent/pipeline_graph.py` | reads G2 |
| 2851 | `agent/pipeline_state.py`, `tools/sdlc_stage_marker.py` | **no** |
| 2849 | `agent/sdk_client.py` + 4 markdown consumers | **no** — zero `SDLC_ISSUE_NUMBER` hits in the router |
| 2832 | `tools/critique_resume.py`, `docs/sdlc/do-plan-critique.md` | **no** |
| 2886 | `.claude/skills-global/do-plan-critique/SKILL.md` | **no** |

So this is **two genuine shared seams (WS-1, WS-2) plus five substantially independent
changes**. This plan is one document, as directed, but it must not pretend to a unity the
code does not have. The workstreams below are ordered by the wave's stated priority and
sequenced into three PRs.

## Solution

### Key Elements

- **WS-1 Terminal lane state** (#2894, #2817): a single terminal guard that preempts the
  whole dispatch table when a lane is finished, plus a `Terminal` decision type so
  "finished" is expressible rather than inferred from a fallthrough.
- **WS-2 Staleness seam** (#2850, #2895): flip the head-staleness default to fail closed,
  pin the call contract across every production caller, and record `head_sha` on every REVIEW
  verdict rather than only approvals.
- **WS-3 Critique loop bound** (#2885): bound the plan↔critique lap on the observable
  verdict trail; retire or rewire the vestigial counter.
- **WS-4 Explicit issue number** (#2849): delete the ambient `SDLC_ISSUE_NUMBER` /
  `SDLC_TRACKING_ISSUE` export and make every consumer resolve explicitly or fail loudly.
- **WS-5 Critique run-dir integrity** (#2832): narrow the stale-run GC so it cannot delete a
  live sibling, and close the genuinely-silent missing-`.plan_hash` path.
- **WS-6 Roster independence** (#2886): record `independent:` in `_roster.json` and surface
  it in the gate, so a serially-executed roster announces itself.
- **WS-7 Mid-pipeline entry** (#2851): refuse mid-pipeline entry, add a detector, and close
  the plan-archival escape hatch recon discovered.

### Flow

**A finished lane today** → `next-skill` → matches row 1/5/8f/10 depending on ledger shape →
dispatches `/do-plan`, `/do-build`, `/do-pr-review`, or `/do-merge` on shipped work → forever.

**A finished lane after WS-1** → `next-skill` → terminal guard fires before any row is walked
→ `{"decision": "terminal", "reason": "pipeline complete (MERGE completed)"}` → `/do-sdlc`
loop exits cleanly.

### Technical Approach

#### WS-1 — Terminal lane state (#2894 + #2817)

The two issues are the same defect seen from opposite ends: #2894 is "row 10 fires when it
should not," #2817 is "nothing fires when something should." Both are consequences of the
router having no terminal concept. **Neither can be fixed alone** — #2894's body works
through this explicitly: adding `MERGE` to row 10's `needed` list makes the `primary is None`
block at `:2051` reachable, and because a merged PR reports `mergeable: UNKNOWN` (re-verified:
`gh pr view 2884` → `state MERGED, mergeable UNKNOWN, mergeStateStatus UNKNOWN`, corroborated
on #2842 and #2844), every finished lane would emit a spurious
`check GH_REPO / SDLC_TARGET_REPO` misconfiguration error.

`pr_merge_state == "UNKNOWN"` carries three meanings the `:2051` block conflates:
merged-and-done (terminal success), not-yet-computed (transient), and genuinely-unresolvable
(real misconfiguration). Terminality must be decided **before** control reaches that block.

Direction:

1. **Add a `Terminal` outcome** alongside `Dispatch` (`:147`) and `Blocked` (`:156`), carrying
   a reason and a terminal-kind discriminator.
2. **Add a terminal guard**, evaluated in `evaluate_guards` (`:935-944`) — which runs at
   `:2032`, *before* the `DISPATCH_RULES` loop at `:2037` — keyed on the lane being finished.
   This placement is what makes the fix general: recon measured four distinct post-merge
   misroutes (rows 1, 5, 8f, 10), and a guard preempts all of them, including rows nobody has
   yet observed misfiring. A per-row terminal condition would have to be written four times
   and re-written for every future row.
3. **Define "finished" from more than one signal**, because recon showed `MERGE: completed`
   alone is insufficient: lane `sdlc-2853`'s ledger has since **emptied** (`pr_number: null`,
   `plan_exists: false`) and now routes to row 1 `/do-plan` on a merged lane. The predicate
   should treat a lane as terminal on `MERGE == completed` **or** a resolvable PR in a merged
   state — so a lost ledger does not resurrect a shipped lane.

   **The merged-PR half requires a fix in meta assembly, not in the router** (critique round
   2 blocker). For a ledger-less lane the router is handed nothing to work with:
   `tools/sdlc_stage_query.py::query_enriched` returns `{"stages": {}, "_meta": _default_meta()}`
   at `:836-837` as soon as no session row is found, short-circuiting `_compute_meta`'s
   two-pass merged-PR fallback at `:622-624`. Measured on lane #2853: `query_enriched(2853)`
   → `stages={}`, `pr_number=None`, `pr_merge_state=None`, while `_lookup_pr(2853, state="merged")`
   **does** return PR **2884**. The resolver works; it is simply never reached. WS-1 must add
   a ledger-less merged-PR rung before that early return so `pr_number` and `pr_merge_state`
   are populated for a shipped lane whose ledger has been evicted. This belongs in
   `tools/sdlc_stage_query.py` — `tests/unit/test_architectural_constraints.py` forbids
   `agent/sdlc_router.py` importing from `tools/`, so the router cannot resolve this itself.
   Without this rung the #2853 replay success criterion below is unsatisfiable.

   **Do not gate that rung on bare `session is None` — critique round 3.** That branch also
   fires for every never-seen issue (`_resolve_issue_record:754-757` notes "the router polls
   this path constantly"), so a naive rung would run a live `_lookup_pr` subprocess
   (`:369-407`, 5s timeout, no memoization) on **every `next-skill` poll for a fresh issue** —
   including this plan's own fresh-lane negative control. Gate it on positive evidence the
   lane once existed (a durable signal: a branch, a plan doc, a prior dispatch record), or put
   a TTL cache in front of the lookup. Fail-closed on evidence, exactly as WS-1's terminal
   guard and WS-7's refusal do — a lookup is not free and "no session row" is not evidence of
   anything.

   **`is_pipeline_complete` is NOT a drop-in — do not call it directly.** Its signature is
   `is_pipeline_complete(psm_states, outcome, pr_open=None) -> tuple[bool, str]`
   (`agent/pipeline_complete.py:35`), and it returns `(False, "outcome_not_success")` unless
   `outcome == "success"`. `decide_next_dispatch` has no `outcome` argument and no honest
   value to synthesize for one — its existing consumer
   `agent/session_runner/completion_guard.py:155` hardcodes `"success"` precisely because it
   already knows the transition succeeded, which the router does not. It also has **no
   "resolvable merged PR" branch**, which is the other half of the terminal predicate this
   plan needs. Reuse its *`MERGE == completed` logic* by extracting a shared pure helper, or
   write the router's predicate independently — but do not wire the existing function in and
   assume it covers the case. The module imports only stdlib, so either route is safe against
   the no-`tools`-import constraint. Any extraction must keep `completion_guard.py:155`'s
   behavior identical.
4. **Teach the `primary is None` fallback** that merged-and-done is success, so the
   misconfiguration message is reserved for genuine misconfiguration.
5. **Surface terminal in `tools/sdlc_next_skill.py`** as a third output shape, and teach
   `/do-sdlc`'s and `/sdlc`'s loops to exit cleanly on it rather than treating it as a
   `blocked` error to report.

**Negative control (from #2817's measured matrix, mandatory):** the four live `/do-merge`
routes where `MERGE` is *not* yet completed must still fire. A terminal guard that swallows a
pre-merge lane is strictly worse than the bug.

**Docstring parity hazard:** `tests/unit/test_sdlc_skill_md_parity.py` ties row ids to
SKILL.md via rule docstrings. Recon also flagged an existing drift to fix in passing:
`_rule_ready_to_merge.__doc__` (`:1818`) claims "or stage_states unavailable" but
`_stages_settled` (`:385-396`) returns False on an empty map.

#### WS-2 — Verdict staleness seam (#2895 + #2850)

**#2895 is reproducible and already went red.** Recon drove `decide_next_dispatch` in-process
on identical state:

- no `context` → `Dispatch(skill='/do-merge', row_id='G6')`
- `context={'pr_head_sha': <different sha>}` → `Dispatch(skill='/do-pr-review', row_id='8f')`

The mechanism: `agent/session_runner/runner.py:1403` calls
`decide_next_dispatch(stage_states, meta)` with **no context**; `decide_next_dispatch`
normalises `context = context or {}` at `:2029-2030`; and `_review_verdict_head_is_stale`
(`:1195`) returns False at `:1221-1222` when the key is absent. The gate is completely inert
on that path. That call site is wrapped in
`except Exception: # noqa: BLE001 — nudge text only, never fatal` (`:1405-1406`), so it could
not have surfaced a problem even if it had one.

**State the reproduction split precisely, and do not overclaim.** The *structural* fail-open
reproduces deterministically and is the demonstrated-red this fix is entitled to close on.
The *originally observed incident* on PR #2884 came through the CLI path where `_build_context`
**does** supply the key and is explicitly fail-closed (`pr_head_sha = ""` plus
`pr_head_sha_lookup_failed = True` on lookup failure). That incident remains **unexplained and
unreproduced**; nothing in this plan claims otherwise. Per the wave doc's #2895 caveat, if the
structural fix lands and the CLI-path contradiction is still unexplained, say so in the PR and
leave that observation documented rather than silently closed on inference.

Direction, in the issue's own order of durability:

1. **Flip the default.** An absent `pr_head_sha` fails **closed** (stale) when a `pr_number`
   and a recorded verdict both exist — the two conditions `_build_context` already uses to
   decide whether to populate the key. Fixing the default is what stops the next caller
   reintroducing the hole.
2. **Require an explicit opt-out** so a caller states "signal not applicable" rather than
   getting it by omission.
3. **Pass context at `runner.py:1403`.** Necessary, not sufficient.

**Recon correction that changes the test scope:** the predicate has **three** inheritors, not
the two the issue names — G6 (`:814`), row 8f (`:1739`), and row 10 `_rule_ready_to_merge`
(`:1770`). A test scoped to two leaves one unpinned. And per the issue, a test scoped to the
*inheritors* at all is insufficient: pin the **call contract across production callers** —
enumerate every `decide_next_dispatch` call site and assert each supplies the staleness signal
or explicitly opts out. That is the test that would have caught `runner.py:1403`.

**#2850** widens verdict recording. Record `head_sha` on every REVIEW verdict while leaving
`_review_trailer_present`'s APPROVED-scoped **assertion** untouched — #2193's Risk 1 contract
is about what may be *required*, and says nothing against *recording* a SHA that is cheaply
available. Two recon corrections:

- **The gap is wider than the title.** `is_approved` is a substring test, so **three** of the
  four `RECOGNIZED_REVIEW_VERDICTS` (`tools/sdlc_review_finalize.py:130-135`) take the
  non-approved path: `CHANGES REQUESTED`, `BLOCKED ON CONFLICT`, `PR CLOSED`.
- **It stores an absent key, not a null.** `tools/sdlc_verdict.py:461-462` attaches `head_sha`
  only when truthy. Tests must assert through `head_sha_of_record` / `_meta.latest_review_head_sha`,
  **not** `record["head_sha"] is None`.

Two decisions this plan makes explicitly rather than inheriting:

- **Failure posture on the non-approved path: record best-effort.** The approved path fails
  CLOSED (`REVIEW_TRAILER_MISSING`) because a trailer-less approval is worse than a loud
  stall. That reasoning does not transfer — a CHANGES REQUESTED verdict that fails to record
  is a **lost finding**, which is worse than an unattributable one. Log and store nothing;
  null degrades safely because `_review_verdict_head_is_stale` already treats an
  unattributable verdict as stale.
- **Recording only; no new CHANGES-REQUESTED staleness rule in this plan.** Adding a router
  rule that treats a head-stale CHANGES REQUESTED verdict as superseded interacts with rows
  8/8b ordering and G4's loop bound, and belongs in its own pass. Recording alone makes the
  defect observable, which is the prerequisite for that rule. See No-Gos.

**Head-SHA resolution is non-negotiable:** every lookup added resolves through
`tools/pr_head_resolver.py::resolve_pr_head_sha` (git-first via
`git ls-remote refs/pull/N/head`), never a bare `gh` read. A stale `gh` head matches the
recorded trailer and flips this very gate from fail-closed to fail-open — see
[`docs/features/gh-stale-state-verdict-gate.md`](../features/gh-stale-state-verdict-gate.md).
Recon confirmed `tools/sdlc_next_skill.py::_fetch_pr_head_sha` (`:150`, body `:166-168`)
already delegates entirely to the resolver and contains no fallback of its own.

**Fixture hazard:** per [`docs/sdlc/do-test.md`](../sdlc/do-test.md), tests must fake
`git ls-remote`, not just `gh`, or `context["pr_head_sha"]` lands on the fail-closed empty
sentinel and the test passes for the wrong reason.

#### WS-3 — Critique loop bound (#2885)

Recon strengthened the diagnosis: `fail_stage` has **zero production call sites** (only its
definition, docstrings, a prose mention at `agent/hooks/pre_tool_use.py:29`, and tests). Since
the sole increment of `critique_cycle_count` lives at `agent/pipeline_state.py:1073-1074`
inside `fail_stage`, the counter cannot leave 0 by any live path, and G2
(`agent/sdlc_router.py:359-382`, short-circuiting at `:368-369`) is dead code. G4 is blind by
construction: `compute_same_stage_count` breaks the streak on any skill change (`:2322-2323`),
and `/do-plan` → `/do-plan-critique` alternation is never the same stage twice.

**Decide the fork deliberately: bound on the verdict trail, not the counter.** N consecutive
`NEEDS REVISION` verdicts on the same plan is directly observable in `_verdicts` and does not
depend on a counter anyone remembers to maintain — which is precisely the maintenance the
current design failed at. The counter is vestigial; either wire it on the live path or retire
it, but do not ship a bound that depends on it without a live writer.

`MAX_CRITIQUE_CYCLES = 2` is a bare literal at `agent/pipeline_graph.py:36`, unlike
`MAX_CONCERN_RECRITIQUE_ROUNDS` at `:55`. Any new bound should be a named, env-overridable
constant with a comment marking it provisional and tunable.

**Test Impact:** `tests/unit/test_pipeline_state_machine.py:601-607`
(`test_fail_critique_increments_critique_cycle_count`) is green today and pins the only
increment path. It must be updated or deleted deliberately, not left to fail.

#### WS-4 — Explicit issue number (#2849)

**Direction 2: delete the ambient export.** This completes an already-enforced precedent
rather than inventing one — `tests/unit/test_sdlc_fork_issue_number.py:371-394` already
forbids `/do-sdlc` from exporting `SDLC_ISSUE_NUMBER`; `agent/sdk_client.py:491-498` is the
same anti-pattern surviving one layer down.

The recon sweep (grep, per the issue's AC, not a hand-written list) found **four un-guarded
consumers, not the two the issue names**:

| Site | Guarded | Consequence when stale |
|---|---|---|
| `do-pr-review/sub-skills/code-review.md:47-49` | no | reviewer judges the PR against the **wrong issue's** acceptance criteria |
| `do-pr-review/sub-skills/post-review.md:221` | no | plan-checkbox commit misattributed and pushed |
| **`docs/sdlc/do-patch.md:62`** | no | **missed by the issue.** `git commit -m "fix(#…)"` into pushed history, reachable **pre-PR** via `do-build/SKILL.md:128` routing test failures to `/do-patch` during BUILD — so no PR-body fallback exists there at all. This directly answers the issue's own open question about pre-PR stages. |
| `agent/session_completion.py:232,240` | no | reads env **first** (`# Check env vars first (most authoritative)`), inverting #1731's precedence. **Currently has no callers** — latent, not firing. |
| `do-pr-review/SKILL.md:56,101` and `docs/sdlc/do-pr-review.md:34` | n/a | **contract prose**, not executable reads — added in critique round 2, which caught that the anti-criterion could not reach 0 while these three hits went unnamed. They document the env var's role and must be rewritten alongside the code, or the sweep never zeroes. |

**Full sweep accounting — all 18 hits, so the anti-criterion is reachable:**
`checkout.md` 5 · `agent/sdk_client.py` 3 · `code-review.md` 3 · `session_completion.py` 2 ·
`do-pr-review/SKILL.md` 2 · `docs/sdlc/do-pr-review.md` 1 · `docs/sdlc/do-patch.md` 1 ·
`post-review.md` 1. Every one is in WS-4's scope; none may be left behind.

Two findings that shrink the work: **`SDLC_TRACKING_ISSUE` has no live consumer anywhere** —
only the dead function plus docs and tests — so it is a pure delete. And the guarded path in
`checkout.md:41-43` still *falls through* to the stale value when a PR body carries neither
`Closes #N` nor a `tracking:` URL; it fails loudly only when the stale value is also absent or
non-numeric. That fall-through is in scope.

#### WS-5 — Critique run-dir integrity (#2832)

**The issue's crux is falsified — do not build the fix it asks for.** `_load_roster`
(`tools/critique_roster_check.py:173-203`) raises `ValueError` on a missing `_roster.json`;
`evaluate()` catches at `:266-278` and returns `complete: false` with rc 2, producing a
spurious `MAJOR REWORK (CRITIQUE INCOMPLETE)`. It fails **loudly**. The "findings silently
dropped, the class #1690 was closed to prevent" framing does not apply, and the "add a guard
that refuses to clean a directory whose roster has unreported members" suggestion addresses a
symptom that does not exist.

What is genuinely wrong, in descending order of value:

1. **The stale-run GC can delete a live sibling.** `docs/sdlc/do-plan-critique.md:51` runs
   `cat /tmp/critique-resume-stale.txt | xargs -r rm -rf`. `tools/critique_resume.py:106-109`
   marks stale on a bare `stored_hash != want_hash` across every `^{issue}-` sibling
   (`:88-89`) with no mtime, lock, roster, or self-exclusion check. Worse, `want_hash is None`
   (unreadable plan) short-circuits the `or` and marks **every** sibling stale. Per the
   issue's own instruction, the defect is in cleanup **scope**, not run-dir **naming** — the
   ns-timestamp suffix already provides uniqueness.
2. **`/tmp/critique-resume-stale.txt` is a fixed machine-global path.** Concurrent runs in
   *different worktrees* overwrite each other's stale lists, and `xargs rm -rf` then resolves
   those relative paths against the caller's cwd. Neither the issue nor the intake named this.
   `CRITIQUE_RUN_DIR` is itself relative (`docs/sdlc/do-plan-critique.md:60`), compounding it.
3. **The genuinely silent path:** a missing `.plan_hash` hits `continue` at
   `tools/critique_resume.py:101-103`, so the dir is never resumable **and** never GC'd — it
   accumulates. Live evidence in the primary checkout: of 13 dirs in
   `/Users/valorengels/src/ai/.critique-runs/`, `1726-1782125973475253000` is **completely
   empty** and `1820-1782889578791903000` lacks both control files.

Hash divergence is real but narrower than the intake claimed: `compute_plan_body_hash` strips
**only** the `revision_applied:` line, so that single key is the entire delta between
`.plan_hash` and G5's anchor.

**Note the evidence does not demonstrate the filed incident.** Both #2741 runs completed, and
`SKILL.md:290-294` deletes the run dir **by design** on the `complete: true` path — which alone
explains both directories disappearing. The GC also only fires on `PROBE_EXIT == 0`, which a
differing-hash sibling by definition does not produce. Latent hazard: confirmed. Reproduction:
not claimed.

#### WS-6 — Roster independence (#2886)

**The root cause is a harness property this repo cannot fix, and the plan does not try.**
Recon ruled out the two available in-repo explanations: the hardlink to
`~/.claude/skills/do-plan-critique/SKILL.md` is **intact** (same inode, 181729529), and the
frontmatter has `context: fork` with **no** `allowed-tools` key — the condition #2672
established as correct. Whether a nested `context: fork` strips `Agent` is decided by harness
tool inheritance, and `tests/unit/test_skill_agent_tool_consistency.py:18-19` says so in as
many words: *"Whether a `context: fork` context also strips `Agent` is a separate question
this test does not speak to."* The nesting hypothesis needs a live paired probe (top-level vs.
nested subagent) and is **not pinnable by a regression test**.

**So the deliverable is detect-and-announce, and that is honest rather than a consolation
prize.** `_roster.json` has no independence field and no Python writer — it is written by the
driver from prose (`SKILL.md:204-209` → `docs/sdlc/do-plan-critique.md:65,114`), and
`_load_roster` reads only `roster`/list, ignoring other keys. The gate reports
`complete/missing/present/roster_count/completed_count` (`:302-310`) — none of which ever
*claimed* independence. It is **silent** on a property every reader assumes. Recording
`independent:` makes the silence explicit rather than correcting a lie, and the announcement
surface already exists at `SKILL.md:324`.

**Evidence citation correction:** the plan doc has exactly **one** `## Critique Results`
section (`:1790`) and one execution note (`:1797`) — the section is *overwritten* each round.
The six rounds are confirmed by per-commit walk (`b4b7cd1dc`, `6f943b78d`, `d40261968`,
`8e7a33f0a`, `81b737b58`, `d3d63ad0f`), each carrying exactly one note, and they are rounds
**2-7, not 1-6**. Cite the SHAs, never the file.

#### WS-7 — Mid-pipeline entry (#2851)

**Direction (b): refuse mid-pipeline entry, plus a detector.** The issue framed (a)-vs-(b) as
a live design decision and named (b)'s weakness as "does nothing for the two already-wedged
lanes." **That weakness has evaporated** — #2734 and #2741 both closed on 2026-08-18 and
their PRs merged. Prevention is all that remains, so (b) is now strictly cheaper than when the
issue was written, and it avoids adding a new write path into the very state the verdict
invariant protects. The detector — flagging a lane that entered at a non-ISSUE stage on the
positive-evidence predicate defined below, **not** on stages-map emptiness — becomes the
highest-value half of the deliverable.

**Recon reshaped the reproduction target.** The wedge is better characterized as **verdict
recorded without a marker** than as "empty stages map": both ex-wedged lanes carry an APPROVED
REVIEW verdict while REVIEW sits `pending`. That is the shape the regression test should pin.

**And recon found a second hole that must be closed alongside.** `plan_exists` is now `false`
for both lanes because their plan docs were archived to `docs/archive/plans-completed/`
(commits `96435c634` → `659f1d0e4`, `cbfdcad0f`), and `find_plan_path`
(`tools/lane_identity.py:123-163`) searches only `docs/plans/`. `skip_stage`'s
`_qualifies_as_never_dispatched` precondition is "no plan document" — so **archiving a plan
document silently makes a lane's CRITIQUE retroactively skippable.** That is an undesigned
escape hatch through the invariant #2851 exists to protect, and it is strictly more dangerous
than the deadlock the issue reports. It must be closed in the same change.

**The refusal cannot key on "empty stages map" alone — critique round 1 caught this.**
`_recover_stage_states_from_durable_signals` (`tools/sdlc_next_skill.py:526`, called at
`:712-718`) runs **before** `decide_next_dispatch` at `:722` and fires only when
`stage_states == {}` exactly. So by the time the router sees an empty map, recovery has
already run and failed, and the router **cannot distinguish a brand-new issue from an evicted
mid-pipeline lane** — both present as empty. A bare refusal on emptiness would reject every
fresh lane at ISSUE.

The refusal must therefore key on positive evidence of mid-pipeline entry, not on absence:
a dispatch **at a non-ISSUE stage** combined with a recorded artifact that implies skipped
predecessors (a plan document, or a recorded verdict with no corresponding marker). That is
the same fail-closed-on-evidence discipline WS-1 and WS-2 apply. Place the check where
recovery's outcome is known, and make the detector report *which* signal was missing so an
evicted ledger is diagnosable rather than merely refused.

The verdict invariant (#2415) and the #2554 precedence rule stay enforced on every existing
write path; regression tests must prove a verdict-less REVIEW/CRITIQUE still cannot be
force-completed.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_runner/runner.py:1405-1406` — `except Exception: # noqa: BLE001 — nudge text only, never fatal` around the `decide_next_dispatch` call. WS-2 must assert observable behavior (a log line naming the failure) rather than leaving the swallow bare, since this is precisely the swallow that hid #2895's fail-open.
- [ ] `tools/critique_roster_check.py:266-278` — the `ValueError` catch that turns a missing `_roster.json` into `complete: false`. WS-5 must add a test asserting it reports the *reason*, not just the boolean.
- [ ] `agent/sdlc_router.py:2037-2049` — the per-rule `try/except` in the dispatch loop that logs and continues. WS-1 must assert a raising terminal guard does not silently degrade into a dispatch.
- [ ] `tools/sdlc_review_finalize.py` — WS-2's non-approved head lookup adds a failure path; assert it logs and records nothing rather than raising (the record-best-effort decision above).

### Empty/Invalid Input Handling
- [ ] `_review_verdict_head_is_stale` with: key absent, key `""`, verdict `""`, `recorded_head` absent, and `stage_states == {}`. All five must be pinned, since four of them are the predicate's fail paths.
- [ ] Terminal guard with an **empty** `stage_states` map — the `sdlc-2853` shape, where the ledger emptied after merge. This must not resurrect the lane into row 1.
- [ ] `critique_resume` with `want_hash is None` (unreadable plan) — currently marks every sibling stale.
- [ ] WS-4: every consumer with `SDLC_ISSUE_NUMBER` unset must fail loudly, not fall through to a default or an empty string in a commit message.

### Error State Rendering
- [ ] The `primary is None` misconfiguration message (`agent/sdlc_router.py:2063`) must still fire for a **genuine** `GH_REPO` / `SDLC_TARGET_REPO` misconfiguration after WS-1 narrows it — test both the merged-and-done path (terminal, no error) and the genuinely-unresolvable path (error preserved).
- [ ] `decision: "terminal"` must render distinctly from `decision: "blocked"` in `sdlc-tool next-skill` output and in the `/do-sdlc` loop's report.

## Test Impact

- [ ] `tests/unit/test_pipeline_state_machine.py:601-607::test_fail_critique_increments_critique_cycle_count` — UPDATE or DELETE: pins the only `critique_cycle_count` increment, which WS-3 retires or rewires. Currently green; must not be left to fail silently.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — UPDATE: WS-1 adds a guard/decision type; row-id ↔ SKILL.md parity is derived from rule docstrings and must be updated on both sides in the same commit.
- [ ] `tests/unit/sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py` — UPDATE: row 10 and row 5 behavior changes for merged lanes; add the merged-PR negative cases.
- [ ] `tests/unit/test_sdlc_router.py`, `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: G2's arming condition changes (WS-3); the guard list and evaluation order change (WS-1).
- [ ] `tests/unit/test_sdlc_next_skill.py` — UPDATE: a third output shape (`terminal`) joins `dispatch`/`blocked`/`error`.
- [ ] `tests/unit/test_sdlc_review_finalize.py`, `tests/integration/test_sdlc_review_finalize_roundtrip.py` — UPDATE: non-approved verdicts now carry `head_sha`; assert via `head_sha_of_record`, never `record["head_sha"] is None` (the key is absent, not null).
- [ ] `tests/unit/test_sdlc_verdict.py` — UPDATE: `_review_trailer_present` must still pass non-APPROVED without asserting a trailer (#2193 Risk 1); add an explicit regression test for that contract.
- [ ] `tests/unit/test_sdlc_env_vars.py:45-92` — UPDATE: `SDLC_ISSUE_NUMBER` / `SDLC_TRACKING_ISSUE` leave the env assembly (WS-4).
- [ ] `tests/unit/test_sdlc_fork_issue_number.py:217-240,251-288,371-394` — UPDATE: the guarded-ordering assertions change shape when the ambient export is deleted; `test_no_sdlc_issue_number_export` should generalize from `/do-sdlc` to all producers.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py:273-274` — UPDATE: asserts `SDLC_ISSUE_NUMBER` in the dispatch env.
- [ ] `tests/unit/test_critique_resume.py:472` — UPDATE: stale-list emission and GC scope change (WS-5).
- [ ] `tests/unit/test_do_plan_critique_barrier.py` — UPDATE: `_roster.json` gains an `independent` field (WS-6).
- [ ] `tests/unit/test_pipeline_graph.py:96,151-159` — UPDATE: `get_next_stage`'s critique-cycle cap (WS-3).
- [ ] `tests/unit/test_pipeline_complete_predicate.py` — UPDATE if WS-1 reuses `is_pipeline_complete`.
- [ ] `tests/unit/test_architectural_constraints.py` — NO CHANGE, but it enforces that `agent/sdlc_router.py` never imports from `tools/`; WS-1's terminal predicate must respect it.
- [ ] `tests/unit/test_skill_agent_tool_consistency.py` — NO CHANGE: its docstring explicitly declines to speak to `context: fork` tool stripping. WS-6 must not overclaim by editing it.

## Rabbit Holes

- **Rewriting `_build_context`'s fail-closed logic to explain the #2884 incident.** Recon
  confirmed it already behaves correctly. The CLI-path contradiction is unexplained, and
  chasing it means reconstructing lost state on a machine that had three reviewers on one PR
  within ten minutes. #2895 itself says: do not try to reproduce this live.
- **Fixing nested-fork tool inheritance for #2886.** It is harness behavior, not repo code.
  No file in this checkout controls it. Detect-and-announce is the deliverable.
- **Rebuilding the critique run-dir naming scheme.** The ns-timestamp already provides
  uniqueness; the defect is cleanup scope. The issue says to confirm this before touching
  naming, and recon did.
- **Adding a CHANGES-REQUESTED head-staleness *rule* while widening the *recording*.** It
  interacts with rows 8/8b ordering and G4's loop bound. Deliberately split — see No-Gos.
- **Backfilling `head_sha` onto historical verdict records.** Null degrades safely
  (unattributable → stale → re-review). A migration buys nothing and risks the substrate.
- **Unwedging #2734 / #2741 by hand.** Both closed on 2026-08-18. Any effort here is spent on
  a state that no longer exists.
- **Wave 3.** #3017 (`SDLC_STAGES` omits PATCH, `models/agent_session.py:81`) will be
  extremely tempting while in these files. Leave it — Wave 3 is deliberately not running and
  touches the same surfaces.
- **Writing an Open Question the task graph has already answered.** This plan did it three
  times — OQ1 (caught as a round-1 blocker), OQ3 (round 2), OQ2 (round 3) — each time
  committing to a direction in Technical Approach, Tasks, and Success Criteria while still
  asking the reader to decide. A builder reading the task list has no way to learn that
  sign-off is pending, and a reader answering the question finds the answer already built.
  If the plan commits, the question is a **notification**; if it genuinely does not, the task
  bullet and its Success Criterion must both be marked conditional. Check this before
  re-critiquing rather than after.

## Risks

### Risk 1: The terminal guard swallows a live pre-merge lane
**Impact:** Catastrophic and silent — a lane that legitimately needs `/do-merge` would be
declared finished and stop dispatching, which is a worse failure than the infinite
re-dispatch it replaces. This is the exact trade #2826's critique made when it cut the
terminal predicate.
**Mitigation:** #2817's measured four-cell `/do-merge` matrix is the mandatory negative
control, encoded as tests before the guard lands. The guard keys on positive evidence of
completion (`MERGE == completed`, or a resolvable merged PR state), never on absence of
signal — the same fail-closed discipline WS-2 applies to staleness.

**Post-ship signal and kill switch (critique round 3).** The mitigation above is all
pre-ship; a false positive after ship is *silent*, because the guard preempts the dispatch
table at `agent/sdlc_router.py:2032-2037` and a wrongly-halted lane is indistinguishable from
a legitimately finished one. The router has no existing `DISABLE_` pattern, so today's only
recovery is revert plus a fleet restart. Therefore: **log every terminal decision tagged with
which evidence branch fired** (`MERGE == completed` vs. resolvable-merged-PR), so a false
positive is greppable rather than invisible; and put the guard behind a named,
env-overridable constant so it can be switched off on one machine without a revert. Treat the
constant as provisional and tunable, per this repo's convention for magic numbers.

### Risk 2: Flipping the staleness default breaks lanes that relied on the fail-open
**Impact:** Lanes whose `pr_head_sha` cannot be resolved would newly route to `/do-pr-review`
instead of `/do-merge`, potentially adding a review lap to every lane on a network blip.
**Mitigation:** The flip is conditioned on a `pr_number` **and** a recorded verdict both
existing, matching `_build_context`'s own population condition, so it cannot fire where the
signal was never applicable. `resolve_pr_head_sha` is git-first, so it does not depend on
`gh` availability. Ship WS-2 behind its own PR so the blast radius is isolated.

### Risk 3: WS-4's deletion strands a consumer the sweep missed
**Impact:** A stage fails loudly at runtime with no issue number — visible, but disruptive
mid-lane.
**Mitigation:** The sweep is a grep across all file types (the issue's AC demands a sweep,
not a list) and it already found two consumers the issue itself missed. Add a Verification
row asserting zero remaining reads, so the check is mechanical and re-runnable rather than a
one-time audit. Fail-loudly is the *designed* outcome per the issue's desired state.

### Risk 4: Seven workstreams in one PR is unreviewable
**Impact:** Review quality collapses; the wave's own bugs (unbounded critique loops,
verdict-staleness) get reproduced by the process shipping their fix.
**Mitigation:** The three-PR split below. This is a PM decision and is flagged as an Open
Question.

### Risk 5: Wave 0 concurrency produces spurious red tests
**Impact:** Time lost re-diagnosing failures that belong to another lane.
**Mitigation:** Lane `wave0-signal-integrity` is fixing stale off-pin `.pyc` and PATH-shadow
problems that make grep-based tests spuriously red. Attribute that failure class to Wave 0 and
do **not** re-diagnose it. Run narrow-scope tests only — full-suite runs from parallel
worktrees collide on Redis state.

## Race Conditions

### Race 1: Stale-run GC deletes a concurrently-live critique run dir
**Location:** `docs/sdlc/do-plan-critique.md:51`; `tools/critique_resume.py:88-109`
**Trigger:** Two `/do-plan-critique` runs on the same issue with different plan hashes. Run B
probes, computes run A's dir as stale on a bare hash inequality, and `rm -rf`s it while A's
critics are still writing.
**Data prerequisite:** A's `_roster.json` and `.plan_hash` must survive until A's gate reads
them.
**State prerequisite:** "Stale" must mean *abandoned*, not merely *different-hash*.
**Mitigation:** Narrow the GC scope — exclude the caller's own run dir, and require positive
evidence of abandonment (mtime age, or a roster with no unreported members) rather than hash
inequality alone. Special-case `want_hash is None`, which currently marks every sibling stale.

### Race 2: Machine-global stale-list file collides across worktrees
**Location:** `/tmp/critique-resume-stale.txt` (fixed path), consumed by `xargs -r rm -rf`
**Trigger:** Concurrent critique runs in different worktrees. One overwrites the other's
stale list; `xargs rm -rf` then resolves those **relative** paths against the *caller's* cwd,
so a path computed in worktree A is deleted relative to worktree B.
**Data prerequisite:** The stale list must be readable only by the process that wrote it.
**State prerequisite:** Paths handed to `rm -rf` must be absolute or cwd-pinned.
**Mitigation:** Make the stale-list path unique per run (include the run dir's own suffix) and
emit absolute paths. This repo runs several agents concurrently by design, so a machine-global
temp path in a destructive pipeline is a standing hazard.

### Race 3: Post-approval commit lands between review finalize and router read
**Location:** `tools/sdlc_review_finalize.py` head-SHA capture vs. `agent/sdlc_router.py:1195`
**Trigger:** The #2884 shape — a commit lands after the verdict records but before the router
reads, so the recorded head is legitimately stale.
**Data prerequisite:** The verdict's `head_sha` must name the commit actually judged.
**State prerequisite:** The gate must fail closed when it cannot establish freshness.
**Mitigation:** This race is *correct* behavior for the gate to detect — the fix is that it
detect it rather than fail open. WS-2's default flip is the mitigation. Note this is the race
the original #2895 observation may have been, and it is not separately reproducible.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2869] Lane slug adoption / minted `sdlc-N` slugs naming branches that do
  not exist. Wave 3; touches the same files and is deliberately not running yet.
- [SEPARATE-SLUG #3017] `current_stage` can never return PATCH because `SDLC_STAGES`
  (`models/agent_session.py:81`) omits it. Wave 3, and named as its lead. Tempting while in
  these files; leave it.
- [SEPARATE-SLUG #2777] `/do-sdlc` step 3d.4 halts `REVIEW_VERDICT_MISSING` in repos
  declaring no verdict substrate. Wave 3; adjacent to WS-1's terminal handling but a
  foreign-repo concern, not a finished-lane concern.
- [SEPARATE-SLUG #2812] Investigation: ledger stage history for #2675 went empty after
  AgentSession recreation. Wave 3, and a reproduction lane rather than a fix lane. Relevant
  because WS-1 must tolerate an emptied ledger, but the cause is not in scope here.
- [SEPARATE-SLUG #2658] Gates that cannot fire — the general form of #2895 and the capstone
  of Wave 4. This plan honors its rule (a demonstrated-red test for #2895) without trying to
  generalize it.
- [ORDERED] Deploying the router change to running services. `./scripts/valor-service.sh restart`
  must follow the merge, and `/update` propagates to other machines — both are post-merge
  events gated on the PR landing, not build steps.

**Anti-criteria** for the code-level No-Gos are encoded as inverse rows in `## Verification`:
no bare `gh` head reads introduced, no `SDLC_ISSUE_NUMBER` reads left behind, and no changes
to `models/agent_session.py`'s `SDLC_STAGES` (the #3017 boundary).

## Update System

The `/update` skill needs no changes: no new dependencies, config files, or migration steps.

Two propagation facts do matter, and belong in the PR description rather than in code:

- **WS-6 edits `.claude/skills-global/do-plan-critique/SKILL.md`**, which is hardlinked to
  `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`). Recon verified the
  hardlink is currently intact (inode 181729529). Per this repo's known failure mode, a
  Write/Edit replace-and-rename can break that hardlink and silently leave the live skill on
  pre-edit text; a PostToolUse relink hook auto-repairs it. Verify the inode still matches
  after editing.
- **After merge, running services must be restarted** — the router, `sdk_client`, and the
  session runner all execute inside the bridge/worker. `./scripts/valor-service.sh restart`,
  then confirm `tail -5 logs/bridge.log` shows "Connected to Telegram". This is a post-merge
  step, listed as an `[ORDERED]` No-Go.

No Popoto model fields change, so no migration is needed in `scripts/update/migrations.py`.

## Agent Integration

No new CLI entry point and no new MCP surface. Every changed component is already reachable:

- `sdlc-tool next-skill` (`tools/sdlc_next_skill.py`) is an existing `pyproject.toml`
  entry point. WS-1 adds a third **output shape** (`decision: "terminal"`) to it, not a new
  command. The consumers are `/do-sdlc` and `/sdlc`, which parse its JSON — both skill bodies
  must be updated in the same PR or the router will emit a shape the loop treats as an error.
  `.claude/skills-global/do-sdlc/SKILL.md`'s loop currently instructs: non-lock `blocked` →
  STOP and report `reason` + `guard_id`. Terminal must be a **clean** exit, distinct from that
  error stop.
- `agent/sdlc_router.py` and `agent/pipeline_state.py` are imported directly by the worker.
- WS-4 **removes** an agent-facing surface (two env vars the skills read). The four markdown
  consumers must be updated in the same commit as the producer, or a stage loses its issue
  number mid-lane.

Integration coverage: `tests/integration/test_sdlc_pipeline_lock.py` and
`tests/integration/test_sdlc_review_finalize_roundtrip.py` already exercise the substrate
end-to-end and must be extended for the terminal decision and the widened verdict record.

## Documentation

- [ ] Update [`docs/features/gh-stale-state-verdict-gate.md`](../features/gh-stale-state-verdict-gate.md)
      — the new status quo: which verdicts **record** a head SHA and which **assert** a
      trailer are now different sets; and the staleness default now fails closed with an
      explicit opt-out.
- [x] Create `docs/features/sdlc-terminal-lane-state.md` — what makes a lane terminal, why
      the terminal guard preempts the dispatch table rather than living in a row, and the
      three meanings of `pr_merge_state == "UNKNOWN"`.
- [x] Update [`docs/features/pipeline-state-machine.md`](../features/pipeline-state-machine.md)
      — WS-7's refusal of mid-pipeline entry, and the closed plan-archival escape hatch.
- [x] Update [`docs/features/off-pipeline-merge-path.md`](../features/off-pipeline-merge-path.md)
      — `skip_stage`'s preconditions after the archival hole is closed.
- [ ] Update `docs/sdlc/do-plan-critique.md` — GC scope, the per-run stale-list path, and the
      `independent:` roster field.
- [ ] Update `.claude/skills-global/do-plan-critique/SKILL.md` — the `independent:` field and
      its announcement in the verdict block. **Verify the hardlink inode after editing.**
- [x] Update `.claude/skills-global/do-sdlc/SKILL.md` and `.claude/skills/sdlc/SKILL.md` —
      the terminal decision shape and its clean-exit handling.
- [ ] Update `docs/sdlc/do-patch.md` and the two `do-pr-review` sub-skills — explicit issue
      resolution replacing the ambient env var (WS-4).
- [ ] Update [`docs/bug-backlog-waves.md`](../bug-backlog-waves.md) — correct the "all nine
      touch `agent/sdlc_router.py`" claim, which is false, and record what shipped.
- [x] Add entries to [`docs/features/README.md`](../features/README.md) for the new feature doc.
- [ ] Delete `docs/plans/next-skill-terminal-verdict-and-pr-resolution.md` (superseded; its
      siblings #2825/#2824 are closed and #2817 is absorbed here).
- [ ] Inline: docstrings for the terminal guard, the staleness opt-out contract, and the
      record-best-effort posture on the non-approved verdict path — each stating *why*, since
      all three are decisions this plan made deliberately over an inherited alternative.

## Success Criteria

- [ ] A lane with `MERGE == completed` produces `decision: "terminal"` from
      `sdlc-tool next-skill`, not a dispatch and not `NO_RULE`. (#2894, #2817)
- [ ] The same holds for the four measured post-merge misroutes — rows 1, 5, 8f, and 10 — not
      only row 10.
- [ ] The four live pre-merge `/do-merge` routes from #2817's matrix still dispatch
      `/do-merge`. (negative control)
- [ ] A merged lane no longer emits the `GH_REPO` / `SDLC_TARGET_REPO` misconfiguration
      message, while a genuinely unresolvable one still does.
- [ ] **#2895 demonstrated-red:** a test drives `decide_next_dispatch(stage_states, meta)`
      with no `context` on a head-stale APPROVED verdict, **fails before the fix** (returns
      `/do-merge` via G6) and **passes after**. Red output pasted into the PR description. If
      it cannot be made red, #2895 stays OPEN and the PR says so.
- [ ] A call-contract test enumerates every production `decide_next_dispatch` call site and
      asserts each supplies the staleness signal or explicitly opts out.
- [ ] All **three** inheritors of `_review_verdict_head_is_stale` (G6, row 8f, row 10) are
      pinned against key-absent, key-empty, and mismatched-head.
- [ ] A CHANGES REQUESTED verdict carries a non-null `head_sha`, surfaced as
      `_meta.latest_review_head_sha`; asserted via `head_sha_of_record`, not `record["head_sha"]`.
      Same for `BLOCKED ON CONFLICT` and `PR CLOSED`. (#2850)
- [ ] `_review_trailer_present` still returns `True` for non-APPROVED verdicts without
      asserting a trailer — #2193 Risk 1 preserved, with an explicit test.
- [ ] The APPROVED path is byte-for-byte unchanged in behavior: same fail-closed posture,
      same idempotent trailer lift, same recorded value.
- [ ] The plan↔critique lap is bounded by a mechanism with a live writer, proven by a test
      that drives N rounds through the **router** path (not `fail_stage`). (#2885)
- [ ] Zero reads of `SDLC_ISSUE_NUMBER` / `SDLC_TRACKING_ISSUE` remain outside tests and
      archived docs, proven by a grep sweep in `## Verification`; every former consumer
      resolves explicitly or fails loudly. (#2849)
- [ ] `docs/sdlc/do-patch.md:62`'s pre-PR commit path resolves its issue number explicitly.
- [ ] The stale-run GC cannot delete the caller's own run dir or a live sibling; the stale
      list is per-run and absolute. (#2832)
- [ ] `_roster.json` records `independent:` and the gate surfaces it; a serial roster
      announces itself instead of reporting N/N as though N agents ran. (#2886)
- [ ] Mid-pipeline entry at a non-ISSUE stage is refused **on positive evidence** (plan doc, or
      a verdict with no marker) rather than on stages-map emptiness, and a
      detector flags the shape. The verdict invariant (#2415) and #2554 precedence still hold
      — proven by a test that a verdict-less REVIEW/CRITIQUE cannot be force-completed. (#2851)
- [ ] Archiving a plan document no longer makes a lane's CRITIQUE retroactively skippable.
- [ ] **Replay against the lanes recon actually measured** (added in critique round 1 — every
      other criterion above is synthetic). For each of the four real post-merge lanes whose
      ledgers recon read at `fc97f7318` — **#2734** (PR #2844, merged; currently routes row 8f
      `/do-pr-review`), **#2741** (PR #2842, merged; same), **#2853** (PR #2884, merged; ledger
      emptied, routes row 1 `/do-plan`), and **#2636** (PR #2833, merged) — `sdlc-tool next-skill`
      returns a terminal decision after the fix. These are the observed failures; a fix that
      only satisfies constructed fixtures has not been shown to solve the reported problem.
- [ ] A fresh lane at ISSUE with an empty stages map still routes to `/do-plan` (row 1) and is
      **not** refused by WS-7's mid-pipeline-entry check.
- [ ] Tests pass (`/do-test`, narrow scope)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (terminal-state)** — Name: `terminal-builder` · WS-1 · Agent Type: `builder` · Resume: true
- **Builder (staleness)** — Name: `staleness-builder` · WS-2 · Agent Type: `builder` · Resume: true
- **Builder (critique-loop)** — Name: `critique-builder` · WS-3, WS-5, WS-6 · Agent Type: `builder` · Resume: true
- **Builder (issue-number)** — Name: `issuenum-builder` · WS-4 · Agent Type: `builder` · Resume: true
- **Builder (pipeline-entry)** — Name: `entry-builder` · WS-7 · Agent Type: `builder` · Resume: true
- **Test engineer (red-first)** — Name: `red-test-engineer` · Authors the #2895 demonstrated-red and the call-contract test **before** WS-2's fix · Agent Type: `test-engineer` · Resume: true
- **Validator** — Name: `wave2-validator` · Agent Type: `validator` · Resume: true
- **Documentarian** — Name: `wave2-documentarian` · Agent Type: `documentarian` · Resume: true

**Fan-out constraint:** this session owns exactly one worktree
(`.worktrees/wave2-router-verdict-integrity`). Builders run against that single worktree with
**disjoint file sets** so their commits never interleave. Every subagent spawn passes
`run_in_background: false`.

**Three workstreams touch `agent/sdlc_router.py` and must be strictly serialized:**

| WS | Router surface |
|---|---|
| WS-1 | `Terminal` type, terminal guard, `evaluate_guards`, `primary is None` fallback |
| WS-2 | `_review_verdict_head_is_stale` and its three inheritors (G6, row 8f, row 10) |
| WS-3 | G2 (`:359-382`) — its arming condition changes when the bound moves to the verdict trail |

**WS-3 is the correction from critique round 1.** It was originally marked
`Parallel: true` / `Depends On: none` and grouped into a PR described as "disjoint from PR 1's
files." That was wrong: a verdict-trail bound necessarily lives in the router, so WS-3 shares
`agent/sdlc_router.py` with WS-1 and WS-2. It is now sequenced after both, and Open Question 1
records that **PR 2 must merge after PR 1** rather than being independent of it. The remaining
PR-2 workstreams (WS-5, WS-6) touch disjoint file sets — WS-5 edits `tools/critique_resume.py`
and `docs/sdlc/do-plan-critique.md`, WS-6 edits `.claude/skills-global/do-plan-critique/SKILL.md`
— but the task graph runs them **sequentially**, because both are assigned to the single
`critique-builder` identity and Task 7 declares `Depends On: build-rundir`. That is
sequential-but-independent, not parallel; corrected in critique round 2, which caught the
prose claiming otherwise. If PR 2 needs to go faster, give WS-6 its own builder identity and
drop Task 7's edge — the files permit it.

Serialization order on the router: **WS-1 → WS-2 → WS-3.** WS-4 and WS-7 touch no router file
and remain freely parallel.

## Step by Step Tasks

### 1. Red-first: pin the staleness fail-open
- **Task ID**: red-staleness
- **Depends On**: none
- **Validates**: `tests/unit/sdlc_router_decision/test_sdlc_router_decision_verdict_staleness.py` (extend)
- **Assigned To**: red-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true
- Write the #2895 demonstrated-red: `decide_next_dispatch(stage_states, meta)` with no `context`, head-stale APPROVED verdict, assert NOT `/do-merge`. Confirm it FAILS at `fc97f7318` and capture the output.
- Write the call-contract test enumerating every production `decide_next_dispatch` call site.
- Pin all three inheritors (G6, row 8f, row 10) against key-absent / key-empty / mismatched-head.
- Fake `git ls-remote`, not just `gh` — see `docs/sdlc/do-test.md`.

### 2. WS-1 — terminal lane state
- **Task ID**: build-terminal
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_router.py`, `tests/unit/sdlc_router_decision/test_sdlc_router_decision_dispatch_rows.py`, `tests/unit/test_sdlc_next_skill.py`, `tests/unit/test_sdlc_skill_md_parity.py`
- **Informed By**: recon (four post-merge misroutes: rows 1/5/8f/10; merged PR reports `mergeable: UNKNOWN`)
- **Assigned To**: terminal-builder
- **Agent Type**: builder
- **Parallel**: false
- Encode #2817's four-cell pre-merge `/do-merge` matrix as the negative control FIRST.
- Add the `Terminal` outcome type and the terminal guard in `evaluate_guards`.
- Log every terminal decision tagged by which evidence branch fired, and put the guard behind a named env-overridable constant so a false positive is greppable and switchable without a revert (critique round 3, Risk 1).
- Gate the `sdlc_stage_query.py` rung on positive evidence the lane once existed, or cache it — **not** on bare `session is None`, which fires on every poll for a fresh issue (critique round 3).
- Handle the emptied-ledger shape (`sdlc-2853`) so a lost ledger does not resurrect a lane. **This requires a change in `tools/sdlc_stage_query.py`, not the router:** add a ledger-less merged-PR rung before `query_enriched`'s `return {"stages": {}, "_meta": _default_meta()}` at `:836-837`, which currently short-circuits `_compute_meta`'s merged fallback at `:622-624`. Verified: `_lookup_pr(2853, state="merged")` returns 2884, but `query_enriched(2853)` yields `pr_number=None`. Without this the #2853 replay criterion cannot pass.
- Narrow the `primary is None` fallback so merged-and-done is success, not unresolvable mergeability.
- Add `decision: "terminal"` to `sdlc-tool next-skill`; update both SKILL.md loops for a clean exit.
- Fix the `_rule_ready_to_merge` docstring drift (parity-tested).

### 3. WS-2 — staleness seam
- **Task ID**: build-staleness
- **Depends On**: red-staleness, build-terminal
- **Validates**: `tests/unit/test_sdlc_review_finalize.py`, `tests/unit/test_sdlc_verdict.py`, `tests/integration/test_sdlc_review_finalize_roundtrip.py`
- **Assigned To**: staleness-builder
- **Agent Type**: builder
- **Parallel**: false
- Flip the default to fail closed when `pr_number` **and** a recorded verdict both exist; add the explicit opt-out.
- Pass context at `agent/session_runner/runner.py:1403` and make its swallow log observably.
- Record `head_sha` on all three non-approved verdict tokens via `resolve_pr_head_sha`, record-best-effort on failure.
- Leave `_review_trailer_present` APPROVED-scoped; add its regression test.
- **Fix the bare `gh` head read at `.claude/skills-global/do-pr-review/sub-skills/code-review.md:22,30`.** Measured red by this plan's anti-criterion: `HEAD_SHA=$(gh pr view $PR_NUMBER --json headRefOid --jq .headRefOid)` is the SHA the review embeds in its `REVIEW_CONTEXT head_sha=` marker, so a stale `gh` read here matches the recorded trailer and flips this very gate from fail-closed to fail-open. Route it through `resolve_pr_head_sha`.
- Confirm the red test from task 1 now passes; paste before/after into the PR.

### 4. Validate the router seam
- **Task ID**: validate-router-seam
- **Depends On**: build-terminal, build-staleness
- **Assigned To**: wave2-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify the demonstrated-red went red then green; verify the negative control; verify no bare `gh` head reads were introduced.

### 5. WS-3 — critique loop bound
- **Task ID**: build-critique-bound
- **Depends On**: build-staleness *(shares `agent/sdlc_router.py` — G2's arming condition. Do NOT run concurrently with WS-1/WS-2.)*
- **Validates**: `tests/unit/test_pipeline_state_machine.py`, `tests/unit/test_pipeline_graph.py`, `tests/unit/test_sdlc_router_oscillation.py`, `tests/unit/test_sdlc_router.py`
- **Assigned To**: critique-builder
- **Agent Type**: builder
- **Parallel**: false
- Bound on the verdict trail; retire or rewire the vestigial counter deliberately.
- Name the bound as an env-overridable constant with a provisional/tunable comment.
- Update or delete `test_fail_critique_increments_critique_cycle_count` deliberately.

### 6. WS-5 — critique run-dir integrity
- **Task ID**: build-rundir
- **Depends On**: none
- **Validates**: `tests/unit/test_critique_resume.py`
- **Assigned To**: critique-builder
- **Agent Type**: builder
- **Parallel**: true
- Narrow the GC: exclude the caller's own dir; require positive evidence of abandonment; special-case `want_hash is None`.
- Make the stale-list path per-run and emit absolute paths.
- Close the missing-`.plan_hash` never-resumable-never-GC'd accumulation.
- Do NOT build the "guard against silent roster loss" the issue asks for — that crux is falsified.

### 7. WS-6 — roster independence
- **Task ID**: build-roster
- **Depends On**: build-rundir
- **Validates**: `tests/unit/test_do_plan_critique_barrier.py`
- **Assigned To**: critique-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `independent:` to `_roster.json`, read it in `critique_roster_check`, surface it in the verdict block.
- Do NOT attempt to fix nested-fork tool inheritance. Do NOT edit `test_skill_agent_tool_consistency.py`.
- Verify the SKILL.md hardlink inode after editing.

### 8. WS-4 — explicit issue number
- **Task ID**: build-issuenum
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_env_vars.py`, `tests/unit/test_sdlc_fork_issue_number.py`, `tests/unit/test_session_executor_runner_dispatch.py`
- **Assigned To**: issuenum-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete the export from `agent/sdk_client.py:491-498`; `SDLC_TRACKING_ISSUE` is a pure delete (no live consumer).
- Update all four un-guarded consumers, including `docs/sdlc/do-patch.md:62` and the dead `session_completion.py` path.
- **Clear all 18 sweep hits across 8 files**, including the three contract-prose hits at `do-pr-review/SKILL.md:56,101` and `docs/sdlc/do-pr-review.md:34` — named in critique round 2, which caught that the anti-criterion could not reach 0 while they went unlisted. See the sweep accounting in WS-4's Technical Approach.
- Close the `checkout.md:41-43` fall-through so it fails loudly.
- Generalize `test_no_sdlc_issue_number_export` from `/do-sdlc` to all producers.

### 9. WS-7 — mid-pipeline entry
- **Task ID**: build-entry
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_stage_marker.py`, `tests/unit/test_pipeline_state_machine.py`, `tests/integration/test_off_pipeline_merge_path.py`
- **Assigned To**: entry-builder
- **Agent Type**: builder
- **Parallel**: true
- Implement direction (b): refuse a non-ISSUE dispatch **on positive evidence of mid-pipeline entry** — a dispatch at a non-ISSUE stage combined with a recorded artifact implying skipped predecessors (a plan document, or a recorded verdict with no corresponding marker). **Never on stages-map emptiness alone**, which cannot separate a fresh issue from an evicted lane; see WS-7 Technical Approach. Add the detector, and have it report *which* signal was missing.
- Thread an explicit recovery-attempted/failed signal through `meta` or `context` so the refusal can tell a failed ledger recovery from a genuinely fresh lane. Add a test covering the recovery-then-refuse ordering.
- Close the plan-archival escape hatch in `find_plan_path` / `_qualifies_as_never_dispatched`. **This is mandatory scope, not optional** — see Open Question 3, which is a notification rather than a decision.
- Pin the reproduction as **verdict-recorded-without-marker**, not "empty stages map".
- Prove the verdict invariant and #2554 precedence still hold.

### 10. Documentation
- **Task ID**: document-wave2
- **Depends On**: all build tasks
- **Assigned To**: wave2-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the `## Documentation` checklist, including correcting `docs/bug-backlog-waves.md`.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: all previous
- **Assigned To**: wave2-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every `## Verification` row; confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Router tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/sdlc_router_decision/ -q` | exit code 0 |
| Next-skill tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_next_skill.py -q` | exit code 0 |
| Verdict tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_verdict.py tests/unit/test_sdlc_review_finalize.py -q` | exit code 0 |
| Pipeline-state tests pass | `scripts/pytest-clean.sh tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_graph.py -q` | exit code 0 |
| Env-var tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_env_vars.py tests/unit/test_sdlc_fork_issue_number.py -q` | exit code 0 |
| Critique tests pass | `scripts/pytest-clean.sh tests/unit/test_critique_resume.py tests/unit/test_do_plan_critique_barrier.py -q` | exit code 0 |
| Architectural constraint holds | `scripts/pytest-clean.sh tests/unit/test_architectural_constraints.py -q` | exit code 0 |
| SKILL.md row parity holds | `scripts/pytest-clean.sh tests/unit/test_sdlc_skill_md_parity.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Router still imports nothing from tools | `grep -c "^from tools\|^import tools" agent/sdlc_router.py` | match count == 0 |
| **Anti-criterion:** no bare `gh` head read on the review gating path | `grep -rn 'gh pr view' .claude/skills-global/ \| grep -c headRefOid` | match count == 0 |
| **Anti-criterion:** ambient issue env fully removed | `grep -rn "SDLC_ISSUE_NUMBER\|SDLC_TRACKING_ISSUE" agent/ tools/ .claude/skills-global/ docs/sdlc/ \| wc -l` | match count == 0 |
| **Anti-criterion:** #3017 boundary respected (Wave 3) | `git diff origin/main -- models/agent_session.py \| grep -c SDLC_STAGES` | match count == 0 |
| Terminal decision reachable | `python -c "from agent.sdlc_router import decide_next_dispatch as d; print(d({'MERGE':'completed'},{}).__class__.__name__)"` | output contains Terminal |

**Red-state proof (measured at `fc97f7318`, before any fix).** Each anti-criterion was run
against the unfixed tree to prove it can fail — per #2658's demonstrated-red discipline, a
verification row that has never been observed red is not a gate:

| Row | Value today | Meaning |
|---|---|---|
| bare `gh` head read | **2** (`do-pr-review/sub-skills/code-review.md:22` and `:30`) | RED — a real bare read on a gating path, must reach 0 |
| ambient issue env | **18** | RED — must reach 0 |
| terminal decision reachable | prints `Dispatch` | RED — must print `Terminal` |
| router imports nothing from `tools/` | **0** | already GREEN — this row guards a regression, it is not a fix target |

Two earlier drafts of the `gh`-head row were discarded because they matched **docstring
prose** rather than command position: all three `headRefOid` hits in `agent/` and `tools/`
(`sdlc_review_finalize.py:178`, `pr_head_resolver.py:105,148`) are documentation text, not
invocations. That is the same text-vs-position defect Wave 4's #2736/#3021 are filed for;
do not reintroduce it here.

## Critique Results

**Round 1** — FULL war room (Risk & Robustness, Scope & Value, History & Consistency) at plan hash `sha256:5c53696d…`, baseline `df48861c7`. Verdict: **NEEDS REVISION** (1 blocker, 5 concerns, 0 nits).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | History & Consistency | WS-3 edits `agent/sdlc_router.py` (G2 at `:359-382`, per this plan's own Test Impact row "G2's arming condition changes (WS-3)"), but Team Orchestration names only WS-1/WS-2 as the pair that "must not run concurrently", task 5 `build-critique-bound` is `Parallel: true` / `Depends On: none`, and Open Question 1 asserts PR 2 is "Disjoint from PR 1's files". Three sections contradict each other, and the fan-out constraint's "commits never interleave" guarantee does not hold. | addressed in the round-1/round-2 revision passes | `guard_g2_critique_cycle_cap` is defined at `agent/sdlc_router.py:359` — the same file `terminal-builder` edits for the `Terminal` outcome. Either add `Depends On: build-terminal, build-staleness` to task 5, or scope WS-3 to `agent/pipeline_state.py` + `agent/pipeline_graph.py` only and leave G2 untouched this wave; then correct the "Disjoint from PR 1's files" claim in Open Question 1 and the Test Impact row for `test_sdlc_router.py`. |
| CONCERN | Risk & Robustness, History & Consistency | WS-1 step 3 says to "reuse its logic rather than writing a second predicate" for `is_pipeline_complete`, but that function is not a drop-in: its signature is `is_pipeline_complete(psm_states, outcome, pr_open=None)` and it returns `(False, "outcome_not_success")` unless `outcome == "success"` is passed, while `decide_next_dispatch(stage_states, meta, context)` has no `outcome` concept. It also has no branch expressing "resolvable merged PR state", which is the other half of the plan's own terminal predicate. Its existing production consumer `agent/session_runner/completion_guard.py:155` is never mentioned. | r1: WS-1 now says it is NOT a drop-in (needs `outcome`, no merged-PR branch); extract shared helper instead | Verified: `agent/pipeline_complete.py:35-86` imports only stdlib (so the no-`tools`-import constraint is safe), and its only non-MERGE branch is `docs_state == "completed" and merge_state != "completed"` gated on a caller-supplied `pr_open` bool — a different signal from "merged PR". State explicitly that the guard calls `is_pipeline_complete(stage_states, "success", pr_open=None)` for the MERGE-completed branch ONLY and writes a separate merged-PR check for the lost-ledger branch. Do not widen `is_pipeline_complete`'s contract: `completion_guard.py:155` is the PM final-delivery gate and would inherit any change. |
| CONCERN | Risk & Robustness, History & Consistency, structural check | The `## Verification` anti-criterion row `grep -rnc "SDLC_ISSUE_NUMBER\\|SDLC_TRACKING_ISSUE" agent/ tools/ .claude/skills-global/ docs/sdlc/` with Expected "match count == 0" is not mechanically checkable: `grep -rnc` over multiple directories emits one count per file. Driver-measured: 411 output lines, real total 18. This row is WS-4's Risk-3 mitigation and the completion gate for the plan's self-declared least-reversible workstream. | r1: row changed to `grep -rn ... | wc -l`; red-state proof table added (18) | Replace the command with an aggregating form, e.g. `grep -rn "SDLC_ISSUE_NUMBER\\|SDLC_TRACKING_ISSUE" agent/ tools/ .claude/skills-global/ docs/sdlc/ \\| wc -l`, which reads **18** today (matching the Red-state proof table) and must reach 0. Note `grep -c` exits 1 on zero matches, so a `set -e` validator wrapper must tolerate that exit code. |
| CONCERN | Risk & Robustness | WS-7's "refuse a non-ISSUE dispatch on an empty stages map" targets exactly the shape that `tools/sdlc_next_skill.py`'s ledger-recovery path already treats as *ambiguous* rather than illegitimate. The guard cannot distinguish "genuinely fresh issue" from "mid-pipeline lane whose Redis ledger was evicted and whose recovery failed", so it can convert a recoverable degraded state into a hard block. No task-9 test covers the recovery-then-refuse ordering. | r1+r2: WS-7 refuses on positive evidence, never on emptiness; r2 added a recovery-attempted signal + ordering test | Verified call order: `_recover_stage_states_from_durable_signals` is defined at `tools/sdlc_next_skill.py:526` and invoked at `:712-718` (`if recovered: stage_states = recovered`), *before* `decide_next_dispatch(...)` at `:722`. The router therefore only ever sees an empty map when recovery already failed, and `stage_states` alone cannot separate the two causes — thread an explicit recovery-attempted/failed flag through `meta` or `context`, or document the hard-block as an accepted regression. |
| CONCERN | Scope & Value | Open Question 1 frames the three-PR split as genuinely undecided ("Confirm this split, or direct a single PR"), but `## Step by Step Tasks` already hard-codes it: task 4 is named `validate-pr1` and depends only on `build-terminal, build-staleness`. A "single PR" answer therefore requires re-authoring the task graph, not a one-line answer. | r1: task 4 renamed `validate-router-seam` | Task 4 (`validate-pr1`) and task 11 (`validate-all`) are the two checkpoints that presuppose the split; a single-PR answer deletes task 4 rather than renaming it. Either commit to the split in `## Solution` and demote Open Question 1 to a notification, or rename task 4 to a split-agnostic gate (e.g. `validate-router-seam`) so PR packaging is decided after the build graph. |
| CONCERN | Scope & Value | Every `## Success Criteria` row is a unit-test assertion, a grep count, or a synthetic `decide_next_dispatch` call. None validates the plan's stated real pain ("burn tokens on live lanes right now") against a lane the recon actually measured, even though the plan names concrete evidence lanes and four measured misroute rows. | r1: added replay criteria against #2734/#2741/#2853/#2636 and a fresh-lane negative control | The lost-ledger lane and its shape (`pr_number: null`, `plan_exists: false`) are already documented in WS-1 direction 3; add one criterion that runs `sdlc-tool next-skill` against that real lane post-fix and asserts `decision: "terminal"`. Note this does NOT conflict with the `## Rabbit Holes` entry forbidding live reproduction — that entry scopes only the #2884 CLI-path incident. |

**Round 2** — FULL war room (Risk & Robustness, Scope & Value, History & Consistency) at plan hash `sha256:6c84b0a1…`, baseline `7eb250cc9`. Verdict: **NEEDS REVISION** (1 blocker, 4 concerns, 1 nit). All six round-1 findings were independently verified as genuinely fixed in the plan prose; the round-2 findings are new, except where a round-1 fix landed in one section but not in the task graph.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness, driver-verified | The success criterion added in round 1 to ground the plan in real evidence — "Replay against the lanes recon actually measured", which requires lane **#2853** to return a terminal decision — is unsatisfiable by the mechanism WS-1 describes. WS-1 direction 3 defines terminal as `MERGE == completed` **or** "a resolvable PR in a merged state", but for a lane whose ledger has emptied the router receives **neither** signal, and no step in WS-1 adds one. Driver-measured at `7eb250cc9`: `query_enriched(2853)` returns `stages={}`, `pr_number=None`, `pr_merge_state=None`. | r2: WS-1 now adds a ledger-less merged-PR rung in tools/sdlc_stage_query.py before the :836-837 early return | The merged-PR resolver already exists and works — driver-verified: `_lookup_pr(2853, repo="tomcounsell/ai", state="merged")` returns **2884**, while `state="open"` returns `None`. The gap is that it is never reached: `tools/sdlc_stage_query.py::query_enriched` returns `{"stages": {}, "_meta": _default_meta()}` at `:836-837` when `_resolve_issue_record` finds no row, short-circuiting `_compute_meta` and its two-pass merged fallback at `:622-624`. So the fix belongs in the **meta-assembly path** (`tools/sdlc_stage_query.py`), not in `agent/sdlc_router.py` — the router cannot import from `tools/` (`tests/unit/test_architectural_constraints.py`). Either add a ledger-less merged-PR rung before that early return and thread `pr_number` / `pr_merge_state` into `_meta`, or downgrade the #2853 replay row to a documented known-gap. Do not leave a mandatory criterion whose data path does not exist. |
| CONCERN | History & Consistency | Round 1's WS-7 correction landed **only in the Technical Approach prose**. Task 9's first bullet still instructs the builder to "refuse a non-ISSUE dispatch on an empty stages map", and Success Criteria `:817` still reads "an empty stages map is refused" — the exact pre-correction framing that WS-7 `:536` now explicitly rejects ("The refusal cannot key on 'empty stages map' alone — critique round 1 caught this"). A builder executing the task list literally builds the guard the critique already rejected, which would refuse every fresh lane at ISSUE. | r2: Task 9 bullet 1 and the Success Criterion both rewritten to the positive-evidence predicate | Rewrite Task 9 bullet 1 (`:973`) and the Success Criteria row (`:817`) to the corrected predicate from `:544-549`: refuse on **positive evidence** of mid-pipeline entry — a dispatch at a non-ISSUE stage combined with a recorded artifact implying skipped predecessors (a plan document, or a recorded verdict with no corresponding marker) — never on stages-map emptiness. Note `_recover_stage_states_from_durable_signals` (`tools/sdlc_next_skill.py:526`, invoked `:712-718`) runs before `decide_next_dispatch` at `:722`, so emptiness alone cannot separate a fresh issue from an evicted lane. |
| CONCERN | Scope & Value | The plan-archival escape hatch is simultaneously **mandatory scope** and an **open question**. WS-7 says it "must be closed in the same change", Success Criteria `:820` makes it a hard gate, and Task 9 bullets it unconditionally — yet Open Question 3 asks whether it should be split into its own issue and fast-tracked ahead of the wave. This is the identical Open-Question-vs-task-graph contradiction round 1's BLOCKER caught for Open Question 1; that instance was fixed, this one recurs. The scope was also discovered by recon, not asked for by #2851's filed AC, and is not filed as an issue. | r2: OQ3 reframed from a live decision into a notification, while Task 9's bullet and the Success Criteria row were deliberately left unconditional as mandatory WS-7 scope | Task 9's bullet at `:974` ("Close the plan-archival escape hatch in `find_plan_path` / `_qualifies_as_never_dispatched`") and the Success Criteria row at `:820` carry no conditional language, so a builder has no way to discover that PM sign-off on OQ3 is pending. Either commit to closing it inside WS-7 and demote OQ3 to a notification (matching the OQ1 fix), or file it as its own issue and mark both the task bullet and the criterion conditional on that answer. `find_plan_path` (`tools/lane_identity.py:123-163`) searches only `docs/plans/`, which is the whole mechanism. |
| CONCERN | History & Consistency | Team Orchestration `:864` states "The remaining PR-2 workstreams (WS-5, WS-6) are genuinely disjoint and stay parallel" and Open Question 1 `:1056` repeats it, but the task graph hard-serializes them: Task 7 (`build-roster`, WS-6) declares `Depends On: build-rundir` and `Parallel: false`, and Tasks 6 and 7 share the single agent identity `critique-builder`, which cannot execute two tasks concurrently regardless of the dependency edge. Same prose-vs-task-graph contradiction pattern round 1's BLOCKER flagged for WS-3. | r2: prose corrected to sequential-but-independent | Task 6 `:931-941` (`Assigned To: critique-builder`, `Parallel: true`) and Task 7 `:943-950` (`Assigned To: critique-builder`, `Depends On: build-rundir`, `Parallel: false`). Either drop the "genuinely disjoint / stay parallel" language and describe WS-5→WS-6 as sequential-but-independent (what the graph actually encodes), or assign WS-6 to a distinct builder identity and remove Task 7's dependency. Note WS-6 edits `.claude/skills-global/do-plan-critique/SKILL.md` while WS-5 edits `tools/critique_resume.py` + `docs/sdlc/do-plan-critique.md`, so the file sets really are disjoint — only the agent identity and the declared edge serialize them. |
| CONCERN | structural check, driver-verified | The `## Verification` anti-criterion "ambient issue env fully removed" (Expected: `match count == 0`) cannot reach 0 given the files the tasks touch. Driver-measured at `7eb250cc9`: the sweep returns **18** hits across 8 files, but three of them live in `.claude/skills-global/do-pr-review/SKILL.md:56,101` and `docs/sdlc/do-pr-review.md:34` — files named **nowhere** in the plan: not in WS-4's consumer table, not in Task 8, not in the `## Documentation` checklist. This is the same class of defect round 1 caught in this row (a Risk-3 mitigation that is not mechanically achievable), one layer down. | r2: all 18 hits enumerated by file in WS-4; the 3 prose hits added to Task 8 | Verified per-file counts: `checkout.md` 5, `code-review.md` 3, `sdk_client.py` 3, `do-pr-review/SKILL.md` **2**, `session_completion.py` 2, `post-review.md` 1, `do-patch.md` 1, `docs/sdlc/do-pr-review.md` **1** = 18. The two unlisted files carry prose references (`$SDLC_ISSUE_NUMBER` named in an env-var list and in a "last resort only" instruction), so they are doc edits, not code changes — add both to Task 8's bullet list and to the `## Documentation` checklist, or scope the anti-criterion's grep to exclude prose-only surfaces. Leaving it as-is makes WS-4's completion gate unpassable. |
| NIT | structural check | All six round-1 findings still carry `pending` in the `Addressed By` column, though the plan prose shows every one of them was actually addressed (verified independently by all three round-2 critics). Per `docs/sdlc/do-plan-critique.md`, the revision pass is what fills that column; leaving it at `pending` erases the audit trail of which revision answered which finding. | r2: this column filled | The revision pass should replace each `pending` with the section(s) it edited (e.g. `Team Orchestration + task 5 + OQ1` for the round-1 BLOCKER). Round 2 preserves round 1's table rather than overwriting the section, so both rounds' trails stay auditable. |

**Round 3** — FULL war room (Risk & Robustness, Scope & Value, History & Consistency), dispatched concurrently in a single message (`_roster.json` `independent: true`), at plan hash `sha256:46e56aa2…`, baseline `f3184a7df`. Verdict: **READY TO BUILD (with concerns)** (0 blockers, 4 concerns, 1 nit). Round 3 independently re-verified every round-1 and round-2 fix and found all of them landed in both the prose and the task graph; the round-2 BLOCKER's `tools/sdlc_stage_query.py` rung is confirmed present in WS-1 and Task 2. The findings below are new.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | WS-1's ledger-less merged-PR rung is directed to land before `query_enriched`'s early return at `tools/sdlc_stage_query.py:836-837`, i.e. gated on bare `session is None`. That branch is not lost-ledger-specific: `_resolve_issue_record` (`:739-785`) returns `None` for any never-seen issue too, and its own docstring at `:754-757` says "A never-seen issue returns `None`… the router polls this path constantly". As directed, the rung fires a live `_lookup_pr` subprocess call (`:369-407`, `subprocess.run(..., timeout=5)`) on every `next-skill` poll for every brand-new issue — including this plan's own fresh-lane negative control. `_lookup_pr` has no memoization in the module (no `lru_cache` / cache hits on grep). | r3: WS-1 direction 3 and Task 2 now forbid gating on bare `session is None`; require positive evidence the lane once existed, or a TTL cache | Gate the rung on positive evidence the lane once existed rather than on the raw `session is None` check at `:836` — e.g. require `_resolve_target_repo_for_read(issue_number)` to succeed AND a resolvable `lane_branch_name(slug)` (the branch-head leg used inside `_lookup_pr` at `:404-407`) — or wrap `_lookup_pr` in a module-level TTL cache keyed on `(issue_number, repo, state)` before wiring it into this hot path. Either way, add a test that a fresh never-seen issue performs zero subprocess calls. |
| CONCERN | Risk & Robustness | Risk 1's only mitigation is the pre-merge negative-control test suite; there is no post-ship operational signal for a false-positive terminal decision. `evaluate_guards` preempts the entire `DISPATCH_RULES` loop (verified `agent/sdlc_router.py:2032-2037`), so a false terminal silently halts dispatch for a lane and is indistinguishable in logs from a lane that legitimately finished — the exact "catastrophic and silent" shape Risk 1 names, addressed only for the pre-ship case. Grep found no `DISABLE_` / `KILL_SWITCH` / `_ENABLED` pattern in `agent/sdlc_router.py`, so live recovery needs a code revert plus a fleet restart, not a config flip. | r3: Risk 1 gains a post-ship signal — log terminal decisions tagged by evidence branch, and gate the guard behind a named env-overridable constant; added to Task 2 | Emit a log line or counter at the `evaluate_guards` terminal return path (`agent/sdlc_router.py:2032-2034`) tagged with which evidence branch fired — `MERGE == completed` vs. the WS-1 merged-PR rung — so the two failure classes are separable during an incident. Gate the guard behind a named, env-overridable constant (matching WS-3's own "no bare literals for tunables" instruction) so a misfire can be disabled without a revert-and-restart cycle. |
| CONCERN | Scope & Value, History & Consistency | Open Question 2 still frames #2851's (a)-vs-(b) as a live decision ("If that workflow is one you actually use, (a) is the answer instead and the plan changes"), but WS-7's Technical Approach commits unconditionally ("**Direction (b): refuse mid-pipeline entry, plus a detector.**"), Task 9 bullet 1 implements only (b), and the WS-7 Success Criteria row hard-gates on the (b) predicate — none carrying language deferring to OQ2. This is the third instance of the open-question-vs-task-graph contradiction: round 1 caught it as a BLOCKER for OQ1, round 2 as a CONCERN for OQ3, and both were reframed. OQ2 was never touched. | r3: OQ2 reframed as a notification matching the OQ1/OQ3 fixes; the recurring pattern added to Rabbit Holes | Apply the OQ1/OQ3 fix pattern: demote OQ2 to a notification stating the plan has committed to (b) and asking only for confirmation or override, OR add an explicit "provisional pending OQ2" cross-reference to Task 9 bullet 1 and the WS-7 Success Criteria row. Do not leave a builder able to ship (b) with no signal that a PM ruling is outstanding. Note the three locations: WS-7 Technical Approach's direction sentence, Task 9 bullet 1, and the `## Success Criteria` mid-pipeline-entry row. |
| CONCERN | History & Consistency | The round-2 table's plan-archival-escape-hatch row carries an `Addressed By` cell reading "r1: added replay criteria against #2734/#2741/#2853/#2636 and a fresh-lane negative control" — byte-identical to the round-1 table's unrelated "no real-lane validation" row, and tagged `r1` although a round-2-table row can only have been answered by the round-2 revision pass. A copy-paste artifact that corrupts the audit trail the column exists to preserve. | r3: cell replaced with the fix that actually landed (OQ3 reframe + unconditional Task 9 bullet and criterion) | Replace that one cell with the fix that actually landed: Open Question 3 was reframed from a live decision into a notification ("Notification, not a decision — the plan-archival escape hatch is in scope") while Task 9's bullet and the `## Success Criteria` row were deliberately left unconditional as mandatory WS-7 scope. Verified the duplication is exact between the two rows and that the OQ3 reframe and the unconditional criterion are both present. |
| NIT | Scope & Value | WS-7's lead sentence still describes the detector as "flagging a lane whose first dispatch snapshot has an empty stages map" and calls that "the highest-value half of the deliverable" — the pre-correction framing the same section explicitly repudiates fifteen lines later ("The refusal cannot key on 'empty stages map' alone — critique round 1 caught this"). Task 9 already carries the corrected predicate, so this is a stale self-contradicting sentence rather than a functional gap, but it is the first thing a builder reads in the subsection. | r3: WS-7 lead sentence rewritten to the positive-evidence predicate | Rewrite the detector sentence to the corrected predicate already used later in the same section and in Task 9: "a dispatch at a non-ISSUE stage with a recorded artifact implying skipped predecessors", reporting which signal was missing. |

---

## Open Questions

1. **PR split — the main decision this plan needs.** Seven workstreams across ~15 files is
   not one reviewable PR. Proposed split, sequenced by the wave's own priority:
   - **PR 1 — router seam (#2894, #2817, #2895, #2850).** WS-1 + WS-2. The two genuine shared
     seams, both in `agent/sdlc_router.py`, both wedging live lanes now. Must ship first and
     together.
   - **PR 2 — critique machinery (#2885, #2832, #2886).** WS-3 + WS-5 + WS-6. **Must merge
     after PR 1** — corrected in critique round 1, which caught that WS-3 changes G2 in
     `agent/sdlc_router.py` and so is *not* disjoint from PR 1 as this question originally
     claimed. WS-5 and WS-6 are genuinely disjoint and could be split out if PR 2 proves too
     large. Order relative to PR 3 is free.
   - **PR 3 — substrate hygiene (#2849, #2851).** WS-4 + WS-7. The two orphans. WS-4 in
     particular is independently shippable and arguably the highest-severity single item in
     the wave (it silently attributes verdicts to the wrong lane).

   Confirm this split, or direct a single PR.

2. **Notification, not a decision — #2851 is built as direction (b).** WS-7, Task 9, and the
   Success Criteria all commit to (b) unconditionally: refuse mid-pipeline entry, because its
   only stated weakness ("does nothing for the two already-wedged lanes") evaporated when
   #2734 and #2741 closed, and because it avoids a new write path into the verdict substrate.
   Flagging it because (b) does remove a capability the pipeline currently has — reviewing
   work planned outside the pipeline. **If you actually use that workflow, say so and the
   plan changes to (a)**; otherwise no action is needed. Reframed in critique round 3, which
   noted this was the *third* instance of the same open-question-vs-task-graph contradiction
   (round 1 caught it as a blocker for OQ1, round 2 as a concern for OQ3). The lesson is
   recorded in Rabbit Holes.

3. **Notification, not a decision — the plan-archival escape hatch is in scope.** Recon found
   that archiving a plan doc makes a lane's CRITIQUE retroactively skippable, an undesigned
   hole through the verdict invariant and arguably more dangerous than #2851's own deadlock.
   It is **mandatory WS-7 scope** (Task 9, and a Success Criterion gates on it). Round 2 of
   critique caught this being framed as an open question while simultaneously being required
   — the same contradiction round 1 fixed for the PR split. Flagging it here only because it
   is not filed as an issue and you may want one opened for the record; say so if you would
   rather it were split out and fast-tracked ahead of this wave, and the plan changes.

4. **How should #2895 be closed?** The structural fail-open reproduces and will get its
   demonstrated-red. The originally observed CLI-path incident does not reproduce and stays
   unexplained. Close #2895 on the structural fix with that caveat recorded, or fix the
   structure and leave #2895 open pending an explanation of the observation?
