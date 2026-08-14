---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2755
last_comment_id:
---

# sdlc_progress lane discovery: read recorded identity, not branch shape

## Problem

`reflections/sdlc_progress.py` is the SDLC stall detector. Every 30 minutes it asks
"which lanes are wedged?" and answers by pattern-matching branch names:
`_SDLC_BRANCH_RE = re.compile(r"^session/sdlc-\d+$")` (`:118`), applied as a corpus
filter over every open PR (`:217-236`), with the issue number recovered by parsing
the slug back out (`:263-266`).

#2735 (merged as PR #2792) established that a lane's identity is a *recorded* value
on `PipelineLedger.slug`, and that **both** slug shapes are real: issue-derived
(`sdlc-2755`) and human-named (`dashboard-jinja-filter-registrar`, `dev-41a59eee`).
A shape filter can only see the first kind.

**Current behavior:**

Measured against production on 2026-08-13, at plan time:

| Open PR | Head branch | Seen by the detector today? |
|---|---|---|
| #2798 | `session/dashboard-jinja-filter-registrar` | no |
| #2746 | `session/hook-validator-target-resolution` | no |
| #2695 | `session/dev-41a59eee` | no |
| #2685 | `session/flip-steering-writers-to-room-key` | no (also draft) |
| #2683 | `session/suite-failure-rotation-db-ownership` | no |

**Zero of the seven currently-open PRs match `session/sdlc-<N>`.** The stall detector
is not degraded — it is presently blind to the entire lane population. And it is
silent about it: no lane is reported missing, because a lane that never enters the
corpus is never counted.

**Census caveat (added at critique, 2026-08-14).** This table is a snapshot and has
already decayed: #2695 and #2683 merged overnight (`0f070970b`, `fb00b8542`), and
#2685 is a draft, which the retained draft filter (`:234`) excludes. The count that
sizes the burst mitigation is therefore volatile by nature. The mitigation is
justified **structurally** — a detector with no track record on a population it has
never seen should not act on all of it at once — not by any particular census
number. Treat `session/dev-41a59eee` in the test plan as a synthetic fixture string,
not a live lane.

**Desired outcome:**

