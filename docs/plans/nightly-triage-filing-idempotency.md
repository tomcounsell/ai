---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-05
tracking: https://github.com/tomcounsell/ai/issues/3170
last_comment_id:
---

# Nightly Triage Filing Idempotency

## Problem


On 2026-08-24 one nightly triage dispatch filed the *same* failing node three times — #2960 (20:35:17), #2972 (20:40:13), #2990 (20:45:57) — with three different body templates, from the same worktree at the same commit `fd848ed83`. Across the whole wave that was 39 issues (#2960–#2999), later closed wholesale as duplicates of #3001.

Every one of those three filings ran the prompt's instruction to "search ALL issues — open AND closed — for the EXACT title" and every one of them came up empty, because a search-index read lags issue creation by minutes and each wave searched inside the lag window its own predecessor had opened issues in. The turn was replayed (why, is #3161's question); the filing step had no defense of its own.

**Current behavior:**

The detector has genuinely good idempotency for its *own* decisions. `dispatch_findings` reads live REST state via `open_issues()` and `closed_issue_dispositions()`, partitions nodes into already-open / closed-not-planned / to-file, and comments rather than re-files. Then it throws the answer away: `maybe_dispatch_triage_session(single_nodes, dry_run=dry_run)` hands the triage agent a bare `list[str]` of node ids, and `_build_triage_prompt` tells that agent to go re-derive from GitHub what the script already knew — using the one read mechanism this module documents as unreliable in exactly this window.

Nothing else stands between a replayed turn and a duplicate issue. There is no record on disk of what the session already filed, so a fresh-context replay starts from zero every time.

**Desired outcome:**

A replayed triage turn re-files nothing. Three independent defenses, cheapest first:

1. The agent never consults the lagging search index — the prompt hands it the exact `gh issue list --state all` REST command.
2. The agent mostly does not need to look at all — the script hands it the disposition it already resolved, per node, with the issue number when one exists.
3. If both of those are somehow bypassed, a session-local ledger on disk records every issue the session opened, and the agent consults it before opening the next.

## Freshness Check


**Baseline commit:** `feebe32aa` (`origin/main` at plan time; local `main` was 3 behind and was pulled before planning)
**Issue filed at:** 2026-09-05T06:46:08Z
**Disposition:** Minor drift

**File:line references re-verified (all at `feebe32aa`):**

