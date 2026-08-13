---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2740
last_comment_id: 5277265665
---

# verdict finalize cluster: honest refusals, a clean verdict field, documented count flags, and a router row that owns the stale-verdict state

Three issues, one lane, one PR. Two distinct rationales, stated separately (critique: the original single sentence conflated them):

- **#2740 + #2769 must ship together** — they are two defects at the **same call site** (`tools/sdlc_review_finalize.py::finalize`). Splitting them guarantees a merge conflict in `finalize`.
- **#2767 rides along because its two halves are causally chained to each other** — (a) causes a finalize to be skipped, which produces the stale verdict that (b) leaves unowned. Splitting #2767 would leave that chain half-diagnosed. It does **not** share files with #2740/#2769: `build-count-flags` and `build-router-row8b` are both `Depends On: none` / `Parallel: true`.

Because #2767 is file-disjoint, splitting this into two PRs is a live option that costs no task rework — only the `Closes` trailers and the Team Orchestration roster split. The default is one PR; the reviewer may call for two.

- **#2740** — a refused `verdict finalize` prints `State NOT persisted.` after it has already persisted the verdict, the head-SHA trailer, and `_meta.latest_review_verdict`.
- **#2769** — `finalize` stores the trailer *inside* the verdict field, so the field reads `APPROVED REVIEW CONTEXT HEAD SHA=<HEX>` where `APPROVED` belongs.
- **#2767** — (a) `--blockers` / `--tech-debt` are undocumented ints on a call the review skill calls "mandatory and terminal"; (b) the router falls off its rule table with `Blocked('no matching dispatch rule', guard_id=None)` after a second review→patch cycle.

## Problem

### #2740 — the refusal lies about what landed

A `/do-pr-review` finishes, calls `sdlc-tool verdict finalize`, and gets:

```
[ERROR] STATE_MACHINE_REJECTED: predecessor backfill for REVIEW refused for issue #2711: ... State NOT persisted.
```

**Current behavior:** the verdict, its `REVIEW_CONTEXT head_sha=` trailer, and `_meta.latest_review_verdict` are all durably written *before* that string prints. Only the stage marker is missing. On issue #2711 / PR #2728 (2026-08-13T03:28:55Z) a `stage-query` immediately after the refusal showed a fully-recorded APPROVED verdict alongside an empty `stages` map — exactly the coexistence the message denies.

The string is emitted from `write_marker`, which is locally correct: *it* wrote nothing. `finalize` is a composite that already committed a write one step earlier, and `write_marker` cannot see that.

**Desired outcome:** the refusal reports what actually landed. The reader — usually an agent, not a human — can act on it without a separate `stage-query`. The precedent is one line down at `tools/sdlc_stage_marker.py:733`, where `STATE_MACHINE_RAISED` says *"Persisted state is INDETERMINATE; re-read with `sdlc-tool stage-query`"* and claims nothing it cannot know.

The cost of the current message is measured: in the observed instance a reviewing agent read "State NOT persisted", concluded `finalize` had regressed its atomicity guarantee, and was about to file a duplicate issue against #1642/#2193 before someone re-read the state directly. A wrong error message on a refusal path costs an investigation every time it fires, and risks an agent re-running a write that already landed.

### #2769 — the trailer is absorbed into the verdict

`_verdicts["REVIEW"].verdict` reads `APPROVED REVIEW CONTEXT HEAD SHA=25E53671…` instead of `APPROVED`.

**Current behavior:** `finalize` composes `f"{verdict.strip()} REVIEW_CONTEXT head_sha={head_sha}"` into a single string and passes it as `verdict=` to `record_verdict`, which normalizes the *whole* string (uppercase, `_`→space) before storing. Reproduced on every finalize during the 2026-08-13 fan-out — PRs #2680, #2700, #2706, #2668 — on clean exit-0 runs as well as backfill-refusal runs. It passes validation because `_verdict_is_recognized` is a substring test.

**Desired outcome:** the verdict field holds the bare verdict token. The head SHA lives in its own field. Every existing reader keeps working against ledgers that already contain the mangled string, with no migration.

This is benign *today* only by coincidence: `agent/sdlc_router.py` substring-matches (`REVIEW_APPROVED not in normalize_verdict(...)`), and `_HEAD_SHA_TRAILER_RE` deliberately matches both the raw and normalized trailer images. Any consumer doing verdict equality, enum parsing, or raw display breaks the day it is written.

### #2767(a) — prose into an int flag, on a terminal call

```
argument --blockers: invalid int value: "1) mkdocs build --strict fails on-branch: ..."
```

**Current behavior:** `--blockers` and `--tech-debt` are `type=int` with **no `help=` text at all**. The global `do-pr-review` SKILL.md §5 never says they are counts. §5 also declares the finalize call "mandatory and terminal" and instructs the agent to STOP on a non-zero exit — so an agent taking the natural reading ("record the verdict" ⇒ findings go in) gets a hard stop, and the review ends up complete on GitHub but absent from the ledger. Observed directly: the popoto #537 pipeline skipped the call on its first substantive review, hit `REVIEW_VERDICT_MISSING`, and recovered only by re-calling with integers.

This repo has not hit it because `docs/sdlc/do-pr-review.md:93-98` models integer usage — but that is a repo-local addendum, invisible to any other repo running the same global skill.

**Desired outcome:** the flag names and their `--help` text make the type self-evident, and the global skill states it.

### #2767(b) — the router falls off its own rule table

popoto #537 / PR #548, run `f312ee30a5cd401daa6c06f2ce78aa60`: after review → patch → review → patch → review, the router returned `blocked` / `no matching dispatch rule` / `guard_id: null`.

**Current behavior:** a **stale** REVIEW verdict (one whose `recorded_at` predates the latest `/do-patch` dispatch) combined with `last_dispatched_skill == /do-pr-review` is owned by no row. See spike-1 — reproduced exactly. A second review→patch cycle is ordinary on a non-trivial PR; the loop must not fall off the table. Separately, `Blocked(guard_id=None)` is indistinguishable to a supervisor from a real guard block.

**Desired outcome:** the stale-verdict state routes to a re-review, and a no-rule-matched block is distinguishable from a guard block.

## Freshness Check

**Baseline commit:** `90c0e81e4f0602f33f8a577f246390a5afbeffd5` (main)
**Issues filed at:** #2740 2026-08-13T03:33:56Z · #2767 2026-08-13T07:12:28Z · #2769 2026-08-13T07:31:39Z
**Disposition:** Minor drift

**File:line references re-verified (all at baseline):**
- `tools/sdlc_stage_marker.py:717-719` — the `State NOT persisted.` backfill refusal — **still holds, verbatim**.
- `tools/sdlc_stage_marker.py:566-567`, `:603-604` — two *additional* sites with the identical false sentence (`start_stage` refusal, `STAGE_RAN_NOT_SKIPPABLE`) — **new finding, not in the issue**.
- `tools/sdlc_stage_marker.py:733` — the accurate `STATE_MACHINE_RAISED` precedent — still holds.
- `tools/sdlc_verdict.py:667` — the documented verdict-first ordering contract — still holds; unchanged by this plan.
- `tools/sdlc_review_finalize.py:466-470` — trailer string composition — still holds.
- `tools/sdlc_verdict.py:428-431` — whole-string `normalize_verdict` at the write boundary — still holds; this is where the uppercasing happens.
- `tools/sdlc_verdict.py:938-939` — `--blockers` / `--tech-debt` as bare `type=int` with no help — still holds.
- `agent/sdlc_router.py:956-981` (`_review_verdict_is_stale`), `:1672` (`Blocked(guard_id=None)`) — still hold.

**Cited sibling issues/PRs re-checked:**
- #1941 (branch `session/sdlc-router-rereview-crash-and-row3-pr-guard`, issue #1932) — **MERGED** at `9e2f2b5cd`. The supervisor's overlap warning is resolved: it is not an active competing branch, and rows 8/8b/8c/8d are at their post-#1941 shape. No coordination needed.
- #2193 (PR #2213), #2415, #2548, #2554, #2577 (PR #2614), #2399, #2404 (PR #2416) — all merged; each is a constraint on this plan, not a conflict. See Prior Art.
- #2735 (`find_plan_path` cwd-dependence) — **open**. Per #2740's 06:05 comment it is the dominant *trigger* that drags a run onto the refusal branch. Out of scope: making the trigger rarer is not making the message honest.

**Commits on main since the issues were filed (touching referenced files):**
- `171c1871a` "Reach lease helpers through the module, closing the #2469 freeze class (#2637) (#2706)" — **irrelevant to all three root causes.** It changes how lease helpers are imported; every defective line above was read at baseline *after* that commit and is present verbatim.

**Bug reproduction against current main:** #2769 and #2767(b) reproduced directly (see Spike Results). #2740's message is confirmed by reading the emitting line and the call ordering — reproducing it live would require driving a real backfill refusal against a live ledger, which BUILD will do as a unit test instead.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/sdlc-lane-recorded-slug.md` (status Ready) and `docs/plans/plan-doc-single-writer-lease.md` (status Ready) both mention `sdlc_router` / `write_marker` in passing but target lane-slug recording and plan-doc leasing respectively — no file-level collision with `sdlc_review_finalize.py`, `sdlc_verdict.py`'s CLI args, or rows 8/8b. `docs/plans/gates-that-cannot-fire.md` (status Planning) is the nearest neighbour conceptually; it audits guards that cannot fire, whereas this plan fixes a *row* that stops firing. Coordinate only if that plan starts editing `DISPATCH_RULES`.

**Notes:** the two extra `State NOT persisted.` sites at `:566` and `:603` are drift *in our favour* — the issue asked for a sibling audit and this found two more instances of the exact same false sentence, both in scope.

## Prior Art