Discovery is driven by recorded identity and by links that exist in the world (the
PR's own closing reference), not by the shape of a string. A stalled lane on
`session/dev-41a59eee` is detected, steered, resumed, or escalated exactly like a
lane on `session/sdlc-2755`. When identity cannot be resolved unambiguously, the
tick reports `gate-unknown` and declines to act — it never guesses.

## Freshness Check

**Baseline commit:** `34ab8da2f` (working tree clean; `e50eba258` is the last code commit touching the referenced files)
**Issue filed at:** 2026-08-13T05:18:28Z
**Disposition:** Minor drift

**File:line references re-verified:**

- `reflections/sdlc_progress.py:116` — issue claimed `_SDLC_BRANCH_RE` — **drifted to `:118`**, definition otherwise identical.
- `reflections/sdlc_progress.py:216-235` — issue claimed `_list_open_sdlc_prs` — **drifted to `:217-236`**; the regex is applied at `:235`.
- `reflections/sdlc_progress.py:262` — issue claimed `_issue_number_from_slug` — **drifted to `:263-266`**, body identical (`re.match(r"sdlc-(\d+)$", slug)`).
- `reflections/sdlc_upvote_lanes.py:328-344` — the already-converted sibling `_has_pr_on_branch` — **still holds**, now at `:329-345`, and is the reference pattern the issue advertised.
- `agent/pipeline_ledger.py:200` — `PipelineLedger.slug` — **exists and is documented** (`:166-190`). Prerequisite satisfied.

**Cited sibling issues/PRs re-checked:**

- **#2735** — closed; shipped as PR **#2792**, merged 2026-08-13T13:14:20Z. It introduced `PipelineLedger.slug`, `tools/lane_identity.py`, and the pass-through of a human-named slug on `sdlc_progress`'s create rung (`:704-716`). The stated prerequisite is met; this plan is unblocked.
- **#2718** — the wedge shape #2735 closed from the *write* direction. This issue is the same shape from the *read* direction.

**Commits on main since issue was filed (touching referenced files):**

- `e50eba258` "SDLC lane identity: one recorded slug, minted once (#2735, #2718) (#2792)" — **partially addresses**: it fixed the sibling site and the create rung, and it created the substrate this plan reads. It deliberately did not touch the discovery path (minimum-solution constraint), which is exactly why #2755 exists.

**Active plans in `docs/plans/` overlapping this area:** `sdlc-lane-recorded-slug.md` — the #2735 plan, `status: Ready`, `revision_applied: true`, and now **shipped**. It is this plan's predecessor, not a live competitor. No coordination needed; no other plan touches `reflections/sdlc_progress.py`.

**Notes:** All three drifted line numbers are corrected inline in the Technical Approach below. The drift is entirely attributable to `e50eba258` adding the `adopt_lane_slug` import at `:96`.

## Prior Art

- **PR #2792 (#2735 / #2718)**: "SDLC lane identity: one recorded slug, minted once" — introduced `PipelineLedger.slug`, `tools/lane_identity.py`'s four-rung adoption ladder, and converted `sdlc_upvote_lanes.py::_has_pr_on_branch` from a shape probe to a recorded-identity read. **Succeeded.** It is the direct predecessor and the source of every substrate this plan consumes. Its module docstring (`tools/lane_identity.py:1-46`) is required reading for the builder: it explains why a `docs/plans/` filename rung and a `git worktree list` rung are both deliberately absent.
- **PR #2710 (#2696)**: "SDLC stall auto-resume: steer, resume, or create an eng session; escalate once" — built the entire action ladder in `sdlc_progress.py` that this plan feeds. **Succeeded**, and is not being modified: this plan changes only which lanes reach the ladder, never what the ladder does with them.
- **`tools/merge_predicate.py::_resolve_tracked_issue` (`:355-440`)** — not a fix for this bug, but the closest existing solution to the same sub-problem ("given a PR, which issue owns it?"). It resolves via `PipelineLedger.query.filter(pr_number=...)` with repo-scoping and distinct-issue disambiguation, and its docstring records that an earlier `AgentSession.query.filter(slug=..., issue_number=...)` mechanism (PR #2035) was **empirically inert** — the two fields are populated by disjoint code paths, so the resolver always degraded to NO_SIGNAL. That failure is the reason this plan does not build a single-source resolver.

No closed issue matches "lane discovery branch shape" — this is the first attempt at the discovery path.

## Research

Purely internal work: no external libraries, APIs, or ecosystem patterns are involved.
The change is confined to one reflection module, one Popoto model already in the repo,
and the `gh` CLI already in use throughout the file.

No relevant external findings — proceeding with codebase context and training data.

## Spike Results

### spike-1: Does the production ledger actually carry enough identity to drive discovery?
- **Assumption**: "`PipelineLedger.slug` and `.pr_number` are populated densely enough that ledger-driven discovery is a strict improvement over the regex."
- **Method**: code-read + read-only production probe (`PipelineLedger.query`)
- **Finding**: **Partly false, and this is the plan's central constraint.** Production holds 8 ledger records total (4 for `tomcounsell/ai`, 4 for `tomcounsell/popoto`). Of the `ai` records, **2 carry a `slug`** and **exactly 1 carries a `pr_number`** (issue 2719 → PR 2798 → slug `dashboard-jinja-filter-registrar`). Against the 5 open `session/*` PRs, a ledger-only resolver — by `pr_number` *or* by recorded `slug` — resolves **1 of 5**. The ledger is young: it only starts carrying identity for lanes that began after #2792, and `pr_number` is written only by `sdlc-tool meta-set --key pr_number` at PR-creation time.
- **Confidence**: high (direct read of the live keyspace)
- **Impact on plan**: The issue's proposed design — "enumerate `PipelineLedger`, read `slug`, build the branch set" — would have shipped a detector that sees 1 lane instead of 0. Better, but still blind to 4 of 5. The resolver must therefore be a **ladder** with a rung that adopts identity the world already publishes, not a single ledger read.

### spike-2: Can a PR's own closing reference supply the issue number?
- **Assumption**: "`gh pr list --json closingIssuesReferences` works and covers the lanes the ledger misses."
- **Method**: prototype (live `gh` call, read-only)
- **Finding**: **True, and it closes the gap.** `gh pr list --state open --json number,headRefName,isDraft,closingIssuesReferences` returns the field for every PR in a single call — no extra round trip per PR. All 5 open `session/*` PRs carry at least one closing reference: #2798→[2719], #2746→[2689, 2738], #2695→[2694], #2685→[2642], #2683→[2628]. Coverage against the ledger-only rung goes from 1/5 to 5/5.
- **Confidence**: high
- **Impact on plan**: `closingIssuesReferences` becomes rung 3 of the resolver, sourced from the *same* `gh` call that already fetches the corpus, so it costs zero additional subprocess work.

### spike-3: Is the multi-closing-reference ambiguity hypothetical?
- **Assumption**: "A PR closes at most one issue in practice, so rung 3 can take the first reference."
- **Method**: prototype (same live `gh` call)
- **Finding**: **False.** PR #2746 (`session/hook-validator-target-resolution`) declares **two** closing references: #2689 and #2738. Taking `[0]` would bind the lane's attempt budget, cooldown key, escalation key, and any created session to a sub-issue chosen by document order. `merge_predicate.py`'s docstring names this exact hazard ("the first `Closes #N` in the PR body ... for a multi-issue-closure PR, points at a sub-issue with no SDLC substrate").
- **Confidence**: high
- **Impact on plan**: Rung 3 adopts only on a **single distinct** reference. Two-or-more is a `gate-unknown: issue-ambiguous` finding and the lane is skipped, never guessed at. This mirrors `merge_predicate`'s AMBIGUOUS outcome and `lane_identity`'s rung-2 "unique match required" rule.

### spike-5: Can the worker's checkout actually resolve these branches? (added at critique)
- **Assumption**: "`_last_commit` will return a timestamp for a human-named lane branch."
- **Method**: to be run before build — `git log -1 --format=%ct origin/session/<human-named-branch>` in the worker's `ai` checkout, for each currently-open lane branch.
- **Why it is load-bearing**: `_last_commit` (`:277-300`) resolves `origin/<branch>` and returns `None` on a missing remote-tracking ref, at which point the loop does a silent `continue` (`:829-831`). Under the old regex this path was unreachable in practice because the corpus was empty. After widening, it is the gate that decides whether this fix delivers anything at all — and it would fail *silently*, which is the exact shape this plan opens by condemning.
- **Impact on plan**: if refs are missing, the fix needs a fetch step or an explicit remote read. Regardless of the outcome, the `commit is None` skip gains a `gate-unknown: branch-not-fetched {slug}` finding (see Key Elements) so this can never be a silent zero again. **This spike must be run and its result recorded before build starts.**

### spike-4: What is the blast radius of widening the corpus filter?
- **Assumption**: "Widening `^session/sdlc-\d+$` to a `session/` prefix admits only lanes."
- **Method**: prototype (live `gh` call over all open PRs)
- **Finding**: Of 7 open PRs, 5 are `session/*` and 2 are not (`fix/router-blocked-on-conflict`, `fix/g8-branch-resolution` — hotfix branches, correctly excluded by the prefix). So the `session/` namespace is a clean lane boundary today. But the corpus goes from **0 lanes to 5 lanes on the first tick after deploy**, all of them long-lived and several of them almost certainly past the 4-hour stall threshold. The create brake is 1/tick, but there is **no per-tick cap on steer or resume**.
- **Confidence**: high for the counts; medium for "all 5 are past threshold" (depends on each branch's last commit time at deploy)
- **Impact on plan**: Adds a per-tick action cap (see Key Elements) and a Risk row. A detector that has never acted on these lanes should not act on all of them in its first 30-minute window.

## Data Flow

1. **Entry point**: the `sdlc-progress-check` reflection fires on its schedule; `run_per_project_audit` calls `_check_project_stalls(project)` once per owned project (`reflections/sdlc_progress.py:754`).
2. **Repo resolution**: `target_repo = _project_repo(project)` (`:771`) yields `owner/name`, or `None` for a project with no `github` block.
3. **Corpus fetch**: `_list_open_lane_prs(cwd)` runs one `gh pr list` and returns open non-draft PRs whose head is in the `session/` namespace, each carrying `number`, `headRefName`, `isDraft`, `closingIssuesReferences`.
4. **Slug**: `_slug_from_branch(branch)` strips `session/` — unchanged, already shape-agnostic.
5. **Issue resolution** (new): `_resolve_lane_issue(pr, slug, target_repo, ledger_map)` walks the three-rung issue resolution ladder and returns `(issue_number | None, reason)`. The ledger rung reads a map built once per tick; the closing-reference rung reads the payload already in hand; the last rung derives from the slug. Resolution runs *after* the staleness gate, so a fresh healthy lane never pays for it.
6. **Gates**: unchanged — issue-open (`_issue_is_open`), staleness (`_last_commit` + threshold), liveness (`_lane_is_live`), escalation-once (`_escalation_exists`), attempt budget (`_attempts_count`), action cooldown (`_action_cooldown_set`).
7. **Action**: unchanged — `_pick_steer_target` then `_attempt_action` (steer / resume / create), with `adopt_lane_slug(issue_number, slug, target_repo)` on the create rung (`:716`).
8. **Output**: `findings` list and `counts` dict returned to the reflection runner; escalations reach a human via `_send_alert`.

The change is confined to steps 3 and 5. Steps 6-8 are untouched, which is the point: this plan changes *which lanes reach the ladder*, never what the ladder does.

## Architectural Impact

- **New dependencies**: none. `PipelineLedger` is already imported in sibling reflections (`sdlc_upvote_lanes.py:317`); `sdlc_progress.py` already imports `tools.lane_identity` (`:96`).
- **Interface changes**: `_list_open_sdlc_prs` is renamed to `_list_open_lane_prs` and its returned dicts gain a `closingIssuesReferences` key. Both are module-private; the only external references are tests (see Test Impact). `_SDLC_BRANCH_RE` is deleted. `_issue_number_from_slug` survives as the ladder's last rung.
- **Coupling**: increases `sdlc_progress` → `PipelineLedger` coupling by one read path, and *decreases* coupling to branch-naming convention — which is the trade this issue exists to make. The lane-identity contract moves from an implicit string convention to an explicit recorded field, matching `sdlc_upvote_lanes` and `merge_predicate`.
- **Data ownership**: unchanged. Discovery is read-only against the ledger. The single write on the create rung (`adopt_lane_slug`) already exists and is conditional-on-empty.
- **Reversibility**: high. One module, one function boundary, no schema change, no migration. Reverting restores the regex.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (the resolver-ladder shape and the per-tick cap default are the two decisions worth confirming)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `PipelineLedger.slug` exists | `python -c "from agent.pipeline_ledger import PipelineLedger; assert hasattr(PipelineLedger, 'slug')"` | #2735 substrate this plan reads |
| `gh` supports `closingIssuesReferences` on `pr list` | `gh pr list --state open --limit 1 --json number,closingIssuesReferences` | Resolver rung 3 |
| Redis reachable for ledger reads | `python -c "from agent.pipeline_ledger import PipelineLedger; list(PipelineLedger.query.filter(target_repo='tomcounsell/ai'))"` | The per-tick ledger map. Note this proves reachability only: `target_repo` is an unindexed plain `Field`, so Popoto loads every key and filters in Python — the call is a full enumeration, not a scoped read. |

## Solution

### Key Elements

- **Namespace corpus filter**: `_list_open_lane_prs` admits open non-draft PRs whose head branch is in the `session/` namespace and whose slug remainder is **non-empty**. The `session/` prefix is the lane boundary; the *shape of the rest* is not consulted. `_SDLC_BRANCH_RE` is deleted outright — the branch-shape concept leaves the module rather than lingering as a demoted fallback (AC bullet 4). The draft filter at `:234` is **retained unchanged**; drafts are a separate, deliberate exclusion and this plan does not revisit them.
- **Three-rung issue resolver** (`_resolve_lane_issue`): a single helper returning `(issue_number | None, reason)`, the module's only path from a PR to an issue number. Named the *issue resolution ladder* to avoid colliding with `tools/lane_identity.py`'s existing (and inverse) four-rung slug ladder. Ordered most-authoritative first:
  1. **Recorded ledger, by PR number** — a lookup into the per-tick ledger map (see below), repo-scoped to `target_repo`, ignoring records whose `issue_number` is `None`, and requiring exactly one distinct `issue_number`. Precedent: `tools/merge_predicate.py:415`.
  2. **The PR's own closing reference** — the single distinct entry in `closingIssuesReferences` **whose repository equals `target_repo`**, already in the corpus payload. This adopts an identity that exists in the world (the same principle as `lane_identity.py`'s adopt rungs), and it is what covers the lanes the ledger cannot see today.
  3. **Issue-derived slug shape** — `_issue_number_from_slug`, retained solely as the last rung with a docstring saying so. It is the only rung that *derives* rather than reads, so it sits at the bottom.
  Any rung that finds two-or-more candidates does **not** fall through to a weaker rung — it returns `None` with an `ambiguous` reason. Falling through from "two authoritative answers" to "one guessed answer" would be strictly worse than declining.
- **Ledger-by-recorded-slug is deliberately NOT a rung.** The original draft had it as rung 2. It is cut because it cannot fire for the population this plan exists to serve: `docs/features/sdlc-lane-identity.md:110-121` records that a human-named lane's minter writes `sdlc-{N}`, and `_record_slug_if_empty` is no-overwrite, so `ledger.slug == branch slug` is permanently false for exactly those lanes. Where it *is* true, the record either carries a `pr_number` (rung 1 already answered) or the slug is `sdlc-N` (rung 3 restated, more expensively). Spike-1's single resolved lane was resolved by `pr_number`. Adding it "for when the ledger matures" would ship an unfalsifiable rung — the #2035 inertness shape this plan's own Prior Art section warns about. If the ledger's slug-writing behavior changes, that is a new issue with a new measurement.
- **One ledger read per tick, not per PR.** `slug`, `pr_number`, and `target_repo` are unindexed plain `Field`s, so Popoto resolves these filters by loading every key and filtering in Python — "repo-scoped" scopes nothing at the Redis layer, and `PipelineLedger` has no TTL by design. `_check_project_stalls` therefore performs **one** repo-scoped enumeration up front, builds a `{pr_number: issue_number}` map, and rung 1 reads the map. Cost is O(ledger) per tick instead of O(open lanes × ledger).
- **Resolution runs after the staleness gate.** `_last_commit` needs only the branch, so a fresh healthy lane never pays for identity resolution or a `gh issue view`. This also bounds finding noise: unlike every existing `gate-unknown` (all transient), an unresolvable identity is a *stable* condition that would otherwise emit a finding every 30 minutes forever.
- **Explicit unresolved reporting**: a lane whose issue cannot be resolved emits `gate-unknown: issue-unresolved {slug}` or `gate-unknown: issue-ambiguous {slug}` rather than a silent `continue`. The silence is half the bug: today an invisible lane produces no signal at all. A lane whose branch has no local `origin/` ref emits `gate-unknown: branch-not-fetched {slug}` for the same reason — that skip is silent today (`:829-831`) and becomes load-bearing once the corpus is non-empty.
- **Declined vs. errored rungs.** A rung that cleanly finds nothing is a *decline* and falls through normally. A rung that **raises** (Redis outage) is an *error*: the ladder still falls through so steer/resume can proceed, but the resolution is marked degraded, the **create rung is suppressed**, and a `gate-unknown: ledger-degraded {slug}` finding is emitted. The reason: create is the only action that calls `adopt_lane_slug`, whose write is no-overwrite and therefore uncorrectable. Steering the wrong session is recoverable; writing the wrong permanent identity is not.
- **Per-tick target dedupe**: the primary burst guard. `_pick_steer_target` queries project-wide (`:544-596`) with same-lane only a ranking preference, so several newly-visible lanes with no same-lane session all resolve to the *same* eng session. Each dispatched target `session_id` is recorded for the tick; a later lane resolving to an already-used target defers with an `action-cap:` finding. A count cap alone does not prevent one session being told to work three different issues in one tick — the rival-incarnation shape.
- **Per-tick action cap**: `_DEFAULT_ACTIONS_MAX_PER_TICK` with a `SDLC_STALL_ACTIONS_MAX_PER_TICK` env override, following the module's existing provisional-constant convention (`:120-136`) and carrying the same grain-of-salt comment. Secondary to the target dedupe. It bounds steer + resume + create per project tick — the machine-wide first-tick ceiling is cap × owned projects, matching how `creates_this_tick` already scopes. The existing create brake stays as-is beneath it. **The cap is tested before `_action_cooldown_set` at `:886`**, not after: the cooldown is a one-hour SETNX claim, and a lane deferred after the claim would wait an hour, not a tick — making the `deferred to next tick` finding text false. Checking before the claim is why the cap needs no `_action_cooldown_release` counterpart.
- **`target_repo is None` handling**: both read-based rungs are unavailable — an unscoped ledger query could bind a lane to another repo's issue, and a closing reference cannot be repo-matched without a repo to match against. The resolver skips both and continues at the deriving rung. The now-stale comment at `:762-770`, which justifies the `None` case as unreachable *because the branch filter admits only issue-derived names*, is rewritten to state the new reason.

### Flow

**Reflection tick** → one repo-scoped ledger enumeration → `{pr_number: issue_number}` map → fetch open `session/*` PRs (one `gh` call, explicit `--limit`) → **per PR**: strip `session/` for the slug → staleness gate (`_last_commit`) → **resolver ladder** (ledger by PR → PR closing ref → slug shape) → *resolved* → remaining gates (issue-open, live, escalated, budget) → cap + target-dedupe check → cooldown claim → **action ladder** (steer / resume / create) → counts + findings
    ↘ *no local ref* → `gate-unknown: branch-not-fetched` → next PR
    ↘ *unresolved / ambiguous* → `gate-unknown` finding → next PR
    ↘ *degraded (a rung errored)* → `gate-unknown: ledger-degraded`, create rung suppressed

### Technical Approach

Corrected line references (post-`e50eba258`):

- Delete `_SDLC_BRANCH_RE` at **`:118`** along with its comment block at `:116-117`.
- Rewrite `_list_open_sdlc_prs` (**`:217-236`**) as `_list_open_lane_prs`: add `closingIssuesReferences` (including each reference's **repository**, not just its `number`) to the `--json` field list, pass an explicit `--limit` (the call currently relies on `gh`'s default of 30, which was harmless when the regex discarded almost everything and is now a silent truncation of the entire discovery surface), and replace the regex predicate at `:235` with a `session/` namespace test expressed through the existing `_slug_from_branch` helper. The membership test is a **non-empty** return — `_slug_from_branch("session/")` returns `""`, which is non-`None` but falsy, and admitting it would format every downstream Redis key with an empty slug so two such lanes would share an escalation key. Do not introduce a second place that knows the prefix.
- Tighten `_slug_from_branch` (**`:256-260`**) to return `None` on a blank remainder, mirroring `tools/lane_identity.py::_nonempty` (`:106-115`), and rewrite its docstring — it currently reads "Return 'sdlc-<N>' for 'session/sdlc-<N>', else None", which after this change is the most misleading line in the file. It remains the module's single owner of the prefix.
- Demote `_issue_number_from_slug` (**`:263-266`**) to the ladder's last rung; keep the body, replace the docstring with one that says it is the last resort and why. **Paraphrase** what it replaced ("the old branch-shape filter") — do not name the deleted constant, or the anti-criterion greps in the Verification table will trip on this very docstring.
- Add `_resolve_lane_issue(pr, slug, target_repo, ledger_map)` next to it, with a docstring modeled on `merge_predicate._resolve_tracked_issue`: the rung order, the unique-match rule, the repo-match rule on the closing reference, the ambiguity outcome, the declined-vs-errored distinction, and the reason the deriving rung is last.
- Hoist a single repo-scoped `PipelineLedger` enumeration to the top of `_check_project_stalls`, building `{pr_number: issue_number}` for `target_repo` (skipping records whose `issue_number` is `None`, matching `merge_predicate.py:349-351`). Import `PipelineLedger` **lazily inside the function under a broad `except Exception`**, matching `reflections/sdlc_upvote_lanes.py:315` and `merge_predicate`'s Guard 1 — a module-level import lets a Popoto client-init failure break module load for the whole reflection.
- Move the staleness gate (`_last_commit`) ahead of identity resolution in the discovery loop, and give its `commit is None` skip (**`:829-831`**) a `gate-unknown: branch-not-fetched {slug}` finding instead of a silent `continue`.
- Rewrite the discovery loop at **`:811-819`** to call the corpus function and the resolver, and to append findings on the unresolved / ambiguous / degraded paths instead of a bare `continue`.
- Guard every ledger read with a broad `except Exception` that logs at `warning` with the rung name and slug, matching `merge_predicate`'s Guard 3 and `sdlc_upvote_lanes._ledger_has_recorded_stage`. A Redis outage must degrade this reflection, never crash it — and a *degraded* resolution suppresses the create rung (see Key Elements).
- Rewrite the stale comment at **`:762-770`**.
- Update the module docstring, which names the old filter twice and load-bearingly (**`:5`**, "inspects open SDLC PRs (`session/sdlc-<N>`)", and **`:10-11`**, gate "1-4 branch shape"), and add `SDLC_STALL_ACTIONS_MAX_PER_TICK` to the Configuration block at **`:53-63`**. These are assertions, not conditionals.
- Update the summary string at **`:948-952`** ("N SDLC PR(s) inspected"): `len(prs)` now counts all session lanes including unresolvable ones, and this is the line a human reads in the reflection report.

Deliberately **not** doing a full `PipelineLedger` enumeration keyed by slug-to-branch-set, as the issue's Desired Outcome sketched. spike-1 shows that design resolves 1 of the live lanes; the ladder resolves all of them at lower cost (the closing-reference rung is free — the data arrives in the corpus call). See Key Elements for why ledger-by-recorded-slug is cut entirely rather than kept as a maturing rung.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Every new ledger read is wrapped in a broad `except Exception` that logs at `warning` and returns "no answer from this rung". Each gets a test that injects a raising `PipelineLedger.query` and asserts (a) the tick does not raise, and (b) the resolver falls through to the next rung.
- [ ] The existing `_run_gh` handlers (`FileNotFoundError`, `TimeoutExpired`, broad) are unchanged and already covered by `test_gh_pr_list_filenotfound_returns_empty`; that test is retargeted to the renamed function.

### Empty/Invalid Input Handling
- [ ] `closingIssuesReferences` absent, `None`, or `[]` → the closing-reference rung yields nothing and the ladder continues to the deriving rung. `gh` omits the key entirely on some payload shapes, so `pr.get(...) or []` is the required idiom. **Parametrized into one test** — three tests for one idiom is over-testing.
- [ ] `headRefName` empty or `None` → `_slug_from_branch` returns `None` → PR excluded from the corpus. Tested.
- [ ] A `session/` branch with an empty remainder (`"session/"`) → slug is `""` → excluded from the corpus, not passed to the resolver with an empty identity.
- [ ] `target_repo is None` → both read-based rungs skipped without an unscoped query. Tested by asserting the ledger query is never invoked and that a closing reference is not trusted.

### Error State Rendering
- [ ] Unresolved and ambiguous lanes surface as `gate-unknown: issue-unresolved {slug}` / `gate-unknown: issue-ambiguous {slug}` in the returned `findings`, and are asserted in tests. This is the user-visible failure path: the reflection's finding list is what a human reads.
- [ ] The per-tick cap emits a distinct `action-cap: {slug} deferred to next tick` finding rather than silently dropping the lane, mirroring the existing `create-brake:` finding at `:897`.

## Test Impact

- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_gh_pr_list_filters_non_sdlc_branches` (`:1266-1276`) — REPLACE: it asserts `session/some-feature` is *excluded*, which is precisely the bug. Rewrite as `test_lane_pr_list_admits_the_session_namespace`: `session/sdlc-1395` and `session/some-feature` both admitted, `dependabot/update` excluded, draft excluded.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_gh_pr_list_filenotfound_returns_empty` (`:1255-1259`) — UPDATE: retarget to `_list_open_lane_prs`; assertion is unchanged.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — UPDATE: every `monkeypatch.setattr(sdlc_progress, "_list_open_sdlc_prs", ...)` site (`:311`, `:931`, `:971`, `:1124`, `:1134`, `:1219`, `:1225`, `:1233`, `:1243`, `:1301`, `:1309`) is retargeted to the new name, and the `_pr(...)` factory gains a `closing` parameter defaulting to the PR's own issue.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py::test_create_rung_records_a_human_named_slug_verbatim` (`:618-643`) — UPDATE the docstring only. It currently states the branch filter "is the only thing standing between a human-named lane and a correctly-named session"; after this change that sentence is false and the test's justification for exercising `_attempt_action` directly no longer holds. The assertions stay.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py:579-590` — UPDATE: the comment block describing the shape filter as the boundary goes stale.
- [ ] `tests/integration/test_sdlc_stall_auto_resume_e2e.py:78-82` — UPDATE: the `stalled_lane` fixture monkeypatches `_list_open_sdlc_prs` with a 3-key dict; retarget the name and add `closingIssuesReferences`. Consider flipping `_BRANCH` to a human-named branch so the e2e path proves the fix end to end.
- [ ] `tests/unit/reflections/test_sdlc_progress_check.py` — ADD: a resolver suite covering all three rungs, the two ambiguity cases (spike-3's real #2746 shape: two closing refs; and two ledger records for one `pr_number`), the `target_repo is None` path, and each rung's exception fall-through.

**Redis mechanism (mandatory).** This file is 100% `fake_redis` / `monkeypatch` today and references `PipelineLedger` nowhere. The new resolver tests **monkeypatch `PipelineLedger.query`**, consistent with the file's existing `fake_query` fixture (`:247`); no real Redis and no Popoto bring-up. Post-#2683 (test-DB ownership) a unit test that quietly claims a DB is exactly the rotation class that issue closed.

**Red-test-first (mandatory).** Before any implementation, write the headline regression test — a stalled lane on a human-named branch is discovered and acted on — against the *current* module and watch it fail. A passing suite written after the fix never proves a rewritten predicate does what you think it does.

Additional required tests, all of them negative cases the first draft missed:

- [ ] **Cap defers without burning the cooldown.** Assert a capped lane leaves no cooldown key claimed. This is the single test that catches the likely implementation error (checking the cap after `_action_cooldown_set` instead of before).
- [ ] **Same-target collision.** Two stalled lanes in one tick must not both dispatch to the same `session_id`.
- [ ] **Cross-repo closing reference** is rejected by the repo-match rule and does not resolve.
- [ ] **Errored rung vs. declined rung.** Assert that when the ledger rung *raises*, steer/resume may proceed but the **create rung is suppressed** and `gate-unknown: ledger-degraded` is emitted. An exception test that only asserts "does not raise" proves the guard exists, not that it declines correctly.
- [ ] **Escalation volume.** With `SDLC_STALL_RESUME_ENABLED=false` over a widened corpus, assert the `_send_alert` call count — this is the Risk 4 behavior and it should be pinned, not discovered in production.
- [ ] **Rung disagreement.** The ledger says issue A, the PR body says issue B: assert A wins. This is the entire justification for the rung ordering, and the first draft tested every rung *except* the case that motivates the order.
- [ ] **`branch-not-fetched`** finding is emitted when `_last_commit` returns `None`.

No `xfail` markers relate to this bug — grep of `tests/` for `pytest.mark.xfail` / `pytest.xfail(` returns nothing tied to lane discovery, so there is no expected-failure conversion to do.

## Rabbit Holes

- **Backfilling `PipelineLedger` for historical lanes.** Tempting ("then rung 1 always works") and a trap: it means inferring identity for lanes that predate the field, which is the exact derivation-wearing-adoption's-clothes mistake `tools/lane_identity.py:36-42` was written to prevent. The ladder makes backfill unnecessary; the ledger densifies naturally as new lanes start.
- **Making `pr_number` a second writer.** Discovery knows the PR number and could "helpfully" write it onto the ledger. Do not. `pr_number` is single-writer by design (`sdlc-tool meta-set`), and a reflection that writes identity during a read path is how #2718 happened.
- **Reconciling the `session/` namespace with `.worktrees/` on disk.** A machine-local worktree listing would seem to enrich discovery. `lane_identity.py:43-46` already rejects this rung: a per-host answer is not an identity.
- **Generalizing the resolver into a shared `tools/` helper for all three consumers.** `merge_predicate`, `sdlc_upvote_lanes`, and this module each resolve a slightly different question with different failure postures (fail-closed merge gate vs. fail-soft reflection). Unifying them is a separate design exercise; the shared *pattern* is enough here. **The critique pushed back on this** (see No-Gos): the ledger-by-PR rung is close to a line-for-line restatement of `merge_predicate._resolve_tracked_issue`, and this repo forbids parallel implementations. The deferral is a scope fence, not a disagreement — it is recorded as a follow-up rather than waved away.
- **Tuning the stall threshold because the corpus just grew.** The threshold is orthogonal. If the widened corpus proves noisy, the per-tick cap is the lever, not the threshold.

## Risks

### Risk 1: First-tick burst — and, more sharply, first-tick *collision on one session*
**Impact:** the corpus goes from zero lanes to the full live population in one tick. The count risk (several lanes steered at once) is the obvious one. The sharper risk is that `_pick_steer_target` is **project-wide** (`:544-596`) with same-lane only a ranking preference, so several newly-visible lanes with no same-lane session all resolve to the *same* eng session — which is then steered or resumed repeatedly in one tick with different per-issue instructions. That is the rival-incarnation shape, and a count cap does not prevent it.
**Mitigation:** the **per-tick target dedupe** is the primary guard — one dispatch per `session_id` per tick, later lanes defer with an `action-cap:` finding. The per-tick action cap (`SDLC_STALL_ACTIONS_MAX_PER_TICK`, provisional default 3) is the secondary bound, checked *before* the cooldown claim so a deferred lane genuinely waits one tick. The create brake (1/tick), the per-`(slug, sha)` attempt budget, and the cooldown are unchanged beneath both. Deferred lanes are reported, not silently dropped.

### Risk 2: The closing-reference rung binds a lane to the wrong issue
**Impact:** `closingIssuesReferences` is authored by whoever wrote the PR body. A wrong, stale, or **cross-repo** `Closes #N` binds the attempt budget, cooldown, escalation key, and any created session to an unrelated issue. The cross-repo case is the dangerous one: GitHub permits `Closes owner/repo#N`, a single such reference passes the uniqueness test, and `_issue_is_open` would then resolve that bare number against the *project's* repo — very likely hitting a real but unrelated issue. The create rung's `adopt_lane_slug` write is no-overwrite, so a wrong bind mints a phantom ledger record with a permanent, uncorrectable identity.
**Mitigation:** the rung sits *below* the recorded-ledger rung, so it only answers where recorded identity is silent. It requires a single distinct reference (spike-3 proved multi-ref PRs are real), **and that reference's repository must equal `target_repo`** — so the rung is skipped entirely when `target_repo is None`. The corpus is restricted to the `session/` namespace, so a hotfix branch's closing reference never reaches it. Downstream gates are unchanged: a wrong issue that is closed, or whose lock says live, still results in no action.

### Risk 3: A ledger query *failure* silently degrades into a derived answer
**Impact:** a Redis outage makes the ledger rung raise. Falling through to the closing-reference and slug-shape rungs keeps discovery working, which is right for *reading* — but it is wrong for *writing*. A lane whose branch is `session/sdlc-2628` but whose ledger records a different issue (the takeover shape `PipelineLedger`'s docstring supports) would resolve to 2628 from the branch string alone during a brownout and then permanently write that identity. The plan refuses to fall through on ambiguity; falling through on error has the same shape and needs the same discipline.
**Mitigation:** the resolver distinguishes **declined** (cleanly found nothing) from **errored** (raised). On error, steer and resume may still proceed, but the **create rung is suppressed** and a `gate-unknown: ledger-degraded {slug}` finding is emitted. Every rung's exception path logs at `warning` with the rung name and slug. The tick never crashes (matching `merge_predicate` Guard 3 and `sdlc_upvote_lanes._ledger_has_recorded_stage`), and the fall-through is designed behavior with tests, not an accident.

### Risk 4: The observation-mode reflex makes things worse
**Impact:** the intuitive safe deploy — run one cycle with `SDLC_STALL_RESUME_ENABLED=false` to watch the finding list before anything fires — is actively harmful. With resume disabled, every stalled non-live lane calls `_escalate` → `_send_alert` (`:873-875`), paging a human once per newly-visible lane on the first tick. Worse, `_escalation_set` is a SETNX with a 30-day TTL and the escalation check short-circuits *before* the action ladder (`:854-856`), so that "observation" cycle suppresses action on those `(slug, sha)` pairs for 30 days. The flag disarms the detector on exactly the lanes it was built to rescue.
**Mitigation:** do not use it as a deploy procedure; it remains the operator's escape hatch. The burst guards above are the deploy story. A genuine report-only mode would be a separate, explicitly non-escalating path and is out of scope here.

### Risk 5: The `session/` namespace stops being a clean lane boundary
**Impact:** spike-4 confirmed it is clean today (hotfix work lives on `fix/*`). If someone later pushes a non-lane `session/*` branch, it enters the corpus, and if it happens to carry a `Closes #N` it could draw an action.
**Mitigation:** accepted, and bounded by the same gates. The `session/` prefix is already the repo's declared lane namespace — `agent/worktree_manager.py` creates `session/{slug}` and `tools/lane_identity.py::lane_branch_name` is its single constructor. Documenting the namespace as load-bearing (in the Documentation section) is the durable mitigation.

## Race Conditions

### Race 1: Lane starts (or its slug is recorded) between the corpus fetch and the resolver
**Location:** `reflections/sdlc_progress.py`, discovery loop at `:811-819`
**Trigger:** `gh pr list` returns a snapshot; a lane can record its slug, take its issue lock, or push a fresh commit in the milliseconds after.
**Data prerequisite:** none — the resolver reads whatever identity exists at read time; a later write simply makes the *next* tick more authoritative.
**State prerequisite:** the lane must be genuinely stalled at action time, not merely at fetch time.
**Mitigation:** already handled downstream and unchanged: `_lane_is_live` is read after the fetch, and the create rung re-reads the lock immediately before creating (`:691-703`). This plan adds no new window.

### Race 2: Two ticks resolve the same lane concurrently
**Location:** discovery loop plus `_action_cooldown_set` (`:887`)
**Trigger:** overlapping project ticks at a 30-minute cadence.
**Data prerequisite:** the `(slug, sha)` cooldown key.
**State prerequisite:** exactly one tick may claim the action window.
**Mitigation:** unchanged — `_action_cooldown_set` is the SETNX claim and is the existing overlapping-tick guard. The resolver is pure-read and idempotent, so concurrent resolution of the same lane yields the same answer even if a concurrent tick's `adopt_lane_slug` lands mid-window: that write is conditional-on-empty and never overwrites, so a lane's identity is stable once recorded (`agent/pipeline_ledger.py:170-180`), and the ladder short-circuits at the first unique answer regardless. The per-tick cap, the target-dedupe set, and the ledger map are all per-call state, deliberately not in Redis, exactly like `creates_this_tick` (`:774-779`) — and carry the same documented non-atomicity. The ledger map is built once at the top of the tick, so a lane whose `pr_number` is recorded mid-tick is simply picked up by the next tick.

(The first draft carried a third race about a recorded slug appearing while the ledger-by-slug rung was answering. It is gone with that rung.)

## No-Gos (Out of Scope)

Three of the issue's four acceptance-criteria bullets are addressed as written: `_issue_number_from_slug` demoted to a documented last-resort fallback, human-named lane detection (proven against the live corpus), and `_SDLC_BRANCH_RE` removed outright.

**AC-1 is amended, not satisfied.** The issue asked for ledger-driven *enumeration*. spike-1 measured that design resolving 1 of the 5 live lanes, because the ledger only carries identity for lanes started after #2792. This plan substitutes a targeted ledger lookup inside a resolution ladder, which resolves all of them. This is a deliberate amendment on evidence, recorded here so the validator has an unambiguous target rather than reading AC-1 as unmet and either failing it or rubber-stamping it.

**Deferred to a follow-up issue:** extracting the tri-state ledger-by-PR-number lookup into `tools/lane_identity.py` as the read-direction sibling of `resolve_lane_slug`, and having `merge_predicate._resolve_tracked_issue` delegate to it. This is the right end state under the repo's no-parallel-implementations doctrine — the rung here is close to a line-for-line restatement of that function — but `tools/merge_predicate.py` and `tools/lane_identity.py` are outside this lane's fence, and a fail-closed merge gate is not something to refactor as a rider on a reflection fix. File the follow-up when this merges.

## Update System

No update system changes required — this is a change to one reflection module's internal logic. No new dependency, no new config file, no new secret, no schema change, and therefore no migration. `scripts/update/migrations.py` is not touched: `PipelineLedger` gains no field (`slug` and `pr_number` both already exist and are already populated by their existing writers).

The new `SDLC_STALL_ACTIONS_MAX_PER_TICK` env var is an *optional* override with a code default, matching every other threshold in the module (`:120-136`). It needs no `.env` entry, no `.env.example` placeholder, and no `config/settings.py` field — it follows the module's established `_env_float` convention, not the secrets convention.

Standard post-merge propagation applies: `/update` on each machine, then a worker restart so the reflection scheduler picks up the new module.

## Agent Integration

No agent integration required — this is a reflection-internal change. `sdlc-progress-check` is already registered and scheduled; the agent reaches it through the existing reflection runner, not through a CLI entry point or a bridge import. No new `pyproject.toml [project.scripts]` entry, no MCP surface, no `bridge/telegram_bridge.py` change.

The one agent-visible surface is unchanged in shape and improved in content: escalations still reach a human via `_send_alert`, and the findings list still flows into the reflection report. After this change that report will, for the first time, mention human-named lanes.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-lane-identity.md` — add a "Discovery reads identity" section covering the four-rung resolver, why rung 4 is last, and why a full-ledger enumeration was rejected (spike-1's 1-of-5 measurement). This doc is #2735's and is the natural home; the read direction belongs next to the write direction.
- [ ] Update the stall-detector documentation to state that the `session/` namespace — not the `sdlc-<N>` shape — is the lane boundary, and to document `SDLC_STALL_ACTIONS_MAX_PER_TICK` alongside the existing thresholds. (Locate via `grep -rln "sdlc-progress-check\|SDLC_STALL_THRESHOLD_HOURS" docs/`; if no dedicated doc exists, create `docs/features/sdlc-stall-detection.md` and add it to the `docs/features/README.md` index table.)
- [ ] Add an entry to `docs/features/README.md` if a new doc is created.

### Inline Documentation
- [ ] `_resolve_lane_issue` docstring: the rung order, the unique-match rule, the ambiguity outcome, and why rung 4 (the only deriving rung) is last.
- [ ] `_issue_number_from_slug` docstring: rewrite to say it is the last-resort fallback and what supersedes it.
- [ ] Rewrite the stale `target_repo` comment at `:762-770`, whose stated justification ("the branch filter admits only issue-derived names") this change invalidates.
- [ ] Grain-of-salt comment on `_DEFAULT_ACTIONS_MAX_PER_TICK`, matching the module's existing provisional-constant block.

## Success Criteria

- [ ] A stalled lane on a human-named branch (`session/dev-<hash>`, `session/<words>`) is discovered and acted on, proven by a test using spike-4's real branch names.
- [ ] `_SDLC_BRANCH_RE` no longer exists anywhere in `reflections/sdlc_progress.py`.
- [ ] `_issue_number_from_slug` is reached only after all three read-based rungs decline, proven by a test that asserts it is not consulted when the ledger answers.
- [ ] A PR with two closing references (the #2746 shape) produces a `gate-unknown: issue-ambiguous` finding and **no** action.
- [ ] A ledger/Redis failure degrades to rungs 3-4 with a logged warning and never raises out of `_check_project_stalls`.
- [ ] The per-tick action cap bounds steer + resume + create, with deferred lanes reported as findings.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (discovery)**
  - Name: `discovery-builder`
  - Role: rewrite the corpus filter and implement the four-rung resolver in `reflections/sdlc_progress.py`
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (tests)**
  - Name: `discovery-test-builder`
  - Role: retarget the existing test surface and add the resolver suite
  - Agent Type: test-engineer
  - Resume: true

- **Validator (discovery)**
  - Name: `discovery-validator`
  - Role: verify the acceptance criteria, especially the negative ones (regex gone, rung 4 not consulted when the ledger answers, ambiguity produces no action)
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `discovery-documentarian`
  - Role: feature docs and index entry
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Per the template's Tier 1 list. The discovery builder carries a `Domain: Redis/Popoto data` tag — paste the matching rules from `DOMAIN_FRAMING.md` into its assignment, particularly the never-raw-Redis rule (all `PipelineLedger` access goes through `Model.query.filter()` / `Model.load()`).

## Step by Step Tasks

### 0. Run spike-5 and the red regression test
- **Task ID**: build-red
- **Depends On**: none
- **Validates**: `tests/unit/reflections/test_sdlc_progress_check.py`
- **Informed By**: spike-5 (nothing has verified the worker's checkout can resolve human-named lane branches, and that gate now decides whether the fix delivers anything)
- **Assigned To**: `discovery-builder`
- **Agent Type**: builder
- **Parallel**: false
- Run spike-5 (`git log -1 --format=%ct origin/session/<human-named-branch>` per open lane branch, in the worker's `ai` checkout) and record the result in the Spike Results section. If refs are missing, stop and report — the design needs a fetch step.
- Write the headline regression test — a stalled lane on a human-named branch is discovered and acted on — and **watch it fail** against the current module. Record the failure. A passing suite written after the fix proves nothing about a rewritten predicate.

### 1. Widen the corpus filter and implement the issue resolution ladder
- **Task ID**: build-discovery
- **Depends On**: build-red
- **Validates**: `tests/unit/reflections/test_sdlc_progress_check.py`
- **Informed By**: spike-1 (a ledger-only read resolves almost nothing — a ladder is required), spike-2 (`closingIssuesReferences` arrives free in the corpus call), spike-3 (multi-ref PRs are real; #2746 declares two)
- **Assigned To**: `discovery-builder`
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: false
- Delete `_SDLC_BRANCH_RE` and its comment block (`:116-118`).
- Rename `_list_open_sdlc_prs` → `_list_open_lane_prs`; add `closingIssuesReferences` **including each reference's repository** to the `--json` field list; pass an explicit `--limit`.
- Express the namespace test through `_slug_from_branch` returning a **non-empty** slug, and tighten that helper to return `None` on a blank remainder. Do not add a second site that knows the `session/` prefix.
- Hoist one repo-scoped `PipelineLedger` enumeration to the top of `_check_project_stalls` into a `{pr_number: issue_number}` map, skipping records with a `None` `issue_number`. Import `PipelineLedger` lazily inside the function under a broad `except Exception`.
- Add `_resolve_lane_issue(pr, slug, target_repo, ledger_map) -> tuple[int | None, str]` implementing the three rungs in order, modeled on `tools/merge_predicate.py::_resolve_tracked_issue` (`:355-440`).
- The closing-reference rung requires a single distinct reference **whose repository equals `target_repo`**. Skip both the ledger rung and the closing-reference rung when `target_repo` is `None` — never issue an unscoped query, never trust an unmatchable reference.
- Two-or-more candidates at any rung returns `(None, "ambiguous")` and does not fall through.
- Distinguish **declined** from **errored**: an errored rung logs at `warning` with rung name and slug, marks the resolution degraded, suppresses the create rung downstream, and emits `gate-unknown: ledger-degraded {slug}`.
- Demote `_issue_number_from_slug` to the last rung and rewrite its docstring — paraphrase what it replaced, never name the removed identifiers.
- Move the staleness gate ahead of resolution; give the `commit is None` skip a `gate-unknown: branch-not-fetched {slug}` finding.
- Rewrite the discovery loop (`:811-819`) to use the corpus function and the resolver, appending findings on the unresolved / ambiguous / degraded paths instead of a bare `continue`.
- Rewrite the stale `target_repo` comment at `:762-770`, the module docstring (`:5`, `:10-11`), the Configuration block (`:53-63`), and the summary string (`:948-952`).

### 2. Add the per-tick target dedupe and action cap
- **Task ID**: build-brakes
- **Depends On**: build-discovery
- **Validates**: `tests/unit/reflections/test_sdlc_progress_check.py`
- **Informed By**: the critique's finding that `_pick_steer_target` is project-wide (`:544-596`), so a count cap alone lets one session be told to work several issues in one tick
- **Assigned To**: `discovery-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add the per-tick **target dedupe**: record each dispatched `session_id`; a later lane resolving to a used target defers with an `action-cap:` finding. This is the primary guard.
- Add `_DEFAULT_ACTIONS_MAX_PER_TICK` to the thresholds block with the module's grain-of-salt comment, plus `_actions_max_per_tick()` reading `SDLC_STALL_ACTIONS_MAX_PER_TICK` via `_env_float`.
- Count steer + resume + create against the cap, emitting `action-cap: {slug} deferred to next tick`. **Check both the dedupe and the cap before `_action_cooldown_set` at `:886`** — deferring after the claim would make the lane wait an hour rather than a tick, and would make the finding text false.
- Leave the existing create brake in place beneath the cap.

### 3. Complete the test surface
- **Task ID**: build-tests
- **Depends On**: build-brakes
- **Validates**: `tests/unit/reflections/test_sdlc_progress_check.py`, `tests/integration/test_sdlc_stall_auto_resume_e2e.py`
- **Informed By**: spike-3 (use #2746's real two-ref shape)
- **Assigned To**: `discovery-builder`
- **Agent Type**: builder
- **Parallel**: false
- Apply every disposition and every added test in the Test Impact section, including all eight negative cases.
- Monkeypatch `PipelineLedger.query`; no real Redis, no Popoto bring-up.
- Flip the e2e fixture's branch to a human-named one — **mandatory**, not "consider": it is the single highest-value test here.

### 4. Validate
- **Task ID**: validate-discovery
- **Depends On**: build-tests
- **Assigned To**: `discovery-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run every Verification row.
- Confirm each negative criterion independently: regex absent, the deriving rung not consulted when the ledger answers, ambiguity produces no action, cross-repo reference rejected, create suppressed on a degraded resolution, no cooldown burned by a capped lane, no unscoped ledger query, ledger enumerated once per tick.
- **Demonstrate the raw-Redis row goes red** against a deliberately-introduced violation, then revert it. A gate never shown to fail has not been verified.
- Confirm no raw Redis access was introduced.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-discovery
- **Assigned To**: `discovery-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Apply every checkbox in the Documentation section. Update the two existing docs; create no new feature doc.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: `discovery-validator`
- **Agent Type**: validator
- **Parallel**: false
- Re-run all Verification rows and confirm all Success Criteria.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Unit tests pass | `scripts/pytest-clean.sh tests/unit/reflections/test_sdlc_progress_check.py -q` | exit code 0 |
| Integration e2e passes | `scripts/pytest-clean.sh tests/integration/test_sdlc_stall_auto_resume_e2e.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Branch-shape regex is gone | `grep -c '_SDLC_BRANCH_RE' reflections/sdlc_progress.py` | match count == 0 |
| Old corpus function name is gone | `grep -rc '_list_open_sdlc_prs' reflections/ tests/` | match count == 0 |
| Resolver exists | `grep -c '_resolve_lane_issue' reflections/sdlc_progress.py` | output > 1 |
| Ledger is read by discovery | `grep -c 'PipelineLedger' reflections/sdlc_progress.py` | output > 0 |
| Closing-reference rung is wired | `grep -c 'closingIssuesReferences' reflections/sdlc_progress.py` | output > 1 |
| Per-tick cap is env-overridable | `grep -c 'SDLC_STALL_ACTIONS_MAX_PER_TICK' reflections/sdlc_progress.py` | output > 0 |
| Ambiguity is reported, not guessed | `grep -c 'issue-ambiguous' reflections/sdlc_progress.py` | output > 0 |
| No raw Redis on Popoto keys | `grep -nE '\.(hgetall\|hget\|scan_iter)\(' reflections/sdlc_progress.py` | match count == 0 |

## Critique Results

**Verdict: NEEDS REVISION** — war room 2026-08-14, FULL roster (Risk & Robustness, Scope & Value, History & Consistency), run `2755-1786628069756790000`.

The plan's direction is endorsed by all three critics: the problem is measured rather than asserted, rung 3 is a genuinely free adoption rung, and the ambiguity posture matches established precedent. The blockers below are corrections within the plan's existing shape, not a redesign.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk | The per-tick cap bounds action *count*, not *collision on one target*. `_pick_steer_target` queries project-wide (`:544-596`); same-lane is only a ranking preference. Several newly-visible lanes with no same-lane session all resolve to the SAME eng session, which is then steered/resumed 3x in one tick with different issue instructions — the rival-incarnation shape. | pending | Track dispatched `session_id`s per tick; one dispatch per target per tick. A second lane resolving to a used target defers. This is the primary guard; demote the count cap to a secondary bound. |
| BLOCKER | Risk, Scope | The cap will burn the hour-long cooldown on lanes it defers. `_action_cooldown_set` claims at `:887`, *before* rung selection; both existing defer paths (`:894`, `:898`) call `_action_cooldown_release`. Step 3 mirrors the `create-brake:` finding but not the release, so `action-cap: {slug} deferred to next tick` would be false — the lane waits an hour, not a tick. Verified against live code. | pending | The cap is kind-agnostic, so check it **before** `_action_cooldown_set` at `:886`. Add the "a deferred lane owes no cooldown" test. |
| BLOCKER | Risk | Escalation is uncapped, and Open Question 1's proposed "observation cycle" is the opposite of safe. With `SDLC_STALL_RESUME_ENABLED=false`, every stalled non-live lane calls `_escalate` → `_send_alert` (`:873-875`), paging a human per newly-visible lane; `_escalation_set` is SETNX with a 30-day TTL, so those keys then suppress action on those `(slug, sha)` pairs for 30 days. The suggested safe posture silently disarms the detector on exactly the lanes it exists to rescue. | pending | Retract Open Question 1's dry-run proposal and document why the flag is not a dry-run mode. If a report-only posture is wanted it is a separate mode, not this flag. |
| BLOCKER | Risk | Rung 3 never checks which *repo* a closing reference belongs to. GitHub permits cross-repo `Closes owner/repo#N`; a single cross-repo ref passes the uniqueness test, yields a bare integer, and `_issue_is_open` resolves it against the wrong repository. The create rung then calls `adopt_lane_slug`, whose write is no-overwrite — a wrong bind mints a phantom ledger record and a permanent identity that can never be corrected. | pending | Require the reference's repository to equal `target_repo`; skip rung 3 entirely when `target_repo is None`. Extract repository/url from the `--json` payload, not just `number`. |
| BLOCKER | Risk | The fail-open path from an *errored* ledger read down to rung 4 is undiscussed. The plan refuses to fall through on ambiguity but is silent on error, which has the same shape: rungs 1-2 raise during a Redis brownout, rung 4 derives a number from the branch string, and a takeover lane binds to the wrong issue — then writes it. | pending | Distinguish "rung declined" from "rung errored". On error let rungs 3-4 resolve for *reporting* but suppress the create rung (the only permanent write) and emit `gate-unknown: ledger-degraded {slug}`. |
| BLOCKER | History | Rung 2 is structurally anti-correlated with the population this plan serves. `docs/features/sdlc-lane-identity.md:110-121` states that for a human-named lane the minter records `sdlc-{N}`, and `_record_slug_if_empty` is no-overwrite — so `ledger.slug == branch slug` can never hold for exactly the lanes rung 2 is meant to catch. Where it does hold it is either the adopt case (rung 1 already answered) or `sdlc-N` (rung 4 restated, more expensively). Spike-1 supplies zero evidence of a rung-2 hit. Doc quote verified verbatim. | pending | Pick one: (a) spike the *equality* — compare the 2 `ai` ledger slugs against their branch names, not just slug density; (b) drop rung 2 and ship three rungs; (c) keep it but return the winning rung in `reason` for resolved lanes and tally per-rung in `counts` so its emptiness is falsifiable in production. |
| BLOCKER | History, Risk, Scope | The raw-Redis verification row cannot fail. Under `grep -E`, `\|` is a literal pipe, not alternation, so the pattern matches nothing ever — a vacuous gate on the CLAUDE.md never-raw-Redis rule the builder's Domain tag exists to enforce. | pending | Use `grep -nE '\.(hgetall\|hget\|scan_iter)\('` with real alternation (unescaped pipes inside `-E`). |
| BLOCKER | Scope | The headline 5/5 coverage claim counts a PR the corpus still excludes. The draft filter at `:234` is retained, and the plan's own table flags #2685 as draft. Real first-tick corpus is 4, not 5 — and Risk 1, the cap default, and spike-4's "0 → 5 lanes" are all sized on 5. | pending | Correct the number, or state that the draft filter is retained by design and one of the five is out of scope. Re-derive the cap default from the corrected count. |
| BLOCKER | Scope | Nothing verified that `_last_commit` can resolve these branches. It reads `origin/<branch>` in the worker's checkout and returns `None` on a missing remote-tracking ref, at which point the loop does a **silent** `continue` (`:829-831`). Under the old regex this path was unreachable; after widening it decides whether the fix delivers anything — and it would fail silently, the exact shape the plan opens by condemning. | pending | Add a spike or Verification row proving `origin/session/<human-name>` resolves in the worker's cwd. Consider a `gate-unknown: branch-not-fetched {slug}` finding on the `commit is None` skip. |
| CONCERN | History, Risk, Scope | Rungs 1-2 are unindexed full-keyspace scans, per PR, per tick, against a model with no TTL. `slug`/`target_repo` are plain `Field`s, so Popoto loads every key and filters client-side. "Repo-scoped" scopes nothing at the Redis layer. | pending | Hoist one repo-scoped enumeration to the top of `_check_project_stalls`; build `{pr_number: ...}` and `{slug: ...}` maps once and have rungs 1-2 read them. Also state in the plan that the scoping is a Python filter, so no later reader assumes Redis does it. |
| CONCERN | History | Rung 1 duplicates `merge_predicate._resolve_tracked_issue` steps 3-5 line for line while citing it as precedent — the third near-duplicate resolver, against the no-parallel-implementations doctrine. `tools/lane_identity.py` is the module #2792 created to own lane identity and is already imported here. | pending | Extract the tri-state ledger-by-PR lookup into `tools/lane_identity.py` as the read-direction sibling; have `merge_predicate` delegate. Keep each consumer's failure posture at its call site. Also rename the ladder ("issue resolution ladder") so it does not collide with `lane_identity`'s existing four-rung terminology. |
| CONCERN | History, Scope | The Documentation section invites creating `docs/features/sdlc-stall-detection.md`, but the detector is already documented at `docs/features/pm-session-liveness.md:263-336` — named by `reflections/sdlc_progress.py:65`. A documentarian reading "no *dedicated* doc" could mint a parallel doc. | pending | Name `pm-session-liveness.md` as the doc of record and delete the create-a-new-doc branch. It carries three statements this change falsifies: `:270` (gate 1 is branch shape), `:273` (`origin/session/sdlc-<N>`), `:336` ("Drafts and ad-hoc branches ... are intentionally excluded"). Add `SDLC_STALL_ACTIONS_MAX_PER_TICK` to its env table at `:322-328`. |
| CONCERN | History, Risk, Scope | Both zero-expecting grep rows will false-fail on the plan's own prose, and `grep -c`/`grep -rc` cannot express "0" as a pass. The plan mandates rewritten docstrings explaining what changed; naming the removed identifier trips the gate. `test_create_rung_records_a_human_named_slug_verbatim`'s docstring already quotes both names verbatim. | pending | Rewrite as `! grep -rq '<name>' reflections/ tests/`. Instruct the builder to paraphrase ("the old branch-shape filter") and never quote removed identifiers. |
| CONCERN | History | Technical Approach and Step 1 give contradictory membership tests, and one admits an empty slug. `_slug_from_branch("session/")` returns `""` — non-`None` but falsy. A branch named `session/` would enter the corpus with empty identity and format every downstream Redis key with it; two such lanes would share an escalation key. | pending | Make Technical Approach say **non-empty**, matching Step 1 and the Empty/Invalid Input section. Consider tightening `_slug_from_branch` to return `None` on a blank remainder, mirroring `lane_identity._nonempty`. |
| CONCERN | History | The new resolver tests have no stated Redis mechanism. The test file is 100% `fake_redis`/`monkeypatch` today and never references `PipelineLedger`. Post-#2683 (test-DB ownership) a unit test that quietly brings up Popoto against a claimed DB is the rotation class that issue closed. | pending | State explicitly: monkeypatch `PipelineLedger.query`, consistent with the file's existing `fake_query` fixture. No real Redis. |
| CONCERN | History | Resolution runs before the staleness gate, so every fresh healthy lane pays full resolution (ledger scan + `gh issue view`) every 30 minutes. `_last_commit` needs only the branch. | pending | Move the age check ahead of identity resolution. This also bounds the `gate-unknown: issue-unresolved` repetition — unlike every existing `gate-unknown`, unresolvable identity is a *stable* condition that would otherwise emit a finding forever. |
| CONCERN | History | The "Current behavior" evidence table has already decayed: #2695 and #2683 both merged overnight (`0f070970b`, `fb00b8542`). The live corpus is no longer 5, which is the sole justification for the cap's default. | pending | Re-measure, or restate the cap's justification as structural rather than as a count that expires in a day. Note that `session/dev-41a59eee` in Step 4 is now a synthetic fixture string, not a live lane. |
| CONCERN | Scope | "Nothing deferred — every relevant item is in scope" over-claims AC-1. The issue's first acceptance bullet is ledger-driven *enumeration*; the plan does targeted lookups and argues (correctly, on evidence) that enumeration resolves 1 of 5. That is an amendment to an AC, not satisfaction of one. | pending | Record it as "AC-1 amended by spike-1" with the measurement, so the validator has an unambiguous target instead of failing it or rubber-stamping it. |
| CONCERN | Risk | Rung 1 is described as unconditionally most-authoritative, but `pr_number` is never cleared, so a stale-but-repo-matching value yields a *unique wrong* answer no downstream gate catches. | pending | Require rung 1 to ignore records whose `issue_number` is `None` (matching `merge_predicate:349-351`) and state the stale-`pr_number` caveat. |
| CONCERN | Scope | The cap is per-*project*, inheriting `creates_this_tick`'s scope, so the real first-tick ceiling is cap × owned projects. Consistent with precedent but Risk 1 presents the cap as bounding "the" burst. | pending | Say which burst it bounds. |
| CONCERN | Risk, Scope | Test strategy misses the negative cases that matter most: cooldown release on the cap path, same-target collision, cross-repo closing reference, errored-rung vs declined-rung (create suppressed), `_send_alert` count under a widened corpus, and rung-1-vs-rung-3 *disagreement* (which is the entire justification for the ordering). The e2e branch flip is hedged as "Consider" when it is the highest-value test in the plan. | pending | Add all six; make the e2e human-named branch flip mandatory. Per the demonstrated-red principle, an exception test asserting only "does not raise" proves the guard exists, not that it declines correctly. |
| CONCERN | Scope | Seven tasks and four agents is over-staffed for one module, one test file, one fixture, one doc section. Tasks 1-3 are the same agent in the same function neighborhood; the separate test-builder costs a full context handoff for a file the builder is already reading line by line. | pending | Collapse tasks 1+2, drop to three agents, and add an explicit red-test-first step: write the human-named-lane test against the current module, watch it fail, then build. |
| CONCERN | Scope | Over-tested: the three-shape `closingIssuesReferences` matrix is three tests for one `pr.get(...) or []` idiom, and the empty-remainder case is already covered by the existing falsy-slug `continue`. | pending | Parametrize to one; one assertion for the empty remainder. |
| NIT | History, Risk, Scope | Stale docstrings and catalogs the task list marks conditional or misses entirely: `_slug_from_branch`'s docstring (`:257`) becomes the most misleading line in the file; the module docstring names the old filter at `:5` and `:10-11`; the Configuration block at `:53-63` needs the new env var; the summary string at `:948-952` still says "SDLC PR(s) inspected". | pending | Make these assertions, not conditionals ("update ... if it names the old filter" — it does). |
| NIT | History | Specify a lazy, exception-guarded `PipelineLedger` import inside the function, matching both siblings. A module-level import lets a Popoto client-init failure break module load for the whole reflection. | pending | Follow `sdlc_upvote_lanes:315` and `merge_predicate`'s Guard 1. |
| NIT | Risk, History | Smaller items: `_slug_from_branch` admits `session/sdlc-2755/fixup`; `gh pr list` defaults to `--limit 30` and the corpus call passes no limit, which now truncates the entire discovery surface; `grep -c` counts lines not occurrences, making the `> 1` rows fragile; Race 2 and Race 3 overlap and should merge; baseline SHA `34ab8da2f` is not main-reachable; `sdlc_upvote_lanes.py:317` is actually `:315`. | pending | Address inline during revision. |

### Open Questions — war room answers

1. **Cap default.** Keep the cap at a provisional default of 3, and do **not** run a `SDLC_STALL_RESUME_ENABLED=false` observation cycle — Risk showed that flag escalates per lane and sets 30-day suppression keys, so it disarms the detector rather than observing it. It stays the operator's escape hatch, not the deploy procedure. Note the count-cap is secondary to the per-tick target dedupe (BLOCKER 1).
2. **Rung 3 trust level.** Do not env-gate it. Rung 3 is the only rung with non-zero measured coverage; defaulted-off it ships a fix that fixes nothing, defaulted-on it is dead config. Its three existing controls (below both ledger rungs, single distinct reference, `session/`-only corpus) are the right ones — **plus** the repo-match requirement from BLOCKER 4, which is not optional.
3. **`session/` namespace as contract.** Document it in `docs/features/sdlc-lane-identity.md` as a *description* of the existing single constructor (`lane_branch_name`; `worktree_manager` creates `session/{slug}`) and a note that automated stall action now keys on it. Do not write it as a new prohibition on humans and do not add an enforcing hook — that is a separate issue.

---

## Open Questions

1. **Per-tick action cap default.** Provisional default of 3 (steer + resume + create combined), env-overridable. Given spike-4's measurement that the first tick after deploy will see roughly 5 previously-invisible lanes at once, is 3 the right first number, or should the first deploy run with `SDLC_STALL_RESUME_ENABLED=false` for one cycle to observe the finding list before any action fires?
2. **Rung 3 trust level.** `closingIssuesReferences` is human-authored and is what covers 4 of the 5 live lanes today. It sits below both recorded-ledger rungs and requires a single distinct reference. Is that acceptable, or should rung 3 be gated behind an env flag until the ledger population matures enough for rungs 1-2 to dominate?
3. **`session/` namespace as a documented contract.** This change makes the `session/` prefix load-bearing for automated action. Worth stating explicitly in `docs/features/sdlc-lane-identity.md` as "the `session/` namespace is reserved for SDLC lanes; do not push ad-hoc branches there" — or is that over-formalizing a convention that already holds?
