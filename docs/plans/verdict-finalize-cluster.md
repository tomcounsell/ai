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

Three issues, one lane, one PR. They are grouped because #2740 and #2769 are two defects at the **same call site** (`tools/sdlc_review_finalize.py::finalize`) and #2767's two halves are causally chained to each other through that same call. Splitting them guarantees a merge conflict in `finalize` and would leave the #2767 chain half-diagnosed.

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
- **Finding**: **they do not.** `REVIEW_VERDICT_MISSING` (`:625-631`), `REVIEW_TRAILER_MISSING` (`:640-647`), `REVIEW_ARTIFACT_MISSING` (`:658-665`), `CRITIQUE_VERDICT_MISSING` (`:668-681`) and `ISSUE_LOCKED` (`:686-690`) all end with `Marker write refused.` / `marker write refused.` — scoped to the marker, and accurate. The overclaim is the sentence **`State NOT persisted.`**, which appears at exactly **three** sites: `:566-567` (`start_stage` refusal), `:603-604` (`STAGE_RAN_NOT_SKIPPABLE`), and `:717-719` (the backfill refusal #2740 reports).
- **Confidence**: high.
- **Impact on plan**: the audit is *done* — BUILD fixes three sites, not five, and records the finding rather than re-running the audit. Note `:566` and `:603` are reachable from bare `stage-marker` calls too, where the sentence is often true but still unknowable from inside `write_marker`.

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

Four defects across four files, three of which are small. The weight is in proving the router predicate change does not re-open #1641/#1668 and that the `head_sha` field change is genuinely migration-free against live ledgers.

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
- In `write_marker`: at all three `State NOT persisted.` sites (`:566-567`, `:603-604`, `:717-719`), drop the claim. Follow the `STATE_MACHINE_RAISED` precedent at `:733` — say the marker write was refused and point at `sdlc-tool stage-query`. `write_marker` must never assert anything about writes it did not perform.

Do **not** make `finalize` transactional. The verdict-first ordering is deliberate (`tools/sdlc_verdict.py:667`, #2577/#2415) and explicitly out of scope.

**2. #2769 — separate the trailer from the verdict (`tools/sdlc_verdict.py`, `tools/sdlc_review_finalize.py`)**

- `record_verdict` gains `head_sha: str | None = None`; when provided it is stored as a `head_sha` key on the record, un-normalized apart from case folding, alongside the existing keys. It follows the `_judges` / `_consensus` precedent: only attached when the caller passes it, so the persisted shape for every other caller is bit-identical.
- `finalize` stops concatenating. It passes `verdict=verdict.strip()` (bare token) and `head_sha=head_sha` separately.
- **The read path is where the compatibility lives.** Introduce one helper — the single place that answers "what is this record's head SHA?" — which prefers the `head_sha` field and falls back to running `_HEAD_SHA_TRAILER_RE` over the `verdict` string. Route `_review_trailer_present` (`tools/sdlc_verdict.py:554`), `check_review_persistence`'s trailer check (`sdlc_review_finalize.py:287-292`), `tools/merge_predicate.py`, and the router's freshness gate through it. Live ledgers already hold mangled strings; nothing may require them to be rewritten.
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

- Prove disjointness against rows 8c/8d/8e explicitly (8c requires `in_progress`; 8d requires no verdict; 8e requires `completed` + no verdict) and add step-asides only where a real overlap is demonstrated, not defensively.
- Confirm G4 (`same_stage_dispatch_count`) still bounds the resulting re-review loop, so a permanently-stale verdict escalates to a human rather than spinning. This is the #1641/#1668 re-opening risk and must be tested, not asserted.
- Update the row-8b docstring, which currently states the `last == /do-patch` requirement as definitional.

Separately, at `agent/sdlc_router.py:1666-1675`: give the no-rule fallthrough a distinguishable signal so a supervisor can tell it from a guard block. The neighbouring UNKNOWN-merge-state branch already models a specific reason string; the bare fallthrough should carry an equally specific marker (e.g. a sentinel `guard_id` such as `"NO_RULE"`, or a reason string a supervisor can match on). Choose one and apply it consistently; do not add a second mechanism.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `tools/sdlc_review_finalize.py` — the `except Exception` in `check_review_persistence` (`:333-343`) is a deliberate fail-closed catch. Assert it still yields `ok: False` with a preserved `reason` when the new `head_sha` read helper raises.
- [ ] `agent/sdlc_router.py` — `_review_verdict_is_stale` (`:967-981`) swallows everything and fails safe to `False` (not stale). With row 8b now depending on it, add a test that a malformed `recorded_at` yields "not stale" and therefore leaves row 8 in control, rather than silently routing to a re-review loop.
- [ ] `agent/sdlc_router.py` — `decide_next_dispatch` catches predicate exceptions and continues (`:1650-1656`). Assert the widened row 8b predicate never raises on a malformed `_verdicts` payload (non-dict, missing keys, `None`).
- [ ] `tools/sdlc_stage_marker.py` — the outer `except Exception` (`:727-735`) already reports `STATE_MACHINE_RAISED` accurately; assert its message is unchanged by this work (it is the model the other three sites are being made to follow).

### Empty/Invalid Input Handling
- [ ] `record_verdict(head_sha=...)` with `None`, `""`, whitespace, and a non-40-hex string — assert no `head_sha` key is written for falsy input and that a malformed value never produces a record that reads as a valid trailer.
- [ ] `finalize` with a verdict that *already* carries a trailer — assert the SHA lands in `head_sha` exactly once and is not also left inside the verdict string.
- [ ] The head-SHA read helper against: a record with only `head_sha`, only a legacy mangled `verdict`, both (agreeing), both (disagreeing — define and test the precedence), and neither.
- [ ] `--blocker-count` with a negative value and with `0` — assert `0` is preserved as a real count and not conflated with "not provided" (the current `default=None` distinction must survive the rename).

### Error State Rendering
- [ ] The #2740 message itself is a user-visible error path and is the deliverable. Assert the exact refusal text against a ledger holding a recorded verdict and no marker: it must NOT contain "State NOT persisted", must state that the verdict persisted, and must name the remedy.
- [ ] Assert the three `write_marker` sites no longer emit `State NOT persisted.` — a repo-wide grep for that sentence must return zero matches.
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

No xfail/xpass markers exist for any of these defects (grep of `tests/` for `xfail` intersected with verdict/finalize/router/marker returned nothing), so there are no expected-failure conversions to perform.

## Rabbit Holes

- **Making `finalize` transactional.** Explicitly forbidden by #2740. The verdict-first ordering is a deliberate design that #2577 hardened the read sites around. Undoing it is a multi-day rewrite that trades a wrong error message for a real correctness risk.
- **Fixing #2735 to make the refusal branch rarer.** Tempting because it removes most occurrences. It does not make the message honest, and it is somebody else's issue.
- **Tightening `_verdict_is_recognized` to an equality check.** Looks like the "real" fix for #2769. It re-opens #2548 (`"APPROVED (0 BLOCKERS)"` would stop being recognized) and does nothing about the already-stored mangled strings.
- **Migrating existing mangled verdict strings.** #2769's own fix shape says migration-free. A backfill over live ledgers is a destructive operation with no upside once the read path tolerates both forms.
- **Rewriting the dispatch rule table.** #2767(b) is one over-narrow predicate. Four prior fixes (#1641, #1668, #1932, and this one) each closed one hole; a wholesale redesign is a separate project with its own plan.
- **Teaching `finalize` to parse prose findings into counts.** A derived count that is silently wrong is worse than the loud failure today.
- **Auditing every `sdlc-tool` error string for honesty.** spike-4 bounded the actual overclaim to one sentence at three sites. Resist expanding to a repo-wide error-message audit.

## Risks

### Risk 1: The split verdict/head_sha shape breaks a reader we did not find
**Impact:** a merge gate or freshness check silently fails open or closed on a live pipeline — the highest-severity outcome in this lane, since #2404/#2415 exist precisely to keep that gate honest.
**Mitigation:** enumerate readers by grepping `_HEAD_SHA_TRAILER_RE`, `head_sha`, and `_verdicts` across `tools/`, `agent/`, and `ui/` before changing the writer; route every hit through the single read helper; keep the regex fallback permanently (it is not scaffolding — legacy records are permanent). Add a test that a record in the *old* shape still satisfies every gate.

### Risk 2: The widened row 8b re-opens an oscillation class
**Impact:** the router ping-pongs `/do-pr-review` on a PR whose verdict stays stale, burning a lane.
**Mitigation:** spike-2 already shows rows 8 and 9 are undisturbed. BUILD must additionally prove G4 bounds the new path (a permanently-stale verdict escalates to a human) and add the #1641/#1668 regression cases to `test_sdlc_router_oscillation.py`.

### Risk 3: The flag alias becomes permanent cruft
**Impact:** the repo carries two spellings forever, violating the no-legacy-code principle.
**Mitigation:** the alias is argparse-level (one `add_argument` call listing both spellings), not a second code path, and every document is updated to the new spelling in this same PR. If the reviewer judges even that too much, dropping the alias is a one-line change — the only callers are skills we are editing anyway.

### Risk 4: The new refusal message is verbose enough that agents skim it
**Impact:** we replace a wrong message with an ignored one.
**Mitigation:** keep the named-reason prefix first (it is what tooling matches on), state the split in one sentence, and give exactly one remedy. Do not enumerate every field that landed.

### Risk 5: Concurrent lanes touch the same files
**Impact:** merge conflicts with `sdlc-lane-recorded-slug` or `gates-that-cannot-fire`.
**Mitigation:** neither currently edits `sdlc_review_finalize.py`, `record_verdict`'s signature, or `DISPATCH_RULES`. Re-check at BUILD start; if `gates-that-cannot-fire` has begun editing rows, coordinate before touching row 8b.

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
- [ ] `_rule_patch_applied_after_review` docstring — it states `last == /do-patch` as definitional; update for the widened predicate and name the state it now owns.
- [ ] `write_marker` — comment why the three refusal sites deliberately no longer claim anything about persistence.

## Success Criteria

- [ ] A backfill-refused `verdict finalize` no longer claims "State NOT persisted" when a verdict was written (#2740 AC1).
- [ ] The emitted message lets a reader determine what landed without a separate `stage-query`, or explicitly tells them to run one (#2740 AC2).
- [ ] The sibling-refusal audit result is recorded in the PR: five named gates are already accurate; the overclaim is one sentence at three sites, all fixed (#2740 AC3).
- [ ] A test asserts the refusal message against a ledger with a recorded verdict and no marker (#2740 AC4).
- [ ] `tools/sdlc_verdict.py:667`'s documented verdict-first ordering is unchanged (#2740 AC5).
- [ ] `_verdicts["REVIEW"].verdict` reads `APPROVED` (bare token) after a fresh finalize; the SHA is in `head_sha` (#2769).
- [ ] A ledger record in the legacy mangled shape still passes every gate that reads it — no migration performed (#2769).
- [ ] `sdlc-tool verdict finalize --help` states that the count flags take integers; the global `do-pr-review` SKILL.md says so too (#2767a).
- [ ] The spike-1 four-scenario router table yields a `Dispatch` in all four rows, with rows 8 and 9 unchanged (#2767b).
- [ ] A no-rule router block is distinguishable from a guard block (#2767b).
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
- Add `head_sha: str | None = None` to `record_verdict`; attach it to the record only when truthy, following the `_judges`/`_consensus` precedent, inside the SAME `update_stage_states` call.
- Change `finalize` to pass the bare verdict token plus `head_sha=`; handle an incoming already-trailered verdict by extracting the SHA rather than storing it twice.
- Add the single head-SHA read helper (field first, legacy regex fallback) and route `_review_trailer_present`, `check_review_persistence`, `tools/merge_predicate.py`, and the router freshness gate through it.
- Leave `_verdict_is_recognized` a substring test — do not tighten it (#2548).

### 3. Make the refusals honest (#2740)
- **Task ID**: build-honest-refusal
- **Depends On**: build-verdict-split
- **Validates**: `tests/unit/test_sdlc_stage_marker.py`, `tests/unit/test_sdlc_dispatch.py`, `tests/integration/test_off_pipeline_merge_path.py`
- **Informed By**: spike-4 (the overclaim is the sentence `State NOT persisted.` at exactly three sites — `:566-567`, `:603-604`, `:717-719`; the five named gates are already accurate and need no change)
- **Assigned To**: finalize-builder
- **Agent Type**: builder
- **Parallel**: false
- In `finalize`'s marker-refusal branch (`:500-511`), emit a message stating that the verdict and trailer persisted and only the marker did not, with the idempotent-re-run remedy. Keep the named-reason prefix first.
- At all three `write_marker` sites, drop `State NOT persisted.` and follow the `STATE_MACHINE_RAISED` precedent at `:733`.
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
| No false persistence claim remains | `grep -rn "State NOT persisted" tools/ agent/` | exit code 1 |
| Verdict-first ordering preserved | `grep -c "record_verdict" tools/sdlc_review_finalize.py` | output > 0 |
| head_sha kwarg exists | `grep -c "head_sha" tools/sdlc_verdict.py` | output > 0 |
| Count flags documented in the CLI | `sdlc-tool verdict finalize --help` | output contains `blocker-count` |
| Count flags documented in the global skill | `grep -c "blocker-count" .claude/skills-global/do-pr-review/SKILL.md` | output > 0 |
| Old flag spellings still parse | `sdlc-tool verdict finalize --help` | output contains `--blockers` |
| Anti-criterion: no ledger migration added (No-Go [ORDERED]) | `grep -rn "verdict-finalize-cluster" scripts/update/migrations.py` | exit code 1 |
| Anti-criterion: `_verdict_is_recognized` stays a substring test (#2548) | `grep -c "token in normalized" tools/sdlc_review_finalize.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

Each question below already has a default chosen and encoded in Technical Approach, so the plan is buildable as written. They are listed for the critique and the human to override, not to block the build.

1. **Flag rename vs. accept prose (#2767a).** This plan chose rename-plus-alias over deriving counts from prose, on the grounds that a silently-wrong count is worse than a loud failure. Confirm, or say if you would rather `finalize` accept prose and count the findings itself.
2. **Legacy-read posture (#2769).** The plan keeps the trailer-regex fallback permanently, since live ledgers hold mangled strings forever. Confirm that is right rather than treating the fallback as time-boxed scaffolding — this repo's no-legacy-code principle could be read either way, and the answer determines whether the fallback carries a removal note.
3. **Precedence when a record has both `head_sha` and a legacy trailer that disagree.** The plan says define and test a precedence; the natural choice is the field wins. Confirm, or say if a disagreement should hard-fail as a corrupted record.
4. **No-rule block signal (#2767b).** Sentinel `guard_id="NO_RULE"` versus a specific `reason` string a supervisor matches on. Either works; the first is more machine-friendly and slightly changes the `Blocked` contract. Preference?