- **PR #2213 (#2193)** — created `tools/sdlc_review_finalize.py` to collapse the hand-run 3-call sequence into one self-verifying call. Succeeded at its goal (the router is no longer left blind by a skipped write). **It is the origin of both #2740 and #2769**: the composite structure is what makes `write_marker`'s message wrong, and the single-string trailer append is what mangles the verdict field. Neither is a regression of #2193 — both are gaps it never addressed.
- **PR #2415 / #2554** — established that the verdict invariant beats the #2399 predecessor backfill, which is *why* the backfill raises and the refusal branch exists at all. This plan must not weaken that precedence.
- **PR #2614 (#2577)** — added the `_review_artifact_readable` conjunct so read sites stay safe in exactly the partial state #2740 describes. This is the evidence that verdict-first ordering is deliberate: the system was *already hardened* for the partial state. Only the message was left wrong.
- **#2548** — closed the verdict vocabulary after `"APPROVE WITH COMMENTS"` took the non-APPROVED exemption and stalled the router. It introduced `_verdict_is_recognized` as a **substring** test, which is the direct enabler of #2769: the composite verdict+trailer string passes validation because it *contains* `APPROVED`. Tightening that test to an equality check would re-break #2548's decorated-verdict case (`"APPROVED (0 BLOCKERS)"`), so the fix must be at the write site, not the validator.
- **PR #2416 (#2404)** — made head-SHA resolution git-first so record and check read the same source. Constrains #2769: whatever field the SHA moves into must still be written from `_fetch_pr_head_sha`'s result.
- **PR #1941 (#1932)** — added rows 8d/8e/8f and the row-3/G1/G5 open-PR guards, closing three "nobody owns this state" holes. #2767(b) is a **fourth hole of the same family** that #1941 did not reach. Merged; no branch conflict.
- **#1641 / #1668** — the two oscillation classes (`stale verdict re-dispatch loop`, `in_progress with no verdict dead-end`) that rows 8b and 2c/8c were built to close. Any widening of row 8b must not re-open them.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|---|---|---|
| PR #2213 (#2193) | Collapsed verdict+trailer+marker into one `finalize` call, self-verifying, fail-closed | Made the *sequence* atomic from the caller's point of view but left the sub-calls independently messaged. `write_marker` still speaks as if it were the whole operation (#2740). The trailer was appended by string concatenation because `record_verdict` had no field for it, and nobody revisited that once the readers were made regex-tolerant (#2769). |
| PR #1941 (#1932) | Added rows 8d/8e/8f for three unowned post-review states | Enumerated the states reachable by a *crash*. The state in #2767(b) is reachable by a **successful review whose verdict write failed for an unrelated reason** — the verdict is present but stale, which none of 7/8/8b/8c/8d/8e tests for. Each fix closed the hole it was shown, not the class. |
| PR #2614 (#2577) | Hardened read sites against the verdict-recorded/marker-missing partial state | Fixed the *consequence* (readers no longer break) and left the *report* (the error message) untouched, so the partial state is now safe but still described as impossible. |

**Root cause pattern:** every one of these fixed a state-machine behavior and left the *reporting* about that behavior stale. The composite call site grew three layers (`finalize` → `record_verdict` → `write_marker`) without ever giving the outer layer responsibility for narrating the result. This plan's through-line is: **the layer that knows what happened is the layer that must say what happened.**

## Research

No relevant external findings — proceeding with codebase context. The work is entirely internal: argparse flag naming, an internal ledger record shape, an error string, and an internal dispatch predicate. No external libraries, APIs, or ecosystem patterns are involved.

## Spike Results

### spike-1: Reproduce #2767(b)'s unowned router state

- **Assumption**: "The reported terminal state (`PATCH: completed`, `REVIEW: pending`, verdict `CHANGES REQUESTED`, `patch_cycle_count: 1`) should have matched row 8, so something else is going on."
- **Method**: prototype — drove `decide_next_dispatch` directly against reconstructed `stage_states` / `_meta` payloads.
- **Finding**: **ROOT CAUSE IDENTIFIED, exactly reproduced.** The unowned state is a **stale verdict with `last_dispatched_skill == /do-pr-review`**:

  | scenario | `last_dispatched_skill` | result |
  |---|---|---|
  | verdict `CHANGES REQUESTED`, fresh (postdates last patch) | either | `Dispatch(/do-patch, row 8)` ✅ |
  | no verdict at all | either | `Dispatch(/do-pr-review, row 7)` ✅ |
  | verdict `CHANGES REQUESTED`, **stale** | `/do-patch` | `Dispatch(/do-pr-review, row 8b)` ✅ |
  | verdict `CHANGES REQUESTED`, **stale** | `/do-pr-review` | **`Blocked('no matching dispatch rule', guard_id=None)`** ❌ |

  Row-by-row: row 7 misses (a verdict *is* present); row 8 steps aside via `_review_verdict_is_stale` (`agent/sdlc_router.py:1170-1172`); row 8b requires `last_dispatched_skill == /do-patch`; row 8c requires `REVIEW == in_progress`; row 8d requires *no* recorded verdict; row 8e requires `REVIEW == completed`. Nobody owns it.
- **Confidence**: high — bit-exact reproduction of the reported `Blocked` value including `guard_id: null`.
- **Impact on plan**: turns #2767(b) from "investigate" into a scoped predicate fix, and removes the risk of a speculative widening re-opening #1641/#1668.

### spike-2: Validate the candidate row-8b widening

- **Assumption**: "Extending row 8b to also own `PATCH == completed AND _review_verdict_is_stale(...)` fixes the hole without disturbing the states rows 8 and 9 already own."
- **Method**: prototype — monkeypatched row 8b's predicate and re-ran the decision table.
- **Finding**: works and is non-disturbing.

  | state | with patched row 8b |
  |---|---|
  | stale verdict, `last=/do-pr-review` | `Dispatch(/do-pr-review, row 8b)` — the correct recovery |
  | fresh `CHANGES REQUESTED` | `Dispatch(/do-patch, row 8)` — unchanged (row 8 precedes 8b) |
  | fresh `APPROVED` + `REVIEW: completed` | `Dispatch(/do-docs, row 9)` — unchanged |
- **Confidence**: medium-high — the three headline states are right; BUILD must still prove disjointness against rows 8c/8d/8e and confirm G4 still bounds the resulting re-review loop.
- **Impact on plan**: gives BUILD a concrete, pre-validated fix shape instead of an open design question. The row-8b docstring must be updated: it currently says 8b requires `last_dispatch == /do-patch`, which stops being the whole truth.

### spike-3: Confirm #2769's mangling mechanism end-to-end

- **Assumption**: "The uppercasing comes from `normalize_verdict` being applied to the already-concatenated string, not from a separate uppercase step."
- **Method**: code-read across the write path.
- **Finding**: confirmed. `sdlc_review_finalize.py:466-470` concatenates → `record_verdict` receives one string → `sdlc_verdict.py:428-431` applies `normalize_verdict` to all of it → `APPROVED REVIEW CONTEXT HEAD SHA=<HEX>` is stored. `record_verdict`'s record dict is `{verdict, recorded_at, artifact_hash[, blockers, tech_debt, _judges, _consensus]}` — there is **no** existing field for the SHA, so this is a genuine schema addition (with precedent: `_judges` / `_consensus` were added the same way in the multi-judge work).
- **Confidence**: high.
- **Impact on plan**: the fix is a new optional `head_sha` kwarg on `record_verdict` plus a bare-token verdict, not a change to `_verdict_is_recognized` (which #2548 needs to stay a substring test).

### spike-4: Audit the sibling refusal paths (#2740's third acceptance criterion)

- **Assumption**: "The five named gates listed in #2740 carry the same overclaim."
- **Method**: code-read of every refusal in `write_marker`.
- **Finding**: **they do not.** `REVIEW_VERDICT_MISSING` (`:625-631`), `REVIEW_TRAILER_MISSING` (`:640-647`), `REVIEW_ARTIFACT_MISSING` (`:658-665`), `CRITIQUE_VERDICT_MISSING` (`:668-681`) and `ISSUE_LOCKED` (`:686-690`) all end with `Marker write refused.` / `marker write refused.` — scoped to the marker, and accurate. The overclaim is the sentence **`State NOT persisted.`**.
- **CORRECTED BY CRITIQUE — the sentence appears at FOUR sites, not three.** This spike audited only refusals *inside* `write_marker` and therefore structurally could not see the fourth. The full set, verified by `grep -rn "State NOT persisted" tools/ agent/` returning 4 matches on main:

  | site | context | inside `write_marker`? |
  |---|---|---|
  | `:566-567` | `start_stage` refusal | yes |
  | `:603-604` | `STAGE_RAN_NOT_SKIPPABLE` | yes |
  | `:717-719` | the backfill refusal #2740 reports | yes |
  | **`:969-974`** | **`main()`'s generic non-zero-exit stderr wrapper** | **no — this is why the spike missed it** |

- **Confidence**: high (post-correction).
- **Impact on plan**: BUILD fixes **four** sites, not three and not five, and records the finding rather than re-running the audit. Note `:566` and `:603` are reachable from bare `stage-marker` calls too, where the sentence is often true but still unknowable from inside `write_marker`. On `:972`: every error key `write_marker` currently returns is in `_DIAGNOSED_ERRORS` (`tools/sdlc_stage_marker.py:152-168`), so that branch is dead code *today* — but it holds the literal grep target the Verification table asserts against, and it is the site a bare `sdlc-tool stage-marker` caller would see the moment an undiagnosed error key is added. Reword it; do not delete the branch.

## Data Flow

The write path all three defects sit on:

1. **Entry point**: `/do-pr-review` §5 runs `sdlc-tool verdict finalize --pr N --issue-number M --verdict "..." --blockers <int> --tech-debt <int> --run-id ...`.
   - *#2767(a) fails here*, before anything else runs, if the agent passes prose.
2. **`tools/sdlc_verdict.py::main` → `_cli_finalize`** — argparse coerces the counts; `type=int` raises `SystemExit(2)` on prose.
3. **`tools/sdlc_review_finalize.py::finalize`** — validates the verdict against `RECOGNIZED_REVIEW_VERDICTS`, resolves + revalidates the ledger lease, and on the APPROVED path resolves the head SHA via `_fetch_pr_head_sha`.
   - *#2769 originates here*: the SHA is concatenated onto the verdict string (`:466-470`).
4. **`tools/sdlc_verdict.py::record_verdict`** — normalizes the whole incoming string and writes `_verdicts["REVIEW"]` via `update_stage_states`. **This write lands and is durable.**
   - *#2769 is materialized here* by the whole-string `normalize_verdict`.
5. **`tools/sdlc_stage_marker.py::write_marker(REVIEW, completed)`** — runs the named gates, then `sm._backfill_predecessors(stage)`.
   - *#2740 originates here*: on a `ValueError` from the backfill it prints `State NOT persisted.` — unaware that step 4 already committed.
6. **`finalize` receives `marker_exit != 0`** and raises `ReviewFinalizeError(f"{reason}: ... {marker_result}")` (`:506-511`). **This is the layer that knows both facts** and is where the honest message belongs.
7. **Output**: non-zero exit; `/do-pr-review` STOPs per §5.
8. **Later, `agent/sdlc_router.py::decide_next_dispatch`** reads the ledger to pick the next skill.
   - *#2767(b) manifests here*: when step 4 never ran on the newest review (e.g. because step 2 hard-failed on prose), the ledger still holds the *previous* review's verdict, which is now stale relative to the latest `/do-patch` dispatch, and no row owns it.

**Chain, stated plainly:** #2767(a) causes a finalize to be skipped → the ledger keeps an older verdict → that verdict is stale → #2767(b)'s unowned state. The two halves of #2767 are cause and effect, which is why they belong in one lane.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**:
  - `record_verdict` gains an optional `head_sha: str | None = None` kwarg (additive; all existing callers unaffected).
  - The `_verdicts[stage]` record gains an optional `head_sha` key (additive; readers must tolerate both presence and absence).
  - `sdlc-tool verdict finalize` gains `--blocker-count` / `--tech-debt-count` flag names.
  - Row 8b's predicate widens.
- **Coupling**: **decreases.** Moving the honest message up to `finalize` removes the implicit assumption that `write_marker`'s stderr is the whole story, and giving the SHA its own field removes the implicit contract that every reader must regex a composite string.
- **Data ownership**: unchanged — `record_verdict` remains the sole writer of `_verdicts`.
- **Reversibility**: high. Every change is additive or a string. The `head_sha` field is written alongside (not instead of) a still-trailered-on-read compatible value during the transition; see Risk 1 for the exact compatibility posture.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the flag-rename-vs-accept-prose decision and the legacy-read posture)
- Review rounds: 1