- `scripts/nightly_regression_tests.py` — `_build_triage_prompt` — claimed to say "search ALL issues". **Still holds**, now at line 1458 (`def`), with the offending wording at line 1470: `"been triaged before. For EACH node below, search ALL issues — open AND "`. Verified present on `origin/main`, not just locally.
- `scripts/nightly_regression_tests.py` — `dispatch_findings` — claimed to "already partition nodes into open / closed-not-planned / to-file before dispatching". **Still holds**, at line 2447. The partition helpers are `partition_environmental` (1668), `partition_already_open` (2261), `partition_closed_matches` (2083). The discarding call site is line 2664.
- Commit `8524e765b` ("Quiet the nightly regression detector") — cited as the origin of the REST-not-search principle. **Confirmed present on main**; the principle is now written into the module's own constants at lines 322–340 and guarded by `test_open_issues_uses_the_rest_list_not_the_lagging_search`.
- `.worktrees/nightly-triage-{slug}` (cited in the #2972 body) — **still the live convention**: `agent/worktree_manager.py:20` sets `WORKTREES_DIR = ".worktrees"` and every lane lands at `.worktrees/{slug}/`. The slug is computed at `scripts/nightly_regression_tests.py:2334`.

**Cited sibling issues/PRs re-checked:**

- **#3161** — still OPEN. This issue was split from it; #3161 retains the "why was the turn replayed" investigation.
- **#3075** — **CLOSED 2026-09-05T02:13:35Z**, four hours before #3170 was filed, via merged PR **#3142**. This is the significant one: #3170's fix item 2 describes work partly done by #3075. See Prior Art.
- **#3001** — CLOSED. Its Work Item 3 is the parent scope ("make triage issue-filing idempotent — the same node must not produce a second issue on a re-run"). #3075 was that work item; this issue is its remainder.

**Commits on main since issue was filed (touching referenced files):**

- `35a225c19` "Nightly environmental classification escalates after consecutive nights and widens the closed dedup window" — **irrelevant to this fix.** It raised `CLOSED_ISSUE_LIST_LIMIT` to 4000 and added the consecutive-night environmental escalation. It touches neither `_build_triage_prompt` nor the `maybe_dispatch_triage_session` call site. Its `escalated` nodes flow into the ordinary filing path, so they inherit whatever this plan builds, with no special handling needed.

**Active plans in `docs/plans/` overlapping this area:** none. No plan doc references the nightly detector; the most recent plan touching adjacent ground is `sdlc-control-plane-asserted-facts.md` (2026-09-04), which is unrelated.

**Notes:** The branch `session/nightly-triage-idempotency-3075` exists locally and on origin and is **not** an ancestor of main — but its four commits are on main by content, squash-merged as `97354ce1e` via PR #3142. It is a leftover lane, not pending work. **The build must branch from `main`, never from that branch**, or it will reintroduce a duplicate of already-landed code.

## Prior Art


This is the fourth pass over nightly triage duplicate filing. Each prior pass closed a real hole; each left the prompt untouched.

- **PR #2195** (2026-07-24) "Nightly regression detector: run lock, readable alerts, triage dispatch" — introduced `maybe_dispatch_triage_session` and the run lock. First appearance of the `list[str]`-only dispatch signature this plan changes.
- **Issue #2559 / PR #2581** (2026-08-06) "Dedup nightly triage dispatch per node" — moved title computation from the agent into Python (`f"Nightly regression: {n}"`) so the same node yields a byte-identical title everywhere, and added per-node `dispatched_nodes` suppression. This is where the "search for the exact title before opening" instruction was written into `_build_triage_prompt` — correct in intent, and the wording that failed on 2026-08-24.
- **Commit `8524e765b`** (2026-08) "Quiet the nightly regression detector" — established the REST-not-search principle for the script's own reads: `open_issues()` uses `gh issue list`, and the constant comments reject `--search` in as many words. **The prompt was not updated in the same pass.** That asymmetry is the whole of fix 1.
- **Commit `8eb2344b9`** "Nightly detector: comment on the open issue instead of filing a twin" (#3134) — added `partition_already_open` returning issue *numbers*, enabling comment-over-create.
- **Issue #3001** (CLOSED) — the umbrella that absorbed the 39-issue flood; its Work Item 3 is the parent scope for all idempotency work here.
- **Issue #3075 / PR #3142** (merged 2026-09-05T02:13Z) — closed-issue-aware dedup, body-failure cascade collapsing, environmental classification. Added `closed_issue_dispositions()`, `partition_closed_matches()`, `closed_epilogue()`. Follow-up `35a225c19` added consecutive-night escalation. **This landed the detector-side half of fix 2 and left the prompt-side half undone** — see Why Previous Fixes Failed.

## Research


The only external surface is the GitHub CLI/API. Verified directly against it rather than by web search, which is stronger evidence:

**Commands run:**

```bash
gh issue list --state all --json number,title,state,stateReason --limit 5
```

**Key findings:**

- **The command in the issue works as written, and is fast.** Exit 0 in ~1.0s wall clock for a 5-row read on this repo. It is the REST list endpoint, not the search index.
- **`stateReason` is an empty string for OPEN issues**, not `null` and not absent. Sample row: `{"number":3170,"state":"OPEN","stateReason":"","title":"..."}`. Any filter the prompt tells the agent to write must branch on `state` first and read `stateReason` only when `state == "CLOSED"` — a naive `stateReason` switch will mis-handle every open issue. This is a real trap for the agent and the prompt must pre-empt it.
- **Closed rows carry `COMPLETED` / `NOT_PLANNED`** as expected, matching the `state_reason` values `closed_issue_dispositions()` already parses (`scripts/nightly_regression_tests.py:1992`), so the prompt's vocabulary and the script's are already aligned.
- **`--limit` above 100 causes `gh` to page the REST endpoint** 100 rows at a time; the module already relies on this for `CLOSED_ISSUE_LIST_LIMIT = 4000` (~40 calls, bounded by `CLOSED_ISSUE_LIST_TIMEOUT_SECONDS = 180`). The issue proposes `--limit 200` for the agent's read, which is two calls — cheap, but see Risks for whether 200 is enough coverage.

No web search was run: the training-data question ("does GitHub's search index lag?") is already settled inside this repo by `8524e765b` with a production incident behind it, and a web answer would be weaker evidence than the module's own comments and the 2026-08-24 wave.

## Spike Results


All verifiable assumptions were resolved during recon, by direct code read and one live command. No agents were dispatched — every question was cheaper to answer inline than to hand off.

### spike-1: The `gh issue list --state all` read is viable for the agent
- **Assumption**: "`gh issue list --state all --json number,title,state,stateReason --limit 200` returns what the prompt needs, fast enough to run once per node list."
- **Method**: code-read + live command
- **Finding**: Confirmed. Exit 0, ~1.0s for a small read, returns all four fields. `stateReason` is `""` for open issues (see Research) — the prompt must account for that.
- **Confidence**: high
- **Impact on plan**: Fix 1 is a prompt-text change with no new Python machinery. The prompt should instruct **one** read for the whole node list, not one per node.

### spike-2: How much of fix 2 did #3075 already land?
- **Assumption**: "`dispatch_findings` already partitions but does not pass dispositions through."
- **Method**: code-read of `dispatch_findings` (2447–2676) and `maybe_dispatch_triage_session` (2295)
- **Finding**: Confirmed exactly. The partitions exist and are acted on by the script itself (it posts the comments). What reaches the agent is `maybe_dispatch_triage_session(single_nodes, dry_run=dry_run)` at line 2664 — a bare `list[str]`. By the time nodes reach that call they have *already* survived `partition_already_open` and `partition_closed_matches`, so **every node in `single_nodes` is known to have no issue in any state**. The script knows the answer is "file it" and says nothing.
- **Confidence**: high
- **Impact on plan**: Fix 2 is smaller than the issue implies and its shape is inverted from the obvious reading. The pre-resolved disposition for the per-node path is uniformly `file` — the value is not in telling the agent *which* nodes are already handled (those never arrive), it is in telling the agent **that the script already checked, and what it checked against**, so a replay does not re-derive. See Solution.

### spike-3: Is the lane worktree a sound home for the ledger?
- **Assumption**: "A file under `.worktrees/{slug}/` is visible to a replayed turn."
- **Method**: code-read of `maybe_dispatch_triage_session:2334` and `agent/worktree_manager.py`
- **Finding**: Partly. The slug is `nightly-triage-{sha256(",".join(sorted(set(dispatch_nodes))))[:8]}` — a pure function of the node set, so a replay of the same dispatch does resolve to the same `.worktrees/{slug}/`. But the worktree is a git checkout: a stray file there shows up in the agent's own `git status` and is destroyed on lane teardown. `DATA_DIR = PROJECT_DIR / "data"` (line 155) is already this script's state home (`nightly_tests_last_run.json`, `nightly_tests.lock`), is gitignored (`.gitignore:181`), survives teardown, and is reachable by absolute path from inside a worktree.
- **Confidence**: high
- **Impact on plan**: Ledger goes in `data/`, keyed by slug, **not** in the lane worktree — a deliberate deviation from the issue's literal wording. Recorded in Open Questions.

### spike-4: Are there xfail markers to convert?
- **Assumption**: "A bug this old has an expected-failure test documenting it."
- **Method**: `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/ --include="*.py"`
- **Finding**: **Zero matches anywhere in `tests/`** — neither decorator nor runtime form, for this bug or any other.
- **Confidence**: high
- **Impact on plan**: No conversion tasks. Nothing in Success Criteria about xfails.

### spike-5: Does the stale `session/nightly-triage-idempotency-3075` branch contain unlanded work?
- **Assumption**: "The branch might hold work this plan would duplicate."
- **Method**: `git merge-base --is-ancestor` + `git log main..origin/session/...`
- **Finding**: Not an ancestor of main, 4 commits ahead of it — but those commits are on main by content as squash-merge `97354ce1e` (PR #3142). It is a leftover lane.
- **Confidence**: high
- **Impact on plan**: Build branches from `main`. Called out in Freshness Check notes and Rabbit Holes.

## Data Flow


The defect lives at one boundary. Tracing a single failing node from pytest to the tracker:

1. **Entry point** — `run_tests()` produces a pytest-json report; `extract_failing_node_ids()` then `reconfirm_serial()` yield a confirmed-failing set.
2. **`compute_dispatch_set(prev, confirmed_failing)`** — drops nodes a previous *run* already dispatched. This is per-machine, per-night state in `data/nightly_tests_last_run.json`. It defends against night-over-night duplicates, not within-session replay.
3. **`dispatch_findings(...)`** — the decision layer.
   - `partition_environmental` removes network-fault nodes (and escalates ones that have been environmental too many consecutive nights).
   - `group_setup_error_cascades` / `group_body_failure_cascades` collapse shared root causes into umbrellas.
   - `open_issues()` and `closed_issue_dispositions()` read **live REST state** — `gh issue list`, deliberately not `--search`.
   - `partition_already_open` → comment on the open issue, record, drop the node.
   - `partition_closed_matches` → comment on a `NOT_PLANNED` closure, record, drop the node. A `COMPLETED` closure falls through and re-files.
   - What survives is `single_nodes`: **nodes with no issue in any state, as of a REST read seconds ago.**
4. **The boundary where the information is lost** — `maybe_dispatch_triage_session(single_nodes, dry_run=dry_run)` (line 2664). Signature accepts `list[str]`. Every disposition, every issue number, every fact about *what was checked and when* stops here.
5. **`_build_triage_prompt(dispatch_nodes)`** — reconstitutes a prompt from node ids alone, and instructs the agent to re-derive the state of the world by "searching ALL issues".
6. **`tools.valor_session create --role eng --slug nightly-triage-{hash} --message <prompt>`** — one Eng session, one lane worktree at `.worktrees/{slug}/`.
7. **The agent** searches (index-backed, lagging), finds nothing, files. **On a replayed turn it does the same thing again**, because nothing in steps 4–6 left a trace it could consult and step 5's read cannot see minutes-old issues.
8. **Output** — one GitHub issue per node. Or three, as on 2026-08-24.

The fix does not move where decisions are made. It stops discarding them at step 4, hardens the read at step 5, and adds a durable trace at step 7 for the case where a replay bypasses both.

## Why Previous Fixes Failed


| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2581 (#2559) | Moved title computation into Python; added per-node `dispatched_nodes` suppression across runs; wrote the "search for the EXACT title" instruction into the prompt. | Right layer for cross-*night* dedup, wrong layer for within-*session* replay. `dispatched_nodes` is written after the dispatch returns, so a turn replayed inside one dispatch never sees it. And the instruction it added is the one that fails: "search" names a read mechanism that cannot see minutes-old issues. |
| Commit `8524e765b` | Established REST-not-search for the detector's own reads; wrote the rationale into the constants. | Fixed the script's read and left the agent's read alone. The module now documents in two places that `--search` lags by minutes, while still telling the agent to search. The principle was correct and its application was incomplete. |
| Commit `8eb2344b9` (#3134) | `partition_already_open` returns issue numbers so the script comments instead of staying quiet. | Made the script's own knowledge richer without widening the channel to the agent. More was known; the same nothing was passed on. |
| PR #3142 (#3075) | Closed-issue-aware dedup, cascade collapsing, environmental classification. Answered "where does idempotency belong?" — in the script. | Answered it **for the script's own filing decisions** and stopped there. The prompt was updated to *describe* the open-and-closed rule in prose, but not to hand the agent either the resolved answer or a reliable way to read it. The agent still re-derives everything. |

**Root cause pattern:** every fix improved what the *script* knows and none of them widened the channel to the *agent*. `maybe_dispatch_triage_session(list[str])` has been the choke point since PR #2195, and four passes of increasingly sophisticated dedup have all been squeezed through it and dropped. The agent has been left to reconstruct, over an unreliable read, a decision that was fully resolved in Python seconds earlier. Fixing the reads without fixing the channel is what keeps this recurring.

## Architectural Impact


- **New dependencies**: none. No new imports, no new packages, no new services. `gh` and `subprocess` are already in use throughout the module.
- **Interface changes**: `maybe_dispatch_triage_session` and `_build_triage_prompt` both gain one optional keyword argument carrying the resolved dispositions. Both are module-private in practice (`_build_triage_prompt` by name, `maybe_dispatch_triage_session` by having exactly two callers, both inside this module). Existing positional calls keep working — the cascade path (`prompt=` override) and the baseline-seed path (`slug_suffix=`) are untouched.
- **Coupling**: decreases the agent's coupling to GitHub's search index, which is the point. Slightly increases coupling between `dispatch_findings` and the prompt builder — deliberate, and the direction the module has been moving since #2559 pinned literal titles for exactly this reason: the pre-flight check and the agent's instructions must not be able to drift.
- **Data ownership**: introduces one new piece of state, `data/nightly-triage-ledger/{slug}.json`, owned by the nightly script (which seeds it) and appended to by the triage agent. It is gitignored, machine-local, and advisory — losing it degrades to today's behavior rather than breaking anything.
- **Reversibility**: high. Fixes 1 and 2 are text and a keyword argument in one file. Fix 3 adds one small write and one prompt paragraph. Any of the three can be reverted independently without touching the others.

## Appetite


**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0 — the three fixes are specified in the issue and recon settled the one design question (ledger location) with a rationale; nothing needs a scope call.
- Review rounds: 1

One Python file, one test file, one doc. No new dependencies, no migration, no service restart. The work is bounded by the care the guards need, not by the code volume.

## Prerequisites


| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | The prompt's `gh issue list --state all` read and the plan's own verification both need a working `gh`. |
| Repo venv on the pinned interpreter | `python -m tools.doctor` | `scripts/pytest-clean.sh` aborts on an off-pin venv; a worktree without one blocks the pre-commit lint hook. |

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

---

## Open Questions

_placeholder_