Four defects across **five** code files: `tools/sdlc_review_finalize.py`, `tools/sdlc_stage_marker.py`, `tools/sdlc_verdict.py`, `tools/sdlc_stage_query.py`, and `agent/sdlc_router.py`. The fifth (`sdlc_stage_query.py`) was added by the round-2 blocker fix and is the one that carries the router-wide-outage failure mode: get `latest_review_head_sha` wrong and every lane re-reviews until G4 escalates.

Medium still holds, because four of the five changes are a string rewrite, an argparse rename, an additive kwarg, and a one-clause predicate widening. The weight is concentrated in four proofs, not in volume: that the router predicate change does not re-open #1641/#1668, that the `head_sha` field change is migration-free against live ledgers, that no head-SHA reader is left on the flattened-string path, and that the three-way `_verdicts["REVIEW"]` guard never lets `None` or a bare `str` reach a call typed for the other shape.

## Prerequisites

| Requirement | Check Command | Purpose |
|---|---|---|
| `gh` authenticated | `gh auth status` | Head-SHA resolution and issue reads in tests |
| Repo venv on the pinned interpreter | `.venv/bin/python -c "import sys,pathlib; assert sys.version.split()[0].startswith(pathlib.Path('.python-version').read_text().strip())"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

## Solution

### Key Elements

- **Honest refusal reporting** — the layer that knows what happened says what happened. `finalize` catches the marker refusal and re-emits a message stating that the verdict + trailer *did* land and only the marker did not, with the actionable remedy. `write_marker` stops asserting a fact it cannot know at all three `State NOT persisted.` sites.
- **A clean verdict field** — the bare verdict token in `verdict`, the head SHA in its own `head_sha` key on the same record. The read path still parses a legacy composite string, so no migration and no flag day.
- **Self-describing count flags** — `--blocker-count` / `--tech-debt-count` with real `--help` text, the old names kept as aliases so no in-flight caller breaks, and the global `do-pr-review` skill stating the type.
- **A row that owns the stale-verdict state** — row 8b widens to cover "PATCH completed and the recorded REVIEW verdict is stale", regardless of which skill was last dispatched.
- **A distinguishable no-rule block** — `Blocked` from the no-rule fallthrough carries a marker a supervisor can tell apart from a guard block.

### Flow

`/do-pr-review` finishes → calls `verdict finalize --blocker-count N --tech-debt-count M` → verdict + head_sha recorded as separate fields → marker write attempted → **either** marker lands (exit 0, router advances to `/do-docs`) **or** the backfill refuses → `finalize` reports *"verdict and trailer PERSISTED; REVIEW marker NOT written; run /do-plan-critique then re-run this identical command (idempotent)"* → operator/agent acts on an accurate statement → re-run lands the marker.

Separately: a review whose finalize never ran leaves a stale verdict → router row 8b now owns it → `/do-pr-review` re-dispatched → loop closes instead of blocking.

### Technical Approach

**1. #2740 — honest refusals (`tools/sdlc_review_finalize.py`, `tools/sdlc_stage_marker.py`)**

The issue's own solution sketch poses this as a choice; spike-4 and the call-graph resolve it to **both, at their respective layers**:

- In `finalize` (`:500-511`): the marker-refusal branch is the only place that knows a verdict write already succeeded. Replace the pass-through `raise ReviewFinalizeError(f"{reason}: ... {marker_result}")` with a message that explicitly names the split — verdict and trailer persisted, marker not — and states the remedy in terms the caller can act on (re-running the identical `finalize` is idempotent: the verdict write repeats harmlessly). Keep the named reason prefix so existing `reason`-matching tests and the `/do-sdlc` supervisor keep working.
- In `write_marker`: at all three in-function `State NOT persisted.` sites (`:566-567`, `:603-604`, `:717-719`), drop the claim. Follow the `STATE_MACHINE_RAISED` precedent at `:733` — say the marker write was refused and point at `sdlc-tool stage-query`. `write_marker` must never assert anything about writes it did not perform.
- In `main()` at `:969-974` — the **fourth** site, outside `write_marker`, added after the critique corrected spike-4. Same rewording, same precedent. It sits after `maybe_heal_after_write` may have retried the write, so it is an overclaim for the same reason as the others. Do not delete the branch; it is currently unreachable (all returned error keys are in `_DIAGNOSED_ERRORS`) but it holds the literal string the Verification anti-criterion greps for.

Do **not** make `finalize` transactional. The verdict-first ordering is deliberate (`tools/sdlc_verdict.py:667`, #2577/#2415) and explicitly out of scope.

**2. #2769 — separate the trailer from the verdict (`tools/sdlc_verdict.py`, `tools/sdlc_review_finalize.py`)**

- `record_verdict` gains `head_sha: str | None = None`; when provided it is stored as a `head_sha` key on the record, un-normalized apart from case folding, alongside the existing keys. It follows the `_judges` / `_consensus` precedent: only attached when the caller passes it, so the persisted shape for every other caller is bit-identical.
- `finalize` stops concatenating. It passes `verdict=verdict.strip()` (bare token) and `head_sha=head_sha` separately.
- **The read path is where the compatibility lives.** Introduce the helper as **two entry points over one body**, because the readers do not all hold the same type:
  - `head_sha_of_record(record: dict)` — prefers the `head_sha` field, falls back to `_HEAD_SHA_TRAILER_RE` over `record.get("verdict", "")`.
  - `head_sha_of_text(text: str)` — regex only, for callers that only ever hold a string.

  Route `_review_trailer_present` (`tools/sdlc_verdict.py:554`), `check_review_persistence`'s trailer check (`sdlc_review_finalize.py:287-292`), `tools/merge_predicate.py`, and the router's freshness gate through them. Live ledgers already hold mangled strings; nothing may require them to be rewritten.

- **CRITIQUE BLOCKER — `tools/sdlc_stage_query.py` is in scope and the original plan named it nowhere.** Row 8f's head-staleness gate cannot see a `head_sha` *field* as the plan was originally written, and the failure mode is fail-closed at scale:

  `agent/sdlc_router.py:944` calls `_latest_review_verdict`, which at `:266-267` prefers `meta["latest_review_verdict"]` — a plain **string**, produced by `tools/sdlc_stage_query.py:475` via `_extract_verdict_text` (`:443-451`), which returns only `record["verdict"]` and **drops every sibling key**. After the split that string carries no trailer, so `agent/sdlc_router.py:950-951` (`if not trailer: return True`) would declare **every** approved verdict head-stale and re-dispatch `/do-pr-review` on every lane until G4 escalates. This is a router-wide re-review loop, not a quiet degradation — a single-string reader is exactly the class Risk 1 predicted.

  The fix: add a `latest_review_head_sha` key to `_compute_meta` (`tools/sdlc_stage_query.py:470-545`), sourced from the record via `head_sha_of_record`, and make row 8f prefer `meta.get("latest_review_head_sha")` before falling back to `head_sha_of_text(...)`. `tools/sdlc_stage_query.py` joins the file fence for this lane.

- **`tools/merge_predicate.py` must keep its lazy import.** It imports `_HEAD_SHA_TRAILER_RE` *inside* the function (`:580`) deliberately, to keep module-level imports stdlib-only for the merge-guard hook. The new helper MUST preserve that lazy-import posture or the hook breaks under a bare interpreter. Separately, `:582-595` treats "no trailer" as legitimate and silently falls back to a weaker `recorded_at`-vs-commit-date comparison — after the split, a new-shape record would take that branch and the head-SHA check #2404/#2415 exist to enforce would stop running, **with no test failing**. Both branches can return "fresh", so a pass/fail-only test cannot detect the downgrade; the assertion must be on the exact `notes` string at `:588`.
- Leave `_verdict_is_recognized` a substring test. #2548 depends on it accepting decorated verdicts like `"APPROVED (0 BLOCKERS)"`; tightening it to equality would re-open that incident. The mangling is fixed at the write site, which is where it was introduced.
- Idempotency must be preserved: `finalize` currently no-ops the append when the incoming verdict already carries a trailer. The new code must handle a caller passing an already-trailered verdict (strip the trailer out into `head_sha` rather than storing it twice).

**3. #2767(a) — self-describing count flags (`tools/sdlc_verdict.py`, `.claude/skills-global/do-pr-review/SKILL.md`)**

Take the **rename** option, not the accept-prose option. Deriving a count from prose is a guess, and a wrong count silently corrupts the ledger — strictly worse than the loud failure we have. Naming the flag for what it holds is the honest fix:

- Rename to `--blocker-count` / `--tech-debt-count`, each with `help=` text saying "integer count of ... — NOT the findings text; findings go in the posted review".
- Keep `--blockers` / `--tech-debt` as argparse aliases so no in-flight caller or worktree breaks. This is not a parallel migration: there is one flag with two spellings, and the old spelling is documented nowhere after this lands.
- State the type in the **global** skill (`.claude/skills-global/do-pr-review/SKILL.md` §5) — that is what a foreign repo reads. Update the repo addendum `docs/sdlc/do-pr-review.md:93-99` to the new names for consistency.
- While in the addendum: `docs/sdlc/do-pr-review.md:103` asserts finalize "is **atomic and self-verifying**", which #2740 shows is false on the backfill-refusal branch. Correct it to state what actually holds.

**4. #2767(b) — row 8b owns the stale-verdict state (`agent/sdlc_router.py`)**

Per spike-2: `_rule_patch_applied_after_review` matches when `pr_number` is set, `PATCH == completed`, and **either** `last_dispatched_skill == /do-patch` **or** `_review_verdict_is_stale(stage_states)`. Row 8 already steps aside on staleness, so the two remain disjoint and row 8 keeps precedence for fresh findings. BUILD must:

**Disjointness against rows 8c/8d/8e is proved here, not deferred to BUILD** (critique upgraded spike-2's "medium-high"). Note first a call edge the original plan missed: **row 8c does not merely sit beside row 8b, it CALLS it** — `_rule_review_in_progress_no_verdict` invokes `_rule_patch_applied_after_review` as a step-aside at `agent/sdlc_router.py:1225`, so widening 8b directly changes 8c's behavior. The widening is nonetheless safe by construction:

> `_review_verdict_is_stale` (`agent/sdlc_router.py:969-973`) returns `False` whenever `recorded_at` is absent, and `recorded_at` is absent exactly when no verdict was recorded. Rows 8c/8d/8e all require the **absence** of a recorded verdict. Therefore the new `or _review_verdict_is_stale(...)` disjunct is identically `False` on every state those three rows own.

BUILD records this proof and adds **no** defensive step-asides. The G4 oscillation bound remains a genuine test obligation:
- Confirm G4 (`same_stage_dispatch_count`) still bounds the resulting re-review loop, so a permanently-stale verdict escalates to a human rather than spinning. This is the #1641/#1668 re-opening risk and must be tested, not asserted.
- Update the row-8b docstring, which currently states the `last == /do-patch` requirement as definitional.

Separately, at `agent/sdlc_router.py:1666-1675`: give the no-rule fallthrough a distinguishable signal so a supervisor can tell it from a guard block. **Default chosen (Q4): the sentinel `guard_id="NO_RULE"`.** Every existing `guard_id` is a short code (`G2`, `G4`, `G7`), so a short sentinel is the convention-consistent choice and is machine-matchable without string parsing. It does slightly widen the `Blocked` contract — `guard_id` stops implying "a numbered guard fired" — so state that in the docstring. Do not also add a reason-string mechanism; one signal only.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_review_finalize.py` — the `except Exception` in `check_review_persistence` (`:333-343`) is a deliberate fail-closed catch. Assert it still yields `ok: False` with a preserved `reason` when the new `head_sha` read helper raises.
- [ ] `agent/sdlc_router.py` — `_review_verdict_is_stale` (`:967-981`) swallows everything and fails safe to `False` (not stale). With row 8b now depending on it, add a test that a malformed `recorded_at` yields "not stale" and therefore leaves row 8 in control, rather than silently routing to a re-review loop.
- [ ] `agent/sdlc_router.py` — `decide_next_dispatch` catches predicate exceptions and continues (`:1650-1656`). Assert the widened row 8b predicate never raises on a malformed `_verdicts` payload (non-dict, missing keys, `None`).
- [ ] `tools/sdlc_stage_marker.py` — the outer `except Exception` (`:727-735`) already reports `STATE_MACHINE_RAISED` accurately; assert its message is unchanged by this work (it is the model the other four sites are being made to follow).

### Empty/Invalid Input Handling
- [ ] `record_verdict(head_sha=...)` with `None`, `""`, whitespace, and a non-40-hex string — assert no `head_sha` key is written for falsy input and that a malformed value never produces a record that reads as a valid trailer.
- [ ] `finalize` with a verdict that *already* carries a trailer — assert the SHA lands in `head_sha` exactly once and is not also left inside the verdict string.
- [ ] The head-SHA read helper against: a record with only `head_sha`, only a legacy mangled `verdict`, both (agreeing), both (disagreeing — define and test the precedence), and neither.
- [ ] `--blocker-count` with a negative value and with `0` — assert `0` is preserved as a real count and not conflated with "not provided" (the current `default=None` distinction must survive the rename).

### Error State Rendering
- [ ] The #2740 message itself is a user-visible error path and is the deliverable. Assert the exact refusal text against a ledger holding a recorded verdict and no marker: it must NOT contain "State NOT persisted", must state that the verdict persisted, and must name the remedy.
- [ ] Assert all four sites no longer emit `State NOT persisted.` — the three inside `write_marker` plus `main()`'s wrapper at `:969-974` — and that a repo-wide grep for that sentence returns zero matches.
- [ ] Assert a no-rule router block renders distinguishably from a guard block (both `reason` and the guard field).

## Test Impact

- [ ] `tests/unit/test_sdlc_stage_marker.py:237-239` — UPDATE: keeps asserting `reason == "STATE_MACHINE_REJECTED"` and the taxon in stderr (both still hold); ADD an assertion that the message does not claim non-persistence.
- [ ] `tests/integration/test_off_pipeline_merge_path.py:323, :368, :446, :576, :636, :663, :689` — NO CHANGE EXPECTED: all seven assert `reason` only, so a message rewording does not break them. Verify, do not pre-emptively edit.
- [ ] `tests/unit/test_sdlc_dispatch.py:434` — NO CHANGE EXPECTED: substring-matches the taxon only. Verify.
- [ ] `tests/unit/test_sdlc_router.py` — UPDATE: add coverage for the widened row 8b (the four-scenario table from spike-1, all four asserted) plus disjointness against rows 8c/8d/8e and the G4 bound.
- [ ] `tests/unit/test_sdlc_router_oscillation.py` — UPDATE: assert the widened row 8b does not re-open #1641's stale-verdict oscillation — a permanently-stale verdict must escalate via G4, not spin.
- [ ] `tests/unit/test_sdlc_router_decision.py` — UPDATE: the no-rule `Blocked` shape changes; adjust any assertion on `guard_id is None` for the fallthrough case.
- [ ] Verdict-record tests covering `record_verdict`'s persisted shape — UPDATE: assert `head_sha` is present when passed and absent when not, and that the `verdict` field holds the bare token. (BUILD locates these by grepping `_verdicts` / `record_verdict` in `tests/unit/`.)
- [ ] Any test asserting the trailered composite string in the `verdict` field — REPLACE: assert against the new split shape, keeping one legacy-string case to prove the read path still parses it.
- [ ] `tests/unit/test_sdlc_skill_md_parity.py` — CHECK: it guards skill-doc/code parity and may assert on flag names in `docs/sdlc/do-pr-review.md`.
- [ ] `tests/unit/test_merge_predicate.py` — ADD (critique concern): assert the **exact `notes` string** at `tools/merge_predicate.py:588` for a new-shape record `{"verdict": "APPROVED", "head_sha": "<40hex>", "recorded_at": …}`. A pass/fail-only assertion cannot detect this regression: `:582-595` treats a trailer-less verdict as legitimate and silently downgrades to the weaker `recorded_at`-vs-commit-date comparison, and **both branches can return "fresh"**. This is a fail-open on the merge gate that #2404/#2415 exist to keep honest.
- [ ] Tests covering `tools/sdlc_stage_query.py::_compute_meta` — ADD (critique blocker): assert `latest_review_head_sha` is populated across **four** `_verdicts["REVIEW"]` shapes — a new-shape dict with `head_sha`, a legacy mangled dict whose `verdict` string carries the trailer, and a **bare `str`** record. and a `_verdicts` payload with **no `"REVIEW"` key at all**. The bare-str case raises `AttributeError` if `head_sha_of_record` is called unguarded; the missing-key case raises `TypeError` if `None` reaches the regex, and that one is the COMMON path — every issue still in PLAN/BUILD/TEST. Both exceptions are swallowed by `_resolve_enriched` into an empty ledger. Assert all four at both the `_compute_meta` and `decide_next_dispatch` levels, since the regression is router-wide.

No xfail/xpass markers exist for any of these defects (grep of `tests/` for `xfail` intersected with verdict/finalize/router/marker returned nothing), so there are no expected-failure conversions to perform.

## Rabbit Holes

- **Making `finalize` transactional.** Explicitly forbidden by #2740. The verdict-first ordering is a deliberate design that #2577 hardened the read sites around. Undoing it is a multi-day rewrite that trades a wrong error message for a real correctness risk.
- **Fixing #2735 to make the refusal branch rarer.** Tempting because it removes most occurrences. It does not make the message honest, and it is somebody else's issue.
- **Tightening `_verdict_is_recognized` to an equality check.** Looks like the "real" fix for #2769. It re-opens #2548 (`"APPROVED (0 BLOCKERS)"` would stop being recognized) and does nothing about the already-stored mangled strings.
- **Migrating existing mangled verdict strings.** #2769's own fix shape says migration-free. A backfill over live ledgers is a destructive operation with no upside once the read path tolerates both forms.
- **Rewriting the dispatch rule table.** #2767(b) is one over-narrow predicate. Four prior fixes (#1641, #1668, #1932, and this one) each closed one hole; a wholesale redesign is a separate project with its own plan.
- **Teaching `finalize` to parse prose findings into counts.** A derived count that is silently wrong is worse than the loud failure today.
- **Auditing every `sdlc-tool` error string for honesty.** spike-4 (as corrected in round 1 of the critique) bounded the actual overclaim to one sentence at four sites. Resist expanding to a repo-wide error-message audit.

## Risks

### Risk 1: The split verdict/head_sha shape breaks a reader we did not find
**Impact:** a merge gate or freshness check silently fails open or closed on a live pipeline — the highest-severity outcome in this lane, since #2404/#2415 exist precisely to keep that gate honest.
**Mitigation:** enumerate readers by grepping `_HEAD_SHA_TRAILER_RE`, `head_sha`, and `_verdicts` across `tools/`, `agent/`, and `ui/` before changing the writer; route every hit through the single read helper; keep the regex fallback permanently (it is not scaffolding — legacy records are permanent). Add a test that a record in the *old* shape still satisfies every gate.

### Risk 2: The widened row 8b re-opens an oscillation class
**Impact:** the router ping-pongs `/do-pr-review` on a PR whose verdict stays stale, burning a lane.
**Mitigation:** spike-2 already shows rows 8 and 9 are undisturbed. BUILD must additionally prove G4 bounds the new path (a permanently-stale verdict escalates to a human) and add the #1641/#1668 regression cases to `test_sdlc_router_oscillation.py`.

### Risk 3: The flag alias becomes permanent cruft
**Impact:** the repo carries two spellings forever, violating the no-legacy-code principle.
**Mitigation:** the alias is argparse-level (one `add_argument` call listing both spellings), not a second code path, and every document is updated to the new spelling in this same PR.

**The alias's actual purpose, which the original plan never stated** (critique nit): it covers the **cross-machine propagation window**. `.claude/skills-global/do-pr-review/SKILL.md` is hardlinked to `~/.claude/skills/` by `/update`, so a machine that has not yet run `/update` is still emitting `--blockers` against a freshly-merged `sdlc-tool`. The alias exists for exactly that window, not for in-repo callers — "the only callers are skills we are editing anyway" was an argument for *dropping* it, not keeping it. Because the window closes, the alias gets a removal trigger: file a follow-up issue "drop `--blockers`/`--tech-debt` aliases" at merge time, actionable once every machine has run `/update`. Removal is a one-line `add_argument` diff.

### Risk 4: The new refusal message is verbose enough that agents skim it
**Impact:** we replace a wrong message with an ignored one.
**Mitigation:** keep the named-reason prefix first (it is what tooling matches on), state the split in one sentence, and give exactly one remedy. Do not enumerate every field that landed.

### Risk 5: Concurrent lanes touch the same files
**Impact:** merge conflicts with `sdlc-lane-recorded-slug` or `gates-that-cannot-fire`.
**Mitigation:** neither currently edits `sdlc_review_finalize.py`, `record_verdict`'s signature, or `DISPATCH_RULES`. Re-check at BUILD start; if `gates-that-cannot-fire` has begun editing rows, coordinate before touching row 8b.

**Updated after the round-2 fence change — there is now a concrete, plausible collision.** `docs/plans/sdlc-lane-recorded-slug.md` (status Ready) plans a repair of the dead slug read at `tools/sdlc_stage_query.py::_compute_meta:487`, which falls **inside** the `:470-545` span this plan edits to add `latest_review_head_sha`. Two lanes editing the same function body is a literal git merge conflict, not a logical one. BUILD must re-check at start: if `sdlc-lane-recorded-slug` has begun editing `_compute_meta`, coordinate before landing `latest_review_head_sha`. Named files for this risk are therefore `sdlc_review_finalize.py`, `DISPATCH_RULES`/row 8b, and `tools/sdlc_stage_query.py::_compute_meta`.

## Race Conditions

### Race 1: Lease taken between the verdict write and the marker write
**Location:** `tools/sdlc_review_finalize.py:474-511`
**Trigger:** a foreign run acquires the issue lease after `finalize` records the verdict but before `write_marker` completes.
**Data prerequisite:** the verdict record must be durable before the marker is attempted (this is the deliberate #2577 ordering).
**State prerequisite:** the lease must be owned by `run_id` at the moment of each write.
**Mitigation:** unchanged by this plan — `revalidate_ledger_lease` runs immediately before the verdict write (`:476`) and `write_marker` re-validates independently (`:684`). This plan only changes what is *reported* when that second validation refuses. The new message must be accurate in this case too: the verdict landed under our lease even though the marker did not.

### Race 2: Head SHA moves between resolution and record
**Location:** `tools/sdlc_review_finalize.py:459-470`
**Trigger:** a push to the PR branch between `_fetch_pr_head_sha` and `record_verdict`.
**Data prerequisite:** the recorded `head_sha` must correspond to the commit actually reviewed.
**State prerequisite:** none beyond the above.
**Mitigation:** pre-existing and unchanged — the staleness gate (#2062 WS3d, row 8f) catches a recorded SHA that no longer matches the head and re-dispatches a review. Moving the SHA into its own field must not change which value is recorded or when; BUILD keeps the resolution call site exactly where it is.

### Race 3: Two lanes finalize the same issue concurrently
**Location:** `tools/sdlc_verdict.py::record_verdict` → `update_stage_states`
**Trigger:** two runs both hold a claim to the same issue (the #1972 self-lock class).
**Data prerequisite:** `_verdicts["REVIEW"]` must reflect the run that actually posted the review.
**State prerequisite:** single-owner lease.
**Mitigation:** unchanged — `update_stage_states` provides the safe concurrent write semantics and the lease is single-owner. Adding a `head_sha` key to the same record dict keeps it inside the same single `update_stage_states` call, preserving the single-writer invariant. **BUILD must not write `head_sha` in a second call.**

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #2777] `/do-sdlc` step 3d.4 halting with `REVIEW_VERDICT_MISSING` in repos that declare no `docs/sdlc/` verdict substrate (#2767 §3). Filed during this planning pass. It is a supervisor-contract change in a different file set from all four defects here.
- [SEPARATE-SLUG #2735] `find_plan_path` cwd-dependence, the dominant trigger that drags a run onto the backfill-refusal branch. Fixing the trigger is not fixing the message.
- [SEPARATE-SLUG #2730] Stage markers and dispatch history diverging — a different divergence in the same ledger.
- [ORDERED] Rewriting live ledger records into the split verdict/head_sha shape. Blocked on nothing in this plan actually needing it (the read path tolerates both forms), and a backfill over production ledgers is a human-gated operation that must follow, not accompany, the writer change.

## Update System

No update system changes required. All four changes are internal to `tools/` and `agent/`, plus one global skill body. The global skill `.claude/skills-global/do-pr-review/SKILL.md` is hardlinked to `~/.claude/skills/` by `/update` (`scripts/update/hardlinks.py`), so the §5 flag-type documentation propagates to every machine on the next `/update` with **no new wiring** — the directory is already registered. No new dependencies, no config files, no migration steps.

Per the repo's post-merge convention, run `/update` after this PR merges so running services pick up the new `sdlc-tool` behavior.

## Agent Integration

No new agent integration required. `sdlc-tool` is already an entry point in `pyproject.toml [project.scripts]` and is how agents reach `verdict finalize` today; this plan changes flag names and messages on an existing surface rather than adding one. `agent/sdlc_router.py` is already imported directly by the dispatch path.

Two integration points must be verified rather than added:

- [ ] The renamed `--blocker-count` / `--tech-debt-count` flags are reachable via `sdlc-tool verdict finalize --help` and the old spellings still parse.
- [ ] `.claude/skills-global/do-pr-review/SKILL.md` §5 (the text an agent in ANY repo reads) states the flags take integer counts.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-router-oscillation-guard.md` — document the stale-verdict state row 8b now owns, and the distinguishable no-rule block.
- [ ] Update `docs/sdlc/do-pr-review.md` — new flag names at `:93-99`; correct the "atomic and self-verifying" claim at `:103` to state what actually holds on the backfill-refusal branch.
- [ ] Update `.claude/skills-global/do-pr-review/SKILL.md` §5 — state that the count flags take integers, not findings text.
- [ ] Check `docs/features/README.md` — add or adjust index entries if any of the above becomes a new page.

### Inline Documentation
- [ ] `tools/sdlc_review_finalize.py` module docstring — it currently describes the trailer as "appended" to the verdict; rewrite for the split shape and state the legacy-read compatibility contract.
- [ ] `record_verdict` docstring — document the new `head_sha` kwarg and that it is the ONLY sanctioned way to record a head SHA.
- [ ] The `--verdict` help string at `tools/sdlc_verdict.py:935` — it currently reads "On APPROVED the head_sha trailer is appended if absent", which becomes false the moment #2769 lands.
- [ ] `_rule_patch_applied_after_review` docstring — it states `last == /do-patch` as definitional; update for the widened predicate and name the state it now owns.
- [ ] `write_marker` — comment why its three refusal sites deliberately no longer claim anything about persistence. Same for the fourth site in `main()` at `:969-974`.

## Success Criteria

- [ ] A backfill-refused `verdict finalize` no longer claims "State NOT persisted" when a verdict was written (#2740 AC1).
- [ ] The emitted message lets a reader determine what landed without a separate `stage-query`, or explicitly tells them to run one (#2740 AC2).
- [ ] The sibling-refusal audit result is recorded in the PR: five named gates are already accurate; the overclaim is one sentence at **four** sites (three in `write_marker`, one in `main()` at `:972`), all fixed (#2740 AC3).
- [ ] A test asserts the refusal message against a ledger with a recorded verdict and no marker (#2740 AC4).
- [ ] `tools/sdlc_verdict.py:667`'s documented verdict-first ordering is unchanged (#2740 AC5).
- [ ] `_verdicts["REVIEW"].verdict` reads `APPROVED` (bare token) after a fresh finalize; the SHA is in `head_sha` (#2769).
- [ ] A ledger record in the legacy mangled shape still passes every gate that reads it — no migration performed (#2769).
- [ ] `sdlc-tool verdict finalize --help` states that the count flags take integers; the global `do-pr-review` SKILL.md says so too (#2767a).
- [ ] The spike-1 four-scenario router table yields a `Dispatch` in all four rows, with rows 8 and 9 unchanged (#2767b).
- [ ] A no-rule router block is distinguishable from a guard block (#2767b).
- [ ] Row 8f still resolves a head SHA after the split — no lane sees a spurious head-stale re-review (critique blocker 1).
- [ ] All four `State NOT persisted.` sites are reworded, including `tools/sdlc_stage_marker.py:972` (critique blocker 2).
- [ ] The merge gate still runs the head-SHA comparison (not the timestamp fallback) against a new-shape record, asserted on the `notes` string (critique concern).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] The PR body closes all three issues: `Closes #2740`, `Closes #2769`, `Closes #2767`.

## Team Orchestration

- **Builder (finalize call site)**
  - Name: `finalize-builder`
  - Role: #2740 + #2769 — honest refusal messaging and the verdict/head_sha split, both in `tools/sdlc_review_finalize.py` / `tools/sdlc_verdict.py` / `tools/sdlc_stage_marker.py`
  - Agent Type: builder
  - Resume: true

- **Builder (router)**
  - Name: `router-builder`
  - Role: #2767(b) — widen row 8b, distinguishable no-rule block, oscillation regression tests
  - Agent Type: builder
  - Resume: true

- **Builder (CLI + skill docs)**
  - Name: `flags-builder`
  - Role: #2767(a) — flag rename with aliases, help text, global skill §5, repo addendum
  - Agent Type: builder
  - Resume: true

- **Validator (readers)**
  - Name: `reader-validator`
  - Role: verify every head-SHA reader routes through the single helper and that legacy records still pass every gate
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `cluster-documentarian`
  - Role: the Documentation section above
  - Agent Type: documentarian
  - Resume: true

- **Validator (final)**
  - Name: `cluster-validator`
  - Role: all success criteria and the Verification table
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Enumerate head-SHA readers before touching the writer
- **Task ID**: audit-readers
- **Depends On**: none
- **Validates**: no test — produces a written list consumed by build-verdict-split
- **Informed By**: spike-3 (confirmed: `record_verdict` has no head-SHA field; the record dict is `{verdict, recorded_at, artifact_hash[, blockers, tech_debt, _judges, _consensus]}`)
- **Assigned To**: reader-validator
- **Agent Type**: validator
- **Parallel**: true
- Grep `_HEAD_SHA_TRAILER_RE`, `head_sha`, `latest_review_verdict`, and `_verdicts` across `tools/`, `agent/`, `ui/`, and `tests/`.
- For each hit, record file:line and whether it reads the SHA, the verdict token, or both.
- Flag any reader that does verdict *equality* or raw display — those are the ones #2769 currently breaks and that must be verified after the split.

### 2. Split the verdict and head SHA (#2769)
- **Task ID**: build-verdict-split
- **Depends On**: audit-readers
- **Validates**: `tests/unit/` verdict-record tests (locate via grep for `record_verdict`), `tests/unit/test_sdlc_stage_marker.py`
- **Informed By**: spike-3 (the mangling is whole-string `normalize_verdict` at `tools/sdlc_verdict.py:428-431`, not a separate uppercase step); audit-readers
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Parallel**: false
**HARD CONSTRAINT — this task is ONE commit.** The writer change (bare token + `head_sha` field) and every read-path change below land together. A commit that ships the writer alone bricks `finalize` for every lane on this machine with `REVIEW_TRAILER_MISSING`, because `_review_trailer_present` gates the REVIEW-completed marker at `tools/sdlc_stage_marker.py:640` and `finalize` calls `write_marker`. This is a live outage for peer lanes, not untidiness.

- Add `head_sha: str | None = None` to `record_verdict`; attach it to the record only when truthy, following the `_judges`/`_consensus` precedent, inside the SAME `update_stage_states` call.
- Change `finalize` to pass the bare verdict token plus `head_sha=`; handle an incoming already-trailered verdict by extracting the SHA rather than storing it twice.
- Add the two-entry-point head-SHA read helper over one body — `head_sha_of_record(record: dict)` (field first, legacy regex fallback) and `head_sha_of_text(text: str)` (regex only, for string-only callers).
- Route `_review_trailer_present`, `check_review_persistence`, `tools/merge_predicate.py`, and the router freshness gate through them. **Preserve `merge_predicate`'s lazy in-function import** (`:580`) — the merge-guard hook needs module imports to stay stdlib-only.
- **`tools/sdlc_stage_query.py`** (critique blocker): add a `latest_review_head_sha` key to `_compute_meta` (`:470-545`) sourced via `head_sha_of_record`, because `_extract_verdict_text` (`:443-451`) flattens the record to `record["verdict"]` and drops sibling keys. Make row 8f (`agent/sdlc_router.py:944-951`) prefer `meta.get("latest_review_head_sha")` before falling back to `head_sha_of_text(...)`. Without this, every approved verdict reads head-stale and the router re-dispatches `/do-pr-review` on every lane until G4 escalates.

  **The `_verdicts["REVIEW"]` entry is three-way, not two-way.** `_extract_verdict_text` (`:442-450`) branches `dict` → `str` → `None`, and the third branch is not an edge case: `verdicts.get("REVIEW")` yields `None` for every issue that has not yet recorded a REVIEW verdict, which is the majority of live ledgers. Mirror it exactly:

  ```
  rec = verdicts.get("REVIEW")
  if isinstance(rec, dict):   head = head_sha_of_record(rec)
  elif isinstance(rec, str):  head = head_sha_of_text(rec)
  else:                       head = None
  ```

  **Guard with `isinstance(rec, str)`, never with `not isinstance(rec, dict)`.** The negated form is precisely what routes `None` into `_HEAD_SHA_TRAILER_RE.search(...)`, which raises `TypeError: expected string or bytes-like object`. `head_sha_of_text(text: str)` is typed non-Optional and must never receive `verdicts.get("REVIEW")` directly.

  This is not defensive padding — the unguarded failure is **worse than the blocker it fixes**. An `AttributeError` (bare-str record) or `TypeError` (missing record) inside `_compute_meta` propagates to `tools/sdlc_next_skill.py::_resolve_enriched` (`:87-97`), whose broad `except Exception` returns `{"stages": {}, "_meta": {}}`. That silently discards the ENTIRE ledger, so the router sees a fully-worked issue as brand new and routes it back to `/do-plan`. BLOCKER 1's failure mode was fail-closed-to-stale; this one is fail-open-to-amnesia — and in the `None` case it fires on the common path, not the rare one.
- Leave `_verdict_is_recognized` a substring test — do not tighten it (#2548).

### 3. Make the refusals honest (#2740)
- **Task ID**: build-honest-refusal
- **Depends On**: build-verdict-split
- **Validates**: `tests/unit/test_sdlc_stage_marker.py`, `tests/unit/test_sdlc_dispatch.py`, `tests/integration/test_off_pipeline_merge_path.py`
- **Informed By**: spike-4 **as corrected in critique round 1** (the overclaim is the sentence `State NOT persisted.` at four sites — `:566-567`, `:603-604`, `:717-719` inside `write_marker` plus `:969-974` in `main()`; the five named gates are already accurate and need no change)
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Parallel**: false
- In `finalize`'s marker-refusal branch (`:500-511`), emit a message stating that the verdict and trailer persisted and only the marker did not, with the idempotent-re-run remedy. Keep the named-reason prefix first.
- At all **four** `State NOT persisted.` sites — `:566-567`, `:603-604`, `:717-719` inside `write_marker`, and `:969-974` in `main()` — drop the claim and follow the `STATE_MACHINE_RAISED` precedent at `:733`. The fourth was missed by spike-4 and is a critique blocker; the Verification anti-criterion cannot pass without it. Reword `:972`, do not delete the branch.
- Do NOT reorder any write. Do NOT change the five accurate named-gate messages.
- Add the test asserting the refusal text against a ledger with a recorded verdict and no marker.

### 4. Rename the count flags and document them (#2767a)
- **Task ID**: build-count-flags
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_skill_md_parity.py`, plus a new CLI test for both spellings
- **Informed By**: recon (`tools/sdlc_verdict.py:938-939` currently has no `help=` at all; the repo addendum at `docs/sdlc/do-pr-review.md:93-98` is the only place the integer shape is implied)
- **Assigned To**: flags-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename to `--blocker-count` / `--tech-debt-count` with explicit `help=` text; keep the old spellings as argparse aliases on the same argument.
- Preserve the `default=None` vs `0` distinction.
- Update `.claude/skills-global/do-pr-review/SKILL.md` §5 and `docs/sdlc/do-pr-review.md:93-99`.
- Correct the false "atomic and self-verifying" claim at `docs/sdlc/do-pr-review.md:103`.

### 5. Give row 8b the stale-verdict state (#2767b)
- **Task ID**: build-router-row8b
- **Depends On**: none
- **Validates**: `tests/unit/test_sdlc_router.py`, `tests/unit/test_sdlc_router_oscillation.py`, `tests/unit/test_sdlc_router_decision.py`
- **Informed By**: spike-1 (exact reproduction: stale verdict + `last=/do-pr-review` is unowned) and spike-2 (the widening fixes it without disturbing rows 8 or 9)
- **Assigned To**: router-builder
- **Agent Type**: builder
- **Parallel**: true
- Widen `_rule_patch_applied_after_review` to `last == /do-patch OR _review_verdict_is_stale(...)`, keeping the `pr_number` and `PATCH == completed` conditions.
- Add the spike-1 four-scenario table as tests, all four asserting a `Dispatch`.
- Prove disjointness against rows 8c/8d/8e; add step-asides only where a real overlap is demonstrated.
- Add the #1641/#1668 regression cases: a permanently-stale verdict must escalate via G4, not spin.
- Give the no-rule `Blocked` fallthrough (`:1666-1675`) a distinguishable signal; pick ONE mechanism.
- Update the row-8b docstring.

### 6. Validate the reader compatibility
- **Task ID**: validate-readers
- **Depends On**: build-verdict-split, build-honest-refusal
- **Assigned To**: reader-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm every reader from audit-readers goes through the single helper.
- Confirm a record in the legacy mangled shape passes every gate (merge predicate, trailer presence, freshness).
- Confirm no migration code was added.

### 7. Documentation
- **Task ID**: document-cluster
- **Depends On**: build-verdict-split, build-honest-refusal, build-count-flags, build-router-row8b
- **Assigned To**: cluster-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute every item in the Documentation section.

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-readers, document-cluster
- **Assigned To**: cluster-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table.
- Confirm every Success Criteria checkbox.
- Confirm the PR body closes #2740, #2769, and #2767.

## Verification

| Check | Command | Expected |
|---|---|---|
| Router tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_router.py tests/unit/test_sdlc_router_oscillation.py tests/unit/test_sdlc_router_decision.py -q` | exit code 0 |
| Marker/verdict tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py tests/unit/test_sdlc_dispatch.py -q` | exit code 0 |
| Off-pipeline integration unaffected | `scripts/pytest-clean.sh tests/integration/test_off_pipeline_merge_path.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No false persistence claim remains (all FOUR sites) | `grep -rn "State NOT persisted" tools/ agent/` | exit code 1 |
| Row 8f can see the split head SHA (writer) | `grep -q "latest_review_head_sha" tools/sdlc_stage_query.py` | exit code 0 |
| Row 8f can see the split head SHA (reader) | `grep -q "latest_review_head_sha" agent/sdlc_router.py` | exit code 0 |
| Merge gate still does the head-SHA check on a new-shape record | `scripts/pytest-clean.sh tests/unit/test_merge_predicate.py -q` | exit code 0 |
| `merge_predicate` module imports stay stdlib-only | `grep -n "_HEAD_SHA_TRAILER_RE" tools/merge_predicate.py` | the import is inside a function, not at module level |
| Verdict-first ordering preserved | `grep -c "record_verdict" tools/sdlc_review_finalize.py` | output > 0 |
| head_sha kwarg exists | `grep -c "head_sha" tools/sdlc_verdict.py` | output > 0 |
| Count flags documented in the CLI | `sdlc-tool verdict finalize --help` | output contains `blocker-count` |
| Count flags documented in the global skill | `grep -c "blocker-count" .claude/skills-global/do-pr-review/SKILL.md` | output > 0 |
| Old flag spellings still parse | `sdlc-tool verdict finalize --help` | output contains `--blockers` |
| Anti-criterion: no ledger migration added (No-Go [ORDERED]) | `grep -rn "verdict-finalize-cluster" scripts/update/migrations.py` | exit code 1 |
| Anti-criterion: `_verdict_is_recognized` stays a substring test (#2548) | `grep -c "token in normalized" tools/sdlc_review_finalize.py` | output > 0 |

## Critique Results

### Round 1 — NEEDS REVISION

War room run 2026-08-13, FULL depth (roster: Risk & Robustness, Scope & Value, History & Consistency), plus independent lane verification. Verdict: **NEEDS REVISION** (2 blockers).

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | Row 8f's head-staleness gate cannot see the new `head_sha` field. `agent/sdlc_router.py:944` calls `_latest_review_verdict`, which at `:266-267` prefers `meta["latest_review_verdict"]` — a plain STRING produced by `tools/sdlc_stage_query.py:475` via `_extract_verdict_text` (`:443-451`), which returns only `record["verdict"]` and drops every sibling key. After the split the verdict string carries no trailer, so `agent/sdlc_router.py:950-951` (`if not trailer: return True`) declares EVERY approved verdict head-stale and re-dispatches `/do-pr-review` on every lane until G4 escalates. `tools/sdlc_stage_query.py` is named nowhere in the plan's tasks or Test Impact. | revision 2026-08-13 | The helper cannot be `head_sha_of(record: dict)` alone — the router path only holds a `str`. Two entry points, one body: `head_sha_of_record(record: dict)` (field first, then `_HEAD_SHA_TRAILER_RE` over `record.get("verdict","")`) and `head_sha_of_text(text: str)` (regex only). Add a `latest_review_head_sha` key to `_compute_meta` (`tools/sdlc_stage_query.py:470-545`) sourced from the record, and make row 8f prefer `meta.get("latest_review_head_sha")` before falling back to `head_sha_of_text(...)`. The fail-closed default at `:950` means getting this wrong is a router-wide re-review loop, not a quiet degradation. |
| BLOCKER | History & Consistency; lane-verdict (independent) | Spike-4 undercounted. It claims `State NOT persisted.` exists at "exactly three sites" and Technical Approach step 1 scopes the fix to those three. A fourth occurrence exists at `tools/sdlc_stage_marker.py:972`, in `main()`'s generic non-zero-exit stderr wrapper — outside `write_marker`, which is why the spike structurally could not see it. The plan's own Verification anti-criterion (`grep -rn "State NOT persisted" tools/ agent/` → exit 1) therefore cannot pass as scoped. Verified: the grep returns 4 matches on main (`:567`, `:603`, `:719`, `:972`). | revision 2026-08-13 | Fix `:972` in the same pass as the other three; reword to the `:733` `STATE_MACHINE_RAISED` precedent (point at `sdlc-tool stage-query`) rather than deleting the branch. Every error key `write_marker` returns is currently in `_DIAGNOSED_ERRORS` (`tools/sdlc_stage_marker.py:152-168`), so the branch is dead code today — but it is also the site a bare `sdlc-tool stage-marker` caller would see if an undiagnosed error is ever added, and it holds the literal grep target regardless of reachability. Also correct spike-4's "exactly three sites" text, since the plan instructs BUILD to trust it without re-running the audit. |
| CONCERN | lane-verdict (independent); driver-verified | The writer change and the read-helper change must be ONE commit, and the plan does not say so. `_review_trailer_present` (`tools/sdlc_verdict.py:554`) regex-searches the verdict STRING and gates the REVIEW-completed marker at `tools/sdlc_stage_marker.py:640`. `finalize` calls `write_marker`, so a bare-token writer landing without the read helper bricks EVERY finalize with `REVIEW_TRAILER_MISSING`. Task 2 lists `_review_trailer_present` as a peer bullet with no ordering constraint. | revision 2026-08-13 | State in task `build-verdict-split` that the `record_verdict(head_sha=…)` writer change and the `_review_trailer_present` re-route land in the same commit, never split across commits. Aggravating factor: this lane edits the tool it runs on, and other lanes on this machine call `finalize` concurrently — an intermediate commit is not merely untidy, it is a live outage for peer lanes. |
| CONCERN | Risk & Robustness | `tools/merge_predicate.py:582-595` treats "no trailer in the verdict text" as a legitimate state and silently falls back to a weaker `recorded_at`-vs-commit-date comparison. After the split a new-shape record has no trailer, so the merge gate stops doing the head-SHA check that #2404/#2415 exist to enforce — a silent fail-open, with no test failing. `tests/unit/test_merge_predicate.py` exists but appears nowhere in Test Impact. | revision 2026-08-13 | The distinguishing assertion is on `notes` content, not the boolean: both branches can return "fresh", so a pass/fail-only test cannot detect the downgrade. Assert the exact note string at `tools/merge_predicate.py:588` for a record shaped `{"verdict": "APPROVED", "head_sha": "<40hex>", "recorded_at": …}`. `merge_predicate` imports `_HEAD_SHA_TRAILER_RE` lazily inside the function (`:580`) to keep module imports stdlib-only for the merge-guard hook — the new helper MUST preserve that lazy-import posture or the hook breaks under a bare interpreter. |
| CONCERN | lane-verdict (independent); driver-verified | Row 8c is not merely "disjoint" from row 8b — it CALLS it. `_rule_review_in_progress_no_verdict` invokes `_rule_patch_applied_after_review` as a step-aside at `agent/sdlc_router.py:1225`, so widening 8b directly changes 8c's behavior. The plan's task 5 asks BUILD to "prove disjointness against rows 8c/8d/8e" without naming this call edge, and rates spike-2 only "medium-high". | revision 2026-08-13 | The proof is available now and should replace the deferral: `_review_verdict_is_stale` (`agent/sdlc_router.py:969-973`) returns False whenever `recorded_at` is absent, and `recorded_at` is absent exactly when no verdict was recorded. Rows 8c/8d/8e all require the ABSENCE of a recorded verdict, so the new `or _review_verdict_is_stale(...)` disjunct is identically False on every state they own — disjointness holds by construction. Record this in the plan and keep only the G4 oscillation bound as a genuine test obligation. |
| CONCERN | Scope & Value | The single-PR bundling rationale conflates two claims. "Splitting them guarantees a merge conflict in `finalize`" holds for #2740+#2769 only; #2767(a) (argparse flags) and #2767(b) (`agent/sdlc_router.py` row 8b) touch neither `finalize`'s body nor each other's files. The plan's own task graph confirms it: `build-count-flags` and `build-router-row8b` both carry `Depends On: none` / `Parallel: true`. The real justification for #2767's inclusion is its internal a→b causal chain. | revision 2026-08-13 | Either restate the rationale (merge-conflict argument for #2740+#2769, causal-chain argument for #2767) or split into two PRs. If splitting, `build-count-flags` and `build-router-row8b` ship unchanged as PR #2; only the `Closes` trailers and the Team Orchestration roster split. No task rework either way. |
| NIT | Scope & Value | Risk 3's mitigation ("the only callers are skills we are editing anyway") is an argument for dropping the flag alias, not keeping it. The alias's real purpose is the cross-machine propagation window before each machine's next `/update` picks up the hardlinked global skill — the plan never says so, and the alias has no removal trigger despite NO LEGACY CODE TOLERANCE. | revision 2026-08-13 | State the propagation-window reason explicitly and file a follow-up "drop `--blockers`/`--tech-debt` aliases" issue. Removal is a one-line `add_argument` diff. |
| NIT | lane-verdict (independent); driver-verified | The `--verdict` help string at `tools/sdlc_verdict.py:935` reads "On APPROVED the head_sha trailer is appended if absent", which becomes false the moment #2769 lands. It is listed in neither the Documentation nor the Inline Documentation checklist. | revision 2026-08-13 | Add it to the Inline Documentation checklist alongside the `record_verdict` docstring item. |
| NIT | Risk & Robustness | Nothing gives an operator a way to confirm after the fact that the honest-refusal path fired in production rather than an old message being served from a stale checkout on another machine. | revision 2026-08-13 | Nothing structural required — the Verification-table repo-wide grep plus the post-merge `/update` convention cover it. Listed so the reviewer can decline it explicitly. |

### Round 2 — READY TO BUILD (with concerns)

War room run 2026-08-13 (round 2), FULL depth (force-FULL: the plan edits
`.claude/skills-global/`; roster: Risk & Robustness, Scope & Value, History &
Consistency, 3/3 complete and grounded). Verdict: **READY TO BUILD (with concerns)**
— 0 blockers, 4 concerns, 1 nit.

**Round-1 disposition: all 9 findings verified substantively closed.** BLOCKER 1's
mechanism fix holds: both production callers of `decide_next_dispatch`
(`tools/sdlc_next_skill.py:534`, `agent/session_runner/runner.py:1386`) build `meta`
in-process via `_compute_meta` in the same checkout, so the additive
`latest_review_head_sha` meta key cannot be version-skewed against the router that
reads it — the lane author's self-flagged "two sources for one fact" worry does not
reproduce in this call graph. BLOCKER 2's four-site table, Technical Approach §1,
Task 3's body, the new Success Criteria checkboxes, and the Verification anti-criterion
are all consistent about four sites (the residue is prose-only, captured below). The
one-commit HARD CONSTRAINT names `REVIEW_TRAILER_MISSING` and the peer-lane outage;
the row-8c call edge's disjointness proof was independently verified sound
(`record_verdict` is the sole `_verdicts` writer and always constructs `verdict` +
`recorded_at` together — no partial-write path); the merge_predicate lazy-import and
exact-`notes`-string obligations, the bundling-rationale split, the alias
propagation-window rationale, the `--verdict` help-string checklist item, and the
explicit decline-able observability nit are all present as claimed.

The four concerns are consistency/coordination gaps, not design defects. None blocks
the build.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness; History & Consistency | Stale "three sites" residue survives the BLOCKER-2 revision in four prose spots — Failure Path Test Strategy (line ~322 "the three `write_marker` sites"), Rabbit Holes (~349), Success Criteria #2740 AC3 (~439 "one sentence at three sites, all fixed"), and Task 2's Informed-By line (~527 "exactly three sites") — contradicting the corrected spike-4 table, Technical Approach §1, Task 3's body, and the Verification table, which all say four. AC3 in particular becomes the PR-body audit claim; shipping it at "three... all fixed" undercounts the fix in a plan whose theme is honest reporting. | revision 2 2026-08-13 | `grep -n "three sites\|three \`write_marker\` sites" docs/plans/verdict-finalize-cluster.md` locates all four spots. Update each to four (naming `:972` in `main()` where useful). No automated gate catches plan-doc self-inconsistency, so this must be swept in the revision pass, not left to BUILD. |
| CONCERN | Risk & Robustness | `_compute_meta`'s planned `head_sha_of_record(record: dict)` call does not account for the REVIEW `_verdicts` entry legitimately being a bare `str` — the exact legacy shape `_extract_verdict_text` (`tools/sdlc_stage_query.py:443-451`) already tolerates for the sibling key. An unguarded call raises `AttributeError`, and `tools/sdlc_next_skill.py::_resolve_enriched` (:87-97) wraps `query_enriched` in a broad `except Exception` falling back to `{"stages": {}, "_meta": {}}` — silently discarding the ENTIRE ledger, so the router treats a fully-worked issue as brand new. Strictly worse than the fail-closed-to-stale outcome BLOCKER 1 was written to prevent. | revision 2 2026-08-13 | In `_compute_meta`, branch on `isinstance(verdicts.get("REVIEW"), dict)` before calling `head_sha_of_record`; for the str-shaped legacy case use `head_sha_of_text` — mirroring `_extract_verdict_text`'s dict-or-str duality. Add a `_compute_meta` unit case with a bare-string REVIEW record alongside the new-shape and legacy-mangled-dict cases Task 2's test note already requires. |
| CONCERN | Scope & Value | The Appetite section still reads "Four defects across four files, three of which are small," but the round-2 blocker fix added `tools/sdlc_stage_query.py` to the file fence — five code files, and the fifth carries the router-wide-outage failure mode. The count undersells the blast radius the plan's own revision widened. | revision 2 2026-08-13 | Edit the Appetite paragraph to name the five files (`sdlc_review_finalize.py`, `sdlc_stage_marker.py`, `sdlc_verdict.py`, `sdlc_stage_query.py`, `agent/sdlc_router.py`) and let the router-outage failure mode justify why Medium still holds. |
| CONCERN | History & Consistency; driver-verified | Risk 5's lane-collision mitigation predates the round-2 fence change and is now stale: `docs/plans/sdlc-lane-recorded-slug.md` (status Ready) plans a repair of the dead slug read at `tools/sdlc_stage_query.py::_compute_meta:487`, which falls INSIDE this plan's `_compute_meta:470-545` edit span for `latest_review_head_sha`. A literal git merge conflict is plausible if both lanes build concurrently, and Risk 5 currently gives BUILD no signal to look for it. | revision 2 2026-08-13 | Add `tools/sdlc_stage_query.py::_compute_meta` to Risk 5's named file list and extend the mitigation: "re-check at BUILD start; if `sdlc-lane-recorded-slug` has begun editing `_compute_meta`, coordinate before landing `latest_review_head_sha`." |
| NIT | Scope & Value | The Open Questions preamble claims every question "already has a default chosen and encoded in Technical Approach," but Q4 (no-rule block signal) has no encoded default — §4 explicitly defers with "Choose one and apply it consistently." Does not block build; contradicts the preamble. | revision 2 2026-08-13 | Existing `guard_id` values are short codes (G2/G4/G7), which favors the `guard_id="NO_RULE"` sentinel for convention consistency. Either encode that default at §4 or soften the preamble to name Q4 as the one genuine implementer choice. |

**Structural checks (round 2, superseded by round 3 below):** required sections PASS (Documentation / Update System / Agent Integration / Test Impact all present and substantive; no Popoto model changes, so no migration obligation); task numbering PASS (1-8 contiguous); dependencies PASS (all `Depends On` IDs resolve, no cycles); file paths PASS (all referenced source files exist; the four `State NOT persisted` sites re-verified by grep at `:567`, `:603`, `:719`, `:972`); prerequisites PASS (`gh auth status` OK, venv on the pinned interpreter); cross-references PASS except the three-vs-four residue and the stale Appetite/Risk-5 counts captured above. One Verification-table note for the revision pass: the row `grep -c "latest_review_head_sha" tools/sdlc_stage_query.py agent/sdlc_router.py` with expected "output > 0 in both" is human-evaluable (two `path:count` lines) but not exit-code-checkable — `grep` exits 0 if EITHER file matches; split it into two `grep -q` rows to make "in both" mechanical.

### Round 3 — READY TO BUILD (with concerns)

War room run 2026-08-13 (round 3, confirmation pass), FULL depth (force-FULL: the
plan edits `.claude/skills-global/` and `agent/sdlc_router.py`; roster: Risk &
Robustness, Scope & Value, History & Consistency, 3/3 complete and grounded).
Verdict: **READY TO BUILD (with concerns)** — 0 blockers, 1 concern, 2 nits.

**Round-2 disposition: all 5 findings verified substantively closed in commit
`d1e7bc026`,** independently confirmed by all three critics against real source.
The four `State NOT persisted.` sites are real at `tools/sdlc_stage_marker.py:567`,
`:603`, `:719`, `:972`; the bare-`str` guard prescription matches
`_extract_verdict_text`'s real `isinstance` branching at
`tools/sdlc_stage_query.py:442-450`, and the `_resolve_enriched` swallow-to-empty-ledger
claim is accurate against `tools/sdlc_next_skill.py:86-97`; Appetite names five files
with a Medium justification; Risk 5's collision claim was cross-checked against
`docs/plans/sdlc-lane-recorded-slug.md` (status Ready, does target the dead slug read
at `tools/sdlc_stage_query.py:487`, inside this plan's `:470-545` span); Q4's
`guard_id="NO_RULE"` default matches the real `G2`/`G4`/`G7` short codes at
`agent/sdlc_router.py:331,426,674`; and the `latest_review_head_sha` Verification row
is split into two exit-code-checkable `grep -q` rows.

The one concern is a defect **in the round-2 fix's own prescription**, driver-verified
against live code — not a re-litigation of a closed finding.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness; driver-verified | The bare-`str` guard added by the round-2 fix is only **two-way** (`dict` vs. "the str case"), but the duality it claims to mirror is **three-way**: `_extract_verdict_text` (`tools/sdlc_stage_query.py:442-450`) returns `None` when the record is neither dict nor str, which is exactly what `verdicts.get("REVIEW")` yields whenever no REVIEW verdict has been recorded — the normal state for every issue still in PLAN/BUILD/TEST, i.e. the MAJORITY of live ledgers, not a legacy edge case. A literal reading of Task 2's "branch on `isinstance(...,dict)` first and route the str case to `head_sha_of_text`" sends that `None` into a regex search. Confirmed by driver: `re.compile(...).search(None)` raises `TypeError: expected string or bytes-like object`, which propagates into `_resolve_enriched`'s broad `except Exception` (`tools/sdlc_next_skill.py:95-96`) and discards the ENTIRE ledger. Same fail-open-to-amnesia mode the round-2 fix was written to prevent, but fired on the common path instead of the rare one — and Test Impact (`:339`) requires only three shapes, so no planned test would catch it. | revision 3 2026-08-13 | Make the guard three-way, mirroring `_extract_verdict_text` exactly: `if isinstance(rec, dict): head_sha_of_record(rec)` / `elif isinstance(rec, str): head_sha_of_text(rec)` / `else: None`. The gotcha is the negation: guard with `isinstance(rec, str)`, NEVER with `not isinstance(rec, dict)` — the latter is precisely what routes `None` into the regex. `head_sha_of_text(text: str)` is typed non-Optional, so it must never receive `verdicts.get("REVIEW")` directly. Add a FOURTH Test Impact case to the three at `:339`: a `_verdicts` payload with no `"REVIEW"` key at all, asserted at both the `_compute_meta` and `decide_next_dispatch` levels. |
| NIT | History & Consistency (Scope & Value contested) | The round-2 "three sites" sweep left one un-tabled occurrence at line ~314 (Failure Path Test Strategy, Exception Handling Coverage): the `STATE_MACHINE_RAISED` bullet still calls `:733` "the model the other three sites are being made to follow" when four sites are being rewritten to follow it. Scope & Value read the same line as a legitimate count of the three `write_marker`-internal siblings and cleared it, so it is genuinely ambiguous rather than plainly wrong; Task 3's body is unambiguous at four, so BUILD cannot be misled. | revision 3 2026-08-13 | `grep -n "other three sites" docs/plans/verdict-finalize-cluster.md` returns exactly this one match, outside the `## Critique Results` tables. Reword to name the set rather than count it ("the `write_marker` sites plus `main()`'s `:969-974` wrapper") so neither reading remains available. |
| NIT | Scope & Value | The Appetite paragraph rewritten in round 2 says "the weight is concentrated in three proofs" and enumerates the router-predicate, migration-free, and flattened-string-reader proofs — omitting the bare-`str` guard proof that the same revision calls "worse than the blocker it fixes." The paragraph rewritten specifically to justify Medium against a widened blast radius undersells the scariest risk it introduces. | revision 3 2026-08-13 | No code or test impact; Technical Approach §2 and Test Impact already carry the obligation. Renumber to four proofs and add "that no bare-`str` or absent REVIEW record ever reaches `head_sha_of_record` unguarded." Same self-consistency class as the round-2 "three sites" residue finding. |

**Structural checks (round 3):** required sections PASS (Documentation carries a
`docs/features/` checkbox item; Update System, Agent Integration, and Test Impact all
present and substantive; no Popoto model changes, so no `scripts/update/migrations.py`
migration obligation). Task numbering PASS (1-8 contiguous). Dependencies PASS (every
`Depends On` ID resolves; no cycles). File paths PASS — all 18 referenced source, test,
and doc paths exist on disk, and `grep -rn "State NOT persisted" tools/ agent/` returns
exactly the 4 sites the plan claims. Prerequisites PASS. Cross-references PASS: every
Success Criterion maps to a task; no No-Go or Rabbit Hole appears in the Solution as
planned work. Round-2's own Verification-table note is discharged (the split `grep -q`
rows landed). No structural finding this round.

---

## Open Questions

Each question below already has a default chosen and encoded in Technical Approach, so the plan is buildable as written. They are listed for the critique and the human to override, not to block the build.

1. **Flag rename vs. accept prose (#2767a).** This plan chose rename-plus-alias over deriving counts from prose, on the grounds that a silently-wrong count is worse than a loud failure. Confirm, or say if you would rather `finalize` accept prose and count the findings itself.
2. **Legacy-read posture (#2769).** The plan keeps the trailer-regex fallback permanently, since live ledgers hold mangled strings forever. Confirm that is right rather than treating the fallback as time-boxed scaffolding — this repo's no-legacy-code principle could be read either way, and the answer determines whether the fallback carries a removal note.
3. **Precedence when a record has both `head_sha` and a legacy trailer that disagree.** The plan says define and test a precedence; the natural choice is the field wins. Confirm, or say if a disagreement should hard-fail as a corrupted record.
4. **No-rule block signal (#2767b).** Sentinel `guard_id="NO_RULE"` versus a specific `reason` string a supervisor matches on. Either works; the first is more machine-friendly and slightly changes the `Blocked` contract. Preference?
